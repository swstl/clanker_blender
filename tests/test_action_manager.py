import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.action_manager")


def _ob(name="Rig"):
    ob = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = ob
    return ob


def _three(ob):
    """Idle 1-20, Run 5-15, Walk 10-40, all keyed on ob, none assigned."""
    for name, s, e in (("Idle", 1, 20), ("Run", 5, 15), ("Walk", 10, 40)):
        ob.animation_data_create().action = bpy.data.actions.new(name)
        for f in (s, e):
            ob.location.x = f
            ob.keyframe_insert("location", index=0, frame=f)
    ob.animation_data.action = None
    return [bpy.data.actions[n] for n in ("Idle", "Run", "Walk")]


def test_assign_sets_slot_and_range():
    ob = _ob()
    _three(ob)
    bpy.context.scene.action_manager.set_range = True
    bpy.ops.action_manager.assign(action="Walk")
    ad = ob.animation_data
    assert ad.action.name == "Walk" and ad.action_slot is not None
    assert (bpy.context.scene.frame_start, bpy.context.scene.frame_end) == (10, 40)
    # Manual range wins.
    run = bpy.data.actions["Run"]
    run.use_frame_range, run.frame_start, run.frame_end = True, 0, 30
    bpy.ops.action_manager.assign(action="Run")
    assert (bpy.context.scene.frame_start, bpy.context.scene.frame_end) == (0, 30)


def test_assign_picks_slot_from_other_object():
    other = _ob("Other")
    other.location.x = 1
    other.keyframe_insert("location", frame=1)
    act = other.animation_data.action
    ob = _ob()
    m.assign_action(bpy.context, ob, act)
    assert ob.animation_data.action_slot == act.slots[0]


def test_reset_pose():
    arm = bpy.data.armatures.new("A")
    ob = bpy.data.objects.new("Arm", arm)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    b = arm.edit_bones.new("B")
    b.tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    ob.pose.bones["B"].location = (1, 2, 3)
    m.assign_action(bpy.context, ob, bpy.data.actions.new("Empty"), reset=True)
    assert tuple(ob.pose.bones["B"].location) == (0, 0, 0)


def test_cycle():
    ob = _ob()
    idle, run, walk = _three(ob)
    bpy.ops.action_manager.assign(action="Idle")
    bpy.ops.action_manager.cycle(step=1)
    assert ob.animation_data.action == run
    bpy.ops.action_manager.cycle(step=-1)
    bpy.ops.action_manager.cycle(step=-1)
    assert ob.animation_data.action == walk  # wraps


def test_compatible_filter():
    ob = _ob()
    _three(ob)
    mat = bpy.data.materials.new("M")
    mat.diffuse_color[0] = 0.5
    mat.keyframe_insert("diffuse_color", index=0, frame=1)
    names = {a.name for a in m.compatible_actions(ob)}
    assert names == {"Idle", "Run", "Walk"}


def test_protect_all_and_handler():
    ob = _ob()
    acts = _three(ob)
    bpy.ops.action_manager.protect_all()
    assert all(a.use_fake_user for a in acts)
    for a in acts:
        a.use_fake_user = False
    prefs = bpy.context.preferences.addons["bl_ext.clanker.action_manager"].preferences
    prefs.auto_protect = False
    m._on_save()
    assert not any(a.use_fake_user for a in acts)
    prefs.auto_protect = True
    assert m._on_save in bpy.app.handlers.save_pre
    m._on_save()
    assert all(a.use_fake_user for a in acts)


def test_batch_rename():
    ob = _ob()
    _three(ob)
    bpy.ops.action_manager.batch_rename(prefix="Hero_", suffix="_v1", find="Run", replace="Sprint", scope='ALL')
    assert {a.name for a in bpy.data.actions} == {"Hero_Idle_v1", "Hero_Sprint_v1", "Hero_Walk_v1"}


def test_remove_unused():
    ob = _ob()
    idle, run, walk = _three(ob)
    run.use_fake_user = True
    bpy.ops.action_manager.assign(action="Walk")
    bpy.ops.action_manager.remove_unused()
    assert {a.name for a in bpy.data.actions} == {"Run", "Walk"}


def test_new_delete_push_range():
    ob = _ob()
    _three(ob)
    bpy.ops.action_manager.assign(action="Walk")
    bpy.ops.action_manager.new(duplicate=True)
    dup = ob.animation_data.action
    assert dup.name.startswith("Walk.") and dup.use_fake_user
    dup.use_frame_range = False
    bpy.ops.action_manager.range_from_keys()
    assert dup.use_frame_range and (dup.frame_start, dup.frame_end) == (10, 40)
    bpy.ops.action_manager.push_nla(stash=True)
    assert ob.animation_data.action == dup and ob.animation_data.nla_tracks[0].mute
    bpy.ops.action_manager.push_nla()
    assert ob.animation_data.action is None
    assert ob.animation_data.nla_tracks[-1].strips[0].action == dup
    bpy.context.scene.action_manager.index = list(bpy.data.actions).index(bpy.data.actions["Idle"])
    assert ob.animation_data.action.name == "Idle"  # clicking a row assigns
    bpy.ops.action_manager.delete()
    assert "Idle" not in bpy.data.actions
