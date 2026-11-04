"""Texture Auto Reload: watch image files on disk and reload them when they change."""
import os

import bpy

_mtimes = {}  # image name -> (abspath, mtime)


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def _watchable(img):
    return (img.source in {'FILE', 'TILED'} and img.filepath and not img.packed_file
            and not img.library)


def check(reload=True):
    """Reload watched images whose file changed. Returns list of reloaded image names.

    Images with unsaved paint changes are never reloaded (that would throw away work)."""
    changed = []
    for img in bpy.data.images:
        if not _watchable(img):
            continue
        path = bpy.path.abspath(img.filepath, library=img.library)
        if img.source == 'TILED':
            path = path.replace("<UDIM>", "1001")
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        old = _mtimes.get(img.name)
        _mtimes[img.name] = (path, mt)
        if old and old[0] == path and mt > old[1]:
            if img.is_dirty:
                print(f"Texture Auto Reload: {img.name} changed on disk but has unsaved paint, skipped")
                continue
            if reload:
                img.reload()
            changed.append(img.name)
    if changed:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type in {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'}:
                    area.tag_redraw()
    return changed


def _timer():
    p = _prefs()
    if p.enabled:
        try:
            n = check()
            if n:
                print("Texture Auto Reload: reloaded", ", ".join(n))
        except Exception as e:  # never let the timer die
            print("Texture Auto Reload:", e)
    return p.interval


class TAR_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    enabled: bpy.props.BoolProperty(name="Watch image files", default=True)
    interval: bpy.props.FloatProperty(name="Check every (s)", default=1.5, min=0.2, max=60)

    def draw(self, context):
        row = self.layout.row()
        row.prop(self, "enabled")
        row.prop(self, "interval")


class TAR_OT_reload_all(bpy.types.Operator):
    bl_idname = "texture_autoreload.reload_all"
    bl_label = "Reload All Images"
    bl_description = "Reload every image from disk (skips images with unsaved paint changes)"

    def execute(self, context):
        n = 0
        for img in bpy.data.images:
            if _watchable(img) and not img.is_dirty:
                img.reload()
                n += 1
        self.report({'INFO'}, f"Reloaded {n} images")
        return {'FINISHED'}


def image_menu(self, context):
    self.layout.separator()
    self.layout.prop(_prefs(), "enabled", text="Auto Reload Changed Files")
    self.layout.operator("texture_autoreload.reload_all")


def register():
    bpy.utils.register_class(TAR_Prefs)
    bpy.utils.register_class(TAR_OT_reload_all)
    bpy.types.IMAGE_MT_image.append(image_menu)
    if not bpy.app.background:
        bpy.app.timers.register(_timer, first_interval=2, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.types.IMAGE_MT_image.remove(image_menu)
    bpy.utils.unregister_class(TAR_OT_reload_all)
    bpy.utils.unregister_class(TAR_Prefs)
    _mtimes.clear()
