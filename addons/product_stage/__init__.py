"""Product Stage: cyclorama + 3-point area lights + camera, all sized to the selection.

Everything goes into a "ProductStage" collection so it can be rebuilt or removed in one click.
"""
import math

import bmesh
import bpy
from mathutils import Matrix, Vector

COLL = "ProductStage"


def bounds(objects):
    pts = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    mn = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
    mx = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
    return mn, mx


def cyclorama_mesh(width, depth, height, radius, segments=12):
    """Floor (y from -depth/2 to wall) curving up into a back wall. Origin at floor center."""
    bm = bmesh.new()
    prof = [(0, -depth / 2, 0), (0, depth / 2 - radius, 0)]
    cy, cz = depth / 2 - radius, radius
    for i in range(1, segments + 1):
        a = -math.pi / 2 + (math.pi / 2) * i / segments
        prof.append((0, cy + radius * math.cos(a), cz + radius * math.sin(a)))
    prof.append((0, depth / 2, height))
    left = [bm.verts.new((-width / 2, y, z)) for _, y, z in prof]
    right = [bm.verts.new((width / 2, y, z)) for _, y, z in prof]
    for i in range(len(prof) - 1):
        bm.faces.new((left[i], right[i], right[i + 1], left[i + 1]))
    me = bpy.data.meshes.new("Cyclorama")
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    return me


def _coll(scene):
    c = bpy.data.collections.get(COLL)
    if c is None:
        c = bpy.data.collections.new(COLL)
        scene.collection.children.link(c)
    return c


def remove_stage():
    c = bpy.data.collections.get(COLL)
    if not c:
        return 0
    n = 0
    for o in list(c.objects):
        data = o.data
        bpy.data.objects.remove(o)
        if data is not None and data.users == 0:
            for store in (bpy.data.meshes, bpy.data.lights, bpy.data.cameras):
                if data.name in store and store[data.name] == data:
                    store.remove(data)
                    break
        n += 1
    bpy.data.collections.remove(c)
    return n


def _look_at(ob, target):
    d = (target - ob.location).normalized()
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def build(context, objects, s):
    sc = context.scene
    remove_stage()
    coll = _coll(sc)
    mn, mx = bounds(objects)
    size = mx - mn
    r = max(size.length / 2, 0.01)
    center = (mn + mx) / 2
    floor_z = mn.z
    w = max(size.x, size.y) * s.backdrop_scale + r * 4
    me = cyclorama_mesh(w, w, w * 0.6, w * 0.15)
    back = bpy.data.objects.new("Cyclorama", me)
    back.location = (center.x, center.y, floor_z)
    coll.objects.link(back)
    mat = bpy.data.materials.get("StageBackdrop") or bpy.data.materials.new("StageBackdrop")
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (*s.backdrop_color, 1)
        bsdf.inputs["Roughness"].default_value = 0.6
    me.materials.append(mat)
    if s.shadow_catcher:
        back.is_shadow_catcher = True
    lights = []
    for name, ang, elev, power, size_f in (("Key", -45, 35, 1.0, 1.0), ("Fill", 50, 20, 0.35, 1.6),
                                            ("Rim", 160, 45, 0.7, 0.8)):
        ld = bpy.data.lights.new(f"Stage{name}", 'AREA')
        ld.shape = 'RECTANGLE'
        ld.size, ld.size_y = r * 1.5 * size_f, r * 1.0 * size_f
        dist = r * 4
        ld.energy = s.light_power * power * (dist ** 2)  # inverse-square: same look at any scale
        lo = bpy.data.objects.new(f"Stage{name}", ld)
        a, e = math.radians(ang), math.radians(elev)
        lo.location = center + Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * dist
        _look_at(lo, center)
        coll.objects.link(lo)
        lights.append(lo)
    cd = bpy.data.cameras.new("StageCam")
    cd.lens = s.lens
    cam = bpy.data.objects.new("StageCam", cd)
    coll.objects.link(cam)
    aspect = sc.render.resolution_x / sc.render.resolution_y
    fov = cd.angle
    narrow = fov if aspect <= 1 else 2 * math.atan(math.tan(fov / 2) / aspect)
    dist = r * s.margin / math.sin(narrow / 2)
    el = math.radians(s.camera_elevation)
    cam.location = center + Vector((0, -math.cos(el), math.sin(el))) * dist
    _look_at(cam, center)
    cd.clip_end = max(cd.clip_end, dist * 10)
    sc.camera = cam
    return back, lights, cam


class PS_Settings(bpy.types.PropertyGroup):
    backdrop_color: bpy.props.FloatVectorProperty(name="Backdrop", subtype='COLOR', size=3, min=0, max=1,
                                                  default=(0.8, 0.8, 0.8))
    backdrop_scale: bpy.props.FloatProperty(name="Backdrop Size", default=3.0, min=1.0)
    shadow_catcher: bpy.props.BoolProperty(name="Shadow Catcher (Cycles)", default=False)
    light_power: bpy.props.FloatProperty(name="Light Power", default=60.0, min=0,
                                         description="Key light energy per square unit of distance")
    lens: bpy.props.FloatProperty(name="Lens (mm)", default=85, min=5)
    margin: bpy.props.FloatProperty(name="Framing Margin", default=1.15, min=0.5, max=3)
    camera_elevation: bpy.props.FloatProperty(name="Camera Elevation", default=12, min=-80, max=80)


class PS_OT_build(bpy.types.Operator):
    bl_idname = "product_stage.build"
    bl_label = "Build Product Stage"
    bl_description = "Backdrop, 3 area lights and a framed camera around the selection (rebuilds if one exists)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type not in {'CAMERA', 'LIGHT'} for o in context.selected_objects)

    def execute(self, context):
        obs = [o for o in context.selected_objects if o.type not in {'CAMERA', 'LIGHT'}
               and COLL not in [c.name for c in o.users_collection]]
        if not obs:
            return {'CANCELLED'}
        build(context, obs, context.scene.product_stage)
        return {'FINISHED'}


class PS_OT_remove(bpy.types.Operator):
    bl_idname = "product_stage.remove"
    bl_label = "Remove Product Stage"
    bl_description = "Delete the stage backdrop, lights and camera"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"Removed {remove_stage()} objects")
        return {'FINISHED'}


class PS_PT_panel(bpy.types.Panel):
    bl_label = "Product Stage"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.product_stage
        col = self.layout.column()
        row = col.row(align=True)
        row.operator("product_stage.build", icon='SCENE')
        row.operator("product_stage.remove", text="", icon='X')
        for p in ("backdrop_color", "backdrop_scale", "shadow_catcher", "light_power", "lens", "margin",
                  "camera_elevation"):
            col.prop(s, p)


_classes = (PS_Settings, PS_OT_build, PS_OT_remove, PS_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.product_stage = bpy.props.PointerProperty(type=PS_Settings)


def unregister():
    del bpy.types.Scene.product_stage
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
