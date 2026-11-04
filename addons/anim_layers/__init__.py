"""Animation Layers: one layer = one NLA track with one strip and one action.

Blender 5.x layered Actions still allow only one layer per Action, so layers are NLA tracks
(bottom track = base layer). The layer being edited is the one in NLA tweak mode, so normal
keying (I, auto-key) goes into it and Blender remaps values through the blend mode.
No metadata is stored: every NLA track of the active object is a layer, so hand edits in the
NLA editor stay in sync.
"""
import ast
import contextlib

import bpy
from bpy_extras import anim_utils

BLEND_NEW = 'COMBINE'


# ---------------------------------------------------------------- helpers

def tracks(obj):
    ad = obj.animation_data
    return ad.nla_tracks if ad else ()


def layer_strip(track, frame=None):
    """The strip that represents a layer: the one under `frame`, else the first."""
    if not track.strips:
        return None
    if frame is not None:
        for s in track.strips:
            if s.frame_start <= frame <= s.frame_end:
                return s
    return track.strips[0]


def active_index(obj):
    ad = obj.animation_data
    if not ad or not ad.nla_tracks.active:
        return -1
    return list(ad.nla_tracks).index(ad.nla_tracks.active)


def exit_tweak(obj):
    ad = obj.animation_data
    if ad and ad.use_tweak_mode:
        ad.use_tweak_mode = False


def key_range(action):
    """(first, last) keyed frame, or None. Action.is_empty is False as soon as a slot exists."""
    fcs = [fc for ly in action.layers for st in ly.strips for cb in st.channelbags for fc in cb.fcurves]
    return action.curve_frame_range[:] if any(fc.keyframe_points for fc in fcs) else None


def fit_strip(strip, scene):
    """Make a 1:1 strip cover scene range + all its keys. Strips the user scaled/offset are left alone."""
    if (strip.action is None or strip.scale != 1 or strip.repeat != 1
            or strip.frame_start != strip.action_frame_start):
        return
    strip.use_sync_length = False  # default True: tweak exit would shrink it to the keyed range
    lo, hi = scene.frame_start, scene.frame_end
    keys = key_range(strip.action)
    if keys:
        lo, hi = min(lo, keys[0]), max(hi, keys[1])
    if (strip.frame_start, strip.frame_end) != (lo, hi):
        set_range(strip, lo, hi)


def set_range(strip, lo, hi):
    """Map action frames lo..hi 1:1 onto scene frames lo..hi (order matters: values get clamped)."""
    strip.action_frame_end = max(hi, strip.action_frame_start + 1)
    strip.action_frame_start = lo
    strip.action_frame_end = hi
    strip.frame_start_ui = lo


def fit_all(obj, scene):
    for t in tracks(obj):
        if len(t.strips) == 1:  # multi-strip tracks are hand-made: don't touch
            fit_strip(t.strips[0], scene)


def init_influence(strip):
    """Keyable influence that also works as a plain slider: animated, but with an empty F-curve."""
    strip.influence = 1.0
    strip.use_animated_influence = True
    fc = strip.fcurves.find("influence")
    if fc:
        fc.keyframe_points.clear()


def new_layer(obj, scene, name, blend, action=None, slot=None, prev=None):
    """New track (above `prev`, or at the bottom) holding one strip with `action`."""
    ad = obj.animation_data_create()
    track = ad.nla_tracks.new(prev=prev)
    track.name = name
    action = action or bpy.data.actions.new(f"{obj.name}_{name}")
    keys = key_range(action)
    start = int(keys[0]) if keys else scene.frame_start
    strip = track.strips.new(name, start, action)
    strip.action_slot = (slot or strip.action_slot
                         or anim_utils.action_get_first_suitable_slot(action, 'OBJECT')
                         or action.slots.new('OBJECT', obj.name))
    strip.blend_type = blend
    strip.use_sync_length = False
    if not keys:  # a fresh strip maps action 0..1 onto start..start+1
        set_range(strip, scene.frame_start, scene.frame_end)
    init_influence(strip)
    fit_strip(strip, scene)
    return track


def push_active_action(obj, scene):
    """Turn a plain active action into a layer (base layer if it's the first one)."""
    ad = obj.animation_data
    if not ad or ad.use_tweak_mode or not ad.action:
        return None
    act, slot, blend, infl = ad.action, ad.action_slot, ad.action_blend_type, ad.action_influence
    prev = ad.nla_tracks[-1] if ad.nla_tracks else None
    track = new_layer(obj, scene, "Base" if prev is None else act.name, blend, act, slot, prev)
    ad.action = None
    if infl < 1:
        layer_strip(track).influence = infl
    return track


@contextlib.contextmanager
def nla_context(context, obj):
    """Temporary NLA editor context with only `obj`'s tracks in play, for nla/anim operators.

    Uses an existing NLA editor if there is one, else briefly switches some other area to it.
    Side effects on other objects (tweak mode, track selection) are undone afterwards.
    """
    win = context.window or context.window_manager.windows[0]
    areas = list(win.screen.areas)
    area = next((a for a in areas if a.type == 'NLA_EDITOR'), None)
    if area is None:
        area = next((a for a in areas if a != context.area), areas[0])
    old_type = area.ui_type
    others = [o for o in context.scene.objects if o != obj and o.animation_data]
    tweak = {o: o.animation_data.use_tweak_mode for o in others}
    sel = {t: t.select for o in others for t in o.animation_data.nla_tracks}
    for t in sel:
        t.select = False
    was_selected = obj.select_get()
    obj.select_set(True)
    area.ui_type = 'NLA_EDITOR'
    try:
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with context.temp_override(window=win, area=area, region=region):
            yield
    finally:
        area.ui_type = old_type
        obj.select_set(was_selected)
        for o, tw in tweak.items():
            if o.animation_data.use_tweak_mode != tw:
                o.animation_data.use_tweak_mode = tw
        for t, s in sel.items():
            t.select = s


def set_active_layer(obj, index, context=None):
    """Make track `index` the edited layer: tweak mode on its strip.

    With `context` (operators only) tweak mode is entered via the NLA operator once so the
    AnimData gets the "evaluate upper stack" flag; it persists, later plain RNA toggles keep it.
    """
    ad = obj.animation_data
    if not ad or not 0 <= index < len(ad.nla_tracks):
        return
    scene = (context or bpy.context).scene
    exit_tweak(obj)
    fit_all(obj, scene)
    track = ad.nla_tracks[index]
    strip = layer_strip(track, scene.frame_current)
    for t in ad.nla_tracks:
        t.select = t == track
        for s in t.strips:
            s.select = s == strip
    ad.nla_tracks.active = track
    if strip is None:
        return
    if context is not None:
        try:
            with nla_context(context, obj):
                if bpy.ops.nla.tweakmode_exit.poll():  # stale scene flag blocks tweakmode_enter
                    bpy.ops.nla.tweakmode_exit()
                bpy.ops.nla.tweakmode_enter(use_upper_stack_evaluation=True)
        except Exception as e:  # no usable window/area: lower-stack tweak is still fine
            print("Animation Layers:", e)
    if not ad.use_tweak_mode:
        ad.use_tweak_mode = True


def _get_index(self):
    return active_index(self)


def _set_index(self, value):
    if value != active_index(self):
        set_active_layer(self, value)


# ------------------------------------------------------------- merge / bake

def keyed_paths(track):
    paths = set()
    for s in track.strips:
        cb = s.action and s.action_slot and anim_utils.action_get_channelbag_for_slot(s.action, s.action_slot)
        if cb:
            paths.update(fc.data_path for fc in cb.fcurves)
    return paths


def read_value(obj, path):
    v = obj.path_resolve(path)
    return list(v) if hasattr(v, "__len__") and not isinstance(v, str) else [v]


def write_value(obj, path, values):
    cur = obj.path_resolve(path)
    if hasattr(cur, "__len__") and not isinstance(cur, str):
        cur[:] = values
        return
    value = type(cur)(values[0])
    if path.endswith("]"):  # custom property: ...["prop"]
        head, _, key = path[:-1].rpartition("[")
        (obj.path_resolve(head) if head else obj)[ast.literal_eval(key)] = value
    else:
        head, _, attr = path.rpartition(".")
        setattr(obj.path_resolve(head) if head else obj, attr, value)


def merge_layers(obj, scene, lo, hi):
    """Bake the evaluated result of layers lo..hi into layer lo (new action) and delete lo+1..hi.

    Values are sampled per frame from the full evaluated stack (no visual keying), then keyed
    into lo while tweaking it, so Blender remaps them through lo's blend mode and influence.
    Only channels keyed in the merged layers are written. Muted layers in the range are kept.
    """
    # ponytail: keys every frame of the range; add a "keys only" mode if dense keys hurt.
    ad = obj.animation_data
    exit_tweak(obj)
    ts = list(ad.nla_tracks)
    if layer_strip(ts[lo]) is None:
        return 0
    merged = [t for t in ts[lo + 1:hi + 1] if not t.mute]
    paths = sorted(set().union(*(keyed_paths(t) for t in [ts[lo]] + merged)))
    mute = {t: t.mute for t in ts}
    solo = {t: t.is_solo for t in ts}
    for t in ts:
        t.is_solo = False
    for t in ts[hi + 1:]:
        t.mute = True
    frame = scene.frame_current
    f0, f1 = scene.frame_start, scene.frame_end
    for t in [ts[lo]] + merged:
        for s in t.strips:
            f0, f1 = min(f0, int(s.frame_start)), max(f1, int(s.frame_end))
    frames = range(f0, f1 + 1)
    removed = []
    try:
        samples = {}
        for f in frames:
            scene.frame_set(f)
            samples[f] = [read_value(obj, p) for p in paths]
        for t in merged:
            t.mute = True
        base = ts[lo]
        strip = layer_strip(base)
        act = bpy.data.actions.new(f"{obj.name}_{base.name}")
        strip.action = act
        strip.action_slot = act.slots.new('OBJECT', obj.name)
        strip.scale, strip.repeat, strip.use_sync_length = 1, 1, False
        set_range(strip, f0, f1)
        set_active_layer(obj, lo)
        for f in frames:
            scene.frame_set(f)
            for p, v in zip(paths, samples[f]):
                write_value(obj, p, v)
                obj.keyframe_insert(p, frame=f)
        exit_tweak(obj)
        for t in merged:
            ad.nla_tracks.remove(t)
        removed = merged
    finally:  # on failure nothing is deleted and every layer gets its mute/solo back
        for t in ts:
            if t not in removed:
                t.mute = mute[t]
                t.is_solo = solo[t]
        scene.frame_set(frame)
    return len(removed)


# ---------------------------------------------------------------- operators

class _LayerOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and len(tracks(context.object)) > 0


class ANIMLAYERS_OT_add(bpy.types.Operator):
    bl_idname = "anim_layers.add"
    bl_label = "Add Layer"
    bl_description = ("Add an animation layer above the active one. The first click turns the "
                      "current action (if any) into the base layer")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj, scene = context.object, context.scene
        exit_tweak(obj)
        pushed = push_active_action(obj, scene)
        ts = tracks(obj)
        if not ts:
            new_layer(obj, scene, "Base", 'REPLACE')
        elif not pushed:
            i = active_index(obj)
            prev = ts[i] if i >= 0 else ts[-1]
            names = {t.name for t in ts}
            n = next(n for n in range(1, len(ts) + 2) if f"Layer {n}" not in names)
            new_layer(obj, scene, f"Layer {n}", BLEND_NEW, prev=prev)
            ts = tracks(obj)
            set_active_layer(obj, list(ts).index(prev) + 1, context)
            return {'FINISHED'}
        set_active_layer(obj, len(tracks(obj)) - 1, context)
        return {'FINISHED'}


class ANIMLAYERS_OT_remove(_LayerOp, bpy.types.Operator):
    bl_idname = "anim_layers.remove"
    bl_label = "Remove Layer"
    bl_description = "Delete the active layer (its NLA track; the action stays until the file is saved)"

    def execute(self, context):
        obj = context.object
        i = max(active_index(obj), 0)
        exit_tweak(obj)
        ad = obj.animation_data
        ad.nla_tracks.remove(ad.nla_tracks[i])
        if ad.nla_tracks:
            set_active_layer(obj, max(i - 1, 0), context)
        return {'FINISHED'}


class ANIMLAYERS_OT_move(_LayerOp, bpy.types.Operator):
    bl_idname = "anim_layers.move"
    bl_label = "Move Layer"
    bl_description = "Move the active layer up or down the stack"
    direction: bpy.props.EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context):
        obj = context.object
        ad = obj.animation_data
        i = active_index(obj)
        j = i + (1 if self.direction == 'UP' else -1)
        if i < 0 or not 0 <= j < len(ad.nla_tracks):
            return {'CANCELLED'}
        exit_tweak(obj)
        track = ad.nla_tracks[i]
        for t in ad.nla_tracks:
            t.select = t == track
        with nla_context(context, obj):
            # entering tweak mode via the operator sets a scene flag that only the operator clears
            if bpy.ops.nla.tweakmode_exit.poll():
                bpy.ops.nla.tweakmode_exit()
            bpy.ops.anim.channels_move(direction=self.direction)
        set_active_layer(obj, list(ad.nla_tracks).index(track), context)
        return {'FINISHED'}


class ANIMLAYERS_OT_merge_down(_LayerOp, bpy.types.Operator):
    bl_idname = "anim_layers.merge_down"
    bl_label = "Merge Down"
    bl_description = "Bake the active layer into the layer below it (keyed channels only, every frame)"

    @classmethod
    def poll(cls, context):
        return super().poll(context) and active_index(context.object) > 0

    def execute(self, context):
        obj = context.object
        i = active_index(obj)
        if obj.animation_data.nla_tracks[i].mute:
            self.report({'WARNING'}, "Unmute the layer to merge it")
            return {'CANCELLED'}
        merge_layers(obj, context.scene, i - 1, i)
        set_active_layer(obj, i - 1, context)
        return {'FINISHED'}


class ANIMLAYERS_OT_bake(_LayerOp, bpy.types.Operator):
    bl_idname = "anim_layers.bake"
    bl_label = "Bake to Base"
    bl_description = ("Bake all unmuted layers into a new action on the base layer and remove them "
                      "(keyed channels only, every frame, no visual keying)")

    def execute(self, context):
        obj = context.object
        n = merge_layers(obj, context.scene, 0, len(obj.animation_data.nla_tracks) - 1)
        set_active_layer(obj, 0, context)
        self.report({'INFO'}, f"Baked {n + 1} layer(s) into the base")
        return {'FINISHED'}


class ANIMLAYERS_OT_extend(_LayerOp, bpy.types.Operator):
    bl_idname = "anim_layers.extend"
    bl_label = "Extend Strips"
    bl_description = "Stretch every layer strip to cover the scene frame range and all its keys"

    def execute(self, context):
        obj = context.object
        i = active_index(obj)
        was_tweaking = obj.animation_data.use_tweak_mode
        exit_tweak(obj)
        fit_all(obj, context.scene)
        if was_tweaking and i >= 0:
            set_active_layer(obj, i, context)
        return {'FINISHED'}


# ---------------------------------------------------------------------- UI

class ANIMLAYERS_UL_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ad = context.object.animation_data
        editing = ad.use_tweak_mode and item == ad.nla_tracks.active
        layout.prop(item, "name", text="", emboss=False,
                    icon='GREASEPENCIL' if editing else ('NLA' if index == 0 else 'ACTION'))
        row = layout.row(align=True)
        row.prop(item, "is_solo", text="", emboss=False, icon='SOLO_ON' if item.is_solo else 'SOLO_OFF')
        row.prop(item, "mute", text="", emboss=False, invert_checkbox=True,
                 icon='HIDE_ON' if item.mute else 'HIDE_OFF')
        row.prop(item, "lock", text="", emboss=False, icon='LOCKED' if item.lock else 'UNLOCKED')

    def filter_items(self, context, data, propname):
        n = len(getattr(data, propname))
        return [], [n - 1 - i for i in range(n)]  # top layer first, like the NLA editor


class _Panel:
    bl_region_type = 'UI'
    bl_category = "Animation"
    bl_label = "Animation Layers"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout, obj = self.layout, context.object
        ad = obj.animation_data
        row = layout.row()
        if ad and ad.nla_tracks:
            row.template_list("ANIMLAYERS_UL_layers", "", ad, "nla_tracks", obj, "anim_layers_index", rows=4)
        else:
            row.label(text="No layers yet", icon='INFO')
        col = row.column(align=True)
        col.operator("anim_layers.add", text="", icon='ADD')
        col.operator("anim_layers.remove", text="", icon='REMOVE')
        col.separator()
        col.operator("anim_layers.move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("anim_layers.move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        track = ad and ad.nla_tracks.active
        strip = track and layer_strip(track, context.scene.frame_current)
        if strip:
            col = layout.column()
            col.prop(strip, "blend_type", text="Blend")
            r = col.row(align=True)
            r.prop(strip, "use_animated_influence", text="")
            r.prop(strip, "influence", slider=True)
            if ad and not ad.use_tweak_mode:
                col.label(text="Click a layer to edit it", icon='ERROR')
        col = layout.column(align=True)
        col.operator("anim_layers.merge_down", icon='TRIA_DOWN_BAR')
        col.operator("anim_layers.bake", icon='NLA_PUSHDOWN')
        col.operator("anim_layers.extend", icon='ARROW_LEFTRIGHT')


class ANIMLAYERS_PT_view3d(_Panel, bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_idname = "ANIMLAYERS_PT_view3d"


class ANIMLAYERS_PT_graph(_Panel, bpy.types.Panel):
    bl_space_type = 'GRAPH_EDITOR'
    bl_idname = "ANIMLAYERS_PT_graph"


class ANIMLAYERS_PT_dopesheet(_Panel, bpy.types.Panel):
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_idname = "ANIMLAYERS_PT_dopesheet"


classes = (
    ANIMLAYERS_OT_add, ANIMLAYERS_OT_remove, ANIMLAYERS_OT_move, ANIMLAYERS_OT_merge_down,
    ANIMLAYERS_OT_bake, ANIMLAYERS_OT_extend, ANIMLAYERS_UL_layers,
    ANIMLAYERS_PT_view3d, ANIMLAYERS_PT_graph, ANIMLAYERS_PT_dopesheet,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.anim_layers_index = bpy.props.IntProperty(
        name="Active Layer", description="Layer being edited (keys go here)",
        get=_get_index, set=_set_index)


def unregister():
    del bpy.types.Object.anim_layers_index
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
