"""Stepped Preview: Maya-style 'stepped preview' toggle for blocking, fully reversible.

Hold Keys: every key holds until the next one (interpolation set to Constant, originals stored).
Every N Frames: a muted-able Stepped F-Modifier (on 2s / on 3s look) on top of the real curves.
"""
import json

import bpy
from bpy_extras import anim_utils

MOD_NAME = "StepPreview"
STORE = "stepped_preview_interp"


def _actions(context, scope):
    if scope == 'ALL':
        return [a for a in bpy.data.actions if not a.library]
    acts = []
    for ob in context.selected_objects:
        for idb in (ob, ob.data, getattr(ob.data, "shape_keys", None)):
            ad = getattr(idb, "animation_data", None) if idb else None
            if ad and ad.action and ad.action not in acts:
                acts.append(ad.action)
    return acts


def _fcurves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                yield from cb.fcurves


def is_active(actions):
    return any(STORE in a or any(m.name == MOD_NAME for fc in _fcurves(a) for m in fc.modifiers)
               for a in actions)


def enable_hold(action):
    store = {}
    for fc in _fcurves(action):
        key = f"{fc.data_path}|{fc.array_index}"
        store[key] = {str(round(k.co.x, 3)): k.interpolation for k in fc.keyframe_points}
        for k in fc.keyframe_points:
            k.interpolation = 'CONSTANT'
    action[STORE] = json.dumps(store)


def enable_steps(action, step, offset=0):
    for fc in _fcurves(action):
        m = fc.modifiers.new('STEPPED')
        m.name = MOD_NAME
        m.frame_step = step
        m.frame_offset = offset  # steps start on the scene's first frame


def disable(action):
    """Restore interpolation (keys added while stepped become Bezier) and remove step modifiers."""
    if STORE in action:
        store = json.loads(action[STORE])
        for fc in _fcurves(action):
            saved = store.get(f"{fc.data_path}|{fc.array_index}", {})
            for k in fc.keyframe_points:
                k.interpolation = saved.get(str(round(k.co.x, 3)), 'BEZIER')
        del action[STORE]
    for fc in _fcurves(action):
        for m in [m for m in fc.modifiers if m.name == MOD_NAME]:
            fc.modifiers.remove(m)


class SP_OT_toggle(bpy.types.Operator):
    bl_idname = "stepped_preview.toggle"
    bl_label = "Toggle Stepped Preview"
    bl_description = "Switch between stepped (blocking) and smooth playback without changing your curves for good"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(name="Mode", items=[
        ('HOLD', "Hold Keys", "Each key holds until the next key"),
        ('STEPS', "Every N Frames", "Sample the animation every N frames (on 2s, on 3s)")])
    step: bpy.props.IntProperty(name="Step", default=2, min=1)
    scope: bpy.props.EnumProperty(name="Scope", items=[('SELECTED', "Selected Objects", ""),
                                                       ('ALL', "All Actions", "")])

    def execute(self, context):
        acts = _actions(context, self.scope)
        if not acts:
            self.report({'INFO'}, "No animation found")
            return {'CANCELLED'}
        if is_active(acts):
            for a in acts:
                disable(a)
            self.report({'INFO'}, "Smooth playback")
        else:
            for a in acts:
                enable_hold(a) if self.mode == 'HOLD' else enable_steps(a, self.step, context.scene.frame_start)
            self.report({'INFO'}, "Stepped preview ON (toggle again to restore)")
        return {'FINISHED'}


def header(self, context):
    on = is_active(_actions(context, 'SELECTED')) if context.selected_objects else False
    self.layout.operator("stepped_preview.toggle", text="", icon='IPO_CONSTANT' if on else 'IPO_BEZIER',
                         depress=on)


_HEADERS = ("DOPESHEET_HT_playback_controls", "GRAPH_HT_playback_controls")


def register():
    bpy.utils.register_class(SP_OT_toggle)
    for h in _HEADERS:
        getattr(bpy.types, h).append(header)


def unregister():
    for h in _HEADERS:
        getattr(bpy.types, h).remove(header)
    bpy.utils.unregister_class(SP_OT_toggle)
