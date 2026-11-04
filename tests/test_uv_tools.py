import importlib
import math

import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.uv_tools")


def _obj(bm, name="M", select=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    ob.select_set(select)
    bpy.context.view_layer.objects.active = ob
    return ob


def _cube(name="C", **kw):
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_cube(bm, size=2, calc_uvs=True)
    return _obj(bm, name, **kw)


def _plane(name="P"):
    """2x2 m plane, 2x2 faces, UVs filling 0..1."""
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1, calc_uvs=True)
    return _obj(bm, name)


def _sel_only(*obs):
    for o in bpy.context.scene.objects:
        o.select_set(o in obs)
    bpy.context.view_layer.objects.active = obs[0]


def test_angle_seams():
    ob = _cube()
    bpy.ops.uv_tools.angle_seams(angle=math.radians(100), use_sharp=False)
    assert sum(e.use_seam for e in ob.data.edges) == 0
    bpy.ops.uv_tools.angle_seams(angle=math.radians(60))
    assert sum(e.use_seam for e in ob.data.edges) == 12
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=2, y_segments=2, size=1)
    next(e for e in bm.edges if len(e.link_faces) == 2).smooth = False  # one inner sharp edge
    p = _obj(bm, "P")
    _sel_only(p)
    bpy.ops.uv_tools.angle_seams(angle=math.radians(30), use_sharp=True)
    seams = [e for e in p.data.edges if e.use_seam]
    assert len(seams) == 1 and seams[0].use_edge_sharp


def test_texel_measure_and_set():
    ob = _plane()
    assert abs(m.measure([(ob, False)], 1024) - 512) < 1e-3
    ob.scale = (2, 2, 2)
    bpy.context.view_layer.update()
    assert abs(m.measure([(ob, False)], 1024) - 256) < 1e-3
    bpy.ops.uv_tools.texel_measure()
    assert abs(bpy.context.scene.uv_tools.density_measured - 256) < 1e-3  # default 1024 px
    bpy.ops.uv_tools.texel_set(target=128, tex_size=1024)
    assert abs(m.measure([(ob, False)], 1024) - 128) < 1e-3
    assert abs(m.measure([(ob, False)], 2048) - 256) < 1e-3
    # copy density from active to selected
    other = _plane("Q")
    _sel_only(ob, other)
    bpy.ops.uv_tools.texel_copy()
    assert abs(m.measure([(other, False)], 1024) - 128) < 1e-3


def test_flipped_faces():
    ob = _plane()
    me = ob.data
    poly = me.polygons[2]
    uv = me.uv_layers.active.data
    idx = list(poly.loop_indices)
    cx = sum(uv[i].uv.x for i in idx) / len(idx)
    for i in idx:  # mirror this face's UVs in place
        uv[i].uv.x = 2 * cx - uv[i].uv.x
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.uv_tools.select_flipped()
    bpy.ops.object.mode_set(mode='OBJECT')
    assert [p.index for p in me.polygons if p.select] == [2], [p.select for p in me.polygons]


def test_uv_maps_batch():
    a, b = _cube("A"), _cube("B")
    b.data.uv_layers[0].name = "Lightmap"
    b.data.uv_layers.new(name="Extra")
    _sel_only(a, b)
    bpy.ops.uv_tools.uv_maps(action='ADD', name="Lightmap")
    assert [l.name for l in a.data.uv_layers] == ["UVMap", "Lightmap"]
    assert [l.name for l in b.data.uv_layers] == ["Lightmap", "Extra"]
    b.data.uv_layers["Lightmap"].uv[0].vector = (0.25, 0.75)
    bpy.ops.uv_tools.uv_maps(action='MATCH', names="UVMap, Lightmap")
    for o in (a, b):
        assert [l.name for l in o.data.uv_layers] == ["UVMap", "Lightmap"], o.name
        assert o.data.uv_layers.active_index == 0 and o.data.uv_layers[0].active_render
    assert tuple(b.data.uv_layers["Lightmap"].uv[0].vector) == (0.25, 0.75)  # kept by name
    bpy.ops.uv_tools.uv_maps(action='RENAME', name="Lightmap", new_name="UV2")
    bpy.ops.uv_tools.uv_maps(action='ACTIVE', name="UV2")
    assert a.data.uv_layers.active.name == "UV2" and a.data.uv_layers["UV2"].active_render
    bpy.ops.uv_tools.uv_maps(action='REMOVE', name="UV2")
    assert [l.name for l in b.data.uv_layers] == ["UVMap"]


def test_checker_restores_materials():
    a, b = _cube("A"), _cube("B")
    ma, mb = bpy.data.materials.new("MA"), bpy.data.materials.new("MB")
    a.data.materials.append(ma)
    a.data.materials.append(None)
    a.data.materials.append(mb)
    a.data.polygons[3].material_index = 2
    _sel_only(a, b)
    bpy.ops.uv_tools.checker()
    assert all(s.material.name == m.CHECKER for o in (a, b) for s in o.material_slots)
    assert len(b.material_slots) == 1
    assert ma.users >= 1  # kept alive by the stored reference
    bpy.ops.uv_tools.checker()
    assert [s.material for s in a.material_slots] == [ma, None, mb]
    assert a.data.polygons[3].material_index == 2
    assert len(b.data.materials) == 0
    assert m.PROP not in a and m.PROP not in b


def test_batch_unwrap_keeps_texel_density():
    for pack in ('SEPARATE', 'TOGETHER'):
        for o in list(bpy.data.objects):
            bpy.data.objects.remove(o)
        a, b = _cube("A"), _cube("B")
        b.scale = (3, 3, 3)
        bpy.context.view_layer.update()
        _sel_only(a, b)
        bpy.ops.uv_tools.batch_unwrap(method='SMART', pack=pack, keep_texel=True)
        da, db = m.measure([(a, False)], 1024), m.measure([(b, False)], 1024)
        assert da > 0 and abs(da - db) / da < 1e-3, (pack, da, db)
        for o in (a, b):
            assert all(-1e-4 <= c <= 1 + 1e-4 for d in o.data.uv_layers.active.data for c in d.uv)
        assert a.mode == 'OBJECT' and a.select_get() and b.select_get()


def test_straighten_to_grid():
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_grid(bm, x_segments=3, y_segments=3, size=1, calc_uvs=True)
    uv = bm.loops.layers.uv.active
    for f in bm.faces:  # skew the UVs
        for l in f.loops:
            l[uv].uv.x += 0.3 * l[uv].uv.y
    ob = _obj(bm)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    assert bpy.ops.uv_tools.straighten() == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    data = ob.data.uv_layers.active.data
    for p in ob.data.polygons:
        pts = [data[i].uv for i in p.loop_indices]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            assert min(abs(a.x - b.x), abs(a.y - b.y)) < 1e-4, "edge not axis aligned"
