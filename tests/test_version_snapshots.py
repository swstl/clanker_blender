import importlib
import os
import tempfile
import time
import bpy

m = importlib.import_module("bl_ext.clanker.version_snapshots")


def test_snapshot_and_prune():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "shot.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=False)
    try:
        p = m._prefs()
        p.keep = 0
        made = []
        for i in range(4):
            made.append(m.snapshot())
            os.utime(made[-1], (time.time() + i, time.time() + i))  # distinct mtimes
        named = m.snapshot("before rig")
        p.keep = 2
        m.prune()
        vs = m.list_versions()
        assert len(set(made)) == 4, made
        assert len([v for v in vs if not v[2]]) <= 2, vs
        assert any(v[2] == "before-rig" for v in vs), vs
        assert os.path.isfile(named)
        assert os.path.dirname(named) == os.path.join(d, "_versions")
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)


def test_timer_waits_for_new_changes():
    # is_dirty stays True after a copy-save: only a depsgraph change may trigger the next snapshot
    d = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(d, "idle.blend"), copy=False)
    try:
        m._changed[0] = False
        m._timer()
        assert m.list_versions() == []
        ob = bpy.data.objects.new("vs_probe", None)
        bpy.context.scene.collection.objects.link(ob)
        bpy.context.view_layer.update()
        assert m._changed[0]
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
