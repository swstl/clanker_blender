"""Time Tools: ripple-edit time for the whole animation (keys, NLA strips, markers, scene range)."""
import bpy


def _actions(context, scope):
    if scope == 'ALL':
        return list(bpy.data.actions)
    acts = set()
    for ob in context.selected_objects:
        for idb in (ob, ob.data, getattr(ob.data, "shape_keys", None)):
            ad = getattr(idb, "animation_data", None) if idb else None
            if not ad:
                continue
            if ad.action:
                acts.add(ad.action)
            for tr in ad.nla_tracks:
                acts.update(s.action for s in tr.strips if s.action)
    return list(acts)


def _fcurves(action):
    for layer in action.layers:
        for strip in layer.strips:
            for cb in strip.channelbags:
                yield from cb.fcurves


def _anim_datas(context, scope):
    ids = bpy.data.objects if scope == 'ALL' else context.selected_objects
    for ob in ids:
        for idb in (ob, ob.data, getattr(ob.data, "shape_keys", None)):
            ad = getattr(idb, "animation_data", None) if idb else None
            if ad:
                yield ad


def remap_frame(f, start, amount):
    """Frames after `start` move by `amount`; when removing (amount<0) frames inside the cut
    (start, start-amount] return None (deleted)."""
    if f <= start:
        return f
    if amount < 0 and f <= start - amount:
        return None
    return f + amount


def shift_time(context, start, amount, scope='ALL', markers=True, scene_range=True):
    """Ripple insert (amount>0) or remove (amount<0) time after frame `start`. Returns key count moved."""
    moved = 0
    for act in _actions(context, scope):
        for fc in _fcurves(act):
            kps = fc.keyframe_points
            # Delete cut keys first (iterate backwards so indices stay valid).
            for i in range(len(kps) - 1, -1, -1):
                if remap_frame(kps[i].co.x, start, amount) is None:
                    kps.remove(kps[i])
            for kp in kps:
                if kp.co.x > start:
                    kp.co.x += amount
                    kp.handle_left.x += amount
                    kp.handle_right.x += amount
                    moved += 1
            fc.update()
    # NLA strips: move strips that start after the cut (process in order to avoid overlap errors).
    for ad in _anim_datas(context, scope):
        for tr in ad.nla_tracks:
            strips = sorted((s for s in tr.strips if s.frame_start > start),
                            key=lambda s: s.frame_start, reverse=amount > 0)
            for s in strips:
                if amount > 0:
                    s.frame_end += amount
                    s.frame_start += amount
                else:
                    s.frame_start += amount
                    s.frame_end += amount
    if markers:
        for m in list(context.scene.timeline_markers):
            nf = remap_frame(m.frame, start, amount)
            if nf is None:
                context.scene.timeline_markers.remove(m)
            else:
                m.frame = int(nf)
    if scene_range and context.scene.frame_end > start:
        context.scene.frame_end = max(context.scene.frame_start, context.scene.frame_end + amount)
    return moved


_scope_items = [('ALL', "All Animation", "Every action in the file"),
                ('SELECTED', "Selected Objects", "Only animation of selected objects")]


class TT_OT_shift(bpy.types.Operator):
    bl_idname = "time_tools.shift"
    bl_label = "Insert/Remove Time"
    bl_description = "Ripple keyframes, NLA strips and markers after the playhead"
    bl_options = {'REGISTER', 'UNDO'}

    amount: bpy.props.IntProperty(name="Frames", default=10,
                                  description="Positive inserts frames, negative removes them")
    scope: bpy.props.EnumProperty(name="Scope", items=_scope_items)
    markers: bpy.props.BoolProperty(name="Move Markers", default=True)
    scene_range: bpy.props.BoolProperty(name="Adjust Scene End", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        n = shift_time(context, context.scene.frame_current, self.amount, self.scope,
                       self.markers, self.scene_range)
        self.report({'INFO'}, f"Moved {n} keys by {self.amount} frames")
        return {'FINISHED'}


def rescale_fps(context, old_fps, new_fps, scope='ALL'):
    """Stretch timing so animation plays at the same real-world speed at the new fps."""
    k = new_fps / old_fps
    sc = context.scene
    for act in _actions(context, scope):
        for fc in _fcurves(act):
            for kp in fc.keyframe_points:
                for v in (kp.co, kp.handle_left, kp.handle_right):
                    v.x *= k
            fc.update()
        if act.use_frame_range:
            act.frame_end *= k
            act.frame_start *= k
    for ad in _anim_datas(context, scope):
        for tr in ad.nla_tracks:
            for s in sorted(tr.strips, key=lambda s: s.frame_start, reverse=True):
                s.frame_end = s.frame_end * k
                s.frame_start = s.frame_start * k
    for m in sc.timeline_markers:
        m.frame = round(m.frame * k)
    sc.frame_start = round(sc.frame_start * k)
    sc.frame_end = round(sc.frame_end * k)
    sc.render.fps = int(round(new_fps))
    sc.render.fps_base = 1.0


class TT_OT_rescale(bpy.types.Operator):
    bl_idname = "time_tools.rescale_fps"
    bl_label = "Change FPS (Keep Timing)"
    bl_description = "Change the scene frame rate and stretch keys, strips and markers so motion keeps its speed"
    bl_options = {'REGISTER', 'UNDO'}

    new_fps: bpy.props.IntProperty(name="New FPS", default=30, min=1)
    scope: bpy.props.EnumProperty(name="Scope", items=_scope_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        r = context.scene.render
        old = r.fps / r.fps_base
        rescale_fps(context, old, self.new_fps, self.scope)
        self.report({'INFO'}, f"{old:g} fps -> {self.new_fps} fps")
        return {'FINISHED'}


class TT_OT_snap_subframes(bpy.types.Operator):
    bl_idname = "time_tools.snap_subframes"
    bl_label = "Snap Keys to Whole Frames"
    bl_description = "Round every keyframe to the nearest whole frame (fixes keys left between frames)"
    bl_options = {'REGISTER', 'UNDO'}

    scope: bpy.props.EnumProperty(name="Scope", items=_scope_items, default='SELECTED')

    def execute(self, context):
        n = 0
        for act in _actions(context, self.scope):
            for fc in _fcurves(act):
                for kp in fc.keyframe_points:
                    d = round(kp.co.x) - kp.co.x
                    if d:
                        kp.co.x += d
                        kp.handle_left.x += d
                        kp.handle_right.x += d
                        n += 1
                fc.update()
        self.report({'INFO'}, f"Snapped {n} keys")
        return {'FINISHED'}


def menu(self, context):
    self.layout.separator()
    self.layout.operator(TT_OT_shift.bl_idname, text="Insert/Remove Time at Playhead")
    self.layout.operator(TT_OT_rescale.bl_idname)
    self.layout.operator(TT_OT_snap_subframes.bl_idname)


_classes = (TT_OT_shift, TT_OT_rescale, TT_OT_snap_subframes)
_menus = ("DOPESHEET_MT_key", "GRAPH_MT_key")


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    for m in _menus:
        getattr(bpy.types, m).append(menu)


def unregister():
    for m in _menus:
        getattr(bpy.types, m).remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
