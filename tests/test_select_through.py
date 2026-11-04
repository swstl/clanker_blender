import importlib
import types

import bmesh
import bpy
from mathutils import Matrix

m = importlib.import_module("bl_ext.clanker.select_through")
SIZE = (100, 100)
FULL = [(0, 0), (100, 0), (100, 100), (0, 100)]
LEFT = [(0, 0), (50, 0), (50, 100), (0, 100)]  # cube x < 0 lands at px 25, x > 0 at 75
VERTS = (True, False, False)


def _ortho_top():
    """Orthographic view looking down -Z from z=10, +-2 units fill the region."""
    n, f = 0.1, 100.0
    proj = Matrix(((0.5, 0, 0, 0), (0, 0.5, 0, 0), (0, 0, -2 / (f - n), -(f + n) / (f - n)), (0, 0, 0, 1)))
    return proj @ Matrix.Translation((0, 0, -10))


def _cube():
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    me = bpy.data.meshes.new("C")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("C", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(me)
    for seq in (bm.verts, bm.edges, bm.faces):
        for e in seq:
            e.select = False
    return ob, bm


def _sel(bm):
    return {tuple(round(c) for c in v.co) for v in bm.verts if v.select}


def _run(test):
    ob, bm = _cube()
    try:
        test(ob, bm)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def test_through_half_extend_subtract():
    def t(ob, bm):
        P = _ortho_top()
        assert m.select_in_polygon([ob], FULL, P, SIZE, 'SET', VERTS) == 8
        assert len(_sel(bm)) == 8  # back verts too: no occlusion
        m.select_in_polygon([ob], LEFT, P, SIZE, 'SET', VERTS)
        assert _sel(bm) == {(-1, y, z) for y in (-1, 1) for z in (-1, 1)}, _sel(bm)
        assert sum(f.select for f in bm.faces) == 1  # flushed: the x=-1 face
        m.select_in_polygon([ob], [(50, 0), (100, 0), (100, 50), (50, 50)], P, SIZE, 'ADD', VERTS)
        assert len(_sel(bm)) == 6
        m.select_in_polygon([ob], [(0, 0), (100, 0), (100, 50), (0, 50)], P, SIZE, 'SUB', VERTS)
        assert _sel(bm) == {(-1, 1, -1), (-1, 1, 1)}, _sel(bm)
    _run(t)


def test_hidden_and_edges_faces():
    def t(ob, bm):
        P = _ortho_top()
        hid = next(v for v in bm.verts if v.co.x < 0 and v.co.y < 0 and v.co.z > 0)
        hid.hide = True
        assert m.select_in_polygon([ob], LEFT, P, SIZE, 'SET', VERTS) == 3
        assert not hid.select
        hid.hide = False
        # faces mode: only the x=-1 face has all verts inside the left half, "touch" gets 5
        assert m.select_in_polygon([ob], LEFT, P, SIZE, 'SET', (False, False, True)) == 1
        assert m.select_in_polygon([ob], LEFT, P, SIZE, 'SET', (False, False, True), use_any=True) == 5
        assert m.select_in_polygon([ob], LEFT, P, SIZE, 'SET', (False, True, False)) == 4
        # lasso: triangle around the lower-left corner column only
        assert m.select_in_polygon([ob], [(10, 10), (45, 10), (10, 45)], P, SIZE, 'SET', VERTS) == 2
    _run(t)


def test_front_only_ortho_and_perspective():
    def t(ob, bm):
        m.select_in_polygon([ob], FULL, _ortho_top(), SIZE, 'SET', VERTS, front_only=True)
        assert _sel(bm) == {(x, y, 1) for x in (-1, 1) for y in (-1, 1)}, _sel(bm)
        cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
        cam.location = (0, 0, 10)  # default orientation looks down -Z
        bpy.context.scene.collection.objects.link(cam)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        P = cam.calc_matrix_camera(dg, x=100, y=100) @ cam.matrix_world.inverted()
        assert m.select_in_polygon([ob], FULL, P, SIZE, 'SET', VERTS) == 8
        m.select_in_polygon([ob], FULL, P, SIZE, 'SET', VERTS, front_only=True)
        assert _sel(bm) == {(x, y, 1) for x in (-1, 1) for y in (-1, 1)}, _sel(bm)
    _run(t)


def test_draw_and_keymap():
    import gpu
    gpu.init()
    for lasso in (False, True):
        ns = types.SimpleNamespace(pts=[(0, 0), (30, 5), (10, 20)], lasso=lasso, _region=bpy.context.region)
        ns._outline = lambda ns=ns: m._SelectThrough._outline(ns)
        with gpu.types.GPUOffScreen(64, 64).bind():
            m._SelectThrough._draw(ns)
    prefs = bpy.context.preferences.addons["bl_ext.clanker.select_through"].preferences
    prefs.use_hotkeys = False
    assert not m._keymaps
    prefs.use_hotkeys = True


def test_poll_needs_window_region():
    class Ctx:  # F3 over the header: context.region is the HEADER, mouse coords would be wrong
        mode = 'EDIT_MESH'
        space_data = types.SimpleNamespace(type='VIEW_3D')
        region = types.SimpleNamespace(type='HEADER')
    assert not m._SelectThrough.poll.__func__(m.ST_OT_box, Ctx)
    Ctx.region = types.SimpleNamespace(type='WINDOW')
    assert m._SelectThrough.poll.__func__(m.ST_OT_box, Ctx)
