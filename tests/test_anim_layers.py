import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.anim_layers")
SCENE = bpy.context.scene


def _obj(name="Cube"):
    ob = bpy.data.objects.new(name, None)
    SCENE.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    SCENE.frame_start, SCENE.frame_end = 1, 50
    return ob


def _key_x(ob, value, frame=10):
    SCENE.frame_set(frame)
    ob.location.x = value
    ob.keyframe_insert("location", index=0, frame=frame)


def _eval_x(ob, frame=10):
    SCENE.frame_set(frame - 1)
    SCENE.frame_set(frame)
    return ob.location.x


def _two_layers(base=1.0, layer=3.0):
    """Base keyed x=base at frame 10, Layer 1 (Combine) keyed so the result is x=layer."""
    ob = _obj()
    bpy.ops.anim_layers.add()
    _key_x(ob, base)
    bpy.ops.anim_layers.add()
    _key_x(ob, layer)
    return ob


def test_add_layers():
    ob = _obj()
    bpy.ops.anim_layers.add()
    ad = ob.animation_data
    assert [t.name for t in ad.nla_tracks] == ["Base"]
    assert ad.use_tweak_mode and ob.anim_layers_index == 0
    bpy.ops.anim_layers.add()
    assert [t.name for t in ad.nla_tracks] == ["Base", "Layer 1"]
    assert ob.anim_layers_index == 1 and ad.use_tweak_mode
    s = ad.nla_tracks[1].strips[0]
    assert s.blend_type == 'COMBINE'
    assert (s.frame_start, s.frame_end) == (1, 50), (s.frame_start, s.frame_end)
    assert s.action_slot is not None


def test_existing_action_becomes_base():
    ob = _obj()
    _key_x(ob, 4.0)
    act = ob.animation_data.action
    bpy.ops.anim_layers.add()
    ad = ob.animation_data
    assert len(ad.nla_tracks) == 1 and ad.nla_tracks[0].strips[0].action == act
    assert _eval_x(ob) == 4.0


def test_keyframe_into_layer_combines():
    ob = _two_layers(1.0, 3.0)
    ad = ob.animation_data
    layer_act = ad.nla_tracks[1].strips[0].action
    assert ad.action == layer_act, "keys must go into the active layer"
    fc = m.anim_utils.action_get_channelbag_for_slot(layer_act, ad.action_slot).fcurves.find("location", index=0)
    assert abs(fc.keyframe_points[0].co.y - 2.0) < 1e-5, "layer stores the offset (+2)"
    assert abs(_eval_x(ob) - 3.0) < 1e-5
    ob.anim_layers_index = 0  # editing the base still shows the full stack
    assert ad.action == ad.nla_tracks[0].strips[0].action
    assert abs(_eval_x(ob) - 3.0) < 1e-5
    m.exit_tweak(ob)
    assert abs(_eval_x(ob) - 3.0) < 1e-5


def test_mute_and_influence():
    ob = _two_layers(1.0, 3.0)
    layer = ob.animation_data.nla_tracks[1]
    m.exit_tweak(ob)
    layer.mute = True
    assert abs(_eval_x(ob) - 1.0) < 1e-5
    layer.mute = False
    layer.strips[0].influence = 0.5
    assert abs(_eval_x(ob) - 2.0) < 1e-5
    layer.strips[0].keyframe_insert("influence", frame=10)  # keyable
    assert abs(_eval_x(ob) - 2.0) < 1e-5


def test_reorder():
    ob = _two_layers()
    bpy.ops.anim_layers.add()
    ad = ob.animation_data
    assert [t.name for t in ad.nla_tracks] == ["Base", "Layer 1", "Layer 2"]
    bpy.ops.anim_layers.move(direction='DOWN')
    assert [t.name for t in ad.nla_tracks] == ["Base", "Layer 2", "Layer 1"]
    assert ad.nla_tracks.active.name == "Layer 2" and ad.use_tweak_mode
    bpy.ops.anim_layers.move(direction='UP')
    assert [t.name for t in ad.nla_tracks] == ["Base", "Layer 1", "Layer 2"]


def test_merge_down():
    ob = _two_layers(1.0, 3.0)
    ad = ob.animation_data
    bpy.ops.anim_layers.add()  # empty top layer must survive
    ob.anim_layers_index = 1
    bpy.ops.anim_layers.merge_down()
    assert [t.name for t in ad.nla_tracks] == ["Base", "Layer 2"]
    assert ad.nla_tracks[0].strips[0].blend_type == 'REPLACE'
    m.exit_tweak(ob)
    assert abs(_eval_x(ob) - 3.0) < 1e-5
    assert abs(_eval_x(ob, 30) - 3.0) < 1e-5


def test_bake_all_keeps_only_keyed_channels():
    ob = _two_layers(1.0, 3.0)
    ob.animation_data.nla_tracks[1].strips[0].blend_type = 'ADD'
    bpy.ops.anim_layers.bake()
    ad = ob.animation_data
    assert len(ad.nla_tracks) == 1
    s = ad.nla_tracks[0].strips[0]
    cb = m.anim_utils.action_get_channelbag_for_slot(s.action, s.action_slot)
    assert {fc.data_path for fc in cb.fcurves} == {"location"}
    assert abs(cb.fcurves.find("location", index=0).evaluate(10) - 3.0) < 1e-5
    m.exit_tweak(ob)
    assert abs(_eval_x(ob) - 3.0) < 1e-5


def test_remove():
    ob = _two_layers(1.0, 3.0)
    bpy.ops.anim_layers.remove()
    ad = ob.animation_data
    assert [t.name for t in ad.nla_tracks] == ["Base"] and ob.anim_layers_index == 0
    assert abs(_eval_x(ob) - 1.0) < 1e-5


def test_extend_strips_covers_keys():
    ob = _two_layers()
    _key_x(ob, 5.0, frame=80)  # outside scene range 1..50
    ob.anim_layers_index = 0  # switching layers refits
    s = ob.animation_data.nla_tracks[1].strips[0]
    assert (s.frame_start, s.frame_end) == (1, 80), (s.frame_start, s.frame_end)
    SCENE.frame_end = 120
    bpy.ops.anim_layers.extend()
    assert s.frame_end == 120


def test_armature_pose_layers():
    arm = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    SCENE.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    b = arm.data.edit_bones.new("Bone")
    b.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='POSE')
    pb = arm.pose.bones["Bone"]
    bpy.ops.anim_layers.add()
    SCENE.frame_set(10)
    pb.location.y = 1
    pb.keyframe_insert("location", frame=10)
    bpy.ops.anim_layers.add()
    pb.location.y = 4
    pb.keyframe_insert("location", frame=10)
    bpy.ops.anim_layers.bake()
    m.exit_tweak(arm)
    SCENE.frame_set(9)
    SCENE.frame_set(10)
    assert abs(pb.location.y - 4) < 1e-5
    bpy.ops.object.mode_set(mode='OBJECT')
