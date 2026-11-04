import importlib
import bpy
from mathutils import Quaternion

m = importlib.import_module("bl_ext.clanker.saved_views")


class FakeR3D:
    view_location = (1, 2, 3)
    view_rotation = Quaternion((0.7071, 0.7071, 0, 0))
    view_distance = 12.0
    view_perspective = 'ORTHO'


class FakeSD:
    lens = 85.0


def test_store_restore_roundtrip():
    v = bpy.context.scene.saved_views.add()
    m.store(v, FakeSD(), FakeR3D())
    r, s = FakeR3D(), FakeSD()
    r.view_location, r.view_distance, r.view_perspective, s.lens = (0, 0, 0), 1, 'PERSP', 50
    m.restore(v, s, r)
    assert tuple(r.view_location) == (1, 2, 3)
    assert r.view_distance == 12 and r.view_perspective == 'ORTHO' and s.lens == 85


def test_camera_from_view():
    sc = bpy.context.scene
    sc.saved_views.clear()
    v = sc.saved_views.add()
    v.name = "Hero"
    m.store(v, FakeSD(), FakeR3D())
    sc.saved_views_index = 0
    bpy.ops.saved_views.to_camera()
    cam = bpy.data.objects["Hero"]
    assert cam.data.lens == 85
    # Camera sits view_distance behind the pivot along the view's +Z.
    assert (cam.location - __import__("mathutils").Vector((1, 2, 3))).length > 11.9
