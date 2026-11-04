import importlib

import bpy

import drawcheck

gp = importlib.import_module("bl_ext.clanker.gn_panel")


def _group():
    ng = bpy.data.node_groups.new("GNP", "GeometryNodeTree")
    ng.is_modifier = True
    it = ng.interface
    it.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    it.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    f = it.new_socket("Lift", in_out="INPUT", socket_type="NodeSocketFloat")
    f.default_value = 0.5
    i = it.new_socket("Count", in_out="INPUT", socket_type="NodeSocketInt")
    i.default_value = 3
    b = it.new_socket("Flag", in_out="INPUT", socket_type="NodeSocketBool")
    v = it.new_socket("Dir", in_out="INPUT", socket_type="NodeSocketVector")
    v.default_value = (1, 2, 3)
    o = it.new_socket("Target", in_out="INPUT", socket_type="NodeSocketObject")
    pan = it.new_panel("Extra")
    for s in (i, b):
        it.move_to_parent(s, pan, len(pan.interface_items))
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    sp = ng.nodes.new("GeometryNodeSetPosition")
    ng.links.new(gi.outputs["Geometry"], sp.inputs["Geometry"])
    ng.links.new(gi.outputs["Lift"], sp.inputs["Offset"])
    ng.links.new(sp.outputs["Geometry"], go.inputs["Geometry"])
    return ng, {s.name: s.identifier for s in (f, i, b, v, o)}


def _setup():
    ng, ids = _group()
    obs = []
    for n in range(3):
        bpy.ops.mesh.primitive_cube_add(location=(n * 3, 0, 0))
        ob = bpy.context.object
        ob.modifiers.new("GN", 'NODES').node_group = ng
        obs.append(ob)
    for ob in obs:
        ob.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    return ng, ids, obs


def _zmax(ob):
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return max(v.co.z for v in ev.data.vertices)


def test_identifiers_and_pins():
    ng, ids, _obs = _setup()
    assert gp.shown_identifiers(ng, False) == [ids[n] for n in ("Lift", "Dir", "Target", "Count", "Flag")]
    assert gp.shown_identifiers(ng, True) == []
    gp.set_pin(ng, ids["Count"], True)
    gp.set_pin(ng, ids["Lift"], True)
    assert gp.shown_identifiers(ng, True) == [ids["Lift"], ids["Count"]]
    with bpy.context.temp_override(active_object=_obs[0]):
        bpy.ops.gn_panel.toggle_pin(modifier="GN", identifier=ids["Count"])
    assert gp.get_pins(ng) == {ids["Lift"]}


def test_set_copy_reset_and_eval():
    ng, ids, (a, b, c) = _setup()
    md = a.modifiers["GN"]
    z0 = _zmax(a)
    assert abs(z0 - 1.5) < 1e-5, z0  # cube top 1 + default lift 0.5
    gp.set_value(md, ids["Lift"], 2.0)
    gp.set_value(md, ids["Count"], 7)
    gp.set_value(md, ids["Flag"], True)
    gp.set_value(md, ids["Dir"], (4, 5, 6))
    gp.set_value(md, ids["Target"], c)
    assert abs(_zmax(a) - 3.0) < 1e-5  # evaluated geometry follows
    assert abs(_zmax(b) - 1.5) < 1e-5

    # pinned only: just Lift goes over
    gp.set_pin(ng, ids["Lift"], True)
    with bpy.context.temp_override(active_object=a, selected_objects=[a, b, c]):
        bpy.ops.gn_panel.copy_to_selected(modifier="GN", pinned_only=True)
    ib = b.modifiers["GN"].properties.inputs
    assert getattr(ib, ids["Lift"]).value == 2.0 and getattr(ib, ids["Count"]).value == 3
    assert abs(_zmax(b) - 3.0) < 1e-5

    with bpy.context.temp_override(active_object=a, selected_objects=[a, b, c]):
        bpy.ops.gn_panel.copy_to_selected(modifier="GN")
    for ob in (b, c):
        ins = ob.modifiers["GN"].properties.inputs
        assert getattr(ins, ids["Count"]).value == 7
        assert getattr(ins, ids["Flag"]).value is True
        assert tuple(getattr(ins, ids["Dir"]).value) == (4, 5, 6)
        assert getattr(ins, ids["Target"]).value == c

    # attribute mode is copied too; nothing left to change afterwards
    getattr(md.properties.inputs, ids["Lift"]).type = 'ATTRIBUTE'
    getattr(md.properties.inputs, ids["Lift"]).attribute_name = "height"
    assert gp.sync_selected(a, [a, b, c]) == 2
    assert getattr(b.modifiers["GN"].properties.inputs, ids["Lift"]).attribute_name == "height"
    assert gp.sync_selected(a, [a, b, c]) == 0

    with bpy.context.temp_override(active_object=a):
        for name in ("Lift", "Dir", "Count"):
            bpy.ops.gn_panel.reset_input(modifier="GN", identifier=ids[name])
    ia = md.properties.inputs
    assert getattr(ia, ids["Lift"]).value == 0.5 and getattr(ia, ids["Lift"]).type == 'VALUE'
    assert tuple(getattr(ia, ids["Dir"]).value) == (1, 2, 3) and getattr(ia, ids["Count"]).value == 3
    assert abs(_zmax(a) - 1.5) < 1e-5


def test_draw_with_modifier():
    ng, ids, (a, _b, _c) = _setup()
    gp.set_pin(ng, ids["Count"], True)
    sc = bpy.context.scene
    for pinned in (False, True):
        sc.gn_panel_pinned_only = pinned
        gp.GNP_PT_panel.draw(drawcheck._Self(gp.GNP_PT_panel), drawcheck.Ctx(active_object=a))
    sc.gn_panel_pinned_only = False
