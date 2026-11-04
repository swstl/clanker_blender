import importlib
import os
import tempfile

import bpy

m = importlib.import_module("bl_ext.clanker.node_finder")


def _group(name, inner=None):
    g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    g.interface.new_socket("Value", in_out='OUTPUT', socket_type='NodeSocketFloat')
    out = g.nodes.new("NodeGroupOutput")
    if inner:
        src = g.nodes.new("ShaderNodeGroup")
        src.node_tree = inner
    else:
        src = g.nodes.new("ShaderNodeMath")
        src.name = "InnerMath"
    g.links.new(src.outputs[0], out.inputs[0])
    return g


def _build():
    inner = _group("NF_Inner")
    outer = _group("NF_Outer", inner)

    m1 = bpy.data.materials.new("NF_Mat1")
    nt = m1.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.new("NF_Img", 4, 4)
    nt.links.new(tex.outputs[0], bsdf.inputs["Base Color"])
    frame = nt.nodes.new("NodeFrame")
    tex.parent = frame
    g = nt.nodes.new("ShaderNodeGroup")
    g.node_tree = outer
    nt.links.new(g.outputs[0], bsdf.inputs["Roughness"])
    nt.nodes.new("ShaderNodeMath").name = "Orphan"
    nt.nodes.new("NodeReroute").name = "R"

    m2 = bpy.data.materials.new("NF_Mat2")
    t2 = m2.node_tree.nodes.new("ShaderNodeTexImage")
    t2.name = "NoImage"
    m2.node_tree.nodes.new("ShaderNodeGroup").node_tree = inner

    gn = bpy.data.node_groups.new("NF_GN", "GeometryNodeTree")
    gn.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    gn.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    gi, go = gn.nodes.new("NodeGroupInput"), gn.nodes.new("NodeGroupOutput")
    sp = gn.nodes.new("GeometryNodeSetPosition")
    sp.name = "GNNode"
    gn.links.new(gi.outputs[0], sp.inputs[0])
    gn.links.new(sp.outputs[0], go.inputs[0])
    gn.nodes.new("GeometryNodeMeshCube").name = "GNUnused"

    me = bpy.data.meshes.new("NF_Cube")
    me.materials.append(m2)
    me.materials.append(m1)
    ob = bpy.data.objects.new("NF_Cube", me)
    bpy.context.scene.collection.objects.link(ob)
    md = ob.modifiers.new("GN", 'NODES')
    md.node_group = gn

    comp = bpy.data.node_groups.new("NF_Comp", "CompositorNodeTree")
    comp.nodes.new("CompositorNodeViewer")
    comp.nodes.new("CompositorNodeBlur").name = "CompBlur"
    bpy.context.scene.compositing_node_group = comp
    return dict(inner=inner, outer=outer, m1=m1, m2=m2, gn=gn, ob=ob, comp=comp)


def _mine(hits):
    return [h for h in hits if h.owner.name.startswith("NF_") or h.kind == "SCENE"]


def test_search():
    d = _build()
    hits = m.search(node_type="Image Texture")
    assert {(h.kind, h.owner.name) for h in hits} == {("MATERIAL", "NF_Mat1"), ("MATERIAL", "NF_Mat2")}
    (h,) = m.search(query="innermath")
    assert m.path_label(h) == "NF_Mat1 > NF_Outer > NF_Inner > InnerMath", m.path_label(h)
    assert [x.name for x in h.trees] == ["Shader Nodetree", "NF_Outer", "NF_Inner"]
    assert len(m.search(image="nf_img")) == 1
    assert len(m.search(group="NF_Inner")) == 2
    (h,) = m.search(query="GNNode")
    assert h.kind == "MODIFIER" and m.path_label(h) == "NF_Cube > GN > GNNode"
    (h,) = m.search(query="CompBlur")
    assert h.kind == "SCENE" and h.owner == bpy.context.scene
    assert {"Image Texture", "Group", "Set Position", "Blur"} <= set(m.node_types())
    # every node exactly once
    nodes = [h.node.as_pointer() for h in m.walk()]
    assert len(nodes) == len(set(nodes))

    s = bpy.context.window_manager.node_finder
    s.query, s.node_type = "", "Image Texture"
    assert bpy.ops.node_finder.search() == {'FINISHED'}
    assert len(s.results) == 2 and "Image Texture" in s.types


def test_where_used_and_missing_images():
    d = _build()
    used = m.where_used(d["inner"])
    assert sorted(m.path_label(h) for h in used) == ["NF_Mat1 > NF_Outer > Group", "NF_Mat2 > Group"]
    (h,) = m.where_used(d["gn"])
    assert h.node is None and m.path_label(h) == "NF_Cube > GN"
    (h,) = _mine(m.missing_images())
    assert h.node.name == "NoImage"
    s = bpy.context.window_manager.node_finder
    s.group_a = "NF_Inner"
    bpy.ops.node_finder.where_used()
    assert len(s.results) == 2
    bpy.ops.node_finder.missing_images()
    assert any(r.node == "NoImage" for r in s.results)


def test_replace_group():
    d = _build()
    new = _group("NF_Inner2")
    assert m.replace_group(d["inner"], new) == 2
    assert not m.where_used(d["inner"]) and len(m.where_used(new)) == 2
    for bad in (d["outer"], d["gn"]):  # recursive / wrong type
        try:
            m.replace_group(new, bad)
            raise AssertionError("must refuse")
        except ValueError:
            pass
    s = bpy.context.window_manager.node_finder
    s.group_a, s.group_b = "NF_Inner2", "NF_Inner"
    assert bpy.ops.node_finder.replace_group() == {'FINISHED'}
    assert len(m.where_used(d["inner"])) == 2


def test_remove_unused():
    d = _build()
    names = {(h.trees[-1].name, h.node.name) for h in _mine(m.find_unused())}
    assert {n for _, n in names} == {"Orphan", "R", "NoImage", "Group", "GNUnused", "CompBlur"}, names
    assert ("NF_Outer", "Group") not in names, "groups that feed the output stay"
    s = bpy.context.window_manager.node_finder
    bpy.ops.node_finder.remove_unused(dry_run=True)
    assert len(s.results) >= 6 and "Orphan" in d["m1"].node_tree.nodes, "dry run changes nothing"
    bpy.ops.node_finder.remove_unused(dry_run=False)
    nt = d["m1"].node_tree.nodes
    assert "Orphan" not in nt and "R" not in nt
    assert {"Frame", "Image Texture", "Group", "Principled BSDF", "Material Output"} <= set(nt.keys())
    assert len(d["m2"].node_tree.nodes) == 2  # BSDF + output
    assert "GNUnused" not in d["gn"].nodes and "GNNode" in d["gn"].nodes
    assert "CompBlur" not in d["comp"].nodes
    assert not _mine(m.find_unused())


def test_linked_is_read_only():
    path = os.path.join(tempfile.mkdtemp(), "nf_lib.blend")
    mat = bpy.data.materials.new("NF_LibMat")
    mat.node_tree.nodes.new("ShaderNodeMath").name = "LibOrphan"
    bpy.data.libraries.write(path, {mat})
    bpy.data.materials.remove(mat)
    with bpy.data.libraries.load(path, link=True) as (src, dst):
        dst.materials = ["NF_LibMat"]
    (h,) = m.search(query="LibOrphan")
    assert m.read_only(h)
    assert not [x for x in m.find_unused() if x.node.name == "LibOrphan"]
    s = bpy.context.window_manager.node_finder
    m.store(s, [h])
    owner, chain = m.resolve(s.results[0])
    assert owner.library and chain[0][0].nodes.get("LibOrphan")
    bpy.data.materials.remove(owner)
    bpy.data.libraries.remove(bpy.data.libraries[0])


def test_jump_chain():
    d = _build()
    s = bpy.context.window_manager.node_finder
    m.store(s, m.search(query="InnerMath"))
    item = s.results[0]
    owner, chain = m.resolve(item)
    outer_node = d["m1"].node_tree.nodes["Group"]
    inner_node = d["outer"].nodes["Group"]
    assert owner == d["m1"]
    assert chain == [(d["m1"].node_tree, None), (d["outer"], outer_node), (d["inner"], inner_node)]

    vl = bpy.context.view_layer
    vl.update()  # view_layer.objects is stale right after clean() + linking
    assert m.user_object("MATERIAL", d["m1"], "", vl) == (d["ob"], 1)
    assert m.jump(bpy.context, item) is None  # no node editor in background: selection only
    assert vl.objects.active == d["ob"] and d["ob"].active_material_index == 1
    assert d["inner"].nodes.active.name == "InnerMath" and d["inner"].nodes["InnerMath"].select

    s.query = "GNNode"
    bpy.ops.node_finder.search()
    s.index = 0  # clicking a row jumps
    assert d["ob"].modifiers["GN"].is_active and d["gn"].nodes.active.name == "GNNode"
    m.store(s, m.search(query="InnerMath"))
    d["m1"].node_tree.nodes.remove(d["m1"].node_tree.nodes["Group"])
    assert m.resolve(s.results[0]) is None, "stale result resolves to None"


def test_jump_in_node_editor():
    d = _build()
    bpy.context.view_layer.update()
    win = bpy.context.window_manager.windows[0]
    area = win.screen.areas[0]
    old = area.type
    area.type = 'NODE_EDITOR'
    sp = area.spaces.active
    s = bpy.context.window_manager.node_finder
    pending = []
    register = bpy.app.timers.register
    bpy.app.timers.register = lambda fn, **k: pending.append(fn)
    try:
        for query, kind, path in (("InnerMath", "ShaderNodeTree", ["Shader Nodetree", "NF_Outer", "NF_Inner"]),
                                  ("GNNode", "GeometryNodeTree", ["NF_GN"]),
                                  ("CompBlur", "CompositorNodeTree", ["NF_Comp"])):
            m.store(s, m.search(query=query))
            with bpy.context.temp_override(window=win, area=area):
                assert m.jump(bpy.context, s.results[0]) is None
            assert sp.tree_type == kind
            pending.pop(0)()  # background never refreshes the editor: finish falls back to pinning
            assert len(pending) == 1  # framing waits for a redraw (node bounds)
            pending.pop()()
            assert [p.node_tree.name for p in sp.path] == path, [p.node_tree.name for p in sp.path]
    finally:
        bpy.app.timers.register = register
        area.type = old
