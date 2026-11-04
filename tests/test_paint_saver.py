import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.paint_saver")


def _paint(img, v):
    px = list(img.pixels)
    px[0] = v
    img.pixels = px  # marks the image dirty like a brush stroke


def test_save_with_blend_and_recovery():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "albedo.png")
    tmp = bpy.data.images.new("w", 4, 4)
    tmp.filepath_raw, tmp.file_format = src, 'PNG'
    tmp.save()
    bpy.data.images.remove(tmp)
    img = bpy.data.images.load(src)
    gen = bpy.data.images.new("NewPaint", 8, 8)
    _paint(img, 1.0)
    _paint(gen, 0.5)
    assert set(i.name for i in m.dirty_images()) >= {"albedo.png", "NewPaint"}
    rec = m.recovery_copy(keep=2)
    assert len(rec) >= 2 and all(os.path.isfile(p) for p in rec)
    assert img.filepath == src  # recovery copy doesn't retarget
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(d, "s.blend"))  # save_pre handler fires
    try:
        assert not img.is_dirty and not gen.is_dirty
        assert os.path.isfile(os.path.join(d, "textures", "NewPaint.png"))
        reloaded = bpy.data.images.load(src, check_existing=False)
        assert abs(reloaded.pixels[0] - 1.0) < 0.01
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
