import bmesh
import bpy


def _cube(name="C", loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def test_object_mode_fit_and_apply():
    ob = _cube(loc=(3, 0, 0))
    bpy.ops.quick_lattice.add(edit=False, align='WORLD')
    lat = bpy.context.active_object
    assert lat.type == 'LATTICE'
    assert tuple(round(v, 3) for v in lat.location) == (3, 0, 0)
    assert abs(lat.scale.x - 2.04) < 1e-3
    assert ob.modifiers[0].object == lat
    lat.data.points[0].co_deform.x -= 0.5  # pull one corner
    bpy.ops.quick_lattice.apply()
    assert not ob.modifiers and "Lattice" not in bpy.data.objects
    xs = [v.co.x for v in ob.data.vertices]
    assert min(xs) < -1.01


def test_edit_mode_vertex_group():
    ob = _cube()
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts:
        v.select = v.co.z > 0
    bm.select_flush(True)
    bpy.ops.quick_lattice.add(edit=False)
    md = ob.modifiers[0]
    assert md.vertex_group == "QuickLattice"
    assert len([v for v in ob.data.vertices if v.groups]) == 4
    lat = md.object
    assert abs(lat.scale.z - 0.2 * 1.02) < 1e-4  # flat selection -> 10% thick box
    bpy.ops.quick_lattice.remove()
    assert not ob.modifiers and "QuickLattice" not in ob.vertex_groups
