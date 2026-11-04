"""IK/FK Snap: match FK and IK chains of any rig (sidebar "Rig" tab, Pose Mode).

A chain is stored on the armature data: ordered FK and IK bone lists (paired by index), the IK
control, the pole and an optional switch property. All matching happens in pose (armature) space.
"""
import re

import bpy
from bpy_extras import anim_utils

# ---------------------------------------------------------------- data

DIRECTIONS = [('FK_TO_IK', "FK to IK", "Pose the FK bones to match the IK chain"),
              ('IK_TO_FK', "IK to FK", "Place the IK control and pole to match the FK chain")]


def _bone_list(s):
    return [n.strip() for n in s.split(",") if n.strip()]


class IKFK_PG_chain(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", default="Chain")
    fk: bpy.props.StringProperty(name="FK Bones", description="FK bones, parent first, comma separated")
    ik: bpy.props.StringProperty(name="IK Bones",
                                 description="IK bones paired with the FK bones, parent first, comma separated")
    control: bpy.props.StringProperty(name="IK Control", description="Bone the IK constraint targets")
    pole: bpy.props.StringProperty(name="Pole", description="Pole target bone (optional)")
    pole_distance: bpy.props.FloatProperty(
        name="Pole Distance", default=1.0, min=0.01,
        description="Pole offset from the mid joint, as a factor of the chain length")
    switch_bone: bpy.props.StringProperty(name="Switch Bone", description="Bone holding the IK/FK switch property")
    switch_prop: bpy.props.StringProperty(name="Switch Property", description="Custom property name, e.g. ik_fk_switch")
    ik_value: bpy.props.FloatProperty(name="IK Value", default=1.0, description="Switch value that means IK")
    fk_value: bpy.props.FloatProperty(name="FK Value", default=0.0, description="Switch value that means FK")


def active_chain(ob):
    chains, i = ob.data.ikfk_chains, ob.data.ikfk_index
    return chains[i] if 0 <= i < len(chains) else None


# ---------------------------------------------------------------- snapping core

def _key(pb, frame):
    rot = {'QUATERNION': "rotation_quaternion", 'AXIS_ANGLE': "rotation_axis_angle"}.get(pb.rotation_mode,
                                                                                          "rotation_euler")
    for path in ("location", rot, "scale"):
        pb.keyframe_insert(path, frame=frame, group=pb.name)


def _set_switch(ob, chain, value, frame, keys):
    pb = ob.pose.bones.get(chain.switch_bone)
    if not pb or not chain.switch_prop:
        return
    pb[chain.switch_prop] = value
    if keys:
        pb.keyframe_insert(f'["{bpy.utils.escape_identifier(chain.switch_prop)}"]', frame=frame)


def _bones(ob, names):
    missing = [n for n in names if n not in ob.pose.bones]
    if missing:
        raise ValueError(f"Missing bones: {', '.join(missing)}")
    return [ob.pose.bones[n] for n in names]


def snap_fk_to_ik(context, ob, chain, keys=False, switch=True):
    fk, ik = _bones(ob, _bone_list(chain.fk)), _bones(ob, _bone_list(chain.ik))
    if len(fk) != len(ik):
        raise ValueError("FK and IK lists differ in length")
    frame = context.scene.frame_current
    for f, i in zip(fk, ik):  # parents first: each set uses the parent's updated pose
        f.matrix = i.matrix.copy()
        context.view_layer.update()
        if keys:
            _key(f, frame)
    if switch:
        _set_switch(ob, chain, chain.fk_value, frame, keys)


def pole_position(chain, fk_chain):
    """Pole location (pose space) in the plane of the FK chain, on the bend side of the mid joint."""
    root, end = fk_chain[0].head, fk_chain[-1].tail
    mid = sum((b.head for b in fk_chain[1:]), root * 0) / max(len(fk_chain) - 1, 1)
    line = end - root
    proj = root + line * ((mid - root).dot(line) / max(line.length_squared, 1e-12))
    out = mid - proj
    if out.length < 1e-6:
        return None  # straight chain: bend side undefined
    length = sum(b.length for b in fk_chain)
    return mid + out.normalized() * length * chain.pole_distance


def _ik_constraint(ob, ik_bones, control):
    for pb in ik_bones:
        for c in pb.constraints:
            if c.type == 'IK' and c.target == ob and c.subtarget == control:
                return pb, c
    return None, None


def snap_ik_to_fk(context, ob, chain, keys=False, switch=True):
    fk, ik = _bones(ob, _bone_list(chain.fk)), _bones(ob, _bone_list(chain.ik))
    if len(fk) != len(ik) or not fk:
        raise ValueError("FK and IK lists differ in length")
    ctrl = _bones(ob, [chain.control])[0]
    frame = context.scene.frame_current
    # Keep the rest-pose offset between the FK end bone and the control.
    end = fk[-1]
    ctrl.matrix = end.matrix @ end.bone.matrix_local.inverted() @ ctrl.bone.matrix_local
    context.view_layer.update()
    if keys:
        _key(ctrl, frame)
    if chain.pole:
        pole = _bones(ob, [chain.pole])[0]
        owner, _ = _ik_constraint(ob, ik, chain.control)
        n = ik.index(owner) + 1 if owner else len(fk)
        start = 0
        if owner:  # only the bones the IK solver moves
            c = next(c for c in owner.constraints if c.type == 'IK')
            start = max(0, n - c.chain_count) if c.chain_count else 0
        pos = pole_position(chain, fk[start:n])
        if pos is not None:
            m = pole.matrix.copy()
            m.translation = pos
            pole.matrix = m
            context.view_layer.update()
            if keys:
                _key(pole, frame)
    if switch:
        _set_switch(ob, chain, chain.ik_value, frame, keys)


def snap(context, ob, chain, direction, keys=False, switch=True):
    (snap_fk_to_ik if direction == 'FK_TO_IK' else snap_ik_to_fk)(context, ob, chain, keys, switch)


def keyed_frames(ob, names):
    ad = ob.animation_data
    if not ad or not ad.action or not ad.action_slot:
        return []
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    paths = tuple(f'pose.bones["{bpy.utils.escape_identifier(n)}"]' for n in names)
    return sorted({round(k.co.x) for fc in (bag.fcurves if bag else ())
                   if fc.data_path.startswith(paths) for k in fc.keyframe_points})


# ---------------------------------------------------------------- auto-detect

_SUBS = [("_ik", "_fk"), ("_IK", "_FK"), ("ik_", "fk_"), ("IK_", "FK_"), (".ik", ".fk"), ("-ik", "-fk"),
         ("IK", "FK"), ("ik", "fk"), ("Ik", "Fk")]


def fk_partner(ob, ik_name, exclude):
    """FK bone for an IK bone: name substitution first, else the bone with the closest rest head+tail."""
    bones = ob.data.bones
    for a, b in _SUBS:
        if a in ik_name:
            cand = ik_name.replace(a, b)
            if cand in bones and cand not in exclude:
                return cand
    ref = bones[ik_name]
    tol = ref.length * 0.01 + 1e-4

    # ponytail: position heuristic; deform/ORG bones overlap FK, so prefer names with "fk" and
    # bones without constraints. Rename or edit the chain when it guesses wrong.
    def score(b):
        pb = ob.pose.bones[b.name]
        return ((b.head_local - ref.head_local).length + (b.tail_local - ref.tail_local).length,
                bool(pb.constraints), "fk" not in b.name.lower())

    cands = [b for b in bones if b.name not in exclude and "ik" not in b.name.lower()]
    best = min(cands, key=score, default=None)
    return best.name if best and score(best)[0] < tol else None


def _strip_ik(name):
    for a, _ in _SUBS:
        name = name.replace(a, "")
    return name


_SWITCH = re.compile(r"ik.?fk|fk.?ik|switch", re.I)


def _same_side(a, b):
    # ponytail: compares 2-char side affixes (".L", "_l", "L_"); covers common naming only
    return a[-2:] == b[-2:] or a[:2] == b[:2]


def find_switch(ob, chain_bones):
    pbs = ob.pose.bones
    order = [pbs[n] for n in chain_bones] + [pb for pb in pbs if _same_side(pb.name, chain_bones[-1])]
    for pb in order:
        for k in pb.keys():
            if _SWITCH.search(k) and isinstance(pb[k], (int, float)):
                return pb.name, k
    return "", ""


def detect_chains(ob):
    """[(dict of chain fields)] for every IK constraint targeting a bone of this armature."""
    found = []
    for pb in ob.pose.bones:
        for c in pb.constraints:
            if c.type != 'IK' or c.target != ob or not c.subtarget:
                continue
            ik, b = [], pb
            while b and (not c.chain_count or len(ik) < c.chain_count):
                ik.insert(0, b.name)
                b = b.parent
            pole = c.pole_subtarget if c.pole_target == ob else ""
            excl = set(ik) | {c.subtarget, pole}
            # Extend past the owner with a child (e.g. hand_ik) or the control itself, if it has an FK partner.
            for nxt in [ch.name for ch in pb.children if ch.name not in excl] + [c.subtarget]:
                if fk_partner(ob, nxt, excl | {nxt}):
                    ik.append(nxt)
                    break
            fk = []
            for n in ik:
                p = fk_partner(ob, n, excl | set(fk))
                if not p:
                    break
                fk.append(p)
            if len(fk) != len(ik):
                continue
            sw_bone, sw_prop = find_switch(ob, [c.subtarget] + ik + fk)
            found.append(dict(name=_strip_ik(ik[0]), fk=", ".join(fk), ik=", ".join(ik), control=c.subtarget,
                              pole=pole, switch_bone=sw_bone, switch_prop=sw_prop))
    return found


def add_chain(ob, **fields):
    ch = ob.data.ikfk_chains.add()
    for k, v in fields.items():
        setattr(ch, k, v)
    ob.data.ikfk_index = len(ob.data.ikfk_chains) - 1
    return ch


def mirror_chain(ob, chain):
    flip = bpy.utils.flip_name
    fields = {k: getattr(chain, k) for k in chain.bl_rna.properties.keys() if k != "rna_type"}
    for k in ("name", "control", "pole", "switch_bone"):
        fields[k] = flip(fields[k])
    for k in ("fk", "ik"):
        fields[k] = ", ".join(flip(n) for n in _bone_list(fields[k]))
    existing = next((c for c in ob.data.ikfk_chains if c.name == fields["name"]), None)
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        return existing
    return add_chain(ob, **fields)


# ---------------------------------------------------------------- operators

class _ArmOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'ARMATURE'


class _ChainOp(_ArmOp):
    @classmethod
    def poll(cls, context):
        return super().poll(context) and active_chain(context.object) is not None


class IKFK_OT_snap(_ChainOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.snap"
    bl_label = "Snap"
    bl_description = "Snap the active chain FK to IK or IK to FK at the current frame"

    direction: bpy.props.EnumProperty(name="Direction", items=DIRECTIONS)
    insert_keys: bpy.props.BoolProperty(name="Insert Keys", default=False)
    set_switch: bpy.props.BoolProperty(name="Set Switch", default=True,
                                       description="Also set the switch property to the snapped mode")

    def execute(self, context):
        ob = context.object
        try:
            snap(context, ob, active_chain(ob), self.direction, self.insert_keys, self.set_switch)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class IKFK_OT_snap_range(_ChainOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.snap_range"
    bl_label = "Snap over Range"
    bl_description = "Snap and key the active chain on every frame of a range (or only on existing keys)"

    direction: bpy.props.EnumProperty(name="Direction", items=DIRECTIONS)
    frame_start: bpy.props.IntProperty(name="Start")
    frame_end: bpy.props.IntProperty(name="End")
    only_keyed: bpy.props.BoolProperty(name="Only Keyed Frames", default=False,
                                       description="Snap only on frames where the chain already has keys")
    set_switch: bpy.props.BoolProperty(name="Key Switch", default=True)

    def invoke(self, context, event):
        self.frame_start, self.frame_end = context.scene.frame_start, context.scene.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob, sc = context.object, context.scene
        chain = active_chain(ob)
        lo, hi = sorted((self.frame_start, self.frame_end))
        if self.only_keyed:
            names = _bone_list(chain.fk) + _bone_list(chain.ik) + [chain.control, chain.pole]
            frames = [f for f in keyed_frames(ob, [n for n in names if n]) if lo <= f <= hi]
        else:
            frames = range(lo, hi + 1)
        old = sc.frame_current
        n = 0
        try:
            for f in frames:
                sc.frame_set(f)
                snap(context, ob, chain, self.direction, True, self.set_switch)
                n += 1
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            sc.frame_set(old)
        self.report({'INFO'}, f"Snapped {n} frames")
        return {'FINISHED'}


class IKFK_OT_detect(_ArmOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.detect"
    bl_label = "Auto-Detect Chains"
    bl_description = ("Find bones with IK constraints and pair their chains with FK bones "
                      "(by ik/fk name or by matching position)")

    def execute(self, context):
        ob = context.object
        known = {c.control for c in ob.data.ikfk_chains}
        new = [d for d in detect_chains(ob) if d["control"] not in known]
        for d in new:
            add_chain(ob, **d)
        self.report({'INFO'}, f"Found {len(new)} new chains")
        return {'FINISHED'}


class IKFK_OT_add(_ArmOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.add"
    bl_label = "Add Chain"
    bl_description = "Add an empty chain definition"

    def execute(self, context):
        add_chain(context.object)
        return {'FINISHED'}


class IKFK_OT_remove(_ChainOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.remove"
    bl_label = "Remove Chain"
    bl_description = "Remove the active chain definition (bones are not touched)"

    def execute(self, context):
        arm = context.object.data
        arm.ikfk_chains.remove(arm.ikfk_index)
        arm.ikfk_index = min(arm.ikfk_index, len(arm.ikfk_chains) - 1)
        return {'FINISHED'}


class IKFK_OT_mirror(_ChainOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.mirror"
    bl_label = "Mirror Chain"
    bl_description = "Create (or update) the opposite side chain by flipping all bone names (.L <-> .R)"

    def execute(self, context):
        ob = context.object
        ch = mirror_chain(ob, active_chain(ob))
        names = _bone_list(ch.fk) + _bone_list(ch.ik) + [ch.control, ch.pole, ch.switch_bone]
        missing = [n for n in names if n and n not in ob.data.bones]
        if missing:
            self.report({'WARNING'}, f"Mirrored, but missing bones: {', '.join(missing)}")
        return {'FINISHED'}


class IKFK_OT_assign(_ChainOp, bpy.types.Operator):
    bl_idname = "ikfk_snap.assign"
    bl_label = "Assign Selected"
    bl_description = "Fill the FK or IK list of the active chain with the selected bones (parents first)"

    side: bpy.props.EnumProperty(items=[('FK', "FK", ""), ('IK', "IK", "")])

    def execute(self, context):
        ob = context.object
        sel = sorted((pb for pb in ob.pose.bones if pb.select), key=lambda pb: len(pb.parent_recursive))
        setattr(active_chain(ob), self.side.lower(), ", ".join(pb.name for pb in sel))
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class IKFK_PT_panel(bpy.types.Panel):
    bl_label = "IK/FK Snap"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rig"

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.object and context.object.type == 'ARMATURE'

    def draw(self, context):
        ob, layout = context.object, self.layout
        arm = ob.data
        row = layout.row()
        row.template_list("UI_UL_list", "ikfk_chains", arm, "ikfk_chains", arm, "ikfk_index", rows=3)
        col = row.column(align=True)
        col.operator("ikfk_snap.detect", text="", icon='VIEWZOOM')
        col.operator("ikfk_snap.add", text="", icon='ADD')
        col.operator("ikfk_snap.remove", text="", icon='REMOVE')
        col.operator("ikfk_snap.mirror", text="", icon='MOD_MIRROR')
        ch = active_chain(ob)
        if not ch:
            return
        keys = context.scene.tool_settings.use_keyframe_insert_auto
        row = layout.row(align=True)
        row.scale_y = 1.4
        for d, label in (('FK_TO_IK', "FK → IK"), ('IK_TO_FK', "IK → FK")):
            op = row.operator("ikfk_snap.snap", text=label)
            op.direction, op.insert_keys = d, keys
        row = layout.row(align=True)
        for d, label in (('FK_TO_IK', "Bake FK → IK"), ('IK_TO_FK', "Bake IK → FK")):
            row.operator("ikfk_snap.snap_range", text=label, icon='KEYFRAME').direction = d
        box = layout.box()
        box.prop(ch, "name")
        for side in ("fk", "ik"):
            r = box.row(align=True)
            r.prop(ch, side)
            r.operator("ikfk_snap.assign", text="", icon='RESTRICT_SELECT_OFF').side = side.upper()
        box.prop_search(ch, "control", arm, "bones")
        box.prop_search(ch, "pole", arm, "bones")
        box.prop(ch, "pole_distance")
        box.prop_search(ch, "switch_bone", arm, "bones")
        box.prop(ch, "switch_prop")
        r = box.row(align=True)
        r.prop(ch, "ik_value")
        r.prop(ch, "fk_value")


_classes = (IKFK_PG_chain, IKFK_OT_snap, IKFK_OT_snap_range, IKFK_OT_detect, IKFK_OT_add, IKFK_OT_remove,
            IKFK_OT_mirror, IKFK_OT_assign, IKFK_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Armature.ikfk_chains = bpy.props.CollectionProperty(type=IKFK_PG_chain)
    bpy.types.Armature.ikfk_index = bpy.props.IntProperty(default=0)


def unregister():
    del bpy.types.Armature.ikfk_chains
    del bpy.types.Armature.ikfk_index
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
