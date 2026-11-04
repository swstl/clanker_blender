"""Remesh Tools: voxel remesh with a hidden detail backup, reproject the detail back
(Shrinkwrap or Multires reshape), restore from backup, and quick decimated LODs."""
import math

import bpy
import numpy as np

BACKUP_COLL = "RemeshBackups"
BACKUP_KEY = "remesh_tools_backup"
# A voxel grid surface is made of axis-aligned quads, so it has more faces than area/size²:
# averaged over all normal directions |nx|+|ny|+|nz| = 1.5.
GRID_FACTOR = 1.5


def mesh_area(me):
    a = np.empty(len(me.polygons), dtype=np.float32)
    me.polygons.foreach_get("area", a)
    return float(a.sum())


def voxel_size_for(area, target_faces):
    return math.sqrt(GRID_FACTOR * area / max(target_faces, 1))


def faces_for(area, voxel_size):
    return int(GRID_FACTOR * area / max(voxel_size, 1e-6) ** 2)


def _source_mesh(context, ob, apply_mods):
    return ob.evaluated_get(context.evaluated_depsgraph_get()).data if apply_mods else ob.data


def estimate(context, ob):
    """(voxel_size, estimated face count) for the current settings."""
    s = context.scene.remesh_tools
    area = mesh_area(_source_mesh(context, ob, s.apply_modifiers))
    size = voxel_size_for(area, s.target_faces) if s.size_mode == 'FACES' else ob.data.remesh_voxel_size
    return size, faces_for(area, size)


def _ops_ctx(ob, *others):
    return bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob, *others],
                                     selected_editable_objects=[ob, *others])


def _backup_collection(context):
    coll = bpy.data.collections.get(BACKUP_COLL)
    if coll is None:
        coll = bpy.data.collections.new(BACKUP_COLL)
        coll.hide_render = coll.hide_select = True
    if coll.name not in context.scene.collection.children:
        context.scene.collection.children.link(coll)
    return coll


def _remove_object(ob):
    me = ob.data
    bpy.data.objects.remove(ob)
    if me and me.users == 0:
        bpy.data.meshes.remove(me)


def make_backup(context, ob):
    old = ob.get(BACKUP_KEY)
    if old:
        _remove_object(old)
    bk = ob.copy()
    bk.data = ob.data.copy()
    bk.name = f"{ob.name}_detail_src"
    for c in list(bk.users_collection):
        c.objects.unlink(bk)
    _backup_collection(context).objects.link(bk)
    context.view_layer.update()
    bk.hide_set(True)  # eye-hidden only: hide_viewport would drop it from the depsgraph (no shrinkwrap)
    ob[BACKUP_KEY] = bk
    return bk


def _baked(ob):
    """New mesh datablock of the object's evaluated result (modifiers applied)."""
    return bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()))


def _shrinkwrap(md, target, method):
    md.target = target
    md.wrap_method = method
    if method == 'PROJECT':
        md.use_negative_direction = md.use_positive_direction = True


def reproject_multires(ob, src, levels, method):
    md = ob.modifiers.new("Multires", 'MULTIRES')
    with _ops_ctx(ob):
        bpy.ops.object.modifier_move_to_index(modifier=md.name, index=0)
    others = [m for m in ob.modifiers if m != md and m.show_viewport]
    for m in others:  # reshape needs the multires result alone
        m.show_viewport = False
    try:
        for _ in range(levels):
            with _ops_ctx(ob):
                bpy.ops.object.multires_subdivide(modifier=md.name, mode='CATMULL_CLARK')
            # Shrinkwrapped copy of the new level, then reshape the level to match it.
            tmp = bpy.data.objects.new("_remesh_tmp", _baked(ob))
            tmp.matrix_world = ob.matrix_world
            bpy.context.scene.collection.objects.link(tmp)
            try:
                _shrinkwrap(tmp.modifiers.new("SW", 'SHRINKWRAP'), src, method)
                old, tmp.data = tmp.data, _baked(tmp)
                tmp.modifiers.clear()
                bpy.data.meshes.remove(old)
                with _ops_ctx(ob, tmp):
                    bpy.ops.object.multires_reshape(modifier=md.name)
            finally:
                _remove_object(tmp)
    finally:
        for m in others:
            m.show_viewport = True


def reproject_shrinkwrap(ob, src, method):
    md = ob.modifiers.new("RemeshReproject", 'SHRINKWRAP')
    _shrinkwrap(md, src, method)
    with _ops_ctx(ob):
        bpy.ops.object.modifier_move_to_index(modifier=md.name, index=0)
        bpy.ops.object.modifier_apply(modifier=md.name)


class _KeepMode:
    """Leave Sculpt/Edit mode for the duration and return to it afterwards."""

    def __init__(self, context):
        self.mode = context.object.mode if context.object else 'OBJECT'

    def __enter__(self):
        if self.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

    def __exit__(self, *exc):
        if self.mode != 'OBJECT' and bpy.context.object:
            bpy.ops.object.mode_set(mode=self.mode)


def _mesh_poll(context):
    ob = context.active_object
    return ob is not None and ob.type == 'MESH' and context.mode in {'OBJECT', 'SCULPT'}


class RT_OT_remesh(bpy.types.Operator):
    bl_idname = "remesh_tools.remesh"
    bl_label = "Remesh + Reproject"
    bl_description = ("Back up the mesh to a hidden object, voxel remesh it and project the detail "
                      "from the backup back onto the new topology")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context)

    def execute(self, context):
        s = context.scene.remesh_tools
        ob = context.active_object
        if ob.data.shape_keys:
            self.report({'ERROR'}, "Object has shape keys: apply or remove them first")
            return {'CANCELLED'}
        with _KeepMode(context):
            size, faces = estimate(context, ob)
            src = make_backup(context, ob)
            if s.apply_modifiers and ob.modifiers:
                old, ob.data = ob.data, _baked(ob)
                ob.modifiers.clear()
                if old.users == 0:
                    bpy.data.meshes.remove(old)
            for m in [m for m in ob.modifiers if m.type == 'MULTIRES']:
                ob.modifiers.remove(m)  # its displacement no longer fits the new topology
            ob.data.remesh_voxel_size = size
            with _ops_ctx(ob):
                if bpy.ops.object.voxel_remesh() != {'FINISHED'}:
                    return {'CANCELLED'}
                if s.quadriflow:
                    bpy.ops.object.quadriflow_remesh(
                        mode='FACES', target_faces=faces, use_mesh_symmetry=False,
                        preserve_attributes=ob.data.use_remesh_preserve_attributes)
            if s.reproject == 'MULTIRES':
                reproject_multires(ob, src, s.levels, s.wrap_method)
            elif s.reproject == 'SHRINKWRAP':
                reproject_shrinkwrap(ob, src, s.wrap_method)
        self.report({'INFO'}, f"Remeshed to {len(ob.data.polygons)} faces (voxel {size:.4g})")
        return {'FINISHED'}


class RT_OT_restore(bpy.types.Operator):
    bl_idname = "remesh_tools.restore"
    bl_label = "Restore Backup"
    bl_description = "Swap the original mesh and modifiers back from the hidden backup and delete it"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context) and context.active_object.get(BACKUP_KEY) is not None

    def execute(self, context):
        ob = context.active_object
        bk = ob[BACKUP_KEY]
        with _KeepMode(context):
            old, ob.data = ob.data, bk.data
            ob.modifiers.clear()
            for m in bk.modifiers:
                with _ops_ctx(bk, ob):
                    bpy.ops.object.modifier_copy_to_selected(modifier=m.name)
            del ob[BACKUP_KEY]
            bpy.data.objects.remove(bk)
            if old.users == 0:
                bpy.data.meshes.remove(old)
        return {'FINISHED'}


class RT_OT_delete_backups(bpy.types.Operator):
    bl_idname = "remesh_tools.delete_backups"
    bl_label = "Delete All Backups"
    bl_description = "Delete every remesh backup object and the RemeshBackups collection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = bpy.data.collections.get(BACKUP_COLL)
        n = 0
        if coll:
            for o in list(coll.objects):
                _remove_object(o)
                n += 1
            bpy.data.collections.remove(coll)
        for o in bpy.data.objects:
            if BACKUP_KEY in o:
                del o[BACKUP_KEY]
        self.report({'INFO'}, f"Deleted {n} backups")
        return {'FINISHED'}


class RT_OT_decimate_lod(bpy.types.Operator):
    bl_idname = "remesh_tools.decimate_lod"
    bl_label = "Quick Decimate LOD"
    bl_description = ("Make a triangulated, decimated copy of the active object (modifiers applied) "
                      "named <object>_LOD")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context)

    def execute(self, context):
        s = context.scene.remesh_tools
        ob = context.active_object
        with _KeepMode(context):
            lod = bpy.data.objects.new(f"{ob.name}_LOD", _baked(ob))
            lod.matrix_world = ob.matrix_world
            for c in ob.users_collection:
                c.objects.link(lod)
            lod.data.calc_loop_triangles()
            n = len(lod.data.loop_triangles)  # Decimate's ratio counts triangles
            md = lod.modifiers.new("Decimate", 'DECIMATE')
            md.use_collapse_triangulate = True
            md.ratio = min(1.0, s.lod_faces / max(n, 1)) if s.lod_mode == 'FACES' else s.lod_ratio
            old, lod.data = lod.data, _baked(lod)
            lod.modifiers.clear()
            bpy.data.meshes.remove(old)
        self.report({'INFO'}, f"{lod.name}: {n} -> {len(lod.data.polygons)} triangles")
        return {'FINISHED'}


class RT_Settings(bpy.types.PropertyGroup):
    size_mode: bpy.props.EnumProperty(name="Size", items=[
        ('VOXEL', "Voxel Size", "Use the mesh's voxel size"),
        ('FACES', "Target Faces", "Derive the voxel size from the surface area and a face budget")])
    target_faces: bpy.props.IntProperty(name="Target Faces", default=50000, min=100)
    reproject: bpy.props.EnumProperty(name="Reproject", default='MULTIRES', items=[
        ('MULTIRES', "Multires", "Add Multires levels and reshape each one onto the backup"),
        ('SHRINKWRAP', "Shrinkwrap", "Shrinkwrap the remeshed surface onto the backup"),
        ('NONE', "None", "Plain remesh")])
    levels: bpy.props.IntProperty(name="Levels", default=2, min=1, max=6)
    wrap_method: bpy.props.EnumProperty(name="Method", items=[
        ('NEAREST_SURFACEPOINT', "Nearest Surface", "Snap to the closest point on the backup"),
        ('PROJECT', "Project", "Project along the normals")])
    quadriflow: bpy.props.BoolProperty(
        name="QuadriFlow", description="Retopologize the voxel result with QuadriFlow (slow, clean quads)")
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers", default=True, description="Bake the modifier stack into the mesh before remeshing")
    lod_mode: bpy.props.EnumProperty(name="LOD", items=[
        ('RATIO', "Ratio", "Keep this fraction of triangles"), ('FACES', "Triangles", "Target triangle count")])
    lod_ratio: bpy.props.FloatProperty(name="Ratio", default=0.25, min=0.001, max=1.0)
    lod_faces: bpy.props.IntProperty(name="Triangles", default=5000, min=4)


class RT_PT_remesh(bpy.types.Panel):
    bl_label = "Remesh"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Sculpt"

    @classmethod
    def poll(cls, context):
        return _mesh_poll(context)

    def draw(self, context):
        s = context.scene.remesh_tools
        ob = context.active_object
        lay = self.layout
        lay.prop(s, "size_mode", expand=True)
        if s.size_mode == 'FACES':
            lay.prop(s, "target_faces")
        else:
            lay.prop(ob.data, "remesh_voxel_size")
        size, faces = estimate(context, ob)
        lay.label(text=f"Voxel {size:.4g}  ~{faces:,} faces", icon='INFO')
        col = lay.column(heading="Preserve")
        col.prop(ob.data, "use_remesh_preserve_volume", text="Volume")
        col.prop(ob.data, "use_remesh_preserve_attributes", text="Attributes")
        lay.prop(s, "quadriflow")
        lay.prop(s, "apply_modifiers")
        lay.prop(s, "reproject")
        if s.reproject != 'NONE':
            lay.prop(s, "wrap_method")
        if s.reproject == 'MULTIRES':
            lay.prop(s, "levels")
        lay.operator("remesh_tools.remesh", icon='MOD_REMESH')
        row = lay.row(align=True)
        row.operator("remesh_tools.restore", icon='LOOP_BACK')
        row.operator("remesh_tools.delete_backups", text="", icon='TRASH')
        box = lay.box()
        box.prop(s, "lod_mode", expand=True)
        box.prop(s, "lod_faces" if s.lod_mode == 'FACES' else "lod_ratio")
        box.operator("remesh_tools.decimate_lod", icon='MOD_DECIM')


_classes = (RT_Settings, RT_OT_remesh, RT_OT_restore, RT_OT_delete_backups, RT_OT_decimate_lod, RT_PT_remesh)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.remesh_tools = bpy.props.PointerProperty(type=RT_Settings)


def unregister():
    del bpy.types.Scene.remesh_tools
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
