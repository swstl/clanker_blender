import importlib

import bpy
from mathutils import noise
from mathutils.bvhtree import BVHTree

rt = importlib.import_module("bl_ext.clanker.remesh_tools")


def _detailed_sphere():
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    ob = bpy.context.active_object
    for v in ob.data.vertices:
        v.co += v.normal * 0.05 * noise.noise(v.co * 4)
    bpy.context.view_layer.update()
    return ob


def _mean_dist(ob, src):
    # Use the same triangulation as Shrinkwrap (the noisy quads are not planar).
    src.data.calc_loop_triangles()
    bvh = BVHTree.FromPolygons([v.co for v in src.data.vertices], [t.vertices for t in src.data.loop_triangles])
    me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    d = [bvh.find_nearest(v.co)[3] for v in me.vertices]
    return sum(d) / len(d)


def _remesh(ob, reproject, **kw):
    s = bpy.context.scene.remesh_tools
    s.reproject = reproject
    s.size_mode = 'VOXEL'
    for k, v in kw.items():
        setattr(s, k, v)
    ob.data.remesh_voxel_size = 0.08
    n0 = len(ob.data.vertices)
    assert bpy.ops.remesh_tools.remesh() == {'FINISHED'}
    src = ob[rt.BACKUP_KEY]
    assert src.name == f"{ob.name}_detail_src" and len(src.data.vertices) == n0
    assert src.users_collection[0].name == rt.BACKUP_COLL and not src.visible_get()
    assert len(ob.data.vertices) != n0
    return src


def test_estimate_math():
    assert abs(rt.voxel_size_for(100.0, 1500) - 0.1 ** 0.5) < 1e-9  # sqrt(1.5 * 100 / 1500)
    assert rt.faces_for(100.0, rt.voxel_size_for(100.0, 1500)) in (1499, 1500)
    ob = _detailed_sphere()
    s = bpy.context.scene.remesh_tools
    s.size_mode, s.target_faces = 'FACES', 8000
    size, faces = rt.estimate(bpy.context, ob)
    ob.data.remesh_voxel_size = size
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.voxel_remesh()
    assert 0.8 < len(ob.data.polygons) / 8000 < 1.25, len(ob.data.polygons)


def test_shrinkwrap_and_restore():
    ob = _detailed_sphere()
    n0 = len(ob.data.vertices)
    src = _remesh(ob, 'SHRINKWRAP')
    assert _mean_dist(ob, src) < 0.08 * 0.05
    assert not ob.modifiers
    assert bpy.ops.remesh_tools.restore() == {'FINISHED'}
    assert len(ob.data.vertices) == n0 and rt.BACKUP_KEY not in ob
    assert f"{ob.name}_detail_src" not in bpy.data.objects


def test_multires_from_sculpt_mode():
    ob = _detailed_sphere()
    ob.modifiers.new("Bevel", 'BEVEL')  # restored on restore
    bpy.ops.object.mode_set(mode='SCULPT')
    src = _remesh(ob, 'MULTIRES', levels=1, apply_modifiers=False)
    assert ob.mode == 'SCULPT'
    md = ob.modifiers[0]
    assert md.type == 'MULTIRES' and md.total_levels == 1
    assert _mean_dist(ob, src) < 0.08 * 0.1
    bpy.ops.remesh_tools.restore()
    assert [m.type for m in ob.modifiers] == ['BEVEL'] and ob.mode == 'SCULPT'
    bpy.ops.object.mode_set(mode='OBJECT')


def test_shape_keys_refused_and_delete_backups():
    ob = _detailed_sphere()
    ob.shape_key_add(name="Basis")
    try:
        bpy.ops.remesh_tools.remesh()
    except RuntimeError as e:
        assert "shape keys" in str(e)
    ob.shape_key_clear()
    _remesh(ob, 'NONE')
    bpy.ops.remesh_tools.delete_backups()
    assert rt.BACKUP_COLL not in bpy.data.collections and rt.BACKUP_KEY not in ob


def test_decimate_lod():
    ob = _detailed_sphere()
    s = bpy.context.scene.remesh_tools
    s.lod_mode, s.lod_faces = 'FACES', 500
    bpy.ops.remesh_tools.decimate_lod()
    lod = bpy.data.objects[f"{ob.name}_LOD"]
    assert abs(len(lod.data.polygons) - 500) < 100, len(lod.data.polygons)
    assert len(ob.data.polygons) == 2048 and not lod.modifiers and lod.name in bpy.context.scene.objects
