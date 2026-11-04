"""Mesh Onion Skin: draw ghosts of animated meshes at past/future frames (Blender only does this for GP)."""
import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy_extras import anim_utils

# frame -> {"tris": (n,3) float32 world-space tri corners, "edges": (m,3) edge endpoints, "<key>_batch": GPUBatch}
_cache = {}
# objects: names of ghosted meshes; frames: [(frame, 'PAST'|'FUTURE', distance)]; busy: we are frame_set()-ing
_state = {"objects": [], "frames": [], "busy": False, "dirty": False}
_draw_handle = None


def targets(objs):
    """Meshes to ghost: selected meshes plus meshes parented to / deformed by selected armatures."""
    out = []
    for ob in objs:
        if ob.type == 'MESH':
            out.append(ob)
        elif ob.type == 'ARMATURE':
            out += [c for c in bpy.data.objects if c.type == 'MESH' and (
                c.parent == ob or any(m.type == 'ARMATURE' and m.object == ob for m in c.modifiers))]
    return list(dict.fromkeys(out))


def key_frames(objs):
    """Integer frames of keyframes on the meshes, their parents, deforming armatures and shape keys."""
    ids = set()
    for ob in objs:
        ids.add(ob)
        if ob.parent:
            ids.add(ob.parent)
        ids.update(m.object for m in ob.modifiers if m.type == 'ARMATURE' and m.object)
        if ob.data.shape_keys:
            ids.add(ob.data.shape_keys)
    frames = set()
    for idb in ids:
        ad = idb.animation_data
        if not (ad and ad.action and ad.action_slot):
            continue
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        if bag:
            for fc in bag.fcurves:
                frames.update(round(k.co.x) for k in fc.keyframe_points)
    return frames


def frame_list(s, cur, objs):
    if s.mode == 'KEYS':
        keys = sorted(key_frames(objs))
        past = [k for k in reversed(keys) if k < cur][:s.before]
        fut = [k for k in keys if k > cur][:s.after]
    else:
        past = [cur - i * s.step for i in range(1, s.before + 1)]
        fut = [cur + i * s.step for i in range(1, s.after + 1)]
    return [(f, 'PAST', d) for d, f in enumerate(past, 1)] + [(f, 'FUTURE', d) for d, f in enumerate(fut, 1)]


def sample(context, objs, frames):
    """Evaluate the meshes at each frame and cache world-space triangles/edges. Restores the frame."""
    scene = context.scene
    f0, sub = scene.frame_current, scene.frame_subframe
    _state["busy"] = True
    try:
        for f in frames:
            scene.frame_set(f)
            dg = context.evaluated_depsgraph_get()
            tris, edges = [], []
            for ob in objs:
                ev = ob.evaluated_get(dg)
                me = ev.to_mesh()
                try:
                    me.calc_loop_triangles()
                    co = np.empty(len(me.vertices) * 3, np.float32)
                    me.vertices.foreach_get("co", co)
                    m = np.array(ev.matrix_world, np.float32)
                    co = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
                    ti = np.empty(len(me.loop_triangles) * 3, np.int32)
                    me.loop_triangles.foreach_get("vertices", ti)
                    ei = np.empty(len(me.edges) * 2, np.int32)
                    me.edges.foreach_get("vertices", ei)
                    tris.append(co[ti])
                    edges.append(co[ei])
                finally:
                    ev.to_mesh_clear()
            empty = np.empty((0, 3), np.float32)
            _cache[f] = {"tris": np.concatenate(tris) if tris else empty,
                         "edges": np.concatenate(edges) if edges else empty}
    finally:
        scene.frame_set(f0, subframe=sub)
        _state["busy"] = False


def tag_redraw():
    wm = bpy.context.window_manager
    for win in (wm.windows if wm else ()):
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def calculate(context, objs=None):
    """Make the cache match the wanted frames around the current one; only missing frames are sampled."""
    if objs is None:
        objs = [o for n in _state["objects"] if (o := bpy.data.objects.get(n)) and o.type == 'MESH']
    s = context.scene.onion_skin
    frames = frame_list(s, context.scene.frame_current, objs) if objs else []
    need = {f for f, _, _ in frames}
    for f in list(_cache):
        if f not in need:
            del _cache[f]
    sample(context, objs, sorted(need - _cache.keys()))
    _state["objects"] = [o.name for o in objs]
    _state["frames"] = frames
    tag_redraw()


def clear():
    _cache.clear()
    _state.update(objects=[], frames=[], dirty=False)
    tag_redraw()


# ---------------------------------------------------------------- auto update

def _tick():
    if any(sc.is_animation_playing for sc in bpy.data.screens):
        return 0.25  # frame_set during playback would fight it; retry once it stops
    if _state["dirty"]:
        _cache.clear()
        _state["dirty"] = False
    if _state["objects"]:
        calculate(bpy.context)
    return None


def schedule():
    """Throttle: many frame/depsgraph events collapse into one recalculation."""
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1)


def _auto(scene):
    return not _state["busy"] and _state["objects"] and scene.onion_skin.auto_update


@persistent
def on_frame(scene, *_):
    if _auto(scene):
        schedule()


@persistent
def on_depsgraph(scene, depsgraph):
    if _auto(scene) and any(isinstance(u.id, bpy.types.Action) for u in depsgraph.updates):
        _state["dirty"] = True  # keys edited: every cached pose may be wrong
        schedule()


@persistent
def on_load(*_):
    clear()


def _settings_changed(self, context):
    if _state["objects"]:
        schedule()


def _redraw(self, context):
    tag_redraw()


# ---------------------------------------------------------------- drawing

def draw():
    if not _state["frames"]:
        return
    s = bpy.context.scene.onion_skin
    if not s.show:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    key = "edges" if s.display == 'WIRE' else "tris"
    count = {'PAST': s.before, 'FUTURE': s.after}
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE' if s.in_front else 'LESS_EQUAL')
    shader.bind()
    for f, side, d in sorted(_state["frames"], key=lambda t: -t[2]):  # farthest first
        e = _cache.get(f)
        if not e or not len(e[key]):
            continue
        batch = e.get(key + "_batch")
        if batch is None:  # built lazily: gpu is unavailable in background / at sample time
            batch = e[key + "_batch"] = batch_for_shader(
                shader, 'LINES' if key == "edges" else 'TRIS', {"pos": e[key]})
        col = s.past_color if side == 'PAST' else s.future_color
        alpha = s.opacity * (1 - s.falloff * (d - 1) / max(count[side] - 1, 1))
        shader.uniform_float("color", (*col, alpha))
        batch.draw(shader)
    gpu.state.blend_set('NONE')
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set('NONE')


# ---------------------------------------------------------------- UI

class OS_Settings(bpy.types.PropertyGroup):
    show: bpy.props.BoolProperty(name="Show", default=True, update=_redraw)
    mode: bpy.props.EnumProperty(name="Mode", update=_settings_changed, items=[
        ('STEP', "Every N Frames", "Ghost every N frames around the current frame"),
        ('KEYS', "Keyframes", "Ghost the nearest keyframes of the object / armature action"),
    ])
    before: bpy.props.IntProperty(name="Before", default=3, min=0, max=50, update=_settings_changed)
    after: bpy.props.IntProperty(name="After", default=3, min=0, max=50, update=_settings_changed)
    step: bpy.props.IntProperty(name="Step", default=2, min=1, max=100, update=_settings_changed)
    past_color: bpy.props.FloatVectorProperty(name="Past", subtype='COLOR', size=3, min=0, max=1,
                                              default=(1.0, 0.25, 0.2), update=_redraw)
    future_color: bpy.props.FloatVectorProperty(name="Future", subtype='COLOR', size=3, min=0, max=1,
                                                default=(0.2, 0.6, 1.0), update=_redraw)
    opacity: bpy.props.FloatProperty(name="Opacity", default=0.3, min=0, max=1, subtype='FACTOR', update=_redraw)
    falloff: bpy.props.FloatProperty(name="Falloff", default=0.7, min=0, max=1, subtype='FACTOR', update=_redraw,
                                     description="How much fainter the farthest ghost is than the nearest")
    display: bpy.props.EnumProperty(name="Display", update=_redraw, items=[
        ('SOLID', "Solid", "Translucent surfaces"),
        ('WIRE', "Wire", "Edges only"),
    ])
    in_front: bpy.props.BoolProperty(name="Show in Front", update=_redraw,
                                     description="Draw ghosts on top of everything instead of depth tested")
    auto_update: bpy.props.BoolProperty(name="Auto Update", default=True, update=_settings_changed,
                                        description="Follow the current frame and recalculate when keys change")
    max_verts: bpy.props.IntProperty(name="Vertex Warning", default=200_000, min=1000,
                                     description="Warn when the ghosted meshes have more vertices than this")


class OS_OT_calculate(bpy.types.Operator):
    bl_idname = "onion_skin.calculate"
    bl_label = "Calculate"
    bl_description = "Sample the selected meshes (or meshes of selected armatures) at the onion skin frames"

    def execute(self, context):
        objs = targets(context.selected_objects)
        if not objs:
            self.report({'ERROR'}, "Select a mesh or an armature with meshes")
            return {'CANCELLED'}
        dg = context.evaluated_depsgraph_get()
        verts = sum(len(o.evaluated_get(dg).data.vertices) for o in objs)
        if verts > context.scene.onion_skin.max_verts:
            self.report({'WARNING'}, f"{verts:,} vertices per frame: onion skinning may be slow")
        _cache.clear()
        calculate(context, objs)
        return {'FINISHED'}


class OS_OT_clear(bpy.types.Operator):
    bl_idname = "onion_skin.clear"
    bl_label = "Clear"
    bl_description = "Remove all ghosts and free the cache"

    def execute(self, context):
        clear()
        return {'FINISHED'}


class OS_PT_panel(bpy.types.Panel):
    bl_label = "Onion Skin"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Animation"

    def draw(self, context):
        s = context.scene.onion_skin
        col = self.layout.column()
        row = col.row(align=True)
        row.operator(OS_OT_calculate.bl_idname, icon='ONIONSKIN_ON')
        row.operator(OS_OT_clear.bl_idname, text="", icon='X')
        if _state["objects"]:
            tris = sum(len(e["tris"]) // 3 for e in _cache.values())
            col.label(text=f"{len(_state['objects'])} mesh(es), {len(_cache)} frames, {tris:,} tris")
        col.prop(s, "show")
        col.prop(s, "auto_update")
        col.prop(s, "mode", text="")
        row = col.row(align=True)
        row.prop(s, "before")
        row.prop(s, "after")
        if s.mode == 'STEP':
            col.prop(s, "step")
        row = col.row(align=True)
        row.prop(s, "past_color", text="")
        row.prop(s, "future_color", text="")
        col.prop(s, "opacity")
        col.prop(s, "falloff")
        col.row().prop(s, "display", expand=True)
        col.prop(s, "in_front")
        col.prop(s, "max_verts")


classes = (OS_Settings, OS_OT_calculate, OS_OT_clear, OS_PT_panel)
_handlers = (
    (bpy.app.handlers.frame_change_post, on_frame),
    (bpy.app.handlers.depsgraph_update_post, on_depsgraph),
    (bpy.app.handlers.load_post, on_load),
)


def register():
    global _draw_handle
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.onion_skin = bpy.props.PointerProperty(type=OS_Settings)
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
    _cache.clear()
    _state.update(objects=[], frames=[], dirty=False)
    del bpy.types.Scene.onion_skin
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
