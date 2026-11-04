import bpy
import importlib

m = importlib.import_module("bl_ext.clanker.render_notify")


def test_handlers_registered():
    assert m.on_complete in bpy.app.handlers.render_complete


def test_fmt_time():
    assert m.fmt_time(65) == "1m 05s"
    assert m.fmt_time(3725) == "1h 02m 05s"


def test_complete_respects_min_seconds():
    called = []
    orig = m.notify
    m.notify = lambda *a: called.append(a)
    try:
        m._prefs().sound = False
        m._prefs().min_seconds = 0
        m.on_init(bpy.context.scene)
        m.on_complete(bpy.context.scene)
        assert called, "should notify"
        called.clear()
        m._prefs().min_seconds = 999
        m.on_complete(bpy.context.scene)
        assert not called, "quick render should not notify"
    finally:
        m.notify = orig
