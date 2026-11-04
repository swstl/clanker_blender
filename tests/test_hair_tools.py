import importlib

import bpy
import numpy as np

ht = importlib.import_module("bl_ext.clanker.hair_tools")


def _clean():
    for c in list(bpy.data.hair_curves):
        bpy.data.hair_curves.remove(c)


def _hair(roots, n_pts=3, height=0.6, location=(0, 0, 0)):
    """Grid surface + a hair object with straight curves standing on `roots` (z=0), both at `location`
    (explicit: primitive ops otherwise follow the 3D cursor that earlier tests move)."""
    _clean()
    bpy.ops.mesh.primitive_grid_add(size=4, x_subdivisions=8, y_subdivisions=8, location=location,
                                    rotation=(0, 0, 0))
    surf = bpy.context.active_object
    c = bpy.data.hair_curves.new("Hair")
    ob = bpy.data.objects.new("Hair", c)
    ob.location = location
    bpy.context.scene.collection.objects.link(ob)
    c.surface = surf
    c.surface_uv_map = surf.data.uv_layers[0].name
    c.add_curves([n_pts] * len(roots))
    pos = [(x, y, height * i / (n_pts - 1)) for x, y in roots for i in range(n_pts)]
    c.attributes["position"].data.foreach_set("vector", np.array(pos, dtype=np.float32).ravel())
    rad = c.attributes.new("radius", 'FLOAT', 'POINT')
    rad.data.foreach_set("value", np.arange(len(pos), dtype=np.float32))
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    return ob


def _roots(c):
    return ht.positions(c)[ht.offsets(c)[:-1]]


def test_mirror_x():
    roots = [(1.0, 0.25, 0), (1.5, -0.5, 0), (0.5, 1.0, 0)]
    ob = _hair([r[:2] for r in roots])
    c = ob.data
    assert bpy.ops.hair_tools.mirror(axis='X') == {'FINISHED'}
    assert len(c.curves) == 6 and len(c.points) == 18
    new = _roots(c)[3:]
    for (x, y, z), p in zip(roots, new):
        assert np.allclose(p, (-x, y, z), atol=1e-3), p
    # radius copied per point, surface UVs recomputed by the native snap
    rad = ht.read_attr(c.attributes["radius"])[:, 0]
    assert np.allclose(rad[9:], rad[:9])
    uv = ht.read_attr(c.attributes["surface_uv_coordinate"])
    assert np.allclose(uv[3:, 0], 1.0 - uv[:3, 0], atol=1e-3)
    # mirroring the now symmetric set again adds nothing with dedupe, doubles without
    bpy.ops.hair_tools.mirror(axis='X')
    assert len(c.curves) == 6
    bpy.ops.hair_tools.mirror(axis='X', dedupe=False)
    assert len(c.curves) == 12


def test_mirror_moved_surface():
    # mirror plane is the surface's local X, not the world origin
    ob = _hair([(1.0, 0.25)], location=(3, 0, 1))
    bpy.ops.hair_tools.mirror(axis='X')
    assert np.allclose(_roots(ob.data)[1], (-1, 0.25, 0), atol=1e-3)


def test_mirror_replace_side():
    ob = _hair([(1.0, 0.0), (-1.0, 0.7), (-0.5, -0.5)])
    bpy.ops.hair_tools.mirror(axis='X', side='POSITIVE', replace=True)
    r = _roots(ob.data)
    assert len(r) == 2
    assert sorted(np.round(r[:, 0], 3)) == [-1.0, 1.0] and np.allclose(r[:, 1], 0, atol=1e-3)


def test_mirror_selection_and_snap():
    ob = _hair([(1.0, 0.0), (1.0, 1.0)])
    c = ob.data
    sel = c.attributes.new(".selection", 'BOOLEAN', 'CURVE')
    sel.data.foreach_set("value", [True, False])
    assert list(ht.selected_curves(c)) == [0]
    # lift the curve: the mirrored copy is snapped back onto the surface (root z=0)
    pos = ht.positions(c)
    pos[:3, 2] += 0.5
    ht.write_attr(c.attributes["position"], pos.astype(np.float32))
    bpy.ops.hair_tools.mirror(axis='X')
    assert len(c.curves) == 3
    assert np.allclose(_roots(c)[2], (-1, 0, 0), atol=1e-3)
    # BVH fallback (no UVs) also moves roots onto the surface
    pos = ht.positions(c)
    pos[6:9, 2] += 0.5
    ht.write_attr(c.attributes["position"], pos.astype(np.float32))
    ht.snap_bvh(bpy.context, ob, [2])
    assert np.allclose(_roots(c)[2], (-1, 0, 0), atol=1e-3)
    assert np.allclose(ht.positions(c)[8], (-1, 0, 0.6), atol=1e-3)


def test_cards():
    ob = _hair([(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)], n_pts=5)
    assert bpy.ops.hair_tools.cards(resample=6, columns=4, random_columns=False) == {'FINISHED'}
    cards = bpy.data.objects["Hair_cards"]
    me = cards.data
    assert len(me.vertices) == 4 * 6 * 2 and len(me.polygons) == 4 * 5
    assert len(me.materials) == 1
    uv = np.empty(len(me.loops) * 2, dtype=np.float32)
    me.uv_layers[0].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    for f in me.polygons:  # card k uses atlas column k (cycling)
        k = f.index // 5
        us = uv[f.loop_start:f.loop_start + 4, 0]
        assert np.allclose(sorted(set(np.round(us, 4))), [k / 4, (k + 1) / 4]), us
    # root width 0.02, card faces the surface normal (+Z): ribbon spans the horizontal plane
    v0, v1 = me.vertices[0].co, me.vertices[1].co
    assert abs((v0 - v1).length - 0.02) < 1e-4 and abs(v0.z - v1.z) < 1e-5
    # every 2nd curve, rerun replaces the mesh in the same object
    bpy.ops.hair_tools.cards(resample=0, step=2)
    assert bpy.data.objects["Hair_cards"] == cards and len(cards.data.vertices) == 2 * 5 * 2
    assert "Hair_cards.001" not in bpy.data.objects


def test_radius_and_delete_short():
    ob = _hair([(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)], n_pts=4)
    c = ob.data
    bpy.ops.hair_tools.set_radius(root=0.03, tip=0.0)
    rad = ht.read_attr(c.attributes["radius"])[:, 0]
    assert np.allclose(rad[:4], [0.03, 0.02, 0.01, 0.0])
    pos = ht.positions(c)
    pos[4:8, 2] *= 0.01  # second curve 6mm long
    ht.write_attr(c.attributes["position"], pos.astype(np.float32))
    assert np.allclose(ht.curve_lengths(c), [0.6, 0.006, 0.6], atol=1e-5)
    bpy.ops.hair_tools.delete_short(length=0.01)
    assert len(c.curves) == 2
    assert np.allclose(_roots(c)[:, 0], [1.0, -1.0])
