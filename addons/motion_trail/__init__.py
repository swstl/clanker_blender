"""Motion Trail: editable motion paths for the active object or selected pose bones.

Blender's own motion paths are display-only; here keyframe points can be dragged in the viewport
and the location keys are rewritten so the object / bone head lands on the dragged point.
"""
import bpy
from bpy.app.handlers import persistent
from bpy_extras import anim_utils, view3d_utils
from mathutils import Vector

# (object name, bone name or None) -> {frame: world-space Vector}
_cache = {}
# targets: what is drawn; frames: wanted frame range; keys: target -> location key frames;
# hover: (target, frame, is_key) under the mouse while editing; busy: we are frame_set()-ing
_state = {"targets": [], "frames": [], "keys": {}, "hover": None, "busy": False, "dirty": False, "editing": False}
_draw_handle = None


# ---------------------------------------------------------------- core math

def world_to_object_location(obj, world_point):
    """`obj.location` that puts the object's origin at `world_point` (parent, parent inverse, deltas)."""
    parent = obj.matrix_world @ obj.matrix_basis.inverted()  # parent world @ matrix_parent_inverse
    return parent.inverted() @ Vector(world_point) - obj.delta_location


def world_to_bone_location(arm_obj, pbone, world_point):
    """`pbone.location` that puts the bone head at `world_point` (armature transform, rest pose, parents)."""
    if pbone.bone.use_connect:
        raise ValueError(f"'{pbone.name}' is connected: its head follows the parent, use IK to move it")
    m = arm_obj.matrix_world @ pbone.matrix
    m.translation = world_point
    return arm_obj.convert_space(pose_bone=pbone, matrix=m, from_space='WORLD', to_space='LOCAL').translation


def world_point(obj, bone=None):
    """Current world position of the object origin or the pose bone head."""
    if bone:
        return obj.matrix_world @ obj.pose.bones[bone].head
    return obj.matrix_world.translation.copy()


def _sample(targets, frames):
    """{target: {frame: world point}}, one frame_set per frame for all targets. Restores the frame."""
    scene = bpy.context.scene
    f0, sub = scene.frame_current, scene.frame_subframe
    out = {t: {} for t in targets}
    _state["busy"] = True
    try:
        for f in frames:
            scene.frame_set(f)
            for t in targets:
                out[t][f] = world_point(bpy.data.objects[t[0]], t[1])
    finally:
        scene.frame_set(f0, subframe=sub)
        _state["busy"] = False
    return out


def sample_path(obj, bone, frames):
    """World positions of the object origin / pose bone head (bone name or None) at each frame."""
    pts = _sample([(obj.name, bone)], frames)[(obj.name, bone)]
    return [pts[f] for f in frames]


def set_key_from_world(obj, bone, frame, world_point):
    """Key `location` (all three axes) at `frame` so the object origin / bone head lands on `world_point`."""
    scene = bpy.context.scene
    f0, sub = scene.frame_current, scene.frame_subframe
    _state["busy"] = True
    try:
        scene.frame_set(frame)  # parents / other channels as they are at that frame
        owner = obj.pose.bones[bone] if bone else obj
        owner.location = (world_to_bone_location(obj, owner, world_point) if bone
                          else world_to_object_location(obj, world_point))
        owner.keyframe_insert("location", frame=frame)  # replacing a key shifts its handles along
    finally:
        scene.frame_set(f0, subframe=sub)  # re-evaluates, so the unkeyed value we set is dropped
        _state["busy"] = False


def delete_key(obj, bone, frame):
    owner = obj.pose.bones[bone] if bone else obj
    return owner.keyframe_delete("location", frame=frame)


def key_frames(obj, bone=None):
    """Frames with a location key on the object / bone in the assigned action slot."""
    ad = obj.animation_data
    if not (ad and ad.action and ad.action_slot):
        return set()
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    path = obj.pose.bones[bone].path_from_id("location") if bone else "location"
    return {round(k.co.x) for fc in (bag.fcurves if bag else ()) if fc.data_path == path
            for k in fc.keyframe_points}


# ---------------------------------------------------------------- cache

def find_targets(view_layer):
    ob = view_layer.objects.active
    if not ob:
        return []
    if ob.type == 'ARMATURE' and ob.mode == 'POSE':
        return [(ob.name, pb.name) for pb in ob.pose.bones if pb.select]
    return [(ob.name, None)]


def frame_range(scene):
    s = scene.motion_trail
    if s.range_mode == 'SCENE':
        return list(range(scene.frame_start, scene.frame_end + 1))
    c = scene.frame_current
    return list(range(c - s.before, c + s.after + 1))


def calculate(context):
    """Make the cache match the targets and frame range; only frames not cached yet are sampled."""
    scene = context.scene
    targets = [t for t in find_targets(context.view_layer)
               if t[0] in bpy.data.objects and (not t[1] or t[1] in bpy.data.objects[t[0]].pose.bones)]
    frames = frame_range(scene)
    want = set(frames)
    for t in list(_cache):
        if t not in targets:
            del _cache[t]
    for t in targets:
        pts = _cache.setdefault(t, {})
        for f in [f for f in pts if f not in want]:
            del pts[f]
    missing = sorted({f for t in targets for f in frames if f not in _cache[t]})
    if missing:
        for t, pts in _sample(targets, missing).items():
            _cache[t].update(pts)
    _state.update(targets=targets, frames=frames,
                  keys={t: key_frames(bpy.data.objects[t[0]], t[1]) for t in targets})
    tag_redraw()


def invalidate(target=None):
    if target:
        _cache.pop(target, None)
    else:
        _cache.clear()


def clear():
    _cache.clear()
    _state.update(targets=[], frames=[], keys={}, hover=None, dirty=False)
    tag_redraw()


def tag_redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ---------------------------------------------------------------- auto update

def _tick():
    if any(sc.is_animation_playing for sc in bpy.data.screens):
        return 0.25  # frame_set during playback would fight it; retry once it stops
    if _state["dirty"]:
        invalidate()
        _state["dirty"] = False
    if bpy.context.scene.motion_trail.show:
        calculate(bpy.context)
    return None


def schedule():
    """Throttle: many frame/depsgraph events collapse into one recalculation."""
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1)


def _auto(scene):
    return scene.motion_trail.show and not _state["busy"] and not _state["editing"]


@persistent
def on_frame(scene, *_):
    if _auto(scene) and scene.motion_trail.range_mode == 'AROUND':
        schedule()


@persistent
def on_depsgraph(scene, depsgraph):
    if not _auto(scene):
        return
    names = {t[0] for t in _state["targets"]}
    if any(isinstance(u.id, bpy.types.Action) or (isinstance(u.id, bpy.types.Object)
                                                  and u.id.name in names and u.is_updated_transform
                                                  and not key_frames(u.id.original))
           for u in depsgraph.updates):
        # keys edited / unkeyed object moved: cached positions may be wrong. A moved object with
        # location keys is skipped: resampling (frame_set) would snap its unkeyed tweak back.
        _state["dirty"] = True
    schedule()  # also follows selection changes (only new targets get sampled)


@persistent
def on_load(*_):
    clear()


def _show_changed(self, context):
    if self.show:
        invalidate()
        calculate(context)
    else:
        clear()


def _settings_changed(self, context):
    if self.show:
        calculate(context)


def _redraw(self, context):
    tag_redraw()


# ---------------------------------------------------------------- drawing

def draw():
    if not _state["targets"]:
        return
    scene = bpy.context.scene
    s = scene.motion_trail
    if not s.show:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    cur = scene.frame_current
    far = max((abs(f - cur) for f in _state["frames"]), default=1) or 1
    line = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    point = gpu.shader.from_builtin('POINT_FLAT_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    for t in _state["targets"]:
        pts = _cache.get(t, {})
        frames = [f for f in _state["frames"] if f in pts]
        if not frames:
            continue
        pos = [pts[f] for f in frames]
        fade = [1 - s.fade * abs(f - cur) / far for f in frames]
        cols = [(*s.color, a) for a in fade]
        if len(pos) > 1:
            line.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
            line.uniform_float("lineWidth", s.line_width)
            batch_for_shader(line, 'LINE_STRIP', {"pos": pos, "color": cols}).draw(line)
        gpu.state.point_size_set(4)
        batch_for_shader(point, 'POINTS', {"pos": pos, "color": cols}).draw(point)  # frame ticks
        keys = _state["keys"].get(t, ())
        kpos = [(p, a) for f, p, a in zip(frames, pos, fade) if f in keys]
        if kpos:
            gpu.state.point_size_set(9)
            batch_for_shader(point, 'POINTS', {"pos": [p for p, _ in kpos],
                                               "color": [(*s.key_color, a) for _, a in kpos]}).draw(point)
    h = _state["hover"]
    if h and h[1] in _cache.get(h[0], {}):
        gpu.state.point_size_set(13 if h[2] else 7)
        batch_for_shader(point, 'POINTS', {"pos": [_cache[h[0]][h[1]]], "color": [(1, 1, 1, 1)]}).draw(point)
    gpu.state.point_size_set(1)
    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------- operators / UI

class MT_Settings(bpy.types.PropertyGroup):
    show: bpy.props.BoolProperty(name="Motion Trail", update=_show_changed,
                                 description="Draw the path of the active object or the selected pose bones")
    range_mode: bpy.props.EnumProperty(name="Range", update=_settings_changed, items=[
        ('AROUND', "Around Frame", "Frames before and after the current frame"),
        ('SCENE', "Scene Range", "The whole scene frame range"),
    ])
    before: bpy.props.IntProperty(name="Before", default=20, min=0, max=1000, update=_settings_changed)
    after: bpy.props.IntProperty(name="After", default=20, min=0, max=1000, update=_settings_changed)
    color: bpy.props.FloatVectorProperty(name="Path", subtype='COLOR', size=3, min=0, max=1,
                                         default=(1.0, 0.6, 0.1), update=_redraw)
    key_color: bpy.props.FloatVectorProperty(name="Keys", subtype='COLOR', size=3, min=0, max=1,
                                             default=(1.0, 0.95, 0.3), update=_redraw)
    fade: bpy.props.FloatProperty(name="Fade", default=0.8, min=0, max=1, subtype='FACTOR', update=_redraw,
                                  description="How much fainter the path gets far from the current frame")
    line_width: bpy.props.FloatProperty(name="Width", default=2.0, min=1, max=10, update=_redraw)


class MT_OT_edit(bpy.types.Operator):
    bl_idname = "motion_trail.edit"
    bl_label = "Edit Trail"
    bl_description = ("Drag keyframe points of the motion trail in the view plane. Ctrl+click the path to "
                      "add a key, X deletes the hovered key, Enter / right-click / Esc finishes")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        r = context.region
        self.region = r if r and r.type == 'WINDOW' else next(r for r in context.area.regions if r.type == 'WINDOW')
        self.rv3d = self.region.data  # matching view in quad view (spaces.active.region_3d is one fixed quad)
        s = context.scene.motion_trail
        if not s.show:
            s.show = True
        calculate(context)
        if not _state["targets"]:
            self.report({'ERROR'}, "Select an object, or pose bones in pose mode")
            return {'CANCELLED'}
        self.drag = None
        self.changed = False
        _state["editing"] = True
        context.workspace.status_text_set(
            "Edit Trail: LMB drag key | Ctrl+LMB add key | X delete key | Esc/RMB cancel drag | Enter/RMB/Esc finish")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _hit(self, mouse):
        """(target, frame, is_key) nearest to the mouse: keys within 12 px win over path samples within 8 px."""
        hits = []
        for t in _state["targets"]:
            keys = _state["keys"].get(t, ())
            for f, p in _cache.get(t, {}).items():
                p2 = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, p)
                if p2 is not None:
                    hits.append(((p2 - mouse).length, t, f, f in keys))
        near = lambda limit, key_only: min((h for h in hits if h[0] < limit and h[3] >= key_only),
                                           key=lambda h: h[0], default=None)
        h = near(12, True) or near(8, False)
        return h[1:] if h else None

    def _set(self, t, f, p):
        try:
            set_key_from_world(bpy.data.objects[t[0]], t[1], f, p)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return False
        self.changed = True
        invalidate(t)
        calculate(bpy.context)
        return True

    def _finish(self, context):
        _state.update(editing=False, hover=None)
        context.workspace.status_text_set(None)
        tag_redraw()
        return {'FINISHED'} if self.changed else {'CANCELLED'}

    def modal(self, context, event):
        r = self.region
        mouse = Vector((event.mouse_x - r.x, event.mouse_y - r.y))
        inside = 0 <= mouse.x < r.width and 0 <= mouse.y < r.height
        press = event.value == 'PRESS'
        if event.type == 'MOUSEMOVE':
            if self.drag:
                t, f, start, offset = self.drag
                p = view3d_utils.region_2d_to_location_3d(r, self.rv3d, mouse, start) + offset
                # ponytail: resamples the whole range per mouse move; sample only the dragged span if slow
                if not self._set(t, f, p):
                    self.drag = None
            else:
                _state["hover"] = self._hit(mouse) if inside else None
            tag_redraw()
            return {'RUNNING_MODAL'} if self.drag else {'PASS_THROUGH'}  # UI hover elsewhere
        if event.type == 'LEFTMOUSE':
            h = _state["hover"]
            if not press:
                if not self.drag:
                    return {'PASS_THROUGH'}  # buttons fire on release
                self.drag = None
                return {'RUNNING_MODAL'}
            if not inside:
                return {'PASS_THROUGH'}
            if h and event.ctrl and not h[2]:
                self._set(h[0], h[1], _cache[h[0]][h[1]])  # key the current position at that frame
                _state["hover"] = self._hit(mouse)
            elif h and h[2]:
                t, f, _ = h
                ob = bpy.data.objects[t[0]]
                if t[1] and ob.pose.bones[t[1]].bone.use_connect:
                    self.report({'WARNING'}, f"'{t[1]}' is connected: its head follows the parent, use IK")
                else:
                    start = _cache[t][f].copy()
                    self.drag = (t, f, start, start - view3d_utils.region_2d_to_location_3d(r, self.rv3d, mouse, start))
            return {'RUNNING_MODAL'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and press:
            if self.drag:  # cancel this drag: put the key back
                t, f, start, _ = self.drag
                self.drag = None
                self._set(t, f, start)
                return {'RUNNING_MODAL'}
            return self._finish(context)
        if event.type in {'RET', 'NUMPAD_ENTER'} and press:
            return self._finish(context)
        if event.type in {'X', 'DEL'} and press and inside:
            h = _state["hover"]
            if h and h[2] and not self.drag:
                delete_key(bpy.data.objects[h[0][0]], h[0][1], h[1])
                self.changed = True
                invalidate(h[0])
                calculate(context)
                _state["hover"] = None
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}  # navigation, timeline scrubbing, ...

    def cancel(self, context):
        _state.update(editing=False, hover=None)


class MT_OT_refresh(bpy.types.Operator):
    bl_idname = "motion_trail.refresh"
    bl_label = "Refresh"
    bl_description = "Resample the motion trail"

    def execute(self, context):
        invalidate()
        calculate(context)
        return {'FINISHED'}


class MT_PT_panel(bpy.types.Panel):
    bl_label = "Motion Trail"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"

    def draw_header(self, context):
        self.layout.prop(context.scene.motion_trail, "show", text="")

    def draw(self, context):
        s = context.scene.motion_trail
        col = self.layout.column()
        row = col.row(align=True)
        row.operator(MT_OT_edit.bl_idname, icon='CURVE_PATH')
        row.operator(MT_OT_refresh.bl_idname, text="", icon='FILE_REFRESH')
        col.prop(s, "range_mode", text="")
        if s.range_mode == 'AROUND':
            row = col.row(align=True)
            row.prop(s, "before")
            row.prop(s, "after")
        row = col.row(align=True)
        row.prop(s, "color", text="")
        row.prop(s, "key_color", text="")
        col.prop(s, "fade")
        col.prop(s, "line_width")


classes = (MT_Settings, MT_OT_edit, MT_OT_refresh, MT_PT_panel)
_handlers = (
    (bpy.app.handlers.frame_change_post, on_frame),
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph),
    (bpy.app.handlers.load_post, on_load),
)


def register():
    global _draw_handle
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.motion_trail = bpy.props.PointerProperty(type=MT_Settings)
    for lst, fn in _handlers:
        lst.append(fn)
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _draw_handle
    if _draw_handle:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
    clear()
    _state["editing"] = False
    del bpy.types.Scene.motion_trail
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
