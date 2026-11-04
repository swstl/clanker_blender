"""Batch Import: every model file in a folder, one collection per file, laid out in a grid."""
import math
import os

import bpy
from mathutils import Vector

IMPORTERS = {
    ".fbx": ("import_scene", "fbx", {}),
    ".obj": ("wm", "obj_import", {}),
    ".gltf": ("import_scene", "gltf", {}),
    ".glb": ("import_scene", "gltf", {}),
    ".stl": ("wm", "stl_import", {}),
    ".ply": ("wm", "ply_import", {}),
    ".usd": ("wm", "usd_import", {}), ".usda": ("wm", "usd_import", {}), ".usdc": ("wm", "usd_import", {}),
    ".usdz": ("wm", "usd_import", {}),
    ".abc": ("wm", "alembic_import", {"as_background_job": False}),
}


def find_files(folder, recursive, exts):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if recursive else []
        for f in sorted(files):
            if os.path.splitext(f)[1].lower() in exts:
                out.append(os.path.join(root, f))
    return out


def import_file(path):
    """Run the matching importer; return the newly created objects."""
    mod, op, extra = IMPORTERS[os.path.splitext(path)[1].lower()]
    before = set(bpy.data.objects)
    getattr(getattr(bpy.ops, mod), op)(filepath=path, **extra)
    return [o for o in bpy.data.objects if o not in before]


def _world_bounds(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return Vector(), Vector()
    return (Vector(tuple(min(p[i] for p in pts) for i in range(3))),
            Vector(tuple(max(p[i] for p in pts) for i in range(3))))


def run(context, folder, recursive=False, exts=None, grid=True, spacing=0.5, remove_empties=False):
    exts = exts or set(IMPORTERS)
    files = find_files(folder, recursive, exts)
    root = bpy.data.collections.new(os.path.basename(os.path.normpath(folder)) or "Imported")
    context.scene.collection.children.link(root)
    results, failed = [], []
    cols = max(1, math.ceil(math.sqrt(len(files))))
    x = y = row_depth = 0.0
    for i, path in enumerate(files):
        try:
            new = import_file(path)
        except Exception as e:  # keep going: one broken file shouldn't stop a batch
            failed.append((path, str(e).splitlines()[0] if str(e) else type(e).__name__))
            continue
        coll = bpy.data.collections.new(os.path.splitext(os.path.basename(path))[0])
        root.children.link(coll)
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            coll.objects.link(o)
        if remove_empties:
            for o in [o for o in new if o.type == 'EMPTY' and not o.children]:
                bpy.data.objects.remove(o)
                new.remove(o)
        if grid and new:
            context.view_layer.update()
            mn, mx = _world_bounds(new)
            tops = [o for o in new if o.parent is None or o.parent not in new]
            offset = Vector((x - mn.x, y - mn.y, -mn.z))
            for o in tops:
                o.location += offset
            x += (mx.x - mn.x) + spacing
            row_depth = max(row_depth, mx.y - mn.y)
            if (i + 1) % cols == 0:
                x, y, row_depth = 0.0, y + row_depth + spacing, 0.0
        results.append((path, len(new)))
    return root, results, failed


class BI_OT_import(bpy.types.Operator):
    bl_idname = "batch_import.folder"
    bl_label = "Batch Import Folder"
    bl_description = "Import every model file in a folder, one collection per file, arranged in a grid"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    recursive: bpy.props.BoolProperty(name="Include Subfolders", default=False)
    fbx: bpy.props.BoolProperty(name="FBX", default=True)
    obj: bpy.props.BoolProperty(name="OBJ", default=True)
    gltf: bpy.props.BoolProperty(name="glTF/GLB", default=True)
    stl: bpy.props.BoolProperty(name="STL", default=True)
    ply: bpy.props.BoolProperty(name="PLY", default=True)
    usd: bpy.props.BoolProperty(name="USD", default=True)
    abc: bpy.props.BoolProperty(name="Alembic", default=False)
    grid: bpy.props.BoolProperty(name="Arrange in Grid", default=True)
    spacing: bpy.props.FloatProperty(name="Spacing", default=0.5, min=0, unit='LENGTH')
    remove_empties: bpy.props.BoolProperty(name="Remove Childless Empties", default=False)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        exts = set()
        for flag, ex in ((self.fbx, {".fbx"}), (self.obj, {".obj"}), (self.gltf, {".gltf", ".glb"}),
                         (self.stl, {".stl"}), (self.ply, {".ply"}),
                         (self.usd, {".usd", ".usda", ".usdc", ".usdz"}), (self.abc, {".abc"})):
            if flag:
                exts |= ex
        if not os.path.isdir(self.directory):
            self.report({'WARNING'}, "Pick a folder")
            return {'CANCELLED'}
        _, ok, failed = run(context, self.directory, self.recursive, exts, self.grid, self.spacing,
                            self.remove_empties)
        for path, err in failed:
            print("Batch Import failed:", path, err)
        self.report({'WARNING' if failed else 'INFO'},
                    f"Imported {len(ok)} files" + (f", {len(failed)} failed (see console)" if failed else ""))
        return {'FINISHED'}


def menu(self, context):
    self.layout.operator("batch_import.folder", text="Batch Import Folder...")


def register():
    bpy.utils.register_class(BI_OT_import)
    bpy.types.TOPBAR_MT_file_import.append(menu)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu)
    bpy.utils.unregister_class(BI_OT_import)
