"""VSE Tools: ripple delete/trim, gaps at playhead and batch strip edits (5.2; ripple is native in 5.3)."""
import bpy


# ---------- interval math (pure) ----------

def merge(ivs):
    """Union of half-open intervals [a, b) -> sorted, non-overlapping list."""
    out = []
    for a, b in sorted(iv for iv in ivs if iv[0] < iv[1]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract(ivs, occupied):
    """Parts of `ivs` not covered by any interval in `occupied`."""
    out = []
    for a, b in merge(ivs):
        for oa, ob in merge(occupied):
            if ob <= a or oa >= b:
                continue
            if oa > a:
                out.append((a, oa))
            a = max(a, ob)
            if a >= b:
                break
        if a < b:
            out.append((a, b))
    return out


def removed_before(x, gaps):
    """Total gap length lying before frame x."""
    return sum(min(b, x) - a for a, b in gaps if a < x)


# ---------- strip helpers ----------

def _scene(context):
    return getattr(context, "sequencer_scene", None) or context.scene


def _container(context):
    """Strip list + channels of the meta being edited, or the top level."""
    se = _scene(context).sequence_editor
    if se is None:
        return None
    return se.meta_stack[-1] if len(se.meta_stack) else se


def _movable(box, s):
    # Effect strips with inputs follow their inputs (content_start is read-only on them).
    return not s.lock and not box.channels[s.channel].lock and not getattr(s, "input_count", 0)


def ripple_close(box, removed, all_channels=True):
    """`removed` = {channel: [(a, b)]} frames that just became empty. Shift later strips left so
    no gap remains. Frames still occupied by another strip in scope are kept (non-destructive,
    like 5.3 ripple delete with Clear Ranges off), which also guarantees no new overlaps."""
    groups = [None] if all_channels else sorted(removed)
    for g in groups:
        ivs = [iv for ch, lst in removed.items() if g is None or ch == g for iv in lst]
        strips = [s for s in box.strips if g is None or s.channel == g]
        gaps = subtract(ivs, [(s.left_handle, s.right_handle) for s in strips])
        # Ascending order: the space each strip moves into is already free.
        # ponytail: locked strips stay put and later strips may hit them (Blender then shuffles
        # them to a free channel); add per-channel walls if that bites.
        for s in sorted(strips, key=lambda s: s.left_handle):
            d = removed_before(s.left_handle, gaps)
            if d and _movable(box, s):
                s.content_start -= d


def ripple_delete(box, strips, all_channels=True):
    removed = {}
    for s in strips:
        removed.setdefault(s.channel, []).append((s.left_handle, s.right_handle))
    for s in list(strips):
        box.strips.remove(s)
    ripple_close(box, removed, all_channels)


def ripple_trim(box, strips, frame, side, all_channels=True):
    """Trim strips' START or END to `frame` and close the cut. Returns trimmed count."""
    removed = {}
    for s in strips:
        lo, hi = s.left_handle, s.right_handle
        if not lo < frame < hi:
            continue
        if side == 'START':
            s.left_handle = frame
            removed.setdefault(s.channel, []).append((lo, frame))
        else:
            s.right_handle = frame
            removed.setdefault(s.channel, []).append((frame, hi))
    ripple_close(box, removed, all_channels)
    return sum(map(len, removed.values()))


def insert_gap(box, frame, amount, channels=None):
    """Shift strips starting at/after `frame` right by `amount` (channels=None: all)."""
    strips = [s for s in box.strips if s.left_handle >= frame and (channels is None or s.channel in channels)]
    for s in sorted(strips, key=lambda s: s.left_handle, reverse=True):
        if _movable(box, s):
            s.content_start += amount


def remove_gap(box, frame, amount, channels=None):
    """Remove up to `amount` empty frames after `frame` (only frames free in scope are removed)."""
    if channels is None:
        ripple_close(box, {0: [(frame, frame + amount)]}, True)
    else:
        ripple_close(box, {c: [(frame, frame + amount)] for c in channels}, False)


# ---------- operators ----------

class _VSEOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _container(context) is not None

    def selected(self, context):
        box = _container(context)
        return box, [s for s in box.strips if s.select]


_all_ch = dict(name="All Channels", default=True,
               description="Shift strips on every channel; off: only channels of the edited strips")


class VSE_OT_ripple_delete(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.ripple_delete"
    bl_label = "Ripple Delete"
    bl_description = "Delete selected strips and move later strips left to close the gap"

    all_channels: bpy.props.BoolProperty(**_all_ch)

    def execute(self, context):
        box, sel = self.selected(context)
        sel = [s for s in sel if not s.lock and not box.channels[s.channel].lock]
        if not sel:
            self.report({'WARNING'}, "No unlocked strips selected")
            return {'CANCELLED'}
        n = len(sel)
        ripple_delete(box, sel, self.all_channels)
        self.report({'INFO'}, f"Ripple deleted {n} strip(s)")
        return {'FINISHED'}


class VSE_OT_ripple_trim(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.ripple_trim"
    bl_label = "Ripple Trim"
    bl_description = "Trim the start or end of selected strips to the playhead and close the gap"

    side: bpy.props.EnumProperty(name="Side", items=[('START', "Start", "Trim the part before the playhead"),
                                                     ('END', "End", "Trim the part after the playhead")])
    all_channels: bpy.props.BoolProperty(**_all_ch)

    def execute(self, context):
        box, sel = self.selected(context)
        sel = [s for s in sel if _movable(box, s)]
        n = ripple_trim(box, sel, _scene(context).frame_current, self.side, self.all_channels)
        if not n:
            self.report({'WARNING'}, "Playhead is not inside a selected unlocked strip")
            return {'CANCELLED'}
        return {'FINISHED'}


class VSE_OT_gap(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.gap"
    bl_label = "Insert/Remove Gap"
    bl_description = "Insert (positive) or remove (negative) frames at the playhead, shifting later strips"

    frames: bpy.props.IntProperty(name="Frames", default=10,
                                  description="Positive inserts empty frames, negative removes empty frames")
    all_channels: bpy.props.BoolProperty(name="All Channels", default=True,
                                         description="Off: only channels of selected strips")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        box, sel = self.selected(context)
        chans = None if self.all_channels else {s.channel for s in sel}
        f = _scene(context).frame_current
        if self.frames > 0:
            insert_gap(box, f, self.frames, chans)
        elif self.frames < 0:
            remove_gap(box, f, -self.frames, chans)
        return {'FINISHED'}


class VSE_OT_batch_set(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.batch_set"
    bl_label = "Batch Set Strips"
    bl_description = "Set blend mode, opacity and/or volume on all selected strips"

    use_blend: bpy.props.BoolProperty(name="Set Blend", default=False)
    blend_type: bpy.props.EnumProperty(
        name="Blend", items=[(i.identifier, i.name, i.description) for i in
                             bpy.types.Strip.bl_rna.properties["blend_type"].enum_items])
    use_opacity: bpy.props.BoolProperty(name="Set Opacity", default=False)
    opacity: bpy.props.FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    use_volume: bpy.props.BoolProperty(name="Set Volume", default=False)
    volume: bpy.props.FloatProperty(name="Volume", default=1.0, min=0.0, max=100.0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        for use, val in (("use_blend", "blend_type"), ("use_opacity", "opacity"), ("use_volume", "volume")):
            row = col.row()
            row.prop(self, use, text="")
            sub = row.row()
            sub.active = getattr(self, use)
            sub.prop(self, val)

    def execute(self, context):
        _box, sel = self.selected(context)
        for s in sel:
            if self.use_blend and hasattr(s, "blend_type"):
                s.blend_type = self.blend_type
            if self.use_opacity and hasattr(s, "blend_alpha"):
                s.blend_alpha = self.opacity
            if self.use_volume and hasattr(s, "volume"):
                s.volume = self.volume
        self.report({'INFO'}, f"Updated {len(sel)} strip(s)")
        return {'FINISHED'}


def fps_pair(fps):
    """Scene (fps, fps_base) for a real frame rate, e.g. 29.97 -> (30, 1.001)."""
    n = max(1, round(fps))
    return n, n / fps


class VSE_OT_fps_check(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.fps_check"
    bl_label = "Check Frame Rates"
    bl_description = ("Select movie strips whose frame rate differs from the scene "
                      "(they play at the wrong speed); optionally match the scene to the active strip")

    match_scene: bpy.props.BoolProperty(name="Match Scene to Active Strip", default=False)

    def execute(self, context):
        sc = _scene(context)
        box = _container(context)
        rate = sc.render.fps / sc.render.fps_base
        movies = [s for s in box.strips if s.type == 'MOVIE']
        act = sc.sequence_editor.active_strip
        if self.match_scene and act and act.type == 'MOVIE' and act.fps > 0:
            sc.render.fps, sc.render.fps_base = fps_pair(act.fps)
            rate = act.fps
        bad = [s for s in movies if s.fps > 0 and abs(s.fps - rate) > 1e-3]
        for s in box.strips:
            s.select = s in bad
        self.report({'WARNING'} if bad else {'INFO'},
                    f"{len(bad)} of {len(movies)} movie strip(s) differ from {rate:.3g} fps")
        return {'FINISHED'}


class VSE_OT_channel_mute(_VSEOp, bpy.types.Operator):
    bl_idname = "vse_tools.channel_mute"
    bl_label = "Mute Channels"
    bl_description = "Mute or unmute the channels that hold selected strips"

    mute: bpy.props.BoolProperty(name="Mute", default=True)

    def execute(self, context):
        box, sel = self.selected(context)
        for ch in {s.channel for s in sel}:
            box.channels[ch].mute = self.mute
        return {'FINISHED'}


# ---------- UI ----------

class VSE_MT_ripple(bpy.types.Menu):
    bl_label = "Ripple"
    bl_idname = "VSE_MT_ripple"

    def draw(self, context):
        l = self.layout
        l.operator(VSE_OT_ripple_delete.bl_idname)
        l.operator(VSE_OT_ripple_trim.bl_idname, text="Ripple Trim Start").side = 'START'
        l.operator(VSE_OT_ripple_trim.bl_idname, text="Ripple Trim End").side = 'END'
        l.operator(VSE_OT_gap.bl_idname, text="Insert/Remove Gap at Playhead")


class VSE_PT_tools(bpy.types.Panel):
    bl_label = "VSE Tools"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Tools"

    def draw(self, context):
        l = self.layout
        col = l.column(align=True)
        col.label(text="Ripple")
        col.operator(VSE_OT_ripple_delete.bl_idname)
        row = col.row(align=True)
        row.operator(VSE_OT_ripple_trim.bl_idname, text="Trim Start").side = 'START'
        row.operator(VSE_OT_ripple_trim.bl_idname, text="Trim End").side = 'END'
        col.operator(VSE_OT_gap.bl_idname)
        col.operator("sequencer.gap_remove", text="Remove All Gaps").all = True

        col = l.column(align=True)
        col.label(text="Range & Select")
        col.operator("sequencer.set_range_to_strips", text="Scene Range to Selected").preview = False
        col.operator("sequencer.set_range_to_strips", text="Preview Range to Selected").preview = True
        col.operator("sequencer.select_grouped", text="Select Same Type").type = 'TYPE'
        row = col.row(align=True)
        row.operator("sequencer.select_side_of_frame", text="Left of Playhead").side = 'LEFT'
        row.operator("sequencer.select_side_of_frame", text="Right of Playhead").side = 'RIGHT'

        col = l.column(align=True)
        col.label(text="Batch")
        col.operator(VSE_OT_batch_set.bl_idname)
        col.operator(VSE_OT_fps_check.bl_idname)
        row = col.row(align=True)
        row.operator(VSE_OT_channel_mute.bl_idname, text="Mute Channels").mute = True
        row.operator(VSE_OT_channel_mute.bl_idname, text="Unmute").mute = False


def menu(self, context):
    self.layout.separator()
    self.layout.menu(VSE_MT_ripple.bl_idname)


_classes = (VSE_OT_ripple_delete, VSE_OT_ripple_trim, VSE_OT_gap, VSE_OT_batch_set,
            VSE_OT_fps_check, VSE_OT_channel_mute, VSE_MT_ripple, VSE_PT_tools)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.SEQUENCER_MT_strip.append(menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:  # None in background
        km = kc.keymaps.new(name="Sequencer", space_type='SEQUENCE_EDITOR')
        # Free in the 5.2 default keymap (plain Delete/X = delete, [ / ] = select side of frame).
        for key, op, props in (('DEL', VSE_OT_ripple_delete, dict(shift=True)),
                               ('LEFT_BRACKET', VSE_OT_ripple_trim, dict(alt=True)),
                               ('RIGHT_BRACKET', VSE_OT_ripple_trim, dict(alt=True))):
            kmi = km.keymap_items.new(op.bl_idname, key, 'PRESS', **props)
            if key == 'LEFT_BRACKET':
                kmi.properties.side = 'START'
            elif key == 'RIGHT_BRACKET':
                kmi.properties.side = 'END'
            _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.SEQUENCER_MT_strip.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
