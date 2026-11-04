"""GP Timing: 2D timing tools for Grease Pencil v3 (exposure, ripple holds, retime, layer navigator).

Natively covered and only exposed in the panel: insert blank keyframe (grease_pencil.insert_blank_frame),
duplicate active drawing to playhead (grease_pencil.frame_duplicate), interpolate, clean duplicate frames.
"""
import bpy

SOLO_KEY = "gp_timing_solo"


# ---------- pure ripple math (frame numbers only) ----------

def ripple_map(frames, start, amount):
    """{old: new} for frames after `start` shifted by `amount`. When removing (amount<0) frames in
    (start, start-amount] map to None (deleted)."""
    out = {}
    for f in frames:
        if f > start:
            out[f] = None if amount < 0 and f <= start - amount else f + amount
    return out


def exposure_ripple(frames, key, length):
    """(start, amount) for ripple_map so `key` holds `length` frames; None if `key` has no next frame.
    Time is added/removed at the end of the hold, so other layers' keys inside it stay put."""
    later = [f for f in frames if f > key]
    if not later:
        return None
    nxt = min(later)
    amount = length - (nxt - key)
    return nxt - 1 + min(amount, 0), amount


def retime_map(frames, step):
    """Evenly space `frames` by `step`, starting at the first one."""
    s = sorted(set(frames))
    return {f: s[0] + i * step for i, f in enumerate(s)}


# ---------- applying to Grease Pencil data ----------

def keys(layer):
    """Drawing frames only. End (null) frames from fixed-duration keys can't be moved, copied or
    removed from Python (move/copy segfault in 5.2); they follow their drawing when it moves."""
    # ponytail: a fixed-duration key keeps its duration under ripple; editable once RNA exposes null frames
    return [f for f in layer.frames if layer.get_frame_at(f.frame_number) is not None]


def apply_map(layer, mapping):
    """Move/delete layer frames per mapping (must keep order). Unmapped frames sitting on a target
    are overwritten, like a dope sheet transform."""
    ks = keys(layer)
    nums = {f.frame_number for f in ks}
    sel = {f.frame_number for f in ks if f.select}  # frames.move drops selection
    mapping = {o: n for o, n in mapping.items() if o in nums and n != o}
    targets = {n for n in mapping.values() if n is not None}
    for f in sorted(nums):
        if (f in mapping and mapping[f] is None) or (f not in mapping and f in targets):
            layer.frames.remove(f)
    moves = [(o, n) for o, n in mapping.items() if n is not None]
    # Right movers from the end, then left movers from the start: an order-keeping map never collides.
    for o, n in sorted((m for m in moves if m[1] > m[0]), reverse=True) + sorted(m for m in moves if m[1] < m[0]):
        layer.frames.move(o, n).select = o in sel


def gp_datas(context):
    """Grease Pencil data of selected + active objects (deduplicated)."""
    obs = list(context.selected_objects)
    if context.active_object and context.active_object not in obs:
        obs.append(context.active_object)
    out = []
    for ob in obs:
        if ob.type == 'GREASEPENCIL' and ob.data not in out:
            out.append(ob.data)
    return out


def ripple_layers(gp, all_layers):
    act = gp.layers.active
    return [l for l in gp.layers if not l.lock and (all_layers or l.select or l == act)]


def ripple(gp, start, amount, all_layers):
    for layer in ripple_layers(gp, all_layers):
        apply_map(layer, ripple_map([f.frame_number for f in keys(layer)], start, amount))
    gp.update_tag()


def set_exposure(gp, frame, length, all_layers=False, selected=False, relative=False):
    """Set the hold of the active layer's key at `frame` (or of its selected keys) to `length`
    (or change it by `length` when relative), rippling later frames. Returns keys changed."""
    act = gp.layers.active
    if act is None:
        return 0
    if selected:
        targets = [f.frame_number for f in keys(act) if f.select]
    else:
        cur = act.get_frame_at(frame)
        targets = [cur.frame_number] if cur else []
    n = 0
    for key in sorted(targets, reverse=True):  # later keys first: earlier ripples don't move them
        nums = [f.frame_number for f in keys(act)]
        if relative:
            later = [f for f in nums if f > key]
            if not later:
                continue
            target = max(1, min(later) - key + length)
        else:
            target = length
        r = exposure_ripple(nums, key, target)
        if r and r[1]:
            ripple(gp, r[0], r[1], all_layers)
            n += 1
    return n


def retime(gp, step):
    """Evenly space selected keys (all unlocked layers, synced by frame number)."""
    layers = [l for l in gp.layers if not l.lock]
    sel = [f.frame_number for l in layers for f in keys(l) if f.select]
    if not sel:
        return 0
    m = retime_map(sel, step)
    for l in layers:
        apply_map(l, {f.frame_number: m[f.frame_number] for f in keys(l) if f.select})
    gp.update_tag()
    return len(set(sel))


def toggle_solo(gp):
    """Hide all but the active layer, or restore the visibility saved by the last solo."""
    if SOLO_KEY in gp:
        hidden = set(gp[SOLO_KEY].split("\n")) - {""}
        for l in gp.layers:
            l.hide = l.name in hidden
        del gp[SOLO_KEY]
        return False
    gp[SOLO_KEY] = "\n".join(l.name for l in gp.layers if l.hide)
    for l in gp.layers:
        l.hide = l != gp.layers.active
    return True


# ---------- operators ----------

_all_layers = bpy.props.BoolProperty(
    name="All Layers", description="Ripple every unlocked layer instead of only selected layers + active")


class GPT_OT_set_exposure(bpy.types.Operator):
    bl_idname = "gp_timing.set_exposure"
    bl_label = "Set Exposure"
    bl_description = ("Set how long the active layer's key under the playhead (or its selected keys) is held, "
                      "rippling later keys on the selected layers")
    bl_options = {'REGISTER', 'UNDO'}

    length: bpy.props.IntProperty(name="Frames", default=2, description="Hold length, or change when Relative")
    relative: bpy.props.BoolProperty(name="Relative", description="Add Frames to the current hold")
    selected: bpy.props.BoolProperty(name="Selected Keys", description="Use selected keys instead of the playhead key")
    all_layers: _all_layers

    def invoke(self, context, event):
        if self.selected:
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        n = sum(set_exposure(gp, context.scene.frame_current, self.length, self.all_layers,
                             self.selected, self.relative) for gp in gp_datas(context))
        self.report({'INFO'}, f"Retimed {n} key(s)")
        return {'FINISHED'}


class GPT_OT_shift_hold(bpy.types.Operator):
    bl_idname = "gp_timing.shift_hold"
    bl_label = "Insert/Remove Hold"
    bl_description = "Insert (or remove) frames after the playhead on the selected layers, rippling later keys"
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.IntProperty(name="Frames", default=1, description="Positive inserts, negative removes")
    all_layers: _all_layers

    def execute(self, context):
        for gp in gp_datas(context):
            ripple(gp, context.scene.frame_current, self.amount, self.all_layers)
        return {'FINISHED'}


class GPT_OT_retime(bpy.types.Operator):
    bl_idname = "gp_timing.retime"
    bl_label = "Retime Selected Keys"
    bl_description = "Space the selected keys evenly, N frames apart, starting from the first selected key"
    bl_options = {'REGISTER', 'UNDO'}

    step: bpy.props.IntProperty(name="Spacing", default=2, min=1)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = sum(retime(gp, self.step) for gp in gp_datas(context))
        self.report({'INFO'}, f"Retimed {n} frame(s)")
        return {'FINISHED'}


class GPT_OT_jump_key(bpy.types.Operator):
    bl_idname = "gp_timing.jump_key"
    bl_label = "Jump to Key"
    bl_description = "Jump to the next/previous Grease Pencil key on any visible layer of the selected objects"
    bl_options = {'REGISTER'}

    next: bpy.props.BoolProperty(name="Next", default=True)

    def execute(self, context):
        cf = context.scene.frame_current
        keys = {f.frame_number for gp in gp_datas(context) for l in gp.layers if not l.hide for f in l.frames}
        keys = [k for k in keys if (k > cf if self.next else k < cf)]
        if not keys:
            return {'CANCELLED'}
        context.scene.frame_set(min(keys) if self.next else max(keys))
        return {'FINISHED'}


def _active_gp(context):
    ob = context.active_object
    return ob.data if ob and ob.type == 'GREASEPENCIL' else None


class _GPOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_gp(context) is not None


class GPT_OT_layer_solo(_GPOp, bpy.types.Operator):
    bl_idname = "gp_timing.layer_solo"
    bl_label = "Solo Layer"
    bl_description = "Hide every layer except the active one; run again to restore the previous visibility"

    def execute(self, context):
        toggle_solo(_active_gp(context))
        return {'FINISHED'}


class GPT_OT_lock_others(_GPOp, bpy.types.Operator):
    bl_idname = "gp_timing.lock_others"
    bl_label = "Lock Others"
    bl_description = "Lock every layer except the active one (unlocks them when all are already locked)"

    def execute(self, context):
        gp = _active_gp(context)
        others = [l for l in gp.layers if l != gp.layers.active]
        lock = not all(l.lock for l in others)
        for l in others:
            l.lock = lock
        return {'FINISHED'}


class GPT_OT_layer_step(_GPOp, bpy.types.Operator):
    bl_idname = "gp_timing.layer_step"
    bl_label = "Next/Previous Layer"
    bl_description = "Make the layer above/below the active one active"

    direction: bpy.props.IntProperty(name="Direction", default=1)

    def execute(self, context):
        gp = _active_gp(context)
        layers = list(gp.layers)
        if layers:
            i = layers.index(gp.layers.active) if gp.layers.active in layers else 0
            gp.layers.active = layers[(i + self.direction) % len(layers)]
        return {'FINISHED'}


class GPT_OT_layer_activate(_GPOp, bpy.types.Operator):
    bl_idname = "gp_timing.layer_activate"
    bl_label = "Set Active Layer"
    bl_description = "Make this layer active"

    name: bpy.props.StringProperty(name="Layer")

    def execute(self, context):
        gp = _active_gp(context)
        layer = next((l for l in gp.layers if l.name == self.name), None)
        if layer is None:
            return {'CANCELLED'}
        gp.layers.active = layer
        return {'FINISHED'}


# ---------- UI ----------

def draw_timing(layout, context):
    col = layout.column(align=True)
    col.label(text="Exposure (playhead key)")
    row = col.row(align=True)
    for n in (1, 2, 3, 4):
        row.operator(GPT_OT_set_exposure.bl_idname, text=f"{n}s").length = n
    row = col.row(align=True)
    for d, icon in ((-1, 'REMOVE'), (1, 'ADD')):
        op = row.operator(GPT_OT_set_exposure.bl_idname, text=f"{d:+d}", icon=icon)
        op.length, op.relative = d, True
    col.operator(GPT_OT_set_exposure.bl_idname, text="Selected Keys...").selected = True
    col.operator(GPT_OT_retime.bl_idname)
    col = layout.column(align=True)
    col.label(text="Hold at Playhead")
    row = col.row(align=True)
    row.operator(GPT_OT_shift_hold.bl_idname, text="Insert", icon='ADD').amount = 1
    row.operator(GPT_OT_shift_hold.bl_idname, text="Remove", icon='REMOVE').amount = -1
    col = layout.column(align=True)
    col.label(text="Keys")
    col.operator("grease_pencil.insert_blank_frame", text="Blank Keyframe")
    col.operator("grease_pencil.frame_duplicate", text="Duplicate Drawing to Playhead")
    row = col.row(align=True)
    row.operator(GPT_OT_jump_key.bl_idname, text="Prev Key", icon='PREV_KEYFRAME').next = False
    row.operator(GPT_OT_jump_key.bl_idname, text="Next Key", icon='NEXT_KEYFRAME').next = True


def draw_layers(layout, context):
    gp = _active_gp(context)
    if gp is None:
        layout.label(text="No active Grease Pencil")
        return
    row = layout.row(align=True)
    row.operator(GPT_OT_layer_step.bl_idname, text="", icon='TRIA_UP').direction = 1
    row.operator(GPT_OT_layer_step.bl_idname, text="", icon='TRIA_DOWN').direction = -1
    row.operator(GPT_OT_layer_solo.bl_idname, text="Restore" if SOLO_KEY in gp else "Solo", icon='HIDE_OFF')
    row.operator(GPT_OT_lock_others.bl_idname, icon='LOCKED')
    col = layout.column(align=True)
    for l in reversed(list(gp.layers)):  # top layer first, like the layer list
        row = col.row(align=True)
        row.operator(GPT_OT_layer_activate.bl_idname, text=l.name,
                     depress=l == gp.layers.active).name = l.name
        row.prop(l, "hide", text="", emboss=False)
        row.prop(l, "lock", text="", emboss=False)


class _DopePanel:
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Timing"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "mode", None) == 'GPENCIL'


class _ViewPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_GREASE_PENCIL'


class GPT_PT_dope_timing(_DopePanel, bpy.types.Panel):
    bl_label = "GP Timing"

    def draw(self, context):
        draw_timing(self.layout, context)


class GPT_PT_dope_layers(_DopePanel, bpy.types.Panel):
    bl_label = "Layers"

    def draw(self, context):
        draw_layers(self.layout, context)


class GPT_PT_view_timing(_ViewPanel, bpy.types.Panel):
    bl_label = "GP Timing"

    def draw(self, context):
        draw_timing(self.layout, context)


class GPT_PT_view_layers(_ViewPanel, bpy.types.Panel):
    bl_label = "GP Layers"

    def draw(self, context):
        draw_layers(self.layout, context)


class GPT_MT_timing(bpy.types.Menu):
    bl_label = "GP Timing"

    def draw(self, context):
        lay = self.layout
        for n in (1, 2, 3, 4):
            lay.operator(GPT_OT_set_exposure.bl_idname, text=f"Exposure on {n}s").length = n
        lay.operator(GPT_OT_set_exposure.bl_idname, text="Exposure of Selected Keys...").selected = True
        lay.operator(GPT_OT_retime.bl_idname)
        lay.separator()
        lay.operator(GPT_OT_shift_hold.bl_idname, text="Insert Hold").amount = 1
        lay.operator(GPT_OT_shift_hold.bl_idname, text="Remove Hold").amount = -1
        lay.separator()
        lay.operator(GPT_OT_jump_key.bl_idname, text="Previous Key (All Layers)").next = False
        lay.operator(GPT_OT_jump_key.bl_idname, text="Next Key (All Layers)").next = True
        lay.operator(GPT_OT_layer_solo.bl_idname)


def menu(self, context):
    if getattr(context.space_data, "mode", None) == 'GPENCIL':
        self.layout.separator()
        self.layout.menu(GPT_MT_timing.__name__)


_classes = (GPT_OT_set_exposure, GPT_OT_shift_hold, GPT_OT_retime, GPT_OT_jump_key, GPT_OT_layer_solo,
            GPT_OT_lock_others, GPT_OT_layer_step, GPT_OT_layer_activate, GPT_PT_dope_timing,
            GPT_PT_dope_layers, GPT_PT_view_timing, GPT_PT_view_layers, GPT_MT_timing)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.DOPESHEET_MT_key.append(menu)


def unregister():
    bpy.types.DOPESHEET_MT_key.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
