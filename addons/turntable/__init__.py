"""Turntable: frame the selection with a camera orbiting (or the model spinning) 360 degrees."""
import math

import bpy
from bpy_extras import anim_utils
from mathutils import Vector


def world_bounds(objects):
    pts = [ob.matrix_world @ Vector(c) for ob in objects for c in ob.bound_box]
    mn = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
    mx = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
    return mn, mx


def fit_distance(radius, cam_data, aspect):
    """Distance at which a sphere of `radius` fits the narrower camera field of view."""
    fov = cam_data.angle  # along the sensor fit axis (largest side when AUTO)
    narrow = fov if aspect <= 1 else 2 * math.atan(math.tan(fov / 2) / aspect)
    return radius / math.sin(narrow / 2)


def _linear(idb):
    ad = idb.animation_data
    if not ad or not ad.action:
        return
    cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in cb.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'


def build(context, frames=120, elevation=15.0, margin=1.1, mode='CAMERA', lens=50.0):
    sc = context.scene
    objs = [o for o in context.selected_objects if o.type not in {'CAMERA', 'LIGHT'}]
    if not objs:
        raise ValueError("Select the objects to show")
    mn, mx = world_bounds(objs)
    center = (mn + mx) / 2
    radius = (mx - mn).length / 2 * margin
    pivot = bpy.data.objects.new("Turntable", None)
    pivot.empty_display_type = 'CIRCLE'
    pivot.empty_display_size = radius
    pivot.location = center
    sc.collection.objects.link(pivot)
    context.view_layer.update()  # pivot.matrix_world must be valid before parenting to it
    cd = bpy.data.cameras.new("TurntableCam")
    cd.lens = lens
    cam = bpy.data.objects.new("TurntableCam", cd)
    sc.collection.objects.link(cam)
    aspect = sc.render.resolution_x / sc.render.resolution_y
    dist = fit_distance(radius, cd, aspect)
    el = math.radians(elevation)
    cam.parent = pivot
    cam.location = (0, -dist * math.cos(el), dist * math.sin(el))
    cam.rotation_euler = (math.pi / 2 - el, 0, 0)
    cd.clip_end = max(cd.clip_end, dist + radius * 4)
    sc.camera = cam
    spin = pivot
    if mode == 'OBJECTS':
        for o in objs:
            if o.parent is None or o.parent not in objs:
                mw = o.matrix_world.copy()
                o.parent = pivot
                o.matrix_world = mw
        cam.parent = None
        cam.location = center + Vector(cam.location)
        # Camera stays put; pivot (with objects) spins the opposite way so the result looks the same.
    start = sc.frame_start
    spin.rotation_euler.z = 0
    spin.keyframe_insert("rotation_euler", index=2, frame=start)
    # Last frame = full turn minus one step, so frame_end loops seamlessly back to frame_start.
    sign = -1 if mode == 'OBJECTS' else 1
    spin.rotation_euler.z = sign * 2 * math.pi
    spin.keyframe_insert("rotation_euler", index=2, frame=start + frames)
    _linear(spin)
    sc.frame_end = start + frames - 1
    return pivot, cam


class TT_OT_add(bpy.types.Operator):
    bl_idname = "turntable.add"
    bl_label = "Add Turntable"
    bl_description = "Frame the selected objects with a camera and animate a seamless 360 degree turn"
    bl_options = {'REGISTER', 'UNDO'}

    frames: bpy.props.IntProperty(name="Frames per Turn", default=120, min=8)
    elevation: bpy.props.FloatProperty(name="Elevation (deg)", default=15, min=-89, max=89)
    margin: bpy.props.FloatProperty(name="Margin", default=1.1, min=0.5, max=3)
    lens: bpy.props.FloatProperty(name="Lens (mm)", default=50, min=5)
    mode: bpy.props.EnumProperty(name="Spin", items=[
        ('CAMERA', "Camera Orbits", "Lighting stays fixed on the model"),
        ('OBJECTS', "Objects Spin", "Lighting/HDRI reflections move across the model")])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        try:
            build(context, self.frames, self.elevation, self.margin, self.mode, self.lens)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


def menu(self, context):
    self.layout.operator("turntable.add", icon='FILE_REFRESH')


def register():
    bpy.utils.register_class(TT_OT_add)
    bpy.types.VIEW3D_MT_camera_add.append(menu)


def unregister():
    bpy.types.VIEW3D_MT_camera_add.remove(menu)
    bpy.utils.unregister_class(TT_OT_add)
