import importlib
import math

import bmesh
import bpy
from mathutils import Vector

bt = importlib.import_module("bl_ext.clanker.bone_tools")
C = bpy.context


def _rig(bones, mesh_groups=None):
    """Armature with bones {name: (head, tail)} and an optional skinned mesh with these vertex groups."""
    ob = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    for name, (h, t) in bones.items():
        eb = ob.data.edit_bones.new(name)
        eb.head, eb.tail = h, t
    bpy.ops.object.mode_set(mode='OBJECT')
    me = None
    if mesh_groups is not None:
        me = bpy.data.objects.new("Body", bpy.data.meshes.new("Body"))
        C.scene.collection.objects.link(me)
        me.modifiers.new("Armature", 'ARMATURE').object = ob
        for g in mesh_groups:
            me.vertex_groups.new(name=g)
    return ob, me


def test_convert_name_styles():
    cv = bt.convert_name
    assert cv("Arm.L", 'UNDER') == "Arm_L"
    assert cv("Arm_r", 'DOT') == "Arm.R"
    assert cv("L_Hand", 'UNDER_LOWER') == "Hand_l"
    assert cv("mixamorig:LeftArm", 'DOT') == "mixamorig:Arm.L"
    assert cv("mixamorig:RightUpLeg", 'DOT', strip_namespace=True) == "UpLeg.R"
    assert cv("Arm.L", 'WORD_PREFIX') == "LeftArm"
    assert cv("Leg_R", 'WORD_SUFFIX') == "LegRight"
    assert cv("ArmLeft", 'PREFIX') == "L_Arm"
    assert cv("Finger.L.001", 'UNDER') == "Finger_L.001"
    assert cv("Spine", 'DOT') == "Spine"
    assert cv("Upright", 'DOT') == "Upright"


def test_convert_renames_vertex_groups():
    ob, me = _rig({"mixamorig:LeftArm": ((0, 0, 0), (0, 0, 1)), "mixamorig:Spine": ((0, 0, 1), (0, 0, 2))},
                  ["mixamorig:LeftArm", "mixamorig:Spine"])
    bpy.ops.bone_tools.convert_sides(style='DOT', strip_namespace=True)
    assert set(ob.data.bones.keys()) == {"Arm.L", "Spine"}
    assert set(me.vertex_groups.keys()) == {"Arm.L", "Spine"}


def test_strip_prefix():
    ob, me = _rig({"DEF-arm": ((0, 0, 0), (0, 0, 1)), "ORG-arm": ((0, 0, 0), (0, 1, 0))}, ["DEF-arm"])
    bpy.ops.bone_tools.strip_prefix(prefix="DEF-")
    assert set(ob.data.bones.keys()) == {"arm", "ORG-arm"}
    assert me.vertex_groups[0].name == "arm"


def test_chain_from_vertex_path():
    me = bpy.data.meshes.new("Path")
    bm = bmesh.new()
    vs = [bm.verts.new((i, 0, i * 0.5)) for i in range(5)]
    for a, b in zip(vs, vs[1:]):
        bm.edges.new((a, b))
    bm.to_mesh(me)
    ob = bpy.data.objects.new("Path", me)
    ob.location = (0, 2, 0)
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.bone_tools.chain_from_mesh(name="Tail")
    arm = C.object
    assert arm.type == 'ARMATURE' and arm.mode == 'OBJECT'
    bones = arm.data.bones
    assert len(bones) == 4
    heads = sorted((b.head_local.copy(), b.tail_local.copy()) for b in bones)
    ends = sorted([heads[0][0].x, heads[-1][1].x])
    assert ends == [0.0, 4.0]
    b2 = bones["Tail_02"]
    assert b2.parent == bones["Tail_01"] and b2.use_connect
    assert (b2.head_local - Vector((1, 2, 0.5))).length < 1e-5 or (b2.head_local - Vector((3, 2, 1.5))).length < 1e-5


def test_chain_from_curve():
    cu = bpy.data.curves.new("C", 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('POLY')
    sp.points.add(1)
    sp.points[0].co = (0, 0, 0, 1)
    sp.points[1].co = (0, 0, 6, 1)
    ob = bpy.data.objects.new("C", cu)
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    bpy.ops.bone_tools.chain_from_curve(count=3, name="Rope")
    bones = C.object.data.bones
    assert len(bones) == 3
    assert (bones["Rope_02"].head_local - Vector((0, 0, 2))).length < 1e-5
    assert (bones["Rope_03"].tail_local - Vector((0, 0, 6))).length < 1e-5


def test_deform_toggle_and_select():
    ob, _ = _rig({"a": ((0, 0, 0), (0, 0, 1)), "b": ((0, 0, 0), (0, 1, 0))})
    C.view_layer.update()
    ob.pose.bones["a"].select = True
    bpy.ops.bone_tools.toggle_deform()
    assert not ob.data.bones["a"].use_deform and ob.data.bones["b"].use_deform
    bpy.ops.bone_tools.select_deform(deform=False)
    assert ob.pose.bones["a"].select and not ob.pose.bones["b"].select
    bpy.ops.bone_tools.toggle_deform()
    assert ob.data.bones["a"].use_deform


def test_unbound_bones_and_orphan_groups():
    ob, me = _rig({"hip": ((0, 0, 0), (0, 0, 1)), "knee": ((0, 0, 1), (0, 0, 2)),
                   "ctrl": ((0, 0, 0), (0, 1, 0))}, ["hip", "old_bone"])
    ob.data.bones["ctrl"].use_deform = False
    assert bt.unbound_bones(ob) == ["knee"]
    assert bt.orphan_groups(ob) == [("Body", "old_bone")]
    C.view_layer.update()
    bpy.ops.bone_tools.check_skin()
    assert [pb.name for pb in ob.pose.bones if pb.select] == ["knee"]


def test_subdivide_numbered_and_roll():
    ob, _ = _rig({"Spine.L": ((0, 0, 0), (0, 0, 3))})
    bpy.ops.object.mode_set(mode='EDIT')
    eb = ob.data.edit_bones["Spine.L"]
    eb.select = eb.select_head = eb.select_tail = True
    bpy.ops.bone_tools.subdivide(number_cuts=2)
    ebs = ob.data.edit_bones
    assert sorted(ebs.keys()) == ["Spine_01.L", "Spine_02.L", "Spine_03.L"]
    assert abs(ebs["Spine_02.L"].head.z - 1) < 1e-5
    C.scene.cursor.location = (5, 0, 1.5)
    bpy.ops.bone_tools.roll_x_to_cursor()
    x = ebs["Spine_02.L"].x_axis
    assert x.dot(Vector((1, 0, 0))) > 0.999, x
    bpy.ops.object.mode_set(mode='OBJECT')


def test_copy_transforms_and_bake():
    src, _ = _rig({"arm": ((0, 0, 0), (0, 0, 1))})
    src.name = "Src"
    dst, _ = _rig({"arm": ((0, 0, 0), (0, 0, 1)), "extra": ((0, 0, 0), (0, 1, 0))})
    C.view_layer.update()
    src.pose.bones["arm"].rotation_mode = 'XYZ'
    src.pose.bones["arm"].rotation_euler = (math.radians(90), 0, 0)
    src.select_set(True)
    C.view_layer.objects.active = dst
    bpy.ops.object.mode_set(mode='POSE')
    for pb in dst.pose.bones:
        pb.select = True
    bpy.ops.bone_tools.copy_transforms()
    c = dst.pose.bones["arm"].constraints[0]
    assert c.type == 'COPY_TRANSFORMS' and c.target == src and c.subtarget == "arm"
    assert not dst.pose.bones["extra"].constraints
    C.scene.frame_start, C.scene.frame_end = 1, 2
    bpy.ops.bone_tools.bake_constraints()
    assert not dst.pose.bones["arm"].constraints
    q = dst.pose.bones["arm"].rotation_quaternion
    assert abs(abs(q.w) - math.cos(math.radians(45))) < 1e-4, q
    bpy.ops.object.mode_set(mode='OBJECT')
