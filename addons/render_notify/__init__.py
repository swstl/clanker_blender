"""Render Notify: tell me when the render is done so I can stop staring at the progress bar."""
import os
import subprocess
import sys
import time

import bpy
from bpy.app.handlers import persistent

_start = [0.0]


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def notify(title, body):
    """Fire-and-forget desktop notification. Never raises."""
    try:
        if sys.platform.startswith("linux"):
            cmd = ["notify-send", "-a", "Blender", title, body]
        elif sys.platform == "darwin":
            cmd = ["osascript", "-e", f'display notification "{body}" with title "{title}"']
        else:
            ps = ("[reflection.assembly]::loadwithpartialname('System.Windows.Forms');"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                  f"$n.Visible=$true;$n.ShowBalloonTip(5000,'{title}','{body}',0);Start-Sleep 6")
            cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # missing notify-send etc.
        print("Render Notify:", e)


def beep():
    try:
        import aud
        dev = aud.Device()
        # Two short tones; sound is generated, no file needed.
        snd = aud.Sound.sine(880).limit(0, 0.15).join(aud.Sound.silence().limit(0, 0.08)).join(
            aud.Sound.sine(1320).limit(0, 0.25)).volume(_prefs().volume)
        dev.play(snd)
    except Exception as e:
        print("Render Notify:", e)


def fmt_time(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def _file_name():
    return os.path.basename(bpy.data.filepath) or "untitled.blend"


@persistent
def on_init(scene, *_):
    _start[0] = time.time()


@persistent
def on_complete(scene, *_):
    p = _prefs()
    elapsed = time.time() - _start[0]
    if elapsed < p.min_seconds:
        return
    if p.desktop:
        frames = "" if scene.frame_start == scene.frame_end else f" frames {scene.frame_start}-{scene.frame_end}"
        notify("Render finished", f"{_file_name()}{frames} in {fmt_time(elapsed)}")
    if p.sound:
        beep()


@persistent
def on_cancel(scene, *_):
    p = _prefs()
    if p.notify_cancel and time.time() - _start[0] >= p.min_seconds and p.desktop:
        notify("Render cancelled", f"{_file_name()} after {fmt_time(time.time() - _start[0])}")


class RN_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    desktop: bpy.props.BoolProperty(name="Desktop notification", default=True)
    sound: bpy.props.BoolProperty(name="Play sound", default=True)
    volume: bpy.props.FloatProperty(name="Volume", default=0.4, min=0, max=1)
    notify_cancel: bpy.props.BoolProperty(name="Notify on cancel", default=False)
    min_seconds: bpy.props.FloatProperty(
        name="Only if render took longer than (s)", default=10, min=0,
        description="Skip notifications for quick test renders")

    def draw(self, context):
        col = self.layout.column()
        for p in ("desktop", "sound", "volume", "notify_cancel", "min_seconds"):
            col.prop(self, p)
        col.operator("render_notify.test")


class RN_OT_test(bpy.types.Operator):
    bl_idname = "render_notify.test"
    bl_label = "Test Notification"

    def execute(self, context):
        notify("Render Notify", "This is what a finished render looks like")
        beep()
        return {'FINISHED'}


_handlers = (
    (bpy.app.handlers.render_init, on_init),
    (bpy.app.handlers.render_complete, on_complete),
    (bpy.app.handlers.render_cancel, on_cancel),
)


def register():
    bpy.utils.register_class(RN_Prefs)
    bpy.utils.register_class(RN_OT_test)
    for lst, fn in _handlers:
        lst.append(fn)


def unregister():
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
    bpy.utils.unregister_class(RN_OT_test)
    bpy.utils.unregister_class(RN_Prefs)
