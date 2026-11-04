import bpy


def _cam(name, rx=None, ry=None):
    cd = bpy.data.cameras.new(name)
    ob = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(ob)
    if rx:
        cd.cm_use_resolution = True
        cd.cm_resolution_x, cd.cm_resolution_y = rx, ry
    return ob


def test_activate_applies_resolution():
    a = _cam("A", 1080, 1920)
    _cam("B")
    bpy.ops.camera_manager.activate(name="A", look_through=False)
    sc = bpy.context.scene
    assert sc.camera == a
    assert (sc.render.resolution_x, sc.render.resolution_y) == (1080, 1920)


def test_cycle_and_marker_binding():
    a, b = _cam("A"), _cam("B", 640, 480)
    sc = bpy.context.scene
    sc.camera = a
    bpy.ops.camera_manager.cycle(step=1)
    assert sc.camera == b
    sc.frame_set(1)
    bpy.ops.camera_manager.bind_marker(name="A")
    sc.frame_set(10)
    bpy.ops.camera_manager.bind_marker(name="B")
    sc.render.resolution_x = 100
    sc.frame_set(1)
    sc.frame_set(12)
    assert sc.camera == b
    assert sc.render.resolution_x == 640
