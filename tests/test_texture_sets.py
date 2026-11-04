import importlib
import os
import tempfile

import bpy

ts = importlib.import_module("bl_ext.clanker.texture_sets")


def _write(d, *names):
    for n in names:
        img = bpy.data.images.new("tmp", 4, 4)
        img.filepath_raw = os.path.join(d, n)
        img.file_format = 'JPEG' if n.endswith(".jpg") else 'PNG'
        img.save()
        bpy.data.images.remove(img)


def _src(sock):
    return sock.links[0].from_node if sock.is_linked else None


def test_parse_names():
    p = ts.parse_name
    assert p("Wood_BaseColor.png") == ("Wood", 'color', 0)
    assert p("Metal_basecolor.jpg") == ("Metal", 'color', 0)
    assert p("wood_table_nor_gl_2k.exr") == ("wood_table", 'normal', 2)
    assert p("rock_nor_dx_4k.png") == ("rock", 'normal_dx', 4)
    assert p("Wood049_2K-JPG_Color.jpg") == ("Wood049", 'color', 2)
    assert p("T_Brick_Base_Color.png") == ("T_Brick", 'color', 0)
    assert p("bark_arm_1k.jpg") == ("bark", 'orm', 1)
    assert p("readme.png") is None


def test_resolution_preference():
    files = ["/x/A_Roughness_2K.png", "/x/A_Roughness_4K.png", "/x/A_Roughness_4K.exr"]
    assert ts.group_files(files)["A"]["roughness"] == "/x/A_Roughness_4K.exr"
    assert ts.group_files(files, '2K')["A"]["roughness"] == "/x/A_Roughness_2K.png"
    assert ts.group_files(files, 'LOWEST')["A"]["roughness"] == "/x/A_Roughness_2K.png"


def test_load_two_sets():
    d = tempfile.mkdtemp()
    _write(d, "Wood_BaseColor.png", "Wood_Roughness.png", "Wood_Normal.png", "Metal_basecolor.jpg",
           "Metal_ORM.png", "Metal_Normal_DX.png")
    open(os.path.join(d, "notes.txt"), "w").close()
    me = bpy.data.meshes.new("m")
    ob = bpy.data.objects.new("ob", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob

    assert bpy.ops.texture_sets.load(directory=d, box_projection=True) == {'FINISHED'}
    wood, metal = bpy.data.materials["Wood"], bpy.data.materials["Metal"]
    assert len(bpy.data.materials) == 2
    assert [s.material for s in ob.material_slots] == [metal, wood]

    bsdf = wood.node_tree.nodes["Principled BSDF"]
    base = _src(bsdf.inputs["Base Color"])
    assert base.type == 'TEX_IMAGE' and base.projection == 'BOX'
    assert base.image.colorspace_settings.name == 'sRGB'
    rough = _src(bsdf.inputs["Roughness"])
    assert rough.image.colorspace_settings.name == 'Non-Color'
    nmap = _src(bsdf.inputs["Normal"])
    assert nmap.type == 'NORMAL_MAP'
    assert _src(nmap.inputs["Color"]).image.colorspace_settings.name == 'Non-Color'

    bsdf = metal.node_tree.nodes["Principled BSDF"]
    lm = bsdf.inputs["Metallic"].links[0]
    assert lm.from_node.type == 'SEPARATE_COLOR' and lm.from_socket.name == "Blue"
    assert bsdf.inputs["Roughness"].links[0].from_socket.name == "Green"
    orm = _src(lm.from_node.inputs["Color"])
    assert orm.image.colorspace_settings.name == 'Non-Color'
    # DirectX normal goes through Combine Color (green flipped)
    assert _src(_src(bsdf.inputs["Normal"]).inputs["Color"]).type == 'COMBINE_COLOR'
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)


def test_ao_height_asset():
    d = tempfile.mkdtemp()
    _write(d, "rock_diff_1k.png", "rock_ao_1k.png", "rock_disp_1k.png")
    assert bpy.ops.texture_sets.load(directory=d, ao_multiply=True, displacement='BOTH', mark_asset=True,
                                     assign='NONE') == {'FINISHED'}
    m = bpy.data.materials["rock"]
    assert m.asset_data and m.displacement_method == 'BOTH'
    assert _src(m.node_tree.nodes["Principled BSDF"].inputs["Base Color"]).type == 'MIX'
    assert _src(m.node_tree.nodes["Material Output"].inputs["Displacement"]).type == 'DISPLACEMENT'
