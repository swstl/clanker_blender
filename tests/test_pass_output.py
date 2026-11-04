import glob
import importlib
import os
import tempfile

import bpy

po = importlib.import_module("bl_ext.clanker.pass_output")


def _setup():
    sc = bpy.context.scene
    sc.compositing_node_group = None
    vl = bpy.context.view_layer
    for p in ("use_pass_z", "use_pass_normal", "use_pass_mist"):
        setattr(vl, p, True)
    vl.use_pass_emit = False
    vl.cycles.denoising_store_passes = False
    sc.render.engine = 'BLENDER_EEVEE'
    sc.pass_output.denoise = False
    sc.pass_output.property_unset("path")
    return sc, vl


def _fo(sc, vl):
    return sc.compositing_node_group.nodes["PassOutput_" + vl.name]


def _links(sc, fo):
    """{item name: source socket name} for linked File Output inputs."""
    return {inp.name: inp.links[0].from_socket.name for inp in fo.inputs if inp.is_linked}


def test_multilayer_and_update():
    sc, vl = _setup()
    sc.pass_output.mode = 'MULTILAYER'
    sc.pass_output.color_depth = '32'
    assert bpy.ops.pass_output.wire() == {'FINISHED'}
    tree = sc.compositing_node_group
    assert tree is not None
    # new tree keeps the standard Render Layers -> Group Output link
    go = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
    assert go.inputs["Image"].links[0].from_node.bl_idname == "CompositorNodeRLayers"
    fo = _fo(sc, vl)
    assert fo.format.file_format == 'OPEN_EXR_MULTILAYER' and fo.format.color_depth == '32'
    names = {it.name for it in fo.file_output_items}
    assert names == {"Image", "Depth", "Mist", "Normal"}, names
    assert _links(sc, fo) == {n: n for n in names}
    assert fo.directory == "//renders/Scene/ViewLayer/"

    assert bpy.ops.pass_output.wire() == {'FINISHED'}
    assert len([n for n in tree.nodes if n.bl_idname == "CompositorNodeOutputFile"]) == 1
    assert len([n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers"]) == 1
    assert len(fo.file_output_items) == 4

    vl.use_pass_mist = False
    vl.use_pass_emit = True
    bpy.ops.pass_output.wire()
    names = {it.name for it in fo.file_output_items}
    assert names == {"Image", "Depth", "Normal", "Emission"}, names
    assert set(_links(sc, fo)) == names


def test_separate_files():
    sc, vl = _setup()
    sc.pass_output.mode = 'SEPARATE'
    sc.pass_output.file_format = 'PNG'
    bpy.ops.pass_output.wire()
    fo = _fo(sc, vl)
    assert fo.format.file_format == 'PNG'
    assert {it.name for it in fo.file_output_items} == {"Image/Image_", "Depth/Depth_", "Mist/Mist_",
                                                        "Normal/Normal_"}
    assert _links(sc, fo)["Depth/Depth_"] == "Depth"
    # switching back to multilayer renames items instead of piling up
    sc.pass_output.mode = 'MULTILAYER'
    bpy.ops.pass_output.wire()
    assert {it.name for it in fo.file_output_items} == {"Image", "Depth", "Mist", "Normal"}


def test_clean_and_path():
    assert po.clean("Crypto Object/00{x}") == "Crypto_Object_00_x"
    sc, vl = _setup()
    assert po.fill_path("//r/{scene}/{viewlayer}/", sc, vl) == "//r/Scene/ViewLayer/"


def test_denoise_and_render():
    sc, vl = _setup()
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 1
    sc.cycles.use_denoising = False
    sc.render.resolution_x = sc.render.resolution_y = 8
    vl.cycles.denoising_store_passes = True
    st = sc.pass_output
    st.mode, st.denoise = 'MULTILAYER', True
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    st.path = os.path.join(tempfile.mkdtemp(), "{scene}", "{viewlayer}", "")
    bpy.ops.pass_output.wire()
    tree = sc.compositing_node_group
    dn = tree.nodes["PassDenoise_" + vl.name]
    assert dn.inputs["Albedo"].is_linked and dn.inputs["Normal"].is_linked
    fo = _fo(sc, vl)
    img = next(i for i in fo.inputs if i.name == "Image")
    assert img.links[0].from_node == dn
    bpy.ops.render.render()
    files = glob.glob(os.path.join(fo.directory, "*.exr"))
    assert files, os.listdir(fo.directory)
    st.denoise = False
    bpy.ops.pass_output.wire()
    assert "PassDenoise_" + vl.name not in tree.nodes
