import importlib
import math
import os
import tempfile

import bpy
import numpy as np

M = importlib.import_module("bl_ext.clanker.hdri_browser")


def _img(path, fmt, w=16, h=8, val=2.0):
    im = bpy.data.images.new("tmp", w, h, float_buffer=True)
    im.pixels.foreach_set(np.full(w * h * 4, val, np.float32))
    im.filepath_raw = path
    im.file_format = fmt
    im.save()
    bpy.data.images.remove(im)


def _setup():
    d = tempfile.mkdtemp(prefix="hdri_browser_")
    os.makedirs(os.path.join(d, "sub"))
    _img(os.path.join(d, "foo_1k.hdr"), 'HDR')
    _img(os.path.join(d, "sub", "foo_4k.exr"), 'OPEN_EXR', 32, 16)
    _img(os.path.join(d, "bar_2k.hdr"), 'HDR')
    open(os.path.join(d, "notes.txt"), "w").close()
    bpy.context.window_manager.hdri_browser_current = ""  # else pref updates re-apply it
    p = M._prefs()
    p.folders.clear()
    p.folders.add().path = d  # update callback rescans
    p.resolution = 'LOWEST'
    p.lowres_viewport = True
    return d


def test_scan_groups_resolutions():
    d = _setup()
    g = M.scan([d])
    assert sorted(g) == ["bar", "foo"], g
    assert sorted(g["foo"]) == ["1k", "4k"] and list(g["bar"]) == ["2k"]
    assert [n for n, _f in M._entries] == ["bar", "foo"]
    files = g["foo"]
    assert M.pick(files, 'LOWEST').endswith("foo_1k.hdr")
    assert M.pick(files, 'HIGHEST').endswith("foo_4k.exr")
    assert M.pick(files, '2K').endswith("foo_1k.hdr")
    assert M.pick({"4k": "a"}, '2K') == "a"


def test_thumbnails_cached():
    _setup()
    M.generate_thumbs()
    for name, files in M._entries:
        dst = M.thumb_path(M.pick(files, 'LOWEST'))
        assert os.path.isfile(dst) and name in M._pcoll, name
        im = bpy.data.images.load(dst)
        assert tuple(im.size) == (M.THUMB_W, M.THUMB_H)
        px = np.empty(len(im.pixels), np.float32)
        im.pixels.foreach_get(px)
        assert 0.3 < px[0] < 1.0  # tonemapped, not clipped white
        bpy.data.images.remove(im)
    left = [i.name for i in bpy.data.images if i.name.startswith(("_hdri_thumb", "foo", "bar"))]
    assert not left, left
    M.refresh()  # fresh cache: nothing re-queued
    assert not M._queue
    for _n, files in M._entries:
        os.remove(M.thumb_path(M.pick(files, 'LOWEST')))


def test_apply_and_switch():
    _setup()
    wm = bpy.context.window_manager
    world = bpy.context.scene.world
    items = M._pick_items(wm, bpy.context)
    assert [i[0] for i in items] == ["bar", "foo"]
    wm.hdri_browser_pick = "foo"
    nodes = world.node_tree.nodes
    env, mp = nodes[M.ENV], nodes[M.MAPPING]
    assert env.image.filepath.endswith("foo_1k.hdr")
    assert env.inputs["Vector"].links[0].from_node == mp
    assert mp.inputs["Vector"].links[0].from_socket.name == "Generated"
    bg = next(n for n in nodes if n.bl_idname == 'ShaderNodeBackground')
    assert bg.inputs["Color"].links[0].from_node == env
    mp.inputs["Rotation"].default_value[2] = math.radians(90)
    bg.inputs["Strength"].default_value = 2.5
    old = env.image.name

    bpy.ops.hdri_browser.step(direction=1)  # wraps to bar
    assert wm.hdri_browser_current == "bar"
    assert len([n for n in nodes if n.bl_idname == 'ShaderNodeTexEnvironment']) == 1
    assert len([n for n in nodes if n.bl_idname == 'ShaderNodeMapping']) == 1
    assert env.image.filepath.endswith("bar_2k.hdr")
    assert abs(mp.inputs["Rotation"].default_value[2] - math.radians(90)) < 1e-5
    assert bg.inputs["Strength"].default_value == 2.5
    assert old not in bpy.data.images
    assert M.RENDER_KEY not in env  # single resolution: nothing to swap


def test_render_swap():
    _setup()
    scene = bpy.context.scene
    env = M.apply_entry(bpy.context, "foo")
    view = env.image.filepath
    assert view.endswith("foo_1k.hdr") and env[M.RENDER_KEY].endswith("foo_4k.exr")
    M._render_pre(scene)
    assert env.image.filepath.endswith("foo_4k.exr")
    M._render_pre(scene)  # next frame of an animation: no double swap
    M._render_post(scene)
    assert env.image.filepath == view and M.VIEW_KEY not in env
    M._prefs().lowres_viewport = False  # update re-applies at viewport res for both
    bpy.context.window_manager.hdri_browser_current = "foo"
    M.apply_entry(bpy.context, "foo")
    assert M.RENDER_KEY not in env


def test_filter_favorites():
    _setup()
    wm = bpy.context.window_manager
    wm.hdri_browser_current = ""
    bpy.ops.hdri_browser.favorite(name="foo")
    wm.hdri_browser_favorites_only = True
    assert [i[0] for i in M._pick_items(wm, bpy.context)] == ["foo"]
    wm.hdri_browser_favorites_only = False
    wm.hdri_browser_filter = "BA"
    assert [i[0] for i in M._pick_items(wm, bpy.context)] == ["bar"]
    wm.hdri_browser_filter = ""
    bpy.ops.hdri_browser.favorite(name="foo")
    assert len(M._prefs().favorites) == 0
