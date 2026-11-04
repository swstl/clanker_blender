import bpy


def _ob(name):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    return ob


def _select(*obs, active=None):
    for o in bpy.context.view_layer.objects:
        o.select_set(o in obs)
    bpy.context.view_layer.objects.active = active or obs[0]


def _types(ob):
    return [m.type for m in ob.modifiers]


def test_copy_stack_replace_and_append():
    src, dst, helper = _ob("src"), _ob("dst"), _ob("helper")
    arr = src.modifiers.new("Arr", 'ARRAY')
    arr.count, arr.use_object_offset, arr.offset_object = 4, True, helper
    src.modifiers.new("Sub", 'SUBSURF').levels = 3
    gn = src.modifiers.new("GN", 'NODES')
    gn.node_group = bpy.data.node_groups.new("G", 'GeometryNodeTree')
    dst.modifiers.new("Old", 'BEVEL')
    _select(src, dst, active=src)
    bpy.ops.modifier_tools.copy_stack(mode='REPLACE')
    assert _types(dst) == ['ARRAY', 'SUBSURF', 'NODES']
    a = dst.modifiers["Arr"]
    assert a.count == 4 and a.offset_object == helper
    assert dst.modifiers["Sub"].levels == 3
    assert dst.modifiers["GN"].node_group == gn.node_group
    bpy.ops.modifier_tools.copy_stack(mode='APPEND')
    assert len(dst.modifiers) == 6


def test_visibility_by_type():
    a, b = _ob("a"), _ob("b")
    for o in (a, b):
        o.modifiers.new("S", 'SUBSURF')
        o.modifiers.new("B", 'BEVEL')
    _select(a, b)
    bpy.ops.modifier_tools.visibility(mod_type='SUBSURF', target='VIEWPORT')
    assert not a.modifiers["S"].show_viewport and not b.modifiers["S"].show_viewport
    assert a.modifiers["B"].show_viewport and a.modifiers["S"].show_render
    bpy.ops.modifier_tools.visibility(mod_type='ALL', target='VIEWPORT')  # some hidden -> any shown -> hide all
    assert not any(m.show_viewport for m in a.modifiers)
    bpy.ops.modifier_tools.visibility(mod_type='ALL', target='RENDER', state='OFF')
    assert not any(m.show_render for o in (a, b) for m in o.modifiers)


def test_apply_except_armature():
    a, shared, sk = _ob("a"), _ob("shared"), _ob("sk")
    twin = bpy.data.objects.new("twin", shared.data)
    bpy.context.scene.collection.objects.link(twin)
    sk.shape_key_add(name="Basis")
    for o in (a, shared, sk):
        o.modifiers.new("Sub", 'SUBSURF').levels = 1
        o.modifiers.new("Arm", 'ARMATURE')
        o.modifiers.new("Bev", 'BEVEL')
    _select(a, shared, sk)
    bpy.ops.modifier_tools.apply()
    assert _types(a) == ['ARMATURE'] and len(a.data.vertices) > 26
    assert len(shared.modifiers) == 3 and len(sk.modifiers) == 3  # skipped
    _select(shared)
    bpy.ops.modifier_tools.apply(single_user=True, stop_at_armature=True)
    assert _types(shared) == ['ARMATURE', 'BEVEL']
    assert len(twin.data.vertices) == 8  # original data untouched


def test_fix_order():
    ob = _ob("o")
    for t in ('SUBSURF', 'WEIGHTED_NORMAL', 'BEVEL', 'ARMATURE', 'DISPLACE', 'TRIANGULATE', 'MIRROR'):
        ob.modifiers.new(t, t)
    _select(ob)
    bpy.ops.modifier_tools.fix_order()
    assert _types(ob) == ['ARMATURE', 'DISPLACE', 'BEVEL', 'MIRROR', 'SUBSURF', 'TRIANGULATE',
                          'WEIGHTED_NORMAL']


def test_remove_by_type_and_select():
    a, b = _ob("a"), _ob("b")
    a.modifiers.new("S", 'SUBSURF')
    a.modifiers.new("B", 'BEVEL')
    b.modifiers.new("B", 'BEVEL')
    _select(a)
    bpy.ops.modifier_tools.select_by_type(mod_type='BEVEL')
    assert a.select_get() and b.select_get()
    bpy.ops.modifier_tools.remove(mod_type='BEVEL')
    assert _types(a) == ['SUBSURF'] and not b.modifiers
    bpy.ops.modifier_tools.remove(mod_type='ALL')
    assert not a.modifiers


def test_expand_and_weighted_normal():
    ob = _ob("o")
    ob.modifiers.new("S", 'SUBSURF')
    _select(ob)
    bpy.ops.modifier_tools.expand(expand=False)
    assert not ob.modifiers[0].show_expanded
    bpy.ops.modifier_tools.add_weighted_normal()
    bpy.ops.modifier_tools.add_weighted_normal()
    assert _types(ob) == ['SUBSURF', 'WEIGHTED_NORMAL']
