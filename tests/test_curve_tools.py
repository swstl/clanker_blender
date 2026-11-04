import math

import bmesh
import bpy
import importlib
from mathutils import Vector

ct = importlib.import_module("bl_ext.clanker.curve_tools")


def _line(length=10.0):
    cu = bpy.data.curves.new("Line", 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(1)
    sp.points[0].co = (0, 0, 0, 1)
    sp.points[1].co = (length, 0, 0, 1)
    ob = bpy.data.objects.new("Line", cu)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _cube(name):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = (0, 5, 3)  # sources anywhere: reset to origin when instanced
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _select(objs, active):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = active


def _instances(curve):
    dg = bpy.context.evaluated_depsgraph_get()
    return [i.matrix_world.copy() for i in dg.object_instances
            if i.is_instance and i.parent and i.parent.original == curve]


def test_spacing_count_and_upright():
    line, a, b = _line(), _cube("A"), _cube("B")
    _select([line, a, b], line)
    bpy.ops.curve_tools.along_curve(mode='Spacing', spacing=1.0, rotation='Upright', rotation_jitter=0.5)
    mats = _instances(line)
    assert len(mats) == 11, len(mats)
    xs = sorted(round(m.translation.x, 4) for m in mats)
    assert xs[0] == 0 and xs[-1] == 10
    for m in mats:
        e = m.to_euler()
        assert abs(e.x) < 1e-4 and abs(e.y) < 1e-4, e
        assert abs(m.translation.y) < 1e-4  # source location reset
    md = ct.along_modifier(line)
    ct.set_input(md, "Include Ends", False)
    ct.set_input(md, "Mode", "Count")
    ct.set_input(md, "Count", 5)
    assert len(_instances(line)) == 3, (len(_instances(line)), ct.get_input(md, "Mode").value)
    line.data.splines[0].points[1].co.x = 20  # live: edit curve -> copies follow
    ct.set_input(md, "Mode", "Spacing")
    assert len(_instances(line)) == 19


def test_tangent_and_realize():
    line, a = _line(4.0), _cube("A")
    line.rotation_euler.z = math.pi / 2  # curve runs along +Y in world
    _select([line, a], line)
    bpy.ops.curve_tools.along_curve(mode='Count', count=5, rotation='Tangent')
    for m in _instances(line):
        x_axis = m.to_3x3() @ Vector((1, 0, 0))
        assert abs(abs(x_axis.y) - 1) < 1e-4, x_axis
    bpy.ops.curve_tools.realize()
    copies = [o for o in bpy.context.scene.objects if o.data == a.data and o != a]
    assert len(copies) == 5
    assert not ct.along_modifier(line) and not _instances(line)
    ys = sorted(round(o.matrix_world.translation.y, 4) for o in copies)
    assert ys == [0, 1, 2, 3, 4], ys


def _empty(name, loc):
    ob = bpy.data.objects.new(name, None)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _cable_verts(cable):
    bpy.context.view_layer.update()
    ev = cable.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    vs = [v.co.copy() for v in me.vertices]
    ev.to_mesh_clear()
    return vs


def test_cable_sag_and_hooks():
    a, b = _empty("EA", (0, 0, 2)), _empty("EB", (4, 0, 2))
    bpy.context.view_layer.update()
    _select([a, b], a)
    bpy.ops.curve_tools.cable(slack=0.1, thickness=0, caps=True)
    cable = bpy.context.active_object
    bps = cable.data.splines[0].bezier_points
    assert (bps[0].co - a.location).length < 1e-5 and (bps[1].co - b.location).length < 1e-5
    mid = ct.bezier_eval(bps[0], bps[1], 0.5)
    expected = ct.sag_for(4.0, 0.1)
    assert abs((2 - mid.z) - expected) < 1e-4, (mid, expected)
    assert abs(mid.x - 2) < 1e-5
    assert len([o for o in bpy.context.scene.objects if o.name.startswith("Cable Cap")]) == 2

    vs = _cable_verts(cable)
    assert max(v.x for v in vs) < 4.001
    b.location = (6, 1, 3)
    vs = _cable_verts(cable)
    end = max(vs, key=lambda v: v.x)
    assert (end - Vector((6, 1, 3))).length < 1e-3, end
    start = min(vs, key=lambda v: v.x)
    assert (start - Vector((0, 0, 2))).length < 1e-3, start
    assert min(v.z for v in vs) < 2  # still sags below both ends


def test_cable_bundle_chain():
    es = [_empty(f"E{i}", (i * 2, 0, 0)) for i in (0, 2, 1)]  # chain sorts by distance
    bpy.context.view_layer.update()
    _select(es, es[0])
    bpy.ops.curve_tools.cable(cables=3, spread=0.1, hooks=True)
    cable = bpy.context.active_object
    assert len(cable.data.splines) == 3
    xs = [round(bp.co.x, 3) for bp in cable.data.splines[0].bezier_points]
    assert xs == [0, 2, 4], xs
    assert len(cable.modifiers) == 3


def test_cable_edit_mode_verts():
    me = bpy.data.meshes.new("M")
    me.from_pydata([(0, 0, 0), (3, 0, 0), (9, 9, 9)], [], [])
    ob = bpy.data.objects.new("M", me)
    bpy.context.scene.collection.objects.link(ob)
    _select([ob], ob)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(me)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        v.select = v.index < 2
    bpy.ops.curve_tools.cable()
    bpy.ops.object.mode_set(mode='OBJECT')
    cable = bpy.data.objects["Cable"]
    assert [round(bp.co.x, 3) for bp in cable.data.splines[0].bezier_points] == [0, 3]


def test_corners_random_pick_realize_mesh():
    line, a, b = _line(), _cube("A"), _cube("B")
    line.data.splines[0].points.add(1)
    line.data.splines[0].points[2].co = (10, 5, 0, 1)
    _select([line, a, b], line)
    bpy.ops.curve_tools.along_curve(mode='Corners', random_pick=True, seed=3, scale_jitter=0.5)
    assert len(_instances(line)) == 3
    bpy.ops.curve_tools.realize(as_mesh=True)
    joined = bpy.data.objects["Line Realized"]
    assert len(joined.data.vertices) == 24
