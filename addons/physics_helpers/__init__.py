"""Physics Helpers: rigid-body drop/settle, raycast drop to surface, quick cloth setup and apply."""
import random

import bpy
from mathutils import Matrix, Quaternion, Vector

DOWN = Vector((0, 0, -1))
UP = Vector((0, 0, 1))
MASS = {'FEATHER': 0.05, 'LIGHT': 0.5, 'MEDIUM': 1.0, 'HEAVY': 10.0}
# Values from Blender's own presets/cloth/*.py
CLOTH = {
    'COTTON': (5, 0.3, 15, 15, 15, 0.5, 5),
    'SILK': (5, 0.15, 5, 5, 5, 0.05, 0),
    'LEATHER': (15, 0.4, 80, 80, 80, 150, 25),
    'DENIM': (12, 1.0, 40, 40, 40, 10, 25),
    'RUBBER': (7, 3.0, 15, 15, 15, 25, 25),
}


def _corners(ob):
    return [ob.matrix_world @ Vector(c) for c in ob.bound_box]


def _ray_down(scene, origin, accept):
    """First hit straight down from origin whose object passes accept(ob): (location, normal, ob) or None."""
    dg = bpy.context.evaluated_depsgraph_get()
    o = Vector(origin)
    for _ in range(100):
        hit, loc, nor, _i, ob, _m = scene.ray_cast(dg, o, DOWN)
        if not hit:
            return None
        if accept(ob):
            return loc, nor, ob
        o = loc + DOWN * 1e-4
    return None


def _rb_call(op, ob, **kw):
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        op(**kw)


def _ground_plane(context, sel):
    pts = [p for o in sel for p in _corners(o)]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), 0))
    c, s = (lo + hi) / 2, max(hi.x - lo.x, hi.y - lo.y) + 100
    me = bpy.data.meshes.new("PH_Ground")
    me.from_pydata([(c.x + x * s, c.y + y * s, lo.z) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))], [], [(0, 1, 2, 3)])
    ob = bpy.data.objects.new("PH_Ground", me)
    context.scene.collection.objects.link(ob)
    context.view_layer.update()
    return ob


GROUND_ITEMS = [('VISIBLE', "Visible Meshes", "All other visible meshes collide"),
                ('BELOW', "Objects Below", "Only meshes directly under the selection collide"),
                ('COLLECTION', "Collection", "Only meshes in the chosen collection collide")]


class PH_OT_drop(bpy.types.Operator):
    bl_idname = "physics_helpers.drop"
    bl_label = "Drop / Settle"
    bl_description = ("Simulate the selected meshes as rigid bodies falling onto the scene, then freeze "
                      "their final pose and remove the temporary physics setup")
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(name="Shape", default='CONVEX_HULL', items=[
        ('CONVEX_HULL', "Convex Hull", ""), ('MESH', "Mesh", ""), ('BOX', "Box", "")])
    friction: bpy.props.FloatProperty(name="Friction", default=0.5, min=0, max=1)
    bounciness: bpy.props.FloatProperty(name="Bounciness", default=0.0, min=0, max=1)
    mass_preset: bpy.props.EnumProperty(name="Mass", default='MEDIUM', items=[
        ('FEATHER', "Feather (0.05 kg)", ""), ('LIGHT', "Light (0.5 kg)", ""), ('MEDIUM', "Medium (1 kg)", ""),
        ('HEAVY', "Heavy (10 kg)", ""), ('CUSTOM', "Custom", "")])
    mass: bpy.props.FloatProperty(name="Custom Mass", default=1.0, min=0.001, unit='MASS')
    ground: bpy.props.EnumProperty(name="Colliders", default='VISIBLE', items=GROUND_ITEMS)
    collection: bpy.props.StringProperty(name="Collection", description="Collider collection name")
    add_ground: bpy.props.BoolProperty(name="Temp Ground Plane", default=True,
                                       description="Add a temporary floor at the lowest point if nothing is below")
    frames: bpy.props.IntProperty(name="Max Frames", default=250, min=1, max=10000)
    threshold: bpy.props.FloatProperty(name="Settle Threshold", default=1e-4, min=0, precision=5,
                                       description="Stop early when no object moves more than this per frame")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        sel = [o for o in context.selected_objects if o.type == 'MESH']
        skip = set(sel)
        if self.ground == 'COLLECTION':
            coll = bpy.data.collections.get(self.collection)
            if not coll:
                self.report({'ERROR'}, "Pick a collider collection")
                return {'CANCELLED'}
            colliders = {o for o in coll.all_objects if o.type == 'MESH' and o not in skip}
        elif self.ground == 'VISIBLE':
            colliders = {o for o in scene.objects if o.type == 'MESH' and o not in skip and o.visible_get()}
        else:
            colliders = set()
            for ob in sel:
                cs = _corners(ob)
                cz = sum(p.z for p in cs) / 8
                for p in [*cs, sum(cs, Vector()) / 8]:
                    hit = _ray_down(scene, (p.x, p.y, cz), lambda o: o not in skip and o.type == 'MESH')
                    if hit:
                        colliders.add(hit[2])
        solid = lambda o: o in colliders or (o.rigid_body is not None and o not in skip)  # noqa: E731
        plane = None
        if self.add_ground and any(_ray_down(scene, sum(_corners(o), Vector()) / 8, solid) is None for o in sel):
            plane = _ground_plane(context, sel)
            colliders.add(plane)

        frame, subframe = scene.frame_current, scene.frame_subframe
        made_world = scene.rigidbody_world is None
        if made_world:
            with context.temp_override(scene=scene):
                bpy.ops.rigidbody.world_add()
        world = scene.rigidbody_world
        cache = world.point_cache
        saved = (world.enabled, cache.frame_end)
        added = []
        try:
            world.enabled = True
            start = cache.frame_start
            cache.frame_end = max(cache.frame_end, start + self.frames)
            mass = self.mass if self.mass_preset == 'CUSTOM' else MASS[self.mass_preset]
            for ob, kind in [*((o, 'ACTIVE') for o in sel), *((o, 'PASSIVE') for o in colliders)]:
                if ob.rigid_body:
                    continue  # keep the user's own rigid body setup
                _rb_call(bpy.ops.rigidbody.object_add, ob, type=kind)
                added.append(ob)
                rb = ob.rigid_body
                rb.friction, rb.restitution = self.friction, self.bounciness
                if kind == 'ACTIVE':
                    rb.collision_shape, rb.mass = self.shape, mass
                else:
                    rb.collision_shape = 'MESH'  # concave ground (tables, terrain) needs real mesh shape
            scene.frame_set(start)
            prev, still, n = [o.matrix_world.copy() for o in sel], 0, 0
            for f in range(start + 1, start + self.frames + 1):
                scene.frame_set(f)
                n += 1
                cur = [o.matrix_world.copy() for o in sel]
                delta = max(abs(x) for a, b in zip(cur, prev) for row in (a - b) for x in row)
                prev = cur
                still = still + 1 if (n > 5 and delta < self.threshold) else 0
                if still >= 3:
                    break
            final = dict(zip(sel, prev))
        finally:
            for ob in added:
                _rb_call(bpy.ops.rigidbody.object_remove, ob)
            if made_world:
                coll = world.collection
                with context.temp_override(scene=scene):
                    bpy.ops.rigidbody.world_remove()
                if coll and not coll.objects:
                    bpy.data.collections.remove(coll)
            else:
                world.enabled, cache.frame_end = saved
            if plane:
                me = plane.data
                bpy.data.objects.remove(plane)
                bpy.data.meshes.remove(me)
            scene.frame_set(frame, subframe=subframe)
        for ob, mw in final.items():
            ob.matrix_world = mw
        self.report({'INFO'}, f"Settled {len(sel)} object(s) after {n} frame(s)")
        return {'FINISHED'}


class PH_OT_drop_surface(bpy.types.Operator):
    bl_idname = "physics_helpers.drop_surface"
    bl_label = "Drop to Surface"
    bl_description = "Move each selected object straight down onto whatever is below it (no simulation)"
    bl_options = {'REGISTER', 'UNDO'}

    align: bpy.props.BoolProperty(name="Align to Normal", default=False)
    random_z: bpy.props.FloatProperty(name="Random Rotation Z", subtype='ANGLE', default=0, min=0, max=3.14159265)
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        scene, rng, moved = context.scene, random.Random(self.seed), 0
        for ob in sorted(context.selected_objects, key=lambda o: min(p.z for p in _corners(o))):
            other = lambda o, ob=ob: o is not ob  # noqa: E731
            if self.align or self.random_z:
                hit = _ray_down(scene, sum(_corners(ob), Vector()) / 8, other)
                q = UP.rotation_difference(hit[1]) if (self.align and hit) else Quaternion()
                q = q @ Quaternion(UP, rng.uniform(-self.random_z, self.random_z))
                loc, rot, scale = ob.matrix_world.decompose()
                ob.matrix_world = Matrix.LocRotScale(loc, q @ rot, scale)
                context.view_layer.update()
            cs = sorted(_corners(ob), key=lambda p: p.z)
            cz = sum(p.z for p in cs) / 8
            drops = []
            for p in [*cs[:4], sum(cs[:4], Vector()) / 4]:
                hit = _ray_down(scene, (p.x, p.y, cz), other)
                if hit:
                    drops.append(p.z - hit[0].z)
            if not drops:
                continue
            mw = ob.matrix_world.copy()
            mw.translation.z -= min(drops)
            ob.matrix_world = mw
            context.view_layer.update()
            moved += 1
        self.report({'INFO'}, f"Dropped {moved} object(s)")
        return {'FINISHED'}


def _cloth_md(ob):
    return next((m for m in ob.modifiers if m.type == 'CLOTH'), None) if ob else None


class PH_OT_cloth(bpy.types.Operator):
    bl_idname = "physics_helpers.cloth"
    bl_label = "Quick Cloth"
    bl_description = ("Add a Cloth modifier with a fabric preset to the active mesh. In Edit Mode the selected "
                      "vertices become the pin group; other selected objects become colliders")
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(name="Preset", default='COTTON', items=[
        (k, k.title(), "") for k in CLOTH])
    quality: bpy.props.IntProperty(name="Quality Steps", default=0, min=0, max=80,
                                   description="Simulation steps per frame (0 = use preset)")
    self_collision: bpy.props.BoolProperty(name="Self Collision", default=False)
    hook: bpy.props.BoolProperty(name="Hook Pins", default=False,
                                 description="Add an empty hooked to the pinned vertices so they can be animated")
    collide_others: bpy.props.BoolProperty(name="Others Collide", default=True,
                                           description="Add Collision to the other selected meshes")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob and ob.type == 'MESH' and context.mode in {'OBJECT', 'EDIT_MESH'}

    def execute(self, context):
        ob = context.active_object
        edit = context.mode == 'EDIT_MESH'
        if edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        idx = [v.index for v in ob.data.vertices if v.select] if edit else []
        md = _cloth_md(ob) or ob.modifiers.new("Cloth", 'CLOTH')
        s = md.settings
        (s.quality, s.mass, s.tension_stiffness, s.compression_stiffness, s.shear_stiffness,
         s.bending_stiffness, damp) = CLOTH[self.preset]
        s.tension_damping = s.compression_damping = s.shear_damping = damp
        s.air_damping = 1.0
        if self.quality:
            s.quality = self.quality
        md.collision_settings.use_self_collision = self.self_collision
        if idx:
            old = ob.vertex_groups.get("Pin")
            if old:
                ob.vertex_groups.remove(old)
            ob.vertex_groups.new(name="Pin").add(idx, 1.0, 'REPLACE')
            s.vertex_group_mass = "Pin"
            if self.hook:
                local = sum((ob.data.vertices[i].co for i in idx), Vector()) / len(idx)
                emp = bpy.data.objects.new("ClothPin", None)
                emp.empty_display_type = 'SPHERE'
                emp.empty_display_size = 0.2
                emp.location = ob.matrix_world @ local
                (ob.users_collection[0] if ob.users_collection else context.collection).objects.link(emp)
                hk = ob.modifiers.new("ClothPin", 'HOOK')
                hk.object, hk.center = emp, local
                hk.vertex_indices_set(idx)
                hk.matrix_inverse = Matrix.Translation(emp.location).inverted() @ ob.matrix_world
                ob.modifiers.move(len(ob.modifiers) - 1, list(ob.modifiers).index(md))
        if self.collide_others:
            for o in context.selected_objects:
                if o is not ob and o.type == 'MESH' and not any(m.type == 'COLLISION' for m in o.modifiers):
                    o.modifiers.new("Collision", 'COLLISION')
        if edit:
            bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, f"Cloth ({self.preset.title()}), {len(idx)} pinned vertices")
        return {'FINISHED'}


class PH_OT_cloth_apply(bpy.types.Operator):
    bl_idname = "physics_helpers.cloth_apply"
    bl_label = "Apply Cloth at Frame"
    bl_description = ("Simulate up to the current frame and store the cloth shape as a new shape key "
                      "(Cloth_<frame>) or apply it to the mesh")
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(name="As", default='SHAPE_KEY', items=[
        ('SHAPE_KEY', "Shape Key", "Keep the modifier, add the result as a shape key"),
        ('MESH', "Mesh", "Apply the modifier to the mesh")])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _cloth_md(context.active_object)

    def execute(self, context):
        ob, scene = context.active_object, context.scene
        md, f = _cloth_md(ob), scene.frame_current
        if self.target == 'MESH' and ob.data.shape_keys:
            self.report({'ERROR'}, "Mesh has shape keys: apply as a shape key instead")
            return {'CANCELLED'}
        for i in range(md.point_cache.frame_start, f + 1):  # cloth only simulates frame by frame
            scene.frame_set(i)
        with context.temp_override(object=ob, active_object=ob):
            if self.target == 'MESH':
                bpy.ops.object.modifier_apply(modifier=md.name)
            else:
                bpy.ops.object.modifier_apply_as_shapekey(modifier=md.name, keep_modifier=True)
                ob.data.shape_keys.key_blocks[-1].name = f"Cloth_{f}"
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

_D, _C = PH_OT_drop.__annotations__, PH_OT_cloth.__annotations__


class PH_Settings(bpy.types.PropertyGroup):
    shape: _D["shape"]
    friction: _D["friction"]
    bounciness: _D["bounciness"]
    mass_preset: _D["mass_preset"]
    mass: _D["mass"]
    ground: _D["ground"]
    collection: bpy.props.PointerProperty(name="Collection", type=bpy.types.Collection)
    add_ground: _D["add_ground"]
    frames: _D["frames"]
    threshold: _D["threshold"]
    align: PH_OT_drop_surface.__annotations__["align"]
    random_z: PH_OT_drop_surface.__annotations__["random_z"]
    preset: _C["preset"]
    quality: _C["quality"]
    self_collision: _C["self_collision"]
    hook: _C["hook"]
    collide_others: _C["collide_others"]


class PH_PT_base:
    bl_space_type, bl_region_type, bl_category = 'VIEW_3D', 'UI', "Physics"


class PH_PT_drop(PH_PT_base, bpy.types.Panel):
    bl_label = "Drop / Settle"

    def draw(self, context):
        s, col = context.scene.physics_helpers, self.layout.column(align=True)
        for p in ("shape", "mass_preset"):
            col.prop(s, p, text="")
        if s.mass_preset == 'CUSTOM':
            col.prop(s, "mass")
        col.prop(s, "friction")
        col.prop(s, "bounciness")
        col.separator()
        col.prop(s, "ground", text="")
        if s.ground == 'COLLECTION':
            col.prop(s, "collection", text="")
        col.prop(s, "add_ground")
        col.prop(s, "frames")
        col.prop(s, "threshold")
        op = col.operator("physics_helpers.drop", icon='PHYSICS')
        for p in ("shape", "friction", "bounciness", "mass_preset", "mass", "ground", "add_ground",
                  "frames", "threshold"):
            setattr(op, p, getattr(s, p))
        op.collection = s.collection.name if s.collection else ""
        col.separator()
        row = col.row(align=True)
        row.prop(s, "align", toggle=True)
        row.prop(s, "random_z")
        op = col.operator("physics_helpers.drop_surface", icon='SORT_ASC')
        op.align, op.random_z = s.align, s.random_z


class PH_PT_cloth(PH_PT_base, bpy.types.Panel):
    bl_label = "Quick Cloth"

    def draw(self, context):
        s, col = context.scene.physics_helpers, self.layout.column(align=True)
        col.prop(s, "preset", text="")
        col.prop(s, "quality")
        col.prop(s, "self_collision")
        col.prop(s, "hook")
        col.prop(s, "collide_others")
        op = col.operator("physics_helpers.cloth", icon='MOD_CLOTH')
        for p in ("preset", "quality", "self_collision", "hook", "collide_others"):
            setattr(op, p, getattr(s, p))
        col.label(text="Edit Mode: selected verts = pins", icon='INFO')
        col.separator()
        row = col.row(align=True)
        row.operator("physics_helpers.cloth_apply", text="To Shape Key").target = 'SHAPE_KEY'
        row.operator("physics_helpers.cloth_apply", text="Apply").target = 'MESH'


_classes = (PH_OT_drop, PH_OT_drop_surface, PH_OT_cloth, PH_OT_cloth_apply, PH_Settings, PH_PT_drop, PH_PT_cloth)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.physics_helpers = bpy.props.PointerProperty(type=PH_Settings)


def unregister():
    del bpy.types.Scene.physics_helpers
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
