import importlib
import json
import os
import shlex
import tempfile

import bpy

m = importlib.import_module("bl_ext.clanker.render_queue")


def _scene():
    s = bpy.context.scene
    s.render_queue.jobs.clear()
    return s


def _camera(name, scene):
    cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
    scene.collection.objects.link(cam)
    return cam


def test_job_creation():
    s = _scene()
    a, b = _camera("CamA", s), _camera("CamB", s)
    s.camera = a
    bpy.ops.render_queue.add()
    job = s.render_queue.jobs[0]
    assert job.scene == s and job.camera == a and job.frame_end == s.frame_end
    bpy.ops.render_queue.duplicate()
    assert len(s.render_queue.jobs) == 2 and s.render_queue.jobs[1].name == "Job 1 copy"
    bpy.ops.render_queue.move(direction='UP')
    assert s.render_queue.jobs[0].name == "Job 1 copy"
    bpy.ops.render_queue.remove()
    assert [j.name for j in s.render_queue.jobs] == ["Job 1"]

    s.render_queue.jobs.clear()
    bpy.ops.render_queue.add_cameras()
    assert [(j.name, j.camera) for j in s.render_queue.jobs] == [("CamA", a), ("CamB", b)]

    s.render_queue.jobs.clear()
    s.timeline_markers.clear()
    s.frame_start, s.frame_end = 1, 100
    s.timeline_markers.new("shot2", frame=40).camera = b
    s.timeline_markers.new("shot1", frame=1).camera = a
    s.timeline_markers.new("unbound", frame=70)
    bpy.ops.render_queue.add_markers()
    got = [(j.name, j.camera, j.frame_start, j.frame_end, j.mode) for j in s.render_queue.jobs]
    assert got == [("shot1", a, 1, 39, 'ANIMATION'), ("shot2", b, 40, 100, 'ANIMATION')], got
    s.timeline_markers.clear()


def test_token_expansion():
    s = _scene()
    s.name = "Scene"
    cam = _camera("Hero Cam", s)
    job = m.new_job(s, name="close up", camera=cam, mode='ANIMATION',
                    output="//out/{blend}/{scene}_{camera}_{job}_####")
    p = m.expand_output(job, "/proj/shot.blend")
    assert p == "/proj/out/shot/Scene_Hero_Cam_close_up_####", p
    job.mode = 'STILL'
    job.use_scene_range = False
    job.frame_start = 7
    assert m.expand_output(job, "/proj/shot.blend").endswith("close_up_0007")


def test_command_and_script():
    s = _scene()
    s.render.engine = 'CYCLES'
    cam = _camera("Cam", s)
    job = m.new_job(s, name="j", camera=cam, resolution=50, samples=16, mode='ANIMATION',
                    use_scene_range=False, frame_start=5, frame_end=9, view_layer="ViewLayer",
                    output="/tmp/x/{job}_")
    cmd = m.job_command(job, "/proj/a.blend", "/tmp/copy.blend")
    assert cmd[:2] == [bpy.app.binary_path, "-b"] and "/tmp/copy.blend" in cmd
    assert cmd[cmd.index("-S") + 1] == s.name
    py = cmd[-1]
    for frag in ("s.render.filepath = '/tmp/x/j_'", "s.camera = bpy.data.objects['Cam']",
                 "resolution_percentage = 50", "s.cycles.samples = 16",
                 "s.frame_start, s.frame_end = 5, 9", "render(animation=True)", "== 'ViewLayer'"):
        assert frag in py, frag
    compile(py, "<job>", "exec")  # one-liner must be valid python

    sh = m.shell_script(s, "/proj/a.blend", windows=False)
    assert sh.startswith("#!/bin/sh") and shlex.quote(py) in sh and "/proj/a.blend" in sh
    bat = m.shell_script(s, "/proj/a.blend", windows=True)
    assert bat.startswith("@echo off") and "--python-expr" in bat


def test_presets():
    s = _scene()
    bpy.ops.render_queue.apply_preset(preset="Draft")
    assert s.render.resolution_percentage == 50 and s.render.use_simplify
    assert s.cycles.samples == 32 and s.eevee.taa_render_samples == 16
    bpy.ops.render_queue.apply_preset(preset="Final")
    assert s.render.resolution_percentage == 100 and not s.render.use_simplify
    assert s.cycles.samples == 1024

    s.render.resolution_percentage = 33
    bpy.ops.render_queue.save_preset(name="Mine")
    assert json.loads(m._prefs().presets_json)["Mine"]["resolution"] == 33
    bpy.ops.render_queue.apply_preset(preset="Draft")
    bpy.ops.render_queue.apply_preset(preset="Mine")
    assert s.render.resolution_percentage == 33
    bpy.ops.render_queue.delete_preset(preset="Mine")
    assert "Mine" not in m.user_presets()


def test_end_to_end_render():
    s = _scene()
    s.render.engine = 'BLENDER_WORKBENCH'
    s.render.resolution_x = s.render.resolution_y = 32
    s.render.resolution_percentage = 100
    s.camera = _camera("Cam", s)
    out = tempfile.mkdtemp(prefix="rq_test_")
    m.new_job(s, name="tiny", output=os.path.join(out, "{job}_####"))
    m.new_job(s, name="off", enabled=False, output=os.path.join(out, "off"))
    old = list(m.EXTRA_ARGS)
    m.EXTRA_ARGS[:] = ["--factory-startup"]
    try:
        jobs = m.run_queue(s)
    finally:
        m.EXTRA_ARGS[:] = old
    assert [j.name for j in jobs] == ["tiny"]
    png = os.path.join(out, f"tiny_{s.frame_current:04d}.png")
    assert jobs[0].status == 'DONE', open(jobs[0].log).read()[-2000:]
    assert os.path.isfile(png), os.listdir(out)
    assert not os.path.exists(os.path.join(out, "off.png"))
