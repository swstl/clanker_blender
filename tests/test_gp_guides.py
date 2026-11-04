import importlib
from math import radians

import bpy
from mathutils import Vector

m = importlib.import_module("bl_ext.clanker.gp_guides")


def _gp(strokes):
    """GP object, active layer + drawing on frame 1 with the given point lists (layer space)."""
    gp = bpy.data.grease_pencils.new("GP")
    layer = gp.layers.new("A")
    gp.layers.active = layer
    d = layer.frames.new(1).drawing
    d.add_strokes([len(s) for s in strokes])
    for st, pts in zip(d.strokes, strokes):
        for p, co in zip(st.points, pts):
            p.position = co
            p.radius = 0.05
    ob = bpy.data.objects.new("GP", gp)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.context.scene.frame_set(1)
    return ob, d


def _pts(stroke):
    return [p.position.copy() for p in stroke.points]


def _wobbly(start, end, n=12, amp=0.05):
    s, e = Vector(start), Vector(end)
    return [s.lerp(e, i / (n - 1)) + Vector((0, 0, amp if i % 2 else -amp)) * (0 < i < n - 1) for i in range(n)]


def test_pure_geometry():
    u, v = m.plane_axes('XZ')
    assert m.straightness([Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((2, 0, 0))]) == 1.0
    d = m.best_dir(m.guide_dirs('GRID', Vector(), u, v), Vector((0.1, 0, 1)))
    assert (d - v).length < 1e-6
    d = m.best_dir(m.guide_dirs('PARALLEL', Vector(), u, v, angle=radians(45)), Vector((-1, 0, -1)))
    assert d.dot(Vector((-1, 0, -1)).normalized()) > 0.999
    assert m.find_duplicates([[Vector((0, 0, 0)), Vector((2, 0, 0))], [Vector((0.5, 0, 0.01)), Vector((1, 0, 0))]], 0.05) == {1}
    lines = [[Vector((0, 0, 0)), Vector((1, 0, 0))], [Vector((2, 0, 0)), Vector((1.01, 0, 0))],
             [Vector((1, 0, 0)), Vector((1, 0, 1))]]  # 3rd touches but turns 90 degrees
    assert m.find_joins(lines, 0.05, radians(20)) == [[(0, False), (1, True)]]
    params = m.resample_params([Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((3, 0, 0))], 0.5)
    assert len(params) == 7 and params[-1] == (1, 1.0)


def test_straighten_toward_vp():
    ob, d = _gp([_wobbly((0, 0, 0), (3, 0, 0.4)), _wobbly((0, 0, 0), (0, 0, 3))])
    vp = bpy.data.objects.new("VP1", None)
    vp.location = (10, 0, 1)
    bpy.context.scene.collection.objects.link(vp)
    g = bpy.context.scene.gp_guides
    g.mode, g.vp_count, g.plane = 'PERSPECTIVE', '1', 'XZ'
    # Last stroke is vertical: 1-point keeps verticals, so it snaps to straight up.
    assert bpy.ops.gp_guides.straighten() == {'FINISHED'}
    up = _pts(d.strokes[1])
    assert all(abs(p.x) < 1e-5 and abs(p.y) < 1e-5 for p in up)
    for i, st in enumerate(d.strokes):
        st.select = i == 0
    before = _pts(d.strokes[0])
    assert bpy.ops.gp_guides.straighten(selected=True) == {'FINISHED'}
    pts = _pts(d.strokes[0])
    assert (pts[0] - before[0]).length < 1e-6  # start kept
    dvp = (Vector((10, 0, 1)) - pts[0]).normalized()
    for p in pts[1:]:
        assert (p - pts[0]).cross(dvp).length < 1e-4  # collinear with the VP direction
    gaps = [(pts[i + 1] - pts[i]).length for i in range(len(pts) - 1)]
    assert max(gaps) - min(gaps) < 1e-4  # evenly resampled
    assert abs((pts[-1] - pts[0]).length - (before[-1] - before[0]).dot(dvp)) < 1e-4  # projected length


def test_cleanup_merges_and_joins():
    a = [Vector((x / 10, 0, 0)) for x in range(11)]            # 0..1
    dup = [Vector((x / 10, 0, 0.005)) for x in range(2, 8)]    # lies on a
    b = [Vector((2 - x / 10, 0, 0)) for x in range(10)]       # 2..1.1, reversed, continues a
    far = [Vector((0, 0, 5)), Vector((1, 0, 5))]
    ob, d = _gp([a, dup, b, far])
    assert bpy.ops.gp_guides.cleanup(tolerance=0.15, join_angle=radians(20)) == {'FINISHED'}
    assert len(d.strokes) == 2
    joined = _pts(d.strokes[0])
    assert len(joined) == 21 and (joined[-1] - Vector((2, 0, 0))).length < 1e-6
    assert all(abs(p.radius - 0.05) < 1e-6 for p in d.strokes[0].points)
    bpy.ops.gp_guides.cleanup(tolerance=0.01, spacing=0.5)
    pts = _pts(d.strokes[0])
    assert len(pts) == 5 and (pts[2] - Vector((1, 0, 0))).length < 1e-4


def test_auto_straighten_once():
    ob, d = _gp([_wobbly((0, 0, 0), (3, 0, 0.02), amp=0.01), _wobbly((0, 0, 0), (1, 0, 1), amp=0.3)])
    g = bpy.context.scene.gp_guides
    g.mode, g.auto_straighten, g.threshold = 'GRID', True, 0.97
    bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
    sc = bpy.context.scene
    assert sc.tool_settings.gpencil_paint.brush.gpencil_brush_type == 'DRAW'
    assert not m.auto_straighten_new(sc, ob)  # newest (last) stroke is too wobbly
    d.remove_strokes(indices=[1])
    assert not m.auto_straighten_new(sc, ob)  # stroke count went down: nothing new
    d.add_strokes([3])
    for p, co in zip(d.strokes[1].points, ((0, 0, 1), (1, 0, 1.02), (2, 0, 1))):
        p.position = co
    assert m.auto_straighten_new(sc, ob)
    assert all(abs(p.position.z - 1) < 1e-6 for p in d.strokes[1].points)
    assert not m.auto_straighten_new(sc, ob)  # acts on a new stroke only once
    assert not m._state["busy"]
    # Handler without a finished brush_stroke operator does nothing.
    m._on_depsgraph(sc, bpy.context.evaluated_depsgraph_get())
    bpy.ops.object.mode_set(mode='OBJECT')
    g.auto_straighten = False


def test_overlay_draws():
    import gpu
    u, v = m.plane_axes('XZ')
    for mode in ('PERSPECTIVE', 'PARALLEL', 'GRID', 'RADIAL'):
        segs = m.guide_lines(mode, u, v, [Vector((-3, 0, 1)), Vector((3, 0, 1))], radians(30))
        assert segs and all(abs(a.y) < 1e-6 and abs(b.y) < 1e-6 for a, b in segs)
    ob, d = _gp([[Vector((0, 0, 0)), Vector((1, 0, 0))]])
    g = bpy.context.scene.gp_guides
    g.mode, g.vp_count = 'PERSPECTIVE', '2'
    bpy.ops.gp_guides.add_vanishing_points()
    assert all(n in bpy.data.objects for n in ("VP1", "VP2"))
    segs = m.world_guide_lines(bpy.context, ob)
    assert len(segs) == 2 * g.density + 1
    gpu.init()
    with gpu.types.GPUOffScreen(64, 64).bind():
        m.draw_lines(segs, (1, 1, 1, 0.3), (64, 64))


def test_hotkey_pref():
    prefs = bpy.context.preferences.addons[m.__name__].preferences
    prefs.use_hotkey = True
    assert [k.idname for _, k in m._keymaps] == ["gp_guides.straighten"]
    prefs.use_hotkey = False
    assert not m._keymaps
