import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.rig_doctor")
C = bpy.context


def _empty(name, x=0.0):
    ob = bpy.data.objects.new(name, None)
    ob.location.x = x
    C.scene.collection.objects.link(ob)
    return ob


def _rig(name, bones):
    ob = bpy.data.objects.new(name, bpy.data.armatures.new(name))
    C.scene.collection.objects.link(ob)
    C.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    for b in bones:
        ob.data.edit_bones.new(b).tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return ob


def _driver(owner, path, index, expr, target, data_path="location[0]"):
    d = owner.driver_add(path, index).driver
    d.expression = expr
    v = d.variables.new()
    v.name = "var"
    v.targets[0].id = target
    v.targets[0].data_path = data_path
    return d


def _build():
    t = _empty("RD_T", 2.0)
    gone = _empty("RD_Gone")
    ob = _empty("RD_Driven")
    old = _rig("RD_RigOld", ["Root"])
    new = _rig("RD_RigNew", ["Root", "Hand"])
    _driver(ob, "location", 0, "var * 2", t)                     # valid
    _driver(ob, "location", 1, "var", gone)                      # target deleted below
    _driver(ob, "location", 2, "var", t, "nope")                 # bad data path
    _driver(ob, "rotation_euler", 0, "var +", t)                 # syntax error
    _driver(ob, "rotation_euler", 1, "var / 0", t)               # division by zero
    _driver(ob, "rotation_euler", 2, "max([var])", t)            # not simple: needs Auto Run
    _driver(ob, "scale", 0, "var", old, 'pose.bones["Hand"].location[0]')  # bone only on RigNew
    c = ob.constraints.new('COPY_LOCATION')
    c.target, c.subtarget = old, "Hand"
    ob.constraints.new('COPY_ROTATION').target = t            # valid
    bpy.data.objects.remove(gone)
    return dict(t=t, ob=ob, old=old, new=new)


def _mine(found):
    return {(p.kind, p.item): p.text for p in found if p.owner.name.startswith("RD_")}


def test_scan():
    _build()
    assert m.autorun_blocked()  # factory settings: Auto Run off
    found = _mine(m.scan())
    assert set(found) == {
        ("DRIVER", "location[1]"), ("DRIVER", "location[2]"), ("DRIVER", "rotation_euler[0]"),
        ("DRIVER", "rotation_euler[1]"), ("DRIVER", "rotation_euler[2]"), ("DRIVER", "scale[0]"),
        ("CONSTRAINT", "Copy Location")}, found
    assert found[("DRIVER", "location[1]")] == "var: target missing"
    assert "path 'nope' not found" in found[("DRIVER", "location[2]")]
    assert found[("DRIVER", "rotation_euler[0]")].startswith("syntax error")
    assert "ZeroDivisionError" in found[("DRIVER", "rotation_euler[1]")]
    assert found[("DRIVER", "rotation_euler[2]")] == "needs Auto Run Python Scripts"
    assert found[("CONSTRAINT", "Copy Location")] == "target bone 'Hand' not in RD_RigOld"

    s = C.window_manager.rig_doctor
    assert bpy.ops.rig_doctor.scan() == {'FINISHED'}
    assert len(s.results) >= 7 and s.index == -1
    i = next(i for i, r in enumerate(s.results) if r.name == "RD_Driven > Copy Location")
    s.index = i  # clicking a row selects the owner
    assert C.view_layer.objects.active.name == "RD_Driven"


def test_bone_and_vgroup_subtargets():
    d = _build()
    me = bpy.data.objects.new("RD_Mesh", bpy.data.meshes.new("RD_Mesh"))
    C.scene.collection.objects.link(me)
    me.vertex_groups.new(name="Grp")
    pb = d["new"].pose.bones["Hand"]
    c = pb.constraints.new('DAMPED_TRACK')
    c.target, c.subtarget = me, "Missing"
    ik = pb.constraints.new('IK')
    ik.target, ik.subtarget = d["new"], "Root"
    ik.influence = 0
    found = {k: v for k, v in _mine(m.scan()).items() if k[0] == "CONSTRAINT"}
    assert found[("CONSTRAINT", "Damped Track")] == "target vertex group 'Missing' not on RD_Mesh"
    assert found[("CONSTRAINT", "IK")] == "zero influence"
    c.subtarget = "Grp"
    ik.mute = True
    found = {k: v for k, v in _mine(m.scan(include_muted=True)).items() if k[0] == "CONSTRAINT"}
    assert ("CONSTRAINT", "Damped Track") not in found and "muted" in found[("CONSTRAINT", "IK")]
    (p,) = [p for p in m.scan() if p.item == "IK"]
    assert p.bone == "Hand" and m.label(p) == "RD_RigNew > Hand > IK"
    # transform-channel variable with a bone that is not in the armature
    v = d["t"].driver_add("delta_location", 0).driver.variables.new()
    v.type = 'TRANSFORMS'
    v.targets[0].id, v.targets[0].bone_target = d["new"], "Nope"
    assert _mine(m.scan())[("DRIVER", "delta_location[0]")] == "var: bone 'Nope' not in RD_RigNew"


def test_replace_target():
    d = _build()
    try:
        m.replace_target(d["old"], d["old"])
        raise AssertionError("must refuse same ID")
    except ValueError:
        pass
    assert m.replace_target(d["old"], d["new"], objects=[d["t"]]) == 0, "scope excludes RD_Driven"
    assert m.replace_target(d["old"], d["new"]) == 2
    found = _mine(m.scan())
    assert ("DRIVER", "scale[0]") not in found and ("CONSTRAINT", "Copy Location") not in found
    assert d["ob"].constraints["Copy Location"].target == d["new"]
    # operator, only selected
    s = C.window_manager.rig_doctor
    s.id_type, s.id_a, s.id_b, s.only_selected = "objects", "RD_RigNew", "RD_RigOld", True
    for o in C.scene.objects:
        o.select_set(o == d["ob"])
    assert bpy.ops.rig_doctor.replace_target() == {'FINISHED'}
    assert d["ob"].constraints["Copy Location"].target == d["old"]


def test_swap_prefix():
    d = _build()
    fc = d["ob"].animation_data.drivers.find("scale", index=0)
    assert m.swap_prefix('pose.bones["', 'pose.bones["X_') == 1
    assert fc.driver.variables[0].targets[0].data_path == 'pose.bones["X_Hand"].location[0]'
    s = C.window_manager.rig_doctor
    s.only_selected = False
    s.old_prefix, s.new_prefix = 'pose.bones["X_', 'pose.bones["'
    assert bpy.ops.rig_doctor.swap_prefix() == {'FINISHED'}
    assert fc.driver.variables[0].targets[0].data_path == 'pose.bones["Hand"].location[0]'


def test_mute_and_remove_broken():
    d = _build()
    ob = d["ob"]
    for o in C.scene.objects:
        o.select_set(o == ob)
    s = C.window_manager.rig_doctor
    s.con_type, s.only_selected = 'COPY_ROTATION', False
    assert bpy.ops.rig_doctor.mute(mute=True) == {'FINISHED'}
    assert ob.constraints["Copy Rotation"].mute and not ob.constraints["Copy Location"].mute
    assert m.set_mute([ob], 'ALL', False) == 1
    assert m.remove_broken() >= 1
    assert [c.name for c in ob.constraints] == ["Copy Rotation"]
    assert bpy.ops.rig_doctor.remove_broken() == {'FINISHED'}
    assert not [r for r in s.results if r.name.startswith("RD_") and "CONSTRAINT" == r.kind]


def test_shape_key_and_material_drivers():
    d = _build()
    me = bpy.data.objects.new("RD_SK", bpy.data.meshes.new("RD_SK"))
    C.scene.collection.objects.link(me)
    me.shape_key_add(name="Basis")
    me.shape_key_add(name="Smile")
    _driver(me.data.shape_keys, 'key_blocks["Smile"].value', -1, "var", d["t"], "nope")
    mat = bpy.data.materials.new("RD_Mat")
    me.data.materials.append(mat)
    _driver(mat.node_tree, 'nodes["Principled BSDF"].inputs[2].default_value', -1, "var", d["t"], "bad")
    found = [p for p in m.scan() if p.owner in (me.data.shape_keys, mat)]
    assert len(found) == 2, found
    s = C.window_manager.rig_doctor
    m.store(s, found)
    assert {r.name for r in s.results} >= {"RD_Mat > node_tree.nodes[\"Principled BSDF\"].inputs[2].default_value[0]"}
    s.index = 0
    assert C.view_layer.objects.active == me
    bpy.data.materials.remove(mat)
