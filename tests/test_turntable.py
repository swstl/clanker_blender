import math
import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


def _cube():
    me = bpy.data.meshes.new("C")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("C", me)
    ob.location = (5, 0, 1)
    bpy.context.scene.collection.objects.link(ob)
    ob.select_set(True)
    return ob


def _all_in_frame(sc, ob):
    for c in ob.bound_box:
        co = world_to_camera_view(sc, sc.camera, ob.matrix_world @ Vector(c))
        if not (0 <= co.x <= 1 and 0 <= co.y <= 1 and co.z > 0):
            return False
    return True


def test_camera_orbit_frames_object_all_turn():
    sc = bpy.context.scene
    sc.frame_start = 1
    ob = _cube()
    bpy.ops.turntable.add(frames=40)
    assert sc.frame_end == 40
    for f in (1, 11, 21, 31):
        sc.frame_set(f)
        assert _all_in_frame(sc, ob), f
    sc.frame_set(21)
    piv = bpy.data.objects["Turntable"]
    assert abs(piv.matrix_world.to_euler().z) > 3.0  # half turn ~ pi
    assert piv.animation_data.action


def test_objects_spin_mode():
    sc = bpy.context.scene
    ob = _cube()
    bpy.ops.turntable.add(frames=20, mode='OBJECTS')
    assert ob.parent == bpy.data.objects["Turntable"]
    sc.frame_set(1)
    assert _all_in_frame(sc, ob)
