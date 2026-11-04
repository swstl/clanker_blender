"""Keymap Tools: shortcut conflict report (incl. add-on keymaps) and an HTML cheat sheet."""
import html
import os
import tempfile

import bpy

MODS = ("ctrl", "alt", "shift", "oskey")


def combo(kmi):
    if kmi.map_type not in {'KEYBOARD', 'MOUSE'}:
        return None
    if kmi.any:
        mods = "Any+"
    else:
        mods = "".join(m.title() + "+" for m in MODS if getattr(kmi, m) == 1)
    km = f"{kmi.key_modifier}+" if kmi.key_modifier != 'NONE' else ""
    val = "" if kmi.value == 'PRESS' else f" ({kmi.value.title()})"
    return f"{mods}{km}{kmi.type}{val}"


def describe(kmi):
    name = kmi.name or kmi.idname
    for key in ("name", "data_path"):  # wm.call_menu(_pie) / wm.context_toggle show what they call
        if kmi.properties and key in kmi.properties.bl_rna.properties:
            v = getattr(kmi.properties, key, "")
            if v:
                name += f" [{v}]"
    return name


def _props_sig(kmi):
    p = kmi.properties
    if not p:
        return ()
    return tuple((k, str(getattr(p, k, None))) for k in p.bl_rna.properties.keys() if k != "rna_type"
                 and p.is_property_set(k))


def find_conflicts(keyconfigs):
    """Items in the same keymap name with the same key combo but different actions.

    keyconfigs: list of KeyConfig (e.g. [user, addon]); keymaps with the same name are merged,
    because Blender evaluates add-on keymaps together with the user keymap of that name."""
    groups = {}
    for kc in keyconfigs:
        for km in kc.keymaps:
            for kmi in km.keymap_items:
                if not kmi.active:
                    continue
                c = combo(kmi)
                if c is None:
                    continue
                groups.setdefault((km.name, c), []).append((kc.name, kmi))
    out = []
    for (kmname, c), items in sorted(groups.items()):
        actions = {(k.idname, _props_sig(k)) for _, k in items}
        if len(actions) > 1:
            out.append((kmname, c, [(kcn, describe(k)) for kcn, k in items]))
    return out


def cheat_sheet_html(kc, title="Blender Keymap"):
    rows = []
    for km in sorted(kc.keymaps, key=lambda k: k.name):
        for kmi in km.keymap_items:
            c = combo(kmi)
            if c and kmi.active:
                rows.append(f"<tr><td>{html.escape(km.name)}</td><td><kbd>{html.escape(c)}</kbd></td>"
                            f"<td>{html.escape(describe(kmi))}</td></tr>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:14px system-ui;margin:16px;background:#1d1d1d;color:#ddd}}input{{width:100%;padding:8px;
font-size:16px;margin-bottom:8px}}table{{border-collapse:collapse;width:100%}}td{{padding:3px 8px;
border-bottom:1px solid #333}}kbd{{background:#333;border-radius:3px;padding:1px 5px}}</style></head><body>
<h2>{html.escape(title)}</h2><input id="q" placeholder="Search editor, key or action..." autofocus>
<table id="t">{''.join(rows)}</table><script>
document.getElementById('q').oninput=e=>{{const q=e.target.value.toLowerCase();
for(const r of document.querySelectorAll('#t tr'))r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';}};
</script></body></html>"""


class KT_OT_conflicts(bpy.types.Operator):
    bl_idname = "keymap_tools.conflicts"
    bl_label = "Find Shortcut Conflicts"
    bl_description = "List shortcuts that trigger different actions in the same editor (user + add-on keymaps)"

    def execute(self, context):
        wm = context.window_manager
        res = find_conflicts([wm.keyconfigs.user, wm.keyconfigs.addon])
        txt = bpy.data.texts.get("Shortcut Conflicts") or bpy.data.texts.new("Shortcut Conflicts")
        txt.clear()
        for kmname, c, items in res:
            txt.write(f"[{kmname}] {c}\n")
            for kcn, d in items:
                txt.write(f"    {kcn:>12}: {d}\n")
        self.report({'INFO'}, f"{len(res)} conflicts, see text 'Shortcut Conflicts' in the Text Editor")
        return {'FINISHED'}


class KT_OT_cheat_sheet(bpy.types.Operator):
    bl_idname = "keymap_tools.cheat_sheet"
    bl_label = "Export Keymap Cheat Sheet"
    bl_description = "Write the active keymap as a searchable HTML page and open it in the browser"

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    open_browser: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        kc = context.window_manager.keyconfigs.user
        path = self.filepath or os.path.join(tempfile.gettempdir(), "blender_keymap.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cheat_sheet_html(kc, f"Blender {bpy.app.version_string} keymap: {kc.name}"))
        if self.open_browser:
            bpy.ops.wm.url_open(url="file://" + path)
        self.report({'INFO'}, path)
        return {'FINISHED'}


def help_menu(self, context):
    self.layout.separator()
    self.layout.operator("keymap_tools.conflicts", icon='ERROR')
    self.layout.operator("keymap_tools.cheat_sheet", icon='KEYINGSET')


_classes = (KT_OT_conflicts, KT_OT_cheat_sheet)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_help.append(help_menu)


def unregister():
    bpy.types.TOPBAR_MT_help.remove(help_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
