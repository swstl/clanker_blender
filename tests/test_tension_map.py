import importlib

import bmesh
import bpy
import numpy as np

M = importlib.import_module("bl_ext.clanker.tension_map")


def _plane():
    """Grid in -1..1 with a shape key that doubles x on the right half and halves it on the left."""
    me = bpy.data.meshes.new("Plane")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=10, y_segments=10, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("Plane", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.shape_key_add(name="Basis")
    key = ob.shape_key_add(name="Deform")
    for p in key.data:
        p.co.x *= 2.0 if p.co.x > 0 else 0.5
    key.value = 1.0
    for o in bpy.context.scene.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    return ob


def _attr(ob, name):
    me = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    a = me.attributes[name]
    out = np.empty(len(a.data) * (4 if a.data_type == 'FLOAT_COLOR' else 1), np.float32)
    a.data.foreach_get("color" if a.data_type == 'FLOAT_COLOR' else "value", out)
    return out


def _halves(ob):
    x = np.array([v.co.x for v in ob.data.vertices])
    return x > 0.3, x < -0.3


def test_tension_attributes():
    ob = _plane()
    ob.modifiers.new("Subsurf", 'SUBSURF')  # added before: tension mod must still end up last
    assert bpy.ops.tension_map.add() == {'FINISHED'}
    assert ob.add_rest_position_attribute
    md = M.find_mod(ob)
    assert ob.modifiers[-1] == md
    ob.modifiers.remove(ob.modifiers["Subsurf"])
    right, left = _halves(ob)
    t = _attr(ob, "tension")
    assert t[right].mean() > 0.3, t[right].mean()
    assert t[left].mean() < -0.15, t[left].mean()
    s, c = _attr(ob, "stretch"), _attr(ob, "compress")
    assert s.min() >= 0 and c.min() >= 0
    assert np.allclose(s - c, t, atol=1e-5)
    col = _attr(ob, "tension_color").reshape(-1, 4)
    assert col[right, 0].mean() > col[right, 2].mean()  # red-ish where stretched
    assert col[left, 2].mean() > col[left, 0].mean()  # blue-ish where compressed

    # strength input scales the map
    item = next(i for i in md.node_group.interface.items_tree if i.name == "Strength")
    getattr(md.properties.inputs, item.identifier).value = 2.0
    ob.update_tag()  # setting GN inputs from Python does not re-evaluate by itself (5.2)
    assert np.allclose(_attr(ob, "tension"), 2 * t, atol=1e-5)


def test_rest_pose_is_zero():
    ob = _plane()
    ob.data.shape_keys.key_blocks["Deform"].value = 0.0
    bpy.ops.tension_map.add()
    assert np.allclose(_attr(ob, "tension"), 0, atol=1e-5)


def test_preview_material():
    ob = _plane()
    bpy.ops.tension_map.add()
    md = M.find_mod(ob)
    item = next(i for i in md.node_group.interface.items_tree if i.name == "Tension Name")
    getattr(md.properties.inputs, item.identifier).value = "wrinkle"
    mine = bpy.data.materials.new("Skin")
    ob.data.materials.append(mine)
    assert bpy.ops.tension_map.add_material() == {'FINISHED'}
    mat = ob.material_slots[0].material
    assert mat.name == M.MAT and ob.data.materials[0] == mine  # mesh material untouched
    attrs = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeAttribute"]
    assert attrs and attrs[0].attribute_name == "wrinkle"
    assert any(n.bl_idname == "ShaderNodeValToRGB" for n in mat.node_tree.nodes)
    assert "wrinkle" in ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.attributes

    bpy.ops.tension_map.remove()
    assert M.find_mod(ob) is None and not ob.add_rest_position_attribute
    assert ob.material_slots[0].link == 'DATA' and ob.material_slots[0].material == mine


def test_bake_vertex_groups():
    ob = _plane()
    bpy.ops.tension_map.add()
    assert bpy.ops.tension_map.bake() == {'FINISHED'}
    right, left = _halves(ob)
    s, c = _attr(ob, "stretch"), _attr(ob, "compress")

    def weights(name):
        vg = ob.vertex_groups[name]
        w = np.zeros(len(ob.data.vertices))
        for v in ob.data.vertices:
            for g in v.groups:
                if g.group == vg.index:
                    w[v.index] = g.weight
        return w

    ws, wc = weights("tension_stretch"), weights("tension_compress")
    assert np.allclose(ws, np.clip(s, 0, 1), atol=1e-5)
    assert np.allclose(wc, np.clip(c, 0, 1), atol=1e-5)
    assert ws[right].mean() > 0.3 and ws[left].max() < 1e-5
    assert wc[left].mean() > 0.15 and wc[right].max() < 1e-5
    # baking again replaces, the tension result is unaffected by the new groups
    bpy.ops.tension_map.bake()
    assert np.allclose(_attr(ob, "stretch"), s, atol=1e-5)
