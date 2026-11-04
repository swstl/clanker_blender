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


def test_disable_addon_while_merged():
    """Disable an add-on while merged, back to KEEP, disable hub: nothing raises, tabs restored."""
    pre = "bl_ext.clanker."
    for a in ("camera_manager", "light_manager", "rig_doctor"):
        bpy.ops.preferences.addon_enable(module=pre + a)
    cm = importlib.import_module(pre + "camera_manager").CM_PT_panel
    orig = cm.bl_category
    p = m._prefs()
    p.merged_name = "QoL"
    p.mode = 'MERGE'
    bpy.ops.preferences.addon_disable(module=pre + "camera_manager")
    assert not cm.is_registered
    p.mode = 'KEEP'
    # Subpanels still attached to their (re-registered) parents.
    assert bpy.types.RD_PT_view3d_fix.is_registered
    bpy.ops.preferences.addon_disable(module=pre + "clanker_hub")
    try:
        bpy.ops.preferences.addon_enable(module=pre + "camera_manager")
        assert cm.bl_category == orig, cm.bl_category  # stale class not left in the merged tab
        bpy.ops.preferences.addon_disable(module=pre + "camera_manager")
        bpy.ops.preferences.addon_disable(module=pre + "light_manager")
        bpy.ops.preferences.addon_disable(module=pre + "rig_doctor")
    finally:
        bpy.ops.preferences.addon_enable(module=pre + "clanker_hub")


def test_merge_then_hub_off_with_addon_disabled():
    pre = "bl_ext.clanker."
    bpy.ops.preferences.addon_enable(module=pre + "camera_manager")
    cm = importlib.import_module(pre + "camera_manager").CM_PT_panel
    orig = cm.bl_category
    p = m._prefs()
    p.mode = 'MERGE'
    bpy.ops.preferences.addon_disable(module=pre + "camera_manager")
    bpy.ops.preferences.addon_disable(module=pre + "clanker_hub")
    try:
        assert cm.bl_category == orig, cm.bl_category
    finally:
        bpy.ops.preferences.addon_enable(module=pre + "clanker_hub")
        m2 = importlib.import_module(pre + "clanker_hub")
        m2._prefs().mode = 'KEEP'
