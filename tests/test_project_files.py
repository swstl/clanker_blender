import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.project_files")


def _png(path):
    img = bpy.data.images.new("tmp", 4, 4)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)


def test_collect_and_report():
    outside = tempfile.mkdtemp()
    proj = tempfile.mkdtemp()
    _png(os.path.join(outside, "wood.png"))
    img = bpy.data.images.load(os.path.join(outside, "wood.png"))
    gone = bpy.data.images.load(os.path.join(outside, "wood.png"))
    gone.filepath = os.path.join(outside, "missing.png")
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "p.blend"))
    try:
        rows = {r[1].name: r[3] for r in m.external_files()}
        assert rows[img.name] is True and rows[gone.name] is False
        copied, skipped = m.collect()
        assert copied == 1 and skipped == 1
        assert img.filepath.startswith("//textures"), img.filepath
        assert os.path.isfile(os.path.join(proj, "textures", "wood.png"))
        assert m.collect() == (0, 1)  # idempotent
        bpy.ops.project_files.bootstrap(folders="renders, exports")
        assert os.path.isdir(os.path.join(proj, "exports"))
        assert bpy.context.scene.render.filepath.startswith("//renders/")
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
