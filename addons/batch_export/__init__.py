"""Batch Export: one file per object / hierarchy / collection, with game engine presets."""
import os
import re

import bpy
from bpy_extras import anim_utils

EXT = {'FBX': ".fbx", 'GLB': ".glb", 'GLTF': ".gltf", 'OBJ': ".obj", 'USD': ".usd"}

# Engine presets: (format, axis_forward, axis_up, scale, extra FBX kwargs).
PRESETS = {
    'UNITY': ('FBX', '-Z', 'Y', 1.0, dict(
        apply_scale_options='FBX_SCALE_ALL', bake_space_transform=True,
        mesh_smooth_type='FACE', add_leaf_bones=False)),
    'UNREAL': ('FBX', '-Z', 'Y', 1.0, dict(
        apply_unit_scale=True, apply_scale_options='FBX_SCALE_NONE',
        mesh_smooth_type='FACE', add_leaf_bones=False, use_tspace=True)),
    'GODOT': ('GLB', '-Z', 'Y', 1.0, {}),
    'GENERIC': ('GLB', '-Z', 'Y', 1.0, {}),
}
AXES = [(a, a, "") for a in ('X', 'Y', 'Z', '-X', '-Y', '-Z')]


def _apply_preset(self, context):
    self.format, self.axis_forward, self.axis_up, self.scale, _ = PRESETS[self.preset]


class BE_Settings(bpy.types.PropertyGroup):
    folder: bpy.props.StringProperty(name="Folder", subtype='DIR_PATH', default="//export/")
    preset: bpy.props.EnumProperty(name="Engine", update=_apply_preset, default='GENERIC', items=[
        ('UNITY', "Unity", "FBX, scale applied (FBX All), transform baked"),
        ('UNREAL', "Unreal", "FBX, unit scale applied, face smoothing, no leaf bones"),
        ('GODOT', "Godot", "glTF binary (.glb)"),
        ('GENERIC', "Generic", "glTF binary (.glb)")])
    format: bpy.props.EnumProperty(name="Format", default='GLB', items=[
        ('FBX', "FBX", ""), ('GLB', "glTF Binary (.glb)", ""),
        ('GLTF', "glTF Separate (.gltf + .bin)", ""), ('OBJ', "OBJ", ""), ('USD', "USD", "")])
    mode: bpy.props.EnumProperty(name="Mode", items=[
        ('OBJECTS', "Per Object", "One file per selected object"),
        ('HIERARCHY', "Per Hierarchy", "One file per top-level parent of the selection, with all children"),
        ('COLLECTION', "Per Collection", "One file per collection of the selected objects")])
    axis_forward: bpy.props.EnumProperty(name="Forward", items=AXES, default='-Z')
    axis_up: bpy.props.EnumProperty(name="Up", items=AXES, default='Y')
    scale: bpy.props.FloatProperty(name="Scale", default=1.0, min=0.0001)
    to_origin: bpy.props.BoolProperty(
        name="Move to World Origin", default=True,
        description="Temporarily move each exported root to 0,0,0 (collections: by their instance offset)")
    apply_modifiers: bpy.props.BoolProperty(name="Apply Modifiers", default=True)
    include_children: bpy.props.BoolProperty(name="Include Children", default=True)
    prefix: bpy.props.StringProperty(name="Prefix")
    suffix: bpy.props.StringProperty(name="Suffix")
    sanitize: bpy.props.BoolProperty(name="Sanitize Names", default=True,
                                     description="Replace spaces and special characters with _")
    split_anims: bpy.props.BoolProperty(
        name="Animations as Separate Files", default=False,
        description="Export each armature action to its own file (mesh file is exported without animation)")
    open_folder: bpy.props.BoolProperty(name="Open Folder After Export", default=False)


def file_name(s, name):
    name = f"{s.prefix}{name}{s.suffix}"
    if s.sanitize:
        name = re.sub(r"[^\w\-.]+", "_", name).strip("_.") or "export"
    return name


def jobs(context, s):
    """[(name, objects, roots, offset)] where roots get moved by -offset (None = to origin)."""
    sel = list(context.selected_objects)
    out = []
    if s.mode == 'COLLECTION':
        seen = []
        for o in sel:
            for c in o.users_collection:
                if c not in seen and c != context.scene.collection:
                    seen.append(c)
        for c in seen:
            objs = [o for o in c.all_objects if o.name in context.view_layer.objects]
            roots = [o for o in objs if o.parent not in objs]
            out.append((c.name, objs, roots, c.instance_offset.copy()))
        return out
    if s.mode == 'HIERARCHY':
        tops = []
        for o in sel:
            while o.parent:
                o = o.parent
            if o not in tops:
                tops.append(o)
        sel = tops
    for o in sel:
        objs = [o] + (list(o.children_recursive) if s.include_children or s.mode == 'HIERARCHY' else [])
        out.append((o.name, [x for x in objs if x.name in context.view_layer.objects], [o], None))
    return out


def actions_for(arm):
    """Actions with an object slot animating at least one of this armature's bones."""
    bones = {b.name for b in arm.data.bones}
    found = []
    for act in bpy.data.actions:
        for slot in act.slots:
            if slot.target_id_type != 'OBJECT':
                continue
            bag = anim_utils.action_get_channelbag_for_slot(act, slot)
            paths = [fc.data_path for fc in bag.fcurves] if bag else []
            if any(p.startswith('pose.bones["') and p.split('"')[1] in bones for p in paths):
                found.append((act, slot))
                break
    return found


def run_export(s, path, anim):
    fwd, up = s.axis_forward, s.axis_up
    if s.format == 'FBX':
        kw = dict(global_scale=s.scale, axis_forward=fwd, axis_up=up, use_mesh_modifiers=s.apply_modifiers,
                  bake_anim=anim is not False)
        if anim:  # only the assigned action, over the scene range we set
            kw.update(bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False)
        kw.update(PRESETS[s.preset][4] if s.preset in ('UNITY', 'UNREAL') else {})
        bpy.ops.export_scene.fbx(filepath=path, use_selection=True, **kw)
    elif s.format in ('GLB', 'GLTF'):
        kw = dict(export_animation_mode='ACTIVE_ACTIONS') if anim else {}
        bpy.ops.export_scene.gltf(
            filepath=path, use_selection=True, export_format='GLB' if s.format == 'GLB' else 'GLTF_SEPARATE',
            export_yup=up == 'Y', export_apply=s.apply_modifiers, export_animations=anim is not False, **kw)
    elif s.format == 'OBJ':
        bpy.ops.wm.obj_export(
            filepath=path, export_selected_objects=True, apply_modifiers=s.apply_modifiers, global_scale=s.scale,
            forward_axis=fwd.replace("-", "NEGATIVE_"), up_axis=up.replace("-", "NEGATIVE_"))
    else:  # USD always evaluates modifiers
        bpy.ops.wm.usd_export(
            filepath=path, selected_objects_only=True, export_animation=bool(anim), convert_orientation=True,
            export_global_forward_selection=fwd.replace("-", "NEGATIVE_"),
            export_global_up_selection=up.replace("-", "NEGATIVE_"))


def batch_export(context, s):
    """Export every job. Returns (written_paths, failures). Restores scene state no matter what."""
    folder = bpy.path.abspath(s.folder)
    if s.folder.startswith("//") and not bpy.data.filepath:
        raise RuntimeError("Save the .blend file first, or use an absolute output folder")
    os.makedirs(folder, exist_ok=True)
    vl, scene = context.view_layer, context.scene
    selected = [o for o in vl.objects if o.select_get()]
    hidden = [o for o in vl.objects if o.hide_get()]
    active = vl.objects.active
    frames = scene.frame_start, scene.frame_end
    written, failed = [], []
    try:
        for name, objs, roots, offset in jobs(context, s):
            base = os.path.join(folder, file_name(s, name))
            saved = [(r, r.location.copy()) for r in roots]
            anims = []
            try:
                for o in vl.objects:
                    o.select_set(False)
                for o in objs:
                    o.hide_set(False)
                    o.select_set(True)
                vl.objects.active = objs[0] if objs else None
                if s.to_origin:
                    for r in roots:
                        mw = r.matrix_world.copy()
                        mw.translation = mw.translation - offset if offset is not None else (0, 0, 0)
                        r.matrix_world = mw
                    vl.update()
                split = s.split_anims and s.format != 'OBJ'
                run_export(s, base + EXT[s.format], False if split else None)
                written.append(base + EXT[s.format])
                if split:
                    for arm in (o for o in objs if o.type == 'ARMATURE'):
                        ad = arm.animation_data or arm.animation_data_create()
                        anims.append((ad, ad.action, ad.action_slot))
                        for act, slot in actions_for(arm):
                            ad.action, ad.action_slot = act, slot
                            scene.frame_start, scene.frame_end = map(int, act.frame_range)
                            path = os.path.join(folder, file_name(s, f"{name}_{act.name}"))
                            try:
                                run_export(s, path + EXT[s.format], True)
                                written.append(path + EXT[s.format])
                            except Exception as e:
                                failed.append(f"{name}/{act.name}: {e}")
            except Exception as e:
                failed.append(f"{name}: {e}")
            finally:
                for r, loc in saved:
                    r.location = loc
                for ad, act, slot in anims:
                    ad.action = act
                    if act:
                        ad.action_slot = slot
                scene.frame_start, scene.frame_end = frames
    finally:
        for o in vl.objects:
            o.select_set(o in selected)
            o.hide_set(o in hidden)
        vl.objects.active = active
    return written, failed


class BE_OT_export(bpy.types.Operator):
    bl_idname = "batch_export.export"
    bl_label = "Batch Export"
    bl_description = "Export the selection to separate files using the scene's Batch Export settings"

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        s = context.scene.batch_export
        try:
            written, failed = batch_export(context, s)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        for f in failed:
            print("Batch Export failed:", f)
        msg = f"Exported {len(written)} file(s)" + (f", {len(failed)} failed: {'; '.join(failed)}" if failed else "")
        self.report({'WARNING'} if failed else {'INFO'}, msg)
        if s.open_folder and not bpy.app.background:
            bpy.ops.wm.path_open(filepath=bpy.path.abspath(s.folder))
        return {'FINISHED'}


class BE_PT_panel(bpy.types.Panel):
    bl_label = "Batch Export"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Export"

    def draw(self, context):
        s = context.scene.batch_export
        col = self.layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        for p in ("folder", "preset", "format", "mode", "axis_forward", "axis_up", "scale"):
            col.prop(s, p)
        for p in ("to_origin", "apply_modifiers", "include_children", "split_anims", "open_folder"):
            col.prop(s, p)
        col.separator()
        for p in ("prefix", "suffix", "sanitize"):
            col.prop(s, p)
        n = len(jobs(context, s)) if context.mode == 'OBJECT' else 0
        self.layout.operator(BE_OT_export.bl_idname, text=f"Export {n} File(s)", icon='EXPORT')


def menu(self, context):
    self.layout.operator(BE_OT_export.bl_idname, text="Batch Export (Scene Settings)")


classes = (BE_Settings, BE_OT_export, BE_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.batch_export = bpy.props.PointerProperty(type=BE_Settings)
    bpy.types.TOPBAR_MT_file_export.append(menu)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu)
    del bpy.types.Scene.batch_export
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
