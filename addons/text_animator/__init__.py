"""Text Animator: motion-graphics helpers for Text objects (typewriter, counter, split, stagger)."""
import math
import re

import bpy
from bpy.app.handlers import persistent
from bpy_extras import anim_utils

UNIT_RE = {'CHARS': r'(?s).', 'WORDS': r'\S+', 'LINES': r'.+'}
# Reuse Blender's own keyframe interpolation/easing lists.
_KF = bpy.types.Keyframe.bl_rna.properties
INTERP = [(e.identifier, e.name, e.description) for e in _KF['interpolation'].enum_items]
EASING = [(e.identifier, e.name, e.description) for e in _KF['easing'].enum_items]


# ---------------------------------------------------------------- core logic

def typewriter_text(src, frame, start, fps, speed, mode='CHARS', cursor="", blink=0.0):
    """What a typewriter shows at `frame`: the first N units of `src` (+ optional blinking cursor)."""
    t = (frame - start) / fps
    n = math.floor(t * speed + 1e-6) if t >= 0 else 0
    if mode == 'CHARS':
        text = src[:max(n, 0)]
    else:
        ends = [m.end() for m in re.finditer(UNIT_RE[mode], src)]
        text = src[:ends[min(n, len(ends)) - 1]] if n > 0 and ends else ""
    if cursor and (blink <= 0 or math.floor(t * blink * 2) % 2 == 0):
        text += cursor
    return text


def format_number(v, decimals=0, separators=True, prefix="", suffix=""):
    return f"{prefix}{v:{',' if separators else ''}.{decimals}f}{suffix}"


def _fcurve(id_data, path):
    adt = id_data.animation_data
    if adt and adt.action and adt.action_slot:
        cb = anim_utils.action_get_channelbag_for_slot(adt.action, adt.action_slot)
        return cb and cb.fcurves.find(path)


def refresh(cu, frame, fps):
    """Rewrite the body of one TextCurve for `frame`. No-op unless a feature is enabled."""
    s = cu.text_animator
    if cu.is_editmode or not cu.is_editable or not (s.typewriter or s.counter):
        return
    if s.counter:
        fc = _fcurve(cu, "text_animator.value")
        v = fc.evaluate(frame) if fc else s.value  # pre-handler runs before animation is evaluated
        text = format_number(v, s.decimals, s.separators, s.prefix, s.suffix)
    else:
        text = typewriter_text(s.source, frame, s.start, fps, s.speed, s.mode, s.cursor, s.blink)
    if cu.body != text:
        cu.body = text


@persistent
def on_frame(scene, depsgraph=None):
    fps = scene.render.fps / scene.render.fps_base
    for cu in bpy.data.curves:
        if isinstance(cu, bpy.types.TextCurve):
            refresh(cu, scene.frame_current_final, fps)


def _upd(self, context):
    sc = context.scene
    if self.typewriter or self.counter:  # frame handlers edit text during renders: crashes without the lock
        sc.render.use_lock_interface = True
    refresh(self.id_data, sc.frame_current_final, sc.render.fps / sc.render.fps_base)


def _upd_typewriter(self, context):
    cu = self.id_data
    if self.typewriter and not self.source:
        self.source = cu.body
    elif not self.typewriter and self.source and not cu.is_editmode:
        cu.body = self.source  # hand the full text back to the user
    _upd(self, context)


class TA_Settings(bpy.types.PropertyGroup):
    typewriter: bpy.props.BoolProperty(name="Typewriter", update=_upd_typewriter,
                                       description="Reveal the source text over time (also in renders)")
    source: bpy.props.StringProperty(name="Text", update=_upd, description="Full text to type out")
    start: bpy.props.IntProperty(name="Start", default=1, update=_upd, description="Frame typing starts")
    speed: bpy.props.FloatProperty(name="Per Second", default=10, min=0, update=_upd,
                                   description="Units (characters, words or lines) revealed per second")
    mode: bpy.props.EnumProperty(name="Unit", update=_upd, items=[
        ('CHARS', "Characters", ""), ('WORDS', "Words", ""), ('LINES', "Lines", "")])
    cursor: bpy.props.StringProperty(name="Cursor", maxlen=8, update=_upd,
                                     description="Cursor appended to the text, e.g. | or _ (empty = none)")
    blink: bpy.props.FloatProperty(name="Blink", default=2, min=0, update=_upd,
                                   description="Cursor blinks per second (0 = always visible)")

    counter: bpy.props.BoolProperty(name="Counter", update=_upd,
                                    description="Show the animatable Value as formatted number (overrides typewriter)")
    value: bpy.props.FloatProperty(name="Value", update=_upd, description="Number shown; keyframe it freely")
    decimals: bpy.props.IntProperty(name="Decimals", min=0, max=10, update=_upd)
    separators: bpy.props.BoolProperty(name="Thousands Separator", default=True, update=_upd)
    prefix: bpy.props.StringProperty(name="Prefix", update=_upd)
    suffix: bpy.props.StringProperty(name="Suffix", update=_upd)
    a: bpy.props.FloatProperty(name="From", default=0)
    b: bpy.props.FloatProperty(name="To", default=1000)
    frame_a: bpy.props.IntProperty(name="Start", default=1)
    frame_b: bpy.props.IntProperty(name="End", default=48)
    interpolation: bpy.props.EnumProperty(name="Interpolation", items=INTERP, default='CUBIC')
    easing: bpy.props.EnumProperty(name="Easing", items=EASING, default='EASE_IN_OUT')


def _text_curve(context):
    ob = context.object
    return ob.data if ob and ob.type == 'FONT' else None


def _set_keys(fcurves, interpolation, easing):
    for fc in fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation, kp.easing = interpolation, easing


class TA_OT_counter_keys(bpy.types.Operator):
    bl_idname = "text_animator.counter_keys"
    bl_label = "Keyframe From → To"
    bl_description = "Enable the counter and keyframe Value from From to To with the chosen easing"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _text_curve(context) is not None

    def execute(self, context):
        cu = _text_curve(context)
        s = cu.text_animator
        fc = _fcurve(cu, "text_animator.value")
        if fc:
            fc.keyframe_points.clear()
        for v, f in ((s.a, s.frame_a), (s.b, s.frame_b)):
            s.value = v
            cu.keyframe_insert("text_animator.value", frame=f)
        _set_keys([_fcurve(cu, "text_animator.value")], s.interpolation, s.easing)
        s.counter = True
        on_frame(context.scene)
        return {'FINISHED'}


class TA_OT_split(bpy.types.Operator):
    bl_idname = "text_animator.split"
    bl_label = "Split Text"
    bl_description = ("Make one text object per letter, word or line at the same place, parented to an "
                      "empty; the original is hidden")
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(name="Into", items=[
        ('LETTERS', "Letters", ""), ('WORDS', "Words", ""), ('LINES', "Lines", "")])

    @classmethod
    def poll(cls, context):
        return _text_curve(context) is not None

    def execute(self, context):
        src = context.object
        objs = split_text(src, self.mode)
        if not objs:
            self.report({'WARNING'}, "Nothing to split")
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        empty = objs[0].parent
        empty.select_set(True)
        context.view_layer.objects.active = empty
        return {'FINISHED'}


def _ink_min(ob, depsgraph):
    """Per-material-index min corner of the evaluated text geometry, in object space."""
    ev = ob.evaluated_get(depsgraph)
    me = ev.to_mesh()
    mins = {}
    for p in me.polygons:
        for vi in p.vertices:
            co = me.vertices[vi].co
            m = mins.setdefault(p.material_index, [math.inf] * 3)
            for k in range(3):
                m[k] = min(m[k], co[k])
    ev.to_mesh_clear()
    return mins


def split_text(src, mode='LETTERS'):
    """Split a text object into per-unit text objects matching the original layout. Returns them."""
    cu = src.data
    body = cu.body
    pattern = {'LETTERS': r'\S', 'WORDS': r'\S+', 'LINES': r'.+'}[mode]
    units = [(m.start(), m.group()) for m in re.finditer(pattern, body)]
    if not units:
        return []
    colls = src.users_collection
    # Measure: tag each unit's characters with its own material index on a temporary copy,
    # so the evaluated geometry tells exactly where each unit's glyphs ended up
    # (handles kerning, alignment, text boxes, word wrap).
    tmp = src.copy()
    tmp.data = cu.copy()
    tmp.data.materials.clear()
    for _ in range(len(units) + 1):
        tmp.data.materials.append(None)
    for f in tmp.data.body_format:
        f.material_index = len(units)
    for u, (i, text) in enumerate(units):
        for j in range(i, i + len(text)):
            tmp.data.body_format[j].material_index = u
    colls[0].objects.link(tmp)

    empty = bpy.data.objects.new(src.name + "_split", None)
    empty.empty_display_size = cu.size
    for c in colls:
        c.objects.link(empty)
    empty.parent, empty.matrix_parent_inverse = src.parent, src.matrix_parent_inverse.copy()
    empty.matrix_basis = src.matrix_basis.copy()
    empty["ta_split"] = src.name

    objs = []
    for u, (i, text) in enumerate(units):
        d = cu.copy()
        d.text_animator.typewriter = d.text_animator.counter = False
        d.body = text
        for j, f in enumerate(d.body_format):
            o = cu.body_format[i + j]
            f.material_index, f.use_bold, f.use_italic = o.material_index, o.use_bold, o.use_italic
            f.use_underline, f.use_small_caps, f.kerning = o.use_underline, o.use_small_caps, o.kerning
        d.align_x = 'LEFT' if d.align_x in {'JUSTIFY', 'FLUSH'} else d.align_x
        ob = bpy.data.objects.new(f"{src.name}_{u:03d}", d)
        for c in colls:
            c.objects.link(ob)
        ob.parent = empty
        ob["ta_index"] = u
        objs.append(ob)

    dg = bpy.context.evaluated_depsgraph_get()
    target = _ink_min(tmp, dg)
    for u, ob in enumerate(objs):
        own = list(_ink_min(ob, dg).values())
        if u in target and own:
            ob.location = [target[u][k] - min(m[k] for m in own) for k in range(3)]
    tmp_data = tmp.data
    bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(tmp_data)
    src.hide_set(True)
    src.hide_render = True
    return objs


def split_children(empty):
    return sorted((c for c in empty.children if "ta_index" in c), key=lambda c: c["ta_index"])


def _split_root(context):
    ob = context.object
    if ob and "ta_split" in ob:
        return ob
    if ob and ob.parent and "ta_split" in ob.parent:
        return ob.parent


EFFECTS = [('SCALE', "Scale", "Grow from zero size"), ('DROP', "Drop", "Fall in from above (local Y)"),
           ('ROTATE', "Rotate", "Spin into place around Z")]


def stagger(objs, start, delay, duration, effects, amount=1.0, angle=math.radians(90),
            interpolation='BACK', easing='EASE_OUT'):
    """Key each object from an 'in' state to its rest pose, item k starting at start + k*delay."""
    for k, ob in enumerate(objs):
        rest = ob.get("ta_rest")  # rest pose survives re-running at any frame
        if rest is None:
            rest = ob["ta_rest"] = [*ob.location, *ob.rotation_euler, *ob.scale]
        rest = list(rest)
        ob.animation_data_clear()
        f0 = start + k * delay
        paths = []
        loc, rot, scl = rest[0:3], rest[3:6], rest[6:9]
        if 'DROP' in effects:
            ob.location = (loc[0], loc[1] + amount, loc[2])
            paths.append("location")
        if 'ROTATE' in effects:
            ob.rotation_euler = (rot[0], rot[1], rot[2] + angle)
            paths.append("rotation_euler")
        if 'SCALE' in effects:
            ob.scale = (0, 0, 0)
            paths.append("scale")
        for p in paths:
            ob.keyframe_insert(p, frame=f0)
        ob.location, ob.rotation_euler, ob.scale = loc, rot, scl
        for p in paths:
            ob.keyframe_insert(p, frame=f0 + duration)
        adt = ob.animation_data
        if adt and adt.action:
            _set_keys(anim_utils.action_get_channelbag_for_slot(adt.action, adt.action_slot).fcurves,
                      interpolation, easing)


class TA_OT_stagger(bpy.types.Operator):
    bl_idname = "text_animator.stagger"
    bl_label = "Stagger Animate"
    bl_description = "Keyframe the split text pieces in one after another (replaces their animation)"
    bl_options = {'REGISTER', 'UNDO'}

    effects: bpy.props.EnumProperty(name="Effect", items=EFFECTS, options={'ENUM_FLAG'}, default={'SCALE'})
    start: bpy.props.IntProperty(name="Start Frame", default=1)
    delay: bpy.props.FloatProperty(name="Delay", default=2, min=0, description="Frames between items")
    duration: bpy.props.FloatProperty(name="Duration", default=10, min=1, description="Frames per item")
    amount: bpy.props.FloatProperty(name="Drop Height", default=1, subtype='DISTANCE')
    angle: bpy.props.FloatProperty(name="Angle", default=math.radians(90), subtype='ANGLE')
    interpolation: bpy.props.EnumProperty(name="Interpolation", items=INTERP, default='BACK')
    easing: bpy.props.EnumProperty(name="Easing", items=EASING, default='EASE_OUT')

    @classmethod
    def poll(cls, context):
        return _split_root(context) is not None

    def invoke(self, context, event):
        self.start = context.scene.frame_current
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        objs = split_children(_split_root(context))
        if not objs or not self.effects:
            return {'CANCELLED'}
        stagger(objs, self.start, self.delay, self.duration, self.effects, self.amount, self.angle,
                self.interpolation, self.easing)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

def draw_panel(layout, context):
    cu = _text_curve(context)
    if cu:
        s = cu.text_animator
        box = layout.box()
        box.prop(s, "typewriter", icon='SMALL_CAPS')
        col = box.column()
        col.active = s.typewriter
        col.prop(s, "source")
        col.prop(s, "mode")
        row = col.row(align=True)
        row.prop(s, "start")
        row.prop(s, "speed")
        row = col.row(align=True)
        row.prop(s, "cursor")
        row.prop(s, "blink")
        col.label(text="Body can't be keyframed: this runs on frame change", icon='INFO')

        box = layout.box()
        box.prop(s, "counter", icon='LINENUMBERS_ON')
        col = box.column()
        col.active = s.counter
        col.prop(s, "value")
        row = col.row(align=True)
        row.prop(s, "prefix")
        row.prop(s, "suffix")
        row = col.row(align=True)
        row.prop(s, "decimals")
        row.prop(s, "separators", text="1,000")
        sub = col.column(align=True)
        row = sub.row(align=True)
        row.prop(s, "a")
        row.prop(s, "b")
        row = sub.row(align=True)
        row.prop(s, "frame_a")
        row.prop(s, "frame_b")
        row = sub.row(align=True)
        row.prop(s, "interpolation", text="")
        row.prop(s, "easing", text="")
        sub.operator("text_animator.counter_keys", icon='KEY_HLT')

        box = layout.box()
        box.label(text="Split Into Objects", icon='MOD_EXPLODE')
        row = box.row(align=True)
        for m, label in (('LETTERS', "Letters"), ('WORDS', "Words"), ('LINES', "Lines")):
            row.operator("text_animator.split", text=label).mode = m
    if _split_root(context):
        layout.operator("text_animator.stagger", icon='ANIM')


class TA_PT_props(bpy.types.Panel):
    bl_label = "Text Animator"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return _text_curve(context) is not None or _split_root(context) is not None

    def draw(self, context):
        draw_panel(self.layout, context)


class TA_PT_sidebar(bpy.types.Panel):
    bl_label = "Text Animator"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Text"
    poll = TA_PT_props.poll
    draw = TA_PT_props.draw


_classes = (TA_Settings, TA_OT_counter_keys, TA_OT_split, TA_OT_stagger, TA_PT_props, TA_PT_sidebar)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TextCurve.text_animator = bpy.props.PointerProperty(type=TA_Settings)
    bpy.app.handlers.frame_change_pre.append(on_frame)


def unregister():
    if on_frame in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(on_frame)
    del bpy.types.TextCurve.text_animator
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
