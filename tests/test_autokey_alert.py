import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.autokey_alert")


def test_should_draw_follows_autokey():
    ts = bpy.context.scene.tool_settings
    ts.use_keyframe_insert_auto = False
    assert not m.should_draw(bpy.context)
    ts.use_keyframe_insert_auto = True
    assert m.should_draw(bpy.context)
    ts.use_keyframe_insert_auto = False


def test_frame_rects_cover_edges():
    r = m.frame_rects(100, 50, 4)
    assert (0, 0, 100, 4) in r and (96, 0, 100, 50) in r
    assert m._handle[0] is not None
