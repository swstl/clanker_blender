import importlib
import math

import bpy
from mathutils import Euler, Matrix, Vector

ik = importlib.import_module("bl_ext.clanker.ikfk_snap")
C = bpy.context
TOL = 1e-3


def _rig(fk_names=("upperarm_fk", "forearm_fk", "hand_fk")):
    """Left + right arm: FK chain, IK chain (IK on forearm_ik -> hand_ik, pole elbow_pole), deform chain."""
    ob = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    ebs = ob.data.edit_bones
    for side, sx in (("L", 1), ("R", -1)):
        pts = [Vector((0, 0, 0)), Vector((1, 0.15, 0)), Vector((2, 0, 0)), Vector((2.4, 0, 0))]
        pts = [Vector((p.x * sx, p.y, p.z)) for p in pts]
        for names in (fk_names, ("upperarm_ik", "forearm_ik", "hand_ik"), ("upperarm", "forearm", "hand")):
            prev = None
            for i, n in enumerate(names):
                eb = ebs.new(f"{n}.{side}")
                eb.head, eb.tail = pts[i], pts[i + 1]
                if prev and n != "hand_ik":
                    eb.parent, eb.use_connect = prev, True
                prev = eb
        pole = ebs.new(f"elbow_pole.{side}")
        pole.head, pole.tail = Vector((sx, 2, 0)), Vector((sx, 2, 0.3))
    bpy.ops.object.mode_set(mode='POSE')
    pbs = ob.pose.bones
    for side in "LR":
        c = pbs[f"forearm_ik.{side}"].constraints.new('IK')
        c.target, c.subtarget, c.chain_count = ob, f"hand_ik.{side}", 2
        c.pole_target, c.pole_subtarget, c.pole_angle = ob, f"elbow_pole.{side}", math.pi
        for n in ("upperarm", "forearm", "hand"):
            ct = pbs[f"{n}.{side}"].constraints.new('COPY_TRANSFORMS')
            ct.target, ct.subtarget = ob, f"{n}_ik.{side}"
        pbs[f"hand_ik.{side}"]["ik_fk_switch"] = 1.0
    C.view_layer.update()
    return ob


def _world(ob, name):
    pb = ob.pose.bones[name]
    return ob.matrix_world @ pb.head, ob.matrix_world @ pb.tail


def _close(a, b):
    return (a - b).length < TOL


def _pole_angle_ok(ob):
    """The test rig's rest pose must survive the IK solve (pole angle right)."""
    for n in ("upperarm", "forearm"):
        h, t = _world(ob, f"{n}_ik.L")
        b = ob.data.bones[f"{n}_ik.L"]
        assert _close(h, b.head_local) and _close(t, b.tail_local), (n, h, t)


def test_detect_and_mirror():
    ob = _rig()
    _pole_angle_ok(ob)
    found = {d["name"]: d for d in ik.detect_chains(ob)}
    d = found["upperarm.L"]
    assert d["ik"] == "upperarm_ik.L, forearm_ik.L, hand_ik.L", d
    assert d["fk"] == "upperarm_fk.L, forearm_fk.L, hand_fk.L", d
    assert d["control"] == "hand_ik.L" and d["pole"] == "elbow_pole.L"
    assert (d["switch_bone"], d["switch_prop"]) == ("hand_ik.L", "ik_fk_switch")
    bpy.ops.ikfk_snap.detect()
    assert len(ob.data.ikfk_chains) == 2
    bpy.ops.ikfk_snap.detect()
    assert len(ob.data.ikfk_chains) == 2  # no duplicates
    ob.data.ikfk_chains.clear()
    ik.add_chain(ob, **found["upperarm.L"])
    ik.active_chain(ob).pole_distance = 1.5
    bpy.ops.ikfk_snap.mirror()
    r = ob.data.ikfk_chains[1]
    assert r.name == "upperarm.R" and r.fk == "upperarm_fk.R, forearm_fk.R, hand_fk.R"
    assert r.control == "hand_ik.R" and r.pole == "elbow_pole.R" and r.switch_bone == "hand_ik.R"
    assert r.pole_distance == 1.5


def test_detect_by_position():
    ob = _rig(fk_names=("ctl_upper", "ctl_fore", "ctl_hand"))
    d = {d["name"]: d for d in ik.detect_chains(ob)}["upperarm.L"]
    assert d["fk"] == "ctl_upper.L, ctl_fore.L, ctl_hand.L", d  # deform bones (constrained) not picked


def test_snap_fk_to_ik_then_ik_to_fk():
    ob = _rig()
    bpy.ops.ikfk_snap.detect()
    ob.data.ikfk_index = next(i for i, c in enumerate(ob.data.ikfk_chains) if c.name == "upperarm.L")
    pbs = ob.pose.bones
    # Pull the IK control in and up: the elbow bends toward the pole.
    pbs["hand_ik.L"].matrix = Matrix.Translation((1.2, 0.5, 0.8)) @ Euler((0.3, 0.2, 0.5)).to_matrix().to_4x4()
    C.view_layer.update()
    bpy.ops.ikfk_snap.snap(direction='FK_TO_IK', insert_keys=True)
    for n in ("upperarm", "forearm", "hand"):
        fh, ft = _world(ob, f"{n}_fk.L")
        ih, it = _world(ob, f"{n}_ik.L")
        assert _close(fh, ih) and _close(ft, it), (n, fh, ih, ft, it)
    assert pbs["hand_ik.L"]["ik_fk_switch"] == 0.0
    assert ob.animation_data and ob.animation_data.action

    # Now pose FK somewhere else and bring IK over.
    for n, rot in (("upperarm", (0.4, -0.3, 0.6)), ("forearm", (0, 0, 0.9)), ("hand", (0.5, 0, 0))):
        pbs[f"{n}_fk.L"].rotation_quaternion = Euler(rot).to_quaternion()
    C.view_layer.update()
    bpy.ops.ikfk_snap.snap(direction='IK_TO_FK')
    eh, et = _world(ob, "hand_fk.L")
    ih, it = _world(ob, "hand_ik.L")
    assert _close(eh, ih) and _close(et, it)
    assert _close(_world(ob, "forearm_ik.L")[1], _world(ob, "forearm_fk.L")[1])
    # Pole correctness: the elbow lands where the FK elbow is.
    assert _close(_world(ob, "forearm_ik.L")[0], _world(ob, "forearm_fk.L")[0]), \
        (_world(ob, "forearm_ik.L")[0], _world(ob, "forearm_fk.L")[0])
    assert pbs["hand_ik.L"]["ik_fk_switch"] == 1.0


def test_snap_range():
    ob = _rig()
    bpy.ops.ikfk_snap.detect()
    ob.data.ikfk_index = next(i for i, c in enumerate(ob.data.ikfk_chains) if c.name == "upperarm.L")
    ctrl = ob.pose.bones["hand_ik.L"]
    for f, z in ((1, 0.0), (5, 0.8)):
        ctrl.matrix = Matrix.Translation((1.5, 0.3, z))
        C.view_layer.update()
        ctrl.keyframe_insert("location", frame=f)
    bpy.ops.ikfk_snap.snap_range(direction='FK_TO_IK', frame_start=1, frame_end=5, only_keyed=True)
    path = 'pose.bones["upperarm_fk.L"].rotation_quaternion'
    fcs = ik.anim_utils.action_get_channelbag_for_slot(ob.animation_data.action, ob.animation_data.action_slot).fcurves
    fc = fcs.find(path, index=0)
    assert [k.co.x for k in fc.keyframe_points] == [1.0, 5.0]
    bpy.ops.ikfk_snap.snap_range(direction='FK_TO_IK', frame_start=1, frame_end=5)
    assert len(fc.keyframe_points) == 5
    C.scene.frame_set(3)
    assert _close(_world(ob, "hand_fk.L")[0], _world(ob, "hand_ik.L")[0])
    sw = fcs.find('pose.bones["hand_ik.L"]["ik_fk_switch"]')
    assert sw and sw.evaluate(3) == 0.0
    bpy.ops.object.mode_set(mode='OBJECT')
