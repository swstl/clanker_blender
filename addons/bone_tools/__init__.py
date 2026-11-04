"""Bone Tools: rigging chores in the 3D View sidebar "Rig" tab.

Bone renames go through RNA (`bone.name = ...`), which already renames vertex groups on
skinned meshes, constraint subtargets, drivers and F-Curve paths, so naming tools only rename.
"""
import re

import bmesh
import bpy

# ---------------------------------------------------------------- side naming

_SIDE = r"(L|R|l|r|Left|Right|left|right)"
_SUFFIX = re.compile(r"^(.+?)([._\- ])" + _SIDE + "$")           # Arm.L  Arm_l  Arm-Left
_PREFIX = re.compile("^" + _SIDE + r"[._\- ](.+)$")               # L_Arm  Left_Arm
_WORD_PRE = re.compile(r"^(Left|Right)([A-Z0-9].*)$")             # LeftArm (Mixamo)
_WORD_SUF = re.compile(r"^(.*[a-z0-9])(Left|Right)$")             # ArmLeft
_NUM = re.compile(r"\.\d{3}$")                                     # Blender's .001 dupes
_WORD = {'L': "Left", 'R': "Right"}

STYLES = [
    ('DOT', "Arm.L", "Dot suffix (Blender mirror default)"),
    ('UNDER', "Arm_L", "Underscore, upper case side"),
    ('UNDER_LOWER', "Arm_l", "Underscore, lower case side"),
    ('PREFIX', "L_Arm", "Side letter prefix"),
    ('WORD_PREFIX', "LeftArm", "Left/Right word prefix (Mixamo style)"),
    ('WORD_SUFFIX', "ArmLeft", "Left/Right word suffix"),
]
_FORMAT = {
    'DOT': lambda b, s: f"{b}.{s}",
    'UNDER': lambda b, s: f"{b}_{s}",
    'UNDER_LOWER': lambda b, s: f"{b}_{s.lower()}",
    'PREFIX': lambda b, s: f"{s}_{b}",
    'WORD_PREFIX': lambda b, s: f"{_WORD[s]}{b[:1].upper()}{b[1:]}",
    'WORD_SUFFIX': lambda b, s: f"{b}{_WORD[s]}",
}


def split_side(name):
    """'Arm.L' / 'L_Arm' / 'LeftArm' -> ('Arm', 'L'). No side found -> (name, None)."""
    for rx, bi, si in ((_SUFFIX, 1, 3), (_PREFIX, 2, 1), (_WORD_PRE, 2, 1), (_WORD_SUF, 1, 2)):
        m = rx.match(name)
        if m:
            return m.group(bi), m.group(si)[0].upper()
    return name, None


def convert_name(name, style, strip_namespace=False):
    """Convert the side marker of a bone name; keeps 'ns:' namespaces and '.001' numbers."""
    ns, sep, rest = name.rpartition(":")
    m = _NUM.search(rest)
    num = m.group() if m else ""
    core = rest[:len(rest) - len(num)]
    base, side = split_side(core)
    new = _FORMAT[style](base, side) if side else core
    return ("" if strip_namespace else ns + sep) + new + num


# ---------------------------------------------------------------- bone helpers

def bone_coll(ob):
    """Bones of the current mode: edit bones in Edit Mode, else Bone data."""
    return ob.data.edit_bones if ob.mode == 'EDIT' else ob.data.bones


def selected_names(ob):
    # 5.0+: selection lives on edit bones / pose bones, Bone has no select flag.
    if ob.mode == 'EDIT':
        return [b.name for b in ob.data.edit_bones if b.select]
    return [pb.name for pb in ob.pose.bones if pb.select]


def set_select(ob, name, state):
    if ob.mode == 'EDIT':
        b = ob.data.edit_bones[name]
        b.select = b.select_head = b.select_tail = state
    else:
        ob.pose.bones[name].select = state


def target_names(ob, only_selected):
    return selected_names(ob) if only_selected else [b.name for b in bone_coll(ob)]


def rename_bones(ob, names, fn):
    coll, n = bone_coll(ob), 0
    for name in names:
        new = fn(name)
        if new and new != name:
            coll[name].name = new
            n += 1
    return n


def bound_meshes(ob):
    return [o for o in bpy.data.objects if o.type == 'MESH'
            and any(m.type == 'ARMATURE' and m.object == ob for m in o.modifiers)]


def unbound_bones(ob):
    """Deform bones with no same-named vertex group on any skinned mesh."""
    groups = {g.name for m in bound_meshes(ob) for g in m.vertex_groups}
    return [b.name for b in bone_coll(ob) if b.use_deform and b.name not in groups]


def orphan_groups(ob):
    """(mesh name, group name) for vertex groups that match no bone."""
    bones = {b.name for b in bone_coll(ob)}
    return [(m.name, g.name) for m in bound_meshes(ob) for g in m.vertex_groups if g.name not in bones]


class _ArmatureOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'ARMATURE'


# ---------------------------------------------------------------- naming ops

class BT_OT_convert_sides(_ArmatureOp, bpy.types.Operator):
    bl_idname = "bone_tools.convert_sides"
    bl_label = "Fix Side Naming"
    bl_description = ("Convert left/right markers in bone names to one convention. "
                      "Vertex groups, constraints and drivers follow the rename")

    style: bpy.props.EnumProperty(name="Style", items=STYLES)
    only_selected: bpy.props.BoolProperty(name="Only Selected", default=False)
    strip_namespace: bpy.props.BoolProperty(
        name="Strip Namespace", default=False, description="Drop 'mixamorig:' style prefixes")

    def execute(self, context):
        ob = context.object
        n = rename_bones(ob, target_names(ob, self.only_selected),
                         lambda s: convert_name(s, self.style, self.strip_namespace))
        self.report({'INFO'}, f"Renamed {n} bones")
        return {'FINISHED'}


class BT_OT_strip_prefix(_ArmatureOp, bpy.types.Operator):
    bl_idname = "bone_tools.strip_prefix"
    bl_label = "Strip Prefix"
    bl_description = "Remove a prefix such as 'mixamorig:' or 'DEF-' from bone names (vertex groups follow)"

    prefix: bpy.props.StringProperty(name="Prefix", default="mixamorig:")
    only_selected: bpy.props.BoolProperty(name="Only Selected", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob, p = context.object, self.prefix
        n = rename_bones(ob, target_names(ob, self.only_selected),
                         lambda s: s[len(p):] if p and s.startswith(p) else None)
        self.report({'INFO'}, f"Renamed {n} bones")
        return {'FINISHED'}


# ---------------------------------------------------------------- bone chains

def ordered_path(bm):
    """Selected vertices in path order, walking selected edges from an end (or the active vertex).
    A closed loop repeats its start vertex at the end. Without edges: vertex click order."""
    adj = {}
    for e in bm.edges:
        if e.select:
            a, b = e.verts
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
    if not adj:
        return [v for v in bm.select_history if isinstance(v, bmesh.types.BMVert)]
    ends = [v for v, nb in adj.items() if len(nb) == 1]
    act = bm.select_history.active
    if act in adj and (act in ends or not ends):
        start = act
    else:
        start = ends[0] if ends else next(iter(adj))
    path, seen, cur = [start], {start}, start
    while True:
        nxt = [v for v in adj[cur] if v not in seen]  # ponytail: branches take the first edge
        if not nxt:
            if len(path) > 2 and start in adj[cur]:
                path.append(start)
            return path
        cur = nxt[0]
        path.append(cur)
        seen.add(cur)


def resample(pts, n):
    """n + 1 points evenly spaced by arc length along a polyline."""
    seg = [(b - a).length for a, b in zip(pts, pts[1:])]
    total, out, i, acc = sum(seg), [], 0, 0.0
    for k in range(n + 1):
        t = total * k / n
        while i < len(seg) - 1 and acc + seg[i] < t:
            acc += seg[i]
            i += 1
        f = (t - acc) / seg[i] if seg[i] else 0.0
        out.append(pts[i].lerp(pts[i + 1], min(max(f, 0.0), 1.0)))
    return out


def make_chain(context, pts, name, arm_ob=None):
    """Connected bone chain through world-space points; new armature unless one is given."""
    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    if arm_ob is None:
        arm_ob = bpy.data.objects.new(name, bpy.data.armatures.new(name))
        context.collection.objects.link(arm_ob)
        context.view_layer.update()
    for o in context.selected_objects:
        o.select_set(False)
    arm_ob.select_set(True)
    context.view_layer.objects.active = arm_ob
    bpy.ops.object.mode_set(mode='EDIT')
    inv, parent, i = arm_ob.matrix_world.inverted(), None, 0
    for a, b in zip(pts, pts[1:]):
        if (b - a).length < 1e-6:
            continue
        i += 1
        eb = arm_ob.data.edit_bones.new(f"{name}_{i:02d}")
        eb.head, eb.tail = inv @ a, inv @ b
        if parent:
            eb.parent, eb.use_connect = parent, True
        parent = eb
    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_ob


def _selected_armature(context):
    return next((o for o in context.selected_objects if o.type == 'ARMATURE'), None)


class BT_OT_chain_from_mesh(bpy.types.Operator):
    bl_idname = "bone_tools.chain_from_mesh"
    bl_label = "Bone Chain from Path"
    bl_description = ("Build a bone chain through the selected edge path (one bone per edge). "
                      "Adds to a selected armature, otherwise creates one")
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="Chain")

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and context.object.mode == 'EDIT'

    def execute(self, context):
        ob = context.object
        mw = ob.matrix_world
        bm = bmesh.from_edit_mesh(ob.data)  # keep a reference: its BMVerts die with the wrapper
        pts = [mw @ v.co for v in ordered_path(bm)]
        if len(pts) < 2:
            self.report({'ERROR'}, "Select a path of connected edges (or click 2+ vertices in order)")
            return {'CANCELLED'}
        make_chain(context, pts, self.name, _selected_armature(context))
        return {'FINISHED'}


class BT_OT_chain_from_curve(bpy.types.Operator):
    bl_idname = "bone_tools.chain_from_curve"
    bl_label = "Bone Chain from Curve"
    bl_description = ("Build a chain of evenly spaced bones along the active curve. "
                      "Adds to a selected armature, otherwise creates one")
    bl_options = {'REGISTER', 'UNDO'}

    count: bpy.props.IntProperty(name="Bones", default=8, min=1, max=512)
    name: bpy.props.StringProperty(name="Name", default="Chain")

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'CURVE'

    def execute(self, context):
        ob = context.object
        ev = ob.evaluated_get(context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        # ponytail: first spline, unbevelled curve (bevel makes to_mesh a tube); bevel 0 is the norm for guides
        pts = [ob.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
        if ob.data.splines and ob.data.splines[0].use_cyclic_u and pts:
            pts.append(pts[0])
        if len(pts) < 2:
            self.report({'ERROR'}, "Curve has no points")
            return {'CANCELLED'}
        make_chain(context, resample(pts, self.count), self.name, _selected_armature(context))
        return {'FINISHED'}


# ---------------------------------------------------------------- edit-mode bone ops

class _EditOp(_ArmatureOp):
    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE'


class BT_OT_roll_x_to_cursor(_EditOp, bpy.types.Operator):
    bl_idname = "bone_tools.roll_x_to_cursor"
    bl_label = "Roll X to Cursor"
    bl_description = "Roll selected bones so their local X axis points toward the 3D cursor"

    def execute(self, context):
        cur = context.object.matrix_world.inverted() @ context.scene.cursor.location
        for eb in context.selected_editable_bones:
            # Z = X x Y, so aligning Z to (target x Y) puts X toward the target.
            eb.align_roll((cur - eb.head).cross(eb.vector))
        return {'FINISHED'}


class BT_OT_subdivide(_EditOp, bpy.types.Operator):
    bl_idname = "bone_tools.subdivide"
    bl_label = "Subdivide Numbered"
    bl_description = "Subdivide selected bones and number the pieces name_01, name_02... (keeps .L/.R last)"

    number_cuts: bpy.props.IntProperty(name="Cuts", default=1, min=1, max=100)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ebs = context.object.data.edit_bones
        before = {b.name for b in ebs}
        roots = [b.name for b in ebs if b.select]
        bpy.ops.armature.subdivide(number_cuts=self.number_cuts)
        for root in roots:
            chain = [ebs[root]]
            while nxt := [c for c in chain[-1].children if c.name not in before]:
                chain.append(nxt[0])
            core = _NUM.sub("", root)
            m = _SUFFIX.match(core)
            base, side = (m.group(1), m.group(2) + m.group(3)) if m else (core, "")
            for i, eb in enumerate(chain, 1):
                eb.name = f"{base}_{i:02d}{side}"
        return {'FINISHED'}


# ---------------------------------------------------------------- deform ops

class BT_OT_toggle_deform(_ArmatureOp, bpy.types.Operator):
    bl_idname = "bone_tools.toggle_deform"
    bl_label = "Toggle Deform"
    bl_description = "Switch Deform off for the selected bones if any deforms, otherwise on"

    def execute(self, context):
        ob = context.object
        bones = [bone_coll(ob)[n] for n in selected_names(ob)]
        state = not any(b.use_deform for b in bones)
        for b in bones:
            b.use_deform = state
        self.report({'INFO'}, f"Deform {'on' if state else 'off'} for {len(bones)} bones")
        return {'FINISHED'}


class BT_OT_select_deform(_ArmatureOp, bpy.types.Operator):
    bl_idname = "bone_tools.select_deform"
    bl_label = "Select Deform"
    bl_description = "Select only deform (or only non-deform) bones"

    deform: bpy.props.BoolProperty(name="Deform", default=True)

    def execute(self, context):
        ob = context.object
        for b in bone_coll(ob):
            set_select(ob, b.name, b.use_deform == self.deform)
        return {'FINISHED'}


class BT_OT_check_skin(_ArmatureOp, bpy.types.Operator):
    bl_idname = "bone_tools.check_skin"
    bl_label = "Check Skinning"
    bl_description = ("Select deform bones that have no vertex group on the skinned meshes, "
                      "and list vertex groups that match no bone")

    def execute(self, context):
        ob = context.object
        missing = set(unbound_bones(ob))
        for b in bone_coll(ob):
            set_select(ob, b.name, b.name in missing)
        orphans = orphan_groups(ob)
        for m, g in orphans:
            print(f"bone_tools: '{m}' vertex group '{g}' has no bone")
        msg = f"{len(missing)} deform bones without vertex groups selected, {len(orphans)} groups without bones"
        if orphans:
            msg += ": " + ", ".join(g for _, g in orphans[:8]) + (" ..." if len(orphans) > 8 else "")
        self.report({'WARNING'} if missing or orphans else {'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------- constraint ops

class _PoseOp(_ArmatureOp):
    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'


class BT_OT_copy_transforms(_PoseOp, bpy.types.Operator):
    bl_idname = "bone_tools.copy_transforms"
    bl_label = "Copy Transforms from Other Rig"
    bl_description = ("Selected bones of the active armature get a Copy Transforms constraint "
                      "targeting the same-named bone of the other selected armature")

    def execute(self, context):
        ob = context.object
        src = next((o for o in context.selected_objects if o.type == 'ARMATURE' and o is not ob), None)
        if not src:
            self.report({'ERROR'}, "Also select the source armature (active = the one that follows)")
            return {'CANCELLED'}
        n = 0
        for pb in context.selected_pose_bones:
            if pb.id_data is ob and pb.name in src.pose.bones:
                c = pb.constraints.new('COPY_TRANSFORMS')
                c.target, c.subtarget = src, pb.name
                n += 1
        self.report({'INFO'}, f"Added {n} constraints from {src.name}")
        return {'FINISHED'}


class BT_OT_bake_constraints(_PoseOp, bpy.types.Operator):
    bl_idname = "bone_tools.bake_constraints"
    bl_label = "Bake & Remove Constraints"
    bl_description = "Bake the visual pose of selected bones over the scene range, then remove their constraints"

    def execute(self, context):
        sc = context.scene
        return bpy.ops.nla.bake(frame_start=sc.frame_start, frame_end=sc.frame_end, only_selected=True,
                                visual_keying=True, clear_constraints=True, bake_types={'POSE'})


# ---------------------------------------------------------------- UI

class BT_PT_panel(bpy.types.Panel):
    bl_label = "Bone Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rig"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and (ob.type in {'ARMATURE', 'CURVE'} or (ob.type == 'MESH' and ob.mode == 'EDIT'))

    def draw(self, context):
        ob, col = context.object, self.layout.column()
        if ob.type == 'MESH':
            col.operator("bone_tools.chain_from_mesh", icon='BONE_DATA')
            return
        if ob.type == 'CURVE':
            col.operator("bone_tools.chain_from_curve", icon='BONE_DATA')
            return
        col.label(text="Naming")
        col.operator_menu_enum("bone_tools.convert_sides", "style", icon='SORTALPHA')
        col.operator("bone_tools.strip_prefix")
        if context.mode == 'EDIT_ARMATURE':
            col.label(text="Roll")
            row = col.row(align=True)
            row.operator("armature.calculate_roll", text="Active").type = 'ACTIVE'
            row.operator("armature.calculate_roll", text="Global +Z").type = 'GLOBAL_POS_Z'
            row = col.row(align=True)
            row.operator("armature.calculate_roll", text="Z to Cursor").type = 'CURSOR'
            row.operator("bone_tools.roll_x_to_cursor", text="X to Cursor")
            col.operator("bone_tools.subdivide")
        col.label(text="Deform")
        col.operator("bone_tools.toggle_deform")
        row = col.row(align=True)
        row.operator("bone_tools.select_deform", text="Select Deform").deform = True
        row.operator("bone_tools.select_deform", text="Non-Deform").deform = False
        col.operator("bone_tools.check_skin", icon='VIEWZOOM')
        if context.mode == 'POSE':
            col.label(text="Constraints")
            col.operator("bone_tools.copy_transforms")
            col.operator("bone_tools.bake_constraints")


_classes = (BT_OT_convert_sides, BT_OT_strip_prefix, BT_OT_chain_from_mesh, BT_OT_chain_from_curve,
            BT_OT_roll_x_to_cursor, BT_OT_subdivide, BT_OT_toggle_deform, BT_OT_select_deform,
            BT_OT_check_skin, BT_OT_copy_transforms, BT_OT_bake_constraints, BT_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
