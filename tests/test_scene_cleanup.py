import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.scene_cleanup")


def _obj(name, mat=None):
    me = bpy.data.meshes.new(name + "_me")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)], [],
                   [(0, 1, 2, 3), (1, 4, 5, 2)])
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    if mat:
        me.materials.append(mat)
    return o


def _color(mat, rgba):
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = rgba


def test_merge_duplicate_materials():
    base = bpy.data.materials.new("Paint")
    _color(base, (1, 0, 0, 1))
    same = base.copy()
    diff = base.copy()
    _color(diff, (0, 0, 1, 1))
    assert (same.name, diff.name) == ("Paint.001", "Paint.002")
    a, b, c = _obj("A", base), _obj("B", same), _obj("C", diff)

    bpy.ops.scene_cleanup.merge_duplicates(identical=True)
    assert "Paint.001" not in bpy.data.materials
    assert b.material_slots[0].material == base
    assert c.material_slots[0].material.name == "Paint.002", "different material must survive"

    bpy.ops.scene_cleanup.merge_duplicates(identical=False)
    assert "Paint.002" not in bpy.data.materials
    assert c.material_slots[0].material == base


def test_merge_nested_node_groups():
    inner = bpy.data.node_groups.new("Inner", "ShaderNodeTree")
    inner.nodes.new("ShaderNodeMath").inputs[1].default_value = 3.0
    outer = bpy.data.node_groups.new("Outer", "ShaderNodeTree")
    outer.nodes.new("ShaderNodeGroup").node_tree = inner
    inner2, outer2 = inner.copy(), outer.copy()
    outer2.nodes["Group"].node_tree = inner2
    plan = m.find_merges(identical=True, kinds=["node_groups"])
    assert {(d.name, b.name) for _, d, b in plan} == {("Inner.001", "Inner"), ("Outer.001", "Outer")}
    inner2.nodes["Math"].inputs[1].default_value = 4.0
    assert not m.find_merges(identical=True, kinds=["node_groups"])
    for ng in (inner, outer, inner2, outer2):
        bpy.data.node_groups.remove(ng)


def test_image_signature():
    i1 = bpy.data.images.new("Tex", 4, 4)
    i2 = bpy.data.images.new("Tex.001", 4, 4)
    assert not m.find_merges(True, ["images"]), "generated images are never identical"
    for i in (i1, i2):
        i.source, i.filepath = 'FILE', "//tex.png"
    assert len(m.find_merges(True, ["images"])) == 1
    bpy.data.images.remove(i1)
    bpy.data.images.remove(i2)


def test_remove_unused_and_empty_slots():
    o = _obj("Slots")
    mats = [bpy.data.materials.new(n) for n in "abc"]
    for mat in mats:
        o.data.materials.append(mat)
    o.data.polygons[0].material_index = 0
    o.data.polygons[1].material_index = 2
    assert m.remove_unused_slots([o]) == 1
    assert [s.material.name for s in o.material_slots] == ["a", "c"]
    assert o.data.polygons[1].material_index == 1

    o.data.materials.append(None)
    o.data.materials.append(mats[1])
    o.data.polygons[1].material_index = 3
    assert m.remove_empty_slots([o]) == 1
    assert [s.material.name for s in o.material_slots] == ["a", "c", "b"]
    assert o.data.polygons[1].material_index == 2

    # object-linked slot: the material lives on the object, data slot is empty
    o.material_slots[0].link = 'OBJECT'
    o.material_slots[0].material = mats[1]
    o.data.materials[0] = None
    assert m.remove_empty_slots([o]) == 0, "object-linked slot holds a material"


def test_rename_data_skips_shared():
    a = _obj("Rock")
    b = _obj("Tree")
    c = bpy.data.objects.new("Twin", b.data)
    assert m.rename_data([a, b, c]) == 1
    assert a.data.name == "Rock"
    assert b.data.name == "Tree_me", "shared data untouched"


def test_select_negative_scale():
    good, bad, flat = _obj("Good"), _obj("Mirror"), _obj("Flat")
    bad.scale.x = -1
    flat.scale.z = 0
    bpy.ops.scene_cleanup.select(category="BAD_SCALE")
    assert bad.select_get() and flat.select_get() and not good.select_get()
    assert {o.name for o in m.find_objects("UNAPPLIED_SCALE", bpy.context.view_layer)} == {"Mirror", "Flat"}


def test_find_empty_and_outside():
    _obj("Full")
    e = bpy.data.objects.new("Empty", bpy.data.meshes.new("e"))
    bpy.context.scene.collection.objects.link(e)
    bpy.data.objects.new("Loose", bpy.data.meshes.new("l"))
    vl = bpy.context.view_layer
    vl.update()  # sync bases with newly linked objects (the UI does this on redraw)
    assert [o.name for o in m.find_objects("EMPTY_MESH", vl)] == ["Empty"]
    assert [o.name for o in m.find_objects("OUTSIDE_LAYER", vl)] == ["Loose"]
    assert {o.name for o in m.find_objects("NO_MATERIAL", vl)} == {"Full", "Empty"}


def test_purge():
    bpy.data.materials.new("orphan")
    assert bpy.ops.scene_cleanup.purge() == {'FINISHED'}
    assert "orphan" not in bpy.data.materials
