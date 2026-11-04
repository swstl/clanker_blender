"""Render Queue: a list of render jobs (camera / range / overrides) run one by one in
background Blender processes, plus one-click quality presets."""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper

WORK_DIR = os.path.join(tempfile.gettempdir(), "blender_render_queue")
EXTRA_ARGS = []  # extra CLI args for the subprocesses (tests add --factory-startup)
_state = {"proc": None, "cancel": False, "done": 0, "total": 0}

PRESETS = {
    "Draft": dict(resolution=50, cycles_samples=32, eevee_samples=16, denoise=True,
                  simplify=True, simplify_subdivision=1),
    "Preview": dict(resolution=75, cycles_samples=128, eevee_samples=32, denoise=True,
                    simplify=False, simplify_subdivision=6),
    "Final": dict(resolution=100, cycles_samples=1024, eevee_samples=128, denoise=True,
                  simplify=False, simplify_subdivision=6),
}
STATUS_ICONS = {"IDLE": "BLANK1", "QUEUED": "TIME", "RUNNING": "RENDER_ANIMATION",
                "DONE": "CHECKMARK", "FAILED": "ERROR", "CANCELLED": "CANCEL"}


# ---------------------------------------------------------------- presets

def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def user_presets():
    try:
        return json.loads(_prefs().presets_json or "{}")
    except ValueError:
        return {}


def all_presets():
    return {**PRESETS, **user_presets()}


def read_preset(scene):
    r, cy = scene.render, getattr(scene, "cycles", None)
    return dict(resolution=r.resolution_percentage,
                cycles_samples=cy.samples if cy else 128,
                eevee_samples=scene.eevee.taa_render_samples,
                denoise=cy.use_denoising if cy else True,
                simplify=r.use_simplify, simplify_subdivision=r.simplify_subdivision_render)


def apply_preset(scene, v):
    r, cy = scene.render, getattr(scene, "cycles", None)
    r.resolution_percentage = v["resolution"]
    r.use_simplify = v["simplify"]
    r.simplify_subdivision_render = v["simplify_subdivision"]
    scene.eevee.taa_render_samples = v["eevee_samples"]
    if cy:  # Cycles add-on can be disabled
        cy.samples = v["cycles_samples"]
        cy.use_denoising = v["denoise"]


# ---------------------------------------------------------------- jobs

def _poll_camera(self, obj):
    return obj.type == 'CAMERA'


class RQ_Job(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Enabled", default=True)
    name: StringProperty(name="Name", default="Job")
    scene: PointerProperty(name="Scene", type=bpy.types.Scene)
    camera: PointerProperty(name="Camera", type=bpy.types.Object, poll=_poll_camera,
                            description="Camera to render from. Empty = scene camera")
    view_layer: StringProperty(name="View Layer",
                               description="Render only this view layer. Empty = all enabled ones")
    mode: EnumProperty(name="Mode", items=[("STILL", "Still", "Render one frame"),
                                           ("ANIMATION", "Animation", "Render a frame range")])
    use_scene_range: BoolProperty(name="Use Scene Frames", default=True,
                                  description="Use the scene's frame range (or current frame for stills)")
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=250)
    resolution: IntProperty(name="Resolution %", default=0, min=0, max=10000,
                            description="Resolution percentage override. 0 = scene setting")
    samples: IntProperty(name="Samples", default=0, min=0,
                         description="Render samples override (Cycles/EEVEE). 0 = scene setting")
    output: StringProperty(name="Output", subtype='FILE_PATH', default="//render/{blend}/{job}_",
                           options={'PATH_SUPPORTS_BLEND_RELATIVE'},
                           description="Output path. Tokens: {blend} {scene} {camera} {job}; # = frame number")
    status: EnumProperty(name="Status", items=[(k, k.title(), "") for k in STATUS_ICONS], default="IDLE")
    log: StringProperty(name="Log", subtype='FILE_PATH')


class RQ_Queue(bpy.types.PropertyGroup):
    jobs: CollectionProperty(type=RQ_Job)
    index: IntProperty()


def new_job(scene, **kw):
    """Add a job with the scene's current settings; kw overrides any job property."""
    q = scene.render_queue
    job = q.jobs.add()
    job.name = f"Job {len(q.jobs)}"
    job.scene = scene
    job.camera = scene.camera
    job.frame_start, job.frame_end = scene.frame_start, scene.frame_end
    for k, v in kw.items():
        setattr(job, k, v)
    q.index = len(q.jobs) - 1
    return job


def copy_job(src, dst):
    for p in src.bl_rna.properties:
        if p.identifier != "rna_type" and not p.is_readonly:
            setattr(dst, p.identifier, getattr(src, p.identifier))


def _stem(blend_path):
    return os.path.splitext(os.path.basename(blend_path))[0] or "untitled"


def _still_frame(job):
    scene = job.scene or bpy.context.scene
    return scene.frame_current if job.use_scene_range else job.frame_start


def expand_output(job, blend_path):
    """Resolve tokens and '//' (relative to blend_path's folder) to an absolute output path."""
    scene = job.scene or bpy.context.scene
    cam = job.camera or scene.camera
    p = job.output
    for token, value in (("{blend}", _stem(blend_path)), ("{scene}", scene.name),
                         ("{camera}", cam.name if cam else "nocamera"), ("{job}", job.name)):
        p = p.replace(token, bpy.path.clean_name(value))
    p = bpy.path.abspath(p, start=os.path.dirname(blend_path) or WORK_DIR)
    if job.mode == 'STILL':  # write_still does not expand '#', do it here
        frame = _still_frame(job)
        p = re.sub(r"#+", lambda m: str(frame).zfill(len(m.group())), p)
    return p


def job_script(job, blend_path):
    """One-line python run in the subprocess: apply overrides, then render."""
    scene = job.scene or bpy.context.scene
    lines = ["import bpy", "s = bpy.context.scene",
             f"s.render.filepath = {expand_output(job, blend_path)!r}"]
    if job.camera:  # explicit camera wins over marker bindings
        lines += [f"s.camera = bpy.data.objects[{job.camera.name!r}]",
                  "[setattr(m, 'camera', None) for m in s.timeline_markers]"]
    if job.view_layer:
        lines.append(f"[setattr(v, 'use', v.name == {job.view_layer!r}) for v in s.view_layers]")
    if job.resolution:
        lines.append(f"s.render.resolution_percentage = {job.resolution}")
    if job.samples:
        if scene.render.engine == 'CYCLES':
            lines.append(f"s.cycles.samples = {job.samples}")
        else:
            lines.append(f"s.eevee.taa_render_samples = {job.samples}")
    if job.mode == 'STILL':
        lines += [f"s.frame_set({_still_frame(job)})", "bpy.ops.render.render(write_still=True)"]
    else:
        if not job.use_scene_range:
            lines.append(f"s.frame_start, s.frame_end = {job.frame_start}, {job.frame_end}")
        lines.append("bpy.ops.render.render(animation=True)")
    return "; ".join(lines)


def job_command(job, blend_path, load_path=None):
    """argv for one job. blend_path resolves tokens/'//'; load_path is the file actually rendered."""
    scene = job.scene or bpy.context.scene
    return [bpy.app.binary_path, "-b", *EXTRA_ARGS, load_path or blend_path, "-S", scene.name,
            "--python-exit-code", "1", "--python-expr", job_script(job, blend_path)]


def shell_script(scene, blend_path, windows=sys.platform == "win32"):
    jobs = [j for j in scene.render_queue.jobs if j.enabled]
    if windows:
        lines = ["@echo off"] + [
            subprocess.list2cmdline(job_command(j, blend_path)).replace("%", "%%")
            + f' || echo Render Queue: job "{j.name}" failed' for j in jobs]
        return "\r\n".join(lines) + "\r\n"
    lines = ["#!/bin/sh", f"# Render Queue for {blend_path}"] + [
        shlex.join(job_command(j, blend_path)) + f" || echo {shlex.quote('job failed: ' + j.name)}"
        for j in jobs]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- running

def save_copy():
    """Save the current state (unsaved changes included) to a temp .blend and return its path."""
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, _stem(bpy.data.filepath) + "_queue.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    return path


def start_job(job, load_path):
    os.makedirs(WORK_DIR, exist_ok=True)
    job.log = os.path.join(WORK_DIR, f"{_stem(bpy.data.filepath)}_{bpy.path.clean_name(job.name)}.log")
    job.status = 'RUNNING'
    with open(job.log, "w") as log:
        return subprocess.Popen(job_command(job, bpy.data.filepath, load_path),
                                stdout=log, stderr=subprocess.STDOUT)


def finish_job(job, returncode):
    job.status = 'DONE' if returncode == 0 else 'FAILED'


def run_queue(scene):
    """Run all enabled jobs synchronously (blocking). Returns the jobs that ran."""
    load_path = save_copy()
    jobs = [j for j in scene.render_queue.jobs if j.enabled]
    for j in jobs:
        j.status = 'QUEUED'
    for j in jobs:
        finish_job(j, start_job(j, load_path).wait())
    return jobs


def _redraw():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type in {'PROPERTIES', 'TOPBAR'}:
                area.tag_redraw()


def _kill():
    proc = _state["proc"]
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait()
    _state["proc"] = None


# ---------------------------------------------------------------- operators

class RQ_OT_add(bpy.types.Operator):
    bl_idname = "render_queue.add"
    bl_label = "Add Job"
    bl_description = "Add a job using the current scene, camera and frame range"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        new_job(context.scene)
        return {'FINISHED'}


class RQ_OT_duplicate(bpy.types.Operator):
    bl_idname = "render_queue.duplicate"
    bl_label = "Duplicate Job"
    bl_description = "Duplicate the active job"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        q = context.scene.render_queue
        return 0 <= q.index < len(q.jobs) and not _state["proc"]

    def execute(self, context):
        q = context.scene.render_queue
        src = q.jobs[q.index]
        dst = q.jobs.add()
        copy_job(src, dst)
        dst.name += " copy"
        dst.status, dst.log = 'IDLE', ""
        q.jobs.move(len(q.jobs) - 1, q.index + 1)
        q.index += 1
        return {'FINISHED'}


class RQ_OT_remove(bpy.types.Operator):
    bl_idname = "render_queue.remove"
    bl_label = "Remove Job"
    bl_description = "Remove the active job"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return RQ_OT_duplicate.poll(context)

    def execute(self, context):
        q = context.scene.render_queue
        q.jobs.remove(q.index)
        q.index = min(q.index, len(q.jobs) - 1)
        return {'FINISHED'}


class RQ_OT_move(bpy.types.Operator):
    bl_idname = "render_queue.move"
    bl_label = "Move Job"
    bl_description = "Move the active job up or down"
    bl_options = {'REGISTER', 'UNDO'}
    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    @classmethod
    def poll(cls, context):
        return RQ_OT_duplicate.poll(context)

    def execute(self, context):
        q = context.scene.render_queue
        to = q.index + (-1 if self.direction == 'UP' else 1)
        if 0 <= to < len(q.jobs):
            q.jobs.move(q.index, to)
            q.index = to
        return {'FINISHED'}


class RQ_OT_add_cameras(bpy.types.Operator):
    bl_idname = "render_queue.add_cameras"
    bl_label = "Job per Camera"
    bl_description = "Add one job for every camera in the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        cams = sorted((o for o in context.scene.objects if o.type == 'CAMERA'), key=lambda o: o.name)
        for cam in cams:
            new_job(context.scene, name=cam.name, camera=cam)
        self.report({'INFO'}, f"Added {len(cams)} job(s)")
        return {'FINISHED'}


class RQ_OT_add_markers(bpy.types.Operator):
    bl_idname = "render_queue.add_markers"
    bl_label = "Job per Marker Camera"
    bl_description = ("Add one animation job per camera-bound timeline marker, "
                      "covering the frames until the next marker")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        markers = sorted((m for m in scene.timeline_markers if m.camera), key=lambda m: m.frame)
        for i, m in enumerate(markers):
            end = markers[i + 1].frame - 1 if i + 1 < len(markers) else scene.frame_end
            new_job(scene, name=m.name, camera=m.camera, mode='ANIMATION', use_scene_range=False,
                    frame_start=max(m.frame, scene.frame_start), frame_end=end)
        self.report({'INFO'}, f"Added {len(markers)} job(s)")
        return {'FINISHED'}


class RQ_OT_run(bpy.types.Operator):
    bl_idname = "render_queue.run"
    bl_label = "Render Queue"
    bl_description = ("Render all enabled jobs one after another in background Blender processes "
                      "(unsaved changes included)")

    @classmethod
    def poll(cls, context):
        return not _state["proc"] and any(j.enabled for j in context.scene.render_queue.jobs)

    def execute(self, context):
        jobs = context.scene.render_queue.jobs
        self.scene_name = context.scene.name
        self.todo = [i for i, j in enumerate(jobs) if j.enabled]
        self.current = None
        self.load_path = save_copy()
        for i in self.todo:
            jobs[i].status = 'QUEUED'
        _state.update(cancel=False, done=0, total=len(self.todo))
        # start now: with proc still None until the first tick, Run could be clicked twice
        self.current = self.todo.pop(0)
        _state["proc"] = start_job(jobs[self.current], self.load_path)
        wm = context.window_manager
        self.timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        scene = bpy.data.scenes.get(self.scene_name)
        jobs = scene.render_queue.jobs if scene else []
        proc = _state["proc"]
        if _state["cancel"] or not scene:
            _kill()
            for j in jobs:
                if j.status in {'QUEUED', 'RUNNING'}:
                    j.status = 'CANCELLED'
            return self.stop(context)
        if proc and proc.poll() is None:
            return {'PASS_THROUGH'}
        if proc:
            finish_job(jobs[self.current], proc.returncode)
            _state["done"] += 1
            _state["proc"] = None
        if not self.todo:
            failed = sum(j.status == 'FAILED' for j in jobs)
            self.report({'WARNING'} if failed else {'INFO'},
                        f"Render Queue finished, {failed} failed" if failed else "Render Queue finished")
            return self.stop(context)
        # ponytail: jobs are tracked by index; add/remove/move are disabled while running
        self.current = self.todo.pop(0)
        _state["proc"] = start_job(jobs[self.current], self.load_path)
        _redraw()
        return {'PASS_THROUGH'}

    def stop(self, context):
        context.window_manager.event_timer_remove(self.timer)
        _state["proc"] = None
        _redraw()
        return {'FINISHED'}

    def cancel(self, context):
        # Blender drops the modal handler on file load / window close: kill the render, or
        # _state["proc"] stays set and the queue UI is stuck on "Rendering" for good.
        _kill()
        context.window_manager.event_timer_remove(self.timer)


class RQ_OT_cancel(bpy.types.Operator):
    bl_idname = "render_queue.cancel"
    bl_label = "Cancel"
    bl_description = "Stop the running job and skip the rest of the queue"

    def execute(self, context):
        _state["cancel"] = True
        return {'FINISHED'}


class RQ_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "render_queue.export_script"
    bl_label = "Export Shell Script"
    bl_description = "Write a .sh/.bat with one command per enabled job, for overnight runs (uses the saved file)"
    filename_ext = ".bat" if sys.platform == "win32" else ".sh"
    filter_glob: StringProperty(default="*.bat;*.sh", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(bpy.data.filepath)

    def execute(self, context):
        with open(self.filepath, "w", newline="") as f:
            f.write(shell_script(context.scene, bpy.data.filepath))
        if not sys.platform == "win32":
            os.chmod(self.filepath, 0o755)
        if bpy.data.is_dirty:
            self.report({'WARNING'}, "Script renders the last saved version; save to include changes")
        else:
            self.report({'INFO'}, f"Wrote {self.filepath}")
        return {'FINISHED'}


class RQ_OT_apply_preset(bpy.types.Operator):
    bl_idname = "render_queue.apply_preset"
    bl_label = "Apply Render Preset"
    bl_description = "Set resolution %, samples, denoising and simplify from this preset"
    bl_options = {'REGISTER', 'UNDO'}
    preset: StringProperty()

    def execute(self, context):
        p = all_presets().get(self.preset)
        if not p:
            self.report({'ERROR'}, f"No preset '{self.preset}'")
            return {'CANCELLED'}
        apply_preset(context.scene, p)
        return {'FINISHED'}


class RQ_OT_save_preset(bpy.types.Operator):
    bl_idname = "render_queue.save_preset"
    bl_label = "Save Render Preset"
    bl_description = "Store the current resolution %, samples, denoising and simplify as a preset"
    name: StringProperty(name="Name", default="My Preset")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        presets = user_presets()
        presets[self.name] = read_preset(context.scene)
        _prefs().presets_json = json.dumps(presets)
        context.preferences.is_dirty = True
        return {'FINISHED'}


class RQ_OT_delete_preset(bpy.types.Operator):
    bl_idname = "render_queue.delete_preset"
    bl_label = "Delete Render Preset"
    bl_description = "Delete this saved preset"
    preset: StringProperty()

    def execute(self, context):
        presets = user_presets()
        presets.pop(self.preset, None)
        _prefs().presets_json = json.dumps(presets)
        context.preferences.is_dirty = True
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class RQ_UL_jobs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False)
        row.label(text="Anim" if item.mode == 'ANIMATION' else "Still")
        row.label(text="", icon=STATUS_ICONS[item.status])


class RQ_PT_queue(bpy.types.Panel):
    bl_label = "Render Queue"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        q = scene.render_queue
        row = layout.row()
        row.template_list("RQ_UL_jobs", "", q, "jobs", q, "index", rows=4)
        col = row.column(align=True)
        col.operator("render_queue.add", text="", icon='ADD')
        col.operator("render_queue.remove", text="", icon='REMOVE')
        col.operator("render_queue.duplicate", text="", icon='DUPLICATE')
        col.separator()
        col.operator("render_queue.move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("render_queue.move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        row = layout.row(align=True)
        row.operator("render_queue.add_cameras", icon='OUTLINER_OB_CAMERA')
        row.operator("render_queue.add_markers", icon='MARKER_HLT')

        if 0 <= q.index < len(q.jobs):
            job = q.jobs[q.index]
            box = layout.box()
            box.use_property_split = True
            box.prop(job, "scene")
            box.prop(job, "camera")
            box.prop_search(job, "view_layer", job.scene or scene, "view_layers")
            box.row().prop(job, "mode", expand=True)
            box.prop(job, "use_scene_range")
            if not job.use_scene_range:
                sub = box.column(align=True)
                sub.prop(job, "frame_start", text="Frame" if job.mode == 'STILL' else "Start")
                if job.mode == 'ANIMATION':
                    sub.prop(job, "frame_end")
            box.prop(job, "resolution")
            box.prop(job, "samples")
            box.prop(job, "output")
            if job.log:
                row = box.row()
                row.label(text=f"{job.status.title()}: {job.log}", icon=STATUS_ICONS[job.status])
                row.operator("wm.path_open", text="", icon='TEXT').filepath = job.log

        if _state["proc"]:
            row = layout.row(align=True)
            row.progress(factor=_state["done"] / max(_state["total"], 1), type='BAR',
                         text=f"Rendering {_state['done'] + 1}/{_state['total']}")
            row.operator("render_queue.cancel", icon='CANCEL')
        else:
            row = layout.row(align=True)
            row.scale_y = 1.4
            row.operator("render_queue.run", icon='RENDER_ANIMATION')
            row.operator("render_queue.export_script", text="", icon='FILE_SCRIPT')


class RQ_PT_presets(bpy.types.Panel):
    bl_label = "Render Presets"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        for name in PRESETS:
            row.operator("render_queue.apply_preset", text=name).preset = name
        for name in user_presets():
            row = layout.row(align=True)
            row.operator("render_queue.apply_preset", text=name, icon='PRESET').preset = name
            row.operator("render_queue.delete_preset", text="", icon='X').preset = name
        layout.operator("render_queue.save_preset", icon='ADD', text="Save Current as Preset")


class RQ_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    presets_json: StringProperty(name="Saved presets (JSON)", default="{}")

    def draw(self, context):
        self.layout.prop(self, "presets_json")


def menu_draw(self, context):
    self.layout.separator()
    self.layout.operator("render_queue.run", icon='RENDER_ANIMATION')


classes = (RQ_Job, RQ_Queue, RQ_OT_add, RQ_OT_duplicate, RQ_OT_remove, RQ_OT_move,
           RQ_OT_add_cameras, RQ_OT_add_markers, RQ_OT_run, RQ_OT_cancel, RQ_OT_export,
           RQ_OT_apply_preset, RQ_OT_save_preset, RQ_OT_delete_preset, RQ_UL_jobs,
           RQ_PT_queue, RQ_PT_presets, RQ_Prefs)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.render_queue = PointerProperty(type=RQ_Queue)
    bpy.types.TOPBAR_MT_render.append(menu_draw)


def unregister():
    _kill()
    bpy.types.TOPBAR_MT_render.remove(menu_draw)
    del bpy.types.Scene.render_queue
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
