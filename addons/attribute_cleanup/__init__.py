"""Attribute Cleanup: imported/GN leftovers (black FBX vertex colors, stray attributes) in bulk."""
import fnmatch

import bpy

# Attributes Blender itself relies on (never offered for removal even if not marked required).
PROTECTED = {"position", "sharp_face", "sharp_edge", "material_index", "crease_edge", "crease_vert",
             "bevel_weight_edge", "bevel_weight_vert", "UVMap"}
BYTES = {'FLOAT': 4, 'INT': 4, 'FLOAT_VECTOR': 12, 'FLOAT_COLOR': 16, 'BYTE_COLOR': 4, 'STRING': 8,
         'BOOLEAN': 1, 'FLOAT2': 8, 'INT8': 1, 'INT16_2D': 4, 'INT32_2D': 8, 'QUATERNION': 16,
         'FLOAT4X4': 64}


def user_attributes(me, include_uv=False):
    """Attributes a user may want to manage (no internal/required/built-in ones)."""
    uv = {u.name for u in me.uv_layers}
    out = []
    for a in me.attributes:
        if a.is_internal or a.is_required or a.name.startswith("."):
            continue
        if a.name in PROTECTED or (a.name in uv and not include_uv):
            continue
        out.append(a)
    return out


def attr_size(me, a):
    count = {'POINT': len(me.vertices), 'EDGE': len(me.edges), 'FACE': len(me.polygons),
             'CORNER': len(me.loops)}.get(a.domain, 0)
    return count * BYTES.get(a.data_type, 4)


def _meshes(context, scope):
    obs = context.selected_objects if scope == 'SELECTED' else bpy.data.objects
    seen, out = set(), []
    for ob in obs:
        if ob.type == 'MESH' and ob.data not in seen and not ob.data.library:
            seen.add(ob.data)
            out.append(ob.data)
    return out


def remove_matching(meshes, pattern, kinds):
    """Remove attributes whose name matches the glob `pattern` and whose kind is in `kinds`
    ('COLOR' = color attributes, 'GENERIC' = everything else). Returns count."""
    n = 0
    for me in meshes:
        colors = {c.name for c in me.color_attributes}
        for a in list(user_attributes(me)):
            kind = 'COLOR' if a.name in colors else 'GENERIC'
            if kind in kinds and fnmatch.fnmatchcase(a.name.lower(), pattern.lower()):
                me.attributes.remove(a)
                n += 1
    return n


class AC_OT_remove(bpy.types.Operator):
    bl_idname = "attribute_cleanup.remove"
    bl_label = "Remove Attributes"
    bl_description = "Remove matching attributes on selected (or all) meshes. Required/internal ones are never touched"
    bl_options = {'REGISTER', 'UNDO'}

    pattern: bpy.props.StringProperty(name="Name Pattern", default="*", options={'SKIP_SAVE'},
                                      description="Glob, e.g. * or Col* or *_tmp")
    colors: bpy.props.BoolProperty(name="Color Attributes", default=True)
    generic: bpy.props.BoolProperty(name="Other Attributes", default=False)
    scope: bpy.props.EnumProperty(name="Meshes", items=[('SELECTED', "Selected", ""), ('ALL', "All", "")])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        kinds = {k for k, on in (('COLOR', self.colors), ('GENERIC', self.generic)) if on}
        n = remove_matching(_meshes(context, self.scope), self.pattern, kinds)
        self.report({'INFO'}, f"Removed {n} attributes")
        return {'FINISHED'}


class AC_OT_rename(bpy.types.Operator):
    bl_idname = "attribute_cleanup.rename"
    bl_label = "Rename Attribute"
    bl_description = "Rename an attribute on all selected meshes that have it (e.g. to match a shader's Attribute node)"
    bl_options = {'REGISTER', 'UNDO'}

    old: bpy.props.StringProperty(name="From", options={'SKIP_SAVE'})
    new: bpy.props.StringProperty(name="To", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = 0
        for me in _meshes(context, 'SELECTED'):
            a = me.attributes.get(self.old)
            if a and self.new and self.new not in me.attributes:
                a.name = self.new
                n += 1
        self.report({'INFO'}, f"Renamed on {n} meshes")
        return {'FINISHED'}


class AC_OT_select_with(bpy.types.Operator):
    bl_idname = "attribute_cleanup.select_with"
    bl_label = "Select Objects With Attribute"
    bl_description = "Select every visible mesh object that has an attribute with this name"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        if self.name:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = 0
        for ob in context.view_layer.objects:
            hit = ob.type == 'MESH' and self.name in ob.data.attributes and ob.visible_get()
            ob.select_set(hit)
            n += hit
        self.report({'INFO'}, f"{n} objects")
        return {'FINISHED'}


class AC_OT_set_color(bpy.types.Operator):
    bl_idname = "attribute_cleanup.set_color"
    bl_label = "Set Active/Render Color"
    bl_description = "Make this color attribute active and the render default on all selected meshes that have it"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        n = 0
        for me in _meshes(context, 'SELECTED'):
            ca = me.color_attributes
            idx = next((i for i, c in enumerate(ca) if c.name == self.name), -1)
            if idx >= 0:
                ca.active_color_index = idx
                ca.render_color_index = idx
                n += 1
        self.report({'INFO'}, f"{n} meshes")
        return {'FINISHED'}


class AC_OT_remove_one(bpy.types.Operator):
    bl_idname = "attribute_cleanup.remove_one"
    bl_label = "Remove This Attribute"
    bl_description = "Remove this attribute from all selected meshes"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        n = 0
        for me in _meshes(context, 'SELECTED'):
            a = me.attributes.get(self.name)
            if a and a in user_attributes(me, include_uv=False):
                me.attributes.remove(a)
                n += 1
        self.report({'INFO'}, f"Removed from {n} meshes")
        return {'FINISHED'}


class AC_PT_panel(bpy.types.Panel):
    bl_label = "Attribute Cleanup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Cleanup"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        me = context.active_object.data
        col = self.layout.column(align=True)
        colors = {c.name for c in me.color_attributes}
        attrs = user_attributes(me)
        total = sum(attr_size(me, a) for a in attrs)
        col.label(text=f"{len(attrs)} attributes, {total / 1024:.0f} KB")
        for a in attrs:
            row = col.row(align=True)
            row.label(text=a.name, icon='GROUP_VCOL' if a.name in colors else 'SPREADSHEET')
            row.label(text=f"{a.domain.title()} {a.data_type.title()}")
            if a.name in colors:
                row.operator("attribute_cleanup.set_color", text="", icon='RESTRICT_RENDER_OFF').name = a.name
            row.operator("attribute_cleanup.select_with", text="", icon='RESTRICT_SELECT_OFF').name = a.name
            row.operator("attribute_cleanup.remove_one", text="", icon='X').name = a.name
        col.separator()
        col.operator("attribute_cleanup.remove", icon='TRASH')
        col.operator("attribute_cleanup.rename", icon='SORTALPHA')


_classes = (AC_OT_remove, AC_OT_rename, AC_OT_select_with, AC_OT_set_color, AC_OT_remove_one, AC_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
