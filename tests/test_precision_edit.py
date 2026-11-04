import importlib
import math

import bmesh
import bpy
from mathutils import Vector

m = importlib.import_module("bl_ext.clanker.precision_edit")


def _cube():
    """Unit cube (verts at +-0.5) with object scale 2, in Edit Mode, nothing selected."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    me = bpy.data.meshes.new("PE")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("PE", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.scale = (2, 2, 2)
    ob.location = (1, 0, 0)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    return ob, bm


def _sel(bm, *elems):
    for el in elems:
        el.select_set(True)
        bm.select_history.add(el)


def _w(ob, v):
    return ob.matrix_world @ v.co


def _done(ob):
    bpy.ops.object.mode_set(mode='OBJECT')


def test_pure_math():
    a, b = Vector((0, 0, 0)), Vector((2, 0, 0))
    assert m.set_length(a, b, 3, 'B') == (a, Vector((3, 0, 0)))
    assert m.set_length(a, b, 3, 'A') == (Vector((-1, 0, 0)), b)
    assert m.set_length(a, b, 4, 'CENTER') == (Vector((-1, 0, 0)), Vector((3, 0, 0)))
    c, f, mv = Vector(), Vector((1, 0, 0)), Vector((0, 2, 0))
    new = m.set_angle(c, f, mv, math.radians(60))
    assert abs(math.degrees(m.angle_at(c, f, new)) - 60) < 1e-4
    assert abs(new.length - 2) < 1e-6 and abs(new.z) < 1e-9


def test_edge_length_world_space():
    ob, bm = _cube()
    e = bm.edges[0]
    va, vb = e.verts
    _sel(bm, va, vb)  # vb is active -> end B
    e.select_set(True)
    before = {v.index: v.co.copy() for v in bm.verts}
    rows = m.readouts(ob, bm)
    assert rows[0][0] == "Edge Length" and abs(rows[0][1] - 2.0) < 1e-6, rows
    assert bpy.ops.precision_edit.set_edge_length(length=1.5, move='B') == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    assert abs((_w(ob, bm.verts[vb.index]) - _w(ob, bm.verts[va.index])).length - 1.5) < 1e-6
    moved = [i for i, co in before.items() if (bm.verts[i].co - co).length > 1e-9]
    assert moved == [vb.index], moved
    _done(ob)


def test_edge_length_stretch():
    ob, bm = _cube()
    bpy.ops.mesh.select_all(action='SELECT')
    e = next(e for e in bm.edges if abs(e.verts[0].co.x - e.verts[1].co.x) > 0.5)  # an X edge
    bm.select_history.clear()
    bm.select_history.add(e)
    assert bpy.ops.precision_edit.set_edge_length(length=3.0, move='B', stretch=True) == {'FINISHED'}
    xs = [_w(ob, v).x for v in bmesh.from_edit_mesh(ob.data).verts]
    assert abs(max(xs) - min(xs) - 3.0) < 1e-6, xs  # whole side moved, box is 3 wide
    _done(ob)


def test_angle_three_verts_and_two_edges():
    ob, bm = _cube()
    c = bm.verts[0]
    arms = [e.other_vert(c) for e in c.link_edges][:2]
    _sel(bm, arms[0], arms[1], c)  # corner active
    rows = m.readouts(ob, bm)
    assert rows[0][0] == "Angle" and abs(math.degrees(rows[0][1]) - 90) < 1e-4, rows
    fixed = _w(ob, arms[0])
    assert bpy.ops.precision_edit.set_angle(angle=math.radians(60)) == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    cw, aw, bw = (_w(ob, bm.verts[v.index]) for v in (c, arms[0], arms[1]))
    assert abs(math.degrees(m.angle_at(cw, aw, bw)) - 60) < 1e-4
    assert (aw - fixed).length < 1e-9 and abs((bw - cw).length - 2.0) < 1e-6
    # Two edges sharing a vertex (edge select), the non-active edge moves.
    bpy.ops.mesh.select_all(action='DESELECT')
    bm.select_history.clear()
    c = bm.verts[7]
    e1, e2 = list(c.link_edges)[:2]
    _sel(bm, e2, e1)  # e1 active
    keep = _w(ob, e1.other_vert(c))
    assert bpy.ops.precision_edit.set_angle(angle=math.radians(45)) == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    cv = bm.verts[7]
    p1, p2 = (_w(ob, e.other_vert(cv)) for e in list(cv.link_edges)[:2])
    assert (p1 - keep).length < 1e-9
    assert abs(math.degrees(m.angle_at(_w(ob, cv), p1, p2)) - 45) < 1e-4
    _done(ob)


def test_distance_center():
    ob, bm = _cube()
    a = bm.verts[0]
    b = max(bm.verts, key=lambda v: (v.co - a.co).length)  # opposite corner, no edge
    _sel(bm, a, b)
    rows = m.readouts(ob, bm)
    assert rows[0][0] == "Distance" and abs(rows[0][1] - 2 * math.sqrt(3)) < 1e-6, rows
    mid = (_w(ob, a) + _w(ob, b)) / 2
    assert bpy.ops.precision_edit.set_distance(distance=5.0, move='CENTER') == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    pa, pb = _w(ob, bm.verts[a.index]), _w(ob, bm.verts[b.index])
    assert abs((pa - pb).length - 5.0) < 1e-6 and ((pa + pb) / 2 - mid).length < 1e-6
    _done(ob)


def test_scale_to_edge():
    ob, bm = _cube()
    bpy.ops.mesh.select_all(action='SELECT')
    bm.select_history.clear()
    bm.select_history.add(bm.edges[3])
    med = sum((_w(ob, v) for v in bm.verts), Vector()) / 8
    assert bpy.ops.precision_edit.scale_to_edge(length=3.0, pivot='MEDIAN') == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.edges.ensure_lookup_table()
    for e in bm.edges:  # all cube edges scale together
        assert abs((_w(ob, e.verts[0]) - _w(ob, e.verts[1])).length - 3.0) < 1e-6
    assert (sum((_w(ob, v) for v in bm.verts), Vector()) / 8 - med).length < 1e-6
    _done(ob)


def test_set_position_world_and_local():
    ob, bm = _cube()
    a, b = bm.verts[0], bm.verts[1]
    _sel(bm, b, a)  # a active
    off = _w(ob, b) - _w(ob, a)
    assert bpy.ops.precision_edit.set_position(location=(1, 2, 3), space='WORLD') == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    assert (_w(ob, bm.verts[0]) - Vector((1, 2, 3))).length < 1e-6, _w(ob, bm.verts[0])
    assert (_w(ob, bm.verts[1]) - _w(ob, bm.verts[0]) - off).length < 1e-6
    assert bpy.ops.precision_edit.set_position(location=(0.25, 0, 0), space='LOCAL') == {'FINISHED'}
    bm = bmesh.from_edit_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    assert (bm.verts[0].co - Vector((0.25, 0, 0))).length < 1e-6
    _done(ob)


def test_format_and_panel():
    import drawcheck
    sc = bpy.context.scene
    sc.unit_settings.scale_length = 2.0
    assert m.fmt(sc, 1.5, 'LENGTH') == "3 m", m.fmt(sc, 1.5, 'LENGTH')
    assert m.fmt(sc, math.radians(60), 'ROTATION').startswith("60.0000")
    sc.unit_settings.scale_length = 1.0
    ob, bm = _cube()
    for sel in ((bm.verts[0],), bm.edges[0].verts, (bm.verts[1], bm.verts[2], bm.verts[0])):
        bpy.ops.mesh.select_all(action='DESELECT')
        bm.select_history.clear()
        _sel(bm, *sel)
        m.PE_PT_panel.draw(drawcheck._Self(m.PE_PT_panel), drawcheck.Ctx())
    assert bpy.ops.precision_edit.set_angle.get_rna_type().properties["angle"].is_skip_save
    _done(ob)
