import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.time_tools")


def _keyed():
    ob = bpy.data.objects.new("T", None)
    bpy.context.scene.collection.objects.link(ob)
    for f in (1, 10, 20, 30):
        ob.location.x = f
        ob.keyframe_insert("location", index=0, frame=f)
    return ob


def _frames(ob):
    return [kp.co.x for fc in m._fcurves(ob.animation_data.action) for kp in fc.keyframe_points]


def test_insert_time():
    ob = _keyed()
    bpy.context.scene.timeline_markers.new("M", frame=25)
    bpy.context.scene.frame_end = 40
    m.shift_time(bpy.context, 10, 5)
    assert _frames(ob) == [1, 10, 25, 35]
    assert bpy.context.scene.timeline_markers["M"].frame == 30
    assert bpy.context.scene.frame_end == 45


def test_remove_time_deletes_cut():
    ob = _keyed()
    m.shift_time(bpy.context, 10, -10)  # removes (10, 20]
    assert _frames(ob) == [1, 10, 20]


def test_rescale_fps():
    ob = _keyed()
    bpy.context.scene.render.fps = 24
    bpy.context.scene.render.fps_base = 1
    m.rescale_fps(bpy.context, 24, 48)
    assert _frames(ob) == [2, 20, 40, 60]
    assert bpy.context.scene.render.fps == 48


def test_snap_operator():
    ob = _keyed()
    ob.select_set(True)
    kp = next(m._fcurves(ob.animation_data.action)).keyframe_points[1]
    kp.co.x = 10.4
    bpy.ops.time_tools.snap_subframes(scope='ALL')
    assert kp.co.x == 10
