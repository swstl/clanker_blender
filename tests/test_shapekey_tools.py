import importlib
import bmesh
import bpy
import numpy as np

m = importlib.import_module("bl_ext.clanker.shapekey_tools")


def _grid():
    me = bpy.data.meshes.new("G")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=4, y_segments=2, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("G", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.shape_key_add(name="Basis")
    smile = ob.shape_key_add(name="Smile", from_mix=False)
    for d in smile.data:
        d.co.z += 1
    ob.shape_key_add(name="Nothing", from_mix=False)
    return ob, smile


def test_split_sums_to_original():
    ob, smile = _grid()
    l, r = m.split_lr(ob, smile, blend=0.3)
    base = m._co(ob.data.shape_keys.reference_key)
    total = (m._co(l) - base) + (m._co(r) - base)
    assert np.allclose(total, m._co(smile) - base, atol=1e-5)
    xs = base[:, 0]
    dl = m._co(l)[:, 2] - base[:, 2]
    assert np.allclose(dl[xs > 0.31], 1) and np.allclose(dl[xs < -0.31], 0)


def test_operator_and_cleanup():
    ob, smile = _grid()
    ob.active_shape_key_index = list(ob.data.shape_keys.key_blocks).index(smile)
    bpy.ops.shapekey_tools.split_lr(blend=0, remove_original=True)
    names = [k.name for k in ob.data.shape_keys.key_blocks]
    assert "Smile" not in names and "Smile.L" in names and "Smile.R" in names
    ob.select_set(True)
    bpy.ops.shapekey_tools.remove_empty()
    assert "Nothing" not in [k.name for k in ob.data.shape_keys.key_blocks]
