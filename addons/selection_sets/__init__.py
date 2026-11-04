"""Object Selection Sets: like the bone selection sets add-on, but for objects."""
import bpy


class SS_Ref(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object)


class SS_Set(bpy.types.PropertyGroup):
    objects: bpy.props.CollectionProperty(type=SS_Ref)

    def members(self):
        return [r.obj for r in self.objects if r.obj and r.obj.name in bpy.context.scene.objects]


def _active_set(context):
    sc = context.scene
    sets = sc.selection_sets
    return sets[sc.selection_sets_index] if 0 <= sc.selection_sets_index < len(sets) else None


def add_objects(s, objects):
    have = {r.obj for r in s.objects}
    for o in objects:
        if o not in have:
            s.objects.add().obj = o


def remove_objects(s, objects):
    drop = set(objects)
    for i in range(len(s.objects) - 1, -1, -1):
        if s.objects[i].obj in drop or s.objects[i].obj is None:
            s.objects.remove(i)


class SS_OT_new(bpy.types.Operator):
    bl_idname = "selection_sets.new"
    bl_label = "New Set from Selection"
    bl_description = "Store the selected objects as a named set"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="Set")

    def execute(self, context):
        sc = context.scene
        s = sc.selection_sets.add()
        s.name = self.name
        add_objects(s, context.selected_objects)
        sc.selection_sets_index = len(sc.selection_sets) - 1
        return {'FINISHED'}


class SS_OT_action(bpy.types.Operator):
    bl_idname = "selection_sets.action"
    bl_label = "Selection Set Action"
    bl_description = "Select, add to, remove from, hide or delete the selection set"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[
        ('SELECT', "Select", "Select the set (Shift-click in list to extend)"),
        ('SELECT_ONLY', "Select Only", "Deselect everything else"),
        ('DESELECT', "Deselect", ""), ('ADD', "Add Selected", ""), ('REMOVE', "Remove Selected", ""),
        ('HIDE', "Hide", ""), ('SHOW', "Show", ""), ('DELETE', "Delete Set", "")])
    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        sc = context.scene
        if self.index >= 0:
            sc.selection_sets_index = self.index
        s = _active_set(context)
        if s is None:
            return {'CANCELLED'}
        a = self.action
        if a == 'DELETE':
            sc.selection_sets.remove(sc.selection_sets_index)
            sc.selection_sets_index = max(0, sc.selection_sets_index - 1)
            return {'FINISHED'}
        if a == 'ADD':
            add_objects(s, context.selected_objects)
            return {'FINISHED'}
        if a == 'REMOVE':
            remove_objects(s, context.selected_objects)
            return {'FINISHED'}
        if a == 'SELECT_ONLY':
            for o in context.selected_objects:
                o.select_set(False)
        for o in s.members():
            if a in {'SELECT', 'SELECT_ONLY'}:
                if o.visible_get():
                    o.select_set(True)
            elif a == 'DESELECT':
                o.select_set(False)
            elif a == 'HIDE':
                o.hide_set(True)
            elif a == 'SHOW':
                o.hide_set(False)
        if a in {'SELECT', 'SELECT_ONLY'} and s.members():
            context.view_layer.objects.active = s.members()[0]
        return {'FINISHED'}


class SS_UL_sets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='GROUP')
        row.label(text=str(len(item.members())))
        op = row.operator("selection_sets.action", text="", icon='RESTRICT_SELECT_OFF')
        op.action, op.index = 'SELECT_ONLY', index


class SS_PT_panel(bpy.types.Panel):
    bl_label = "Selection Sets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Select"

    def draw(self, context):
        sc = context.scene
        row = self.layout.row()
        row.template_list("SS_UL_sets", "", sc, "selection_sets", sc, "selection_sets_index", rows=4)
        col = row.column(align=True)
        col.operator("selection_sets.new", text="", icon='ADD')
        col.operator("selection_sets.action", text="", icon='REMOVE').action = 'DELETE'
        grid = self.layout.grid_flow(columns=2, align=True)
        for a, label in (('SELECT', "Select"), ('DESELECT', "Deselect"), ('ADD', "Assign"),
                         ('REMOVE', "Remove"), ('HIDE', "Hide"), ('SHOW', "Show")):
            grid.operator("selection_sets.action", text=label).action = a


class SS_MT_menu(bpy.types.Menu):
    bl_idname = "SS_MT_menu"
    bl_label = "Selection Sets"

    def draw(self, context):
        lay = self.layout
        lay.operator("selection_sets.new", icon='ADD')
        for i, s in enumerate(context.scene.selection_sets):
            op = lay.operator("selection_sets.action", text=s.name, icon='GROUP')
            op.action, op.index = 'SELECT_ONLY', i


def select_menu(self, context):
    self.layout.menu("SS_MT_menu")


_classes = (SS_Ref, SS_Set, SS_OT_new, SS_OT_action, SS_UL_sets, SS_PT_panel, SS_MT_menu)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.selection_sets = bpy.props.CollectionProperty(type=SS_Set)
    bpy.types.Scene.selection_sets_index = bpy.props.IntProperty()
    bpy.types.VIEW3D_MT_select_object.append(select_menu)


def unregister():
    bpy.types.VIEW3D_MT_select_object.remove(select_menu)
    del bpy.types.Scene.selection_sets_index
    del bpy.types.Scene.selection_sets
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
