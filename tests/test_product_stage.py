import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


def _bottle():
    me = bpy.data.meshes.new("B")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, segments=16, radius1=0.3, radius2=0.3, depth=1.2, cap_ends=True)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("Bottle", me)
    ob.location = (2, 1, 0.6)
    bpy.context.scene.collection.objects.link(ob)
    ob.select_set(True)
    return ob


def test_build_frames_and_rebuild_remove():
    ob = _bottle()
    sc = bpy.context.scene
    bpy.ops.product_stage.build()
    c = bpy.data.collections["ProductStage"]
    names = sorted(o.name for o in c.objects)
    assert names == ["Cyclorama", "StageCam", "StageFill", "StageKey", "StageRim"]
    bpy.context.view_layer.update()
    for corner in ob.bound_box:
        co = world_to_camera_view(sc, sc.camera, ob.matrix_world @ Vector(corner))
        assert 0 <= co.x <= 1 and 0 <= co.y <= 1 and co.z > 0
    back = bpy.data.objects["Cyclorama"]
    assert abs(back.location.z - 0.0) < 1e-4  # floor at the object's lowest point
    zs = [v.co.z for v in back.data.vertices]
    assert max(zs) > 1.0
    key = bpy.data.objects["StageKey"]
    d = key.matrix_world.to_3x3() @ Vector((0, 0, -1))
    to = (Vector((2, 1, 0.6)) - key.location).normalized()
    assert d.dot(to) > 0.99  # aimed at the product
    bpy.ops.product_stage.build()  # rebuild doesn't duplicate
    assert len(bpy.data.collections["ProductStage"].objects) == 5
    bpy.ops.product_stage.remove()
    assert "ProductStage" not in bpy.data.collections and "StageKey" not in bpy.data.objects
