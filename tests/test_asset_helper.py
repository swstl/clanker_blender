import os
import tempfile

import bmesh
import bpy

ctx = bpy.context
PRE_UID = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-2222-4333-8444-555555555555:Other/Stuff:Other-Stuff"


def _coll(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or ctx.scene.collection).children.link(c)
    return c


def _cube(name, coll):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def _setup():
    """Saved .blend in a temp dir with Lib > Props > Chairs, plus a pre-existing cats file."""
    folder = tempfile.mkdtemp(prefix="asset_helper_")
    with open(os.path.join(folder, "blender_assets.cats.txt"), "w") as f:
        f.write(f"VERSION 1\n\n{OTHER}\n{PRE_UID}:Lib/Props:Lib-Props\n")
    lib = _coll("Lib")
    props = _coll("Props", lib)
    chairs = _coll("Chairs", props)
    lamp = _cube("Lamp", props)
    chair = _cube("Chair", chairs)
    mat = bpy.data.materials.new("Wood")
    chair.data.materials.append(mat)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(folder, "lib.blend"))
    s = ctx.scene.asset_helper
    s.root, s.mode, s.studio = lib, 'OBJECTS', False
    s.author, s.description = "", ""
    return folder, lamp, chair, mat


def _cats(folder):
    lines = open(os.path.join(folder, "blender_assets.cats.txt")).read().splitlines()
    return lines, {l.split(":")[1]: l.split(":")[0] for l in lines if l.count(":") == 2}


def test_collections_to_catalogs_objects():
    folder, lamp, chair, _ = _setup()
    ctx.scene.asset_helper.author = "Me"
    assert bpy.ops.asset_helper.collections_to_catalogs() == {'FINISHED'}
    lines, cats = _cats(folder)
    assert OTHER in lines, "pre-existing lines kept"
    assert cats["Lib/Props"] == PRE_UID, "existing UUID reused"
    assert set(cats) == {"Other/Stuff", "Lib", "Lib/Props", "Lib/Props/Chairs"}
    assert chair.asset_data.catalog_id == cats["Lib/Props/Chairs"]
    assert lamp.asset_data.catalog_id == PRE_UID
    assert {t.name for t in chair.asset_data.tags} == {"Lib", "Props", "Chairs"}
    assert chair.asset_data.author == "Me"
    # Second run: same file, no duplicates.
    before = open(os.path.join(folder, "blender_assets.cats.txt")).read()
    bpy.ops.asset_helper.collections_to_catalogs()
    assert open(os.path.join(folder, "blender_assets.cats.txt")).read() == before
    assert len(chair.asset_data.tags) == 3


def test_collections_mode_and_materials_mode():
    folder, lamp, chair, mat = _setup()
    ctx.scene.asset_helper.mode = 'COLLECTIONS'
    bpy.ops.asset_helper.collections_to_catalogs()
    _, cats = _cats(folder)
    chairs = bpy.data.collections["Chairs"]
    assert chairs.asset_data.catalog_id == cats["Lib/Props"]
    assert bpy.data.collections["Props"].asset_data is None  # not a leaf
    assert chair.asset_data is None
    ctx.scene.asset_helper.mode = 'MATERIALS'
    bpy.ops.asset_helper.collections_to_catalogs()
    assert mat.asset_data.catalog_id == cats["Lib/Props/Chairs"]
    assert chair.asset_data is None


def test_batch_mark_and_clear():
    _, lamp, chair, mat = _setup()
    s = ctx.scene.asset_helper
    s.use_objects, s.use_materials, s.description = True, True, "desc"
    for ob in ctx.scene.objects:
        ob.select_set(ob == chair)
    assert bpy.ops.asset_helper.batch(clear=False) == {'FINISHED'}
    assert chair.asset_data and mat.asset_data and lamp.asset_data is None
    assert "Chairs" in chair.asset_data.tags and mat.asset_data.description == "desc"
    chair.asset_generate_preview()
    assert chair.preview
    bpy.ops.asset_helper.batch(clear=True)
    assert chair.asset_data is None and mat.asset_data is None
    assert chair.preview is None


def test_previews_studio_and_default():
    _, lamp, chair, mat = _setup()
    s = ctx.scene.asset_helper
    chair.asset_mark()
    mat.asset_mark()
    lamp.asset_mark()
    s.studio, s.size = True, 64
    n_scenes, n_obs = len(bpy.data.scenes), len(bpy.data.objects)
    assert bpy.ops.asset_helper.previews() == {'FINISHED'}
    for idb in (chair, mat):
        assert idb.preview.is_image_custom and tuple(idb.preview.image_size) == (64, 64)
        assert any(v > 0 for v in idb.preview.image_pixels_float[3::4]), "rendered something opaque"
    assert len(bpy.data.scenes) == n_scenes and len(bpy.data.objects) == n_obs, "temp data removed"
    s.studio = False
    lamp.preview_ensure()
    assert bpy.ops.asset_helper.previews() == {'FINISHED'}


def test_register_library():
    _setup()
    libs = ctx.preferences.filepaths.asset_libraries
    n = len(libs)
    assert bpy.ops.asset_helper.register_library() == {'FINISHED'}
    assert len(libs) == n + 1
    assert os.path.normpath(libs[-1].path) == os.path.dirname(bpy.data.filepath)
    assert bpy.ops.asset_helper.register_library() == {'CANCELLED'}
    libs.remove(libs[-1])
