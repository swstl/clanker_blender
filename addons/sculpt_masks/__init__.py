"""Sculpt Masks: mask <-> vertex group, face set <-> vertex group, mask from color, stored masks."""
import bpy
import numpy as np

MASK = ".sculpt_mask"        # float, POINT
FSET = ".sculpt_face_set"    # int, FACE
PREFIX = ".mask:"            # stored masks: hidden float POINT attributes

MODES = (
    ('REPLACE', "Replace", "Replace the mask"),
    ('ADD', "Add", "Add to the mask"),
    ('SUBTRACT', "Subtract", "Subtract from the mask"),
    ('MULTIPLY', "Multiply", "Multiply the mask"),
    ('INTERSECT', "Intersect", "Keep the minimum of both masks"),
)


def combine(cur, new, mode):
    if mode == 'ADD':
        out = cur + new
    elif mode == 'SUBTRACT':
        out = cur - new
    elif mode == 'MULTIPLY':
        out = cur * new
    elif mode == 'INTERSECT':
        out = np.minimum(cur, new)
    else:
        out = new
    return np.clip(out, 0.0, 1.0)


# --- raw data access (object mode) ---------------------------------------------------------------

def read_float(me, name):
    out = np.zeros(len(me.vertices), np.float32)
    a = me.attributes.get(name)
    if a:
        a.data.foreach_get("value", out)
    return out


def write_float(me, name, values):
    a = me.attributes.get(name) or me.attributes.new(name, 'FLOAT', 'POINT')
    a.data.foreach_set("value", np.asarray(values, np.float32))


def get_mask(me):
    return read_float(me, MASK)


def set_mask(me, values):
    write_float(me, MASK, values)


def get_face_sets(me):
    a = me.attributes.get(FSET)
    if not a:
        return None
    out = np.zeros(len(me.polygons), np.int32)
    a.data.foreach_get("value", out)
    return out


def set_face_sets(me, values):
    a = me.attributes.get(FSET) or me.attributes.new(FSET, 'INT', 'FACE')
    a.data.foreach_set("value", np.asarray(values, np.int32))


def corners(me):
    """(corner vertex index, face index per corner, face loop starts)."""
    cv = np.zeros(len(me.loops), np.int32)
    me.loops.foreach_get("vertex_index", cv)
    start = np.zeros(len(me.polygons), np.int32)
    total = np.zeros(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", start)
    me.polygons.foreach_get("loop_total", total)
    return cv, np.repeat(np.arange(len(me.polygons)), total), start


def vg_weights(ob, name):
    gi = ob.vertex_groups[name].index
    w = np.zeros(len(ob.data.vertices), np.float32)
    # ponytail: python loop over vertex groups, fine up to ~1M verts
    for v in ob.data.vertices:
        for g in v.groups:
            if g.group == gi:
                w[v.index] = g.weight
                break
    return w


def write_vg(ob, name, weights, new=False):
    vg = None if new else ob.vertex_groups.get(name)
    if vg is None:
        vg = ob.vertex_groups.new(name=name)
    vg.remove(list(range(len(ob.data.vertices))))
    for i in np.flatnonzero(weights > 0.0):
        vg.add([int(i)], float(weights[i]), 'REPLACE')
    return vg


def color_values(me, channel):
    """Per-vertex value of the active color attribute (display/sRGB values)."""
    ca = me.color_attributes.active_color
    if ca is None:
        return None
    rgba = np.zeros(len(ca.data) * 4, np.float32)
    ca.data.foreach_get("color_srgb", rgba)
    rgba = rgba.reshape(-1, 4)
    if channel == 'LUMINANCE':
        val = rgba[:, :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    else:
        val = rgba[:, "RGBA".index(channel)]
    if ca.domain == 'CORNER':
        cv = corners(me)[0]
        n = len(me.vertices)
        cnt = np.bincount(cv, minlength=n)
        val = np.bincount(cv, weights=val, minlength=n) / np.maximum(cnt, 1)
    return val.astype(np.float32)


# --- operator plumbing ---------------------------------------------------------------------------

def blocker(ob):
    if any(m.type == 'MULTIRES' and m.total_levels > 0 for m in ob.modifiers):
        return "Multires sculpting stores masks per grid, not supported"
    if ob.mode == 'SCULPT' and ob.use_dynamic_topology_sculpting:
        return "Dyntopo is on, disable it first"
    return None


class _MeshOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        ob = context.active_object
        msg = blocker(ob)
        if msg:
            self.report({'WARNING'}, msg)
            return {'CANCELLED'}
        # Sculpt mode keeps its own PBVH caches and edit mode a BMesh: write mesh data in
        # object mode and switch back so the mode rebuilds from it.
        mode = ob.mode
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            err = self.run(ob, ob.data)
        finally:
            ob.data.update()
            if mode != 'OBJECT':
                bpy.ops.object.mode_set(mode=mode)
        if err:
            self.report({'WARNING'}, err)
            return {'CANCELLED'}
        return {'FINISHED'}


def _active_group(context):
    ob = context.active_object
    vg = ob.vertex_groups.active if ob else None
    return vg.name if vg else ""


class SM_OT_mask_to_vgroup(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.mask_to_vgroup"
    bl_label = "Mask to Vertex Group"
    bl_description = "Copy the sculpt mask into a vertex group (empty name: new group 'Mask')"

    group: bpy.props.StringProperty(name="Group", description="Group to overwrite or create")

    def run(self, ob, me):
        vg = write_vg(ob, self.group or "Mask", get_mask(me), new=not self.group)
        ob.vertex_groups.active_index = vg.index


class SM_OT_vgroup_to_mask(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.vgroup_to_mask"
    bl_label = "Vertex Group to Mask"
    bl_description = "Combine a vertex group's weights into the sculpt mask"

    group: bpy.props.StringProperty(name="Group", description="Empty uses the active group")
    mode: bpy.props.EnumProperty(name="Mode", items=MODES[:4])

    def run(self, ob, me):
        name = self.group or (ob.vertex_groups.active.name if ob.vertex_groups.active else "")
        if name not in ob.vertex_groups:
            return "No vertex group"
        set_mask(me, combine(get_mask(me), vg_weights(ob, name), self.mode))


class SM_OT_faceset_to_vgroups(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.faceset_to_vgroups"
    bl_label = "Face Sets to Vertex Groups"
    bl_description = "Create one vertex group per face set ('FaceSet <id>', weight 1)"

    face_set: bpy.props.IntProperty(name="Face Set", default=0, min=0,
                                    description="Only this face set id (0 = all face sets)")

    def run(self, ob, me):
        fs = get_face_sets(me)
        if fs is None:
            return "Mesh has no face sets"
        cv, cface, _ = corners(me)
        cfs = fs[cface]
        ids = [self.face_set] if self.face_set else np.unique(fs).tolist()
        n = len(me.vertices)
        for fid in ids:
            w = np.zeros(n, np.float32)
            w[cv[cfs == fid]] = 1.0
            if not w.any():
                return f"No face set {fid}"
            write_vg(ob, f"FaceSet {fid}", w)


class SM_OT_vgroup_to_faceset(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.vgroup_to_faceset"
    bl_label = "Vertex Group to Face Set"
    bl_description = "New face set from faces whose vertices all weigh more than the threshold"

    group: bpy.props.StringProperty(name="Group", description="Empty uses the active group")
    threshold: bpy.props.FloatProperty(name="Threshold", default=0.5, min=0.0, max=1.0)

    def run(self, ob, me):
        name = self.group or (ob.vertex_groups.active.name if ob.vertex_groups.active else "")
        if name not in ob.vertex_groups:
            return "No vertex group"
        cv, _, start = corners(me)
        inside = (vg_weights(ob, name) > self.threshold)[cv].astype(np.int8)
        faces = np.minimum.reduceat(inside, start).astype(bool)
        if not faces.any():
            return "No face fully inside the group"
        fs = get_face_sets(me)
        if fs is None:
            fs = np.ones(len(me.polygons), np.int32)
        fs[faces] = max(int(fs.max()) + 1, 2)
        set_face_sets(me, fs)


class SM_OT_mask_from_color(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.mask_from_color"
    bl_label = "Mask from Color"
    bl_description = "Build the mask from a channel of the active color attribute"

    channel: bpy.props.EnumProperty(name="Channel", items=(
        ('LUMINANCE', "Luminance", ""), ('R', "Red", ""), ('G', "Green", ""),
        ('B', "Blue", ""), ('A', "Alpha", "")))
    mode: bpy.props.EnumProperty(name="Mode", items=MODES[:4])

    def run(self, ob, me):
        val = color_values(me, self.channel)
        if val is None:
            return "No active color attribute"
        set_mask(me, combine(get_mask(me), val, self.mode))


def _stored_name(me, name):
    if name:
        return PREFIX + name
    attrs = me.attributes
    i = me.sculpt_masks_index
    return attrs[i].name if 0 <= i < len(attrs) and attrs[i].name.startswith(PREFIX) else ""


class SM_OT_store(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.store"
    bl_label = "Store Mask"
    bl_description = "Save the current mask under a name (overwrites a stored mask of that name)"

    name: bpy.props.StringProperty(name="Name", default="Mask")

    def run(self, ob, me):
        if not self.name:
            return "Name is empty"
        write_float(me, PREFIX + self.name, get_mask(me))
        me.sculpt_masks_index = me.attributes.find(PREFIX + self.name)


class SM_OT_restore(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.restore"
    bl_label = "Restore Mask"
    bl_description = "Combine a stored mask into the current mask"

    name: bpy.props.StringProperty(name="Name", description="Empty uses the active list item")
    mode: bpy.props.EnumProperty(name="Mode", items=(MODES[0], MODES[1], MODES[2], MODES[4]))

    def run(self, ob, me):
        attr = _stored_name(me, self.name)
        if attr not in me.attributes:
            return "No stored mask"
        set_mask(me, combine(get_mask(me), read_float(me, attr), self.mode))


class SM_OT_delete(_MeshOp, bpy.types.Operator):
    bl_idname = "sculpt_masks.delete"
    bl_label = "Delete Stored Mask"
    bl_description = "Delete a stored mask"

    name: bpy.props.StringProperty(name="Name", description="Empty uses the active list item")

    def run(self, ob, me):
        attr = _stored_name(me, self.name)
        if attr not in me.attributes:
            return "No stored mask"
        me.attributes.remove(me.attributes[attr])


class SM_OT_vgroup_dialog(_MeshOp, bpy.types.Operator):
    """Menu entry: pick a group and mode, then run vgroup_to_mask / vgroup_to_faceset."""
    bl_idname = "sculpt_masks.vgroup_dialog"
    bl_label = "From Vertex Group"
    bl_description = "Pick a vertex group to turn into a mask or a face set"

    target: bpy.props.EnumProperty(items=(('MASK', "Mask", ""), ('FACESET', "Face Set", "")))
    group: bpy.props.StringProperty(name="Group")
    mode: bpy.props.EnumProperty(name="Mode", items=MODES[:4])
    threshold: bpy.props.FloatProperty(name="Threshold", default=0.5, min=0.0, max=1.0)

    def invoke(self, context, event):
        self.group = self.group or _active_group(context)
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, "group", context.active_object, "vertex_groups")
        self.layout.prop(self, "mode" if self.target == 'MASK' else "threshold")

    def execute(self, context):
        if self.target == 'MASK':
            return bpy.ops.sculpt_masks.vgroup_to_mask(group=self.group, mode=self.mode)
        return bpy.ops.sculpt_masks.vgroup_to_faceset(group=self.group, threshold=self.threshold)


# --- UI ------------------------------------------------------------------------------------------

class SM_UL_stored(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name[len(PREFIX):], icon='MOD_MASK')

    def filter_items(self, context, data, propname):
        flags = [self.bitflag_filter_item if a.name.startswith(PREFIX) else 0
                 for a in getattr(data, propname)]
        return flags, []


class SM_PT_panel(bpy.types.Panel):
    bl_label = "Masks & Face Sets"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Sculpt"

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return context.mode == 'SCULPT' and ob is not None and ob.type == 'MESH'

    def draw(self, context):
        ob = context.active_object
        me = ob.data
        wm = context.window_manager
        layout = self.layout
        msg = blocker(ob)
        if msg:
            layout.label(text=msg, icon='ERROR')

        col = layout.column(align=True)
        col.label(text="Quick")
        row = col.row(align=True)
        row.operator("paint.mask_flood_fill", text="Invert").mode = 'INVERT'
        op = row.operator("paint.mask_flood_fill", text="Clear")
        op.mode = 'VALUE'
        op.value = 0.0
        row = col.row(align=True)
        op = row.operator("sculpt.mask_filter", text="Smooth")
        op.filter_type = 'SMOOTH'
        op.auto_iteration_count = False
        op.iterations = wm.sculpt_masks_smooth
        row.prop(wm, "sculpt_masks_smooth", text="")

        col = layout.column(align=True)
        col.label(text="Vertex Groups")
        col.template_list("MESH_UL_vgroups", "", ob, "vertex_groups",
                          ob.vertex_groups, "active_index", rows=3)
        row = col.row(align=True)
        row.operator("sculpt_masks.mask_to_vgroup", text="Mask to New").group = ""
        row.operator("sculpt_masks.mask_to_vgroup", text="Mask to Active").group = _active_group(context)
        col.operator_menu_enum("sculpt_masks.vgroup_to_mask", "mode", text="Active Group to Mask")
        row = col.row(align=True)
        row.operator("sculpt_masks.faceset_to_vgroups", text="Face Sets to Groups")
        row.operator("sculpt_masks.vgroup_to_faceset", text="Group to Face Set")
        col.operator_menu_enum("sculpt_masks.mask_from_color", "channel", text="Mask from Color")

        col = layout.column(align=True)
        col.label(text="Stored Masks")
        col.template_list("SM_UL_stored", "", me, "attributes", me, "sculpt_masks_index", rows=3)
        row = col.row(align=True)
        row.prop(wm, "sculpt_masks_name", text="")
        row.operator("sculpt_masks.store", text="", icon='ADD').name = wm.sculpt_masks_name
        row.operator("sculpt_masks.delete", text="", icon='REMOVE').name = ""
        col.operator_menu_enum("sculpt_masks.restore", "mode", text="Restore Active")


def draw_mask_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator("sculpt_masks.mask_to_vgroup", text="Mask to New Vertex Group").group = ""
    layout.operator("sculpt_masks.vgroup_dialog", text="Mask from Vertex Group...").target = 'MASK'
    layout.operator_menu_enum("sculpt_masks.mask_from_color", "channel", text="Mask from Color")


def draw_faceset_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator("sculpt_masks.faceset_to_vgroups", text="Face Sets to Vertex Groups")
    layout.operator("sculpt_masks.vgroup_dialog", text="Face Set from Vertex Group...").target = 'FACESET'


_classes = (SM_OT_mask_to_vgroup, SM_OT_vgroup_to_mask, SM_OT_faceset_to_vgroups,
            SM_OT_vgroup_to_faceset, SM_OT_mask_from_color, SM_OT_store, SM_OT_restore,
            SM_OT_delete, SM_OT_vgroup_dialog, SM_UL_stored, SM_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Mesh.sculpt_masks_index = bpy.props.IntProperty()
    bpy.types.WindowManager.sculpt_masks_name = bpy.props.StringProperty(name="Name", default="Mask")
    bpy.types.WindowManager.sculpt_masks_smooth = bpy.props.IntProperty(
        name="Iterations", default=5, min=1, max=100, description="Mask smooth iterations")
    bpy.types.VIEW3D_MT_mask.append(draw_mask_menu)
    bpy.types.VIEW3D_MT_face_sets.append(draw_faceset_menu)


def unregister():
    bpy.types.VIEW3D_MT_face_sets.remove(draw_faceset_menu)
    bpy.types.VIEW3D_MT_mask.remove(draw_mask_menu)
    del bpy.types.WindowManager.sculpt_masks_smooth
    del bpy.types.WindowManager.sculpt_masks_name
    del bpy.types.Mesh.sculpt_masks_index
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
