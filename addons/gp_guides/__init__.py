"""GP Guides: drawing guides for Grease Pencil v3 (perspective / parallel / grid / radial overlay),
straighten strokes toward the guides (manually or right after drawing) and line cleanup.

Python can't hook the draw tool, so strokes are corrected after they are drawn.
All geometry works in layer space (stroke positions); guides live on a plane of the layer.
"""
from math import cos, sin, pi

import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

PLANES = {'XZ': ((1, 0, 0), (0, 0, 1)), 'XY': ((1, 0, 0), (0, 1, 0)), 'YZ': ((0, 1, 0), (0, 0, 1))}
VP_NAMES = ("VP1", "VP2", "VP3")
ATTRS = ("position", "radius", "opacity", "vertex_color", "rotation", "delta_time")
MAX_LINES = 400


# ---------- pure geometry (Vectors in layer space) ----------

def plane_axes(plane):
    u, v = PLANES[plane]
    return Vector(u), Vector(v)


def to_plane(p, u, v, origin=Vector()):
    """Project p onto the plane spanned by u, v through origin."""
    n = u.cross(v)
    return p - n * (p - origin).dot(n)


def path_length(pts):
    return sum((pts[i + 1] - pts[i]).length for i in range(len(pts) - 1))


def straightness(pts):
    """Chord / path length: 1 for a perfectly straight stroke."""
    total = path_length(pts)
    return (pts[-1] - pts[0]).length / total if total > 1e-9 else 0.0


def guide_dirs(mode, start, u, v, vps=(), angle=0.0, center=Vector()):
    """Unit directions (in the u/v plane) a line starting at `start` may follow."""
    if mode == 'PERSPECTIVE':
        dirs = [vp - start for vp in vps]
        dirs += [u, v] if len(vps) == 1 else [v] if len(vps) == 2 else []  # 1/2-point keep flat lines
    elif mode == 'PARALLEL':
        dirs = [u * cos(angle) + v * sin(angle)]
    elif mode == 'GRID':
        dirs = [u, v]
    else:  # RADIAL
        dirs = [start - center]
    out = [to_plane(d, u, v) for d in dirs]
    return [d.normalized() for d in out if d.length > 1e-9]


def best_dir(dirs, chord):
    """Guide direction closest to `chord`, signed to point the same way. None if no dirs."""
    if not dirs or chord.length < 1e-9:
        return None
    c = chord.normalized()
    d = max(dirs, key=lambda d: abs(d.dot(c)))
    return d if d.dot(c) >= 0 else -d


def straighten_points(pts, d):
    """Same number of points, evenly spaced on the line from pts[0] along d, length = projected end."""
    s, n = pts[0].copy(), len(pts)
    length = (pts[-1] - s).dot(d)
    return [s + d * (length * i / (n - 1)) for i in range(n)]


def _seg_dist(p, a, b):
    ab = b - a
    t = 0.0 if ab.length_squared < 1e-12 else max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
    return (a + ab * t - p).length


def poly_dist(p, pts):
    if len(pts) == 1:
        return (p - pts[0]).length
    return min(_seg_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def covered_by(a, b):
    """Directed Hausdorff distance: how far `a` strays from polyline `b`."""
    return max(poly_dist(p, b) for p in a)


def find_duplicates(lines, tol):
    """Indices of strokes lying within `tol` of a longer kept stroke (the longer one is kept)."""
    # ponytail: O(n^2 * points^2) brute force, add a bbox/KD-tree prefilter if frames get huge
    keep, drop = [], set()
    for i in sorted(range(len(lines)), key=lambda i: -path_length(lines[i])):
        if lines[i] and any(covered_by(lines[i], lines[k]) < tol for k in keep):
            drop.add(i)
        elif lines[i]:
            keep.append(i)
    return drop


def _chain_pts(lines, chain):
    out = []
    for i, rev in chain:
        out += lines[i][::-1] if rev else lines[i]
    return out


def _rev(chain):
    return [(i, not r) for i, r in reversed(chain)]


def _try_join(lines, a, b, tol, cos_max):
    for ca in (a, _rev(a)):
        for cb in (b, _rev(b)):
            pa, pb = _chain_pts(lines, ca), _chain_pts(lines, cb)
            if (pa[-1] - pb[0]).length > tol:
                continue
            ka, kb = min(3, len(pa) - 1), min(3, len(pb) - 1)
            ta, tb = pa[-1] - pa[-1 - ka], pb[kb] - pb[0]
            if ta.length > 1e-9 and tb.length > 1e-9 and ta.normalized().dot(tb.normalized()) >= cos_max:
                return ca + cb
    return None


def find_joins(lines, tol, max_angle):
    """Chains [(index, reversed), ...] of strokes whose endpoints touch within tol and continue
    each other's direction within max_angle. Lines with < 2 points never join."""
    # ponytail: greedy O(n^3) merge loop, fine for hand-drawn frames (hundreds of strokes)
    chains = [[(i, False)] for i in range(len(lines)) if len(lines[i]) >= 2]
    cos_max = cos(max_angle)
    while True:
        hit = next(((ia, ib, j) for ia in range(len(chains)) for ib in range(ia + 1, len(chains))
                    if (j := _try_join(lines, chains[ia], chains[ib], tol, cos_max))), None)
        if not hit:
            return [c for c in chains if len(c) > 1]
        ia, ib, j = hit
        chains[ia] = j
        del chains[ib]


def resample_params(pts, spacing):
    """[(segment index, t)] for points evenly spaced ~spacing apart along the polyline (ends kept)."""
    segs = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    total = sum(segs)
    if total < 1e-9:
        return [(0, 0.0)]
    count = max(2, round(total / spacing) + 1)
    out, i, acc = [], 0, 0.0
    for k in range(count):
        target = total * k / (count - 1)
        while i < len(segs) - 1 and acc + segs[i] < target:
            acc += segs[i]
            i += 1
        out.append((i, 0.0 if segs[i] < 1e-9 else min(1.0, (target - acc) / segs[i])))
    return out


def guide_lines(mode, u, v, vps=(), angle=0.0, center=Vector(), density=24, spacing=0.5, extent=10.0):
    """[(a, b)] line segments of the overlay, in layer space."""
    lines = []

    def rays(c, n):
        for k in range(n):
            a = 2 * pi * k / n
            lines.append((c, c + (u * cos(a) + v * sin(a)) * extent * 2))

    def family(d, step):
        nrm = to_plane(d.cross(u.cross(v)), u, v).normalized()
        n = min(int(extent / step), MAX_LINES // 2)
        for k in range(-n, n + 1):
            o = center + nrm * k * step
            lines.append((o - d * extent, o + d * extent))

    if mode == 'PERSPECTIVE':
        for vp in vps:
            rays(vp, density)
        if vps:
            h = (vps[1] - vps[0]).normalized() if len(vps) > 1 and (vps[1] - vps[0]).length > 1e-9 else u
            lines.append((vps[0] - h * extent * 3, vps[0] + h * extent * 3))
    elif mode == 'PARALLEL':
        family(u * cos(angle) + v * sin(angle), spacing)
    elif mode == 'GRID':
        family(u, spacing)
        family(v, spacing)
    else:
        rays(center, density)
    return lines


# ---------- Blender data ----------

def layer_matrix(ob, layer):
    return ob.matrix_world @ layer.matrix_local


def guide_setup(context, ob, layer):
    """(mode, u, v, vps, angle, center) in layer space, VPs and center on the guide plane."""
    g = context.scene.gp_guides
    u, v = plane_axes(g.plane)
    inv = layer_matrix(ob, layer).inverted_safe()
    vps = [to_plane(inv @ o.matrix_world.translation, u, v)
           for o in (bpy.data.objects.get(n) for n in VP_NAMES[:int(g.vp_count)]) if o]
    center = to_plane(inv @ context.scene.cursor.location, u, v)
    return g.mode, u, v, vps, g.angle, center


def active_drawing(ob):
    layer = ob.data.layers.active if ob and ob.type == 'GREASEPENCIL' else None
    fr = layer.current_frame() if layer else None
    return layer, (fr.drawing if fr else None)


def read_points(stroke):
    return [{a: (getattr(p, a).copy() if a == "position" else
                 tuple(getattr(p, a)) if a == "vertex_color" else getattr(p, a)) for a in ATTRS}
            for p in stroke.points]


def write_points(drawing, index, pts):
    stroke = drawing.strokes[index]
    n = len(stroke.points)
    if len(pts) > n:
        stroke.add_points(len(pts) - n)
    elif len(pts) < n:
        stroke.remove_points(n - len(pts))
    stroke = drawing.strokes[index]  # re-fetch after resize
    for p, d in zip(stroke.points, pts):
        for a in ATTRS:
            setattr(p, a, d[a])


def _lerp(a, b, t):
    if isinstance(a, (int, float)):
        return a + (b - a) * t
    return type(a)(x + (y - x) * t for x, y in zip(a, b)) if isinstance(a, tuple) else a.lerp(b, t)


def resample_attrs(pts, spacing):
    pos = [d["position"] for d in pts]
    if len(pts) < 2:
        return pts
    return [{a: _lerp(pts[i][a], pts[min(i + 1, len(pts) - 1)][a], t) for a in ATTRS}
            for i, t in resample_params(pos, spacing)]


def straighten_stroke(stroke, dirs_for):
    """Snap stroke to the guide direction nearest its chord. dirs_for(start) -> directions."""
    pts = [p.position.copy() for p in stroke.points]
    if len(pts) < 2:
        return False
    d = best_dir(dirs_for(pts[0]), pts[-1] - pts[0])
    if d is None:
        return False
    for p, q in zip(stroke.points, straighten_points(pts, d)):
        p.position = q
    return True


def straighten(context, ob, selected=False):
    """Straighten the last (or the selected) strokes of the active layer's current drawing."""
    layer, drawing = active_drawing(ob)
    if not drawing or not len(drawing.strokes):
        return 0
    mode, u, v, vps, angle, center = guide_setup(context, ob, layer)
    strokes = [s for s in drawing.strokes if s.select] if selected else [drawing.strokes[-1]]
    n = sum(straighten_stroke(s, lambda st: guide_dirs(mode, st, u, v, vps, angle, center)) for s in strokes)
    if n:
        drawing.tag_positions_changed()
        ob.data.update_tag()
    return n


def cleanup_drawing(drawing, tol, max_angle, spacing=0.0):
    """Merge duplicates, join touching strokes, optionally resample. Returns (dups, joins, resampled)."""
    strokes = drawing.strokes
    lines = [[p.position.copy() for p in s.points] for s in strokes]
    dup = find_duplicates(lines, tol)
    keep = [i for i in range(len(lines)) if i not in dup]
    chains = find_joins([[] if strokes[i].cyclic else lines[i] for i in keep], tol, max_angle)
    removed = set(dup)
    for chain in chains:
        idx = [(keep[i], r) for i, r in chain]
        pts = []
        for i, r in idx:
            data = read_points(drawing.strokes[i])
            pts += data[::-1] if r else data
        write_points(drawing, idx[0][0], pts)
        removed |= {i for i, _ in idx[1:]}
    resampled = 0
    if spacing > 0:
        for i in range(len(drawing.strokes)):
            if i not in removed and len(drawing.strokes[i].points) > 1:
                write_points(drawing, i, resample_attrs(read_points(drawing.strokes[i]), spacing))
                resampled += 1
    if removed:
        drawing.remove_strokes(indices=sorted(removed))
    drawing.tag_positions_changed()
    return len(dup), sum(len(c) - 1 for c in chains), resampled


# ---------- overlay ----------

_handle = [None]


def draw_lines(segments, color, viewport_size, width=1.0):
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": [p for seg in segments for p in seg]})
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("viewportSize", viewport_size)
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def world_guide_lines(context, ob):
    g = context.scene.gp_guides
    layer = ob.data.layers.active
    if not layer:
        return []
    mode, u, v, vps, angle, center = guide_setup(context, ob, layer)
    m = layer_matrix(ob, layer)
    segs = guide_lines(mode, u, v, vps, angle, center, g.density, g.spacing, g.extent)
    return [(m @ a, m @ b) for a, b in segs[:MAX_LINES]]


def _draw():
    ctx = bpy.context
    ob = ctx.object
    if not (ob and ob.type == 'GREASEPENCIL' and ob.mode in {'PAINT_GREASE_PENCIL', 'EDIT_GREASE_PENCIL'}
            and ctx.scene.gp_guides.show and ctx.space_data.overlay.show_overlays):
        return
    segs = world_guide_lines(ctx, ob)
    if segs:
        draw_lines(segs, ctx.scene.gp_guides.color, (ctx.region.width, ctx.region.height))


# ---------- auto straighten ----------

_state = {"op": None, "busy": False, "count": {}}


@persistent
def _on_depsgraph(scene, depsgraph):
    """Straighten a just-finished nearly straight draw stroke, once.

    The draw operator writes into the drawing while the stroke is in progress, so a new stroke is
    only trusted once its brush_stroke operator shows up in wm.operators (it has REGISTER)."""
    if _state["busy"] or not scene.gp_guides.auto_straighten:
        return
    ops = bpy.context.window_manager.operators
    last = ops[-1] if len(ops) else None
    ptr = last.as_pointer() if last else None
    if ptr == _state["op"]:
        return
    _state["op"] = ptr
    ob = depsgraph.view_layer.objects.active
    if last and last.bl_idname == "GREASE_PENCIL_OT_brush_stroke":
        auto_straighten_new(scene, ob)


def auto_straighten_new(scene, ob):
    """Straighten the newest stroke of the active drawing once, if drawn with a Draw brush and nearly
    straight. Returns True when it changed the stroke."""
    if not (ob and ob.type == 'GREASEPENCIL' and ob.mode == 'PAINT_GREASE_PENCIL'):
        return False
    brush = scene.tool_settings.gpencil_paint.brush
    layer, drawing = active_drawing(ob)
    if not (brush and brush.gpencil_brush_type == 'DRAW' and drawing and len(drawing.strokes)):
        return False
    n = len(drawing.strokes)
    key = drawing.as_pointer()
    prev = _state["count"].get(key, -1)
    _state["count"][key] = n
    if prev >= n:  # nothing new on this drawing (e.g. too-short stroke discarded)
        return False
    pts = [p.position for p in drawing.strokes[-1].points]
    if len(pts) < 2 or straightness(pts) < scene.gp_guides.threshold:
        return False
    _state["busy"] = True
    try:
        return straighten(bpy.context, ob) > 0
    finally:
        _state["busy"] = False


# ---------- operators ----------

def _gp_poll(context):
    ob = context.object
    return ob is not None and ob.type == 'GREASEPENCIL' and ob.data.layers.active is not None


class GP_GUIDES_OT_straighten(bpy.types.Operator):
    bl_idname = "gp_guides.straighten"
    bl_label = "Straighten Stroke"
    bl_description = ("Snap the last stroke (or the selected strokes) of the active layer to a straight line "
                      "along the nearest guide, keeping its start point and projected length")
    bl_options = {'REGISTER', 'UNDO'}

    selected: bpy.props.BoolProperty(name="Selected Strokes", options={'SKIP_SAVE'},
                                     description="Straighten selected strokes instead of the last drawn one")

    @classmethod
    def poll(cls, context):
        return _gp_poll(context)

    def execute(self, context):
        n = straighten(context, context.object, self.selected)
        if not n:
            self.report({'WARNING'}, "No stroke to straighten")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Straightened {n} stroke(s)")
        return {'FINISHED'}


class GP_GUIDES_OT_cleanup(bpy.types.Operator):
    bl_idname = "gp_guides.cleanup"
    bl_label = "Clean Up Lines"
    bl_description = ("On the active layer's current drawing: remove near-duplicate strokes (keeps the longer), "
                      "join strokes whose ends touch and continue each other, optionally resample evenly")
    bl_options = {'REGISTER', 'UNDO'}

    tolerance: bpy.props.FloatProperty(name="Tolerance", default=0.02, min=0.0, subtype='DISTANCE',
                                       description="Max distance for duplicates and touching endpoints")
    join_angle: bpy.props.FloatProperty(name="Join Angle", default=0.5236, min=0.0, max=pi, subtype='ANGLE',
                                        description="Max direction change where two strokes are joined")
    spacing: bpy.props.FloatProperty(name="Resample Spacing", default=0.0, min=0.0, subtype='DISTANCE',
                                     description="Resample strokes to even point spacing (0 = off)")

    @classmethod
    def poll(cls, context):
        return _gp_poll(context)

    def execute(self, context):
        layer, drawing = active_drawing(context.object)
        if not drawing:
            self.report({'WARNING'}, "No drawing on the current frame")
            return {'CANCELLED'}
        d, j, r = cleanup_drawing(drawing, self.tolerance, self.join_angle, self.spacing)
        context.object.data.update_tag()
        self.report({'INFO'}, f"Removed {d} duplicate(s), joined {j}, resampled {r}")
        return {'FINISHED'}


class GP_GUIDES_OT_add_vps(bpy.types.Operator):
    bl_idname = "gp_guides.add_vanishing_points"
    bl_label = "Add Vanishing Points"
    bl_description = ("Create missing empties VP1..VP3 on the guide plane of the active Grease Pencil layer; "
                      "move them to set the perspective")
    bl_options = {'REGISTER', 'UNDO'}

    horizon: bpy.props.FloatProperty(name="Horizon Height", default=0.0, subtype='DISTANCE',
                                     description="Height of new vanishing points on the guide plane")

    @classmethod
    def poll(cls, context):
        return _gp_poll(context)

    def execute(self, context):
        g = context.scene.gp_guides
        ob = context.object
        u, v = plane_axes(g.plane)
        m = layer_matrix(ob, ob.data.layers.active)
        w = g.extent * 0.6
        spots = {"1": [(0, self.horizon)], "2": [(-w, self.horizon), (w, self.horizon)],
                 "3": [(-w, self.horizon), (w, self.horizon), (0, self.horizon - g.extent * 1.5)]}[g.vp_count]
        made = 0
        for name, (x, y) in zip(VP_NAMES, spots):
            if name in bpy.data.objects:
                continue
            e = bpy.data.objects.new(name, None)
            e.empty_display_type = 'SPHERE'
            e.empty_display_size = 0.2
            e.location = m @ (u * x + v * y)
            context.scene.collection.objects.link(e)
            made += 1
        self.report({'INFO'}, f"Added {made} vanishing point(s)")
        return {'FINISHED'}


# ---------- UI ----------

class GP_GUIDES_PG_settings(bpy.types.PropertyGroup):
    show: bpy.props.BoolProperty(name="Show Guides", default=False,
                                 description="Draw guide lines in Grease Pencil Draw and Edit mode")
    mode: bpy.props.EnumProperty(name="Guide", items=[
        ('PERSPECTIVE', "Perspective", "Lines radiating from vanishing point empties VP1..VP3"),
        ('PARALLEL', "Parallel", "Parallel lines at an angle"),
        ('GRID', "Grid", "Square grid"),
        ('RADIAL', "Radial", "Lines radiating from the 3D cursor"),
    ], default='PERSPECTIVE')
    vp_count: bpy.props.EnumProperty(name="Points", items=[
        ('1', "1-Point", "One vanishing point (VP1)"),
        ('2', "2-Point", "Two vanishing points (VP1, VP2)"),
        ('3', "3-Point", "Three vanishing points (VP1..VP3)")], default='2')
    plane: bpy.props.EnumProperty(name="Plane", items=[
        ('XZ', "Front (X-Z)", "Guide plane in the layer's X-Z plane (default Grease Pencil drawing plane)"),
        ('XY', "Top (X-Y)", "Guide plane in the layer's X-Y plane"),
        ('YZ', "Side (Y-Z)", "Guide plane in the layer's Y-Z plane")], default='XZ')
    density: bpy.props.IntProperty(name="Density", default=24, min=2, max=180,
                                   description="Lines drawn around each vanishing point / the radial center")
    angle: bpy.props.FloatProperty(name="Angle", default=0.0, subtype='ANGLE', description="Parallel line angle")
    spacing: bpy.props.FloatProperty(name="Spacing", default=0.5, min=0.01, subtype='DISTANCE',
                                     description="Distance between parallel / grid lines")
    extent: bpy.props.FloatProperty(name="Size", default=10.0, min=0.1, subtype='DISTANCE',
                                    description="How far guide lines reach")
    color: bpy.props.FloatVectorProperty(name="Color", subtype='COLOR', size=4, min=0, max=1,
                                         default=(0.3, 0.7, 1.0, 0.25))
    auto_straighten: bpy.props.BoolProperty(
        name="Auto Straighten", default=False,
        description="Straighten each new stroke drawn with a Draw brush when it is already nearly straight")
    threshold: bpy.props.FloatProperty(name="Straightness", default=0.97, min=0.5, max=1.0,
                                       description="Chord / length ratio above which a new stroke is straightened")


class GP_GUIDES_PT_panel(bpy.types.Panel):
    bl_label = "Guides"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Draw"

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'GREASEPENCIL'

    def draw(self, context):
        g = context.scene.gp_guides
        col = self.layout.column()
        col.prop(g, "show")
        col.prop(g, "mode", text="")
        if g.mode == 'PERSPECTIVE':
            col.prop(g, "vp_count", text="")
            col.operator("gp_guides.add_vanishing_points", icon='EMPTY_AXIS')
            col.prop(g, "density")
        elif g.mode == 'PARALLEL':
            col.prop(g, "angle")
            col.prop(g, "spacing")
        elif g.mode == 'GRID':
            col.prop(g, "spacing")
        else:
            col.label(text="Center: 3D cursor")
            col.prop(g, "density")
        col.prop(g, "plane", text="")
        col.prop(g, "extent")
        col.prop(g, "color")
        col.separator()
        row = col.row(align=True)
        row.operator("gp_guides.straighten", text="Straighten Last").selected = False
        row.operator("gp_guides.straighten", text="Selected").selected = True
        col.prop(g, "auto_straighten")
        sub = col.row()
        sub.active = g.auto_straighten
        sub.prop(g, "threshold")
        col.separator()
        col.operator("gp_guides.cleanup", icon='BRUSH_DATA')


_keymaps = []


def _sync_keymap(prefs=None, context=None):
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    kc = bpy.context.window_manager.keyconfigs.addon
    prefs = prefs or bpy.context.preferences.addons[__package__].preferences
    if kc and prefs.use_hotkey:
        km = kc.keymaps.new("Grease Pencil Draw Mode", space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new("gp_guides.straighten", 'L', 'PRESS', alt=True)))


class GP_GUIDES_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    use_hotkey: bpy.props.BoolProperty(name="Alt+L straightens the last stroke (Draw mode)", default=False,
                                       update=_sync_keymap)

    def draw(self, context):
        self.layout.prop(self, "use_hotkey")


classes = (GP_GUIDES_OT_straighten, GP_GUIDES_OT_cleanup, GP_GUIDES_OT_add_vps,
           GP_GUIDES_PG_settings, GP_GUIDES_PT_panel, GP_GUIDES_AP_prefs)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gp_guides = bpy.props.PointerProperty(type=GP_GUIDES_PG_settings)
    _handle[0] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    _sync_keymap()


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _handle[0]:
        bpy.types.SpaceView3D.draw_handler_remove(_handle[0], 'WINDOW')
        _handle[0] = None
    del bpy.types.Scene.gp_guides
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
