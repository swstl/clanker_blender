import importlib
import time

import bpy
import numpy as np

m = importlib.import_module("bl_ext.clanker.weight_layers")


def _setup():
    """Plane with 4 verts; target group G = 0.5 on verts 0..2, vert 3 unassigned."""
    bpy.ops.mesh.primitive_plane_add()
    ob = bpy.context.object
    g = ob.vertex_groups.new(name="G")
    g.add([0, 1, 2], 0.5, 'REPLACE')
    bpy.ops.weight_layers.create()
    return ob, ob.weight_layers["G"]


def _w(ob, name, i):
    try:
        return ob.vertex_groups[name].weight(i)
    except RuntimeError:
        return None


def _layer(ob, st, blend, opacity, weights):
    bpy.ops.weight_layers.add()
    lay = st.layers[st.active_index]
    lay.blend, lay.opacity = blend, opacity
    for i, w in weights.items():
        ob.vertex_groups[lay.group].add([i], w, 'REPLACE')
    return lay


def test_math():
    W = np.array([[0.5, 0.5, 0.5], [1.0, 0.4, 0.0]], np.float32)
    M = np.array([[1, 1, 1], [1, 1, 0]], bool)
    cases = {
        ('MIX', 0.5): [0.75, 0.45, 0.5],      # vert 2 not in layer -> untouched
        ('ADD', 1.0): [1.0, 0.9, 0.5],        # clamped
        ('SUBTRACT', 1.0): [0.0, 0.1, 0.5],
        ('MULTIPLY', 1.0): [0.5, 0.2, 0.5],
        ('MULTIPLY', 0.5): [0.5, 0.35, 0.5],
        ('MAX', 1.0): [1.0, 0.5, 0.5],
        ('MIN', 1.0): [0.5, 0.4, 0.5],
    }
    for (mode, op), want in cases.items():
        r, used = m.blend_stack(W, M, [('MIX', 1.0, True), (mode, op, True)])
        assert np.allclose(r, want), (mode, op, r)
        assert used.tolist() == [True, True, True]
    r, _ = m.blend_stack(W, M, [('MIX', 1.0, True), ('ADD', 1.0, False)])
    assert np.allclose(r, [0.5, 0.5, 0.5]), "disabled layer must be ignored"


def test_create_copies_base():
    ob, st = _setup()
    assert [l.name for l in st.layers] == ["Base"]
    assert st.layers[0].group == "G~Base"
    assert ob.vertex_groups.active.name == "G~Base", "painting goes to the layer"
    assert _w(ob, "G~Base", 1) == 0.5 and _w(ob, "G~Base", 3) is None


def test_composite_updates_target():
    ob, st = _setup()
    _layer(ob, st, 'ADD', 1.0, {0: 0.25, 3: 0.3})
    m.composite(ob, st)
    assert abs(_w(ob, "G", 0) - 0.75) < 1e-5
    assert abs(_w(ob, "G", 1) - 0.5) < 1e-5
    assert abs(_w(ob, "G", 3) - 0.3) < 1e-5, "vertex only in a layer joins G"
    lay = _layer(ob, st, 'SUBTRACT', 0.5, {0: 1.0})
    m.composite(ob, st)
    assert abs(_w(ob, "G", 0) - 0.25) < 1e-5
    lay.enabled = False
    assert m.composite(ob, st) == 1
    assert abs(_w(ob, "G", 0) - 0.75) < 1e-5
    assert m.composite(ob, st) == 0, "no change -> no write"
    # base layer still intact (non-destructive)
    assert abs(_w(ob, "G~Base", 0) - 0.5) < 1e-5


def test_mix_opacity_and_multiply_via_object():
    ob, st = _setup()
    _layer(ob, st, 'MIX', 0.5, {0: 1.0, 1: 0.0})
    m.composite(ob, st)
    assert abs(_w(ob, "G", 0) - 0.75) < 1e-5 and abs(_w(ob, "G", 1) - 0.25) < 1e-5
    assert abs(_w(ob, "G", 2) - 0.5) < 1e-5
    _layer(ob, st, 'MULTIPLY', 1.0, {0: 0.5})
    m.composite(ob, st)
    assert abs(_w(ob, "G", 0) - 0.375) < 1e-5


def test_remove_base_drops_vertices():
    ob, st = _setup()
    st.active_index = 0
    bpy.ops.weight_layers.remove()
    assert "G~Base" not in ob.vertex_groups and len(st.layers) == 0
    assert _w(ob, "G", 0) is None, "empty stack -> empty result"


def test_duplicate_move_rename():
    ob, st = _setup()
    st.layers[0].opacity = 0.3
    bpy.ops.weight_layers.duplicate()
    assert len(st.layers) == 2 and st.active_index == 1
    dup = st.layers[1]
    assert abs(dup.opacity - 0.3) < 1e-6 and _w(ob, dup.group, 0) == 0.5
    dup_group = dup.group  # collection items are re-pointed by move(); keep the name
    bpy.ops.weight_layers.move(direction='DOWN')
    assert st.active_index == 0 and st.layers[0].group == dup_group
    dup = st.layers[0]
    dup.name = "Fix"
    assert dup.group == "G~Fix" and "G~Fix" in ob.vertex_groups


def test_flatten():
    ob, st = _setup()
    _layer(ob, st, 'ADD', 1.0, {1: 0.2})
    bpy.ops.weight_layers.flatten()
    assert "G" not in ob.weight_layers
    assert [g.name for g in ob.vertex_groups] == ["G"]
    assert abs(_w(ob, "G", 1) - 0.7) < 1e-5 and abs(_w(ob, "G", 0) - 0.5) < 1e-5


def test_mirror_layer():
    ob, st = _setup()  # plane verts: 0(-1,-1) 1(1,-1) 2(-1,1) 3(1,1)
    lay = _layer(ob, st, 'ADD', 1.0, {0: 0.4})
    bpy.ops.weight_layers.mirror()
    assert lay.group in ob.vertex_groups, "group name not flipped"
    assert _w(ob, lay.group, 1) is not None and abs(_w(ob, lay.group, 1) - 0.4) < 1e-5


def test_normalize():
    ob, st = _setup()
    other = ob.vertex_groups.new(name="Other")
    other.add([0], 1.0, 'REPLACE')
    ob.vertex_groups.active_index = ob.vertex_groups["G~Base"].index
    st.normalize = True
    _layer(ob, st, 'ADD', 1.0, {0: 0.3})
    m.composite(ob, st)
    assert abs(_w(ob, "G", 0) - 0.8) < 1e-5
    assert abs(_w(ob, "Other", 0) - 0.2) < 1e-5
    assert abs(_w(ob, "G~Base", 0) - 0.5) < 1e-5, "layers are not touched by normalize"


def test_auto_update_tick():
    ob, st = _setup()
    _layer(ob, st, 'ADD', 1.0, {2: 0.5})
    m.schedule(ob)
    m._last[0] = time.monotonic() - 1  # pretend user stopped painting
    m._tick()
    assert abs(_w(ob, "G", 2) - 1.0) < 1e-5


def test_depsgraph_marks_dirty():
    ob, st = _setup()
    m._dirty.clear()
    ob.vertex_groups["G~Base"].add([3], 0.1, 'REPLACE')
    bpy.context.view_layer.update()
    assert ob.name in m._dirty
