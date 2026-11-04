import bmesh
import bpy


def _cube(name, loc=(0, 0, 0), size=2.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _select(target, *cutters):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in (target,) + cutters:
        o.select_set(True)
    bpy.context.view_layer.objects.active = target


def test_difference_then_apply():
    t = _cube("Target")
    c = _cube("Cutter", loc=(1, 1, 1), size=1.0)
    _select(t, c)
    bpy.ops.quick_boolean.apply_op(operation='DIFFERENCE')
    assert t.modifiers[0].type == 'BOOLEAN' and t.modifiers[0].object == c
    assert c.parent == t and c.display_type == 'WIRE' and c.hide_render
    assert "Cutters" in [x.name for x in c.users_collection]
    assert tuple(round(v, 4) for v in c.matrix_world.translation) == (1, 1, 1)
    bpy.ops.quick_boolean.apply_all()
    assert not t.modifiers
    assert "Cutter" not in bpy.data.objects
    assert len(t.data.vertices) > 8


def test_slice_makes_piece():
    t = _cube("T")
    c = _cube("C", loc=(1, 0, 0), size=1.0)
    _select(t, c)
    bpy.ops.quick_boolean.apply_op(operation='SLICE')
    piece = bpy.data.objects["T_slice"]
    assert piece.modifiers[0].operation == 'INTERSECT'
    assert t.modifiers[0].operation == 'DIFFERENCE'
    assert piece.data != t.data
