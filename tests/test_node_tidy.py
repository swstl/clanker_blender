import importlib

import bpy

m = importlib.import_module("bl_ext.clanker.node_tidy")


def _editor(tree):
    win = bpy.context.window_manager.windows[0]
    area = win.screen.areas[0]
    area.type = 'NODE_EDITOR'
    sp = area.spaces.active
    sp.tree_type = tree.bl_idname
    sp.node_tree = tree
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return bpy.context.temp_override(window=win, area=area, region=region, space_data=sp)


def _messy():
    """tex -> mix -> bsdf -> output, at messy heights."""
    nt = bpy.data.materials.new("NT_Mat").node_tree
    bsdf, out = nt.nodes["Principled BSDF"], nt.nodes["Material Output"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    mix = nt.nodes.new("ShaderNodeMath")
    nt.links.new(tex.outputs[0], mix.inputs[0])
    nt.links.new(mix.outputs[0], bsdf.inputs["Base Color"])
    for n, xy in ((tex, (-900, 130)), (mix, (-500, -333)), (bsdf, (-200, 417)), (out, (300, -71))):
        n.location = xy
    for n in nt.nodes:
        n.select = False
    return nt, tex, mix, bsdf, out


def test_straighten_upstream():
    nt, tex, mix, bsdf, out = _messy()
    for n in (mix, bsdf, out):
        n.select = True
    with _editor(nt):
        assert bpy.ops.node_tidy.straighten(direction='UPSTREAM') == {'FINISHED'}
    for l in nt.links:
        assert abs(m.socket_y(l.from_socket) - m.socket_y(l.to_socket)) < 1e-3, (l.from_node.name, l.to_node.name)
    assert tex.location.y == 130  # unselected source stays


def test_straighten_downstream():
    nt, tex, mix, bsdf, out = _messy()
    assert m.straighten([tex, mix], upstream=False) == 2
    l = mix.inputs[0].links[0]
    assert abs(m.socket_y(l.from_socket) - m.socket_y(l.to_socket)) < 1e-3
    l = bsdf.inputs["Base Color"].links[0]
    assert abs(m.socket_y(l.from_socket) - m.socket_y(l.to_socket)) < 1e-3


def test_align_and_distribute():
    nt, tex, mix, bsdf, out = _messy()
    nodes = [tex, mix, bsdf]
    m.align(nodes, 'LEFT')
    assert {n.location.x for n in nodes} == {-900}
    m.align(nodes, 'TOP')
    assert {n.location.y for n in nodes} == {417}
    m.align(nodes, 'RIGHT')
    assert len({round(m.box(n)[2], 3) for n in nodes}) == 1
    m.distribute(nodes, 'Y', gap=30)
    boxes = sorted((m.box(n) for n in nodes), key=lambda b: -b[1])
    assert boxes[0][1] == 417
    for a, b in zip(boxes, boxes[1:]):
        assert abs(a[3] - b[1] - 30) < 1e-3
    for n in nodes:
        n.select = True
    with _editor(nt):
        bpy.ops.node_tidy.distribute(axis='X', gap=50)
    boxes = sorted((m.box(n) for n in nodes), key=lambda b: b[0])
    for a, b in zip(boxes, boxes[1:]):
        assert abs(b[0] - a[2] - 50) < 1e-3


def _reroute_chain():
    nt, tex, mix, bsdf, out = _messy()
    nt.links.remove(mix.inputs[0].links[0])
    rs = []
    prev = tex.outputs[0]
    for i, y in enumerate((50, 210, -40)):
        r = nt.nodes.new("NodeReroute")
        r.location = (-800 + i * 100, y)
        nt.links.new(prev, r.inputs[0])
        prev = r.outputs[0]
        rs.append(r)
    nt.links.new(prev, mix.inputs[0])
    nt.links.new(prev, mix.inputs[1])
    return nt, tex, mix, rs


def test_reroute_straighten():
    nt, tex, mix, rs = _reroute_chain()
    assert m.straighten_reroutes(rs) == 3
    ys = {round(r.location.y, 3) for r in rs}
    assert ys == {round(m.socket_y(tex.outputs[0]), 3)}


def test_reroute_remove_keeps_links():
    nt, tex, mix, rs = _reroute_chain()
    for r in rs:
        r.select = True
    with _editor(nt):
        bpy.ops.node_tidy.reroute_remove()
    assert not [n for n in nt.nodes if m.is_reroute(n)]
    for ident in (0, 1):
        (l,) = mix.inputs[ident].links
        assert l.from_socket == tex.outputs[0]


def test_reroute_merge():
    nt, tex, mix, bsdf, out = _messy()
    a, b = nt.nodes.new("NodeReroute"), nt.nodes.new("NodeReroute")
    nt.links.new(mix.outputs[0], a.inputs[0])
    nt.links.new(mix.outputs[0], b.inputs[0])
    nt.links.new(a.outputs[0], bsdf.inputs["Base Color"])
    nt.links.new(b.outputs[0], bsdf.inputs["Emission Color"])
    assert m.merge_reroutes(nt, [a, b]) == 1
    (r,) = [n for n in nt.nodes if m.is_reroute(n)]
    assert {l.to_socket.identifier for l in r.outputs[0].links} == {bsdf.inputs["Base Color"].identifier,
                                                                    bsdf.inputs["Emission Color"].identifier}


def test_frames():
    nt, tex, mix, bsdf, out = _messy()
    before = {n.name: tuple(n.location_absolute) for n in (tex, mix)}
    tex.select = mix.select = True
    nt.nodes.active = mix
    with _editor(nt):
        bpy.ops.node_tidy.frame()
        f = nt.nodes.active
        assert f.bl_idname == "NodeFrame" and f.label == mix.name and f.shrink and f.use_custom_color
        assert tex.parent == f and mix.parent == f and bsdf.parent is None
        assert {n.name: tuple(n.location_absolute) for n in (tex, mix)} == before
        for n in nt.nodes:
            n.select = n == f
        bpy.ops.node_tidy.frame_select()
        assert tex.select and mix.select and not bsdf.select
        for n in nt.nodes:
            n.select = n == f
        bpy.ops.node_tidy.frame_remove()
    assert not [n for n in nt.nodes if n.bl_idname == "NodeFrame"]
    assert tex.parent is None and {n.name: tuple(n.location_absolute) for n in (tex, mix)} == before
    f = m.frame_nodes(nt, [bsdf], "Shading")
    assert f.label == "Shading" and bsdf.parent == f


def test_grid_snap():
    nt, tex, mix, bsdf, out = _messy()
    m.snap([tex, mix, bsdf, out], 25)
    for n in (tex, mix, bsdf, out):
        assert n.location.x % 25 == 0 and n.location.y % 25 == 0
    assert tuple(mix.location) == (-500, -325)
