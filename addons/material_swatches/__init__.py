"""Material Swatches: a preview grid of the file's materials; one click assigns."""
import colorsys
import random

import bmesh
import bpy


def assign(context, mat, mode='REPLACE'):
    """Object Mode: put mat on selected objects (REPLACE all slots / ACTIVE slot / ADD slot).
    Edit Mode: assign mat to selected faces (adds a slot if needed). Returns count."""
    n = 0
    if context.mode == 'EDIT_MESH':
        for ob in context.objects_in_mode_unique_data:
            idx = next((i for i, s in enumerate(ob.material_slots) if s.material == mat), None)
            if idx is None:
                ob.data.materials.append(mat)
                idx = len(ob.material_slots) - 1
            bm = bmesh.from_edit_mesh(ob.data)
            for f in bm.faces:
                if f.select:
                    f.material_index = idx
                    n += 1
            bmesh.update_edit_mesh(ob.data)
        return n
    for ob in context.selected_objects:
        if not hasattr(ob.data, "materials"):
            continue
        mats = ob.data.materials
        if mode == 'ADD' or not ob.material_slots:
            if mat.name not in [m.name for m in mats if m]:
                mats.append(mat)
        elif mode == 'ACTIVE':
            ob.material_slots[ob.active_material_index].material = mat
        else:
            for s in ob.material_slots:
                s.material = mat
        n += 1
    return n


class MS_OT_assign(bpy.types.Operator):
    bl_idname = "material_swatches.assign"
    bl_label = "Assign Material"
    bl_description = "Assign to selected objects (Object Mode) or selected faces (Edit Mode). Shift: add as new slot"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()
    mode: bpy.props.EnumProperty(items=[('REPLACE', "Replace All Slots", ""), ('ACTIVE', "Active Slot", ""),
                                        ('ADD', "Add Slot", "")])

    def invoke(self, context, event):
        if event.shift:
            self.mode = 'ADD'
        else:
            self.mode = context.scene.material_swatches_mode
        return self.execute(context)

    def execute(self, context):
        mat = bpy.data.materials.get(self.name)
        if not mat:
            return {'CANCELLED'}
        n = assign(context, mat, self.mode)
        self.report({'INFO'}, f"{mat.name} -> {n}")
        return {'FINISHED'}


class MS_OT_new(bpy.types.Operator):
    bl_idname = "material_swatches.new"
    bl_label = "New Swatch"
    bl_description = "Create a new material with a random color and assign it"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = bpy.data.materials.new("Swatch")
        h = random.random()
        rgb = colorsys.hsv_to_rgb(h, 0.6, 0.8)
        mat.diffuse_color = (*rgb, 1)
        bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*rgb, 1)
        if context.selected_objects:
            assign(context, mat, context.scene.material_swatches_mode)
        return {'FINISHED'}


class MS_OT_random_viewport(bpy.types.Operator):
    bl_idname = "material_swatches.random_viewport_colors"
    bl_label = "Random Viewport Colors"
    bl_description = ("Give every material a distinct Viewport Display color (Solid mode, Color: Material) "
                      "so parts are easy to tell apart. Render colors are untouched")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mats = [m for m in bpy.data.materials if not m.library]
        golden = 0.618033988749895
        h = random.random()
        for m in mats:
            h = (h + golden) % 1.0  # golden ratio spacing: neighbours never look alike
            m.diffuse_color = (*colorsys.hsv_to_rgb(h, 0.55, 0.85), 1)
        shading = context.space_data.shading if context.space_data and context.space_data.type == 'VIEW_3D' else None
        if shading:
            shading.color_type = 'MATERIAL'
        return {'FINISHED'}


class MS_OT_select_users(bpy.types.Operator):
    bl_idname = "material_swatches.select_users"
    bl_label = "Select Objects Using"
    bl_description = "Select all visible objects that use this material"

    name: bpy.props.StringProperty()

    def execute(self, context):
        mat = bpy.data.materials.get(self.name)
        for ob in context.view_layer.objects:
            if ob.visible_get():
                ob.select_set(any(s.material == mat for s in ob.material_slots))
        return {'FINISHED'}


class MS_PT_panel(bpy.types.Panel):
    bl_label = "Material Swatches"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Material"

    def draw(self, context):
        sc = context.scene
        lay = self.layout
        row = lay.row(align=True)
        row.prop(sc, "material_swatches_mode", text="")
        row.operator("material_swatches.new", text="", icon='ADD')
        row.operator("material_swatches.random_viewport_colors", text="", icon='COLOR')
        lay.prop(sc, "material_swatches_filter", text="", icon='VIEWZOOM')
        flt = sc.material_swatches_filter.lower()
        mats = [m for m in bpy.data.materials if not m.is_grease_pencil and flt in m.name.lower()]
        grid = lay.grid_flow(columns=sc.material_swatches_columns, even_columns=True, align=True)
        for m in sorted(mats, key=lambda m: m.name.lower()):
            col = grid.column(align=True)
            col.template_icon(icon_value=m.preview_ensure().icon_id if hasattr(m, "preview_ensure")
                              else bpy.types.UILayout.icon(m), scale=sc.material_swatches_size)
            r = col.row(align=True)
            r.operator("material_swatches.assign", text=m.name).name = m.name
            r.operator("material_swatches.select_users", text="", icon='RESTRICT_SELECT_OFF').name = m.name
        lay.prop(sc, "material_swatches_columns")
        lay.prop(sc, "material_swatches_size")


_classes = (MS_OT_assign, MS_OT_new, MS_OT_random_viewport, MS_OT_select_users, MS_PT_panel)
_props = ("material_swatches_mode", "material_swatches_filter", "material_swatches_columns", "material_swatches_size")


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.material_swatches_mode = bpy.props.EnumProperty(name="Assign Mode", items=[
        ('REPLACE', "Replace All Slots", "Object gets only this material"),
        ('ACTIVE', "Active Slot", "Replace only the active slot"),
        ('ADD', "Add Slot", "Add as a new slot")])
    S.material_swatches_filter = bpy.props.StringProperty(name="Filter", options={'TEXTEDIT_UPDATE'})
    S.material_swatches_columns = bpy.props.IntProperty(name="Columns", default=3, min=1, max=8)
    S.material_swatches_size = bpy.props.FloatProperty(name="Preview Size", default=3, min=1, max=10)


def unregister():
    for p in _props:
        delattr(bpy.types.Scene, p)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
