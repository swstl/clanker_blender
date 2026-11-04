import importlib
import math
import os
import tempfile

import bpy
from mathutils import Euler, Vector

mod = importlib.import_module("bl_ext.clanker.paste_reference")
TMP = tempfile.mkdtemp(prefix="paste_reference_")


def _png(name, w, h):
    img = bpy.data.images.new(name, w, h)
    img.pixels[:] = [0.2, 0.4, 0.8, 1.0] * (w * h)
    path = os.path.join(TMP, name + ".png")
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    return img, path


def test_empty_view_aligned():
    img, _ = _png("wide", 40, 20)
    rot = Euler((math.radians(90), 0, math.radians(30))).to_matrix()
    (ob,) = mod.create_references(bpy.context, [img], 'EMPTY', rot, (1, 2, 3), 2.0,
                                  opacity=0.5, depth='BACK')
    assert ob.empty_display_type == 'IMAGE' and ob.data == img
    assert abs(ob.empty_display_size - 4.0) < 1e-5  # long side = 2 * aspect 2
    assert ob.empty_image_depth == 'BACK' and not ob.show_in_front
    assert abs(ob.color[3] - 0.5) < 1e-5 and ob.use_empty_image_alpha
    assert (ob.location - Vector((1, 2, 3))).length < 1e-5
    assert ob.matrix_world.to_3x3().normalized().to_quaternion().rotation_difference(
        rot.to_quaternion()).angle < 1e-4
    assert bpy.context.view_layer.objects.active == ob


def test_plane_aspect_and_material():
    img, _ = _png("tall", 10, 30)
    (ob,) = mod.create_references(bpy.context, [img], 'PLANE', mod.Matrix(), (0, 0, 0), 3.0)
    dims = ob.dimensions
    assert abs(dims.y - 3.0) < 1e-5 and abs(dims.x - 1.0) < 1e-5
    nodes = ob.data.materials[0].node_tree.nodes
    assert any(n.type == 'TEX_IMAGE' and n.image == img for n in nodes)
    assert ob.data.uv_layers


def test_camera_background():
    img, _ = _png("bg", 8, 8)
    try:
        mod.create_references(bpy.context, [img], 'CAMERA', mod.Matrix(), (0, 0, 0), 1.0)
        assert False, "should fail without camera"
    except ValueError:
        pass
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    mod.create_references(bpy.context, [img], 'CAMERA', mod.Matrix(), (0, 0, 0), 1.0, opacity=0.3, depth='FRONT')
    bg = cam.data.background_images[0]
    assert bg.image == img and abs(bg.alpha - 0.3) < 1e-5 and bg.display_depth == 'FRONT'
    assert cam.data.show_background_images


def test_load_files_side_by_side():
    _, a = _png("a", 20, 10)
    _, b = _png("b", 10, 10)
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.paste_reference.load(directory=TMP, files=[{"name": "a.png"}, {"name": "b.png"}], fill=0.5)
    obs = sorted((o for o in bpy.data.objects if o.type == 'EMPTY'), key=lambda o: o.location.x)
    assert len(obs) == 2
    h = 2.0  # headless: no view -> 4 * fill
    assert abs(obs[0].location.x - (-(2 * h + h + 0.1 * h) / 2 + h)) < 1e-4
    assert abs(obs[1].location.x - obs[0].location.x - (h + 0.1 * h + h / 2)) < 1e-4  # no overlap + gap
    assert all(abs(o.location.y) < 1e-6 and abs(o.location.z) < 1e-6 for o in obs)


def test_persist_save_and_pack():
    img = bpy.data.images.new("clip", 6, 4)
    mod.persist(img, None)
    assert img.packed_file
    img2 = bpy.data.images.new("clip2", 6, 4)
    mod.persist(img2, os.path.join(TMP, "references"))
    path = bpy.path.abspath(img2.filepath)
    assert os.path.isfile(path) and os.path.basename(path).startswith("ref_")
    with open(path, "rb") as f:
        img3 = mod.image_from_png(f.read(), None)
    assert img3.packed_file and tuple(img3.size) == (6, 4)


def test_paste_failure_reports():
    orig = mod.external_paste
    mod.external_paste = lambda: None
    try:
        bpy.ops.paste_reference.paste()
        assert False, "should report an error"
    except RuntimeError as e:
        assert "No image in the clipboard" in str(e)
    finally:
        mod.external_paste = orig
    assert not bpy.data.objects
