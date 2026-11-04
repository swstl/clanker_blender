import bpy
import importlib

m = importlib.import_module("bl_ext.clanker.weight_tools")


def _mesh(verts):
    me = bpy.data.meshes.new("wt")
    me.from_pydata(verts, [], [])
    ob = bpy.data.objects.new("wt", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    return ob


def _w(ob, name, vi):
    vg = ob.vertex_groups[name]
    return next((g.weight for g in ob.data.vertices[vi].groups if g.group == vg.index), None)


def test_is_left():
    assert m._is_left("arm.L") and m._is_left("Left_arm") and m._is_left("arm_l.001")
    assert not m._is_left("arm.R") and not m._is_left("HandRight")


def test_mirror_l_to_r():
    ob = _mesh([(1, 0, 0), (-1, 0, 0), (2, 0, 0), (-2, 0, 0), (0, 1, 0)])
    ob.vertex_groups.new(name="arm.L").add([0], 0.8, 'REPLACE')
    ob.vertex_groups["arm.L"].add([2], 0.3, 'REPLACE')
    stale = ob.vertex_groups.new(name="arm.R")
    stale.add([4], 1.0, 'REPLACE')  # must be overwritten
    ob.vertex_groups.new(name="spine").add([4], 1.0, 'REPLACE')
    bpy.ops.weight_tools.mirror()
    assert abs(_w(ob, "arm.R", 1) - 0.8) < 1e-6
    assert abs(_w(ob, "arm.R", 3) - 0.3) < 1e-6
    assert _w(ob, "arm.R", 4) is None and _w(ob, "arm.R", 0) is None
    assert "spine.R" not in ob.vertex_groups


def test_mirror_r_to_l_creates_group():
    ob = _mesh([(1, 0, 0), (-1, 0, 0)])
    ob.vertex_groups.new(name="Hand_R").add([1], 0.5, 'REPLACE')
    assert m.mirror_groups(ob, to_right=False) == 1
    assert abs(_w(ob, "Hand_L", 0) - 0.5) < 1e-6


def test_remove_empty():
    ob = _mesh([(0, 0, 0), (1, 0, 0)])
    ob.vertex_groups.new(name="keep").add([0], 0.5, 'REPLACE')
    ob.vertex_groups.new(name="zero").add([1], 0.0, 'REPLACE')
    ob.vertex_groups.new(name="none")
    bpy.ops.weight_tools.remove_empty()
    assert [vg.name for vg in ob.vertex_groups] == ["keep"]


def test_remove_unmatched():
    ob = _mesh([(0, 0, 0)])
    arm = bpy.data.objects.new("rig", bpy.data.armatures.new("rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for name, deform in (("bone", True), ("ctrl", False)):
        b = arm.data.edit_bones.new(name)
        b.tail = (0, 0, 1)
        b.use_deform = deform
    bpy.ops.object.mode_set(mode='OBJECT')
    ob.modifiers.new("Armature", 'ARMATURE').object = arm
    for n in ("bone", "ctrl", "junk"):
        ob.vertex_groups.new(name=n)
    assert m.remove_unmatched_groups(ob, m.armature_of(ob)) == 1
    assert {vg.name for vg in ob.vertex_groups} == {"bone", "ctrl"}
    assert m.remove_unmatched_groups(ob, arm, only_deform=True) == 1
    assert [vg.name for vg in ob.vertex_groups] == ["bone"]


def test_game_ready_limits_and_normalizes():
    ob = _mesh([(0, 0, 0), (1, 0, 0)])
    for i, w in enumerate((0.5, 0.4, 0.3, 0.2, 0.1, 0.005)):
        ob.vertex_groups.new(name=f"g{i}").add([0], w, 'REPLACE')
    ob.vertex_groups["g0"].add([1], 1.0, 'REPLACE')
    assert m.game_ready(ob, threshold=0.01, limit=4) == 1
    ws = sorted(g.weight for g in ob.data.vertices[0].groups)
    assert len(ws) == 4 and abs(sum(ws) - 1) < 1e-4
    assert min(ws) > 0.1  # smallest ones were dropped


def test_select_problems():
    ob = _mesh([(0, 0, 0), (1, 0, 0), (2, 0, 0)])
    for i in range(3):
        ob.vertex_groups.new(name=f"g{i}").add([0, 1], 0.3, 'REPLACE')
    u, o = m.select_problem_verts(ob, max_influences=2)
    assert (u, o) == (1, 2)
    assert [v.select for v in ob.data.vertices] == [True, True, True]
    u, o = m.select_problem_verts(ob, max_influences=3)
    assert (u, o) == (1, 0)
    assert [v.select for v in ob.data.vertices] == [False, False, True]


def test_set_weight():
    bpy.ops.weight_tools.set_weight(value=0.25)
    assert abs(bpy.context.scene.tool_settings.weight_paint.unified_paint_settings.weight - 0.25) < 1e-6


def test_operator_from_edit_mode():
    ob = _mesh([(1, 0, 0), (-1, 0, 0)])
    ob.vertex_groups.new(name="a.L").add([0], 0.7, 'REPLACE')
    bpy.ops.object.mode_set(mode='EDIT')
    assert bpy.ops.weight_tools.mirror() == {'FINISHED'}
    assert ob.mode == 'EDIT'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert abs(_w(ob, "a.R", 1) - 0.7) < 1e-6
