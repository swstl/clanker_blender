import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.space_switch")
TOL = 1e-4


def _close(a, b):
    return all(abs(x - y) < TOL for r1, r2 in zip(a, b) for x, y in zip(r1, r2))


def _scene():
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 30
    A = bpy.data.objects.new("A", None)
    sc.collection.objects.link(A)
    for f, x, rz in ((1, 0, 0), (30, 6, 1.0)):
        A.location = (x, 1, 0)
        A.rotation_euler = (0, 0, rz)
        A.keyframe_insert("location", frame=f)
        A.keyframe_insert("rotation_euler", frame=f)
    sc.frame_set(1)
    return sc, A


def _world(o):
    return o.id_data.matrix_world @ o.matrix if isinstance(o, bpy.types.PoseBone) else o.matrix_world.copy()


def _at(sc, o, f):
    sc.frame_set(f)
    return _world(o)


def _check_switching(sc, A, B):
    """B gets parent A, switches to it at 10 and back to world at 20."""
    vl = bpy.context.view_layer
    c = m.add_parent(B, A)
    assert c.name == "SS:A" and c.influence == 0
    before = {f: _at(sc, B, f) for f in (5, 9, 10)}
    sc.frame_set(10)
    m.switch(sc, vl, B, "SS:A")
    assert m.active_parent(B) == "SS:A"
    for f in (5, 9, 10):
        assert _close(_at(sc, B, f), before[f]), f
    # B follows A after the switch: B relative to A stays constant
    rel = A.matrix_world.inverted() @ _at(sc, B, 10)
    w15 = _at(sc, B, 15)
    assert _close(A.matrix_world.inverted() @ w15, rel)
    assert not _close(w15, before[10])
    w20 = _at(sc, B, 20)
    m.switch(sc, vl, B, m.WORLD, 20)
    assert m.active_parent(B) == m.WORLD
    assert _close(_at(sc, B, 20), w20) and _close(_at(sc, B, 15), w15)
    assert _close(_at(sc, B, 25), w20)  # world-space hold after switching back
    assert m.switches(B) == [(10, "SS:A"), (20, m.WORLD)]


def test_object_switch():
    sc, A = _scene()
    B = bpy.data.objects.new("B", None)
    sc.collection.objects.link(B)
    B.location, B.rotation_euler = (3, 0, 1), (0.2, 0, 0)
    _check_switching(sc, A, B)


def _rig(sc):
    arm = bpy.data.armatures.new("Rig")
    ob = bpy.data.objects.new("Rig", arm)
    sc.collection.objects.link(ob)
    ob.location = (0, -2, 0)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    for n, x in (("root", 0), ("hand", 1)):
        b = arm.edit_bones.new(n)
        b.head, b.tail = (x, 0, 0), (x, 0, 1)
    # hand stays unparented: Child Of stacks on top of any (animated) bone parent
    bpy.ops.object.mode_set(mode='POSE')
    root = ob.pose.bones["root"]
    for f, z in ((1, 0), (30, 2)):
        root.location = (0, z, 0)
        root.keyframe_insert("location", frame=f)
    return ob


def test_bone_switch():
    sc, A = _scene()
    ob = _rig(sc)
    _check_switching(sc, A, ob.pose.bones["hand"])
    bpy.ops.object.mode_set(mode='OBJECT')


def test_bone_to_bone_add_parent_op():
    sc, A = _scene()
    ob = _rig(sc)
    root, hand = ob.pose.bones["root"], ob.pose.bones["hand"]
    root.select = hand.select = True
    ob.data.bones.active = hand.bone
    bpy.ops.space_switch.add_parent()
    assert [c.name for c in m.ss_constraints(hand)] == ["SS:Rig/root"]
    bpy.ops.space_switch.switch(parent="SS:Rig/root")  # switches every selected bone that has it
    assert m.active_parent(hand) == "SS:Rig/root" and m.active_parent(root) == m.WORLD
    bpy.ops.object.mode_set(mode='OBJECT')


def test_bake_to_world():
    sc, A = _scene()
    B = bpy.data.objects.new("B", None)
    sc.collection.objects.link(B)
    vl = bpy.context.view_layer
    m.add_parent(B, A)
    m.switch(sc, vl, B, "SS:A", 10)
    m.switch(sc, vl, B, m.WORLD, 20)
    ref = {f: _at(sc, B, f) for f in range(1, 31, 3)}
    assert m.bake_to_world(sc, vl, [B], 1, 30) == 1
    assert not B.constraints and not m.switches(B)
    for f, w in ref.items():
        assert _close(_at(sc, B, f), w), f
    # disable instead of remove
    m.add_parent(B, A)
    m.switch(sc, vl, B, "SS:A", 5)
    ref = {f: _at(sc, B, f) for f in (5, 12, 30)}
    m.bake_to_world(sc, vl, [B], 1, 30, remove=False)
    assert B.constraints["SS:A"].influence == 0
    for f, w in ref.items():
        assert _close(_at(sc, B, f), w), f
