import importlib
import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.dimensions_overlay")


def test_bounds_and_lines():
    me = bpy.data.meshes.new("C")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("C", me)
    ob.scale = (1, 2, 3)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    mn, mx = m.selection_bounds([ob])
    lens = [round((e - s).length, 4) for _, s, e in m.measure_lines(mn, mx)]
    assert lens == [2, 4, 6]
    assert "m" in m.fmt(bpy.context.scene, 2.0)
