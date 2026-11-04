import bmesh
import bpy


def _setup():
    sc = bpy.context.scene
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    cam.location = (0, 0, 10)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.resolution_x = sc.render.resolution_y = 100
    me = bpy.data.meshes.new("C")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("C", me)
    sc.collection.objects.link(ob)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    return sc, ob


def test_fit_centered_object():
    sc, ob = _setup()
    bpy.ops.render_border.fit(padding=0)
    r = sc.render
    assert r.use_border
    assert abs((r.border_min_x + r.border_max_x) / 2 - 0.5) < 1e-3
    assert 0.0 < r.border_min_x < 0.5 < r.border_max_x < 1.0


def test_follow_moves_border():
    sc, ob = _setup()
    bpy.ops.render_border.follow_selected()
    ob.location.x = 2
    ob.keyframe_insert("location", frame=10)
    ob.location.x = 0
    ob.keyframe_insert("location", frame=1)
    sc.frame_set(1)
    left = sc.render.border_min_x
    sc.frame_set(10)
    assert sc.render.border_min_x > left + 0.05
