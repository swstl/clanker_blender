import importlib

import bpy
import numpy as np
from bpy_extras import anim_utils

M = importlib.import_module("bl_ext.clanker.onion_skin")


def _cube(name="Cube"):
    import bmesh
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _animated_cube():
    """x = frame - 1 between frames 1 and 11 (linear), plus a hold key at 20."""
    ob = _cube()
    for f, x in ((1, 0.0), (11, 10.0), (20, 10.0)):
        ob.location.x = x
        ob.keyframe_insert("location", index=0, frame=f)
    ad = ob.animation_data
    for fc in anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves:
        for k in fc.keyframe_points:
            k.interpolation = 'LINEAR'
    for o in bpy.data.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    return ob


def _settings(**kw):
    s = bpy.context.scene.onion_skin
    s.auto_update = False
    for k, v in dict(mode='STEP', before=2, after=2, step=1).items():
        setattr(s, k, kw.get(k, v))
    return s


def test_step_mode_caches_offset_frames():
    _animated_cube()
    _settings()
    sc = bpy.context.scene
    sc.frame_set(6)
    assert bpy.ops.onion_skin.calculate() == {'FINISHED'}
    assert sc.frame_current == 6
    assert sorted(M._cache) == [4, 5, 7, 8]
    assert {(f, side) for f, side, _ in M._state["frames"]} == {(5, 'PAST'), (4, 'PAST'), (7, 'FUTURE'), (8, 'FUTURE')}
    t5, t7 = M._cache[5]["tris"], M._cache[7]["tris"]
    assert t5.shape == (36, 3)
    assert np.allclose(t7 - t5, (2, 0, 0), atol=1e-4)
    assert np.isclose(t5[:, 0].mean(), 4.0, atol=1e-4)  # world space: x = frame - 1
    assert M._cache[5]["edges"].shape == (24, 3)


def test_keys_mode_picks_keyframes():
    _animated_cube()
    _settings(mode='KEYS', before=3, after=3)
    bpy.context.scene.frame_set(6)
    bpy.ops.onion_skin.calculate()
    assert sorted(M._cache) == [1, 11, 20]
    assert np.isclose(M._cache[11]["tris"][:, 0].mean(), 10.0, atol=1e-4)


def test_relative_update_reuses_cache():
    _animated_cube()
    _settings()
    sc = bpy.context.scene
    sc.frame_set(6)
    bpy.ops.onion_skin.calculate()
    entry8 = M._cache[8]
    sc.onion_skin.auto_update = True
    sc.frame_set(7)
    M._tick()  # what the throttled timer runs
    assert sorted(M._cache) == [5, 6, 8, 9]
    assert M._cache[8] is entry8  # already-cached absolute frame reused, not resampled
    assert sc.frame_current == 7
    assert np.isclose(M._cache[9]["tris"][:, 0].mean(), 8.0, atol=1e-4)


def test_armature_resolves_to_deformed_meshes():
    arm = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(arm)
    child = _cube("Child")
    child.parent = arm
    deformed = _cube("Deformed")
    deformed.modifiers.new("Arm", 'ARMATURE').object = arm
    _cube("Other")
    assert set(M.targets([arm])) == {child, deformed}


def test_clear():
    _animated_cube()
    _settings()
    bpy.ops.onion_skin.calculate()
    bpy.ops.onion_skin.clear()
    assert not M._cache and not M._state["frames"]
