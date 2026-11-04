"""Preferences Snapshot: zip the config folder (prefs, keymaps, startup file) + an add-on list.

Restore unpacks a snapshot over the config folder; restart Blender afterwards.
Use it to sync machines, try risky settings, or survive a broken userpref.blend.
"""
import datetime
import json
import os
import shutil
import zipfile

import bpy

CONFIG_FILES = ("userpref.blend", "startup.blend", "bookmarks.txt", "recent-files.txt",
                "recent-searches.txt", "platform_support.txt")


def default_folder():
    return os.path.join(os.path.expanduser("~"), "BlenderPrefSnapshots")


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def addon_manifest():
    prefs = bpy.context.preferences
    return {
        "blender": bpy.app.version_string,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "addons": sorted(prefs.addons.keys()),
        "repos": [{"name": r.name, "module": r.module, "url": r.remote_url, "enabled": r.enabled}
                  for r in prefs.extensions.repos],
    }


def make_snapshot(folder, config_dir=None, note=""):
    config_dir = config_dir or bpy.utils.user_resource('CONFIG')
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in note)[:30]
    path = os.path.join(folder, f"blender{bpy.app.version[0]}.{bpy.app.version[1]}_{stamp}"
                                + (f"_{safe}" if safe else "") + ".zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in CONFIG_FILES:
            p = os.path.join(config_dir, f)
            if os.path.isfile(p):
                z.write(p, f"config/{f}")
        z.writestr("manifest.json", json.dumps(addon_manifest(), indent=2))
    return path


def restore_snapshot(path, config_dir=None):
    """Unpack config/* into config_dir, keeping a .bak copy of each replaced file. Returns file list."""
    config_dir = config_dir or bpy.utils.user_resource('CONFIG')
    os.makedirs(config_dir, exist_ok=True)
    restored = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.startswith("config/") or name.endswith("/"):
                continue
            base = os.path.basename(name)
            if base not in CONFIG_FILES:
                continue  # never write anything unexpected (zip slip etc.)
            dst = os.path.join(config_dir, base)
            if os.path.exists(dst):
                shutil.copy2(dst, dst + ".bak")
            with z.open(name) as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            restored.append(base)
    return restored


def list_snapshots(folder):
    if not os.path.isdir(folder):
        return []
    return sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".zip")),
                  key=os.path.getmtime, reverse=True)


class PS_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    folder: bpy.props.StringProperty(name="Snapshot Folder", subtype='DIR_PATH', default=default_folder())
    note: bpy.props.StringProperty(name="Note")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "folder")
        row = col.row(align=True)
        row.prop(self, "note", text="")
        row.operator("prefs_snapshot.create", icon='FILE_TICK')
        for p in list_snapshots(bpy.path.abspath(self.folder))[:12]:
            row = col.row(align=True)
            row.label(text=os.path.basename(p), icon='FILE_ARCHIVE')
            row.operator("prefs_snapshot.restore", text="Restore").filepath = p
        col.operator("wm.path_open", text="Open Folder").filepath = bpy.path.abspath(self.folder)


class PS_OT_create(bpy.types.Operator):
    bl_idname = "prefs_snapshot.create"
    bl_label = "Save Snapshot"
    bl_description = "Save preferences now and zip preferences, keymaps and startup file"

    def execute(self, context):
        if not bpy.app.background:
            bpy.ops.wm.save_userpref()
        p = _prefs()
        path = make_snapshot(bpy.path.abspath(p.folder), note=p.note)
        self.report({'INFO'}, f"Snapshot: {path}")
        return {'FINISHED'}


class PS_OT_restore(bpy.types.Operator):
    bl_idname = "prefs_snapshot.restore"
    bl_label = "Restore Snapshot"
    bl_description = "Overwrite current config files with this snapshot (old ones kept as .bak). Restart Blender after"

    filepath: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        files = restore_snapshot(self.filepath)
        context.preferences.use_preferences_save = False  # don't let auto-save overwrite the restore on quit
        self.report({'WARNING'}, f"Restored {len(files)} files. Restart Blender now (auto-save prefs disabled)")
        return {'FINISHED'}


_classes = (PS_Prefs, PS_OT_create, PS_OT_restore)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
