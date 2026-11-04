import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.playblast")


def test_settings_swapped_and_restored_even_on_error():
    sc = bpy.context.scene
    r = sc.render
    r.filepath, r.resolution_percentage = "//final/shot_", 100
    r.image_settings.file_format = 'PNG'
    s = sc.playblast
    s.folder = tempfile.mkdtemp() + "/"
    try:
        with m.swapped_settings(sc, s) as path:
            assert r.image_settings.file_format == 'FFMPEG' and r.resolution_percentage == 50
            assert path.endswith("_v001.mp4")
            raise RuntimeError("render failed")
    except RuntimeError:
        pass
    assert r.filepath == "//final/shot_" and r.resolution_percentage == 100
    assert r.image_settings.file_format == 'PNG'


def test_versioning():
    sc = bpy.context.scene
    s = sc.playblast
    d = tempfile.mkdtemp()
    s.folder = d + "/"
    p1 = m.next_path(sc, s)
    open(p1, "w").close()
    p2 = m.next_path(sc, s)
    assert p1.endswith("_v001.mp4") and p2.endswith("_v002.mp4")
    assert os.path.dirname(p2) == d
