import bmesh
import bpy


def _mesh(name, loc=(0, 0, 0), size=2, plane=False):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if plane:
        bmesh.ops.create_grid(bm, x_segments=8, y_segments=8, size=size / 2)
    else:
        bmesh.ops.create_cube(bm, size=size)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob


def _select(*obs):
    for o in bpy.context.scene.objects:
        o.select_set(o in obs)
    bpy.context.view_layer.objects.active = obs[0]


def _rb_free():
    return all(o.rigid_body is None for o in bpy.context.scene.objects)


def test_drop_settle_on_plane():
    sc = bpy.context.scene
    sc.frame_set(7)
    plane = _mesh("Floor", (0, 0, 0.5), size=20, plane=True)
    cube = _mesh("Box", (0, 0, 4))
    _select(cube)
    bpy.ops.physics_helpers.drop(frames=150)
    z = cube.matrix_world.translation.z
    assert abs(z - 1.5) < 0.08, z
    assert _rb_free() and sc.rigidbody_world is None
    assert "RigidBodyWorld" not in bpy.data.collections
    assert sc.frame_current == 7
    assert plane.matrix_world.translation.z == 0.5


def test_drop_temp_ground_keeps_user_world():
    sc = bpy.context.scene
    with bpy.context.temp_override(scene=sc):
        bpy.ops.rigidbody.world_add()
    cube = _mesh("Box", (5, 5, 2))
    _select(cube)
    bpy.ops.physics_helpers.drop(frames=60)
    assert sc.rigidbody_world is not None  # pre-existing world kept
    assert "PH_Ground" not in bpy.data.objects
    assert abs(cube.matrix_world.translation.z - 2) < 0.05  # rests on temp floor at its own base
    assert _rb_free()
    with bpy.context.temp_override(scene=sc):
        bpy.ops.rigidbody.world_remove()


def test_drop_to_surface():
    _mesh("Floor", (0, 0, -1), size=20, plane=True)
    cube = _mesh("Box", (0, 0, 6))
    _select(cube)
    bpy.ops.physics_helpers.drop_surface()
    assert abs(cube.matrix_world.translation.z - 0) < 1e-4
    bpy.ops.physics_helpers.drop_surface(random_z=3.0, seed=3)
    assert abs(cube.matrix_world.translation.z) < 1e-3
    assert abs(cube.matrix_world.to_euler().z) > 1e-3


def _cloth_setup():
    cloth = _mesh("Sheet", (0, 0, 3), size=2, plane=True)
    other = _mesh("Ball", (0, 0, 0))
    _select(cloth, other)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')
    pins = [v.index for v in cloth.data.vertices if v.co.y > 0.99]
    for i in pins:
        cloth.data.vertices[i].select = True
    bpy.ops.object.mode_set(mode='EDIT')
    return cloth, other, pins


def test_quick_cloth():
    cloth, other, pins = _cloth_setup()
    bpy.ops.physics_helpers.cloth(preset='DENIM', self_collision=True, hook=True)
    assert bpy.context.mode == 'EDIT_MESH'
    bpy.ops.object.mode_set(mode='OBJECT')
    md = cloth.modifiers["Cloth"]
    s = md.settings
    assert (s.quality, s.mass, s.tension_stiffness, s.bending_stiffness) == (12, 1.0, 40, 10)
    assert md.collision_settings.use_self_collision
    assert s.vertex_group_mass == "Pin"
    vg = cloth.vertex_groups["Pin"].index
    assert sorted(v.index for v in cloth.data.vertices if any(g.group == vg for g in v.groups)) == sorted(pins)
    assert [m.type for m in cloth.modifiers] == ['HOOK', 'CLOTH']
    assert any(m.type == 'COLLISION' for m in other.modifiers)
    # hook at rest leaves the mesh in place
    cloth.modifiers["Cloth"].show_viewport = False
    ev = cloth.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    assert max((a.co - b.co).length for a, b in zip(ev.vertices, cloth.data.vertices)) < 1e-5


def test_cloth_apply_shape_key_and_mesh():
    cloth, _other, _pins = _cloth_setup()
    bpy.ops.physics_helpers.cloth(preset='COTTON')
    bpy.ops.object.mode_set(mode='OBJECT')
    sc = bpy.context.scene
    sc.frame_set(15)
    bpy.ops.physics_helpers.cloth_apply(target='SHAPE_KEY')
    keys = cloth.data.shape_keys.key_blocks
    assert "Cloth_15" in keys and "Cloth" in [m.type.title() for m in cloth.modifiers]
    assert min(v.co.z for v in keys["Cloth_15"].data) < -0.2  # unpinned edge sagged
    cloth.shape_key_clear()
    bpy.ops.physics_helpers.cloth_apply(target='MESH')
    assert not any(m.type == 'CLOTH' for m in cloth.modifiers)
    assert min(v.co.z for v in cloth.data.vertices) < -0.2
    sc.frame_set(1)


def test_drop_below_mode_stacks():
    base = _mesh("Base", (0, 0, 1))  # top at z=2
    _mesh("Side", (0, 0, -50))  # far below, never reached (base is in the way)
    cube = _mesh("Box", (0, 0, 5), size=1)
    _select(cube)
    bpy.ops.physics_helpers.drop(ground='BELOW', frames=120, add_ground=False)
    assert abs(cube.matrix_world.translation.z - 2.5) < 0.08, cube.matrix_world.translation
    assert base.matrix_world.translation.z == 1 and _rb_free()
