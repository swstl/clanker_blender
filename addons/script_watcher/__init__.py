"""Script Watcher: edit Python in VS Code/Vim, see the result in Blender on save.

Watches one .py file (re-runs it) or an add-on/extension folder (disables + re-enables it).
Uses a timer on the main thread, so it is safe to touch bpy from the script.
"""
import os
import runpy
import sys
import traceback

import bpy

_state = {"mtime": 0.0}


def _settings():
    return bpy.context.window_manager.script_watcher


def newest_mtime(path):
    if os.path.isfile(path):
        return os.path.getmtime(path)
    newest = 0.0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith((".", "__pycache__"))]
        for f in files:
            if f.endswith((".py", ".toml")):
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
    return newest


def module_for_folder(folder):
    """Find the enabled add-on module whose files live in `folder`."""
    folder = os.path.realpath(folder)
    for mod_name in list(bpy.context.preferences.addons.keys()):
        mod = sys.modules.get(mod_name)
        f = getattr(mod, "__file__", None)
        if f and os.path.realpath(os.path.dirname(f)) == folder:
            return mod_name
    return None


def run_target(path):
    """Run a script or reload an add-on folder. Returns (ok, message)."""
    try:
        if os.path.isfile(path):
            runpy.run_path(path, run_name="__main__")
            return True, f"Ran {os.path.basename(path)}"
        mod = module_for_folder(path)
        if not mod:
            return False, "Folder is not an enabled add-on"
        bpy.ops.preferences.addon_disable(module=mod)
        for name in [n for n in sys.modules if n == mod or n.startswith(mod + ".")]:
            del sys.modules[name]  # drop stale submodules so edits in them are picked up
        bpy.ops.preferences.addon_enable(module=mod)
        return True, f"Reloaded {mod}"
    except Exception:
        traceback.print_exc()
        return False, traceback.format_exc().strip().splitlines()[-1]


def _tick():
    s = _settings()
    if not s.active:
        return None
    path = bpy.path.abspath(s.path)
    try:
        mt = newest_mtime(path)
    except OSError:
        return 1.0
    if mt > _state["mtime"]:
        _state["mtime"] = mt
        ok, msg = run_target(path)
        s.last_result = ("OK: " if ok else "ERROR: ") + msg
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    return s.interval


class SW_Settings(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="Watch", subtype='FILE_PATH',
                                   description="A .py file to re-run, or an add-on folder to reload")
    interval: bpy.props.FloatProperty(name="Check every (s)", default=0.5, min=0.1, max=10)
    active: bpy.props.BoolProperty(name="Watching", default=False)
    last_result: bpy.props.StringProperty(name="Last")


class SW_OT_toggle(bpy.types.Operator):
    bl_idname = "script_watcher.toggle"
    bl_label = "Start/Stop Watching"
    bl_description = "Run the file now and again every time it is saved"

    def execute(self, context):
        s = _settings()
        if s.active:
            s.active = False
            if bpy.app.timers.is_registered(_tick):
                bpy.app.timers.unregister(_tick)
            return {'FINISHED'}
        if not os.path.exists(bpy.path.abspath(s.path)):
            self.report({'WARNING'}, "Pick a .py file or add-on folder first")
            return {'CANCELLED'}
        s.active = True
        _state["mtime"] = 0.0  # run immediately
        bpy.app.timers.register(_tick, first_interval=0.01)
        return {'FINISHED'}


class SW_PT_panel(bpy.types.Panel):
    bl_label = "Script Watcher"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Text"

    def draw(self, context):
        s = context.window_manager.script_watcher
        col = self.layout.column()
        col.prop(s, "path", text="")
        col.prop(s, "interval")
        col.operator("script_watcher.toggle", text="Stop" if s.active else "Watch",
                     icon='PAUSE' if s.active else 'PLAY', depress=s.active)
        if s.last_result:
            row = col.row()
            row.alert = s.last_result.startswith("ERROR")
            row.label(text=s.last_result)


_classes = (SW_Settings, SW_OT_toggle, SW_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.script_watcher = bpy.props.PointerProperty(type=SW_Settings)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    del bpy.types.WindowManager.script_watcher
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
