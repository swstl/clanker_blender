"""Key Tweener: AnimAide / tweenMachine style keyframe sliders for slotted actions (5.x)."""
from bisect import bisect_right

import bpy
from bpy_extras import anim_utils


# ---------------------------------------------------------------------------
# Pure math over (frames, values, selected) lists. Each returns new values.
# ---------------------------------------------------------------------------

def _lerp(a, b, t):
    return a + (b - a) * t


def _blocks(sel):
    """Yield (indices, prev, next) per run of selected keys. prev/next are the neighbouring
    unselected keys, or the run's own edge key when the run touches the curve's end."""
    i, n = 0, len(sel)
    while i < n:
        if not sel[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and sel[j + 1]:
            j += 1
        yield range(i, j + 1), (i - 1 if i else i), (j + 1 if j + 1 < n else j)
        i = j + 1


def _t(fr, p, n, i):
    return (fr[i] - fr[p]) / (fr[n] - fr[p]) if fr[n] != fr[p] else 0.0


def tween(fr, v, sel, f, **_):
    """-1 = previous neighbour, 0 = halfway, +1 = next neighbour."""
    out = list(v)
    for idx, p, n in _blocks(sel):
        for i in idx:
            out[i] = _lerp(v[p], v[n], (f + 1) / 2)
    return out


def blend_neighbor(fr, v, sel, f, **_):
    out = list(v)
    for idx, p, n in _blocks(sel):
        for i in idx:
            out[i] = _lerp(v[i], v[n] if f > 0 else v[p], abs(f))
    return out


def ease(fr, v, sel, f, **_):
    """Place keys on an ease curve between the neighbours: <0 ease in, >0 ease out, 0 linear."""
    e = 1 + 4 * abs(f)
    out = list(v)
    for idx, p, n in _blocks(sel):
        for i in idx:
            t = _t(fr, p, n, i)
            t = 1 - (1 - t) ** e if f > 0 else t ** e
            out[i] = _lerp(v[p], v[n], t)
    return out


def push_pull(fr, v, sel, f, **_):
    """Scale away from (+) / toward (-) the straight line between the neighbours."""
    out = list(v)
    for idx, p, n in _blocks(sel):
        for i in idx:
            line = _lerp(v[p], v[n], _t(fr, p, n, i))
            out[i] = line + (v[i] - line) * (1 + f)
    return out


def scale_average(fr, v, sel, f, **_):
    chosen = [x for x, s in zip(v, sel) if s]
    if not chosen:
        return list(v)
    avg = sum(chosen) / len(chosen)
    return [avg + (x - avg) * (1 + f) if s else x for x, s in zip(v, sel)]


def blend_default(fr, v, sel, f, default=0.0, **_):
    return [_lerp(x, default, f) if s else x for x, s in zip(v, sel)]


def smooth(fr, v, sel, f, **_):
    n = len(v)
    return [_lerp(x, (v[max(i - 1, 0)] + v[min(i + 1, n - 1)]) / 2, f) if s else x
            for i, (x, s) in enumerate(zip(v, sel))]


def linear_sampler(fr, v):
    def sample(x):
        i = min(max(bisect_right(fr, x), 1), len(fr) - 1)
        span = fr[i] - fr[i - 1]
        return _lerp(v[i - 1], v[i], (x - fr[i - 1]) / span if span else 0.0)
    return sample


def time_offset(fr, v, sel, f, sample=None, **_):
    """Shift values in time by f * half the curve's length, wrapping around (cyclic)."""
    span = fr[-1] - fr[0]
    if span <= 0:
        return list(v)
    sample = sample or linear_sampler(fr, v)
    off = f * span / 2
    return [sample(fr[0] + (x + off - fr[0]) % span) if s else y
            for x, y, s in zip(fr, v, sel)]


TOOLS = (  # (id, label, function, description)
    ("tween", "Tween", tween, "Breakdown between the previous and next unselected keys"),
    ("blend_neighbor", "Blend to Neighbor", blend_neighbor,
     "Blend keys toward the previous (left) or next (right) key"),
    ("ease", "Ease", ease, "Place keys on an ease-in (left) / ease-out (right) curve between neighbours"),
    ("push_pull", "Push/Pull", push_pull, "Exaggerate (right) or soften (left) keys relative to neighbours"),
    ("scale_average", "Scale Average", scale_average, "Scale keys around their average value"),
    ("blend_default", "Blend to Default", blend_default,
     "Blend keys toward the rest value (0, scale 1, quaternion W 1)"),
    ("smooth", "Smooth", smooth, "Average keys with their neighbours (left sharpens)"),
    ("time_offset", "Time Offset", time_offset, "Shift the curve's values in time (cyclic)"),
)
FUNCS = {t[0]: t[2] for t in TOOLS}


def default_value(path, index):
    if path.endswith("scale"):
        return 1.0
    if (path.endswith("rotation_quaternion") and index == 0) or \
            (path.endswith("rotation_axis_angle") and index == 2):
        return 1.0
    return 0.0


# ---------------------------------------------------------------------------
# Gathering keys and applying
# ---------------------------------------------------------------------------

def _object_fcurves(context):
    obs = set(context.selected_objects or ())
    if context.active_object:
        obs.add(context.active_object)
    for ob in obs:
        bones = {pb.name for pb in ob.pose.bones if pb.select} if ob.mode == 'POSE' and ob.pose else None
        for idb in (ob, ob.data, getattr(ob.data, "shape_keys", None)):
            ad = getattr(idb, "animation_data", None) if idb else None
            if not (ad and ad.action and ad.action_slot):
                continue
            cb = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
            for fc in (cb.fcurves if cb else ()):
                if bones is not None and not (idb is ob and fc.data_path.startswith('pose.bones["')
                                              and fc.data_path.split('"')[1] in bones):
                    continue
                yield fc


def _fcurves(context):
    if context.area and context.area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR'}:
        fcs = getattr(context, "visible_fcurves", None)
        if fcs is not None:
            return [fc for fc in fcs if not fc.lock]
    return [fc for fc in _object_fcurves(context) if not fc.lock]


class Snapshot:
    """Original key state of every affected curve, so the slider can re-apply and restore."""

    def __init__(self, context, mode='AUTO'):
        if mode == 'AUTO':
            mode = 'FRAME' if context.area and context.area.type == 'VIEW_3D' else 'KEYS'
        self.curves = []
        fcs = _fcurves(context)
        if mode == 'KEYS':
            for fc in fcs:
                sel = [kp.select_control_point for kp in fc.keyframe_points]
                if any(sel):
                    self._add(fc, sel, None)
            if self.curves or context.area is None or context.area.type == 'VIEW_3D':
                return
            mode = 'FRAME'  # editor with nothing selected: fall back to the playhead
        frame = context.scene.frame_current
        for fc in fcs:
            kps = fc.keyframe_points
            if len(kps) < 2:
                continue
            frames = [kp.co.x for kp in kps]
            inserted = None
            if not any(abs(x - frame) < 1e-4 for x in frames):
                if not frames[0] < frame < frames[-1]:
                    continue
                kps.insert(frame, fc.evaluate(frame))
                fc.update()
                inserted = frame
            self._add(fc, [abs(kp.co.x - frame) < 1e-4 for kp in kps], inserted)

    def _add(self, fc, sel, inserted):
        kps = fc.keyframe_points
        self.curves.append(dict(
            fc=fc, sel=sel, inserted=inserted,
            frames=[kp.co.x for kp in kps], values=[kp.co.y for kp in kps],
            hl=[kp.handle_left.y for kp in kps], hr=[kp.handle_right.y for kp in kps],
            default=default_value(fc.data_path, fc.array_index)))

    def restore(self, remove_inserted=False):
        for c in self.curves:
            fc = c["fc"]
            kps = fc.keyframe_points
            for i, kp in enumerate(kps):
                if c["sel"][i]:
                    kp.co.y, kp.handle_left.y, kp.handle_right.y = c["values"][i], c["hl"][i], c["hr"][i]
            if remove_inserted and c["inserted"] is not None:
                kps.remove(kps[c["sel"].index(True)])
            fc.update()

    def apply(self, tool, factor):
        self.restore()  # time offset samples the live curve, so it must be the original
        for c in self.curves:
            fc = c["fc"]
            new = FUNCS[tool](c["frames"], c["values"], c["sel"], factor,
                              default=c["default"], sample=fc.evaluate)
            for i, kp in enumerate(fc.keyframe_points):
                if c["sel"][i]:
                    d = new[i] - c["values"][i]
                    kp.co.y = new[i]
                    kp.handle_left.y = c["hl"][i] + d
                    kp.handle_right.y = c["hr"][i] + d
            fc.update()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

_mode_items = [('AUTO', "Auto", "3D View: current frame. Graph/Dope Sheet: selected keys "
                "(current frame when none are selected)"),
               ('KEYS', "Selected Keys", "Selected keyframes"),
               ('FRAME', "Current Frame", "Keys at the playhead, inserted between neighbours if missing")]


class _SliderBase:
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING'}
    tool = ""

    factor: bpy.props.FloatProperty(name="Factor", default=0.0, min=-1.0, max=1.0,
                                    subtype='FACTOR', description="-100% .. +100%")
    mode: bpy.props.EnumProperty(name="Keys", items=_mode_items)

    def execute(self, context):
        snap = Snapshot(context, self.mode)
        if not snap.curves:
            self.report({'WARNING'}, "No keys to adjust")
            return {'CANCELLED'}
        snap.apply(self.tool, self.factor)
        return {'FINISHED'}

    def invoke(self, context, event):
        self._snap = Snapshot(context, self.mode)
        if not self._snap.curves:
            self.report({'WARNING'}, "No keys to adjust")
            return {'CANCELLED'}
        self._x0 = event.mouse_x
        self._px = 250 * context.preferences.system.ui_scale  # mouse travel for 100%
        self.factor = 0.0
        self._update(context, event)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _update(self, context, event):
        f = max(-1.0, min(1.0, (event.mouse_x - self._x0) / self._px))
        if event.ctrl:
            f = round(f * 20) / 20
        self.factor = f
        self._snap.apply(self.tool, f)
        if context.area:
            context.area.header_text_set(
                f"{self.bl_label}: {f * 100:+.0f}%   Ctrl: 5% steps   LMB/Enter: confirm   RMB/Esc: cancel")
        for a in context.screen.areas:
            a.tag_redraw()

    def _finish(self, context):
        if context.area:
            context.area.header_text_set(None)
        for a in context.screen.areas:
            a.tag_redraw()

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._update(context, event)
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._snap.restore(remove_inserted=True)
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    def cancel(self, context):  # file load / window close drops the handler: revert like Esc
        self._snap.restore(remove_inserted=True)
        if context.area:
            context.area.header_text_set(None)


_ops = [type(f"KTW_OT_{tid}", (_SliderBase, bpy.types.Operator), {
    "bl_idname": f"key_tweener.{tid}", "bl_label": label,
    "bl_description": desc + ". Drag left/right; Ctrl for 5% steps",
    "tool": tid, "__annotations__": {}}) for tid, label, _f, desc in TOOLS]


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _draw(self, context):
    col = self.layout.column(align=True)
    for tid, label, _f, _d in TOOLS:
        col.operator(f"key_tweener.{tid}", text=label)
    self.layout.label(text="Tween")
    row = self.layout.row(align=True)
    row.operator_context = 'EXEC_DEFAULT'
    for p in (-75, -50, -25, 0, 25, 50, 75):
        row.operator("key_tweener.tween", text=str(p)).factor = p / 100


_panels = [type(f"KTW_PT_{space.lower()}", (bpy.types.Panel,), {
    "bl_label": "Key Tweener", "bl_space_type": space, "bl_region_type": 'UI',
    "bl_category": "Animation", "draw": _draw})
    for space in ('VIEW_3D', 'GRAPH_EDITOR', 'DOPESHEET_EDITOR')]

_keymaps = []


def _set_keymap(enabled):
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    kc = bpy.context.window_manager.keyconfigs.addon
    if enabled and kc:
        km = kc.keymaps.new(name="Graph Editor", space_type='GRAPH_EDITOR')
        _keymaps.append((km, km.keymap_items.new("key_tweener.tween", 'W', 'PRESS', shift=True)))


class KTW_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    tween_hotkey: bpy.props.BoolProperty(
        name="Shift+W: Tween slider in Graph Editor", default=False,
        update=lambda self, ctx: _set_keymap(self.tween_hotkey))

    def draw(self, context):
        self.layout.prop(self, "tween_hotkey")


_classes = (*_ops, *_panels, KTW_Preferences)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    _set_keymap(bpy.context.preferences.addons[__package__].preferences.tween_hotkey)


def unregister():
    _set_keymap(False)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
