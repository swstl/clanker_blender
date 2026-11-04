import bmesh
import bpy


def _cube(name):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def test_assign_modes():
    a, b = _cube("A"), _cube("B")
    red, blue = bpy.data.materials.new("Red"), bpy.data.materials.new("Blue")
    a.data.materials.append(red)
    a.data.materials.append(red)
    for o in (a, b):
        o.select_set(True)
    bpy.ops.material_swatches.assign(name="Blue", mode='REPLACE')
    assert [s.material for s in a.material_slots] == [blue, blue]
    assert [s.material for s in b.material_slots] == [blue]
    bpy.ops.material_swatches.assign(name="Red", mode='ADD')
    assert len(b.material_slots) == 2


def test_edit_mode_faces():
    ob = _cube("C")
    ob.data.materials.append(bpy.data.materials.new("Base"))
    green = bpy.data.materials.new("Green")
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    for f in bm.faces:
        f.select = f.normal.z > 0.5
    bpy.ops.material_swatches.assign(name="Green")
    bpy.ops.object.mode_set(mode='OBJECT')
    assert ob.material_slots[1].material == green
    assert sorted(p.material_index for p in ob.data.polygons) == [0, 0, 0, 0, 0, 1]


def test_random_viewport_colors_distinct():
    ms = [bpy.data.materials.new(f"M{i}") for i in range(5)]
    bpy.ops.material_swatches.random_viewport_colors()
    cols = {tuple(round(c, 2) for c in m.diffuse_color) for m in ms}
    assert len(cols) == 5
