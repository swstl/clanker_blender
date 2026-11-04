import importlib
import json
import math
import os
import tempfile

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

m = importlib.import_module("bl_ext.clanker.quick_retarget")

# Source: Mixamo-like names, FBX-style object transform (rot X 90, scale 0.01), T-pose.
SRC = [  # name, head, tail (world), parent
    ("mixamorig:Hips", (0, 0, 1), (0, 0, 1.1), None),
    ("mixamorig:Spine", (0, 0, 1.1), (0, 0, 1.4), "mixamorig:Hips"),
    ("mixamorig:LeftArm", (0.2, 0, 1.4), (0.5, 0, 1.4), "mixamorig:Spine"),
    ("mixamorig:LeftForeArm", (0.5, 0, 1.4), (0.8, 0, 1.4), "mixamorig:LeftArm"),
    ("mixamorig:LeftUpLeg", (0.1, 0, 1), (0.1, 0, 0.5), "mixamorig:Hips"),
    ("mixamorig:LeftLeg", (0.1, 0, 0.5), (0.1, 0, 0.05), "mixamorig:LeftUpLeg"),
    ("mixamorig:LeftToe_End", (0.1, 0, 0.05), (0.1, -0.1, 0), "mixamorig:LeftLeg"),
]
# Target: twice as tall, pelvis points down, arms in A-pose with other rolls, extra ORG copies.
_a = 0.6 / math.sqrt(2)
TGT = [
    ("pelvis", (0, 0, 2), (0, 0, 1.8), None, 0.0),
    ("spine", (0, 0, 2.2), (0, 0, 2.8), "pelvis", 0.5),
    ("upper_arm.L", (0.4, 0, 2.8), (0.4 + _a, 0, 2.8 - _a), "spine", 1.0),
    ("forearm.L", (0.4 + _a, 0, 2.8 - _a), (0.4 + 2 * _a, 0.1, 2.8 - 2 * _a), "upper_arm.L", -0.7),
    ("thigh.L", (0.2, 0, 2), (0.25, 0, 1), "pelvis", 0.3),
    ("shin.L", (0.25, 0, 1), (0.2, 0.05, 0.1), "thigh.L", 0.0),
    ("ORG-upper_arm.L", (0.4, 0, 2.8), (0.4 + _a, 0, 2.8 - _a), "spine", 0.0),
]
SRC_MW = Matrix.LocRotScale(Vector((3, 1, 0)), Euler((math.pi / 2, 0, 0)), Vector((0.01,) * 3))


def _armature(name, bones, mw):
    ob = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    bpy.context.scene.collection.objects.link(ob)
    ob.matrix_world = mw
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    inv = mw.inverted()
    for b in bones:
        eb = ob.data.edit_bones.new(b[0])
        eb.head, eb.tail = inv @ Vector(b[1]), inv @ Vector(b[2])
        if len(b) > 4:
            eb.roll = b[4]
        if b[3]:
            eb.parent = ob.data.edit_bones[b[3]]
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob


def _rigs():
    src = _armature("Src", SRC, SRC_MW)
    tgt = _armature("Tgt", TGT, Matrix.Translation((-2, 0, 0)))
    tgt.pose.bones["upper_arm.L"].rotation_mode = 'XYZ'  # exercise euler output
    return src, tgt


def _animate(src, name="Walk", frames=(1, 5, 10), seed=1.0):
    src.animation_data_create().action = bpy.data.actions.new(name)
    for i, f in enumerate(frames):
        for j, pb in enumerate(src.pose.bones):
            k = seed * (i + 1) * (j + 1)
            pb.rotation_quaternion = Euler((0.3 * math.sin(k), 0.5 * math.cos(k), 0.4 * math.sin(2 * k))).to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=f)
        hips = src.pose.bones["mixamorig:Hips"]
        hips.location = (10 * i, 5 * i, -3 * i)
        hips.keyframe_insert("location", frame=f)
    return src.animation_data.action


def _world(ob, bone):
    pb = ob.pose.bones[bone]
    return ob.matrix_world @ pb.matrix, ob.matrix_world @ pb.head, ob.matrix_world @ pb.tail


def _rest_rot(ob, bone):
    return (ob.matrix_world @ ob.data.bones[bone].matrix_local).to_quaternion()


def _close_q(a, b, tol=1e-3):
    return abs(abs(a.normalized().dot(b.normalized())) - 1) < tol


def test_bone_key():
    k = m.bone_key
    assert k("mixamorig:LeftArm") == k("upper_arm.L") == k("DEF-upper_arm.L") == k("upper_arm_fk.L") == ('upperarm', 'L')
    assert k("mixamorig1:RightUpLeg") == k("thigh_r") == k("Bip01 R Thigh") == ('thigh', 'R')
    assert k("mixamorig:LeftHandIndex1") == k("f_index.01.L") == ('index1', 'L')
    assert k("Hips") == k("pelvis") == ('hips', '')
    assert k("LeftLeg") == k("calf_l") == ('shin', 'L')
    assert k("mixamorig:LeftToe_End") is None


def test_auto_map():
    src, tgt = _rigs()
    pairs = {s: (t, loc, mode) for s, t, loc, mode in m.auto_map(src, tgt)}
    assert pairs["mixamorig:LeftArm"] == ("upper_arm.L", False, 'ALIGN')  # control beats ORG-
    assert pairs["mixamorig:Hips"] == ("pelvis", True, 'OFFSET')
    assert pairs["mixamorig:LeftLeg"][0] == "shin.L"
    assert len(pairs) == 6


def test_retarget_matches_world_directions():
    src, tgt = _rigs()
    _animate(src)
    pairs = m.auto_map(src, tgt)
    (new,) = m.retarget(bpy.context.scene, src, tgt, pairs)
    assert new.name == "Tgt_Walk" and tgt.animation_data.action == new
    assert src.animation_data.action.name == "Walk"
    for f in (1, 3, 5, 10):
        bpy.context.scene.frame_set(f)
        for s, t, loc, mode in pairs:
            sm, sh, st = _world(src, s)
            tm, th, tt = _world(tgt, t)
            if mode == 'ALIGN':
                assert (st - sh).normalized().dot((tt - th).normalized()) > 0.9999, (f, s)
            else:  # same rotation change since rest
                ds = sm.to_quaternion() @ _rest_rot(src, s).inverted()
                dt = tm.to_quaternion() @ _rest_rot(tgt, t).inverted()
                assert _close_q(ds, dt), (f, s)
        # root motion scaled by hip height ratio (2.0)
        s_off = _world(src, "mixamorig:Hips")[1] - SRC_MW @ src.data.bones["mixamorig:Hips"].head_local
        t_off = _world(tgt, "pelvis")[1] - tgt.matrix_world @ tgt.data.bones["pelvis"].head_local
        assert (s_off * 2 - t_off).length < 1e-4, (f, s_off, t_off)


def test_batch_and_operator():
    src, tgt = _rigs()
    _animate(src, "Run", seed=0.7)
    _animate(src, "Walk", frames=(1, 20))
    bpy.data.actions.new("Unrelated")
    st = bpy.context.scene.quick_retarget
    st.source, st.target, st.batch = src, tgt, True
    assert bpy.ops.quick_retarget.auto_map() == {'FINISHED'}
    assert len(st.bone_map) == 6
    assert bpy.ops.quick_retarget.retarget() == {'FINISHED'}
    assert {"Tgt_Run", "Tgt_Walk"} <= set(bpy.data.actions.keys())
    assert "Tgt_Unrelated" not in bpy.data.actions
    assert tuple(bpy.data.actions["Tgt_Walk"].frame_range) == (1, 20)
    assert src.animation_data.action.name == "Walk"


def test_save_load_map():
    src, tgt = _rigs()
    st = bpy.context.scene.quick_retarget
    st.source, st.target = src, tgt
    bpy.ops.quick_retarget.auto_map()
    st.bone_map[0].rotation = 'NONE'
    path = os.path.join(tempfile.mkdtemp(), "map.json")
    assert bpy.ops.quick_retarget.save_map(filepath=path) == {'FINISHED'}
    before = [(i.source, i.target, i.use_location, i.rotation) for i in st.bone_map]
    st.bone_map.clear()
    assert bpy.ops.quick_retarget.load_map(filepath=path) == {'FINISHED'}
    assert [(i.source, i.target, i.use_location, i.rotation) for i in st.bone_map] == before
    assert json.load(open(path))["bone_map"][0]["rotation"] == 'NONE'


def test_rigify_preset():
    src, _ = _rigs()
    tgt = _armature("Rig", [("torso", (0, 0, 1), (0, -0.3, 1), None),
                            ("upper_arm_fk.L", (0.2, 0, 1.4), (0.5, 0, 1.2), "torso"),
                            ("thigh_fk.L", (0.1, 0, 1), (0.1, 0, 0.5), "torso"),
                            ("ORG-upper_arm.L", (0.2, 0, 1.4), (0.5, 0, 1.2), "torso")], Matrix())
    pairs = {s: (t, loc) for s, t, loc, _ in m.rigify_map(src, tgt)}
    assert pairs == {"mixamorig:Hips": ("torso", True), "mixamorig:LeftArm": ("upper_arm_fk.L", False),
                     "mixamorig:LeftUpLeg": ("thigh_fk.L", False)}
