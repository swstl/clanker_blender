import importlib
from math import radians

import bpy
from mathutils import Vector

M = importlib.import_module("bl_ext.clanker.motion_trail")
TOL = 1e-4


def _scene():
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 20
    sc.frame_set(1)
    return sc


def _empty(name):
    ob = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _parented_child():
    """Child under an animated, rotated, non-uniformly scaled parent with a non-identity parent inverse."""
    sc = _scene()
    parent = _empty("Parent")
    parent.location = (1, 2, 3)
    parent.rotation_euler = (radians(30), radians(10), radians(45))
    parent.scale = (2, 1, 0.5)
    bpy.context.view_layer.update()
    child = _empty("Child")
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()
    parent.location = (-1, 0, 2)
    parent.rotation_euler = (radians(-20), radians(40), radians(70))
    for f, z in ((1, 0.0), (20, radians(90))):
        parent.rotation_euler.z = z
        parent.keyframe_insert("rotation_euler", index=2, frame=f)
    child.delta_location = (0.3, -0.2, 0.1)
    for f, x in ((1, 0.0), (10, 4.0), (20, 1.0)):
        child.location = (x, 1, 0)
        child.keyframe_insert("location", frame=f)
    sc.frame_set(3)
    return sc, parent, child


def _rig():
    sc = _scene()
    arm = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    sc.collection.objects.link(arm)
    arm.location = (2, -1, 0.5)
    arm.rotation_euler = (radians(15), 0, radians(60))
    arm.scale = (1.5, 1.5, 1.5)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    root = eb.new("root")
    root.head, root.tail, root.roll = (0, 0, 0), (0, 1, 0.3), 0.4
    free = eb.new("free")
    free.head, free.tail, free.parent = (1, 1, 0), (1, 2, 1), root
    conn = eb.new("conn")
    conn.head, conn.tail, conn.parent, conn.use_connect = (1, 2, 1), (1, 3, 1), free, True
    bpy.ops.object.mode_set(mode='POSE')
    pr, pf = arm.pose.bones["root"], arm.pose.bones["free"]
    pr.rotation_mode = pf.rotation_mode = 'XYZ'
    pr.scale = (1.2, 1, 0.8)
    for f, r in ((1, 0.0), (20, radians(80))):
        pr.rotation_euler = (radians(20), 0, r)
        pr.keyframe_insert("rotation_euler", frame=f)
    pf.rotation_euler = (radians(25), radians(-30), 0)
    for f, y in ((1, 0.0), (10, 2.0)):
        pf.location = (0, y, 0)
        pf.keyframe_insert("location", frame=f)
    sc.frame_set(2)
    return sc, arm


def test_object_with_parent_hits_target():
    sc, parent, child = _parented_child()
    for frame, target in ((5, Vector((3, -2, 4))), (10, Vector((-1, 0.5, 2)))):  # new key, existing key
        M.set_key_from_world(child, None, frame, target)
        assert sc.frame_current == 3
        sc.frame_set(frame)
        assert (child.matrix_world.translation - target).length < TOL
        sc.frame_set(3)
    assert M.key_frames(child) == {1, 5, 10, 20}


def test_world_to_object_location_is_inverse():
    _, _, child = _parented_child()
    loc = M.world_to_object_location(child, child.matrix_world.translation)
    assert (loc - child.location).length < TOL


def test_pose_bone_hits_target():
    sc, arm = _rig()
    target = Vector((0.5, 3, -1))
    for frame in (7, 10):
        M.set_key_from_world(arm, "free", frame, target)
        assert sc.frame_current == 2
        sc.frame_set(frame)
        assert (arm.matrix_world @ arm.pose.bones["free"].head - target).length < TOL
        sc.frame_set(2)
    assert M.key_frames(arm, "free") == {1, 7, 10}
    assert M.key_frames(arm, "root") == set()  # only location keys count


def test_connected_bone_refused():
    _, arm = _rig()
    try:
        M.set_key_from_world(arm, "conn", 5, (0, 0, 0))
    except ValueError as e:
        assert "IK" in str(e)
    else:
        raise AssertionError("connected bone accepted")
    assert M.key_frames(arm, "conn") == set()


def test_sample_path_matches_evaluation():
    sc, parent, child = _parented_child()
    frames = [1, 4, 10, 17]
    pts = M.sample_path(child, None, frames)
    assert sc.frame_current == 3
    for f, p in zip(frames, pts):
        sc.frame_set(f)
        assert (child.matrix_world.translation - p).length < TOL
    sc.frame_set(3)
    _, arm = _rig()
    pts = M.sample_path(arm, "free", frames)
    assert sc.frame_current == 2
    for f, p in zip(frames, pts):
        sc.frame_set(f)
        assert (arm.matrix_world @ arm.pose.bones["free"].head - p).length < TOL


def test_delete_key():
    _, _, child = _parented_child()
    assert M.delete_key(child, None, 10)
    assert M.key_frames(child) == {1, 20}


def test_cache_follows_frame_and_selection():
    sc, parent, child = _parented_child()
    bpy.context.view_layer.objects.active = child
    s = sc.motion_trail
    s.range_mode, s.before, s.after = 'AROUND', 2, 3
    s.show = True
    assert M._state["targets"] == [("Child", None)]
    assert sorted(M._cache[("Child", None)]) == [1, 2, 3, 4, 5, 6]
    assert M._state["keys"][("Child", None)] == {1, 10, 20}
    p4 = M._cache[("Child", None)][4]
    sc.frame_set(5)
    M._tick()  # what the throttled timer runs
    assert sorted(M._cache[("Child", None)]) == [3, 4, 5, 6, 7, 8]
    assert M._cache[("Child", None)][4] is p4  # reused, not resampled
    assert sc.frame_current == 5
    s.range_mode = 'SCENE'
    assert sorted(M._cache[("Child", None)]) == list(range(1, 21))
    _, arm = _rig()  # active = armature in pose mode
    for pb in arm.pose.bones:
        pb.select = pb.name == "free"
    M._tick()
    assert M._state["targets"] == [("Rig", "free")] and list(M._cache) == [("Rig", "free")]
    s.show = False
    assert not M._cache and not M._state["targets"]


def test_draw_smoke():
    import gpu
    sc, _, child = _parented_child()
    bpy.context.view_layer.objects.active = child
    sc.motion_trail.show = True
    M._state["hover"] = (("Child", None), 10, True)
    gpu.init()
    with gpu.types.GPUOffScreen(64, 64).bind():
        M.draw()
    M._state["hover"] = None
    sc.motion_trail.show = False


def test_modal_passes_through_when_idle():
    """Idle mouse moves / LMB releases must reach the UI (hover, buttons fire on release)."""
    import types
    region = types.SimpleNamespace(x=0, y=0, width=100, height=100)
    op = types.SimpleNamespace(region=region, rv3d=None, drag=None)
    op._hit = lambda mouse: None
    ev = lambda t, v='NOTHING', **kw: types.SimpleNamespace(type=t, value=v, mouse_x=500, mouse_y=500,
                                                              ctrl=False, **kw)
    modal = M.MT_OT_edit.modal
    assert modal(op, bpy.context, ev('MOUSEMOVE')) == {'PASS_THROUGH'}
    assert modal(op, bpy.context, ev('LEFTMOUSE', 'RELEASE')) == {'PASS_THROUGH'}
    assert modal(op, bpy.context, ev('LEFTMOUSE', 'PRESS')) == {'PASS_THROUGH'}  # outside the view
    assert modal(op, bpy.context, ev('WHEELUPMOUSE', 'PRESS')) == {'PASS_THROUGH'}
    op.drag = (("X", None), 1, None, None)
    assert modal(op, bpy.context, ev('LEFTMOUSE', 'RELEASE')) == {'RUNNING_MODAL'}
    assert op.drag is None


def test_unkeyed_tweak_of_keyed_target_not_resampled():
    """Moving a target that has location keys must not mark the cache dirty (resample would revert it)."""
    import types
    sc, parent, child = _parented_child()
    bpy.context.view_layer.objects.active = child
    sc.motion_trail.show = True
    plain = _empty("Plain")
    M._state["targets"] = [("Child", None), ("Plain", None)]
    upd = lambda ob: types.SimpleNamespace(updates=[types.SimpleNamespace(id=ob, is_updated_transform=True)])
    M._state["dirty"] = False
    M.on_depsgraph(sc, upd(child))
    assert not M._state["dirty"]
    M.on_depsgraph(sc, upd(plain))
    assert M._state["dirty"]
    sc.motion_trail.show = False
