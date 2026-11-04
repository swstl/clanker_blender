import importlib

import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.export_preflight")


def _cube(name, uv=True, mat=True, size=2.0):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    if uv:
        bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_cube(bm, size=size, calc_uvs=uv)
    bm.to_mesh(me)
    bm.free()
    if mat:
        me.materials.append(bpy.data.materials.get("M_Clean") or bpy.data.materials.new("M_Clean"))
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _run(obs, profile='UNITY', **kw):
    s = bpy.context.scene.export_preflight
    s.profile = profile
    s.source = 'SELECTED'
    s.vert_budget = kw.get("budget", 50000)
    s.unreal_collision = kw.get("collision", False)
    return m.run_checks(bpy.context, obs)


def _sev(rows, obj, text):
    """Severities of findings on obj whose problem contains text."""
    return [r[0] for r in rows if r[1] == obj and text in r[2]]


def test_clean_cube_and_profiles():
    ob = _cube("Crate")
    assert _run([ob]) == [], _run([ob])
    assert _sev(_run([ob], 'UNREAL'), "Crate", "lightmap") == ['WARN']
    ob.data.uv_layers.new(name="UV2")
    ob.data.uv_layers.new(name="UV3")
    assert _sev(_run([ob], 'GODOT'), "Crate", "UV maps") == ['WARN']
    assert _run([ob], 'UNITY') == []


def test_transform_rules():
    a, b, c, d = (_cube(n) for n in ("Scaled", "NonUni", "Neg", "Rot"))
    a.scale = (2, 2, 2)
    b.scale = (1, 2, 1)
    c.scale = (-1, 1, 1)
    d.rotation_euler = (0, 0, 0.5)
    rows = _run([a, b, c, d])
    assert _sev(rows, "Scaled", "Unapplied scale") == ['WARN']
    assert _sev(rows, "NonUni", "Non-uniform") == ['WARN']
    assert _sev(rows, "Neg", "Negative scale") == ['WARN']
    assert _sev(rows, "Rot", "rotation") == ['INFO']
    assert _sev(_run([d], 'UNREAL'), "Rot", "rotation") == ['WARN']
    far = _cube("Far")
    far.data.transform(__import__("mathutils").Matrix.Translation((10, 0, 0)))
    rows = _run([far])
    assert _sev(rows, "Far", "Origin far") == ['INFO']
    bpy.ops.export_preflight.fix(fix="origin", obj="Far", item="")
    assert not _sev(m._findings, "Far", "Origin far")
    assert abs(far.location.x - 10) < 1e-5 and abs(far.location.z + 1) < 1e-5


def test_mesh_rules():
    ob = _cube("Messy")
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    v = [bm.verts.new(p) for p in ((5, 0, 0), (6, 0, 0), (6.5, 1, 0), (6, 2, 0), (5, 2, 0))]
    bm.faces.new(v)  # n-gon, open -> non-manifold edges
    bm.verts.new((9, 9, 9))  # loose vert
    bm.edges.new((bm.verts.new((8, 8, 8)), bm.verts.new((8, 8, 9))))  # loose edge
    z = [bm.verts.new(p) for p in ((0, 5, 0), (1, 5, 0), (2, 5, 0))]
    bm.faces.new(z)  # zero area
    bm.to_mesh(me)
    bm.free()
    rows = _run([ob])
    assert _sev(rows, "Messy", "n-gons") == ['INFO']
    assert _sev(rows, "Messy", "1 loose vertices, 1 loose edges") == ['WARN']
    assert _sev(rows, "Messy", "zero-area") == ['WARN']
    assert _sev(rows, "Messy", "non-manifold") == ['INFO']
    bpy.ops.export_preflight.fix(fix="loose", obj="Messy", item="")
    assert not _sev(m._findings, "Messy", "loose")
    assert _sev(_run([ob], budget=10), "Messy", "budget 10") == ['WARN']
    assert not _sev(_run([ob], budget=0), "Messy", "budget")


def test_uv_and_materials():
    a = _cube("NoUV", uv=False)
    b = _cube("NoMat", mat=False)
    c = _cube("ManyMats")
    for i in range(9):
        c.data.materials.append(bpy.data.materials.new(f"M{i}"))
    c.data.materials.append(None)
    rows = _run([a, b, c])
    assert _sev(rows, "NoUV", "No UV map") == ['ERROR']
    assert _sev(rows, "NoMat", "No material") == ['WARN']
    assert _sev(rows, "ManyMats", "material slots (max 8)") == ['WARN']
    assert _sev(rows, "ManyMats", "empty material slot") == ['WARN']
    assert not _sev(_run([c], 'GENERIC'), "ManyMats", "max")


def test_modifiers_and_shape_keys():
    ob = _cube("Mods")
    ob.modifiers.new("Bevel", 'BEVEL')
    rows = _run([ob])
    assert _sev(rows, "Mods", "Unapplied modifiers: Bevel") == ['INFO']
    assert not _sev(rows, "Mods", "Shape keys")
    ob.shape_key_add(name="Basis")
    assert _sev(_run([ob]), "Mods", "Shape keys") == ['WARN']


def _rig(scale=1.0):
    arm = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    for i in range(6):
        eb = arm.data.edit_bones.new(f"b{i}" if i else "bad bone")
        eb.head, eb.tail = (i, 0, 0), (i, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    ob = _cube("Skin")
    ob.modifiers.new("Armature", 'ARMATURE').object = arm
    ob.parent = arm
    for b in arm.data.bones:
        ob.vertex_groups.new(name=b.name)
    for i in range(6):  # vertex 0: 6 influences, sum 1.2
        ob.vertex_groups[i].add([0], 0.2, 'REPLACE')
    for v in range(1, 8):
        ob.vertex_groups[1].add([v], 1.0, 'REPLACE')
    ob.vertex_groups[1].add([1], 0.5, 'REPLACE')  # unnormalized single weight
    return arm, ob


def test_skinning_and_fix_all_safe():
    arm, ob = _rig()
    arm.scale = (100, 100, 100)
    ob.scale = (0.5, 0.5, 0.5)
    rows = _run([arm, ob])
    assert _sev(rows, "Skin", "more than 4 bone influences") == ['WARN']
    assert not _sev(_run([arm, ob], 'UNREAL'), "Skin", "bone influences")
    rows = _run([arm, ob], 'GODOT')
    assert _sev(rows, "Skin", "more than 4") == ['WARN']
    assert _sev(rows, "Skin", "2 vertices with weights not summing") == ['WARN']
    assert _sev(rows, "Rig", "Armature scale") == ['WARN']
    assert _sev(rows, "Rig", "Bone name 'bad bone' has spaces") == ['WARN']
    bpy.ops.export_preflight.fix_all()
    assert tuple(ob.scale) == (1, 1, 1)
    assert tuple(arm.scale) == (100, 100, 100)  # armature scale is not a safe fix
    deform = {vg.index for vg in ob.vertex_groups}
    for v in ob.data.vertices:
        assert abs(sum(g.weight for g in v.groups if g.group in deform) - 1) < 1e-4
    assert not _sev(m._findings, "Skin", "summing")
    assert not _sev(m._findings, "Skin", "scale")
    assert _sev(m._findings, "Skin", "more than 4")  # limiting influences is not "safe"
    bpy.ops.export_preflight.fix(fix="limit", obj="Skin", item="")
    assert not _sev(m._findings, "Skin", "more than 4")
    assert len(ob.data.vertices[0].groups) == 4
    bpy.ops.export_preflight.fix(fix="rename_bone", obj="Rig", item="bad bone")
    assert "bad_bone" in arm.data.bones and "bad_bone" in ob.vertex_groups


def test_naming():
    a, b, c, d = (_cube(n) for n in ("My Crate", "Café", "a b", "a_b"))
    a.data.materials[0] = bpy.data.materials.new("Wood Mat")
    rows = _run([a, b, c, d])
    assert _sev(rows, "My Crate", "Object name 'My Crate' has spaces") == ['WARN']
    assert _sev(rows, "Café", "non-ASCII") == ['WARN']
    assert len([r for r in rows if "clash" in r[2] and "a b" in r[2] and "a_b" in r[2]]) == 1
    assert _sev(rows, "My Crate", "Material name 'Wood Mat'") == ['WARN']
    bpy.ops.export_preflight.fix(fix="rename_mat", obj="My Crate", item="Wood Mat")
    assert a.data.materials[0].name == "Wood_Mat"
    bpy.ops.export_preflight.fix(fix="rename_ob", obj="My Crate", item="My Crate")
    assert a.name == "My_Crate"


def test_unreal_collision():
    ob = _cube("Rock")
    assert not _sev(_run([ob], 'UNREAL'), "Rock", "collision")
    assert _sev(_run([ob], 'UNREAL', collision=True), "Rock", "No collision") == ['WARN']
    col = _cube("UCX_Rock_01", uv=False, mat=False)
    rows = _run([ob, col], 'UNREAL', collision=True)
    assert not _sev(rows, "Rock", "collision")
    assert not _sev(rows, "UCX_Rock_01", "UV") and not _sev(rows, "UCX_Rock_01", "material")


def test_textures():
    ob = _cube("Tex")
    mat = ob.data.materials[0]
    for name, (w, h) in (("odd", (100, 60)), ("pot", (64, 64))):
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.new(name, w, h)
    gone = mat.node_tree.nodes.new("ShaderNodeTexImage")
    gone.image = bpy.data.images.new("gone", 4, 4)
    gone.image.source = 'FILE'
    gone.image.filepath = "/nonexistent/dir/gone.png"
    rows = _run([ob])
    assert _sev(rows, "Tex", "Texture odd is 100x60") == ['WARN']
    assert not _sev(rows, "Tex", "Texture pot")
    assert _sev(rows, "Tex", "missing: gone") == ['ERROR']
    assert _sev(_run([ob], 'GODOT'), "Tex", "Texture odd") == ['INFO']


def test_select_and_collection_source_and_report():
    a, b = _cube("First"), _cube("Second")
    b.scale = (3, 3, 3)
    coll = bpy.data.collections.new("Export")
    bpy.context.scene.collection.children.link(coll)
    coll.objects.link(b)
    s = bpy.context.scene.export_preflight
    s.source, s.collection = 'COLLECTION', coll
    m.run_checks(bpy.context)
    assert m._checked == ["Second"]
    a.select_set(True)
    bpy.ops.export_preflight.select(obj="Second")
    assert b.select_get() and not a.select_get() and bpy.context.view_layer.objects.active == b
    bpy.ops.export_preflight.copy()  # clipboard is not readable back in --background
    text = m.report_text()
    assert "Export Preflight (Unity): 1 object(s)" in text and "Second: Unapplied scale 3" in text, text
    s.source = 'SELECTED'
