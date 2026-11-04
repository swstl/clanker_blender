import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.stepped_preview")


def _ob():
    ob = bpy.data.objects.new("S", None)
    bpy.context.scene.collection.objects.link(ob)
    for f, v in ((1, 0), (11, 10)):
        ob.location.x = v
        ob.keyframe_insert("location", index=0, frame=f)
    ob.select_set(True)
    kp = next(m._fcurves(ob.animation_data.action)).keyframe_points
    kp[1].interpolation = 'LINEAR'
    return ob, kp


def _x(ob, f):
    bpy.context.scene.frame_set(f)
    return ob.matrix_world.translation.x


def test_hold_roundtrip():
    ob, kp = _ob()
    smooth = _x(ob, 6)
    bpy.ops.stepped_preview.toggle(mode='HOLD')
    assert _x(ob, 6) == 0
    bpy.ops.stepped_preview.toggle()
    assert [k.interpolation for k in kp] == ['BEZIER', 'LINEAR']
    assert abs(_x(ob, 6) - smooth) < 1e-5


def test_steps_modifier_roundtrip():
    ob, kp = _ob()
    bpy.ops.stepped_preview.toggle(mode='STEPS', step=4)
    fc = next(m._fcurves(ob.animation_data.action))
    assert fc.modifiers[0].name == "StepPreview" and fc.modifiers[0].frame_step == 4
    bpy.context.scene.frame_start = 1
    assert _x(ob, 4) == _x(ob, 1) and _x(ob, 5) != _x(ob, 4)
    bpy.ops.stepped_preview.toggle()
    assert not fc.modifiers
