import importlib

import bmesh
import bpy
from mathutils import Vector

m = importlib.import_module("bl_ext.clanker.origin_align")


def cube(loc=(0, 0, 0), size=2.0, name="Cube"):
    bpy.ops.mesh.primitive_cube_add(size=size, location=loc)
    ob = bpy.context.object
    ob.name = name
    return ob


def world_verts(ob):
    return [ob.matrix_world @ v.co for v in ob.data.vertices]


def close(a, b, eps=1e-5):
    return (Vector(a) - Vector(b)).length < eps


def select_only(*obs, active=None):
    bpy.ops.object.select_all(action='DESELECT')
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or obs[0]


def test_origin_bottom_center_keeps_geometry():
    ob = cube((1, 2, 3))
    ob.rotation_euler.z = 0.3
    ob.scale = (1, 2, 1)
    child = cube((5, 0, 0), name="Child")
    child.parent = ob
    child.matrix_parent_inverse = ob.matrix_world.inverted()
    bpy.context.view_layer.update()
    before, child_before = world_verts(ob), child.matrix_world.copy()
    select_only(ob)
    assert bpy.ops.origin_align.origin_bounds(z='MIN') == {'FINISHED'}
    bpy.context.view_layer.update()
    assert all(close(a, b) for a, b in zip(before, world_verts(ob)))
    assert close(ob.matrix_world.translation, (1, 2, 2))  # local bbox bottom center
    assert all(abs(a - b) < 1e-5 for ra, rb in zip(child.matrix_world, child_before) for a, b in zip(ra, rb))


def test_shared_mesh_is_skipped():
    a = cube()
    b = bpy.data.objects.new("Linked", a.data)
    bpy.context.collection.objects.link(b)
    select_only(a)
    assert bpy.ops.origin_align.origin_bounds(z='MIN') == {'CANCELLED'}
    assert close(a.location, (0, 0, 0))


def test_edit_mode_origin_to_selection():
    ob = cube((2, 0, 0))
    select_only(ob)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select_set(v.co.x > 0 and v.co.z > 0)  # top +X edge: x=1, z=1
    bm.select_flush_mode()
    bmesh.update_edit_mesh(ob.data)
    assert bpy.ops.origin_align.origin_to(target='SELECTION') == {'FINISHED'}
    assert ob.mode == 'EDIT'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert close(ob.location, (3, 0, 1))
    assert close(sum(world_verts(ob), Vector()) / 8, (2, 0, 0))


def test_origin_to_active_vertex():
    ob = cube()
    select_only(ob)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    bpy.ops.mesh.select_all(action='DESELECT')
    bm.verts.ensure_lookup_table()
    v = bm.verts[0]
    v.select_set(True)
    bm.select_history.add(v)
    target = ob.matrix_world @ v.co
    assert bpy.ops.origin_align.origin_to(target='ACTIVE_VERT') == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    assert close(ob.location, target)


def test_origin_face_normal():
    ob = cube()
    select_only(ob)
    me = ob.data
    me.polygons.active = next(p.index for p in me.polygons if p.normal.x > 0.9)  # +X face
    before = world_verts(ob)
    assert bpy.ops.origin_align.origin_face_normal() == {'FINISHED'}
    assert all(close(a, b) for a, b in zip(before, world_verts(ob)))
    z = ob.matrix_world.to_3x3() @ Vector((0, 0, 1))
    assert close(z, (1, 0, 0))


def test_align_min_x_to_active():
    a = cube((0, 0, 0), name="A")
    b = cube((5, 3, 0), size=4, name="B")
    select_only(a, b, active=a)
    assert bpy.ops.origin_align.align(axis='X', side='MIN', target='ACTIVE') == {'FINISHED'}
    assert close(b.location, (1, 3, 0))  # min x of B (-2 offset) == min x of A (-1)
    assert close(a.location, (0, 0, 0))


def test_distribute_equal_gaps():
    obs = [cube((0, 0, 0), 2, "A"), cube((3, 0, 0), 4, "B"), cube((4, 0, 0), 1, "C"), cube((10, 0, 0), 2, "D")]
    select_only(*obs)
    assert bpy.ops.origin_align.distribute(axis='X', mode='GAPS') == {'FINISHED'}
    dg = bpy.context.evaluated_depsgraph_get()
    order = sorted(obs, key=lambda o: o.location.x)
    assert [o.name for o in order] == ["A", "B", "C", "D"]
    bounds = [m.world_bounds(o, dg) for o in order]
    gaps = [bounds[k + 1][0].x - bounds[k][1].x for k in range(3)]
    assert all(abs(g - gaps[0]) < 1e-5 for g in gaps), gaps
    assert abs(gaps[0] - 1.0) < 1e-5  # span -1..11 = 12, sizes 9 -> 3 gaps of 1
    assert close(obs[0].location, (0, 0, 0)) and close(obs[3].location, (10, 0, 0))


def test_drop_to_floor_and_stack():
    a = cube((0, 0, 5), name="A")
    b = cube((3, 0, -4), size=1, name="B")
    select_only(a, b)
    assert bpy.ops.origin_align.drop(target='FLOOR') == {'FINISHED'}
    assert close(a.location, (0, 0, 1)) and close(b.location, (3, 0, 0.5))
    select_only(a, b, active=b)
    assert bpy.ops.origin_align.drop(target='ACTIVE') == {'FINISHED'}
    assert close(a.location, (0, 0, 2))  # bottom of A on top of B (z=1)


def test_keymap_pref_toggles():
    prefs = bpy.context.preferences.addons[m.__package__].preferences
    assert m._keymaps
    prefs.use_keymap = False
    assert not m._keymaps
    prefs.use_keymap = True
    assert len(m._keymaps) == 2
