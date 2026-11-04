import importlib
import os
import tempfile
import time
import bpy

m = importlib.import_module("bl_ext.clanker.script_watcher")


def test_runs_file_on_change():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.py")
    open(p, "w").write("import bpy\nbpy.context.scene['sw_test'] = 1\n")
    s = bpy.context.window_manager.script_watcher
    s.path = p
    bpy.ops.script_watcher.toggle()
    assert s.active
    m._tick()
    assert bpy.context.scene["sw_test"] == 1 and s.last_result.startswith("OK")
    m._tick()  # unchanged: no re-run
    open(p, "w").write("import bpy\nbpy.context.scene['sw_test'] = 2\n")
    os.utime(p, (time.time() + 5,) * 2)
    m._tick()
    assert bpy.context.scene["sw_test"] == 2
    open(p, "w").write("raise ValueError('boom')\n")
    os.utime(p, (time.time() + 10,) * 2)
    m._tick()
    assert s.last_result.startswith("ERROR") and "boom" in s.last_result
    open(p, "w").write("import sys\nsys.exit(3)\n")
    os.utime(p, (time.time() + 15,) * 2)
    m._tick()  # must not quit Blender
    assert s.last_result.startswith("ERROR")
    bpy.ops.script_watcher.toggle()
    assert not s.active and not m.watching()
    s.active = True  # as loaded from a .blend saved while watching: no timer behind it
    bpy.ops.script_watcher.toggle()
    assert s.active and m.watching()
    bpy.ops.script_watcher.toggle()
    assert not s.active and not m.watching()


def test_module_for_folder_finds_addon():
    folder = os.path.dirname(m.__file__)
    assert m.module_for_folder(folder) == "bl_ext.clanker.script_watcher"
