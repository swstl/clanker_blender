import bpy


def _mat(name, rough=0.5):
    m = bpy.data.materials.new(name)
    m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = rough
    return m


def _p(m):
    return m.node_tree.nodes["Principled BSDF"].inputs


def test_set_multiply_and_skip_linked():
    a, b = _mat("A", 0.4), _mat("B", 0.8)
    tex = b.node_tree.nodes.new("ShaderNodeTexNoise")
    b.node_tree.links.new(tex.outputs["Fac"], _p(b)["Metallic"])
    s = bpy.context.scene.batch_materials
    s.scope = 'ALL'
    s.use_metallic, s.metallic = True, 1.0
    s.float_mode = 'SET'
    bpy.ops.batch_materials.apply()
    assert _p(a)["Metallic"].default_value == 1.0
    assert _p(b)["Metallic"].default_value == 0.0  # linked: untouched
    s.use_metallic = False
    s.use_roughness, s.roughness, s.float_mode = True, 2.0, 'MULTIPLY'
    bpy.ops.batch_materials.apply()
    assert abs(_p(a)["Roughness"].default_value - 0.8) < 1e-6
    assert _p(b)["Roughness"].default_value == 1.0  # clamped


def test_sync_viewport():
    a = _mat("A", 0.2)
    _p(a)["Base Color"].default_value = (1, 0, 0, 1)
    bpy.context.scene.batch_materials.scope = 'ALL'
    bpy.ops.batch_materials.sync_viewport()
    assert tuple(a.diffuse_color) == (1, 0, 0, 1)
    assert abs(a.roughness - 0.2) < 1e-6
