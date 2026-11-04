import bpy


def _obs(n=10):
    me = bpy.data.meshes.new("M")
    me.materials.append(bpy.data.materials.new("Brick_A"))
    bpy.data.materials.new("Brick_B")
    bpy.data.materials.new("Brick_C")
    out = []
    for i in range(n):
        ob = bpy.data.objects.new(f"O{i:02d}", me)
        bpy.context.scene.collection.objects.link(ob)
        ob.select_set(True)
        out.append(ob)
    return out


def test_materials_per_object_and_deterministic():
    obs = _obs()
    s = bpy.context.scene.randomizer
    s.mat_filter, s.seed = "brick", 3
    bpy.ops.randomizer.apply(what='MATERIAL')
    first = [o.material_slots[0].material.name for o in obs]
    assert len(set(first)) > 1
    assert all(o.material_slots[0].link == 'OBJECT' for o in obs)
    assert obs[0].data.materials[0].name == "Brick_A"  # shared mesh untouched
    bpy.ops.randomizer.apply(what='MATERIAL')
    assert [o.material_slots[0].material.name for o in obs] == first


def test_colors_scale_thin():
    obs = _obs()
    s = bpy.context.scene.randomizer
    bpy.ops.randomizer.apply(what='COLOR')
    assert len({tuple(round(c, 3) for c in o.color) for o in obs}) > 5
    s.scale_min, s.scale_max = 2, 3
    bpy.ops.randomizer.apply(what='SCALE')
    assert all(2 <= o.scale.x <= 3 and o.scale.x == o.scale.z for o in obs)
    s.keep_ratio = 0.5
    bpy.ops.randomizer.apply(what='THIN')
    kept = sum(o.select_get() for o in obs)
    assert 0 < kept < 10
