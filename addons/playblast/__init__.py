"""Playblast: one-click viewport animation preview with its own output settings and burn-ins.

Render settings (path, format, resolution, stamp) are swapped in for the playblast and always put
back afterwards, so a preview can never overwrite your final render setup.
"""
import os
import re
from contextlib import contextmanager

import bpy

# (owner attr path, property) pairs we change and must restore.
SAVED = [("render", p) for p in ("filepath", "resolution_percentage", "use_stamp", "use_stamp_frame",
                                 "use_stamp_camera", "use_stamp_filename", "use_stamp_date", "use_stamp_time",
                                 "use_stamp_render_time", "use_stamp_lens", "use_stamp_note", "stamp_note_text",
                                 "use_stamp_marker", "stamp_font_size", "use_file_extension")]
SAVED += [("render.image_settings", p) for p in ("media_type", "file_format", "color_mode")]


def _owner(scene, path):
    o = scene
    for part in path.split("."):
        o = getattr(o, part)
    return o


@contextmanager
def swapped_settings(scene, s):
    saved = [(path, p, getattr(_owner(scene, path), p)) for path, p in SAVED]
    ff = scene.render.ffmpeg
    ff_saved = {p: getattr(ff, p) for p in ("format", "codec", "constant_rate_factor", "audio_codec")}
    try:
        r = scene.render
        r.resolution_percentage = s.resolution
        if hasattr(r.image_settings, "media_type"):  # 5.x: pick video first, then the container
            r.image_settings.media_type = 'VIDEO'
        r.image_settings.file_format = 'FFMPEG'
        ff.format, ff.codec = 'MPEG4', 'H264'
        ff.constant_rate_factor = 'MEDIUM'
        ff.audio_codec = 'AAC' if s.audio else 'NONE'
        r.use_file_extension = True
        r.use_stamp = s.burn_in
        r.use_stamp_frame = r.use_stamp_camera = r.use_stamp_filename = r.use_stamp_lens = True
        r.use_stamp_marker = True
        r.use_stamp_date = r.use_stamp_render_time = False
        r.use_stamp_time = True
        r.use_stamp_note = bool(s.note)
        r.stamp_note_text = s.note
        r.stamp_font_size = 20
        r.filepath = next_path(scene, s)
        yield r.filepath
    finally:
        for path, p, v in saved:
            try:
                setattr(_owner(scene, path), p, v)
            except (AttributeError, TypeError):
                pass
        for p, v in ff_saved.items():
            setattr(ff, p, v)


def next_path(scene, s):
    """//playblasts/<blend>_<scene>_v###.mp4 with the next free version number."""
    folder = bpy.path.abspath(s.folder) if bpy.data.filepath or not s.folder.startswith("//") \
        else os.path.join(bpy.app.tempdir, "playblasts")
    os.makedirs(folder, exist_ok=True)
    stem = bpy.path.clean_name(os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled")
    base = f"{stem}_{bpy.path.clean_name(scene.name)}_v"
    pat = re.compile(re.escape(base) + r"(\d+)")
    nums = [int(m.group(1)) for f in os.listdir(folder) for m in [pat.match(f)] if m]
    return os.path.join(folder, f"{base}{max(nums, default=0) + 1:03d}.mp4")


class PB_Settings(bpy.types.PropertyGroup):
    folder: bpy.props.StringProperty(name="Folder", default="//playblasts/", subtype='DIR_PATH',
                                     options={'PATH_SUPPORTS_BLEND_RELATIVE'})
    resolution: bpy.props.IntProperty(name="Resolution %", default=50, min=1, max=100, subtype='PERCENTAGE')
    burn_in: bpy.props.BoolProperty(name="Burn-ins", default=True,
                                    description="Frame, camera, lens, file, marker and note on the video")
    note: bpy.props.StringProperty(name="Note", description="Extra text burned into the video")
    audio: bpy.props.BoolProperty(name="Audio", default=True)
    play: bpy.props.BoolProperty(name="Play When Done", default=True)
    camera: bpy.props.BoolProperty(name="From Camera", default=False,
                                   description="Render through the scene camera instead of the current view")


class PB_OT_playblast(bpy.types.Operator):
    bl_idname = "playblast.run"
    bl_label = "Playblast"
    bl_description = "Render a viewport preview video of the frame range (render settings are restored after)"

    def execute(self, context):
        s = context.scene.playblast
        sc = context.scene
        r3d = context.space_data.region_3d if context.space_data and context.space_data.type == 'VIEW_3D' else None
        prev_persp = r3d.view_perspective if r3d else None
        try:
            if s.camera and r3d and sc.camera:
                r3d.view_perspective = 'CAMERA'
            with swapped_settings(sc, s) as path:
                bpy.ops.render.opengl(animation=True, view_context=r3d is not None)
        finally:
            if r3d and prev_persp:
                r3d.view_perspective = prev_persp
        self.report({'INFO'}, f"Playblast: {path}")
        if s.play and os.path.isfile(path):
            bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class PB_PT_panel(bpy.types.Panel):
    bl_label = "Playblast"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.playblast
        col = self.layout.column()
        col.operator("playblast.run", icon='RENDER_ANIMATION')
        for p in ("folder", "resolution", "burn_in", "note", "camera", "audio", "play"):
            col.prop(s, p)


def view_menu(self, context):
    self.layout.operator("playblast.run", icon='RENDER_ANIMATION')


_classes = (PB_Settings, PB_OT_playblast, PB_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.playblast = bpy.props.PointerProperty(type=PB_Settings)
    bpy.types.VIEW3D_MT_view.append(view_menu)


def unregister():
    bpy.types.VIEW3D_MT_view.remove(view_menu)
    del bpy.types.Scene.playblast
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
