import importlib
import bmesh
import bpy
import numpy as np

m = importlib.import_module("bl_ext.clanker.symmetry_check")


def _grid():
    bm = bmesh.new()
    bm.loops.layers.uv.new()
    bmesh.ops.create_grid(bm, x_segments=10, y_segments=6, size=1, calc_uvs=True)
    return bm


def _obj(bm):
    me = bpy.data.meshes.new("S")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("S", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.context.scene.symmetry_check.axis = 'X'
    return ob


def _co(ob):
    return m.read_mesh(ob)[0]


def _res(ob):
    return m.analyze(_co(ob), 0, 1e-4, 10)


def test_symmetric_grid():
    ob = _obj(_grid())
    r = m.full_report(ob, 0, 1e-4, 10)
    assert r["unmatched"].sum() == 0 and r["near"].sum() == 0 and r["topo"].sum() == 0
    assert r["max_dev"] < 1e-6 and r["centre"].sum() > 0
    assert r["uv"][0] == 0, r["uv"]
    bpy.ops.symmetry_check.analyze()
    assert bpy.context.window_manager.symmetry_check_results.unmatched == 0


def test_near_miss_average():
    ob = _obj(_grid())
    co = _co(ob)
    idx = np.flatnonzero(co[:, 0] > 0.3)[:3]
    for k in idx:
        ob.data.vertices[k].co.y += 0.0005
    r = _res(ob)
    assert r["near"].sum() == 6 and r["unmatched"].sum() == 0, (r["near"].sum(), r["unmatched"].sum())
    assert r["max_dev"] > 4e-4
    bpy.ops.symmetry_check.fix(mode='AVERAGE')
    r = _res(ob)
    assert r["near"].sum() == 0 and r["max_dev"] < 1e-6, r["max_dev"]


def test_only_selected():
    ob = _obj(_grid())
    co = _co(ob)
    a, b = np.flatnonzero(co[:, 0] > 0.3)[:2]
    for v in ob.data.vertices:
        v.select = bool(v.index == a)
    ob.data.vertices[a].co.y += 0.0005
    ob.data.vertices[b].co.y += 0.0005
    bpy.ops.symmetry_check.fix(mode='AVERAGE', only_selected=True)
    assert _res(ob)["near"].sum() == 2


def test_unmatched_select():
    bm = _grid()
    bm.verts.ensure_lookup_table()
    gone = next(v for v in bm.verts if v.co.x > 0.3)
    target = (-gone.co.x, gone.co.y, gone.co.z)
    bmesh.ops.delete(bm, geom=[gone], context='VERTS')
    ob = _obj(bm)
    r = _res(ob)
    assert r["unmatched"].sum() == 1
    bpy.ops.symmetry_check.analyze()
    assert bpy.context.window_manager.symmetry_check_results.unmatched == 1
    bpy.ops.symmetry_check.select(kind='unmatched')
    assert ob.mode == 'EDIT'
    bpy.ops.object.mode_set(mode='OBJECT')
    sel = [v for v in ob.data.vertices if v.select]
    assert len(sel) == 1 and (sel[0].co - __import__("mathutils").Vector(target)).length < 1e-6


def test_centre_snap():
    ob = _obj(_grid())
    k = int(np.flatnonzero(np.abs(_co(ob)[:, 0]) < 1e-9)[0])
    ob.data.vertices[k].co.x = 5e-5
    r = _res(ob)
    assert r["centre_off"].sum() == 1
    bpy.ops.symmetry_check.fix(mode='CENTRE')
    assert ob.data.vertices[k].co.x == 0.0
    assert _res(ob)["centre_off"].sum() == 0


def test_copy_pos_to_neg():
    ob = _obj(_grid())
    co = _co(ob)
    neg = int(np.flatnonzero(co[:, 0] < -0.3)[0])
    r = _res(ob)
    pos = int(r["mirror"][neg])
    pos_before = ob.data.vertices[pos].co.copy()
    ob.data.vertices[neg].co.y += 0.0005
    ob.data.vertices[neg].co.z += 0.0003
    bpy.ops.symmetry_check.fix(mode='POS_TO_NEG')
    p, n = ob.data.vertices[pos].co, ob.data.vertices[neg].co
    assert (p - pos_before).length == 0
    assert abs(n.x + p.x) < 1e-7 and abs(n.y - p.y) < 1e-7 and abs(n.z - p.z) < 1e-7
    # and the other direction moves the positive side instead
    ob.data.vertices[neg].co.y += 0.0005
    bpy.ops.symmetry_check.fix(mode='NEG_TO_POS')
    assert abs(ob.data.vertices[pos].co.y - ob.data.vertices[neg].co.y) < 1e-7


def test_topology():
    bm = _grid()
    bm.edges.ensure_lookup_table()
    e = next(e for e in bm.edges if all(v.co.x > 0.3 for v in e.verts) and len(e.link_faces) == 2)
    bmesh.ops.dissolve_edges(bm, edges=[e])
    ob = _obj(bm)
    r = m.full_report(ob, 0, 1e-4, 10)
    assert r["unmatched"].sum() == 0
    assert r["topo"].sum() >= 2, r["topo"].sum()
    assert m.topology_bad(np.array([[0, 1], [1, 2]]), {"mirror": np.array([2, 1, 0]),
                                                        "pair": np.ones(3, bool)}).sum() == 0


def test_shape_keys():
    ob = _obj(_grid())
    ob.shape_key_add(name="Basis")
    sym = ob.shape_key_add(name="Sym")
    one = ob.shape_key_add(name="OneSide")
    for i, v in enumerate(ob.data.vertices):
        sym.data[i].co.z += 0.1 * abs(v.co.x)
        if v.co.x > 0.3:
            one.data[i].co.z += 0.1
    r = dict(m.full_report(ob, 0, 1e-4, 10)["keys"])
    assert r["Sym"] < 1e-6 and abs(r["OneSide"] - 0.1) < 1e-6, r
