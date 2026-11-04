"""Custom Property Manager: custom properties on many objects / pose bones at once."""
import bpy
from rna_prop_ui import rna_idprop_ui_create


def targets(context, where):
    if where == 'BONES':
        return list(context.selected_pose_bones or [])
    if where == 'DATA':
        return [o.data for o in context.selected_objects if o.data is not None]
    return list(context.selected_objects)


def all_owners():
    """Everything in the file that commonly carries custom props (objects, data, pose bones, scenes)."""
    for ob in bpy.data.objects:
        yield ob
        if ob.data is not None:
            yield ob.data
        if ob.pose:
            yield from ob.pose.bones
    yield from bpy.data.scenes
    yield from bpy.data.materials


def _value(s):
    return {'FLOAT': s.float_value, 'INT': s.int_value, 'BOOL': s.bool_value, 'STRING': s.string_value}[s.ptype]


def add_prop(owners, s):
    n = 0
    for o in owners:
        if s.name in o.keys() and not s.overwrite:
            continue
        kw = {"default": _value(s), "description": s.description, "overridable": s.overridable}
        if s.ptype in {'FLOAT', 'INT'}:
            kw.update(min=s.min, max=s.max, soft_min=s.min, soft_max=s.max)
        rna_idprop_ui_create(o, s.name, **kw)
        n += 1
    return n


def rename_prop(owners, old, new):
    n = 0
    for o in owners:
        if old in o.keys() and new not in o.keys():
            ui = o.id_properties_ui(old).as_dict() if hasattr(o, "id_properties_ui") else {}
            o[new] = o[old]
            if ui:
                try:
                    o.id_properties_ui(new).update(**{k: v for k, v in ui.items() if k != "subtype"})
                except TypeError:
                    pass
            del o[old]
            n += 1
    return n


def remove_prop(owners, name):
    n = 0
    for o in owners:
        if name in o.keys():
            del o[name]
            n += 1
    return n


class CP_Settings(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="prop")
    ptype: bpy.props.EnumProperty(name="Type", items=[('FLOAT', "Float", ""), ('INT', "Integer", ""),
                                                      ('BOOL', "Boolean", ""), ('STRING', "String", "")])
    float_value: bpy.props.FloatProperty(name="Default")
    int_value: bpy.props.IntProperty(name="Default")
    bool_value: bpy.props.BoolProperty(name="Default")
    string_value: bpy.props.StringProperty(name="Default")
    min: bpy.props.FloatProperty(name="Min", default=0)
    max: bpy.props.FloatProperty(name="Max", default=1)
    description: bpy.props.StringProperty(name="Tooltip")
    overridable: bpy.props.BoolProperty(name="Library Overridable", default=True)
    overwrite: bpy.props.BoolProperty(name="Overwrite Existing", default=False)
    where: bpy.props.EnumProperty(name="On", items=[('OBJECTS', "Selected Objects", ""),
                                                    ('DATA', "Their Data", "Mesh/armature/... data"),
                                                    ('BONES', "Selected Pose Bones", "")])
    new_name: bpy.props.StringProperty(name="New Name")


class CP_OT_add(bpy.types.Operator):
    bl_idname = "custom_props.add"
    bl_label = "Add to Selection"
    bl_description = "Add the custom property (with min/max/tooltip) to every selected object or pose bone"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.window_manager.custom_props
        n = add_prop(targets(context, s.where), s)
        self.report({'INFO'}, f"Added '{s.name}' to {n}")
        return {'FINISHED'}


class CP_OT_rename(bpy.types.Operator):
    bl_idname = "custom_props.rename"
    bl_label = "Rename Everywhere"
    bl_description = "Rename the property on every object, data, pose bone, scene and material in the file"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.window_manager.custom_props
        n = rename_prop(all_owners(), s.name, s.new_name)
        self.report({'INFO'}, f"Renamed on {n}")
        return {'FINISHED'}


class CP_OT_remove(bpy.types.Operator):
    bl_idname = "custom_props.remove"
    bl_label = "Remove"
    bl_description = "Remove the property from the selection, or from the whole file"
    bl_options = {'REGISTER', 'UNDO'}

    everywhere: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        s = context.window_manager.custom_props
        owners = all_owners() if self.everywhere else targets(context, s.where)
        self.report({'INFO'}, f"Removed from {remove_prop(owners, s.name)}")
        return {'FINISHED'}


class CP_OT_select(bpy.types.Operator):
    bl_idname = "custom_props.select"
    bl_label = "Select Objects Having It"
    bl_description = "Select visible objects (or their data) that have this property, optionally equal to the default value"
    bl_options = {'REGISTER', 'UNDO'}

    match_value: bpy.props.BoolProperty(name="Match Value", options={'SKIP_SAVE'})

    def execute(self, context):
        s = context.window_manager.custom_props
        n = 0
        for ob in context.view_layer.objects:
            hit = False
            for o in (ob, ob.data):
                if o is not None and s.name in o.keys():
                    hit = (not self.match_value) or o[s.name] == _value(s)
                    if hit:
                        break
            ob.select_set(hit and ob.visible_get())
            n += hit
        self.report({'INFO'}, f"{n} objects")
        return {'FINISHED'}


class CP_PT_panel(bpy.types.Panel):
    bl_label = "Custom Properties"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.window_manager.custom_props
        col = self.layout.column()
        col.prop(s, "where")
        col.prop(s, "name")
        col.prop(s, "ptype")
        col.prop(s, {'FLOAT': "float_value", 'INT': "int_value", 'BOOL': "bool_value",
                     'STRING': "string_value"}[s.ptype])
        if s.ptype in {'FLOAT', 'INT'}:
            row = col.row(align=True)
            row.prop(s, "min")
            row.prop(s, "max")
        col.prop(s, "description")
        col.prop(s, "overridable")
        col.prop(s, "overwrite")
        col.operator("custom_props.add", icon='ADD')
        row = col.row(align=True)
        row.operator("custom_props.select", icon='RESTRICT_SELECT_OFF')
        row.operator("custom_props.select", text="", icon='EYEDROPPER').match_value = True
        row = col.row(align=True)
        row.operator("custom_props.remove", icon='REMOVE')
        row.operator("custom_props.remove", text="Everywhere").everywhere = True
        row = col.row(align=True)
        row.prop(s, "new_name", text="")
        row.operator("custom_props.rename", icon='SORTALPHA')


_classes = (CP_Settings, CP_OT_add, CP_OT_rename, CP_OT_remove, CP_OT_select, CP_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.custom_props = bpy.props.PointerProperty(type=CP_Settings)


def unregister():
    del bpy.types.WindowManager.custom_props
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
