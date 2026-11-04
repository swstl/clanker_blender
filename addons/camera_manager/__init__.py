"""Camera Manager: one panel for all cameras, per-camera resolution, marker binding."""
import bpy
from bpy.app.handlers import persistent


def apply_camera(scene, cam_ob):
    """Make cam_ob the scene camera and apply its resolution override."""
    scene.camera = cam_ob
    cd = cam_ob.data
    if cd.cm_use_resolution:
        scene.render.resolution_x = cd.cm_resolution_x
        scene.render.resolution_y = cd.cm_resolution_y


_last_cam = {}


@persistent
def on_frame(scene, *_):
    # Marker-bound cameras switch during playback: keep their resolution in sync.
    cam = scene.camera
    if cam and cam.type == 'CAMERA' and _last_cam.get(scene.name) != cam.name:
        _last_cam[scene.name] = cam.name
        if cam.data.cm_use_resolution:
            apply_camera(scene, cam)


def _cameras(context):
    return sorted((o for o in context.scene.objects if o.type == 'CAMERA'), key=lambda o: o.name.lower())


class CM_OT_activate(bpy.types.Operator):
    bl_idname = "camera_manager.activate"
    bl_label = "Set Active Camera"
    bl_description = "Make this the scene camera (applies its resolution) and look through it"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()
    look_through: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if not ob or ob.type != 'CAMERA':
            return {'CANCELLED'}
        apply_camera(context.scene, ob)
        if self.look_through and context.area and context.area.type == 'VIEW_3D':
            context.space_data.region_3d.view_perspective = 'CAMERA'
        return {'FINISHED'}


class CM_OT_cycle(bpy.types.Operator):
    bl_idname = "camera_manager.cycle"
    bl_label = "Next Camera"
    bl_description = "Switch to the next (or previous) camera in the scene"
    bl_options = {'REGISTER', 'UNDO'}

    step: bpy.props.IntProperty(default=1)

    def execute(self, context):
        cams = _cameras(context)
        if not cams:
            return {'CANCELLED'}
        i = cams.index(context.scene.camera) if context.scene.camera in cams else -1
        apply_camera(context.scene, cams[(i + self.step) % len(cams)])
        return {'FINISHED'}


class CM_OT_bind_marker(bpy.types.Operator):
    bl_idname = "camera_manager.bind_marker"
    bl_label = "Bind to Current Frame"
    bl_description = "Add a timeline marker at the current frame that switches to this camera"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        sc = context.scene
        ob = bpy.data.objects.get(self.name)
        if not ob:
            return {'CANCELLED'}
        mk = next((m for m in sc.timeline_markers if m.frame == sc.frame_current), None)
        if mk is None:
            mk = sc.timeline_markers.new(ob.name, frame=sc.frame_current)
        mk.camera = ob
        mk.name = ob.name
        return {'FINISHED'}


class CM_OT_add_from_view(bpy.types.Operator):
    bl_idname = "camera_manager.add_from_view"
    bl_label = "New Camera from View"
    bl_description = "Add a camera matching the current viewport view and make it active"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def execute(self, context):
        cd = bpy.data.cameras.new("Camera")
        ob = bpy.data.objects.new("Camera", cd)
        context.collection.objects.link(ob)
        r3d = context.space_data.region_3d
        ob.matrix_world = r3d.view_matrix.inverted()
        cd.lens = context.space_data.lens
        apply_camera(context.scene, ob)
        r3d.view_perspective = 'CAMERA'
        return {'FINISHED'}


class CM_OT_select(bpy.types.Operator):
    bl_idname = "camera_manager.select"
    bl_label = "Select Camera"
    bl_description = "Select this camera object"

    name: bpy.props.StringProperty()

    def execute(self, context):
        ob = context.scene.objects.get(self.name)
        if not ob:
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


class CM_PT_panel(bpy.types.Panel):
    bl_label = "Cameras"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context):
        sc = context.scene
        col = self.layout.column(align=True)
        row = col.row(align=True)
        row.operator("camera_manager.add_from_view", icon='ADD')
        row.operator("camera_manager.cycle", text="", icon='TRIA_LEFT').step = -1
        row.operator("camera_manager.cycle", text="", icon='TRIA_RIGHT').step = 1
        col.separator()
        for ob in _cameras(context):
            active = ob == sc.camera
            box = col.box() if active else col
            row = box.row(align=True)
            row.operator("camera_manager.activate", text="", icon='CHECKBOX_HLT' if active else 'CHECKBOX_DEHLT',
                         emboss=False).name = ob.name
            row.operator("camera_manager.select", text=ob.name, emboss=False).name = ob.name
            row.operator("camera_manager.bind_marker", text="", icon='MARKER_HLT').name = ob.name
            if active:
                cd = ob.data
                box.prop(cd, "lens")
                box.prop(cd, "passepartout_alpha", text="Passepartout")
                r = box.row(align=True)
                r.prop(cd, "cm_use_resolution", text="Own Resolution")
                sub = r.row(align=True)
                sub.active = cd.cm_use_resolution
                sub.prop(cd, "cm_resolution_x", text="X")
                sub.prop(cd, "cm_resolution_y", text="Y")
                box.prop(cd.dof, "use_dof", text="Depth of Field")
                if cd.dof.use_dof:
                    box.prop(cd.dof, "focus_object")
                    box.prop(cd.dof, "aperture_fstop")


def _res_update(self, context):
    sc = context.scene
    if sc.camera and sc.camera.data == self and self.cm_use_resolution:
        apply_camera(sc, sc.camera)


_classes = (CM_OT_activate, CM_OT_cycle, CM_OT_bind_marker, CM_OT_add_from_view, CM_OT_select, CM_PT_panel)


def register():
    C = bpy.types.Camera
    C.cm_use_resolution = bpy.props.BoolProperty(name="Own Resolution", update=_res_update,
                                                 description="Switch render resolution when this camera becomes active")
    C.cm_resolution_x = bpy.props.IntProperty(name="Resolution X", default=1920, min=4, update=_res_update)
    C.cm_resolution_y = bpy.props.IntProperty(name="Resolution Y", default=1080, min=4, update=_res_update)
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.app.handlers.frame_change_post.append(on_frame)


def unregister():
    if on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(on_frame)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    for p in ("cm_use_resolution", "cm_resolution_x", "cm_resolution_y"):
        delattr(bpy.types.Camera, p)
