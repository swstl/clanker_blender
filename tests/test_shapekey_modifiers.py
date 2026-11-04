import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.shapekey_modifiers")


def _cube(name="SKCube"):
    me = bpy.data.meshes.new(name)
    v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me.from_pydata(v, [], f)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.shape_key_add(name="Basis")
    up = ob.shape_key_add(name="Up", from_mix=False)
    for d in up.data:
        if d.co.z > 0:
            d.co.z += 1
    wide = ob.shape_key_add(name="Wide", from_mix=False)
    for d in wide.data:
        d.co.y *= 2
    return ob


def _coords(kb):
    return [tuple(d.co) for d in kb.data]


def test_apply_subsurf_mirror_keeps_shapes():
    ob = _cube()
    key = ob.data.shape_keys
    ob.vertex_groups.new(name="grp")
    up, wide = key.key_blocks["Up"], key.key_blocks["Wide"]
    wide.relative_key = up
    wide.vertex_group = "grp"
    wide.slider_min, wide.slider_max, wide.value = -1, 2, 1.5
    up.mute = True
    up.interpolation = 'KEY_CARDINAL'
    fc = key.driver_add('key_blocks["Up"].value')
    var = fc.driver.variables.new()
    var.targets[0].id_type = 'KEY'
    var.targets[0].id = key
    var.targets[0].data_path = 'key_blocks["Wide"].value'
    fc.driver.expression = var.name
    wide.keyframe_insert("value", frame=1)
    act = key.animation_data.action

    ob.modifiers.new("Armature", 'ARMATURE')
    ob.modifiers.new("Subdivision", 'SUBSURF').levels = 1
    bev = ob.modifiers.new("Bevel", 'BEVEL')
    bev.show_viewport = False
    ob.modifiers.new("Mirror", 'MIRROR')
    n0 = len(ob.data.vertices)

    with bpy.context.temp_override(active_object=ob, object=ob):
        assert bpy.ops.shapekey_modifiers.apply() == {'FINISHED'}

    assert [md.name for md in ob.modifiers] == ["Armature", "Bevel"], "unapplied mods kept in order"
    key = ob.data.shape_keys
    assert [kb.name for kb in key.key_blocks] == ["Basis", "Up", "Wide"]
    n = len(ob.data.vertices)
    assert n > n0
    basis, up, wide = key.key_blocks
    assert len(up.data) == n
    assert max(c[2] for c in _coords(up)) > max(c[2] for c in _coords(basis)) + 0.5
    assert max(c[1] for c in _coords(wide)) > max(c[1] for c in _coords(basis)) + 0.5
    assert abs(max(c[2] for c in _coords(wide)) - max(c[2] for c in _coords(basis))) < 1e-4, (max(c[2] for c in _coords(wide)), max(c[2] for c in _coords(basis)))
    assert _coords(basis) == [tuple(v.co) for v in ob.data.vertices]
    assert wide.relative_key == up and wide.vertex_group == "grp"
    assert (wide.slider_min, wide.slider_max, wide.value) == (-1, 2, 1.5)
    assert up.mute and up.interpolation == 'KEY_CARDINAL'
    drv = key.animation_data.drivers.find('key_blocks["Up"].value')
    assert drv and drv.driver.variables[0].targets[0].id == key, "driver retargeted to new Key"
    assert key.animation_data.action == act
    assert not [me for me in bpy.data.meshes if me.name.endswith("_backup")]


def test_solidify_and_bevel_hidden_but_chosen():
    ob = _cube()
    ob.modifiers.new("Solidify", 'SOLIDIFY')
    b = ob.modifiers.new("Bevel", 'BEVEL')
    b.show_viewport = False
    assert m.apply_keep_shapes(bpy.context, ob, ["Solidify", "Bevel"]) is None
    assert not ob.modifiers
    assert len(ob.data.shape_keys.key_blocks["Up"].data) == len(ob.data.vertices) > 16


def test_decimate_aborts_unchanged():
    ob = _cube()
    for d in ob.data.shape_keys.key_blocks["Up"].data[:1]:
        d.co = (5, 7, -3)
    sub = ob.modifiers.new("Subdivision", 'SUBSURF')
    ob.modifiers.new("Decimate", 'DECIMATE').ratio = 0.3
    before = {kb.name: _coords(kb) for kb in ob.data.shape_keys.key_blocks}
    err = m.apply_keep_shapes(bpy.context, ob, ["Subdivision", "Decimate"])
    assert err and "topology" in err and "'" in err, err
    assert [md.name for md in ob.modifiers] == ["Subdivision", "Decimate"]
    assert sub.show_viewport and len(ob.data.vertices) == 8
    assert {kb.name: _coords(kb) for kb in ob.data.shape_keys.key_blocks} == before
