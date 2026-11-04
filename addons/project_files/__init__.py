"""Project Files: external file overview, collect-into-project, project folder bootstrap."""
import os
import shutil

import bpy

# (data collection, subfolder for collecting)
KINDS = (
    ("images", "textures"),
    ("sounds", "sounds"),
    ("movieclips", "footage"),
    ("fonts", "fonts"),
    ("cache_files", "cache"),
    ("libraries", "libraries"),
    ("volumes", "cache"),
)


def external_files():
    """Yield (kind, datablock, abspath, exists) for every datablock that points to a file on disk."""
    for kind, _ in KINDS:
        for idb in getattr(bpy.data, kind, ()):
            fp = getattr(idb, "filepath", "")
            if not fp or getattr(idb, "packed_file", None):
                continue
            if kind == "images" and idb.source not in {'FILE', 'SEQUENCE', 'MOVIE', 'TILED'}:
                continue
            if kind == "fonts" and fp == "<builtin>":
                continue
            ap = os.path.normpath(bpy.path.abspath(fp, library=idb.library))
            yield kind, idb, ap, os.path.exists(ap.replace("<UDIM>", "1001"))


class PF_OT_report(bpy.types.Operator):
    bl_idname = "project_files.report"
    bl_label = "External Files Report"
    bl_description = "List every external file this .blend uses and whether it exists"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=700)

    def draw(self, context):
        files = sorted(external_files(), key=lambda r: (r[3], r[0], r[1].name))
        missing = sum(1 for f in files if not f[3])
        col = self.layout.column()
        col.label(text=f"{len(files)} external files, {missing} missing",
                  icon='ERROR' if missing else 'CHECKMARK')
        for kind, idb, ap, ok in files[:60]:
            row = col.row()
            row.alert = not ok
            row.label(text=idb.name, icon='CHECKMARK' if ok else 'ERROR')
            row.label(text=ap)
        if len(files) > 60:
            col.label(text=f"... and {len(files) - 60} more")
        row = col.row()
        row.operator("file.find_missing_files", text="Find Missing in Folder...")
        row.operator("project_files.collect")

    def execute(self, context):
        return {'FINISHED'}


def collect(subfolders=True, only_outside=True):
    """Copy external files into folders next to the .blend and relink relative. Returns (copied, skipped)."""
    root = os.path.dirname(bpy.data.filepath)
    copied = skipped = 0
    for kind, sub in KINDS:
        if kind in {"libraries", "cache_files", "volumes"}:
            continue  # moving linked libs/caches breaks relative links inside them: leave alone
        for idb in getattr(bpy.data, kind, ()):
            fp = getattr(idb, "filepath", "")
            if not fp or getattr(idb, "packed_file", None) or idb.library:
                continue
            if kind == "images" and idb.source not in {'FILE', 'MOVIE'}:
                skipped += 1  # sequences/UDIMs: many files, skip
                continue
            src = os.path.normpath(bpy.path.abspath(fp))
            if not os.path.isfile(src):
                skipped += 1
                continue
            if only_outside and os.path.commonpath([root, src]) == root:
                continue
            dst_dir = os.path.join(root, sub) if subfolders else root
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, os.path.basename(src))
            base, ext = os.path.splitext(dst)
            i = 1
            while os.path.exists(dst) and not _same_file(src, dst):
                dst = f"{base}_{i}{ext}"
                i += 1
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
            idb.filepath = bpy.path.relpath(dst)
            copied += 1
    return copied, skipped


def _same_file(a, b):
    try:
        return os.path.getsize(a) == os.path.getsize(b) and open(a, "rb").read(65536) == open(b, "rb").read(65536)
    except OSError:
        return False


class PF_OT_collect(bpy.types.Operator):
    bl_idname = "project_files.collect"
    bl_label = "Collect Files into Project"
    bl_description = ("Copy images, sounds, footage and fonts that live outside the project folder into "
                      "subfolders next to the .blend and relink them with relative paths")
    bl_options = {'REGISTER', 'UNDO'}

    subfolders: bpy.props.BoolProperty(name="Sort into Subfolders", default=True)

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        c, s = collect(self.subfolders)
        self.report({'INFO'}, f"Collected {c} files" + (f", skipped {s} (missing or sequences)" if s else ""))
        return {'FINISHED'}


class PF_OT_bootstrap(bpy.types.Operator):
    bl_idname = "project_files.bootstrap"
    bl_label = "Set Up Project Folders"
    bl_description = "Create standard folders next to the .blend and point render output into renders/"
    bl_options = {'REGISTER', 'UNDO'}

    folders: bpy.props.StringProperty(name="Folders", default="textures, renders, exports, references, cache")
    set_output: bpy.props.BoolProperty(name="Render Output to renders/<scene>/<camera>_", default=True)
    relative: bpy.props.BoolProperty(name="Make All Paths Relative", default=True)

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        root = os.path.dirname(bpy.data.filepath)
        for f in (s.strip() for s in self.folders.split(",")):
            if f:
                os.makedirs(os.path.join(root, f), exist_ok=True)
        if self.set_output:
            sc = context.scene
            cam = sc.camera.name if sc.camera else "render"
            sc.render.filepath = f"//renders/{bpy.path.clean_name(sc.name)}/{bpy.path.clean_name(cam)}_"
        if self.relative:
            bpy.ops.file.make_paths_relative()
        self.report({'INFO'}, "Project folders ready")
        return {'FINISHED'}


class PF_MT_menu(bpy.types.Menu):
    bl_idname = "PF_MT_menu"
    bl_label = "Project Files"

    def draw(self, context):
        lay = self.layout
        lay.operator("project_files.report", icon='FILE')
        lay.operator("project_files.collect", icon='PACKAGE')
        lay.operator("project_files.bootstrap", icon='NEWFOLDER')
        lay.separator()
        lay.operator("file.find_missing_files")
        lay.operator("file.make_paths_relative")
        lay.operator("wm.path_open", text="Open Project Folder").filepath = "//"


def file_menu(self, context):
    self.layout.menu("PF_MT_menu", icon='FILE_FOLDER')


_classes = (PF_OT_report, PF_OT_collect, PF_OT_bootstrap, PF_MT_menu)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_external_data.append(file_menu)
    bpy.types.TOPBAR_MT_file.append(file_menu)


def unregister():
    bpy.types.TOPBAR_MT_file.remove(file_menu)
    bpy.types.TOPBAR_MT_file_external_data.remove(file_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
