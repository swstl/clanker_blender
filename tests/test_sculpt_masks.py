import importlib

import bmesh
import bpy
import numpy as np

import drawcheck

m = importlib.import_module("bl_ext.clanker.sculpt_masks")


def _grid(n=4):
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=n, y_segments=n, size=1)
    me = bpy.data.meshes.new("G")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("G", me)
    bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob


def _ramp(me):
    n = len(me.vertices)
    return np.linspace(0, 1, n, dtype=np.float32)


def test_mask_to_vgroup_and_back():
    ob = _grid()
    me = ob.data
    ramp = _ramp(me)
    m.set_mask(me, ramp)
    assert bpy.ops.sculpt_masks.mask_to_vgroup() == {'FINISHED'}
    assert ob.vertex_groups.active.name == "Mask"
    assert np.allclose(m.vg_weights(ob, "Mask"), ramp)
    # Overwrite existing group by name.
    m.set_mask(me, 1 - ramp)
    bpy.ops.sculpt_masks.mask_to_vgroup(group="Mask")
    assert len(ob.vertex_groups) == 1
    assert np.allclose(m.vg_weights(ob, "Mask"), 1 - ramp)

    m.set_mask(me, np.full(len(ramp), 0.5))
    w = m.vg_weights(ob, "Mask")
    expect = {'REPLACE': w, 'ADD': np.clip(0.5 + w, 0, 1),
              'SUBTRACT': np.clip(0.5 - w, 0, 1), 'MULTIPLY': 0.5 * w}
    for mode, exp in expect.items():
        m.set_mask(me, np.full(len(ramp), 0.5))
        bpy.ops.sculpt_masks.vgroup_to_mask(group="Mask", mode=mode)
        assert np.allclose(m.get_mask(me), exp, atol=1e-6), mode


def test_face_sets_and_vgroups():
    ob = _grid(2)  # 4 faces
    me = ob.data
    m.set_face_sets(me, [1, 1, 2, 3])
    bpy.ops.sculpt_masks.faceset_to_vgroups()
    assert {g.name for g in ob.vertex_groups} == {"FaceSet 1", "FaceSet 2", "FaceSet 3"}
    cv, cface, _ = m.corners(me)
    for fid in (1, 2, 3):
        faces = [i for i, f in enumerate([1, 1, 2, 3]) if f == fid]
        verts = set(cv[np.isin(cface, faces)].tolist())
        w = m.vg_weights(ob, f"FaceSet {fid}")
        assert set(np.flatnonzero(w).tolist()) == verts, fid
    # Single face set id only.
    bpy.ops.sculpt_masks.faceset_to_vgroups(face_set=2)

    # Group -> face set: only faces fully inside get the new id.
    vg = ob.vertex_groups["FaceSet 3"]
    ob.vertex_groups.active_index = vg.index
    bpy.ops.sculpt_masks.vgroup_to_faceset()
    assert m.get_face_sets(me).tolist() == [1, 1, 2, 4]


def test_vgroup_to_faceset_without_attribute():
    ob = _grid(2)
    vg = ob.vertex_groups.new(name="G")
    vg.add([v.index for v in ob.data.vertices if v.co.x < 0.1], 1.0, 'REPLACE')
    bpy.ops.sculpt_masks.vgroup_to_faceset(group="G")
    fs = m.get_face_sets(ob.data)
    assert sorted(set(fs.tolist())) == [1, 2] and (fs == 2).sum() == 2, fs


def test_mask_from_color():
    ob = _grid(2)
    me = ob.data
    ca = me.color_attributes.new("Col", 'FLOAT_COLOR', 'POINT')
    me.color_attributes.active_color = ca
    for i, d in enumerate(ca.data):
        d.color_srgb = (i % 2, 0.0, 1.0, 1.0)
    bpy.ops.sculpt_masks.mask_from_color(channel='R')
    assert np.allclose(m.get_mask(me), [i % 2 for i in range(len(me.vertices))], atol=1e-3)
    bpy.ops.sculpt_masks.mask_from_color(channel='LUMINANCE')
    assert np.allclose(m.get_mask(me)[1], 0.2126 + 0.0722, atol=1e-3)


def test_stored_masks():
    ob = _grid()
    me = ob.data
    ramp = _ramp(me)
    m.set_mask(me, ramp)
    bpy.ops.sculpt_masks.store(name="A")
    assert (m.PREFIX + "A") in me.attributes
    m.set_mask(me, np.full(len(ramp), 0.5))
    for mode, exp in (('ADD', np.clip(ramp + 0.5, 0, 1)), ('SUBTRACT', np.clip(0.5 - ramp, 0, 1)),
                      ('INTERSECT', np.minimum(ramp, 0.5)), ('REPLACE', ramp)):
        m.set_mask(me, np.full(len(ramp), 0.5))
        bpy.ops.sculpt_masks.restore(name="A", mode=mode)
        assert np.allclose(m.get_mask(me), exp, atol=1e-6), mode
    # Active list item is used when no name is given.
    m.set_mask(me, np.zeros(len(ramp)))
    bpy.ops.sculpt_masks.restore()
    assert np.allclose(m.get_mask(me), ramp)
    bpy.ops.sculpt_masks.delete()
    assert (m.PREFIX + "A") not in me.attributes
    assert bpy.ops.sculpt_masks.restore.poll()


def test_in_sculpt_mode():
    ob = _grid()
    me = ob.data
    ramp = _ramp(me)
    m.set_mask(me, ramp)
    bpy.ops.object.mode_set(mode='SCULPT')
    assert ob.mode == 'SCULPT'
    bpy.ops.sculpt_masks.mask_to_vgroup()
    assert ob.mode == 'SCULPT'
    assert np.allclose(m.vg_weights(ob, "Mask"), ramp)
    bpy.ops.sculpt_masks.store(name="B")
    bpy.ops.sculpt_masks.vgroup_to_mask(group="Mask", mode='MULTIPLY')
    assert ob.mode == 'SCULPT'
    bpy.ops.object.mode_set(mode='OBJECT')
    assert np.allclose(m.get_mask(me), ramp * ramp, atol=1e-6)
    # Panel draws in sculpt mode (the generic draw check skips it: poll needs sculpt mode).
    m.SM_PT_panel.draw(drawcheck._Self(m.SM_PT_panel), drawcheck.Ctx(mode='SCULPT'))


def test_multires_skipped():
    ob = _grid()
    md = ob.modifiers.new("Multires", 'MULTIRES')
    with bpy.context.temp_override(object=ob, active_object=ob):
        bpy.ops.object.multires_subdivide(modifier="Multires")
    assert md.total_levels > 0
    assert bpy.ops.sculpt_masks.mask_to_vgroup() == {'CANCELLED'}
    assert len(ob.vertex_groups) == 0
