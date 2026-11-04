import bpy


def _obs():
    out = []
    for n in "ABC":
        o = bpy.data.objects.new(n, None)
        bpy.context.scene.collection.objects.link(o)
        out.append(o)
    return out


def test_add_rename_select_remove():
    a, b, c = _obs()
    for o in (a, b):
        o.select_set(True)
    s = bpy.context.window_manager.custom_props
    s.where, s.name, s.ptype, s.float_value, s.min, s.max = 'OBJECTS', "health", 'FLOAT', 0.5, 0, 2
    bpy.ops.custom_props.add()
    assert a["health"] == 0.5 and "health" not in c.keys()
    ui = a.id_properties_ui("health").as_dict()
    assert ui["max"] == 2 and ui["min"] == 0
    a["health"] = 1.0
    for o in (a, b):
        o.select_set(False)
    bpy.ops.custom_props.select(match_value=True)
    assert b.select_get() and not a.select_get()
    s.new_name = "hp"
    bpy.ops.custom_props.rename()
    assert "hp" in a.keys() and "health" not in a.keys() and a["hp"] == 1.0
    assert a.id_properties_ui("hp").as_dict()["max"] == 2
    s.name = "hp"
    bpy.ops.custom_props.remove(everywhere=True)
    assert "hp" not in a.keys() and "hp" not in b.keys()
