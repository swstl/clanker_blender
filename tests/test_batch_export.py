import importlib
import os
import tempfile

import bpy

m = importlib.import_module("bl_ext.clanker.batch_export")


def _setup(tmp, fmt, mode):
    s = bpy.context.scene.batch_export
    s.folder, s.format, s.mode = tmp, fmt, mode
    s.prefix = s.suffix = ""
    s.to_origin, s.split_anims, s.open_folder = True, False, False
    return s


def _cube(name, loc, coll=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new(name, me)
    (coll or bpy.context.scene.collection).objects.link(ob)
    ob.location = loc
    return ob


def _export():
    assert bpy.ops.batch_export.export() == {'FINISHED'}


def test_per_object_fbx_glb_obj():
    a, b = _cube("Crate A", (3, 0, 0)), _cube("Barrel", (0, 5, 1))
    child = _cube("Lid", (0, 0, 1))
    child.parent = a
    other = _cube("Unselected", (1, 1, 1))
    for o in (a, b):
        o.select_set(True)
    bpy.context.view_layer.objects.active = b
    for fmt, ext in (('FBX', ".fbx"), ('GLB', ".glb"), ('OBJ', ".obj"), ('GLTF', ".gltf"), ('USD', ".usd")):
        with tempfile.TemporaryDirectory() as tmp:
            s = _setup(tmp, fmt, 'OBJECTS')
            s.prefix = "SM_"
            _export()
            files = sorted(f for f in os.listdir(tmp) if f.endswith(ext))
            assert files == ["SM_Barrel" + ext, "SM_Crate_A" + ext], (fmt, os.listdir(tmp))
        assert tuple(a.location) == (3, 0, 0) and tuple(b.location) == (0, 5, 1)
        assert a.select_get() and b.select_get() and not child.select_get() and not other.select_get()
        assert bpy.context.view_layer.objects.active == b


def test_hierarchy_restores_hidden_child():
    root = _cube("Root", (2, 2, 2))
    kid = _cube("Kid", (1, 0, 0))
    kid.parent = root
    kid.hide_set(True)
    root.select_set(True)
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, 'GLB', 'HIERARCHY')
        _export()
        assert os.listdir(tmp) == ["Root.glb"]
    assert kid.hide_get() and tuple(root.location) == (2, 2, 2)


def test_per_collection():
    for cname in ("Props", "Walls"):
        c = bpy.data.collections.new(cname)
        bpy.context.scene.collection.children.link(c)
        _cube(cname + "1", (4, 0, 0), c).select_set(True)
        _cube(cname + "2", (0, 4, 0), c)
    with tempfile.TemporaryDirectory() as tmp:
        _setup(tmp, 'FBX', 'COLLECTION')
        _export()
        assert sorted(os.listdir(tmp)) == ["Props.fbx", "Walls.fbx"]


def test_split_animations():
    arm = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones.new("Bone")
    eb.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    for name in ("Walk", "Run"):
        arm.pose.bones["Bone"].location = (0, 0, 0)
        arm.pose.bones["Bone"].keyframe_insert("location", frame=1)
        arm.pose.bones["Bone"].location = (0, 1, 0)
        arm.pose.bones["Bone"].keyframe_insert("location", frame=10)
        arm.animation_data.action.name = name
        arm.animation_data.action = None
    arm.select_set(True)
    with tempfile.TemporaryDirectory() as tmp:
        s = _setup(tmp, 'FBX', 'OBJECTS')
        s.split_anims = True
        _export()
        assert sorted(os.listdir(tmp)) == ["Rig.fbx", "Rig_Run.fbx", "Rig_Walk.fbx"]
    assert arm.animation_data.action is None


def test_failure_continues():
    a, b = _cube("A", (0, 0, 0)), _cube("B", (1, 0, 0))
    a.select_set(True)
    b.select_set(True)
    orig = m.run_export

    def flaky(s, path, anim):
        if path.endswith("A.glb"):
            raise RuntimeError("boom")
        assert tuple(b.matrix_world.translation) == (0, 0, 0), "root not moved to origin during export"
        orig(s, path, anim)
    m.run_export = flaky
    try:
        with tempfile.TemporaryDirectory() as tmp:
            written, failed = m.batch_export(bpy.context, _setup(tmp, 'GLB', 'OBJECTS'))
            assert os.listdir(tmp) == ["B.glb"] and len(written) == 1
            assert len(failed) == 1 and "boom" in failed[0], failed
    finally:
        m.run_export = orig


def test_presets():
    s = bpy.context.scene.batch_export
    s.preset = 'UNITY'
    assert s.format == 'FBX'
    s.preset = 'GODOT'
    assert s.format == 'GLB'
    s.prefix = s.suffix = ""
    assert m.file_name(s, "My Mesh:01") == "My_Mesh_01"
