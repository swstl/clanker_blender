import importlib

import bpy
import numpy as np

m = importlib.import_module("bl_ext.clanker.sculpt_layers")


def _setup():
    """3x3 grid (9 verts) with two layers: d1 lifts z by 1, d2 moves x by 0.5."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=2, y_subdivisions=2)
    ob = bpy.context.object
    base = np.array([v.co[:] for v in ob.data.vertices], np.float32)
    bpy.ops.sculpt_layers.add()
    l1 = ob.active_shape_key
    m.set_co(l1, base + [0, 0, 1])
    bpy.ops.sculpt_layers.add()
    l2 = ob.active_shape_key
    m.set_co(l2, base + [0.5, 0, 0])
    l1.value, l2.value = 0.5, 1.0
    return ob, base, l1.name, l2.name


def _ev(ob):
    bpy.context.view_layer.update()
    e = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return np.array([v.co[:] for v in e.data.vertices], np.float32)


def test_add_layers_and_mix():
    ob, base, n1, n2 = _setup()
    keys = ob.data.shape_keys.key_blocks
    assert [k.name for k in keys] == ["Basis", n1, n2] and n1.startswith("SL: ")
    assert ob.active_shape_key.name == n2, "new layer is the sculpt target"
    assert keys[n1].slider_min == -1 and keys[n1].slider_max == 2
    assert keys[n1].relative_key == keys["Basis"]
    assert np.allclose(_ev(ob), base + [0.5, 0, 0.5], atol=1e-5)


def test_vertex_group_mask_halves():
    ob, base, n1, n2 = _setup()
    vg = ob.vertex_groups.new(name="half")
    vg.add(list(range(len(base))), 0.5, 'REPLACE')
    ob.data.shape_keys.key_blocks[n2].vertex_group = "half"
    assert np.allclose(_ev(ob), base + [0.25, 0, 0.5], atol=1e-5)


def test_solo_restores_mutes():
    ob, base, n1, n2 = _setup()
    keys = ob.data.shape_keys.key_blocks
    keys[n2].mute = True
    bpy.ops.sculpt_layers.add()
    n3 = ob.active_shape_key.name
    bpy.ops.sculpt_layers.solo(layer=n2)
    assert not keys[n2].mute and keys[n1].mute and keys[n3].mute
    assert np.allclose(_ev(ob), base + [0.5, 0, 0], atol=1e-5)
    bpy.ops.sculpt_layers.solo(layer=n2)  # toggle off
    assert [keys[n].mute for n in (n1, n2, n3)] == [False, True, False]
    assert "sl_solo" not in ob.data.shape_keys


def test_merge_down():
    ob, base, n1, n2 = _setup()
    keys = ob.data.shape_keys.key_blocks
    keys[n1].value = 1.0
    keys[n2].value = 0.5
    before = _ev(ob)
    ob.active_shape_key_index = keys.find(n2)
    bpy.ops.sculpt_layers.merge_down()
    assert n2 not in keys and ob.active_shape_key.name == n1
    assert np.allclose(m.co(keys[n1]), base + [0.25, 0, 1], atol=1e-5)
    assert np.allclose(_ev(ob), before, atol=1e-5)
    assert bpy.ops.sculpt_layers.merge_down.poll() and \
        bpy.ops.sculpt_layers.merge_down() == {'CANCELLED'}, "nothing below the bottom layer"


def test_flatten_into_basis():
    ob, base, n1, n2 = _setup()
    other = ob.shape_key_add(name="Smile", from_mix=False)  # non-layer key must follow
    m.set_co(other, base + [0, 0.3, 0])
    other.value = 1.0
    before = _ev(ob)
    bpy.ops.sculpt_layers.flatten()
    keys = ob.data.shape_keys.key_blocks
    assert [k.name for k in keys] == ["Basis", "Smile"]
    assert np.allclose(m.co(keys["Basis"]), base + [0.5, 0, 0.5], atol=1e-5)
    assert np.allclose(_ev(ob), before, atol=1e-5)
    other.value = 0.0
    assert np.allclose(_ev(ob), base + [0.5, 0, 0.5], atol=1e-5)


def test_bake_and_mask_from_sculpt():
    ob, base, n1, n2 = _setup()
    before = _ev(ob)
    bpy.ops.sculpt_layers.bake()
    keys = ob.data.shape_keys.key_blocks
    assert keys[n1].mute and keys[n2].mute and m.is_layer(ob.active_shape_key)
    assert np.allclose(_ev(ob), before, atol=1e-5)
    mask = np.zeros(len(base), np.float32)
    mask[0] = 1.0
    ob.data.attributes.new(m.MASK, 'FLOAT', 'POINT').data.foreach_set("value", mask)
    bpy.ops.sculpt_layers.mask_from_sculpt()
    kb = ob.active_shape_key
    assert kb.vertex_group in ob.vertex_groups
    ev = _ev(ob)
    assert np.allclose(ev[0], before[0], atol=1e-5) and np.allclose(ev[1:], base[1:], atol=1e-5)


def test_move_remove_and_sculpt_mode():
    ob, base, n1, n2 = _setup()
    keys = ob.data.shape_keys.key_blocks
    bpy.ops.sculpt_layers.move(direction='DOWN')
    assert [k.name for k in keys] == ["Basis", n2, n1]
    bpy.ops.object.mode_set(mode='SCULPT')
    bpy.ops.sculpt_layers.add()
    assert ob.mode == 'SCULPT' and len(m.layers(ob)) == 3
    bpy.ops.sculpt_layers.remove()
    assert len(m.layers(ob)) == 2 and ob.active_shape_key.name == n1
    bpy.ops.object.mode_set(mode='OBJECT')


def test_warnings():
    bpy.ops.mesh.primitive_plane_add()
    ob = bpy.context.object
    assert m.warnings(ob) == []
    ob.modifiers.new("M", 'MULTIRES')
    assert any("Multires" in w for w in m.warnings(ob))
