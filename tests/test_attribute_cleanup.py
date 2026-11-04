import importlib
import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.attribute_cleanup")


def _ob(name):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    me.uv_layers.new(name="UVMap")
    me.color_attributes.new("Col", 'BYTE_COLOR', 'CORNER')
    me.color_attributes.new("Col.001", 'FLOAT_COLOR', 'POINT')
    me.attributes.new("weight_tmp", 'FLOAT', 'POINT')
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.select_set(True)
    return ob


def test_user_attributes_and_remove_pattern():
    a, b = _ob("A"), _ob("B")
    names = {x.name for x in m.user_attributes(a.data)}
    assert names == {"Col", "Col.001", "weight_tmp"}
    bpy.ops.attribute_cleanup.remove(pattern="col*", colors=True, generic=False)
    for o in (a, b):
        assert not o.data.color_attributes and "weight_tmp" in o.data.attributes
        assert "position" in o.data.attributes and o.data.uv_layers
    bpy.ops.attribute_cleanup.remove(pattern="*", colors=False, generic=True)
    assert "weight_tmp" not in a.data.attributes


def test_rename_select_set_color():
    a, b = _ob("A"), _ob("B")
    bpy.ops.attribute_cleanup.rename(old="Col.001", new="Paint")
    assert "Paint" in a.data.attributes and "Paint" in b.data.attributes
    bpy.ops.attribute_cleanup.set_color(name="Paint")
    assert a.data.color_attributes[a.data.color_attributes.render_color_index].name == "Paint"
    b.data.attributes.remove(b.data.attributes["weight_tmp"])
    bpy.context.view_layer.update()
    bpy.ops.attribute_cleanup.select_with(name="weight_tmp")
    assert a.select_get() and not b.select_get()
    assert m.attr_size(a.data, a.data.attributes["Paint"]) == 8 * 16
