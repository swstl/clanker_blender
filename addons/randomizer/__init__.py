"""Randomizer: break up repetition on many selected objects (materials, colors, transforms, visibility)."""
import colorsys
import math
import random

import bpy


class RZ_Settings(bpy.types.PropertyGroup):
    seed: bpy.props.IntProperty(name="Seed", default=0)
    mat_filter: bpy.props.StringProperty(name="Material Name Contains", default="",
                                         description="Pick from materials whose name contains this (empty = "
                                                     "materials already used by the selection)")
    hue: bpy.props.FloatProperty(name="Hue Range", default=1.0, min=0, max=1)
    sat: bpy.props.FloatProperty(name="Saturation", default=0.6, min=0, max=1)
    val: bpy.props.FloatProperty(name="Value", default=0.8, min=0, max=1)
    scale_min: bpy.props.FloatProperty(name="Scale Min", default=0.8, min=0.001)
    scale_max: bpy.props.FloatProperty(name="Scale Max", default=1.2, min=0.001)
    rot_z: bpy.props.FloatProperty(name="Rotation Z (deg)", default=360, min=0, max=360)
    rot_xy: bpy.props.FloatProperty(name="Tilt X/Y (deg)", default=0, min=0, max=90)
    keep_ratio: bpy.props.FloatProperty(name="Keep Fraction", default=0.5, min=0, max=1, subtype='FACTOR',
                                        description="Fraction of selected objects kept selected by 'Thin Out'")


def _rng(s, salt):
    return random.Random(f"{s.seed}:{salt}")


def material_pool(context, s):
    if s.mat_filter:
        return [m for m in bpy.data.materials if s.mat_filter.lower() in m.name.lower() and not m.is_grease_pencil]
    pool = []
    for ob in context.selected_objects:
        for slot in getattr(ob, "material_slots", ()):
            if slot.material and slot.material not in pool:
                pool.append(slot.material)
    return pool


def random_materials(objects, pool, s):
    r = _rng(s, "mat")
    for ob in sorted(objects, key=lambda o: o.name):
        if not hasattr(ob.data, "materials"):
            continue
        mat = r.choice(pool)
        if ob.material_slots:
            ob.material_slots[ob.active_material_index].link = 'OBJECT'  # per-object, shared mesh stays intact
            ob.material_slots[ob.active_material_index].material = mat
        else:
            ob.data.materials.append(mat)


def random_colors(objects, s):
    """Sets Object Color (Object Info > Color in shaders, Solid mode 'Object' color)."""
    r = _rng(s, "col")
    base = r.random()
    for ob in sorted(objects, key=lambda o: o.name):
        h = (base + r.random() * s.hue) % 1.0
        ob.color = (*colorsys.hsv_to_rgb(h, s.sat, s.val), 1.0)


def random_transforms(objects, s, scale=True, rotate=True):
    r = _rng(s, "xf")
    for ob in sorted(objects, key=lambda o: o.name):
        if scale:
            f = r.uniform(s.scale_min, s.scale_max)
            ob.scale = tuple(v * f for v in ob.scale)
        if rotate:
            ob.rotation_mode = 'XYZ' if ob.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'} else ob.rotation_mode
            ob.rotation_euler.x += math.radians(r.uniform(-s.rot_xy, s.rot_xy))
            ob.rotation_euler.y += math.radians(r.uniform(-s.rot_xy, s.rot_xy))
            ob.rotation_euler.z += math.radians(r.uniform(0, s.rot_z))


class RZ_OT_apply(bpy.types.Operator):
    bl_idname = "randomizer.apply"
    bl_label = "Randomize"
    bl_description = "Randomize the chosen aspect of all selected objects (same seed = same result)"
    bl_options = {'REGISTER', 'UNDO'}

    what: bpy.props.EnumProperty(items=[
        ('MATERIAL', "Materials", "Pick a random material per object from the pool"),
        ('COLOR', "Object Colors", "Random Object Color (use Object Info > Color in shaders)"),
        ('SCALE', "Scale", ""), ('ROTATION', "Rotation", ""),
        ('THIN', "Thin Out Selection", "Randomly deselect objects, keeping a fraction")])

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def execute(self, context):
        s = context.scene.randomizer
        obs = list(context.selected_objects)
        if self.what == 'MATERIAL':
            pool = material_pool(context, s)
            if not pool:
                self.report({'WARNING'}, "No materials to pick from")
                return {'CANCELLED'}
            random_materials(obs, pool, s)
        elif self.what == 'COLOR':
            random_colors(obs, s)
        elif self.what == 'SCALE':
            random_transforms(obs, s, rotate=False)
        elif self.what == 'ROTATION':
            random_transforms(obs, s, scale=False)
        else:
            r = _rng(s, "thin")
            for ob in sorted(obs, key=lambda o: o.name):
                if r.random() > s.keep_ratio:
                    ob.select_set(False)
        return {'FINISHED'}


class RZ_PT_panel(bpy.types.Panel):
    bl_label = "Randomizer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.randomizer
        col = self.layout.column(align=True)
        col.prop(s, "seed")
        col.separator()
        col.prop(s, "mat_filter", text="", icon='MATERIAL')
        col.operator("randomizer.apply", text="Random Materials", icon='MATERIAL').what = 'MATERIAL'
        col.separator()
        row = col.row(align=True)
        row.prop(s, "hue")
        row.prop(s, "sat")
        row.prop(s, "val")
        col.operator("randomizer.apply", text="Random Object Colors", icon='COLOR').what = 'COLOR'
        col.separator()
        row = col.row(align=True)
        row.prop(s, "scale_min")
        row.prop(s, "scale_max")
        col.operator("randomizer.apply", text="Random Scale", icon='FULLSCREEN_ENTER').what = 'SCALE'
        row = col.row(align=True)
        row.prop(s, "rot_z")
        row.prop(s, "rot_xy")
        col.operator("randomizer.apply", text="Random Rotation", icon='DRIVER_ROTATIONAL_DIFFERENCE').what = 'ROTATION'
        col.separator()
        col.prop(s, "keep_ratio")
        col.operator("randomizer.apply", text="Thin Out Selection", icon='STICKY_UVS_DISABLE').what = 'THIN'


_classes = (RZ_Settings, RZ_OT_apply, RZ_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.randomizer = bpy.props.PointerProperty(type=RZ_Settings)


def unregister():
    del bpy.types.Scene.randomizer
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
