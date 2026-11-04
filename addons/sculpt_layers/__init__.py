"""Sculpt Layers: non-destructive sculpt layers stored as relative shape keys.

A layer is a shape key named "SL: <name>" relative to the Basis. Blender sculpts on the active
shape key, so the active layer is the one being recorded. Result = Basis + sum over unmuted
layers of (layer - Basis) * strength * vertex-group weight, which is plain shape key mixing.
"""
from contextlib import contextmanager

import bpy
import numpy as np

PREFIX = "SL: "
MASK = ".sculpt_mask"


# ---------------------------------------------------------------- core

def co(kb):
    a = np.empty(len(kb.data) * 3, np.float32)
    kb.data.foreach_get("co", a)
    return a.reshape(-1, 3)


def set_co(kb, arr):
    kb.data.foreach_set("co", np.asarray(arr, np.float32).ravel())


def is_layer(kb):
    return kb.name.startswith(PREFIX)


def layers(ob):
    """Layer keys bottom-first (shape key order)."""
    keys = ob.data.shape_keys
    return [kb for kb in keys.key_blocks if is_layer(kb)] if keys else []


def group_weights(ob, name):
    """Per-vertex weights of a vertex group; no/missing group = 1 everywhere (Blender's rule)."""
    n = len(ob.data.vertices)
    vg = ob.vertex_groups.get(name) if name else None
    if vg is None:
        return np.ones(n, np.float32)
    w = np.zeros(n, np.float32)
    for v in ob.data.vertices:
        for g in v.groups:
            if g.group == vg.index:
                w[v.index] = g.weight
    return w


def delta(ob, kb):
    """What the key currently adds to the mesh (0 when muted)."""
    if kb.mute:
        return np.zeros((len(kb.data), 3), np.float32)
    d = co(kb) - co(kb.relative_key)
    return d * kb.value * group_weights(ob, kb.vertex_group)[:, None]


@contextmanager
def object_mode(ob):
    """Sculpt mode keeps its own caches: write key data in Object Mode, then switch back."""
    mode = ob.mode
    if mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        yield
    finally:
        ob.data.update()
        if mode != 'OBJECT':
            bpy.ops.object.mode_set(mode=mode)


def add_layer(ob, name="Layer", coords=None):
    if not ob.data.shape_keys:
        ob.shape_key_add(name="Basis", from_mix=False)
    keys = ob.data.shape_keys
    kb = ob.shape_key_add(name=PREFIX + name, from_mix=False)
    kb.relative_key = keys.reference_key
    kb.slider_min, kb.slider_max = -1.0, 2.0
    kb.value = 1.0
    if coords is not None:
        set_co(kb, coords)
    ob.active_shape_key_index = keys.key_blocks.find(kb.name)
    ob.show_only_shape_key = False  # pinned key would hide the other layers while sculpting
    ob.data.update()
    return kb


def remove_layer(ob, kb):
    keys = ob.data.shape_keys
    if keys.get("sl_solo") == kb.name:
        unsolo(ob)
    i = keys.key_blocks.find(kb.name)
    ob.shape_key_remove(kb)
    ob.active_shape_key_index = max(0, min(i, len(keys.key_blocks)) - 1) if keys.key_blocks else 0


def merge_down(ob, kb):
    """Fold kb into the layer below it, keeping the result. Returns the lower layer."""
    ls = layers(ob)
    i = ls.index(kb)
    if i == 0:
        raise ValueError("No layer below")
    low = ls[i - 1]
    if abs(low.value) < 1e-6:
        raise ValueError(f"'{low.name}' has strength 0")
    # ponytail: exact where the lower layer's mask weight is 1; under its mask the merged
    # sculpt is scaled by that weight too (no way to divide by 0 weight)
    set_co(low, co(low) + delta(ob, kb) / low.value)
    remove_layer(ob, kb)
    ob.active_shape_key_index = ob.data.shape_keys.key_blocks.find(low.name)
    return low


def flatten(ob):
    """Apply all unmuted layers into the Basis; other keys keep their shape relative to it."""
    keys = ob.data.shape_keys
    basis = keys.reference_key
    unsolo(ob)
    ls = [kb for kb in layers(ob) if not kb.mute]
    d = sum((delta(ob, kb) for kb in ls), np.zeros((len(basis.data), 3), np.float32))
    for kb in ls:
        ob.shape_key_remove(kb)
    for kb in keys.key_blocks:  # basis + every other key move together
        set_co(kb, co(kb) + d)
    ob.data.vertices.foreach_set("co", co(basis).ravel())
    ob.active_shape_key_index = 0
    return len(ls)


def bake(ob):
    """New layer holding the current mix of all layers; the source layers get muted."""
    keys = ob.data.shape_keys
    basis = keys.reference_key
    unsolo(ob)
    ls = [kb for kb in layers(ob) if not kb.mute]
    out = co(basis) + sum((delta(ob, kb) for kb in ls), np.zeros((len(basis.data), 3), np.float32))
    for kb in ls:
        kb.mute = True
    return add_layer(ob, "Baked", out)


def solo(ob, kb):
    keys = ob.data.shape_keys
    if keys.get("sl_solo") == kb.name:
        return unsolo(ob)
    unsolo(ob)
    keys["sl_mutes"] = {k.name: k.mute for k in keys.key_blocks}
    keys["sl_solo"] = kb.name
    for k in keys.key_blocks[1:]:
        k.mute = k != kb
    ob.data.update()


def unsolo(ob):
    keys = ob.data.shape_keys
    if not keys or "sl_solo" not in keys:
        return
    for name, m in keys.get("sl_mutes", {}).items():
        k = keys.key_blocks.get(name)
        if k:
            k.mute = bool(m)
    del keys["sl_solo"]
    keys.pop("sl_mutes", None)
    ob.data.update()


def mask_to_group(ob, kb, invert=False):
    """Sculpt mask -> new vertex group used as the layer's mask."""
    me = ob.data
    m = np.zeros(len(me.vertices), np.float32)
    a = me.attributes.get(MASK)
    if a:
        a.data.foreach_get("value", m)
    if invert:
        m = 1.0 - m
    vg = ob.vertex_groups.new(name=kb.name[len(PREFIX):] + " Mask")
    for w in np.unique(m):
        idx = np.flatnonzero(m == w).tolist()
        vg.add(idx, float(w), 'REPLACE')
    kb.vertex_group = vg.name
    me.update()
    return vg


def warnings(ob):
    out = []
    if any(md.type == 'MULTIRES' for md in ob.modifiers):
        out.append("Multires: sculpt layers don't work with it")
    if ob.use_dynamic_topology_sculpting:
        out.append("Dyntopo: drops shape keys, turn it off")
    mods = [md for md in ob.modifiers if md.show_viewport and md.type != 'MULTIRES']
    if mods:
        out.append(f"Layers apply before {len(mods)} modifier(s)")
    return out


# ---------------------------------------------------------------- operators

class _Op:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == 'MESH' and ob.mode in {'OBJECT', 'SCULPT'}

    @staticmethod
    def active(context):
        kb = context.object.active_shape_key
        return kb if kb and is_layer(kb) else None


class _LayerOp(_Op):
    @classmethod
    def poll(cls, context):
        return super().poll(context) and cls.active(context) is not None


class SL_OT_add(_Op, bpy.types.Operator):
    bl_idname = "sculpt_layers.add"
    bl_label = "Add Layer"
    bl_description = "Add an empty sculpt layer (shape key) and make it the one you sculpt on"

    def execute(self, context):
        ob = context.object
        with object_mode(ob):
            add_layer(ob, f"Layer {len(layers(ob)) + 1}")
        return {'FINISHED'}


class SL_OT_remove(_LayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.remove"
    bl_label = "Delete Layer"
    bl_description = "Delete the active sculpt layer"

    def execute(self, context):
        ob = context.object
        with object_mode(ob):
            remove_layer(ob, self.active(context))
        return {'FINISHED'}


class SL_OT_move(_LayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.move"
    bl_label = "Move Layer"
    bl_description = "Move the active layer up or down the stack"
    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")],
                                      options={'SKIP_SAVE'})

    def execute(self, context):
        ob = context.object
        keys = ob.data.shape_keys
        for k in keys.key_blocks:
            k.select = k == ob.active_shape_key
        # list is drawn top-first, so "up" = later in the shape key list
        with context.temp_override(object=ob, active_object=ob):
            bpy.ops.object.shape_key_move(type='DOWN' if self.direction == 'UP' else 'UP')
        return {'FINISHED'}


class SL_OT_solo(_Op, bpy.types.Operator):
    bl_idname = "sculpt_layers.solo"
    bl_label = "Solo Layer"
    bl_description = "Show only this layer (mutes the others); click again to restore their mute states"
    layer: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        ob = context.object
        kb = ob.data.shape_keys and ob.data.shape_keys.key_blocks.get(self.layer)
        if not kb:
            return {'CANCELLED'}
        solo(ob, kb)
        return {'FINISHED'}


class SL_OT_mask_from_sculpt(_LayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.mask_from_sculpt"
    bl_label = "Mask from Sculpt Mask"
    bl_description = "Turn the current sculpt mask into a vertex group that limits the active layer"
    invert: bpy.props.BoolProperty(name="Invert", default=False, options={'SKIP_SAVE'},
                                   description="Layer shows where the sculpt mask is NOT painted")

    def execute(self, context):
        ob = context.object
        with object_mode(ob):
            vg = mask_to_group(ob, self.active(context), self.invert)
        self.report({'INFO'}, f"Mask group '{vg.name}'")
        return {'FINISHED'}


class SL_OT_merge_down(_LayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.merge_down"
    bl_label = "Merge Down"
    bl_description = "Combine the active layer into the layer below at the current strengths"

    def execute(self, context):
        ob = context.object
        try:
            with object_mode(ob):
                merge_down(ob, self.active(context))
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class _AnyLayerOp(_Op):
    @classmethod
    def poll(cls, context):
        return super().poll(context) and bool(layers(context.object))


class SL_OT_flatten(_AnyLayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.flatten"
    bl_label = "Apply to Basis"
    bl_description = "Bake all visible layers into the Basis and delete them (other shape keys stay correct)"

    def execute(self, context):
        ob = context.object
        with object_mode(ob):
            n = flatten(ob)
        self.report({'INFO'}, f"Applied {n} layer(s)")
        return {'FINISHED'}


class SL_OT_bake(_AnyLayerOp, bpy.types.Operator):
    bl_idname = "sculpt_layers.bake"
    bl_label = "Bake Mix"
    bl_description = "Combine all visible layers into one new layer and mute the originals"

    def execute(self, context):
        ob = context.object
        with object_mode(ob):
            bake(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class SL_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        row = layout.row(align=True)
        row.prop(item, "mute", text="", emboss=False, invert_checkbox=True,
                 icon='HIDE_ON' if item.mute else 'HIDE_OFF')
        row.prop(item, "name", text="", emboss=False)
        row.prop(item, "value", text="", slider=True)
        soloed = data.get("sl_solo") == item.name
        row.operator("sculpt_layers.solo", text="", emboss=soloed,
                     icon='SOLO_ON' if soloed else 'SOLO_OFF').layer = item.name

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item if is_layer(k) else 0 for k in items]
        n = len(items)
        return flags, [n - 1 - i for i in range(n)]  # top layer first


class SL_PT_layers(bpy.types.Panel):
    bl_label = "Sculpt Layers"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Sculpt"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == 'MESH' and context.mode in {'SCULPT', 'OBJECT'}

    def draw(self, context):
        layout = self.layout
        ob = context.object
        for w in warnings(ob):
            layout.label(text=w, icon='ERROR')
        keys = ob.data.shape_keys
        row = layout.row()
        if keys:
            row.template_list("SL_UL_layers", "", keys, "key_blocks", ob, "active_shape_key_index", rows=4)
        else:
            row.label(text="No layers yet")
        col = row.column(align=True)
        col.operator("sculpt_layers.add", icon='ADD', text="")
        col.operator("sculpt_layers.remove", icon='REMOVE', text="")
        col.separator()
        col.operator("sculpt_layers.move", icon='TRIA_UP', text="").direction = 'UP'
        col.operator("sculpt_layers.move", icon='TRIA_DOWN', text="").direction = 'DOWN'
        kb = ob.active_shape_key
        if kb and is_layer(kb):
            row = layout.row(align=True)
            row.prop_search(kb, "vertex_group", ob, "vertex_groups", text="Mask")
            row.operator("sculpt_layers.mask_from_sculpt", text="", icon='MOD_MASK')
            if ob.show_only_shape_key:
                layout.prop(ob, "show_only_shape_key", text="Shape Lock hides other layers")
        elif keys:
            layout.label(text="Sculpting the Basis, not a layer", icon='INFO')
        row = layout.row(align=True)
        row.operator("sculpt_layers.merge_down", icon='TRIA_DOWN_BAR')
        row.operator("sculpt_layers.bake", icon='RENDERLAYERS')
        layout.operator("sculpt_layers.flatten", icon='IMPORT')


classes = (SL_OT_add, SL_OT_remove, SL_OT_move, SL_OT_solo, SL_OT_mask_from_sculpt,
           SL_OT_merge_down, SL_OT_flatten, SL_OT_bake, SL_UL_layers, SL_PT_layers)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
