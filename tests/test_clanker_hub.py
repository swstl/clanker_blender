import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.clanker_hub")


def test_merge_rename_restore():
    for a in ("camera_manager", "light_manager", "uv_tools"):
        bpy.ops.preferences.addon_enable(module="bl_ext.clanker." + a)
    panels = m.clanker_panels()
    assert panels
    before = {c.__name__: c.bl_category for c in panels}
    p = m._prefs()
    p.merged_name = "QoL"
    p.mode = 'MERGE'  # update callback applies
    assert all(c.bl_category == "QoL" for c in m.clanker_panels())
    # Panels are still registered and usable after the move.
    assert hasattr(bpy.types, "CM_PT_panel") and bpy.types.CM_PT_panel.is_registered
    p.renames = '{"View": "Cams"}'
    p.mode = 'RENAME'
    after = {c.__name__: c.bl_category for c in m.clanker_panels()}
    assert after["CM_PT_panel"] == "Cams"
    assert all(after[k] == v for k, v in before.items() if v != "View")
    p.mode = 'KEEP'
    assert {c.__name__: c.bl_category for c in m.clanker_panels()} == before
