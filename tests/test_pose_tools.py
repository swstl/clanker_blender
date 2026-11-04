import importlib
import math
import bpy

m = importlib.import_module("bl_ext.clanker.pose_tools")
FRAMES = (1, 10, 20)


def _rig():
    arm = bpy.data.armatures.new("Rig")
    ob = bpy.data.objects.new("Rig", arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    for n, x in (("arm.L", 1), ("arm.R", -1), ("ctrl", 0)):
        b = arm.edit_bones.new(n)
        b.head, b.tail = (x, 0, 0), (x, 0, 1)
    bpy.ops.object.mode_set(mode='POSE')
    for pb in ob.pose.bones:
        pb.select = False
    L, R = ob.pose.bones["arm.L"], ob.pose.bones["arm.R"]
    L["ik"] = 0.0
    for f in FRAMES:
        for pb in (L, R):
            pb.location = (f * 0.1, 0, 0)
            pb.rotation_quaternion = (1, 0, 0, 0)
            pb.keyframe_insert("location", frame=f)
            pb.keyframe_insert("rotation_quaternion", frame=f)
        L.keyframe_insert('["ik"]', frame=f)
    return ob


def _fc(ob, bone, prop, i=0):
    cb = m._channelbag(ob)
    return cb.fcurves.find(f'pose.bones["{bone}"].{prop}', index=i)


def _vals(fc):
    return [round(k.co.y, 4) for k in fc.keyframe_points]


def test_paste_flipped_to_range():
    ob = _rig()
    L, R = ob.pose.bones["arm.L"], ob.pose.bones["arm.R"]
    L.location = (0.5, 0.2, 0.3)
    q = (0.9, 0.1, 0.2, 0.3)
    L.rotation_quaternion = q
    m.copy_pose(ob, [L], 1)
    n = m.paste_pose(ob, [R], lambda pb: m.key_frames(ob, pb, 5, 20), flipped=True)
    assert n == 2  # keys 10 and 20
    assert _vals(_fc(ob, "arm.R", "location", 0)) == [0.1, -0.5, -0.5]
    assert _vals(_fc(ob, "arm.R", "location", 1)) == [0, 0.2, 0.2]
    assert [_vals(_fc(ob, "arm.R", "rotation_quaternion", i))[1] for i in range(4)] == [0.9, 0.1, -0.2, -0.3]


def test_additive_paste():
    ob = _rig()
    L = ob.pose.bones["arm.L"]
    bpy.context.scene.frame_set(10)
    L.location.y += 2  # offset on top of the animated pose at frame 10
    L["ik"] = 0.5
    m.copy_pose(ob, [L], 10)
    m.paste_pose(ob, [L], lambda pb: m.key_frames(ob, pb, 1, 20), additive=True)
    assert _vals(_fc(ob, "arm.L", "location", 0)) == [0.1, 1.0, 2.0]  # x untouched
    assert _vals(_fc(ob, "arm.L", "location", 1)) == [2, 2, 2]
    cb = m._channelbag(ob)
    assert _vals(cb.fcurves.find('pose.bones["arm.L"]["ik"]')) == [0.5, 0.5, 0.5]


def test_mirror_animation_with_offset():
    ob = _rig()
    L = ob.pose.bones["arm.L"]
    m.mirror_animation(ob, [L], offset=5)
    fc = _fc(ob, "arm.R", "location", 0)
    assert [k.co.x for k in fc.keyframe_points] == [6, 15, 25]
    assert _vals(fc) == [-0.1, -1.0, -2.0]
    # wrapped into a 1..21 cycle with half-cycle offset
    m.mirror_animation(ob, [L], offset=10, cycle=(1, 21))
    fc = _fc(ob, "arm.R", "location", 0)
    assert [k.co.x for k in fc.keyframe_points] == [10, 11, 20]  # 20+10 wraps to 10
    assert _vals(fc) == [-2.0, -0.1, -1.0]


def test_sparse_bake_constraint():
    ob = _rig()
    L, R = ob.pose.bones["arm.L"], ob.pose.bones["arm.R"]
    for f, a in zip(FRAMES, (0, 0.5, 1.0)):
        L.rotation_quaternion = (math.cos(a / 2), 0, 0, math.sin(a / 2))
        L.keyframe_insert("rotation_quaternion", frame=f)
    c = R.constraints.new('COPY_ROTATION')
    c.target, c.subtarget = ob, "arm.L"
    sc = bpy.context.scene
    expected = {}
    for f in FRAMES:
        sc.frame_set(f)
        expected[f] = m._visual_basis(ob, R).to_quaternion()
    frames = m.smart_bake(sc, ob, [R], 1, 20, keys_only=True, clear_constraints=True)
    assert frames == list(FRAMES)
    assert not R.constraints
    fcs = [_fc(ob, "arm.R", "rotation_quaternion", i) for i in range(4)]
    assert [k.co.x for k in fcs[3].keyframe_points] == list(FRAMES)
    for f in FRAMES:
        got = [fc.evaluate(f) for fc in fcs]
        assert all(abs(a - b) < 1e-4 for a, b in zip(got, expected[f])), (f, got, expected[f])
    assert abs(expected[20][3] - math.sin(0.5)) < 1e-4


def test_select_keyed_and_delete():
    ob = _rig()
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.pose_tools.select(kind='KEYED')
    assert {pb.name for pb in ob.pose.bones if pb.select} == {"arm.L", "arm.R"}
    assert m.delete_keys_at(ob, [ob.pose.bones["arm.L"]], 10) == 8  # loc 3 + quat 4 + ik
    assert [k.co.x for k in _fc(ob, "arm.L", "location").keyframe_points] == [1, 20]
    assert len(_fc(ob, "arm.R", "location").keyframe_points) == 3


def test_reset_and_key_all():
    ob = _rig()
    L = ob.pose.bones["arm.L"]
    L.location = (1, 2, 3)
    L.scale = (2, 2, 2)
    L["ik"] = 0.7
    m.reset_bones([L])
    assert tuple(L.location) == (0, 0, 0) and tuple(L.scale) == (1, 1, 1) and L["ik"] == 0
    m.key_whole_character(ob, 30, visual=True)
    assert _fc(ob, "ctrl", "scale", 2).keyframe_points[0].co[:] == (30, 1)
