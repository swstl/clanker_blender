import importlib
import numpy as np
import bpy

m = importlib.import_module("bl_ext.clanker.image_palette")


def _img():
    img = bpy.data.images.new("Pal", 20, 10)
    px = np.zeros((10, 20, 4), dtype=np.float32)
    px[:, :14] = (1, 0, 0, 1)   # 70% red
    px[:, 14:] = (0, 0, 1, 1)   # 30% blue
    img.pixels.foreach_set(px.ravel())
    return img


def test_kmeans_finds_two_colors_by_size():
    img = _img()
    c, sizes = m.kmeans(m.image_pixels(img), 2)
    assert np.allclose(c[0], (1, 0, 0), atol=1e-3) and np.allclose(c[1], (0, 0, 1), atol=1e-3)
    assert sizes[0] > sizes[1]
    assert m.to_hex((1, 0, 0)) == "#FF0000"
    assert m.to_hex((0.2159, 0.2159, 0.2159)) == "#808080"


def test_operator_palette_and_materials():
    img = _img()
    bpy.ops.image_palette.extract(image=img.name, count=4, materials=True, copy_hex=False)
    pal = bpy.data.palettes["Pal"]
    assert len(pal.colors) == 2  # only 2 distinct colors exist
    assert "Pal_FF0000" in bpy.data.materials
