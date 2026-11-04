"""Pose Tools: paste a stored pose over a frame range, mirror animation L<->R, sparse visual bake."""
import bpy
from bpy_extras import anim_utils
from mathutils import Euler, Quaternion

flip_name = bpy.utils.flip_name

# Channels negated when mirroring across X (same as Blender's Paste Flipped).
FLIP_SIGN = {("location", 0), ("rotation_quaternion", 2), ("rotation_quaternion", 3),
             ("rotation_euler", 1), ("rotation_euler", 2),
             ("rotation_axis_angle", 2), ("rotation_axis_angle", 3)}

_clip = {}  # bone name -> {"vals": {channel: value}, "base": {channel: animated value at copy frame}}


# ---------------------------------------------------------------- helpers

def _rot_prop(pb):
    return {'QUATERNION': "rotation_quaternion", 'AXIS_ANGLE': "rotation_axis_angle"}.get(
        pb.rotation_mode, "rotation_euler")


def _channels(pb):
    """Keyable channels as (prop, index): transforms + numeric custom props ('["name"]', 0)."""
    rp = _rot_prop(pb)
    out = [("location", i) for i in range(3)]
    out += [(rp, i) for i in range(3 if rp == "rotation_euler" else 4)]
    out += [("scale", i) for i in range(3)]
    out += [(f'["{bpy.utils.escape_identifier(k)}"]', 0) for k in pb.keys()
            if isinstance(pb[k], (int, float))]
    return out


def _path(pb, ch):
    prop = ch[0]
    return pb.path_from_id() + prop if prop.startswith("[") else pb.path_from_id(prop)


def _get(pb, ch):
    v = pb.path_resolve(ch[0])
    return v if ch[0].startswith("[") else v[ch[1]]


def _set(pb, ch, v):
    prop, i = ch
    if prop.startswith("["):
        k = prop[2:-2]
        pb[k] = type(pb[k])(v)
    else:
        getattr(pb, prop)[i] = v


def _channelbag(ob):
    ad = ob.animation_data or ob.animation_data_create()
    if not ad.action:
        ad.action = bpy.data.actions.new(ob.name + "Action")
    if not ad.action_slot:
        ad.action_slot = ad.action.slots[0] if ad.action.slots else ad.action.slots.new('OBJECT', ob.name)
    return anim_utils.action_ensure_channelbag_for_slot(ad.action, ad.action_slot)


def _bone_fcurves(cb, pb):
    prefix = pb.path_from_id()  # 'pose.bones["x"]' - closing quote makes the prefix exact
    return [fc for fc in cb.fcurves if fc.data_path.startswith(prefix)]


def _key(cb, pb, ch, frame, value):
    fc = cb.fcurves.ensure(_path(pb, ch), index=ch[1], group_name=pb.name)
    fc.keyframe_points.insert(frame, value)


def _eval(cb, pb, ch, frame):
    fc = cb.fcurves.find(_path(pb, ch), index=ch[1])
    return fc.evaluate(frame) if fc else _get(pb, ch)


def _flip(d):
    return {ch: -v if ch in FLIP_SIGN else v for ch, v in d.items()}


def _add_delta(cur, base, vals):
    """cur + (vals - base): quaternions compose, scale multiplies, everything else adds."""
    out = {}
    for ch, v in vals.items():
        if ch[0] == "rotation_quaternion" or ch[0] == "scale":
            continue
        out[ch] = cur[ch] + v - base[ch]
    for i in range(3):
        ch = ("scale", i)
        if ch in vals:
            out[ch] = cur[ch] * vals[ch] / base[ch] if base[ch] else cur[ch] + vals[ch] - base[ch]
    if ("rotation_quaternion", 0) in vals:
        q = [Quaternion([d[("rotation_quaternion", i)] for i in range(4)]) for d in (cur, base, vals)]
        res = q[0] @ (q[1].inverted() @ q[2])
        out.update({("rotation_quaternion", i): res[i] for i in range(4)})
    return out


def _visual_basis(ob, pb):
    """Evaluated pose-space matrix (constraints/IK/drivers) converted to a matrix_basis."""
    return ob.convert_space(pose_bone=pb, matrix=pb.matrix, from_space='POSE', to_space='LOCAL')


def _key_matrix(cb, pb, m, frame, prev):
    """Key loc/rot/scale from a basis matrix; `prev` keeps rotations continuous between frames."""
    loc, q, sca = m.decompose()
    rp = _rot_prop(pb)
    if rp == "rotation_quaternion":
        if pb.name in prev:
            q.make_compatible(prev[pb.name])
        rot = prev[pb.name] = q
    elif rp == "rotation_euler":
        rot = prev[pb.name] = q.to_euler(pb.rotation_mode, prev.get(pb.name, Euler()))
    else:
        axis, angle = q.to_axis_angle()
        rot = (angle, *axis)
    for prop, vals in (("location", loc), (rp, rot), ("scale", sca)):
        for i, v in enumerate(vals):
            _key(cb, pb, (prop, i), frame, v)


# ---------------------------------------------------------------- core

def copy_pose(ob, bones, frame):
    """Store the current pose of `bones` (plus the animated pose at `frame` for additive paste)."""
    cb = _channelbag(ob)
    _clip.clear()
    for pb in bones:
        _clip[pb.name] = {"vals": {ch: _get(pb, ch) for ch in _channels(pb)},
                          "base": {ch: _eval(cb, pb, ch, frame) for ch in _channels(pb)}}
    return len(_clip)


def key_frames(ob, pb, start=None, end=None, selected=False):
    """Frames of `pb`'s keys inside [start, end] (or the selected keys when `selected`)."""
    cb = _channelbag(ob)
    return sorted({k.co.x for fc in _bone_fcurves(cb, pb) for k in fc.keyframe_points
                   if (k.select_control_point if selected else start <= k.co.x <= end)})


def paste_pose(ob, bones, frames_of, flipped=False, additive=False):
    """Key the stored pose on every frame from `frames_of(pb)`. Returns number of keyed poses."""
    cb = _channelbag(ob)
    n = 0
    for pb in bones:
        src = _clip.get(flip_name(pb.name) if flipped else pb.name)
        if not src:
            continue
        vals, base = src["vals"], src["base"]
        if flipped:
            vals, base = _flip(vals), _flip(base)
        own = set(_channels(pb))  # skip custom props the target bone doesn't have
        vals = {ch: v for ch, v in vals.items() if ch in own or not ch[0].startswith("[")}
        for f in frames_of(pb):
            new = vals
            if additive:
                new = _add_delta({ch: _eval(cb, pb, ch, f) for ch in vals}, base, vals)
            for ch, v in new.items():
                _key(cb, pb, ch, f, v)
            n += 1
    return n


def mirror_animation(ob, bones, offset=0.0, cycle=None):
    """Copy each bone's F-curves onto its flip_name bone, mirrored and shifted by `offset`.
    `cycle=(start, end)` wraps shifted keys into that range (walk cycles)."""
    cb = _channelbag(ob)
    snap = []  # read everything before writing so selecting both sides swaps cleanly
    for pb in bones:
        tgt = ob.pose.bones.get(flip_name(pb.name))
        if not tgt or tgt == pb:
            continue
        for fc in _bone_fcurves(cb, pb):
            prop = fc.data_path[len(pb.path_from_id()):].lstrip(".")
            sign = -1 if (prop, fc.array_index) in FLIP_SIGN else 1
            path = tgt.path_from_id() + ("" if prop.startswith("[") else ".") + prop
            keys = {}
            for k in fc.keyframe_points:
                d = offset
                if cycle:
                    s, e = cycle
                    d += s + (k.co.x + offset - s) % (e - s) - (k.co.x + offset)
                keys[round(k.co.x + d, 3)] = (d, k.co.y, k.handle_left.copy(), k.handle_right.copy(),
                                              k.interpolation, k.handle_left_type, k.handle_right_type)
            if cycle and cycle[0] in keys and cycle[1] not in keys:  # close the loop
                d, *rest = keys[cycle[0]]
                keys[cycle[1]] = (d + cycle[1] - cycle[0], *rest)
            snap.append((path, fc.array_index, tgt.name, sign, keys))
    for path, idx, group, sign, keys in snap:
        old = cb.fcurves.find(path, index=idx)
        if old:
            cb.fcurves.remove(old)
        fc = cb.fcurves.new(path, index=idx, group_name=group)
        fc.keyframe_points.add(len(keys))
        for kp, x in zip(fc.keyframe_points, sorted(keys)):
            d, y, hl, hr, interp, hlt, hrt = keys[x]
            kp.co = (x, y * sign)
            kp.handle_left = (hl.x + d, hl.y * sign)
            kp.handle_right = (hr.x + d, hr.y * sign)
            kp.interpolation, kp.handle_left_type, kp.handle_right_type = interp, hlt, hrt
        fc.update()
    return len(snap)


def smart_bake(scene, ob, bones, start, end, step=1, keys_only=False, clear_constraints=False):
    """Bake the visual transforms of `bones` into the current action. `keys_only` samples only
    frames that already hold a key anywhere in the action. Returns baked frames."""
    cb = _channelbag(ob)
    if keys_only:
        frames = sorted({round(k.co.x) for fc in cb.fcurves for k in fc.keyframe_points
                         if start <= k.co.x <= end})
    else:
        frames = list(range(start, end + 1, max(1, step)))
    cur = scene.frame_current
    samples = []
    for f in frames:
        scene.frame_set(f)
        samples.append((f, [(pb, _visual_basis(ob, pb)) for pb in bones]))
    if clear_constraints:
        for pb in bones:
            for c in list(pb.constraints):
                pb.constraints.remove(c)
    prev = {}
    for f, mats in samples:
        for pb, m in mats:
            _key_matrix(cb, pb, m, f, prev)
    scene.frame_set(cur)
    return frames


def reset_bones(bones):
    for pb in bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.rotation_axis_angle = (0, 0, 1, 0)
        pb.scale = (1, 1, 1)
        for k in pb.keys():
            if isinstance(pb[k], (int, float)):
                pb[k] = type(pb[k])(pb.id_properties_ui(k).as_dict().get("default", 0))


def key_whole_character(ob, frame, visual=False):
    cb = _channelbag(ob)
    for pb in ob.pose.bones:
        if visual:
            _key_matrix(cb, pb, _visual_basis(ob, pb), frame, {})
            chans = [ch for ch in _channels(pb) if ch[0].startswith("[")]
        else:
            chans = _channels(pb)
        for ch in chans:
            _key(cb, pb, ch, frame, _get(pb, ch))


def delete_keys_at(ob, bones, frame):
    cb = _channelbag(ob)
    n = 0
    for pb in bones:
        for fc in _bone_fcurves(cb, pb):
            for k in [k for k in fc.keyframe_points if abs(k.co.x - frame) < 1e-3]:
                fc.keyframe_points.remove(k)
                n += 1
            if not fc.keyframe_points:
                cb.fcurves.remove(fc)
    return n


# ---------------------------------------------------------------- operators

class _PoseOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.object and context.object.type == 'ARMATURE'


class PT_OT_copy(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.copy"
    bl_label = "Copy Pose"
    bl_description = "Store the selected bones' pose (transforms + custom properties) for Paste to Range"
    bl_options = {'REGISTER'}

    def execute(self, context):
        n = copy_pose(context.object, context.selected_pose_bones, context.scene.frame_current)
        self.report({'INFO'}, f"Copied {n} bones")
        return {'FINISHED'}


class PT_OT_paste_range(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.paste_range"
    bl_label = "Paste to Range"
    bl_description = "Paste the stored pose onto every key of the selected bones in a range (or on selected keys)"

    target: bpy.props.EnumProperty(name="Keys", items=[
        ('RANGE', "Keys in Range", "Every key of the selected bones between Start and End"),
        ('SELECTED', "Selected Keys", "Keys selected in the Dope Sheet / Graph Editor")])
    start: bpy.props.IntProperty(name="Start", default=1)
    end: bpy.props.IntProperty(name="End", default=250)
    flipped: bpy.props.BoolProperty(name="Flipped", description="Paste onto the mirrored bones, mirrored")
    additive: bpy.props.BoolProperty(
        name="Additive", description="Add the offset between the stored pose and the animation at the copy frame")

    def invoke(self, context, event):
        self.start, self.end = context.scene.frame_start, context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not _clip:
            self.report({'WARNING'}, "Copy a pose first")
            return {'CANCELLED'}
        ob = context.object
        sel = self.target == 'SELECTED'
        n = paste_pose(ob, context.selected_pose_bones,
                       lambda pb: key_frames(ob, pb, self.start, self.end, sel), self.flipped, self.additive)
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, f"Pasted on {n} keys")
        return {'FINISHED'}


class PT_OT_mirror_anim(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.mirror_animation"
    bl_label = "Mirror Animation"
    bl_description = "Copy the selected bones' animation onto their mirror bones (flipped), with a time offset"

    offset: bpy.props.FloatProperty(name="Offset", default=0, description="Frames to shift (half a cycle for walks)")
    wrap: bpy.props.BoolProperty(name="Wrap in Scene Range", default=True,
                                 description="Wrap shifted keys back into the scene range (cycles)")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        sc = context.scene
        n = mirror_animation(context.object, context.selected_pose_bones, self.offset,
                             (sc.frame_start, sc.frame_end) if self.wrap else None)
        sc.frame_set(sc.frame_current)
        self.report({'INFO'}, f"Mirrored {n} curves")
        return {'FINISHED'}


class PT_OT_bake(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.smart_bake"
    bl_label = "Smart Bake"
    bl_description = "Bake the selected bones' visual transforms into the current action, sparse or every N frames"

    start: bpy.props.IntProperty(name="Start", default=1)
    end: bpy.props.IntProperty(name="End", default=250)
    step: bpy.props.IntProperty(name="Every N Frames", default=1, min=1)
    keys_only: bpy.props.BoolProperty(name="Keys Only", description="Bake only on frames that already have a key")
    clear_constraints: bpy.props.BoolProperty(name="Remove Constraints")

    def invoke(self, context, event):
        self.start, self.end = context.scene.frame_start, context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        frames = smart_bake(context.scene, context.object, context.selected_pose_bones, self.start, self.end,
                            self.step, self.keys_only, self.clear_constraints)
        self.report({'INFO'}, f"Baked {len(frames)} frames")
        return {'FINISHED'}


class PT_OT_reset(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.reset_all"
    bl_label = "Reset All"
    bl_description = "Reset location, rotation, scale and custom properties of selected bones to defaults"

    def execute(self, context):
        reset_bones(context.selected_pose_bones)
        return {'FINISHED'}


class PT_OT_select(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.select"
    bl_label = "Select Bones"
    bl_description = "Select bones that have keys or constraints"

    kind: bpy.props.EnumProperty(items=[('KEYED', "Keyed", "Bones with animation"),
                                        ('CONSTRAINED', "Constrained", "Bones with constraints")])
    extend: bpy.props.BoolProperty(name="Extend")

    def execute(self, context):
        ob = context.object
        keyed = set()
        if self.kind == 'KEYED':
            cb = _channelbag(ob)
            keyed = {pb.name for pb in ob.pose.bones if _bone_fcurves(cb, pb)}
        for pb in ob.pose.bones:
            hit = pb.name in keyed if self.kind == 'KEYED' else bool(pb.constraints)
            if hit or not self.extend:
                pb.select = hit
        return {'FINISHED'}


class PT_OT_key_all(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.key_whole_character"
    bl_label = "Key Whole Character"
    bl_description = "Keyframe every bone's transforms and custom properties at the current frame"

    visual: bpy.props.BoolProperty(name="Visual", description="Key the visual result of constraints",
                                    options={'SKIP_SAVE'})

    def execute(self, context):
        key_whole_character(context.object, context.scene.frame_current, self.visual)
        return {'FINISHED'}


class PT_OT_delete_keys(_PoseOp, bpy.types.Operator):
    bl_idname = "pose_tools.delete_keys"
    bl_label = "Delete Keys at Frame"
    bl_description = "Delete all keys of the selected bones at the current frame"

    def execute(self, context):
        n = delete_keys_at(context.object, context.selected_pose_bones, context.scene.frame_current)
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, f"Deleted {n} keys")
        return {'FINISHED'}


def _draw_ops(layout):
    layout.operator(PT_OT_copy.bl_idname, icon='COPYDOWN')
    layout.operator(PT_OT_paste_range.bl_idname, icon='PASTEDOWN')
    layout.operator(PT_OT_mirror_anim.bl_idname, icon='MOD_MIRROR')
    layout.operator(PT_OT_bake.bl_idname, icon='REC')
    layout.operator(PT_OT_key_all.bl_idname, icon='KEYINGSET')
    layout.operator(PT_OT_delete_keys.bl_idname, icon='KEY_DEHLT')
    layout.operator(PT_OT_reset.bl_idname, icon='LOOP_BACK')


class PT_PT_panel(bpy.types.Panel):
    bl_label = "Pose Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"
    bl_context = "posemode"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator(PT_OT_copy.bl_idname, icon='COPYDOWN')
        col.operator(PT_OT_paste_range.bl_idname, icon='PASTEDOWN')
        col = self.layout.column(align=True)
        col.operator(PT_OT_mirror_anim.bl_idname, icon='MOD_MIRROR')
        col.operator(PT_OT_bake.bl_idname, icon='REC')
        col.operator("nla.bake", text="Bake Action (Full Range)", icon='ACTION')
        col = self.layout.column(align=True)
        col.operator(PT_OT_key_all.bl_idname, icon='KEYINGSET')
        col.operator(PT_OT_key_all.bl_idname, text="Key Whole Character (Visual)").visual = True
        col.operator(PT_OT_delete_keys.bl_idname, icon='KEY_DEHLT')
        col.operator(PT_OT_reset.bl_idname, text="Reset All incl. Custom Props", icon='LOOP_BACK')
        row = self.layout.row(align=True)
        row.label(text="Select:")
        row.operator("pose.select_mirror", text="Mirror")
        row.operator(PT_OT_select.bl_idname, text="Keyed").kind = 'KEYED'
        row.operator(PT_OT_select.bl_idname, text="Constrained").kind = 'CONSTRAINED'


def menu(self, context):
    self.layout.separator()
    _draw_ops(self.layout)


_classes = (PT_OT_copy, PT_OT_paste_range, PT_OT_mirror_anim, PT_OT_bake, PT_OT_reset, PT_OT_select,
            PT_OT_key_all, PT_OT_delete_keys, PT_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_pose.append(menu)


def unregister():
    bpy.types.VIEW3D_MT_pose.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
