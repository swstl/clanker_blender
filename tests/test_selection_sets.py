import bpy


def _obs():
    out = []
    for n in "ABCD":
        o = bpy.data.objects.new(n, None)
        bpy.context.scene.collection.objects.link(o)
        out.append(o)
    return out


def test_sets_roundtrip():
    a, b, c, d = _obs()
    sc = bpy.context.scene
    sc.selection_sets.clear()
    for o in (a, b):
        o.select_set(True)
    bpy.ops.selection_sets.new(name="Lights")
    for o in bpy.context.selected_objects:
        o.select_set(False)
    c.select_set(True)
    bpy.ops.selection_sets.action(action='ADD')
    d.select_set(True)
    bpy.ops.selection_sets.action(action='SELECT_ONLY')
    assert {o.name for o in bpy.context.selected_objects} == {"A", "B", "C"}
    for o in bpy.context.selected_objects:
        o.select_set(False)
    a.select_set(True)
    bpy.ops.selection_sets.action(action='REMOVE')
    assert {o.name for o in sc.selection_sets[0].members()} == {"B", "C"}
    bpy.ops.selection_sets.action(action='HIDE')
    assert b.hide_get() and not a.hide_get()
    bpy.ops.selection_sets.action(action='SHOW')
    bpy.data.objects.remove(c)
    assert {o.name for o in sc.selection_sets[0].members()} == {"B"}
    bpy.ops.selection_sets.action(action='DELETE')
    assert not sc.selection_sets
