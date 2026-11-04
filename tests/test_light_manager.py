import bpy
from mathutils import Vector


def _light(name, kind='POINT', loc=(0, 0, 0)):
    ob = bpy.data.objects.new(name, bpy.data.lights.new(name, kind))
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _emissive_mesh(name, strength):
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
    mat = bpy.data.materials.new(name)
    mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = strength
    me.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _mod():
    import importlib
    return importlib.import_module("bl_ext.clanker.light_manager")


def _select(*obs):
    for o in bpy.context.scene.objects:
        o.select_set(o in obs)
    bpy.context.view_layer.objects.active = obs[-1] if obs else None


def test_list_lights_and_emissive():
    _light("B"), _light("a", 'SUN')
    _emissive_mesh("Glow", 5.0)
    _emissive_mesh("Dull", 0.0)
    names = [o.name for o in _mod().list_lights(bpy.context.scene, emissive=True)]
    assert names == ["a", "B", "Glow"], names
    assert "Glow" not in [o.name for o in _mod().list_lights(bpy.context.scene, emissive=False)]


def test_solo_and_restore():
    a, b, c = _light("A"), _light("B"), _light("C")
    c.hide_render = True
    bpy.ops.light_manager.solo(name="A")
    assert not a.hide_viewport and not a.hide_render
    assert b.hide_viewport and b.hide_render and c.hide_viewport
    bpy.ops.light_manager.solo(name="A")
    assert not b.hide_viewport and not b.hide_render
    assert not c.hide_viewport and c.hide_render  # previous state, not just "visible"
    bpy.ops.light_manager.solo(name="A")
    bpy.ops.light_manager.solo(name="B")  # switching solo restores first
    assert a.hide_viewport and not b.hide_viewport and c.hide_render
    bpy.ops.light_manager.solo(name="B")
    assert not a.hide_viewport and not a.hide_render and c.hide_render


def test_kelvin():
    ob = _light("K")
    _select(ob)
    bpy.ops.light_manager.temperature(kelvin=2000)
    r, g, b = ob.data.temperature_color
    assert ob.data.use_temperature and r > g > b
    bpy.ops.light_manager.temperature(kelvin=6500)
    r, g, b = ob.data.temperature_color
    assert max(r, g, b) - min(r, g, b) < 0.1, (r, g, b)


def test_aim_at_cursor_and_place():
    ob = _light("S", 'SPOT', (3, -2, 4))
    bpy.context.scene.cursor.location = (1, 1, 0)
    _select(ob)
    bpy.ops.light_manager.aim(target='CURSOR')
    bpy.context.view_layer.update()
    fwd = (ob.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    want = (Vector((1, 1, 0)) - Vector((3, -2, 4))).normalized()
    assert (fwd - want).length < 1e-4
    assert (ob.matrix_world.translation - Vector((3, -2, 4))).length < 1e-5
    bpy.ops.light_manager.place(target='CURSOR', distance=2.0)
    bpy.context.view_layer.update()
    assert abs((ob.matrix_world.translation - Vector((1, 1, 0))).length - 2.0) < 1e-4


def test_track_to():
    ob, tgt = _light("L"), bpy.data.objects.new("T", None)
    bpy.context.scene.collection.objects.link(tgt)
    _select(ob, tgt)
    bpy.ops.light_manager.track()
    c = ob.constraints[0]
    assert c.type == 'TRACK_TO' and c.target == tgt and not tgt.constraints


def test_scale_energy():
    a, b = _light("A"), _light("B")
    a.data.energy = b.data.energy = 100
    _select(a)
    bpy.ops.light_manager.scale_energy(factor=0.5)
    assert a.data.energy == 50 and b.data.energy == 100
    _select()
    bpy.ops.light_manager.scale_energy(factor=2)
    assert a.data.energy == 100 and b.data.energy == 200


def test_hdri_rotation():
    w = bpy.data.worlds.new("HDRI")
    bpy.context.scene.world = w
    nodes = w.node_tree.nodes
    env = nodes.new('ShaderNodeTexEnvironment')
    w.node_tree.links.new(env.outputs["Color"], nodes["Background"].inputs["Color"])
    bpy.ops.light_manager.hdri_mapping(rotation=1.5)
    bg, env2, mp = _mod().world_nodes(w)
    assert env2 == env and mp is not None
    assert abs(mp.inputs["Rotation"].default_value[2] - 1.5) < 1e-6
    bpy.ops.light_manager.hdri_mapping(rotation=0.5)  # reuses existing mapping
    assert len([n for n in nodes if n.bl_idname == 'ShaderNodeMapping']) == 1
    assert abs(mp.inputs["Rotation"].default_value[2] - 0.5) < 1e-6
    bpy.data.worlds.remove(w)

