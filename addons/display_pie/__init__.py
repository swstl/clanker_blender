"""Display Pie (Shift+Alt+D in Object Mode): display toggles for the selection + color management."""
import bpy

VIEW_TRANSFORMS = ("AgX", "Standard", "Khronos PBR Neutral", "Filmic", "False Color")


class DP_OT_toggle(bpy.types.Operator):
    bl_idname = "display_pie.toggle"
    bl_label = "Toggle Display"
    bl_description = "Toggle a display setting on all selected objects (follows the active object's state)"
    bl_options = {'REGISTER', 'UNDO'}

    prop: bpy.props.EnumProperty(items=[
        ('show_wire', "Wireframe Overlay", ""), ('show_in_front', "In Front", ""),
        ('show_name', "Name", ""), ('show_axis', "Axes", ""), ('show_bounds', "Bounds", ""),
        ('hide_render', "Hide in Render", ""), ('hide_select', "Unselectable", "")])

    def execute(self, context):
        obs = context.selected_objects
        if not obs:
            return {'CANCELLED'}
        ref = context.active_object if context.active_object in obs else obs[0]
        val = not getattr(ref, self.prop)
        for ob in obs:
            setattr(ob, self.prop, val)
        return {'FINISHED'}


class DP_OT_display_as(bpy.types.Operator):
    bl_idname = "display_pie.display_as"
    bl_label = "Display As"
    bl_description = "Set Display As on all selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    display: bpy.props.EnumProperty(items=[('BOUNDS', "Bounds", ""), ('WIRE', "Wire", ""),
                                           ('SOLID', "Solid", ""), ('TEXTURED', "Textured", "")])

    def execute(self, context):
        for ob in context.selected_objects:
            ob.display_type = self.display
        return {'FINISHED'}


def cycle_view_transform(scene, step=1):
    vs = scene.view_settings
    names = list(VIEW_TRANSFORMS)
    i = names.index(vs.view_transform) if vs.view_transform in names else -1
    for k in range(1, len(names) + 1):
        cand = names[(i + step * k) % len(names)]
        try:
            vs.view_transform = cand
            return cand
        except TypeError:
            continue  # not available in this OCIO config
    return vs.view_transform


class DP_OT_view_transform(bpy.types.Operator):
    bl_idname = "display_pie.view_transform"
    bl_label = "Cycle View Transform"
    bl_description = "Switch Color Management view transform (AgX, Standard, Khronos PBR Neutral, Filmic, False Color)"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(description="Set this transform; empty = next one")

    def execute(self, context):
        vs = context.scene.view_settings
        if self.name:
            try:
                vs.view_transform = self.name
            except TypeError:
                self.report({'WARNING'}, f"{self.name} not available")
                return {'CANCELLED'}
        else:
            cycle_view_transform(context.scene)
        self.report({'INFO'}, vs.view_transform)
        return {'FINISHED'}


class DP_OT_reset_exposure(bpy.types.Operator):
    bl_idname = "display_pie.reset_exposure"
    bl_label = "Reset Exposure/Gamma/Look"
    bl_description = "Exposure 0, gamma 1, look None"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        vs = context.scene.view_settings
        vs.exposure, vs.gamma = 0.0, 1.0
        try:
            vs.look = 'None'
        except TypeError:
            pass
        return {'FINISHED'}


def _t(layout, prop, text, icon):
    layout.operator("display_pie.toggle", text=text, icon=icon).prop = prop


class DP_MT_pie(bpy.types.Menu):
    bl_idname = "DP_MT_pie"
    bl_label = "Display"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # W, E, S, N, NW, NE, SW, SE
        col = pie.column(align=True)
        for d, icon in (('BOUNDS', 'SHADING_BBOX'), ('WIRE', 'SHADING_WIRE'),
                        ('SOLID', 'SHADING_SOLID'), ('TEXTURED', 'SHADING_TEXTURE')):
            col.operator("display_pie.display_as", text=d.title(), icon=icon).display = d
        col = pie.column(align=True)
        vs = context.scene.view_settings
        col.label(text=f"View: {vs.view_transform}")
        for name in VIEW_TRANSFORMS:
            col.operator("display_pie.view_transform", text=name).name = name
        col.prop(vs, "exposure")
        col.operator("display_pie.reset_exposure", icon='LOOP_BACK')
        _t(pie, 'show_wire', "Wireframe", 'MOD_WIREFRAME')
        _t(pie, 'show_in_front', "In Front", 'XRAY')
        _t(pie, 'show_name', "Name", 'SORTALPHA')
        _t(pie, 'hide_render', "Render Visibility", 'RESTRICT_RENDER_OFF')
        _t(pie, 'show_bounds', "Bounds", 'SHADING_BBOX')
        _t(pie, 'hide_select', "Selectable", 'RESTRICT_SELECT_OFF')


class DP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        self.layout.label(text="Pie: Shift+Alt+D in Object Mode (change in Keymap > Object Mode)")


_classes = (DP_OT_toggle, DP_OT_display_as, DP_OT_view_transform, DP_OT_reset_exposure, DP_MT_pie, DP_Prefs)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        kmi = km.keymap_items.new("wm.call_menu_pie", 'D', 'PRESS', shift=True, alt=True)
        kmi.properties.name = DP_MT_pie.bl_idname
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
