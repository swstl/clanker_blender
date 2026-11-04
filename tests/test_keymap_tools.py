import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.keymap_tools")


def test_conflict_detected_between_addon_items():
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
    a = km.keymap_items.new("object.select_all", 'F13', 'PRESS', ctrl=True)
    b = km.keymap_items.new("object.delete", 'F13', 'PRESS', ctrl=True)
    same = km.keymap_items.new("object.delete", 'F14', 'PRESS')
    try:
        res = m.find_conflicts([kc])
        hits = [r for r in res if r[1] == "Ctrl+F13"]
        assert hits and len(hits[0][2]) == 2
        assert not [r for r in res if r[1] == "F14"]
    finally:
        for k in (a, b, same):
            km.keymap_items.remove(k)


def test_cheat_sheet_written():
    p = os.path.join(tempfile.mkdtemp(), "k.html")
    kc = bpy.context.window_manager.keyconfigs.addon
    km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
    k = km.keymap_items.new("object.delete", 'F15', 'PRESS', shift=True)
    try:
        bpy.ops.keymap_tools.cheat_sheet(filepath=p, open_browser=False)
        html = open(p).read()
        assert "<input" in html
        s = m.cheat_sheet_html(kc)
        assert "Shift+F15" in s
    finally:
        km.keymap_items.remove(k)
