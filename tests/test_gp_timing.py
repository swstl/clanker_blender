import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.gp_timing")


def _gp(layers=(("A", (1, 2, 3)),)):
    for g in list(bpy.data.grease_pencils):
        bpy.data.grease_pencils.remove(g)
    gp = bpy.data.grease_pencils.new("GP")
    for name, frames in layers:
        layer = gp.layers.new(name)
        for f in frames:
            layer.frames.new(f)
    gp.layers.active = gp.layers[0]
    ob = bpy.data.objects.new("GP", gp)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob, gp


def _nums(layer):
    return [f.frame_number for f in layer.frames]


def test_pure_math():
    assert m.ripple_map([1, 5, 9], 4, 3) == {5: 8, 9: 12}
    assert m.ripple_map([1, 5, 9], 4, -2) == {5: None, 9: 7}
    assert m.exposure_ripple([1, 5, 9], 1, 2) == (2, -2)
    assert m.exposure_ripple([1, 5, 9], 9, 2) is None
    assert m.retime_map([3, 4, 10], 2) == {3: 3, 4: 5, 10: 7}


def test_exposure_on_twos_selected():
    ob, gp = _gp()
    for f in gp.layers[0].frames:
        f.select = True
    assert m.set_exposure(gp, 1, 2, selected=True) == 2
    assert _nums(gp.layers[0]) == [1, 3, 5]
    assert all(f.select for f in gp.layers[0].frames)  # move keeps the frame


def test_exposure_operator_ripples_other_layers():
    ob, gp = _gp((("A", (1, 2, 3)), ("B", (1, 3, 4))))
    bpy.context.scene.frame_set(1)
    bpy.ops.gp_timing.set_exposure(length=3, all_layers=True)  # time added at the end of the hold
    assert _nums(gp.layers["A"]) == [1, 4, 5]
    assert _nums(gp.layers["B"]) == [1, 5, 6]
    bpy.ops.gp_timing.set_exposure(length=-1, relative=True, all_layers=True)
    assert _nums(gp.layers["A"]) == [1, 3, 4]
    # Not selected, not active: B stays without all_layers.
    bpy.ops.gp_timing.set_exposure(length=1)
    assert _nums(gp.layers["A"]) == [1, 2, 3]
    assert _nums(gp.layers["B"]) == [1, 4, 5]


def test_hold_insert_remove():
    ob, gp = _gp((("A", (1, 5, 9)), ("B", (1, 6))))
    bpy.context.scene.frame_set(3)
    bpy.ops.gp_timing.shift_hold(amount=2, all_layers=True)
    assert _nums(gp.layers["A"]) == [1, 7, 11]
    assert _nums(gp.layers["B"]) == [1, 8]
    bpy.ops.gp_timing.shift_hold(amount=-5, all_layers=True)  # removes (3, 8]
    assert _nums(gp.layers["A"]) == [1, 6]
    assert _nums(gp.layers["B"]) == [1]


def test_retime():
    ob, gp = _gp((("A", (1, 2, 3, 20)), ("B", (2, 10))))
    for layer in gp.layers:
        for f in layer.frames:
            f.select = f.frame_number in (1, 2, 3)
    bpy.ops.gp_timing.retime(step=3)
    assert _nums(gp.layers["A"]) == [1, 4, 7, 20]
    assert _nums(gp.layers["B"]) == [4, 10]  # synced by frame number


def test_blank_keyframe_and_ripple_end_frame():
    ob, gp = _gp((("A", (1, 10)),))
    bpy.context.scene.frame_set(20)
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.grease_pencil.insert_blank_frame(duration=2)  # native, adds end frame at 22
    assert _nums(gp.layers[0]) == [1, 10, 20, 22]
    m.ripple(gp, 15, 3, True)
    assert _nums(gp.layers[0]) == [1, 10, 23, 25]
    assert gp.layers[0].get_frame_at(25) is None  # end frame followed its drawing
    m.ripple(gp, 24, 3, True)  # only the end frame is after 24: left alone (can't move from Python)
    assert _nums(gp.layers[0]) == [1, 10, 23, 25]


def test_jump_key():
    ob, gp = _gp((("A", (1, 10)), ("B", (5,)), ("C", (7,))))
    gp.layers["C"].hide = True
    sc = bpy.context.scene
    sc.frame_set(1)
    bpy.ops.gp_timing.jump_key(next=True)
    assert sc.frame_current == 5
    bpy.ops.gp_timing.jump_key(next=True)
    assert sc.frame_current == 10
    bpy.ops.gp_timing.jump_key(next=False)
    assert sc.frame_current == 5


def test_layer_solo_restore_lock_step():
    ob, gp = _gp((("A", (1,)), ("B", (1,)), ("C", (1,))))
    gp.layers["C"].hide = True
    gp.layers.active = gp.layers["B"]
    bpy.ops.gp_timing.layer_solo()
    assert [l.hide for l in gp.layers] == [True, False, True]
    bpy.ops.gp_timing.layer_solo()
    assert [l.hide for l in gp.layers] == [False, False, True]
    bpy.ops.gp_timing.lock_others()
    assert [l.lock for l in gp.layers] == [True, False, True]
    bpy.ops.gp_timing.lock_others()
    assert not any(l.lock for l in gp.layers)
    bpy.ops.gp_timing.layer_step(direction=1)
    assert gp.layers.active.name == "C"
    bpy.ops.gp_timing.layer_activate(name="A")
    assert gp.layers.active.name == "A"


def test_exposure_props_not_remembered():
    # Buttons set length / relative / selected in different combinations.
    props = bpy.ops.gp_timing.set_exposure.get_rna_type().properties
    assert all(props[p].is_skip_save for p in ("length", "relative", "selected"))
