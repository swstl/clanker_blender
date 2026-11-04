import bmesh
import bpy


def _cube(name, loc, parent=None):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    ob.rotation_euler.z = 0.5
    bpy.context.scene.collection.objects.link(ob)
    if parent:
        ob.parent = parent
    return ob


def _sel(*obs):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]


def test_group_keeps_world_and_nested_hierarchy():
    a, b = _cube("A", (0, 0, 1)), _cube("B", (4, 0, 1))
    c = _cube("C", (4, 3, 1), parent=b)
    bpy.context.view_layer.update()
    before = {o.name: o.matrix_world.copy() for o in (a, b, c)}
    _sel(a, b, c)
    bpy.ops.group_empty.group(pivot='BOTTOM')
    g = bpy.context.active_object
    bpy.context.view_layer.update()
    assert a.parent == g and b.parent == g and c.parent == b
    assert abs(g.location.z) < 1e-4  # bottom of cubes (z=1-1)
    for o in (a, b, c):
        assert (o.matrix_world.translation - before[o.name].translation).length < 1e-4
    g.location.x += 10
    bpy.context.view_layer.update()
    assert abs(c.matrix_world.translation.x - before["C"].translation.x - 10) < 1e-4
    bpy.ops.group_empty.ungroup()
    bpy.context.view_layer.update()
    assert "Group" not in bpy.data.objects and a.parent is None
    assert abs(a.matrix_world.translation.x - 10) < 1e-4


def test_select_whole_group():
    a, b = _cube("A", (0, 0, 0)), _cube("B", (3, 0, 0))
    _sel(a, b)
    bpy.ops.group_empty.group()
    g = bpy.context.active_object
    _sel(a)
    bpy.ops.group_empty.select_group()
    assert g.select_get() and b.select_get()
