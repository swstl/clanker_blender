import importlib
import math

import bmesh
import bpy
from mathutils import Vector

ps = importlib.import_module("bl_ext.clanker.print_split")


def _mesh_ob(name, build):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _cube(name="Cube", size=2.0):
    return _mesh_ob(name, lambda bm: bmesh.ops.create_cube(bm, size=size))


def _volume(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    v = bm.calc_volume()
    bm.free()
    return v


def _watertight(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    ok = all(len(e.link_faces) == 2 for e in bm.edges)
    bm.free()
    return ok and ps.non_manifold_count(ob) == 0


def _select(*obs):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]


def _plane_z(target, connectors=False):
    _select(target)
    bpy.ops.print_split.add_plane(orient='Z', at='OBJECT')
    s = bpy.context.scene.print_split
    s.connectors = connectors
    return s


def test_add_plane():
    ob = _cube()
    ob.location = (1, 2, 3)
    bpy.context.view_layer.update()
    _select(ob)
    bpy.ops.print_split.add_plane(orient='X', at='OBJECT')
    plane = bpy.context.scene.print_split.plane
    assert plane.name.startswith("SplitPlane") and plane.display_type == 'WIRE'
    co, no = ps.plane_of(plane)
    assert (co - ob.location).length < 1e-5 and abs(no.x - 1) < 1e-6
    assert plane["print_split_target"] == ob


def test_split_cube_watertight():
    ob = _cube()
    vol = _volume(ob)
    _plane_z(ob)
    _select(ob)
    assert bpy.ops.print_split.split() == {'FINISHED'}
    a, b = bpy.data.objects["Cube_A"], bpy.data.objects["Cube_B"]
    assert ob.hide_get()
    assert _watertight(a) and _watertight(b)
    assert abs(_volume(a) - 4) < 1e-5 and abs(_volume(b) - 4) < 1e-5
    assert min(v.co.z for v in a.data.vertices) > -1e-6  # A is on the normal side
    assert abs(_volume(a) + _volume(b) - vol) < 1e-5


def test_split_sphere_repeated():
    ob = _mesh_ob("Ball", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1))
    vol = _volume(ob)
    _plane_z(ob)  # through the equator vertices
    bpy.ops.print_split.split()
    a, b = bpy.data.objects["Ball_A"], bpy.data.objects["Ball_B"]
    assert _watertight(a) and _watertight(b)
    assert abs(_volume(a) + _volume(b) - vol) < 1e-4
    # Second plane across X on the top half, plane found via selection.
    _select(a)
    bpy.ops.print_split.add_plane(orient='X', at='CURSOR')
    s = bpy.context.scene.print_split
    s.connectors = False
    s.delete_original = True
    _select(a)
    bpy.ops.print_split.split()
    aa, ab = bpy.data.objects["Ball_A_A"], bpy.data.objects["Ball_A_B"]
    assert "Ball_A" not in bpy.data.objects
    assert _watertight(aa) and _watertight(ab)
    assert abs(_volume(aa) + _volume(ab) - _volume(b)) < 1e-4


def test_ring_cut_keeps_hole():
    ob = _mesh_ob("Ring", lambda bm: bmesh.ops.create_cone(bm, segments=16, radius1=1, radius2=1, depth=2,
                                                             cap_ends=True))
    hole = _mesh_ob("Hole", lambda bm: bmesh.ops.create_cone(bm, segments=16, radius1=0.5, radius2=0.5,
                                                             depth=3, cap_ends=True))
    me = hole.data
    bpy.data.objects.remove(hole)
    ps.apply_boolean(ob, me, 'DIFFERENCE')  # also removes the cutter mesh
    vol = _volume(ob)
    _plane_z(ob)
    bpy.ops.print_split.split()
    a, b = bpy.data.objects["Ring_A"], bpy.data.objects["Ring_B"]
    assert _watertight(a) and _watertight(b)
    assert abs(_volume(a) - vol / 2) < 1e-4  # cap did not fill the hole


def test_connectors_volumes():
    ob = _cube()
    s = _plane_z(ob, connectors=True)
    s.peg_count, s.peg_radius, s.peg_length, s.tolerance, s.margin = 2, 0.1, 0.3, 0.02, 0.05
    assert abs(s.tolerance - 0.02) < 1e-7
    _select(ob)
    bpy.ops.print_split.split()
    a, b = bpy.data.objects["Cube_A"], bpy.data.objects["Cube_B"]
    assert _watertight(a) and _watertight(b)
    area = lambda r: 16 * r * r * math.sin(2 * math.pi / 32)  # 32-segment cylinder
    peg = 2 * area(0.1) * 0.3
    hole = 2 * area(0.1 + 0.02) * (0.3 + 0.02)
    assert abs(_volume(a) - (4 + peg)) < 1e-4, _volume(a)
    assert abs(_volume(b) - (4 - hole)) < 1e-4, _volume(b)
    # Pegs stick down out of A by peg length.
    assert abs(min(v.co.z for v in a.data.vertices) + 0.3) < 1e-5
    # Hole radius = peg radius + tolerance (deepest hole ring sits at z = length + tolerance).
    ring = [v.co for v in b.data.vertices if abs(v.co.z + 0.32) < 1e-5]
    assert ring
    sq = [Vector(p) for p in ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0))]
    centers = ps.place_pegs([(sq[i], sq[(i + 1) % 4]) for i in range(4)], Vector(), Vector((0, 0, 1)), 2, 0.17, 0.34)
    rs = [min((v.xy - c.xy).length for c in centers) for v in ring]
    assert abs(min(rs) - 0.12) < 1e-4 and abs(max(rs) - 0.12) < 1e-4


def test_place_pegs_respects_outline():
    sq = [Vector(p) for p in ((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0))]
    segs = [(sq[i], sq[(i + 1) % 4]) for i in range(4)]
    pts = ps.place_pegs(segs, Vector(), Vector((0, 0, 1)), 4, 0.3, 0.6)
    assert len(pts) == 4
    assert all(max(abs(p.x), abs(p.y)) <= 0.7 + 1e-9 for p in pts)
    assert ps.place_pegs(segs, Vector(), Vector((0, 0, 1)), 3, 1.5, 0.1) == []  # too small
    one = ps.place_pegs(segs, Vector(), Vector((0, 0, 1)), 1, 0.3, 0.6)
    assert one[0].length < 0.1  # single peg goes to the deepest point


def test_bed_fit_logic():
    assert ps.bed_fit((0.1, 0.1, 0.1), (0.22, 0.22, 0.25)) == (0, 1, 2)
    assert ps.bed_fit((0.24, 0.1, 0.1), (0.22, 0.22, 0.25)) is not None  # tip it on its side
    assert ps.bed_fit((0.3, 0.1, 0.1), (0.22, 0.22, 0.25)) is None


def test_bed_check_operator_and_units():
    s = bpy.context.scene.print_split
    s.bed_preset = 'BAMBU'
    assert abs(s.bed_x - 0.256) < 1e-6
    small, big = _cube("Small", 0.1), _cube("Big", 0.3)
    _select(small, big)
    bpy.ops.print_split.bed_check()
    assert tuple(small.color) == (1, 1, 1, 1) and big.color[1] < 0.5
    bpy.context.scene.unit_settings.scale_length = 0.001  # 1 BU = 1 mm
    try:
        assert abs(s.bed_x - 256) < 1e-3 and abs(s.tolerance - 0.2) < 1e-5
    finally:
        bpy.context.scene.unit_settings.scale_length = 1.0


def test_manifold_check():
    ob = _cube()
    assert ps.non_manifold_count(ob) == 0
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.faces.ensure_lookup_table()
    bm.faces.remove(bm.faces[0])
    bm.to_mesh(ob.data)
    bm.free()
    assert ps.non_manifold_count(ob) == 4
    _select(ob)
    assert bpy.ops.print_split.manifold_check() == {'FINISHED'}
