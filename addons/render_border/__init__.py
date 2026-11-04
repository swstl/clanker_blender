"""Render Border from Selection: crop renders to what matters (fast test renders, animated region)."""
import bpy
from bpy.app.handlers import persistent
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector


def object_points(ob, depsgraph):
    """World-space points to frame: evaluated mesh verts when cheap, else bounding box corners."""
    ev = ob.evaluated_get(depsgraph)
    mw = ev.matrix_world
    if ob.type == 'MESH' and len(ev.data.vertices) <= 20000:
        return [mw @ v.co for v in ev.data.vertices]
    return [mw @ Vector(c) for c in ev.bound_box]


def compute_border(scene, objects, padding=0.02, depsgraph=None):
    """(min_x, min_y, max_x, max_y) in 0..1 camera frame coords, or None if nothing is in front."""
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    cam = scene.camera
    xs, ys = [], []
    for ob in objects:
        for p in object_points(ob, depsgraph):
            co = world_to_camera_view(scene, cam, p)
            if co.z > 0:
                xs.append(co.x)
                ys.append(co.y)
    if not xs:
        return None
    clamp = lambda v: max(0.0, min(1.0, v))
    return (clamp(min(xs) - padding), clamp(min(ys) - padding),
            clamp(max(xs) + padding), clamp(max(ys) + padding))


def apply_border(scene, border):
    r = scene.render
    r.border_min_x, r.border_min_y, r.border_max_x, r.border_max_y = border
    r.use_border = True


class RB_OT_fit(bpy.types.Operator):
    bl_idname = "render_border.fit"
    bl_label = "Render Border from Selection"
    bl_description = "Set the render region to the selected objects as seen from the scene camera"
    bl_options = {'REGISTER', 'UNDO'}

    padding: bpy.props.FloatProperty(name="Padding", default=0.02, min=0, max=0.5, subtype='FACTOR')

    @classmethod
    def poll(cls, context):
        return context.scene.camera and context.selected_objects

    def execute(self, context):
        b = compute_border(context.scene, context.selected_objects, self.padding)
        if b is None:
            self.report({'WARNING'}, "Selection is behind the camera")
            return {'CANCELLED'}
        apply_border(context.scene, b)
        return {'FINISHED'}


@persistent
def follow(scene, depsgraph=None):
    s = scene.render_border_follow
    if not s.enabled or not scene.camera or not s.collection:
        return
    b = compute_border(scene, list(s.collection.all_objects), s.padding, depsgraph)
    if b:
        apply_border(scene, b)


class RB_Follow(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(name="Animated Border", description="Recompute the border every frame "
                                    "(also during rendering) for the objects in the collection")
    collection: bpy.props.PointerProperty(name="Objects", type=bpy.types.Collection)
    padding: bpy.props.FloatProperty(name="Padding", default=0.03, min=0, max=0.5, subtype='FACTOR')


class RB_OT_follow_selected(bpy.types.Operator):
    bl_idname = "render_border.follow_selected"
    bl_label = "Follow Selected While Animating"
    bl_description = "Put the selection in a 'RenderBorder' collection and recompute the border every frame"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = bpy.data.collections.get("RenderBorder") or bpy.data.collections.new("RenderBorder")
        for ob in list(coll.objects):
            coll.objects.unlink(ob)
        for ob in context.selected_objects:
            coll.objects.link(ob)  # extra link only, objects stay where they are
        s = context.scene.render_border_follow
        s.collection, s.enabled = coll, True
        # The follow handler edits render settings per frame, also during animation renders.
        context.scene.render.use_lock_interface = True
        follow(context.scene)
        return {'FINISHED'}


class RB_OT_clear(bpy.types.Operator):
    bl_idname = "render_border.clear"
    bl_label = "Clear Render Border"
    bl_description = "Disable the render region and animated following"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.render.use_border = False
        context.scene.render_border_follow.enabled = False
        return {'FINISHED'}


class RB_PT_panel(bpy.types.Panel):
    bl_label = "Render Border"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        sc = context.scene
        col = self.layout.column()
        row = col.row(align=True)
        row.operator("render_border.fit", icon='SELECT_SET')
        row.operator("render_border.clear", text="", icon='X')
        col.prop(sc.render, "use_crop_to_border")
        col.separator()
        col.operator("render_border.follow_selected", icon='ANIM')
        s = sc.render_border_follow
        col.prop(s, "enabled")
        col.prop(s, "collection")
        col.prop(s, "padding")


def view_menu(self, context):
    self.layout.operator("render_border.fit", icon='SELECT_SET')


_classes = (RB_Follow, RB_OT_fit, RB_OT_follow_selected, RB_OT_clear, RB_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.render_border_follow = bpy.props.PointerProperty(type=RB_Follow)
    bpy.types.VIEW3D_MT_view_regions.append(view_menu)
    bpy.app.handlers.frame_change_post.append(follow)


def unregister():
    if follow in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(follow)
    bpy.types.VIEW3D_MT_view_regions.remove(view_menu)
    del bpy.types.Scene.render_border_follow
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
