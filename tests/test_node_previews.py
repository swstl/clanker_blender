import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.node_previews")


def _editor(tree):
    win = bpy.context.window_manager.windows[0]
    area = win.screen.areas[0]
    area.type = 'NODE_EDITOR'
    sp = area.spaces.active
    sp.tree_type = tree.bl_idname
    sp.node_tree = tree
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return sp, bpy.context.temp_override(window=win, area=area, region=region, space_data=sp)


def test_set_all_on_and_off():
    nt = bpy.data.materials.new("NP_Mat").node_tree
    nt.nodes.new("ShaderNodeTexNoise")
    sp, ctx = _editor(nt)
    sp.overlay.show_previews = False
    with ctx:
        assert bpy.ops.node_previews.set_all() == {'FINISHED'}
        assert all(n.show_preview for n in nt.nodes)
        assert sp.overlay.show_previews
        assert bpy.ops.node_previews.set_all(state=False) == {'FINISHED'}
    assert not any(n.show_preview for n in nt.nodes)


def test_auto_previews_only_new_nodes():
    nt = bpy.data.materials.new("NP_Auto").node_tree
    _editor(nt)
    m._tick()  # existing nodes get handled (previewed) once
    bsdf = nt.nodes["Principled BSDF"]
    assert bsdf.show_preview
    bsdf.show_preview = False  # user turns one off: must stay off
    noise = nt.nodes.new("ShaderNodeTexNoise")
    m._tick()
    assert noise.show_preview and not bsdf.show_preview


def test_auto_pref_off():
    m._prefs().auto = False
    try:
        nt = bpy.data.materials.new("NP_Off").node_tree
        _editor(nt)
        m._tick()
        assert not any(n.show_preview for n in nt.nodes)
    finally:
        m._prefs().auto = True
