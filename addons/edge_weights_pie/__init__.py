"""Edge Weights Pie (Ctrl+Alt+E in Edit Mesh): exact bevel weight / crease values in one click."""
import bmesh
import bpy

ATTRS = {'BEVEL': "bevel_weight_edge", 'CREASE': "crease_edge"}


def set_edge_value(me, kind, value):
    """Set bevel/crease attribute on selected edges of an edit mesh. Returns edge count."""
    bm = bmesh.from_edit_mesh(me)
    name = ATTRS[kind]
    layer = bm.edges.layers.float.get(name) or bm.edges.layers.float.new(name)
    n = 0
    for e in bm.edges:
        if e.select:
            e[layer] = value
            n += 1
    bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
    return n


def set_edge_flag(me, flag, value):
    bm = bmesh.from_edit_mesh(me)
    n = 0
    for e in bm.edges:
        if e.select:
            if flag == 'SHARP':
                e.smooth = not value
            else:
                e.seam = value
            n += 1
    bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
    return n


class EWP_OT_value(bpy.types.Operator):
    bl_idname = "edge_weights_pie.value"
    bl_label = "Set Edge Weight"
    bl_description = "Set bevel weight or crease of the selected edges"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(items=[('BEVEL', "Bevel Weight", ""), ('CREASE', "Crease", "")])
    value: bpy.props.FloatProperty(name="Value", min=0, max=1, default=1)

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        n = 0
        for ob in context.objects_in_mode_unique_data:
            n += set_edge_value(ob.data, self.kind, self.value)
        self.report({'INFO'}, f"{self.kind.title()} {self.value:g} on {n} edges")
        return {'FINISHED'}


class EWP_OT_flag(bpy.types.Operator):
    bl_idname = "edge_weights_pie.flag"
    bl_label = "Set Edge Flag"
    bl_description = "Mark or clear sharp / seam on the selected edges"
    bl_options = {'REGISTER', 'UNDO'}

    flag: bpy.props.EnumProperty(items=[('SHARP', "Sharp", ""), ('SEAM', "Seam", "")])
    value: bpy.props.BoolProperty(default=True)

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        for ob in context.objects_in_mode_unique_data:
            set_edge_flag(ob.data, self.flag, self.value)
        return {'FINISHED'}


class EWP_OT_bevel_sharp(bpy.types.Operator):
    bl_idname = "edge_weights_pie.bevel_and_sharp"
    bl_label = "Bevel 1 + Sharp"
    bl_description = "Hard-surface combo: bevel weight 1 and mark sharp"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        for ob in context.objects_in_mode_unique_data:
            set_edge_value(ob.data, 'BEVEL', 1.0)
            set_edge_flag(ob.data, 'SHARP', True)
        return {'FINISHED'}


def _val(layout, kind, v, text=None):
    op = layout.operator("edge_weights_pie.value", text=text or f"{kind.title()} {v:g}")
    op.kind, op.value = kind, v


class EWP_MT_pie(bpy.types.Menu):
    bl_idname = "EWP_MT_pie"
    bl_label = "Edge Weights"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # Order: W, E, S, N, NW, NE, SW, SE
        col = pie.column(align=True)
        col.label(text="Bevel Weight")
        for v in (0.25, 0.5, 0.75):
            _val(col, 'BEVEL', v, f"{v:g}")
        col = pie.column(align=True)
        col.label(text="Crease")
        for v in (0.25, 0.5, 0.75):
            _val(col, 'CREASE', v, f"{v:g}")
        _val(pie, 'BEVEL', 0.0, "Clear Bevel")
        _val(pie, 'BEVEL', 1.0, "Bevel 1")
        col = pie.column(align=True)
        for flag, icon in (('SHARP', 'SHARPCURVE'), ('SEAM', 'UV')):
            row = col.row(align=True)
            op = row.operator("edge_weights_pie.flag", text=f"Mark {flag.title()}", icon=icon)
            op.flag, op.value = flag, True
            op = row.operator("edge_weights_pie.flag", text="", icon='X')
            op.flag, op.value = flag, False
        _val(pie, 'CREASE', 1.0, "Crease 1")
        _val(pie, 'CREASE', 0.0, "Clear Crease")
        pie.operator("edge_weights_pie.bevel_and_sharp", icon='MOD_BEVEL')


class EWP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        self.layout.label(text="Pie: Ctrl+Alt+E in Edit Mode (change it in Keymap > Mesh)")


_classes = (EWP_OT_value, EWP_OT_flag, EWP_OT_bevel_sharp, EWP_MT_pie, EWP_Prefs)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
        kmi = km.keymap_items.new("wm.call_menu_pie", 'E', 'PRESS', ctrl=True, alt=True)
        kmi.properties.name = EWP_MT_pie.bl_idname
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
