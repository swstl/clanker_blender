import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.texture_proxies")


def _setup():
    d = tempfile.mkdtemp()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(d, "p.blend"))
    big = bpy.data.images.new("Big", 2048, 1024)
    small = bpy.data.images.new("Small", 256, 256)
    mat = bpy.data.materials.new("M")
    nodes = []
    for img in (big, small):
        n = mat.node_tree.nodes.new("ShaderNodeTexImage")
        n.image = img
        nodes.append(n)
    return d, mat, nodes, big


def test_proxy_roundtrip_and_render_swap():
    d, mat, (nb, ns), big = _setup()
    try:
        n, saved = m.use_proxies([mat], 512)
        assert n == 1 and saved > 0, (n, saved, nb.image.name, tuple(nb.image.size))
        assert tuple(nb.image.size) == (512, 256) and ns.image.name == "Small"
        assert os.path.isfile(os.path.join(d, "proxies", "Big__proxy512.png"))
        bpy.context.scene.texture_proxies.full_res_render = True
        m._render_pre(bpy.context.scene)
        assert nb.image == big
        m._render_post(bpy.context.scene)
        assert nb.image.name == "Big__proxy512"
        assert m.use_proxies([mat], 512)[0] == 0  # already proxied
        assert m.use_originals() == 1 and nb.image == big
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
