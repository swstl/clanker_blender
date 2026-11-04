import importlib
import math
from collections import Counter

import bmesh
import bpy
from mathutils import Vector

M = importlib.import_module("bl_ext.clanker.archviz_walls")


def _plan(points, closed=True, name="Plan"):
    me = bpy.data.meshes.new(name)
    n = len(points)
    edges = [(i, (i + 1) % n) for i in range(n if closed else n - 1)]
    me.from_pydata([Vector(p) for p in points], edges, [])
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    return ob


SQUARE = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]


def _eval(ob):
    bpy.context.view_layer.update()
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    ev.to_mesh_clear()
    return bm


def _bbox(bm):
    xs, ys, zs = zip(*(v.co for v in bm.verts))
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _watertight(bm):
    return bm.faces and all(len(e.link_faces) == 2 for e in bm.edges)


def _walls(**kw):
    bpy.ops.archviz_walls.add_walls(**kw)
    return bpy.context.active_object


def test_square_alignments():
    t = 0.2
    # Clockwise loop too: Inside/Outside must follow the loop, not the drawing direction.
    for pts in (SQUARE, SQUARE[::-1]):
        plan = _plan(pts)
        for align, lo, hi in (('Outside', -t, 4 + t), ('Center', -t / 2, 4 + t / 2), ('Inside', 0, 4)):
            w = _walls(thickness=t, height=3.0, alignment=align, floor=False)
            bm = _eval(w)
            (x0, y0, z0), (x1, y1, z1) = _bbox(bm)
            assert abs(x0 - lo) < 1e-4 and abs(x1 - hi) < 1e-4 and abs(y1 - hi) < 1e-4, (align, x0, x1)
            assert abs(z0) < 1e-5 and abs(z1 - 3.0) < 1e-5
            assert _watertight(bm), align
            # Mitred: 8 vertical corner columns -> 16 verts, no pinch at corners.
            assert len(bm.verts) == 16, len(bm.verts)
            vol = bm.calc_volume()
            ring = (hi - lo) ** 2 - (hi - lo - 2 * t) ** 2
            assert abs(vol - ring * 3.0) < 1e-3, (align, vol)
            bpy.context.view_layer.objects.active = plan


def test_live_update():
    plan = _plan(SQUARE)
    w = _walls(alignment='Inside', floor=False)
    plan.data.vertices[1].co.x = 6
    plan.data.vertices[2].co.x = 6
    plan.data.update()
    assert abs(_bbox(_eval(w))[1][0] - 6) < 1e-4
    M.set_input(M.walls_modifier(w), "Height", 1.5)
    assert abs(_bbox(_eval(w))[1][2] - 1.5) < 1e-5


def test_open_l_shape():
    _plan([(0, 0, 0), (4, 0, 0), (4, 3, 0)], closed=False)
    w = _walls(thickness=0.2, height=2.5, alignment='Center')
    bm = _eval(w)
    assert _watertight(bm)
    (x0, y0, _), (x1, y1, z1) = _bbox(bm)
    assert abs(x0) < 1e-4 and abs(x1 - 4.1) < 1e-4 and abs(y0 + 0.1) < 1e-4 and abs(y1 - 3) < 1e-4
    assert abs(z1 - 2.5) < 1e-5
    assert len(bm.verts) == 12  # 3 profile pairs top+bottom, mitred corner, capped ends
    assert abs(bm.calc_volume() - (4 + 3) * 0.2 * 2.5) < 1e-3  # mitre keeps centre-line volume


def test_curve_plan():
    cu = bpy.data.curves.new("PlanCurve", 'CURVE')
    sp = cu.splines.new('POLY')
    sp.points.add(3)
    for p, co in zip(sp.points, SQUARE):
        p.co = (*co, 1)
    sp.use_cyclic_u = True
    ob = bpy.data.objects.new("PlanCurve", cu)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    w = _walls(alignment='Outside', floor=False)
    bm = _eval(w)
    assert _watertight(bm) and abs(_bbox(bm)[1][0] - 4.2) < 1e-4


def test_floor_ceiling():
    _plan(SQUARE)
    w = _walls(alignment='Outside', height=3.0, floor=True, ceiling=True, slab=0.25)
    md = M.walls_modifier(w)
    set_walls = M.set_input
    set_walls(md, "Walls", False)
    bm = _eval(w)
    assert _watertight(bm)
    up = [f for f in bm.faces if f.normal.z > 0.99]
    floor_top = sum(f.calc_area() for f in up if abs(f.calc_center_median().z) < 1e-5)
    assert abs(floor_top - 16) < 1e-3, floor_top
    (_, _, z0), (_, _, z1) = _bbox(bm)
    assert abs(z0 + 0.25) < 1e-5 and abs(z1 - 3.25) < 1e-5


def test_window_cutter():
    _plan(SQUARE)
    w = _walls(alignment='Center', thickness=0.2, height=3.0, floor=False)
    vol0 = _eval(w).calc_volume()
    bpy.context.scene.cursor.location = (4.3, 2.2, 1.0)  # near the x=4 wall (runs along +Y)
    bpy.ops.archviz_walls.add_opening(kind='WINDOW', width=1.2, height=1.0, sill=0.9)
    cutter = bpy.data.objects["Window"]
    assert cutter.parent == w and cutter.display_type == 'WIRE' and cutter.hide_render
    assert "Cutters" in [c.name for c in cutter.users_collection]
    assert abs(math.degrees(cutter.rotation_euler.z) % 180 - 90) < 1e-3
    loc = cutter.matrix_world.translation
    assert abs(loc.x - 4) < 1e-4 and abs(loc.y - 2.2) < 1e-4 and abs(loc.z - 0.9) < 1e-4
    md = w.modifiers[-1]
    assert md.type == 'BOOLEAN' and md.operation == 'DIFFERENCE' and md.object == cutter
    bm = _eval(w)
    assert _watertight(bm)
    assert abs(vol0 - bm.calc_volume() - 1.2 * 1.0 * 0.2) < 1e-3

    # Door at floor level with a floor slab in the same object, then bake everything.
    M.set_input(M.walls_modifier(w), "Floor", True)
    vol1 = _eval(w).calc_volume()
    bpy.context.scene.cursor.location = (2.0, -0.5, 0.0)
    bpy.ops.archviz_walls.add_opening(kind='DOOR', width=0.9, height=2.1, sill=0.0)
    bm = _eval(w)
    assert _watertight(bm)
    assert abs(vol1 - bm.calc_volume() - 0.9 * 2.1 * 0.2) < 1e-3
    bpy.ops.archviz_walls.apply()
    assert not w.modifiers and "Window" not in bpy.data.objects and "Door" not in bpy.data.objects
    assert len(w.data.polygons) > 20
