import importlib
import os
import tempfile

import bpy

m = importlib.import_module("bl_ext.clanker.render_preflight")


def _status(name, scene=None, farm=True):
    rows = [r for r in m.run_checks(scene or bpy.context.scene, farm) if r[1] == name]
    assert rows, f"no row {name}"
    return rows[0][0]


def _png(path):
    img = bpy.data.images.new("tmp", 4, 4)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)


def test_unsaved_scene():
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    assert _status("File") == 'FAIL'
    assert _status("Camera") == 'FAIL'
    sc.render.filepath = ""
    assert _status("Output Path") == 'WARN'
    sc.render.filepath = "/tmp/"
    assert _status("Output Path") == 'WARN'
    bpy.ops.render_preflight.fix(what="output")
    assert sc.render.filepath.startswith("//renders/") and _status("Output Path") == 'OK'
    sc.render.resolution_percentage = 50
    assert _status("Resolution") == 'WARN'
    bpy.ops.render_preflight.fix(what="res100")
    assert _status("Resolution") == 'OK'
    sc.frame_step = 2
    assert _status("Frame Range") == 'WARN'
    sc.frame_step = 1
    sc.frame_end = sc.frame_start + 10
    sc.render.image_settings.media_type = 'VIDEO'
    sc.render.image_settings.file_format = 'FFMPEG'
    assert _status("File Format") == 'WARN' and _status("File Format", farm=False) == 'OK'
    bpy.ops.render_preflight.fix(what="png")
    assert sc.render.image_settings.file_format == 'PNG' and _status("File Format") == 'OK'
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    sc.collection.objects.link(cam)
    sc.timeline_markers.new("late", frame=sc.frame_end + 50).camera = cam
    # the earliest marker camera is used even before its frame, but the switch is outside the range
    assert _status("Camera") == 'OK' and _status("Camera Markers") == 'WARN'
    text = m.report_text()
    assert "[OK  ] Camera: Cam" in text and "Render Preflight: (unsaved)" in text, text


def test_paths_missing_and_make_relative():
    outside, proj = tempfile.mkdtemp(), tempfile.mkdtemp()
    _png(os.path.join(outside, "wood.png"))
    bpy.data.images.load(os.path.join(outside, "wood.png")).use_fake_user = True
    gone = bpy.data.images.load(os.path.join(outside, "wood.png"))
    gone.use_fake_user = True
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "p.blend"))  # remaps paths relative
    for img in bpy.data.images:
        img.filepath = os.path.join(outside, "wood.png")
    gone.filepath = os.path.join(outside, "missing.png")
    try:
        assert _status("File") != 'FAIL'  # is_dirty stays True after saving in --background
        assert _status("Absolute Paths") == 'WARN'
        assert _status("Missing Files") == 'FAIL'
        bpy.ops.render_preflight.fix(what="relative")
        assert _status("Absolute Paths") == 'OK', m.report_text()
        bpy.data.images.remove(gone)
        assert _status("Missing Files") == 'OK'
        sc = bpy.context.scene
        sc.render.filepath = os.path.join(outside, "frame_")
        assert _status("Output Path") == 'WARN'
        sc.render.filepath = os.path.join(proj, "renders", "frame_")
        assert _status("Output Path") == 'OK'
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)


def test_cloth_and_python_driver():
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    me = bpy.data.meshes.new("m")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    ob = bpy.data.objects.new("Cloth", me)
    sc.collection.objects.link(ob)
    assert _status("Simulations") == 'OK'
    ob.modifiers.new("Cloth", 'CLOTH')
    assert _status("Simulations") == 'WARN'
    names = {r[1] for r in m.run_checks(sc, True)}
    assert "Python Drivers" not in names
    fc = ob.driver_add("location", 2)
    fc.driver.type = 'SCRIPTED'
    fc.driver.expression = "frame * 2"  # simple expression: no Python needed
    assert "Python Drivers" not in {r[1] for r in m.run_checks(sc, True)}
    fc.driver.expression = "len('ab') * frame"
    assert _status("Python Drivers") == 'WARN'


def test_render_handler_and_copy():
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    m._results.clear()
    m._on_render_init(sc)
    assert not m._results  # option off by default
    sc.render_preflight_on_render = True
    m._on_render_init(sc)
    assert any(r[0] == 'FAIL' for r in m._results)
    assert bpy.ops.render_preflight.copy() == {'FINISHED'}
    assert m.report_text().count("\n") == len(m._results) + 1


def test_engine_compositor_and_gn_bake():
    bpy.ops.wm.read_homefile(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    sc.cycles.samples = 20000
    assert _status("Cycles Samples") == 'WARN'
    sc.render.engine = 'BLENDER_EEVEE'
    assert _status("Engine") == 'OK'
    tree = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
    sc.compositing_node_group = tree
    tree.nodes.new("CompositorNodeOutputFile").directory = "/abs/out/"
    assert _status("Compositor Output") == 'WARN'

    proj = tempfile.mkdtemp()
    ob = bpy.data.objects.new("GN", bpy.data.meshes.new("GN"))
    sc.collection.objects.link(ob)
    ng = bpy.data.node_groups.new("G", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    i, o, b = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput"), ng.nodes.new("GeometryNodeBake")
    ng.links.new(i.outputs[0], b.inputs[0])
    ng.links.new(b.outputs[0], o.inputs[0])
    md = ob.modifiers.new("GN", 'NODES')
    md.node_group = ng
    md.bake_target = 'DISK'
    md.bake_directory = "//cache"
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(proj, "g.blend"))
    try:
        assert _status("Simulations") == 'WARN'
        with bpy.context.temp_override(object=ob, active_object=ob):
            bpy.ops.object.geometry_node_bake_single(session_uid=ob.session_uid, modifier_name=md.name,
                                                    bake_id=md.bakes[0].bake_id)
        assert _status("Simulations") == 'OK', m.report_text()
    finally:
        bpy.ops.wm.read_homefile(use_empty=True)
