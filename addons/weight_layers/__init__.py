"""Weight Layers: Photoshop-style layers for a vertex group.

Layers of target group G are ordinary vertex groups named "G~<layer>". You paint in a layer;
the blended stack is written into G (the group the armature uses). Vertices that are not in a
layer's group are transparent for that layer.
"""
import time

import bpy
import numpy as np
from bpy.app.handlers import persistent

SEP = "~"  # not ".L." : that clashes with Blender's .L/.R side naming used by mirror tools


BLENDS = {
    'MIX': lambda r, w: w,
    'ADD': lambda r, w: r + w,
    'SUBTRACT': lambda r, w: r - w,
    'MULTIPLY': lambda r, w: r * w,
    'MAX': np.maximum,
    'MIN': np.minimum,
}


def blend_stack(W, M, layers):
    """W, M: (k, n) weights / membership per layer. layers: [(blend, opacity, enabled)].
    Returns (result weights, union membership). Bottom layer first."""
    n = W.shape[1]
    r = np.zeros(n, np.float32)
    used = np.zeros(n, bool)
    for (mode, opacity, enabled), w, m in zip(layers, W, M):
        if not enabled or opacity <= 0:
            continue
        # opacity scales the change, then clamp: Subtract 1.0 at 50% = r - 0.5
        mixed = r + (BLENDS[mode](r, w) - r) * opacity
        r = np.where(m, np.clip(mixed, 0.0, 1.0), r)
        used |= m
    return r, used


def read_groups(ob, names):
    """One pass over the mesh for all groups -> (weights, membership) arrays of shape (k, n)."""
    n = len(ob.data.vertices)
    W = np.zeros((len(names), n), np.float32)
    M = np.zeros((len(names), n), bool)
    col = {ob.vertex_groups[nm].index: k for k, nm in enumerate(names)}
    for i, v in enumerate(ob.data.vertices):
        for e in v.groups:
            k = col.get(e.group)
            if k is not None:
                W[k, i] = e.weight
                M[k, i] = True
    return W, M


def _deform_indices(ob, target):
    """Groups that share normalization with the target: armature bone groups if any, else all non-layer groups."""
    bones = set()
    for md in ob.modifiers:
        if md.type == 'ARMATURE' and md.object and md.object.type == 'ARMATURE':
            bones.update(b.name for b in md.object.data.bones)
    return {g.index for g in ob.vertex_groups
            if g.name != target and SEP not in g.name and (not bones or g.name in bones)}


def normalize(ob, target, idx):
    """Scale the other deform groups so each vertex in idx sums to 1, keeping the target weight."""
    # ponytail: ignores group locks; respect vg.lock_weight if users ask
    others = _deform_indices(ob, target)
    gi = ob.vertex_groups[target].index
    verts = ob.data.vertices
    for i in idx:
        g, rest = 0.0, []
        for e in verts[i].groups:
            if e.group == gi:
                g = e.weight
            elif e.group in others:
                rest.append(e)
        s = sum(e.weight for e in rest)
        if s > 0:
            f = max(0.0, 1.0 - g) / s
            for e in rest:
                e.weight *= f


def composite(ob, stack):
    """Blend the stack into the target group. Writes only changed vertices. Returns how many changed."""
    target = stack.name
    if target not in ob.vertex_groups:
        return 0
    layers = [l for l in stack.layers if l.group in ob.vertex_groups]
    W, M = read_groups(ob, [target] + [l.group for l in layers])
    r, used = blend_stack(W[1:], M[1:], [(l.blend, l.opacity, l.enabled) for l in layers])
    cur, cur_m = W[0], M[0]
    changed = used & (~cur_m | (np.abs(r - cur) > 1e-6))
    drop = cur_m & ~used
    vg = ob.vertex_groups[target]
    if drop.any():
        vg.remove(np.flatnonzero(drop).tolist())
    idx = np.flatnonzero(changed).tolist()
    for i in idx:
        vg.add([i], float(r[i]), 'REPLACE')
    if stack.normalize and idx:
        normalize(ob, target, idx)
    if idx or drop.any():
        ob.data.update()
    return len(idx) + int(drop.sum())


def copy_group(ob, src, name):
    dst = ob.vertex_groups.new(name=name)
    W, M = read_groups(ob, [src])
    for i in np.flatnonzero(M[0]).tolist():
        dst.add([i], float(W[0, i]), 'REPLACE')
    return dst


# ---------------------------------------------------------------- auto update (debounced)

_dirty = set()
_last = [0.0]
IDLE = 0.25  # seconds without changes before compositing, so painting never stutters


def schedule(ob):
    _dirty.add(ob.name)
    _last[0] = time.monotonic()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=IDLE)


def _tick():
    if time.monotonic() - _last[0] < IDLE:
        return IDLE
    while _dirty:
        ob = bpy.data.objects.get(_dirty.pop())
        if ob and ob.type == 'MESH' and ob.mode != 'EDIT':
            for st in ob.weight_layers:
                if st.auto_update:
                    composite(ob, st)
    return None


@persistent
def on_depsgraph(scene, depsgraph):
    ob = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if not ob or ob.type != 'MESH' or not ob.weight_layers:
        return
    for u in depsgraph.updates:
        if u.is_updated_geometry and u.id.original in (ob, ob.data):
            schedule(ob)
            return


@persistent
def on_undo(*_):
    ob = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if ob and ob.type == 'MESH' and ob.weight_layers:
        schedule(ob)


def _prop_changed(self, context):
    schedule(self.id_data)


# ---------------------------------------------------------------- data

def _rename_layer(self, context):
    ob = self.id_data
    vg = ob.vertex_groups.get(self.group)
    if vg:
        vg.name = self.group.split(SEP)[0] + SEP + self.name
        self.group = vg.name


def _select_layer(self, context):
    ob = self.id_data
    if 0 <= self.active_index < len(self.layers):
        vg = ob.vertex_groups.get(self.layers[self.active_index].group)
        if vg:
            ob.vertex_groups.active_index = vg.index


class WL_Layer(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(name="Name", update=_rename_layer)
    group: bpy.props.StringProperty(name="Vertex Group", description="Vertex group holding this layer's weights")
    blend: bpy.props.EnumProperty(name="Blend", items=[
        ('MIX', "Mix", "Replace by the layer weight"),
        ('ADD', "Add", "Add the layer weight"),
        ('SUBTRACT', "Subtract", "Subtract the layer weight"),
        ('MULTIPLY', "Multiply", "Multiply by the layer weight"),
        ('MAX', "Max", "Keep the larger weight"),
        ('MIN', "Min", "Keep the smaller weight"),
    ], default='MIX', update=_prop_changed)
    opacity: bpy.props.FloatProperty(name="Opacity", default=1.0, min=0, max=1, subtype='FACTOR',
                                     update=_prop_changed)
    enabled: bpy.props.BoolProperty(name="Enabled", default=True, update=_prop_changed)


class WL_Stack(bpy.types.PropertyGroup):
    # name = target vertex group
    layers: bpy.props.CollectionProperty(type=WL_Layer)
    active_index: bpy.props.IntProperty(update=_select_layer)
    auto_update: bpy.props.BoolProperty(
        name="Auto Update", default=True,
        description="Rewrite the target group shortly after you stop painting or change a layer")
    normalize: bpy.props.BoolProperty(
        name="Normalize", default=False, update=_prop_changed,
        description="After compositing, scale the other deform groups so every vertex sums to 1")


def stack_of(ob):
    """Stack belonging to the active vertex group (the target itself or one of its layers)."""
    vg = ob.vertex_groups.active if ob and ob.type == 'MESH' else None
    return ob.weight_layers.get(vg.name.split(SEP)[0]) if vg else None


def add_layer(ob, stack, name, src=None):
    """New layer above the active one; src = vertex group to copy weights from."""
    base = stack.name + SEP + name
    vg = copy_group(ob, src, base) if src else ob.vertex_groups.new(name=base)
    lay = stack.layers.add()
    lay.group = vg.name
    lay.name = vg.name.split(SEP, 1)[1]  # Blender may have suffixed .001
    to = min(stack.active_index + 1, len(stack.layers) - 1) if len(stack.layers) > 1 else 0
    stack.layers.move(len(stack.layers) - 1, to)
    stack.active_index = to
    return lay


# ---------------------------------------------------------------- operators

class _StackOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.mode != 'EDIT' and stack_of(ob) is not None

    def layer(self, context):
        st = stack_of(context.object)
        return st.layers[st.active_index] if 0 <= st.active_index < len(st.layers) else None


class WL_OT_create(bpy.types.Operator):
    bl_idname = "weight_layers.create"
    bl_label = "Create Layers"
    bl_description = "Start a layer stack for the active vertex group; its current weights become the Base layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return (ob and ob.type == 'MESH' and ob.mode != 'EDIT' and ob.vertex_groups.active
                and stack_of(ob) is None)

    def execute(self, context):
        ob = context.object
        target = ob.vertex_groups.active.name
        st = ob.weight_layers.add()
        st.name = target
        add_layer(ob, st, "Base", src=target)
        return {'FINISHED'}


class WL_OT_add(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.add"
    bl_label = "Add Layer"
    bl_description = "Add an empty layer above the active one (unpainted vertices do not affect the result)"

    def execute(self, context):
        st = stack_of(context.object)
        add_layer(context.object, st, f"Layer {len(st.layers)}")
        return {'FINISHED'}


class WL_OT_duplicate(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.duplicate"
    bl_label = "Duplicate Layer"
    bl_description = "Copy the active layer with its weights and settings"

    def execute(self, context):
        ob, src = context.object, self.layer(context)
        if not src:
            return {'CANCELLED'}
        lay = add_layer(ob, stack_of(ob), src.name, src=src.group)
        lay.blend, lay.opacity, lay.enabled = src.blend, src.opacity, src.enabled
        return {'FINISHED'}


class WL_OT_remove(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.remove"
    bl_label = "Remove Layer"
    bl_description = "Delete the active layer and its vertex group"

    def execute(self, context):
        ob = context.object
        st, lay = stack_of(ob), self.layer(context)
        if not lay:
            return {'CANCELLED'}
        vg = ob.vertex_groups.get(lay.group)
        if vg:
            ob.vertex_groups.remove(vg)
        st.layers.remove(st.active_index)
        if st.layers:
            st.active_index = min(st.active_index, len(st.layers) - 1)
        else:
            ob.vertex_groups.active_index = ob.vertex_groups[st.name].index
        composite(ob, st)
        return {'FINISHED'}


class WL_OT_move(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.move"
    bl_label = "Move Layer"
    bl_description = "Move the active layer up or down in the stack"
    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context):
        st = stack_of(context.object)
        i = st.active_index
        j = i + (1 if self.direction == 'UP' else -1)
        if not (0 <= j < len(st.layers)):
            return {'CANCELLED'}
        st.layers.move(i, j)
        st.active_index = j
        composite(context.object, st)
        return {'FINISHED'}


class WL_OT_composite(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.composite"
    bl_label = "Update Result"
    bl_description = "Blend all layers into the target vertex group now"

    def execute(self, context):
        n = composite(context.object, stack_of(context.object))
        self.report({'INFO'}, f"{n} vertices updated")
        return {'FINISHED'}


class WL_OT_show_result(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.show_result"
    bl_label = "Show Result"
    bl_description = "Make the target (composited) group active to view the final weights"

    def execute(self, context):
        ob = context.object
        st = stack_of(ob)
        composite(ob, st)
        ob.vertex_groups.active_index = ob.vertex_groups[st.name].index
        return {'FINISHED'}


class WL_OT_flatten(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.flatten"
    bl_label = "Flatten"
    bl_description = "Write the result into the target group and delete all layers"

    def execute(self, context):
        flatten(context.object, stack_of(context.object))
        return {'FINISHED'}


def flatten(ob, st):
    composite(ob, st)
    target = st.name
    for lay in st.layers:
        vg = ob.vertex_groups.get(lay.group)
        if vg:
            ob.vertex_groups.remove(vg)
    ob.weight_layers.remove(ob.weight_layers.find(target))
    if target in ob.vertex_groups:
        ob.vertex_groups.active_index = ob.vertex_groups[target].index


class WL_OT_mirror(_StackOp, bpy.types.Operator):
    bl_idname = "weight_layers.mirror"
    bl_label = "Mirror Layer"
    bl_description = "Mirror the active layer's weights across X (Blender's Mirror Vertex Group)"
    use_topology: bpy.props.BoolProperty(name="Topology Mirror", default=False)

    def execute(self, context):
        ob, lay = context.object, self.layer(context)
        vg = lay and ob.vertex_groups.get(lay.group)
        if not vg:
            return {'CANCELLED'}
        ob.vertex_groups.active_index = vg.index
        # flip_group_names off: a layer called e.g. "LeftFix" must not get renamed to "RightFix"
        bpy.ops.object.vertex_group_mirror(flip_group_names=False, use_topology=self.use_topology)
        composite(ob, stack_of(ob))
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class WL_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="", emboss=False,
                 icon='HIDE_OFF' if item.enabled else 'HIDE_ON')
        row.prop(item, "name", text="", emboss=False)
        if item.group not in context.object.vertex_groups:
            row.label(text="", icon='ERROR')
        row.label(text=item.blend.title())
        row.label(text=f"{item.opacity:.0%}")

    def filter_items(self, context, data, propname):
        n = len(getattr(data, propname))
        return [], [n - 1 - i for i in range(n)]  # top layer drawn first, like an image editor


def draw_stack(layout, context):
    ob = context.object
    st = stack_of(ob)
    if st is None:
        vg = ob.vertex_groups.active
        layout.operator("weight_layers.create",
                        text=f"Create Layers for '{vg.name}'" if vg else "Select a vertex group",
                        icon='ADD')
        return
    layout.label(text=f"Target: {st.name}", icon='GROUP_VERTEX')
    row = layout.row()
    row.template_list("WL_UL_layers", "", st, "layers", st, "active_index", rows=4)
    col = row.column(align=True)
    col.operator("weight_layers.add", icon='ADD', text="")
    col.operator("weight_layers.remove", icon='REMOVE', text="")
    col.operator("weight_layers.duplicate", icon='DUPLICATE', text="")
    col.separator()
    col.operator("weight_layers.move", icon='TRIA_UP', text="").direction = 'UP'
    col.operator("weight_layers.move", icon='TRIA_DOWN', text="").direction = 'DOWN'
    if 0 <= st.active_index < len(st.layers):
        lay = st.layers[st.active_index]
        row = layout.row(align=True)
        row.prop(lay, "blend", text="")
        row.prop(lay, "opacity", slider=True)
    if ob.vertex_groups.active.name == st.name:
        layout.label(text="Viewing result: paint in a layer, not here", icon='INFO')
    row = layout.row(align=True)
    row.prop(st, "auto_update", toggle=True)
    row.prop(st, "normalize", toggle=True)
    row = layout.row(align=True)
    row.operator("weight_layers.show_result", icon='HIDE_OFF')
    row.operator("weight_layers.composite", icon='FILE_REFRESH', text="Update")
    row = layout.row(align=True)
    row.operator("weight_layers.mirror", icon='MOD_MIRROR')
    row.operator("weight_layers.flatten", icon='IMPORT')


class WL_PT_data(bpy.types.Panel):
    bl_label = "Weight Layers"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def draw(self, context):
        draw_stack(self.layout, context)


class WL_PT_view3d(bpy.types.Panel):
    bl_label = "Weight Layers"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Weights"

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_WEIGHT'

    def draw(self, context):
        draw_stack(self.layout, context)


classes = (WL_Layer, WL_Stack, WL_OT_create, WL_OT_add, WL_OT_duplicate, WL_OT_remove, WL_OT_move,
           WL_OT_composite, WL_OT_show_result, WL_OT_flatten, WL_OT_mirror, WL_UL_layers,
           WL_PT_data, WL_PT_view3d)

_handlers = (
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph),
    (bpy.app.handlers.undo_post, on_undo),
    (bpy.app.handlers.redo_post, on_undo),
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.weight_layers = bpy.props.CollectionProperty(type=WL_Stack)
    for lst, fn in _handlers:
        lst.append(fn)


def unregister():
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _dirty.clear()
    del bpy.types.Object.weight_layers
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
