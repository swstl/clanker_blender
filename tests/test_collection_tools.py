import bpy

ctx = bpy.context


def _coll(name, parent=None):
    c = bpy.data.collections.new(name)
    (parent or ctx.scene.collection).children.link(c)
    return c


def _ob(name, coll):
    ob = bpy.data.objects.new(name, None)
    coll.objects.link(ob)
    return ob


def test_move_to_new_named_after_active():
    c = _coll("Stuff")
    a, b = _ob("Hero", c), _ob("Sword", c)
    for o in (a, b):
        o.select_set(True)
    ctx.view_layer.objects.active = a
    bpy.ops.collection_tools.move_to_new()
    new = bpy.data.collections["Hero"]
    assert set(new.objects) == {a, b}
    assert not c.objects


def test_sort_keeps_exclude():
    for n in ("c", "a", "b"):
        _coll(n)
    ctx.view_layer.layer_collection.children["b"].exclude = True
    ctx.view_layer.active_layer_collection = ctx.view_layer.layer_collection
    bpy.ops.collection_tools.sort()
    assert [c.name for c in ctx.scene.collection.children] == ["a", "b", "c"]
    assert ctx.view_layer.layer_collection.children["b"].exclude


def test_remove_empty_nested():
    outer = _coll("outer")
    _coll("inner", outer)
    keep = _coll("keep")
    _ob("x", keep)
    bpy.ops.collection_tools.remove_empty()
    assert "outer" not in bpy.data.collections and "inner" not in bpy.data.collections
    assert "keep" in bpy.data.collections


def test_isolate_toggle():
    a, b = _coll("A"), _coll("B")
    vl = ctx.view_layer
    vl.active_layer_collection = vl.layer_collection.children["A"]
    bpy.ops.collection_tools.isolate()
    assert vl.layer_collection.children["B"].hide_viewport
    assert not vl.layer_collection.children["A"].hide_viewport
    bpy.ops.collection_tools.isolate()
    assert not vl.layer_collection.children["B"].hide_viewport
