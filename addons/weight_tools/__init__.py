"""Weight Tools: batch vertex group fixes riggers do all the time."""
import bpy
from mathutils.kdtree import KDTree


def _is_left(name):
    """flip_name only swaps the side token, so the first differing char tells the side."""
    flipped = bpy.utils.flip_name(name)
    return next((a for a, b in zip(name, flipped) if a != b), "") in "lL"


def mirror_groups(ob, to_right=True, tol=0.001):
    """Create/overwrite the opposite-side group of every L (or R) group, mirrored across local X.
    Returns the number of groups written."""
    vgs = ob.vertex_groups
    pairs = [(vg.index, bpy.utils.flip_name(vg.name)) for vg in vgs
             if bpy.utils.flip_name(vg.name) != vg.name and _is_left(vg.name) == to_right]
    if not pairs:
        return 0
    verts = ob.data.vertices
    kd = KDTree(len(verts))
    for v in verts:
        kd.insert(v.co, v.index)
    kd.balance()
    mirror = {}
    for v in verts:
        co, i, d = kd.find((-v.co.x, v.co.y, v.co.z))
        if co is not None and d <= tol:
            mirror[v.index] = i
    src = {gi: {} for gi, _ in pairs}
    for v in verts:
        for g in v.groups:
            if g.group in src:
                src[g.group][v.index] = g.weight
    all_idx = list(range(len(verts)))
    for gi, name in pairs:
        tgt = vgs.get(name) or vgs.new(name=name)
        tgt.remove(all_idx)
        w = src[gi]
        # ponytail: one add() per vertex, batch by weight value if huge meshes get slow
        for v, m in mirror.items():
            if m in w:
                tgt.add([v], w[m], 'REPLACE')
    return len(pairs)


def remove_empty_groups(ob):
    used = {g.group for v in ob.data.vertices for g in v.groups if g.weight > 0}
    empty = [vg for vg in ob.vertex_groups if vg.index not in used]
    for vg in empty:
        ob.vertex_groups.remove(vg)
    return len(empty)


def armature_of(ob):
    arm = next((m.object for m in ob.modifiers if m.type == 'ARMATURE' and m.object), None)
    if arm is None and ob.parent and ob.parent.type == 'ARMATURE':
        arm = ob.parent
    return arm


def remove_unmatched_groups(ob, arm, only_deform=False):
    bones = {b.name for b in arm.data.bones if b.use_deform or not only_deform}
    bad = [vg for vg in ob.vertex_groups if vg.name not in bones]
    for vg in bad:
        ob.vertex_groups.remove(vg)
    return len(bad)


def _snapshot(ob):
    return [tuple((g.group, round(g.weight, 6)) for g in v.groups) for v in ob.data.vertices]


def game_ready(ob, threshold=0.01, limit=4, quantize=0):
    """Clean, limit, normalize (lock-aware), optionally quantize. Returns changed vertex count."""
    before = _snapshot(ob)
    vgs = ob.vertex_groups
    locked = {vg.index for vg in vgs if vg.lock_weight}
    # Clean + limit done here: the built-in ops only act on view-layer-selected objects.
    for v in ob.data.vertices:
        gs = sorted(v.groups, key=lambda g: -g.weight)
        drop = [g.group for i, g in enumerate(gs)
                if (g.weight < threshold or i >= limit) and g.group not in locked]
        for gi in drop:
            vgs[gi].remove([v.index])
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.vertex_group_normalize_all(lock_active=False)
        if quantize:
            bpy.ops.object.vertex_group_quantize(group_select_mode='ALL', steps=quantize)
    return sum(a != b for a, b in zip(before, _snapshot(ob)))


def select_problem_verts(ob, max_influences=4):
    """Select unweighted verts and verts with more than max_influences. Returns (unweighted, over)."""
    me = ob.data
    unweighted = over = 0
    sel = []
    for v in me.vertices:
        ws = [g.weight for g in v.groups if g.weight > 0]
        u, o = not ws, len(ws) > max_influences
        unweighted += u
        over += o
        sel.append(u or o)
    me.vertices.foreach_set("select", sel)
    me.edges.foreach_set("select", [False] * len(me.edges))
    me.polygons.foreach_set("select", [False] * len(me.polygons))
    me.use_paint_mask_vertex = True
    me.update()
    return unweighted, over


def set_paint_weight(context, value):
    wp = context.scene.tool_settings.weight_paint
    wp.unified_paint_settings.weight = value
    if wp.brush:
        wp.brush.weight = value


class WT_Settings(bpy.types.PropertyGroup):
    to_right: bpy.props.EnumProperty(name="Direction", items=(
        ('L2R', "L → R", "Mirror left groups onto right"),
        ('R2L', "R → L", "Mirror right groups onto left")))
    tolerance: bpy.props.FloatProperty(name="Tolerance", default=0.001, min=0, precision=4,
                                       description="Max distance to the mirrored vertex position")
    only_deform: bpy.props.BoolProperty(
        name="Deform Bones Only", default=False,
        description="Also remove groups of bones that have Deform turned off")
    threshold: bpy.props.FloatProperty(name="Clean Below", default=0.01, min=0, max=1)
    limit: bpy.props.IntProperty(name="Max Influences", default=4, min=1, max=32)
    quantize: bpy.props.IntProperty(name="Quantize Steps", default=0, min=0, max=1000,
                                    description="0 = don't quantize")


def _settings(context):
    return context.scene.weight_tools


class _MeshOp:
    """Runs in Object mode (vertex data is stale in Edit mode) and goes back after."""
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        ob = context.object
        edit = ob.mode == 'EDIT'
        if edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            return self.run(context, ob, _settings(context))
        finally:
            if edit:
                bpy.ops.object.mode_set(mode='EDIT')


class WT_OT_mirror(_MeshOp, bpy.types.Operator):
    bl_idname = "weight_tools.mirror"
    bl_label = "Mirror L/R Groups"
    bl_description = "Create or overwrite every opposite-side vertex group, mirrored across local X"

    def run(self, context, ob, s):
        n = mirror_groups(ob, s.to_right == 'L2R', s.tolerance)
        self.report({'INFO'}, f"Mirrored {n} group(s)")
        return {'FINISHED'}


class WT_OT_remove_empty(_MeshOp, bpy.types.Operator):
    bl_idname = "weight_tools.remove_empty"
    bl_label = "Remove Empty Groups"
    bl_description = "Delete vertex groups that have no vertices or only zero weights"

    def run(self, context, ob, s):
        self.report({'INFO'}, f"Removed {remove_empty_groups(ob)} empty group(s)")
        return {'FINISHED'}


class WT_OT_remove_unmatched(_MeshOp, bpy.types.Operator):
    bl_idname = "weight_tools.remove_unmatched"
    bl_label = "Remove Non-Bone Groups"
    bl_description = "Delete vertex groups with no matching bone in the Armature modifier's (or parent) armature"

    def run(self, context, ob, s):
        arm = armature_of(ob)
        if arm is None:
            self.report({'ERROR'}, "No Armature modifier or armature parent")
            return {'CANCELLED'}
        n = remove_unmatched_groups(ob, arm, s.only_deform)
        self.report({'INFO'}, f"Removed {n} group(s) not matching {arm.name}")
        return {'FINISHED'}


class WT_OT_game_ready(_MeshOp, bpy.types.Operator):
    bl_idname = "weight_tools.game_ready"
    bl_label = "Game-Ready Cleanup"
    bl_description = "Clean small weights, limit influences, normalize all (respects locks), optionally quantize"

    def run(self, context, ob, s):
        n = game_ready(ob, s.threshold, s.limit, s.quantize)
        self.report({'INFO'}, f"{n} vertices changed")
        return {'FINISHED'}


class WT_OT_select_problems(_MeshOp, bpy.types.Operator):
    bl_idname = "weight_tools.select_problems"
    bl_label = "Select Problem Verts"
    bl_description = "Select unweighted vertices and vertices over the max influence count"

    def run(self, context, ob, s):
        u, o = select_problem_verts(ob, s.limit)
        self.report({'WARNING'} if u or o else {'INFO'},
                    f"{u} unweighted, {o} over {s.limit} influences")
        return {'FINISHED'}


class WT_OT_set_weight(bpy.types.Operator):
    bl_idname = "weight_tools.set_weight"
    bl_label = "Set Brush Weight"
    bl_description = "Set the weight paint brush weight"
    value: bpy.props.FloatProperty(min=0, max=1)

    def execute(self, context):
        set_paint_weight(context, self.value)
        return {'FINISHED'}


class WT_PT_panel(bpy.types.Panel):
    bl_label = "Weight Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Weights"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == 'MESH' and context.mode in {'OBJECT', 'PAINT_WEIGHT', 'EDIT_MESH'}

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column(align=True)
        col.prop(s, "to_right", expand=True)
        col.prop(s, "tolerance")
        col.operator("weight_tools.mirror", icon='MOD_MIRROR')

        col = self.layout.column(align=True)
        col.operator("weight_tools.remove_empty", icon='TRASH')
        col.operator("weight_tools.remove_unmatched", icon='BONE_DATA')
        col.prop(s, "only_deform")

        col = self.layout.column(align=True)
        col.prop(s, "threshold")
        col.prop(s, "limit")
        col.prop(s, "quantize")
        col.operator("weight_tools.game_ready", icon='CHECKMARK')
        col.operator("weight_tools.select_problems", icon='RESTRICT_SELECT_OFF')

        if context.mode in {'EDIT_MESH', 'PAINT_WEIGHT'}:
            self.layout.operator("object.vertex_weight_copy", text="Copy Active Vert to Selected")
        if context.mode == 'PAINT_WEIGHT':
            row = self.layout.row(align=True)
            for v in (0, 0.1, 0.25, 0.5, 0.75, 1):
                row.operator("weight_tools.set_weight", text=f"{v:g}").value = v


classes = (WT_Settings, WT_OT_mirror, WT_OT_remove_empty, WT_OT_remove_unmatched,
           WT_OT_game_ready, WT_OT_select_problems, WT_OT_set_weight, WT_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.weight_tools = bpy.props.PointerProperty(type=WT_Settings)


def unregister():
    del bpy.types.Scene.weight_tools
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
