import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.vse_tools")


def _setup():
    """ch1: A[1,11) B[11,21) C[21,31)   ch2: D[31,41) E[5,9)"""
    sc = bpy.context.scene
    if sc.sequence_editor:
        sc.sequence_editor_clear()
    se = sc.sequence_editor_create()
    for name, ch, start in (("A", 1, 1), ("B", 1, 11), ("C", 1, 21), ("D", 2, 31)):
        se.strips.new_effect(name=name, type='COLOR', channel=ch, frame_start=start, length=10)
    se.strips.new_effect(name="E", type='COLOR', channel=2, frame_start=5, length=4)
    for s in se.strips:
        s.select = False
    return sc, se


def _pos(se):
    return {s.name: (s.channel, s.left_handle, s.right_handle) for s in se.strips}


def test_interval_math():
    assert m.merge([(5, 10), (1, 3), (2, 6), (12, 12)]) == [(1, 10)]
    assert m.subtract([(0, 10)], [(2, 4), (8, 20)]) == [(0, 2), (4, 8)]
    assert m.removed_before(15, [(0, 2), (4, 8), (20, 30)]) == 6
    assert m.removed_before(5, [(4, 8)]) == 1


def test_ripple_delete_all_channels():
    sc, se = _setup()
    se.strips["B"].select = True
    bpy.ops.vse_tools.ripple_delete()
    p = _pos(se)
    assert "B" not in p
    assert p["C"] == (1, 11, 21) and p["D"] == (2, 21, 31) and p["A"] == (1, 1, 11)
    assert p["E"] == (2, 5, 9)


def test_ripple_delete_same_channel_and_overlap():
    sc, se = _setup()
    se.strips["A"].select = se.strips["B"].select = True
    bpy.ops.vse_tools.ripple_delete(all_channels=False)
    p = _pos(se)
    assert p["C"] == (1, 1, 11) and p["D"] == (2, 31, 41)


def test_ripple_delete_keeps_occupied_frames():
    sc, se = _setup()
    # E on ch2 covers [5,9) of A: only the free 6 frames of A's 10 get closed.
    se.strips["A"].select = True
    bpy.ops.vse_tools.ripple_delete()
    p = _pos(se)
    assert p["B"] == (1, 5, 15), p
    assert p["D"] == (2, 25, 35) and p["E"] == (2, 1, 5), p


def test_locked_strip_stays():
    sc, se = _setup()
    se.strips["D"].lock = True
    se.strips["B"].select = True
    bpy.ops.vse_tools.ripple_delete()
    assert _pos(se)["D"] == (2, 31, 41) and _pos(se)["C"] == (1, 11, 21)


def test_ripple_trim_end():
    sc, se = _setup()
    se.strips["B"].select = True
    sc.frame_current = 15
    bpy.ops.vse_tools.ripple_trim(side='END')
    p = _pos(se)
    assert p["B"] == (1, 11, 15) and p["C"] == (1, 15, 25) and p["D"] == (2, 25, 35)


def test_ripple_trim_start():
    sc, se = _setup()
    se.strips["B"].select = True
    sc.frame_current = 15
    bpy.ops.vse_tools.ripple_trim(side='START')
    p = _pos(se)
    assert p["B"] == (1, 11, 17) and p["C"] == (1, 17, 27)


def test_insert_and_remove_gap():
    sc, se = _setup()
    sc.frame_current = 21
    bpy.ops.vse_tools.gap(frames=5)
    p = _pos(se)
    assert p["C"] == (1, 26, 36) and p["D"] == (2, 36, 46) and p["B"] == (1, 11, 21)
    bpy.ops.vse_tools.gap(frames=-50)  # only the 5 empty frames can go
    assert _pos(se)["C"] == (1, 21, 31)


def test_batch_and_mute():
    sc, se = _setup()
    se.strips["A"].select = se.strips["D"].select = True
    bpy.ops.vse_tools.batch_set(use_opacity=True, opacity=0.5, use_blend=True, blend_type='ADD')
    assert se.strips["A"].blend_alpha == 0.5 and se.strips["D"].blend_type == 'ADD'
    assert se.strips["B"].blend_alpha == 1.0
    bpy.ops.vse_tools.channel_mute(mute=True)
    assert se.channels[1].mute and se.channels[2].mute and not se.channels[3].mute


def test_fps_pair():
    f, b = m.fps_pair(29.97)
    assert f == 30 and abs(f / b - 29.97) < 1e-6
