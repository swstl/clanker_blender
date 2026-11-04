import importlib
import os
import tempfile

import bpy
import numpy as np

bh = importlib.import_module("bl_ext.clanker.bake_helper")

COLOR = (0.8, 0.2, 0.1)


def _srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _cube(name, rough=0.3, metal=0.7, loc=(0, 0, 0), size=2.0):
    """Plane (its UVs fill the whole 0-1 square, so the image mean equals the baked value)."""
    bpy.ops.mesh.primitive_plane_add(size=size, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    mat = bpy.data.materials.new(name + "_mat")
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*COLOR, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    ob.data.materials.append(mat)
    return ob, mat


def _settings(**kw):
    s = bpy.context.scene.bake_helper
    for k in bh.MAPS:
        setattr(s, "map_" + k, False)
    base = dict(resolution=32, margin=2, flat_samples=1, samples=4, save=True, output_dir=tempfile.mkdtemp(),
                selected_to_active=False, atlas=False, map_orm=False, create_material=False, use_float=False,
                uv_mode='ACTIVE')
    base.update(kw)
    for k, v in base.items():
        setattr(s, k, v)
    return s


def _mean(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(-1, 4)[:, :3].mean(axis=0)


def _select(*obs, active):
    for o in bpy.context.scene.objects:
        o.select_set(o in obs)
    bpy.context.view_layer.objects.active = active


def _snapshot(mat):
    nt = mat.node_tree
    return (sorted(n.name for n in nt.nodes),
            sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                   for l in nt.links),
            nt.nodes.active.name if nt.nodes.active else None)


def test_bake_maps_and_restore():
    ob, mat = _cube("Box")
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.cycles.samples = 77
    before = _snapshot(mat)
    s = _settings(map_base_color=True, map_roughness=True, map_metallic=True, map_alpha=True)
    assert bpy.ops.bake_helper.bake() == {'FINISHED'}

    d = s.output_dir
    col = _mean(os.path.join(d, "Box_BaseColor.png"))
    assert np.allclose(col, [_srgb(c) for c in COLOR], atol=0.03), col
    assert np.allclose(_mean(os.path.join(d, "Box_Roughness.png")), 0.3, atol=0.03)
    assert np.allclose(_mean(os.path.join(d, "Box_Metallic.png")), 0.7, atol=0.03)
    assert np.allclose(_mean(os.path.join(d, "Box_Alpha.png")), 1.0, atol=0.03)
    # everything restored
    assert scene.render.engine == 'BLENDER_EEVEE' and scene.cycles.samples == 77
    assert _snapshot(mat) == before
    assert ob.select_get() and bpy.context.view_layer.objects.active == ob


def test_restore_on_failure():
    ob, mat = _cube("Box")
    before = _snapshot(mat)
    bpy.context.scene.render.engine = 'BLENDER_WORKBENCH'
    _settings(map_metallic=True, save=False)
    orig = bh.MAPS['metallic']
    bh.MAPS['metallic'] = orig[:1] + ('NOT_A_PASS',) + orig[2:]
    try:
        bh.bake(bpy.context, bpy.context.scene.bake_helper)
        raise AssertionError("bake should have failed")
    except TypeError:
        pass
    finally:
        bh.MAPS['metallic'] = orig
    assert _snapshot(mat) == before
    assert bpy.context.scene.render.engine == 'BLENDER_WORKBENCH'


def test_orm_pack_and_material():
    ob, mat = _cube("Box")
    s = _settings(map_base_color=True, map_orm=True, create_material=True, assign='KEEP', uv_mode='BAKE')
    assert bpy.ops.bake_helper.bake() == {'FINISHED'}
    d = s.output_dir
    r, g, b = _mean(os.path.join(d, "Box_ORM.png"))
    assert abs(r - 1.0) < 0.05 and abs(g - 0.3) < 0.03 and abs(b - 0.7) < 0.03, (r, g, b)
    # components only baked for packing are not written separately
    assert sorted(os.listdir(d)) == ["Box_BaseColor.png", "Box_ORM.png"]
    assert "Bake" in ob.data.uv_layers and ob.data.uv_layers.active.name == "UVMap"
    baked = bpy.data.materials["Box_baked"]
    assert [sl.material for sl in ob.material_slots] == [baked, mat]
    bsdf = next(n for n in baked.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    assert bsdf.inputs["Metallic"].links[0].from_socket.name == "Blue"
    assert bsdf.inputs["Base Color"].links[0].from_node.image.name == "Box_BaseColor"
    uvn = next(n for n in baked.node_tree.nodes if n.type == 'UVMAP')
    assert uvn.uv_map == "Bake"


def test_atlas():
    a, _ = _cube("A", rough=0.2, loc=(-3, 0, 0))
    b, _ = _cube("B", rough=0.2, loc=(3, 0, 0))
    _select(a, b, active=a)
    s = _settings(map_roughness=True, atlas=True, atlas_name="Atlas", uv_mode='BAKE', create_material=True,
                  assign='REPLACE')
    assert bpy.ops.bake_helper.bake() == {'FINISHED'}
    assert os.listdir(s.output_dir) == ["Atlas_Roughness.png"]
    m = bpy.data.materials["Atlas_baked"]
    assert [sl.material for sl in a.material_slots] == [m] == [sl.material for sl in b.material_slots]


def test_selected_to_active():
    low, _ = _cube("Low", rough=0.1, metal=0.0)
    high, _ = _cube("High", rough=0.6, metal=0.0, loc=(0, 0, 0.02))
    _select(low, high, active=low)
    s = _settings(map_roughness=True, map_metallic=True, selected_to_active=True, cage_extrusion=0.1)
    assert bpy.ops.bake_helper.bake() == {'FINISHED'}
    assert np.allclose(_mean(os.path.join(s.output_dir, "Low_Roughness.png")), 0.6, atol=0.03)
    assert sorted(os.listdir(s.output_dir)) == ["Low_Metallic.png", "Low_Roughness.png"]
    assert not any(n.label == bh.TAG for m in bpy.data.materials for n in m.node_tree.nodes)


def test_lit_and_normal_passes():
    _cube("Box")
    s = _settings(map_normal=True, use_float=True, map_emission=True, map_combined=True, map_glossy=True,
                  map_ao=True)
    assert bpy.ops.bake_helper.bake() == {'FINISHED'}
    assert sorted(os.listdir(s.output_dir)) == ["Box_AO.png", "Box_Combined.png", "Box_Emission.png",
                                                "Box_Glossy.png", "Box_Normal.exr"]
    assert np.allclose(_mean(os.path.join(s.output_dir, "Box_Normal.exr")), (0.5, 0.5, 1.0), atol=0.02)
