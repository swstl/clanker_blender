import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.view_layer_matrix")


def test_layers_from_collections_and_copy():
    sc = bpy.context.scene
    for n in ("Chars", "Env"):
        c = bpy.data.collections.new(n)
        sc.collection.children.link(c)
    sub = bpy.data.collections.new("Props")
    bpy.data.collections["Env"].children.link(sub)
    m.layers_from_collections(sc)
    chars, env = sc.view_layers["Chars"], sc.view_layers["Env"]
    assert not m.find_lc(chars, "Chars").exclude and m.find_lc(chars, "Env").exclude
    assert m.find_lc(env, "Chars").exclude and not m.find_lc(env, "Env").exclude
    m.find_lc(env, "Props").holdout = True
    bpy.ops.view_layer_matrix.copy(src="Env", dst="Chars")
    assert m.find_lc(chars, "Props").holdout and m.find_lc(chars, "Chars").exclude
    for n in ("Chars", "Env"):
        sc.view_layers.remove(sc.view_layers[n])
