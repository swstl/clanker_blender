"""Version Snapshots: periodic timestamped copies of the current .blend, kept next to it.

Unlike Blender's autosave (one file in /tmp, overwritten) this keeps a history you can browse.
"""
import os
import time

import bpy
from bpy.app.handlers import persistent

STAMP = "%Y%m%d_%H%M%S"


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def versions_dir(blend_path=None):
    blend_path = blend_path or bpy.data.filepath
    if not blend_path:
        return ""
    folder = _prefs().folder_name
    return os.path.join(os.path.dirname(blend_path), folder)


def list_versions(blend_path=None):
    """[(path, mtime, note)] newest first, for snapshots of this blend only."""
    blend_path = blend_path or bpy.data.filepath
    d = versions_dir(blend_path)
    if not d or not os.path.isdir(d):
        return []
    stem = os.path.splitext(os.path.basename(blend_path))[0]
    out = []
    for f in os.listdir(d):
        if f.startswith(stem + "__") and f.endswith(".blend"):
            p = os.path.join(d, f)
            note = f[len(stem) + 2:-6].split("__", 1)
            out.append((p, os.path.getmtime(p), note[1] if len(note) > 1 else ""))
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def _safe(s):
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in s).strip().replace(" ", "-")[:40]


def snapshot(note=""):
    """Write a copy of the current file into the versions folder. Returns the path or None."""
    if not bpy.data.filepath:
        return None
    d = versions_dir()
    os.makedirs(d, exist_ok=True)
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    name = f"{stem}__{time.strftime(STAMP)}" + (f"__{_safe(note)}" if note else "") + ".blend"
    path = os.path.join(d, name)
    i = 2
    while os.path.exists(path):  # two snapshots in the same second
        path = os.path.join(d, name[:-6] + f"_{i}.blend")
        i += 1
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    with bpy.context.temp_override(window=win) if win else _null():
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, check_existing=False, compress=_prefs().compress)
    prune()
    return path


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def prune():
    keep = _prefs().keep
    if keep <= 0:
        return
    # Snapshots with a note are milestones: never pruned automatically.
    auto = [v for v in list_versions() if not v[2]]
    for p, _, _ in auto[keep:]:
        try:
            os.remove(p)
        except OSError:
            pass


# is_dirty stays True after a copy-save, so without this an idle unsaved file would be
# snapshotted every interval and the identical copies would prune the real history.
# ponytail: edits that never touch the depsgraph (e.g. text editor only) don't count as changes.
_changed = [False]


@persistent
def _on_change(*_):
    _changed[0] = True


def _timer():
    p = _prefs()
    if p.enabled and bpy.data.filepath and bpy.data.is_dirty and _changed[0]:
        mode = bpy.context.mode if hasattr(bpy.context, "mode") else "OBJECT"
        # Saving mid-sculpt/paint stroke can stall; wait for object/edit-free moments.
        if mode in {'OBJECT', 'POSE', 'EDIT_MESH', 'EDIT_ARMATURE'}:
            try:
                snapshot()
                _changed[0] = False
            except Exception as e:
                print("Version Snapshots:", e)
    return max(60.0, p.interval_minutes * 60.0)


class VS_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    enabled: bpy.props.BoolProperty(name="Timed snapshots", default=True)
    interval_minutes: bpy.props.FloatProperty(name="Every (minutes)", default=10, min=1)
    keep: bpy.props.IntProperty(name="Keep last", default=20, min=0,
                                description="Automatic snapshots to keep per file (0 = keep all). Named ones are never deleted")
    folder_name: bpy.props.StringProperty(name="Folder", default="_versions")
    compress: bpy.props.BoolProperty(name="Compress", default=True)

    def draw(self, context):
        col = self.layout.column()
        for p in ("enabled", "interval_minutes", "keep", "folder_name", "compress"):
            col.prop(self, p)


class VS_OT_snapshot(bpy.types.Operator):
    bl_idname = "version_snapshots.snapshot"
    bl_label = "Save Snapshot"
    bl_description = "Save a timestamped copy of this file to the versions folder (optionally with a note)"

    note: bpy.props.StringProperty(name="Note", description="Named snapshots are kept forever",
                                   options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        path = snapshot(self.note)
        self.report({'INFO'}, f"Snapshot: {os.path.basename(path)}")
        return {'FINISHED'}


class VS_OT_open(bpy.types.Operator):
    bl_idname = "version_snapshots.open"
    bl_label = "Open Snapshot"
    bl_description = "Open this snapshot (save it under a new name to continue from it)"

    filepath: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        bpy.ops.wm.open_mainfile(filepath=self.filepath)
        return {'FINISHED'}


class VS_OT_open_folder(bpy.types.Operator):
    bl_idname = "version_snapshots.open_folder"
    bl_label = "Open Folder"
    bl_description = "Show the versions folder in the file browser of your OS"

    def execute(self, context):
        d = versions_dir()
        os.makedirs(d, exist_ok=True)
        bpy.ops.wm.path_open(filepath=d)
        return {'FINISHED'}


class VS_PT_panel(bpy.types.Panel):
    bl_label = "Version Snapshots"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        col = self.layout.column()
        if not bpy.data.filepath:
            col.label(text="Save the file first", icon='INFO')
            return
        row = col.row(align=True)
        row.operator("version_snapshots.snapshot", icon='FILE_TICK')
        row.operator("version_snapshots.open_folder", text="", icon='FILE_FOLDER')
        col.prop(_prefs(), "enabled")
        for p, mtime, note in list_versions()[:15]:
            label = time.strftime("%b %d  %H:%M", time.localtime(mtime)) + (f"  {note}" if note else "")
            col.operator("version_snapshots.open", text=label,
                         icon='BOOKMARKS' if note else 'TIME', emboss=False).filepath = p


def file_menu(self, context):
    self.layout.operator("version_snapshots.snapshot", icon='FILE_TICK')


_classes = (VS_Prefs, VS_OT_snapshot, VS_OT_open, VS_OT_open_folder, VS_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file.append(file_menu)
    bpy.app.handlers.depsgraph_update_post.append(_on_change)
    if not bpy.app.background:
        bpy.app.timers.register(_timer, first_interval=60, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    if _on_change in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_change)
    bpy.types.TOPBAR_MT_file.remove(file_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
