import bmesh
import bpy


def test_set_values_and_flags():
    me = bpy.data.meshes.new("C")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("C", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.edge_weights_pie.value(kind='BEVEL', value=0.5)
    bpy.ops.edge_weights_pie.value(kind='CREASE', value=1.0)
    bpy.ops.edge_weights_pie.bevel_and_sharp()
    bpy.ops.object.mode_set(mode='OBJECT')
    bw = [d.value for d in me.attributes["bevel_weight_edge"].data]
    cr = [d.value for d in me.attributes["crease_edge"].data]
    assert all(v == 1.0 for v in bw) and all(v == 1.0 for v in cr)
    assert all(me.attributes["sharp_edge"].data[i].value for i in range(len(me.edges)))
