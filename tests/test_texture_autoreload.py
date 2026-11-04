import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.texture_autoreload")


def _write(path, color):
    img = bpy.data.images.new("w", 2, 2)
    img.pixels = list(color) * 4
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)


def test_reload_on_change_and_skip_dirty():
    path = os.path.join(tempfile.mkdtemp(), "t.png")
    _write(path, (1, 0, 0, 1))
    img = bpy.data.images.load(path)
    assert img.pixels[0] == 1.0
    assert m.check() == []  # first pass only records
    _write(path, (0, 0, 1, 1))
    os.utime(path, (os.path.getmtime(path) + 5,) * 2)
    assert m.check() == [img.name]
    assert img.pixels[0] == 0.0 and img.pixels[2] == 1.0
    img.pixels[0] = 0.5  # unsaved paint
    os.utime(path, (os.path.getmtime(path) + 5,) * 2)
    assert m.check() == []
