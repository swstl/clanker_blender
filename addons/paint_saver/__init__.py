"""Paint Saver: Ctrl+S also saves painted images; timed recovery copies; visible unsaved-paint warning.

Blender's Ctrl+S saves the .blend but not modified images, and autosave/crash recovery doesn't keep
paint. This closes that gap.
"""
import os
import time

import bpy
from bpy.app.handlers import persistent


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def dirty_images():
    return [i for i in bpy.data.images if i.is_dirty and not i.library
            and i.type not in {'RENDER_RESULT', 'COMPOSITING'}]


def save_image(img, blend_path=None):
    """Save to its own file, pack it (unsaved .blend), or write to //textures/. Returns how."""
    if img.packed_file:
        img.pack()
        return "repacked"
    path = bpy.path.abspath(img.filepath_raw) if img.filepath_raw else ""
    if img.source == 'FILE' and path:
        img.save()
        return "saved"
    blend_path = blend_path or bpy.data.filepath
    if blend_path and _prefs().generated_to_disk:
        folder = os.path.join(os.path.dirname(blend_path), "textures")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, bpy.path.clean_name(img.name) + ".png")
        img.filepath_raw = dst
        img.file_format = 'PNG'
        img.save()
        img.source = 'FILE'
        img.filepath = bpy.path.relpath(dst, start=os.path.dirname(blend_path))
        return "written"
    img.pack()
    return "packed"


def save_all(blend_path=None):
    return [(i.name, save_image(i, blend_path)) for i in dirty_images()]


def recovery_copy(keep=5):
    """Copy dirty images to //paint_recovery/ (or the temp dir) without touching their paths."""
    base = bpy.path.abspath("//paint_recovery") if bpy.data.filepath else os.path.join(bpy.app.tempdir, "paint_recovery")
    os.makedirs(base, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    written = []
    for img in dirty_images():
        stem = bpy.path.clean_name(img.name)
        dst = os.path.join(base, f"{stem}__{stamp}.png")
        img.save(filepath=dst, save_copy=True)
        written.append(dst)
        old = sorted(f for f in os.listdir(base) if f.startswith(stem + "__"))
        for f in old[:-keep]:
            try:
                os.remove(os.path.join(base, f))
            except OSError:
                pass
    return written


@persistent
def on_save_pre(*args):
    # Save handlers get the target path; during "Save As" bpy.data.filepath is still the old one.
    target = next((a for a in args if isinstance(a, str) and a), None)
    if _prefs().save_with_file:
        try:
            for name, how in save_all(target):
                print(f"Paint Saver: {name} {how}")
        except Exception as e:
            print("Paint Saver:", e)


def _timer():
    p = _prefs()
    if p.recovery and dirty_images():
        try:
            recovery_copy(p.keep)
        except Exception as e:
            print("Paint Saver:", e)
    return max(30.0, p.interval * 60)


class PS_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    save_with_file: bpy.props.BoolProperty(name="Save painted images when saving the .blend", default=True)
    generated_to_disk: bpy.props.BoolProperty(
        name="New (generated) images go to //textures/", default=True,
        description="Otherwise they are packed into the .blend")
    recovery: bpy.props.BoolProperty(name="Timed recovery copies", default=True)
    interval: bpy.props.FloatProperty(name="Every (minutes)", default=3, min=0.5)
    keep: bpy.props.IntProperty(name="Copies to keep per image", default=5, min=1)

    def draw(self, context):
        col = self.layout.column()
        for p in ("save_with_file", "generated_to_disk", "recovery", "interval", "keep"):
            col.prop(self, p)


class PS_OT_save_all(bpy.types.Operator):
    bl_idname = "paint_saver.save_all"
    bl_label = "Save All Painted Images"
    bl_description = "Save every modified image now (file images to their files, new ones to //textures/ or packed)"

    def execute(self, context):
        res = save_all()
        self.report({'INFO'}, f"Saved {len(res)} images" if res else "No unsaved paint")
        return {'FINISHED'}


def header(self, context):
    n = len(dirty_images())
    if n:
        row = self.layout.row()
        row.alert = True
        row.operator("paint_saver.save_all", text=f"{n} unsaved image{'s' if n > 1 else ''}", icon='IMAGE')


_classes = (PS_Prefs, PS_OT_save_all)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.app.handlers.save_pre.append(on_save_pre)
    bpy.types.VIEW3D_HT_header.append(header)
    bpy.types.IMAGE_HT_header.append(header)
    if not bpy.app.background:
        bpy.app.timers.register(_timer, first_interval=60, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.types.IMAGE_HT_header.remove(header)
    bpy.types.VIEW3D_HT_header.remove(header)
    if on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_save_pre)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
