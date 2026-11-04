"""Space Switch: dynamic parenting via "SS:" Child Of constraints, switched without popping."""
import bpy
from bpy_extras import anim_utils
from mathutils import Matrix

PREFIX = "SS:"
WORLD = "World"


# ---------------------------------------------------------------- helpers

def _is_bone(o):
    return isinstance(o, bpy.types.PoseBone)


def _final(o):
    """Evaluated matrix we keep stable: pose space for bones (armature untouched), world for objects."""
    return (o.matrix if _is_bone(o) else o.matrix_world).copy()


def _world(ob, bone=""):
    return ob.matrix_world @ ob.pose.bones[bone].matrix if bone else ob.matrix_world.copy()


def ss_constraints(o):
    return [c for c in o.constraints if c.type == 'CHILD_OF' and c.name.startswith(PREFIX)]


def active_parent(o):
    """Name of the SS constraint with influence >= 0.5, or WORLD."""
    best = max(ss_constraints(o), key=lambda c: c.influence, default=None)
    return best.name if best and best.influence >= 0.5 else WORLD


def _rot_prop(o):
    return {'QUATERNION': "rotation_quaternion", 'AXIS_ANGLE': "rotation_axis_angle"}.get(
        o.rotation_mode, "rotation_euler")


def _set_basis(o, m):
    loc, q, sca = m.decompose()
    o.location, o.scale = loc, sca
    if o.rotation_mode == 'QUATERNION':
        q.make_compatible(o.rotation_quaternion)
        o.rotation_quaternion = q
    elif o.rotation_mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        o.rotation_axis_angle = (angle, *axis)
    else:
        o.rotation_euler = q.to_euler(o.rotation_mode, o.rotation_euler)


def _match(o, target, view_layer):
    """Solve the local transform so the evaluated matrix equals `target`.
    final = A @ basis while one Child Of is fully on (or none), so basis = A^-1 @ target.
    Repeated for partial influences / other constraints that make it non-linear."""
    for _ in range(4):
        view_layer.update()
        cur = _final(o)
        if all(abs(a - b) < 1e-6 for r1, r2 in zip(cur, target) for a, b in zip(r1, r2)):
            return
        _set_basis(o, (cur @ o.matrix_basis.inverted()).inverted() @ target)
    view_layer.update()


def _key_transform(o, frame):
    for p in ("location", _rot_prop(o), "scale"):
        o.keyframe_insert(p, frame=frame)


def _channelbag(o):
    ad = o.id_data.animation_data
    if not ad or not ad.action or not ad.action_slot:
        return None
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


def _influence_fcurves(o):
    """{constraint name: influence F-Curve} for the SS constraints of `o`."""
    cb = _channelbag(o)
    if not cb:
        return {}
    out = {}
    for c in ss_constraints(o):
        fc = cb.fcurves.find(c.path_from_id("influence"))
        if fc:
            out[c.name] = fc
    return out


# ---------------------------------------------------------------- core

def add_parent(o, target, subtarget=""):
    """Add (or reuse) a Child Of named "SS:<target>[/<bone>]" with influence 0 and inverse set now."""
    name = PREFIX + target.name + (f"/{subtarget}" if subtarget else "")
    c = o.constraints.get(name)
    if not c:
        c = o.constraints.new('CHILD_OF')
        c.name = name
        c.influence = 0.0
    c.target = target
    c.subtarget = subtarget
    c.set_inverse_pending = False
    c.inverse_matrix = _world(target, subtarget).inverted()
    return c


def switch(scene, view_layer, o, name, frame=None):
    """Switch `o` to parent `name` (constraint name or WORLD) at `frame` without a pop.
    Keys the SS influences (constant) at frame-1 (old) and frame (new), plus loc/rot/scale
    at frame-1 (old) and frame (compensated)."""
    frame = scene.frame_current if frame is None else frame
    cons = ss_constraints(o)
    if name != WORLD and name not in {c.name for c in cons}:
        raise ValueError(f"{name} is not a space switch parent")
    if scene.frame_current != frame:
        scene.frame_set(frame)
    target = _final(o)  # what the user sees right now (also unkeyed edits)
    scene.frame_set(frame - 1)
    for c in cons:
        c.keyframe_insert("influence", frame=frame - 1)
    _key_transform(o, frame - 1)
    scene.frame_set(frame)
    for c in cons:
        c.influence = float(c.name == name)
        c.keyframe_insert("influence", frame=frame)
    for fc in _influence_fcurves(o).values():
        for k in fc.keyframe_points:
            k.interpolation = 'CONSTANT'
    _match(o, target, view_layer)
    _key_transform(o, frame)


def switches(o):
    """[(frame, parent name)] where the active parent changes along the influence keys."""
    fcs = _influence_fcurves(o)
    frames = sorted({k.co.x for fc in fcs.values() for k in fc.keyframe_points})
    out, prev = [], None
    for f in frames:
        vals = {n: fc.evaluate(f) for n, fc in fcs.items()}
        best = max(vals, key=vals.get, default=None)
        cur = best if best and vals[best] >= 0.5 else WORLD
        if prev is not None and cur != prev:
            out.append((int(f), cur))
        prev = cur
    return out


def bake_to_world(scene, view_layer, owners, start, end, remove=True):
    """Key the visual transform of every owner on each frame in [start, end], then drop the
    SS influence animation and remove (or disable) the SS constraints."""
    owners = [o for o in owners if ss_constraints(o)]
    if not owners:
        return 0
    orig = scene.frame_current
    mats = {}
    for f in range(start, end + 1):
        scene.frame_set(f)
        for i, o in enumerate(owners):
            mats[i, f] = _final(o)
    for o in owners:
        cb = _channelbag(o)
        for fc in _influence_fcurves(o).values():
            cb.fcurves.remove(fc)
        for c in ss_constraints(o):
            if remove:
                o.constraints.remove(c)
            else:
                c.influence = 0.0
    for f in range(start, end + 1):
        scene.frame_set(f)
        for i, o in enumerate(owners):  # pose.bones order is parent-first
            _match(o, mats[i, f], view_layer)
            _key_transform(o, f)
    scene.frame_set(orig)
    return len(owners)


# ---------------------------------------------------------------- operators

def _active(context):
    return context.active_pose_bone if context.mode == 'POSE' else context.active_object


def _owners(context):
    if context.mode == 'POSE':
        return list(context.selected_pose_bones or [])
    return [context.active_object] if context.active_object else []


class _Op:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active(context) is not None


class SS_OT_add_parent(_Op, bpy.types.Operator):
    bl_idname = "space_switch.add_parent"
    bl_label = "Add Parent"
    bl_description = ("Add the other selected object or bone as a switchable parent of the active "
                      "object / bone (Child Of named SS:<target>, influence 0)")

    def execute(self, context):
        o = _active(context)
        if _is_bone(o):
            bones = [pb for pb in context.selected_pose_bones or [] if pb != o]
            if bones:
                add_parent(o, bones[0].id_data, bones[0].name)
                return {'FINISHED'}
        obs = [x for x in context.selected_objects if x != o.id_data]
        if not obs:
            self.report({'WARNING'}, "Select a parent object or bone besides the active one")
            return {'CANCELLED'}
        add_parent(o, obs[0])
        return {'FINISHED'}


class SS_OT_switch(_Op, bpy.types.Operator):
    bl_idname = "space_switch.switch"
    bl_label = "Switch Parent"
    bl_description = "Switch to this parent at the current frame, keying so the world transform does not pop"
    parent: bpy.props.StringProperty(name="Parent", default=WORLD)

    def execute(self, context):
        n = 0
        for o in _owners(context):
            if active_parent(o) != self.parent and (
                    self.parent == WORLD and ss_constraints(o) or o.constraints.get(self.parent)):
                switch(context.scene, context.view_layer, o, self.parent)
                n += 1
        if not n:
            self.report({'INFO'}, "Nothing to switch")
        return {'FINISHED'}


class SS_OT_bake(_Op, bpy.types.Operator):
    bl_idname = "space_switch.bake_world"
    bl_label = "Bake Range to World"
    bl_description = "Bake the visual transform over a frame range to keys and remove the space switching"
    start: bpy.props.IntProperty(name="Start")
    end: bpy.props.IntProperty(name="End")
    remove: bpy.props.BoolProperty(name="Remove Constraints", default=True,
                                   description="Delete the SS constraints (otherwise only disable them)")

    def invoke(self, context, event):
        self.start, self.end = context.scene.frame_start, context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = bake_to_world(context.scene, context.view_layer, _owners(context),
                          self.start, self.end, self.remove)
        self.report({'INFO'}, f"Baked {n} owner(s)")
        return {'FINISHED'}


class SS_OT_jump(bpy.types.Operator):
    bl_idname = "space_switch.jump"
    bl_label = "Jump to Switch"
    bl_description = "Jump to this space switch frame"
    bl_options = {'REGISTER'}
    frame: bpy.props.IntProperty()

    def execute(self, context):
        context.scene.frame_set(self.frame)
        return {'FINISHED'}


class SS_PT_panel(bpy.types.Panel):
    bl_label = "Space Switch"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'}

    def draw(self, context):
        layout = self.layout
        o = _active(context)
        if o is None:
            layout.label(text="No active object or bone")
            return
        layout.label(text=o.name, icon='BONE_DATA' if _is_bone(o) else 'OBJECT_DATA')
        layout.operator(SS_OT_add_parent.bl_idname, icon='ADD')
        cur = active_parent(o)
        col = layout.column(align=True)
        for name in [WORLD] + [c.name for c in ss_constraints(o)]:
            label = name[len(PREFIX):] if name.startswith(PREFIX) else name
            col.operator(SS_OT_switch.bl_idname, text=f"Switch to {label}",
                         icon='WORLD' if name == WORLD else 'CON_CHILDOF',
                         depress=name == cur).parent = name
        sw = switches(o)
        if sw:
            box = layout.box()
            box.label(text="Switches")
            for f, name in sw:
                box.operator(SS_OT_jump.bl_idname, text=f"{f}: {name.removeprefix(PREFIX)}",
                             icon='TIME').frame = f
        layout.operator(SS_OT_bake.bl_idname, icon='REC')


_classes = (SS_OT_add_parent, SS_OT_switch, SS_OT_bake, SS_OT_jump, SS_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
