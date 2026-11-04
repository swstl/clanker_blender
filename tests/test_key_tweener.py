import importlib
import bpy
from bpy_extras import anim_utils

m = importlib.import_module("bl_ext.clanker.key_tweener")

FR = [0, 10, 20, 30, 40]
V = [0, 4, 8, 2, 10]
SEL = [False, True, True, True, False]


def close(a, b):
    assert all(abs(x - y) < 1e-4 for x, y in zip(a, b)), (a, b)


def test_pure_math():
    close(m.tween(FR, V, SEL, 0), [0, 5, 5, 5, 10])
    close(m.tween(FR, V, SEL, 0.5), [0, 7.5, 7.5, 7.5, 10])
    close(m.blend_neighbor(FR, V, SEL, 0.5), [0, 7, 9, 6, 10])
    close(m.blend_neighbor(FR, V, SEL, -1), [0, 0, 0, 0, 10])
    close(m.ease(FR, V, SEL, 0), [0, 2.5, 5, 7.5, 10])
    close(m.ease(FR, V, SEL, 1)[1:2], [10 * (1 - 0.75 ** 5)])
    close(m.push_pull(FR, V, SEL, 1), [0, 5.5, 11, -3.5, 10])
    close(m.push_pull(FR, V, SEL, -1), [0, 2.5, 5, 7.5, 10])
    avg = 14 / 3
    close(m.scale_average(FR, V, SEL, 1), [0, 8 - avg, 16 - avg, 4 - avg, 10])
    close(m.scale_average(FR, V, SEL, -1), [0, avg, avg, avg, 10])
    close(m.blend_default(FR, V, SEL, 0.5, default=1), [0, 2.5, 4.5, 1.5, 10])
    close(m.smooth(FR, V, SEL, 1), [0, 4, 3, 9, 10])
    close(m.time_offset(FR, V, SEL, 0.5), [0, 8, 2, 0, 10])
    assert m.default_value("scale", 2) == 1 and m.default_value("location", 0) == 0
    assert m.default_value('pose.bones["a"].rotation_quaternion', 0) == 1


def _keyed(path="location"):
    ob = bpy.data.objects.new("T", None)
    bpy.context.scene.collection.objects.link(ob)
    for f, v in zip(FR, V):
        getattr(ob, path)[0] = v
        ob.keyframe_insert(path, index=0, frame=f)
    ad = ob.animation_data
    fc = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves.find(path, index=0)
    for kp, s in zip(fc.keyframe_points, SEL):
        kp.select_control_point = s
        kp.interpolation = 'LINEAR'
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob, fc


def _vals(fc):
    return [kp.co.y for kp in fc.keyframe_points]


def test_operators_on_selected_keys():
    expected = {
        "tween": ({"factor": 0}, [0, 5, 5, 5, 10]),
        "blend_neighbor": ({"factor": 0.5}, [0, 7, 9, 6, 10]),
        "ease": ({"factor": 0}, [0, 2.5, 5, 7.5, 10]),
        "push_pull": ({"factor": 1}, [0, 5.5, 11, -3.5, 10]),
        "scale_average": ({"factor": -1}, [0, 14 / 3, 14 / 3, 14 / 3, 10]),
        "blend_default": ({"factor": 1}, [0, 0, 0, 0, 10]),
        "smooth": ({"factor": 1}, [0, 4, 3, 9, 10]),
        "time_offset": ({"factor": 0.5}, [0, 8, 2, 0, 10]),
    }
    for tool, (kw, want) in expected.items():
        for ob in list(bpy.data.objects):
            bpy.data.objects.remove(ob)
        ob, fc = _keyed()
        assert getattr(bpy.ops.key_tweener, tool)(mode='KEYS', **kw) == {'FINISHED'}, tool
        close(_vals(fc), want)
    bpy.context.view_layer.update()


def test_blend_default_scale():
    ob, fc = _keyed("scale")
    bpy.ops.key_tweener.blend_default(factor=1, mode='KEYS')
    close(_vals(fc), [0, 1, 1, 1, 10])


def test_current_frame_inserts_and_cancel_restores():
    ob, fc = _keyed()
    bpy.context.scene.frame_set(5)
    snap = m.Snapshot(bpy.context, 'FRAME')
    assert len(fc.keyframe_points) == 6
    snap.apply("tween", 0.5)  # between key 0 (0) and key 10 (4): 3
    close([fc.keyframe_points[1].co.y], [3])
    snap.restore(remove_inserted=True)
    close(_vals(fc), V)
    # Existing key at the playhead is used without inserting.
    bpy.context.scene.frame_set(20)
    bpy.ops.key_tweener.tween(factor=-1, mode='FRAME')
    close(_vals(fc), [0, 4, 4, 2, 10])


def test_hotkey_pref_toggles():
    prefs = bpy.context.preferences.addons["bl_ext.clanker.key_tweener"].preferences
    prefs.tween_hotkey = True
    prefs.tween_hotkey = False
    assert not m._keymaps
