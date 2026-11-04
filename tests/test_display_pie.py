import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.display_pie")


def test_toggle_follows_active():
    a = bpy.data.objects.new("A", None)
    b = bpy.data.objects.new("B", None)
    for o in (a, b):
        bpy.context.scene.collection.objects.link(o)
        o.select_set(True)
    b.show_in_front = True
    bpy.context.view_layer.objects.active = a
    bpy.ops.display_pie.toggle(prop='show_in_front')
    assert a.show_in_front and b.show_in_front
    bpy.ops.display_pie.display_as(display='WIRE')
    assert a.display_type == 'WIRE'


def test_view_transform_cycle():
    sc = bpy.context.scene
    sc.view_settings.view_transform = 'AgX'
    assert m.cycle_view_transform(sc) == 'Standard'
    bpy.ops.display_pie.view_transform(name='False Color')
    assert sc.view_settings.view_transform == 'False Color'
    sc.view_settings.exposure = 2
    bpy.ops.display_pie.reset_exposure()
    assert sc.view_settings.exposure == 0
