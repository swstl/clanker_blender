import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.light_link_matrix")


def test_cycle_states_and_clear():
    sc = bpy.context.scene
    lt = bpy.data.objects.new("Key", bpy.data.lights.new("Key", 'AREA'))
    me = bpy.data.objects.new("Hero", bpy.data.meshes.new("Hero"))
    for o in (lt, me):
        sc.collection.objects.link(o)
    assert m.get_state(lt, me) == 'DEFAULT'
    bpy.ops.light_link_matrix.cycle(light="Key", obj="Hero", kind='LIGHT')
    assert m.get_state(lt, me) == 'INCLUDE'
    assert lt.light_linking.receiver_collection is not None
    bpy.ops.light_link_matrix.cycle(light="Key", obj="Hero", kind='LIGHT')
    assert m.get_state(lt, me) == 'EXCLUDE'
    bpy.ops.light_link_matrix.cycle(light="Key", obj="Hero", kind='LIGHT')
    assert m.get_state(lt, me) == 'DEFAULT'
    m.set_state(lt, me, 'EXCLUDE', 'SHADOW')
    assert lt.light_linking.blocker_collection.collection_objects[0].light_linking.link_state == 'EXCLUDE'
    bpy.ops.light_link_matrix.clear()
    assert lt.light_linking.receiver_collection is None and lt.light_linking.blocker_collection is None
