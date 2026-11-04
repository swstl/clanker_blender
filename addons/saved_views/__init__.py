"""Saved Views: viewport bookmarks stored in the .blend (Ctrl+Alt+Shift+1..9 save, Ctrl+Alt+1..9 recall)."""
import bpy


class SV_View(bpy.types.PropertyGroup):
    location: bpy.props.FloatVectorProperty(size=3)
    rotation: bpy.props.FloatVectorProperty(size=4)
    distance: bpy.props.FloatProperty()
    perspective: bpy.props.StringProperty()
    lens: bpy.props.FloatProperty()


def _r3d(context):
    sd = context.space_data
    return (sd, sd.region_3d) if sd and sd.type == 'VIEW_3D' else (None, None)


def store(view, sd, r3d):
    view.location = r3d.view_location
    view.rotation = r3d.view_rotation
    view.distance = r3d.view_distance
    view.perspective = r3d.view_perspective if r3d.view_perspective != 'CAMERA' else 'PERSP'
    view.lens = sd.lens


def restore(view, sd, r3d):
    r3d.view_perspective = view.perspective or 'PERSP'
    r3d.view_location = view.location
    r3d.view_rotation = view.rotation
    r3d.view_distance = view.distance
    sd.lens = view.lens


class SV_OT_save(bpy.types.Operator):
    bl_idname = "saved_views.save"
    bl_label = "Save View"
    bl_description = "Bookmark the current viewport angle (slot number, or a new entry)"
    bl_options = {'REGISTER', 'UNDO'}

    slot: bpy.props.IntProperty(default=0, description="0 = new entry, 1-9 = numbered slot")

    @classmethod
    def poll(cls, context):
        return _r3d(context)[1] is not None

    def execute(self, context):
        views = context.scene.saved_views
        name = f"View {self.slot}" if self.slot else f"View {len(views) + 1}"
        v = views.get(name) if self.slot else None
        if v is None:
            v = views.add()
            v.name = name
        store(v, *_r3d(context))
        context.scene.saved_views_index = list(views).index(v)
        self.report({'INFO'}, f"Saved {v.name}")
        return {'FINISHED'}


class SV_OT_recall(bpy.types.Operator):
    bl_idname = "saved_views.recall"
    bl_label = "Go to View"
    bl_description = "Jump to this bookmarked viewport angle"

    name: bpy.props.StringProperty(options={'SKIP_SAVE'})
    slot: bpy.props.IntProperty(default=0, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _r3d(context)[1] is not None

    def execute(self, context):
        name = f"View {self.slot}" if self.slot else self.name
        v = context.scene.saved_views.get(name)
        if v is None:
            self.report({'WARNING'}, f"No saved view '{name}' (save with Ctrl+Alt+Shift+number)")
            return {'CANCELLED'}
        restore(v, *_r3d(context))
        return {'FINISHED'}


class SV_OT_remove(bpy.types.Operator):
    bl_idname = "saved_views.remove"
    bl_label = "Remove View"
    bl_description = "Delete the selected bookmark"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        sc = context.scene
        if 0 <= sc.saved_views_index < len(sc.saved_views):
            sc.saved_views.remove(sc.saved_views_index)
            sc.saved_views_index = max(0, sc.saved_views_index - 1)
        return {'FINISHED'}


class SV_OT_to_camera(bpy.types.Operator):
    bl_idname = "saved_views.to_camera"
    bl_label = "Camera from View"
    bl_description = "Create a camera matching the selected bookmark"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        sc = context.scene
        if not (0 <= sc.saved_views_index < len(sc.saved_views)):
            return {'CANCELLED'}
        v = sc.saved_views[sc.saved_views_index]
        from mathutils import Quaternion, Vector
        rot = Quaternion(v.rotation)
        cd = bpy.data.cameras.new(v.name)
        cd.lens = v.lens
        ob = bpy.data.objects.new(v.name, cd)
        ob.rotation_mode = 'QUATERNION'
        ob.rotation_quaternion = rot
        ob.location = Vector(v.location) + rot @ Vector((0, 0, v.distance))
        context.collection.objects.link(ob)
        return {'FINISHED'}


class SV_UL_views(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon='HIDE_OFF')
        row.operator("saved_views.recall", text="", icon='PLAY').name = item.name


class SV_PT_panel(bpy.types.Panel):
    bl_label = "Saved Views"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context):
        sc = context.scene
        row = self.layout.row()
        row.template_list("SV_UL_views", "", sc, "saved_views", sc, "saved_views_index", rows=4)
        col = row.column(align=True)
        col.operator("saved_views.save", text="", icon='ADD').slot = 0
        col.operator("saved_views.remove", text="", icon='REMOVE')
        col.operator("saved_views.to_camera", text="", icon='OUTLINER_OB_CAMERA')
        self.layout.label(text="Ctrl+Alt+Shift+1-9 save, Ctrl+Alt+1-9 recall")


_classes = (SV_View, SV_OT_save, SV_OT_recall, SV_OT_remove, SV_OT_to_camera, SV_UL_views, SV_PT_panel)
_keymaps = []
_NUM = ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.saved_views = bpy.props.CollectionProperty(type=SV_View)
    bpy.types.Scene.saved_views_index = bpy.props.IntProperty()
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="3D View", space_type='VIEW_3D')
        for i, key in enumerate(_NUM, 1):
            kmi = km.keymap_items.new("saved_views.recall", key, 'PRESS', ctrl=True, alt=True)
            kmi.properties.slot = i
            _keymaps.append((km, kmi))
            kmi = km.keymap_items.new("saved_views.save", key, 'PRESS', ctrl=True, alt=True, shift=True)
            kmi.properties.slot = i
            _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    del bpy.types.Scene.saved_views_index
    del bpy.types.Scene.saved_views
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
