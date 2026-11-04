import bmesh
import bpy


def _sphere(name="Rock"):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    ob.location = (3, 0, 0)
    bpy.context.scene.collection.objects.link(ob)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    return ob


def test_collision_names_and_shape():
    ob = _sphere()
    sc = bpy.context.scene
    sc.game_ready_engine = 'UNREAL'
    bpy.ops.game_ready.collision(kind='HULL')
    bpy.ops.game_ready.collision(kind='BOX')
    hull, box = bpy.data.objects["UCX_Rock_01"], bpy.data.objects["UBX_Rock_01"]
    assert hull.parent == ob and hull.hide_render
    assert len(hull.data.vertices) <= len(ob.data.vertices)
    assert len(box.data.vertices) == 8
    bpy.context.view_layer.update()
    assert abs(box.matrix_world.translation.x - 3) < 1e-4  # follows parent space
    sc.game_ready_engine = 'GODOT'
    bpy.ops.game_ready.collision(kind='HULL')
    assert "Rock_01-convcolonly" in bpy.data.objects


def test_lods_and_lightmap_and_socket():
    ob = _sphere("Tree")
    bpy.ops.game_ready.lods(levels=2, ratio=0.5)
    assert ob.name == "Tree_LOD0"
    l1, l2 = bpy.data.objects["Tree_LOD1"], bpy.data.objects["Tree_LOD2"]
    f0 = len(ob.data.polygons)
    assert len(l2.data.polygons) < len(l1.data.polygons) < f0 * 1.5 and not l1.modifiers
    ob.data.uv_layers.new(name="UVMap")
    bpy.ops.game_ready.lightmap_uv()
    assert [u.name for u in ob.data.uv_layers] == ["UVMap", "Lightmap"]
    assert ob.data.uv_layers["UVMap"].active_render
    bpy.context.scene.game_ready_engine = 'UNREAL'
    bpy.context.scene.cursor.location = (3, 0, 1)
    bpy.ops.game_ready.socket(name="Top")
    s = bpy.data.objects["SOCKET_Top"]
    bpy.context.view_layer.update()
    assert s.parent == ob and abs(s.matrix_world.translation.z - 1) < 1e-4
