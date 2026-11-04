import bpy


def _cube(name, subd=0):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if subd:
        md = ob.modifiers.new("S", 'SUBSURF')
        md.levels = subd
    return ob


def test_scan_ranks_by_evaluated_tris():
    _cube("light")
    _cube("heavy", subd=3)
    bpy.ops.heavy_finder.scan()
    items = bpy.context.scene.heavy_finder_items
    assert items[0].obj.name == "heavy"
    assert items[0].tris == 12 * 4 ** 3
    assert items[1].tris == 12


def test_tame_top_list():
    ob = _cube("heavy", subd=2)
    bpy.ops.heavy_finder.scan()
    bpy.context.scene.heavy_finder_top = 1
    bpy.ops.heavy_finder.tame(action='MODS_OFF', use_list=True)
    assert not ob.modifiers[0].show_viewport
    assert ob.modifiers[0].show_render
