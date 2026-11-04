import importlib

import bpy

M = importlib.import_module("bl_ext.clanker.text_animator")


def _text(body):
    sc = bpy.context.scene
    sc.render.fps, sc.render.fps_base = 24, 1
    cu = bpy.data.curves.new("T", 'FONT')
    cu.body = body
    ob = bpy.data.objects.new("T", cu)
    sc.collection.objects.link(ob)
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = ob
    return sc, ob, cu


def test_typewriter_chars():
    sc, ob, cu = _text("Hello world")
    s = cu.text_animator
    s.start, s.speed = 1, 10
    s.typewriter = True  # captures body as source
    assert s.source == "Hello world"
    sc.frame_set(13)  # 0.5 s * 10 cps
    assert cu.body == "Hello", cu.body
    sc.frame_set(1)
    assert cu.body == ""
    sc.frame_set(100)
    assert cu.body == "Hello world"
    s.typewriter = False
    assert cu.body == "Hello world"


def test_typewriter_words_and_cursor():
    f = M.typewriter_text
    assert f("one two  three", 25, 1, 24, 2, 'WORDS') == "one two"
    assert f("a\nbb\nccc", 25, 1, 24, 2, 'LINES') == "a\nbb"
    # blink 1/s: visible in first half second, hidden in second half
    assert f("abc", 1, 1, 24, 0, 'CHARS', "|", 1) == "|"
    assert f("abc", 13, 1, 24, 0, 'CHARS', "|", 1) == ""
    assert f("abc", 25, 1, 24, 0, 'CHARS', "_", 1) == "_"


def test_counter_format_and_easing():
    assert M.format_number(1234567.891, 2, True, "$") == "$1,234,567.89"
    assert M.format_number(42.4, 0, False, suffix="%") == "42%"
    sc, ob, cu = _text("0")
    s = cu.text_animator
    s.a, s.b, s.frame_a, s.frame_b = 0, 1000, 1, 21
    s.interpolation, s.easing = 'CUBIC', 'EASE_IN'
    with bpy.context.temp_override(object=ob):
        bpy.ops.text_animator.counter_keys()
    sc.frame_set(11)
    assert cu.body == "125", cu.body  # cubic ease-in at midpoint = 1/8
    s.easing = 'EASE_IN_OUT'
    with bpy.context.temp_override(object=ob):
        bpy.ops.text_animator.counter_keys()
    sc.frame_set(12)
    sc.frame_set(11)
    assert cu.body == "500", cu.body
    sc.frame_set(21)
    assert cu.body == "1,000"


def test_split_letters():
    sc, ob, cu = _text("Hi you")
    ob.location.x = 3
    with bpy.context.temp_override(object=ob, active_object=ob):
        assert bpy.ops.text_animator.split(mode='LETTERS') == {'FINISHED'}
    empty = next(o for o in sc.objects if "ta_split" in o)
    kids = M.split_children(empty)
    assert [k.data.body for k in kids] == list("Hiyou")
    xs = [k.location.x for k in kids]
    assert xs == sorted(xs) and len(set(xs)) == 5, xs
    assert xs[0] < 0.1 and xs[2] > 0.5  # space was skipped but kept its gap
    assert abs(empty.location.x - 3) < 1e-6
    assert ob.hide_render


def test_split_words_lines_and_stagger():
    sc, ob, cu = _text("ab cd\nef")
    assert len(M.split_text(ob, 'WORDS')) == 3
    lines = M.split_text(ob, 'LINES')
    assert lines[1].location.y < lines[0].location.y
    letters = M.split_text(ob, 'LETTERS')
    with bpy.context.temp_override(object=letters[0]):
        bpy.ops.text_animator.stagger(effects={'SCALE', 'DROP'}, start=5, delay=3, duration=10)
    for k, o in enumerate(letters):
        frames = sorted({kp.co.x for fc in M.anim_utils.action_get_channelbag_for_slot(
            o.animation_data.action, o.animation_data.action_slot).fcurves for kp in fc.keyframe_points})
        assert frames == [5 + 3 * k, 15 + 3 * k], frames
    sc.frame_set(5)
    assert letters[0].scale.x == 0 and letters[1].scale.x == 0
    sc.frame_set(100)
    assert abs(letters[-1].scale.x - 1) < 1e-6
    # re-run at a frame where everything is scaled to 0: rest pose must survive
    sc.frame_set(1)
    M.stagger(letters, 1, 1, 5, {'SCALE'})
    sc.frame_set(50)
    assert abs(letters[0].scale.x - 1) < 1e-6
