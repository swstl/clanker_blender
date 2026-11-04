"""Shape Key Tools: L/R split for facial blendshapes, empty-key cleanup, reset values."""
import numpy as np

import bpy


def _co(kb):
    a = np.empty(len(kb.data) * 3, dtype=np.float32)
    kb.data.foreach_get("co", a)
    return a.reshape(-1, 3)


def side_weights(xs, blend):
    """Weight of the LEFT side (+X in Blender's convention) per vertex: 1 on the left, 0 on the right,
    smooth ramp over [-blend, +blend] around the center."""
    if blend <= 0:
        return np.where(xs > 0, 1.0, np.where(xs < 0, 0.0, 0.5))
    t = np.clip((xs + blend) / (2 * blend), 0.0, 1.0)
    return t * t * (3 - 2 * t)  # smoothstep


def split_lr(ob, kb, blend=0.0, remove_original=False, suffixes=(".L", ".R")):
    """Create kb.L and kb.R from kb. Halves sum exactly to the original. Returns (left, right)."""
    keys = ob.data.shape_keys
    ref = kb.relative_key
    base, shape = _co(ref), _co(kb)
    delta = shape - base
    wl = side_weights(base[:, 0], blend)[:, None]
    out = []
    for suffix, w in zip(suffixes, (wl, 1.0 - wl)):
        name = kb.name + suffix
        new = keys.key_blocks.get(name) or ob.shape_key_add(name=name, from_mix=False)
        new.data.foreach_set("co", (base + delta * w).astype(np.float32).ravel())
        new.relative_key = ref
        new.slider_min, new.slider_max = kb.slider_min, kb.slider_max
        new.vertex_group = kb.vertex_group
        new.value = 0.0
        out.append(new)
    if remove_original:
        ob.shape_key_remove(kb)
    ob.data.update()
    return tuple(out)


def empty_keys(ob, tol=1e-5):
    keys = ob.data.shape_keys
    if not keys:
        return []
    basis = keys.reference_key
    out = []
    for kb in keys.key_blocks:
        if kb == basis:
            continue
        if np.abs(_co(kb) - _co(kb.relative_key)).max(initial=0) <= tol:
            out.append(kb)
    return out


class SKT_OT_split(bpy.types.Operator):
    bl_idname = "shapekey_tools.split_lr"
    bl_label = "Split Left/Right"
    bl_description = "Split the active shape key into .L and .R keys along the X axis (they add up to the original)"
    bl_options = {'REGISTER', 'UNDO'}

    blend: bpy.props.FloatProperty(name="Center Blend", default=0.02, min=0, unit='LENGTH', subtype='DISTANCE',
                                   description="Width of the soft transition around the middle (local units)")
    remove_original: bpy.props.BoolProperty(name="Remove Original", default=False)

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob and ob.type == 'MESH' and ob.data.shape_keys and ob.active_shape_key_index > 0

    def execute(self, context):
        ob = context.object
        if ob.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        l, r = split_lr(ob, ob.active_shape_key, self.blend, self.remove_original)
        self.report({'INFO'}, f"Created {l.name}, {r.name}")
        return {'FINISHED'}


class SKT_OT_clean(bpy.types.Operator):
    bl_idname = "shapekey_tools.remove_empty"
    bl_label = "Remove Empty Shape Keys"
    bl_description = "Delete shape keys that do not move any vertex (on selected meshes)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = 0
        for ob in context.selected_objects or [context.object]:
            if ob and ob.type == 'MESH':
                for kb in empty_keys(ob):
                    ob.shape_key_remove(kb)
                    n += 1
        self.report({'INFO'}, f"Removed {n} empty shape keys")
        return {'FINISHED'}


class SKT_OT_reset(bpy.types.Operator):
    bl_idname = "shapekey_tools.reset_values"
    bl_label = "Reset All Values"
    bl_description = "Set every shape key value to 0 (skips keys driven or animated)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ob = context.object
        keys = ob.data.shape_keys if ob and ob.type == 'MESH' else None
        if not keys:
            return {'CANCELLED'}
        driven = set()
        ad = keys.animation_data
        if ad:
            driven = {d.data_path for d in ad.drivers}
        for kb in keys.key_blocks:
            if f'key_blocks["{kb.name}"].value' not in driven:
                kb.value = 0.0
        return {'FINISHED'}


def menu(self, context):
    lay = self.layout
    lay.separator()
    lay.operator("shapekey_tools.split_lr", icon='MOD_MIRROR')
    lay.operator("shapekey_tools.remove_empty", icon='TRASH')
    lay.operator("shapekey_tools.reset_values", icon='LOOP_BACK')


_classes = (SKT_OT_split, SKT_OT_clean, SKT_OT_reset)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.MESH_MT_shape_key_context_menu.append(menu)


def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
