import importlib
import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.mesh_check")


def _obj(bm):
    me = bpy.data.meshes.new("M")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("M", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    return ob


def test_counts_and_fixes():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.verts.new((5, 5, 5))                      # loose vert
    a, b = bm.verts.new((9, 0, 0)), bm.verts.new((9, 1, 0))
    bm.edges.new((a, b))                         # loose edge (2 more loose-edge verts count as not loose)
    bm.verts.new((0.5, 0.5, 0.5))                # duplicate of a cube corner -> also loose
    bm.faces.ensure_lookup_table()
    f = bm.faces[0]
    f.normal_flip()                              # one inconsistent face
    ob = _obj(bm)
    r = m.run_checks(ob)
    assert r["loose_verts"] == 2, r
    assert r["loose_edges"] == 1, r
    assert r["doubles"] == 2, r
    assert r["flipped"] == 4, r
    assert r["ngons"] == 0 and r["tris"] == 0
    bpy.ops.mesh_check.fix(check="flipped")
    bpy.ops.mesh_check.fix(check="loose_verts")
    r = m.run_checks(ob)
    assert r["flipped"] == 0 and r["loose_verts"] == 0, r


def test_ngon_triangulate_and_select():
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, segments=8, cap_ends=True, radius=1)
    ob = _obj(bm)
    assert m.run_checks(ob)["ngons"] == 1
    bpy.ops.mesh_check.select(check="ngons")
    assert ob.mode == 'EDIT'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert sum(p.select for p in ob.data.polygons) == 1
    bpy.ops.mesh_check.fix(check="ngons")
    assert m.run_checks(ob)["ngons"] == 0
