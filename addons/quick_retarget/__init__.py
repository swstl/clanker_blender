"""Quick Retarget: copy animation from one armature to another with a name-based bone map.

Each frame is sampled in world space and written as keys on the target (no constraints, no bake
operator). Per mapped bone the rotation is transferred one of two ways:
  ALIGN  - target bone points where the source bone points (handles T-pose vs A-pose rigs);
  OFFSET - target gets the source's world rotation change since rest (keeps rest differences,
           right for control bones like Rigify "torso" whose axis differs from the source hips).
The location bone (hips) is moved by the source's world offset from rest, scaled by the ratio of
the two rigs' hip heights (leg-length ratio).
"""
import json
import re

import bpy
from bpy_extras.anim_utils import action_ensure_channelbag_for_slot, action_get_channelbag_for_slot
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

Y = Vector((0, 1, 0))
ROT_MODES = [
    ('ALIGN', "Align", "Point the target bone where the source bone points (different rest poses)"),
    ('OFFSET', "Offset", "Apply the source's rotation change since rest (same rest pose, any bone axes)"),
    ('NONE', "None", "Do not transfer rotation"),
]

# ---------------------------------------------------------------- name matching

_ALIASES = {
    'hips': 'hips pelvis hip',
    'spine': 'spine spine0 abdomen',
    'spine1': 'spine1 spine01',
    'chest': 'chest spine2 spine02 upperchest spine3',
    'neck': 'neck neck1 neck01',
    'head': 'head',
    'shoulder': 'shoulder clavicle collar collarbone',
    'upperarm': 'upperarm arm uparm',
    'forearm': 'forearm lowerarm elbow',
    'hand': 'hand wrist',
    'thigh': 'thigh upleg upperleg',
    'shin': 'shin leg calf lowerleg knee',
    'foot': 'foot ankle',
    'toe': 'toe toebase toes ball',
}
SYNONYMS = {alias: canon for canon, s in _ALIASES.items() for alias in s.split()}
CENTRAL = {'hips', 'spine', 'spine1', 'chest', 'neck', 'head'}
_DROP = {'mixamorig', 'def', 'org', 'mch', 'bip', 'bip01', 'fk', 'ctrl', 'jnt', 'bn', 'f', 'b'}
_FINGERS = 'thumb|index|middle|ring|pinky|little'


def bone_key(name):
    """('upperarm', 'L') style key for a bone name, or None for end/helper bones."""
    name = name.split(':')[-1]
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', name).lower()
    tokens = [t for t in re.split(r'[^a-z0-9]+', name) if t]
    side = ''
    rest = []
    for t in tokens:
        if t in ('l', 'left'):
            side = 'L'
        elif t in ('r', 'right'):
            side = 'R'
        elif t not in _DROP:
            rest.append(t)
    base = ''.join(rest)
    if not base or base.endswith('end') or 'nub' in base:
        return None
    base = re.sub(r'(?<=\D)0+(?=\d)', '', base)  # spine.001 -> spine1, thumb.01 -> thumb1
    base = re.sub(rf'^hand(?={_FINGERS})', '', base).replace('little', 'pinky')
    return SYNONYMS.get(base, base), side


def _prefix_rank(name):
    low = name.split(':')[-1].lower()
    for i, p in enumerate(('org', 'def', 'mch'), 1):
        if low.startswith(p + '-') or low.startswith(p + '_'):
            return i
    return 0


def auto_map(src_arm, tgt_arm):
    """Match source bones to target bones by normalized name. Returns [(src, tgt, use_loc, mode)]."""
    by_key = {}
    for b in sorted(tgt_arm.data.bones, key=lambda b: _prefix_rank(b.name)):  # controls before ORG/DEF
        k = bone_key(b.name)
        if k:
            by_key.setdefault(k, b.name)
    pairs = []
    used = set()
    for b in src_arm.data.bones:
        k = bone_key(b.name)
        tgt = by_key.get(k) if k else None
        if tgt and tgt not in used:
            used.add(tgt)
            root = k[0] == 'hips' or b.parent is None
            pairs.append((b.name, tgt, root, 'OFFSET' if k[0] in CENTRAL or root else 'ALIGN'))
    if pairs and not any(p[2] for p in pairs):
        pairs[0] = (pairs[0][0], pairs[0][1], True, pairs[0][3])
    return pairs


# Mixamo -> generated Rigify rig (FK controls). Target candidates: first existing wins.
_RIGIFY = [
    ('Hips', ('torso',), True, 'OFFSET'),
    ('Spine2', ('chest',), False, 'OFFSET'),
    ('Neck', ('neck',), False, 'OFFSET'),
    ('Head', ('head',), False, 'OFFSET'),
    ('{S}Shoulder', ('shoulder.{s}',), False, 'ALIGN'),
    ('{S}Arm', ('upper_arm_fk.{s}',), False, 'ALIGN'),
    ('{S}ForeArm', ('forearm_fk.{s}',), False, 'ALIGN'),
    ('{S}Hand', ('hand_fk.{s}',), False, 'ALIGN'),
    ('{S}UpLeg', ('thigh_fk.{s}',), False, 'ALIGN'),
    ('{S}Leg', ('shin_fk.{s}',), False, 'ALIGN'),
    ('{S}Foot', ('foot_fk.{s}',), False, 'ALIGN'),
    ('{S}ToeBase', ('toe_fk.{s}', 'toe.{s}'), False, 'ALIGN'),
] + [(f'{{S}}Hand{m}{i}', (f'{r}.0{i}.{{s}}',), False, 'ALIGN')
     for m, r in (('Thumb', 'thumb'), ('Index', 'f_index'), ('Middle', 'f_middle'),
                  ('Ring', 'f_ring'), ('Pinky', 'f_pinky')) for i in (1, 2, 3)]


def rigify_map(src_arm, tgt_arm):
    """Mixamo -> Rigify control map, limited to bones present on both rigs."""
    src = {b.name.split(':')[-1]: b.name for b in src_arm.data.bones}
    tgt = tgt_arm.data.bones
    pairs = []
    for s_pat, t_pats, loc, mode in _RIGIFY:
        for S, s in (('Left', 'L'), ('Right', 'R')) if '{S}' in s_pat else (('', ''),):
            sname = src.get(s_pat.format(S=S))
            tname = next((t.format(s=s) for t in t_pats if t.format(s=s) in tgt), None)
            if sname and tname:
                pairs.append((sname, tname, loc, mode))
    return pairs


# ---------------------------------------------------------------- retarget core

def _hier(bones):
    out = []
    stack = [b for b in bones if b.parent is None][::-1]
    while stack:
        b = stack.pop()
        out.append(b)
        stack.extend(reversed(b.children))
    return out


def source_slot(action, src):
    """Slot of `action` that animates bones of armature `src`, or None."""
    names = src.data.bones.keys()
    for slot in action.slots:
        if slot.target_id_type not in ('OBJECT', 'UNSPECIFIED'):
            continue
        cb = action_get_channelbag_for_slot(action, slot)
        for fc in (cb.fcurves if cb else ()):
            m = re.match(r'pose\.bones\["(.+?)"\]', fc.data_path)
            if m and m.group(1) in names:
                return slot
    return None


def source_actions(src):
    return [a for a in bpy.data.actions if source_slot(a, src)]


def _hip_scale(src, tgt, pairs):
    for s, t, loc, _ in pairs:
        if loc:
            hs = (src.matrix_world @ src.data.bones[s].head_local).z
            ht = (tgt.matrix_world @ tgt.data.bones[t].head_local).z
            if abs(hs) > 1e-6 and abs(ht) > 1e-6:
                return ht / hs
    return 1.0


def retarget(scene, src, tgt, pairs, actions=None, auto_scale=True, scale=1.0):
    """Bake each source action onto `tgt` as a new action "<tgt>_<action>". Returns the new actions."""
    pairs = [p for p in pairs if p[0] in src.data.bones and p[1] in tgt.data.bones]
    if not pairs:
        raise ValueError("No valid bone pairs")
    sad = src.animation_data_create()
    old = (sad.action, sad.action_slot if sad.action else None, sad.use_nla, scene.frame_current)
    if actions is None:
        actions = [sad.action] if sad.action else []
    if not actions:
        raise ValueError("Source armature has no action")
    if auto_scale:
        scale *= _hip_scale(src, tgt, pairs)
    by_tgt = {t: (s, loc, mode) for s, t, loc, mode in pairs}
    order = _hier(tgt.data.bones)
    tad = tgt.animation_data_create()
    made = []
    try:
        sad.use_nla = False  # evaluate only the action being retargeted
        for act in actions:
            sad.action = act
            slot = source_slot(act, src) or (act.slots[0] if act.slots else None)
            if slot:
                sad.action_slot = slot
            made.append(_bake(scene, src, tgt, by_tgt, order, act, scale))
            tad.action = made[-1]
            tad.action_slot = made[-1].slots[0]
    finally:
        sad.action = old[0]
        if old[1]:
            sad.action_slot = old[1]
        sad.use_nla = old[2]
        scene.frame_set(old[3])
    return made


def _bake(scene, src, tgt, by_tgt, order, act, scale):
    f0, f1 = (int(round(f)) for f in act.frame_range)
    keys = {}  # (path, index) -> [x, y, x, y, ...]
    prev = {}
    for f in range(f0, f1 + 1):
        scene.frame_set(f)
        smw, tmw = src.matrix_world, tgt.matrix_world
        tmw_inv, trot_inv = tmw.inverted(), tmw.to_quaternion().inverted()
        pose = {}
        for b in order:
            pb = tgt.pose.bones[b.name]
            par = {} if b.parent is None else {
                'parent_matrix': pose[b.parent.name], 'parent_matrix_local': b.parent.matrix_local}
            if b.name not in by_tgt:
                pose[b.name] = b.convert_local_to_pose(pb.matrix_basis, b.matrix_local, **par)
                continue
            sname, use_loc, mode = by_tgt[b.name]
            sb, spb = src.data.bones[sname], src.pose.bones[sname]
            natural = b.convert_local_to_pose(Matrix.Identity(4), b.matrix_local, **par)
            s_rest = (smw @ sb.matrix_local).to_quaternion()
            s_now = (smw @ spb.matrix).to_quaternion()
            t_rest = (tmw @ b.matrix_local).to_quaternion()
            if mode == 'ALIGN':
                c = s_rest.inverted() @ t_rest
                rot = s_now @ (c @ Y).rotation_difference(Y) @ c
            elif mode == 'OFFSET':
                rot = s_now @ s_rest.inverted() @ t_rest
            else:
                rot = tmw.to_quaternion() @ natural.to_quaternion()
            loc = natural.translation
            if use_loc:
                delta = (smw @ spb.head) - (smw @ sb.head_local)
                loc = tmw_inv @ ((tmw @ b.head_local) + delta * scale)
            m = Matrix.LocRotScale(loc, trot_inv @ rot, None)
            pose[b.name] = m
            basis = b.convert_local_to_pose(m, b.matrix_local, invert=True, **par)
            _record(keys, prev, pb, basis, use_loc, f)
    new = bpy.data.actions.new(f"{tgt.name}_{act.name}")
    new.use_fake_user = True
    slot = new.slots.new(id_type='OBJECT', name=tgt.name)
    cb = action_ensure_channelbag_for_slot(new, slot)
    for (path, idx, group), co in keys.items():
        fc = cb.fcurves.new(path, index=idx, group_name=group)
        fc.keyframe_points.add(len(co) // 2)
        fc.keyframe_points.foreach_set('co', co)
        fc.update()
    return new


def _record(keys, prev, pb, basis, use_loc, f):
    base = f'pose.bones["{bpy.utils.escape_identifier(pb.name)}"].'
    q = basis.to_quaternion()
    mode = pb.rotation_mode
    if mode == 'QUATERNION':
        if pb.name in prev:
            q.make_compatible(prev[pb.name])
        prev[pb.name] = q
        vals = ('rotation_quaternion', q)
    elif mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        vals = ('rotation_axis_angle', (angle, *axis))
    else:
        e = q.to_euler(mode, prev[pb.name]) if pb.name in prev else q.to_euler(mode)
        prev[pb.name] = e
        vals = ('rotation_euler', e)
    chans = [vals] + ([('location', basis.translation)] if use_loc else [])
    for prop, v in chans:
        for i, x in enumerate(v):
            keys.setdefault((base + prop, i, pb.name), []).extend((f, x))


# ---------------------------------------------------------------- data

def _is_arm(self, ob):
    return ob.type == 'ARMATURE'


class QRT_BoneMapItem(bpy.types.PropertyGroup):
    source: bpy.props.StringProperty(name="Source", description="Source bone")
    target: bpy.props.StringProperty(name="Target", description="Target bone")
    use_location: bpy.props.BoolProperty(name="Location", description="Also transfer location (root/hips)")
    rotation: bpy.props.EnumProperty(name="Rotation", items=ROT_MODES, default='ALIGN')


class QRT_Settings(bpy.types.PropertyGroup):
    source: bpy.props.PointerProperty(type=bpy.types.Object, poll=_is_arm, name="Source",
                                      description="Armature with the animation")
    target: bpy.props.PointerProperty(type=bpy.types.Object, poll=_is_arm, name="Target",
                                      description="Armature that receives the animation")
    bone_map: bpy.props.CollectionProperty(type=QRT_BoneMapItem)
    active: bpy.props.IntProperty()
    batch: bpy.props.BoolProperty(name="All Source Actions",
                                  description="Retarget every action that animates the source armature")
    auto_scale: bpy.props.BoolProperty(name="Auto Scale", default=True,
                                       description="Scale root motion by the hip height ratio of the rigs")
    scale: bpy.props.FloatProperty(name="Scale", default=1.0, min=0.0,
                                   description="Extra multiplier on root motion")


def _settings(context):
    return context.scene.quick_retarget


def _pairs(st):
    return [(i.source, i.target, i.use_location, i.rotation) for i in st.bone_map]


def _set_map(st, pairs):
    st.bone_map.clear()
    for s, t, loc, mode in pairs:
        it = st.bone_map.add()
        it.source, it.target, it.use_location, it.rotation = s, t, loc, mode


class _NeedsRigs:
    @classmethod
    def poll(cls, context):
        st = _settings(context)
        return st.source and st.target and st.source != st.target


class QRT_OT_auto_map(_NeedsRigs, bpy.types.Operator):
    bl_idname = "quick_retarget.auto_map"
    bl_label = "Auto Map"
    bl_description = "Fill the bone map by matching normalized bone names and humanoid synonyms"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = _settings(context)
        _set_map(st, auto_map(st.source, st.target))
        self.report({'INFO'}, f"Mapped {len(st.bone_map)} bones")
        return {'FINISHED'}


class QRT_OT_rigify_map(_NeedsRigs, bpy.types.Operator):
    bl_idname = "quick_retarget.rigify_map"
    bl_label = "Mixamo to Rigify"
    bl_description = "Map Mixamo bones to generated Rigify FK controls (set IK/FK switches to FK)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = _settings(context)
        _set_map(st, rigify_map(st.source, st.target))
        self.report({'INFO'}, f"Mapped {len(st.bone_map)} bones")
        return {'FINISHED'}


class QRT_OT_add(bpy.types.Operator):
    bl_idname = "quick_retarget.add"
    bl_label = "Add Pair"
    bl_description = "Add a bone pair to the map"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = _settings(context)
        st.bone_map.add()
        st.active = len(st.bone_map) - 1
        return {'FINISHED'}


class QRT_OT_remove(bpy.types.Operator):
    bl_idname = "quick_retarget.remove"
    bl_label = "Remove Pair"
    bl_description = "Remove the active bone pair"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = _settings(context)
        if 0 <= st.active < len(st.bone_map):
            st.bone_map.remove(st.active)
            st.active = min(st.active, len(st.bone_map) - 1)
        return {'FINISHED'}


class QRT_OT_save(bpy.types.Operator, ExportHelper):
    bl_idname = "quick_retarget.save_map"
    bl_label = "Save Map"
    bl_description = "Save the bone map as a JSON preset"
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        data = [dict(source=s, target=t, location=l, rotation=r) for s, t, l, r in _pairs(_settings(context))]
        with open(self.filepath, 'w', encoding='utf-8') as fh:
            json.dump({"bone_map": data}, fh, indent=1)
        return {'FINISHED'}


class QRT_OT_load(bpy.types.Operator, ImportHelper):
    bl_idname = "quick_retarget.load_map"
    bl_label = "Load Map"
    bl_description = "Load a bone map from a JSON preset"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    def execute(self, context):
        try:
            with open(self.filepath, encoding='utf-8') as fh:
                data = json.load(fh)["bone_map"]
            pairs = [(d["source"], d["target"], bool(d.get("location")), d.get("rotation", 'ALIGN'))
                     for d in data]
        except (OSError, ValueError, KeyError, TypeError) as e:
            self.report({'ERROR'}, f"Bad preset: {e}")
            return {'CANCELLED'}
        modes = {m[0] for m in ROT_MODES}
        _set_map(_settings(context), [(s, t, l, r if r in modes else 'ALIGN') for s, t, l, r in pairs])
        return {'FINISHED'}


class QRT_OT_retarget(_NeedsRigs, bpy.types.Operator):
    bl_idname = "quick_retarget.retarget"
    bl_label = "Retarget"
    bl_description = "Bake the source animation onto the target as new action(s) named <target>_<action>"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = _settings(context)
        acts = source_actions(st.source) if st.batch else None
        try:
            made = retarget(context.scene, st.source, st.target, _pairs(st), acts, st.auto_scale, st.scale)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, "Created " + ", ".join(a.name for a in made))
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class QRT_UL_map(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index=0):
        row = layout.row(align=True)
        if data.source:
            row.prop_search(item, "source", data.source.data, "bones", text="")
        else:
            row.prop(item, "source", text="")
        if data.target:
            row.prop_search(item, "target", data.target.data, "bones", text="")
        else:
            row.prop(item, "target", text="")
        row.prop(item, "use_location", text="", icon='ORIENTATION_GLOBAL')
        row.prop(item, "rotation", text="")


class QRT_PT_panel(bpy.types.Panel):
    bl_label = "Quick Retarget"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Retarget"

    def draw(self, context):
        st = _settings(context)
        col = self.layout.column()
        col.prop(st, "source")
        col.prop(st, "target")
        row = col.row(align=True)
        row.operator("quick_retarget.auto_map", icon='LINKED')
        row.operator("quick_retarget.rigify_map")
        row = col.row()
        row.template_list("QRT_UL_map", "", st, "bone_map", st, "active", rows=6)
        side = row.column(align=True)
        side.operator("quick_retarget.add", text="", icon='ADD')
        side.operator("quick_retarget.remove", text="", icon='REMOVE')
        side.separator()
        side.operator("quick_retarget.load_map", text="", icon='FILEBROWSER')
        side.operator("quick_retarget.save_map", text="", icon='FILE_TICK')
        row = col.row(align=True)
        row.prop(st, "auto_scale")
        row.prop(st, "scale")
        col.prop(st, "batch")
        col.operator("quick_retarget.retarget", icon='ARMATURE_DATA')


classes = (QRT_BoneMapItem, QRT_Settings, QRT_OT_auto_map, QRT_OT_rigify_map, QRT_OT_add,
           QRT_OT_remove, QRT_OT_save, QRT_OT_load, QRT_OT_retarget, QRT_UL_map, QRT_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.quick_retarget = bpy.props.PointerProperty(type=QRT_Settings)


def unregister():
    del bpy.types.Scene.quick_retarget
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
