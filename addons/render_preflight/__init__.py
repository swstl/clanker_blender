"""Render Preflight: one-click checklist for "is this file ready to render (locally or on a farm)?"."""
import os

import bpy
from bpy.app.handlers import persistent

ICONS = {'OK': 'CHECKMARK', 'WARN': 'ERROR', 'FAIL': 'CANCEL'}
FILE_KINDS = ("images", "sounds", "movieclips", "fonts", "cache_files", "volumes")
BUILTIN_TREES = {"ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree", "TextureNodeTree"}

_results = []  # last run: list of (status, name, message, fix_key or None)


def _abspath(idb):
    return os.path.normpath(bpy.path.abspath(idb.filepath, library=idb.library))


def _file_users():
    """Yield datablocks that point to an external file (not packed, not generated, not linked-in)."""
    for kind in FILE_KINDS:
        for idb in getattr(bpy.data, kind, ()):
            fp = getattr(idb, "filepath", "")
            if not fp or fp == "<builtin>" or getattr(idb, "packed_file", None) or idb.library:
                continue
            if kind == "images" and idb.source not in {'FILE', 'SEQUENCE', 'MOVIE', 'TILED'}:
                continue
            yield idb


def _short(names, n=5):
    names = list(names)
    return ", ".join(names[:n]) + (f" (+{len(names) - n} more)" if len(names) > n else "")


def _all_ids():
    for name in dir(bpy.data):
        coll = getattr(bpy.data, name, None)
        if isinstance(coll, bpy.types.bpy_prop_collection):
            for idb in coll:
                if isinstance(idb, bpy.types.ID):
                    yield idb
                    nt = getattr(idb, "node_tree", None)  # embedded trees (materials, worlds, lights)
                    if isinstance(nt, bpy.types.NodeTree):
                        yield nt


# ---------------------------------------------------------------- checks
# Each check returns a list of (status, name, message, fix_key or None).

def check_file(scene, farm):
    out = []
    if not bpy.data.filepath:
        out.append(('FAIL', "File", "Never saved (relative paths and farms need a saved .blend)", "save_as"))
    elif bpy.data.is_dirty:
        out.append(('WARN', "File", "Unsaved changes (the farm renders the file on disk)", "save"))
    else:
        out.append(('OK', "File", "Saved", None))

    absolute = [idb.name for idb in _file_users() if not idb.filepath.startswith("//")]
    absolute += [lib.name for lib in bpy.data.libraries if not lib.filepath.startswith("//")]
    if absolute:
        out.append(('WARN' if farm else 'OK', "Absolute Paths",
                    f"{len(absolute)} absolute: {_short(absolute)}", "relative" if bpy.data.filepath else None))
    else:
        out.append(('OK', "Absolute Paths", "All paths relative", None))

    missing = [idb.name for idb in _file_users()
               if not os.path.exists(_abspath(idb).replace("<UDIM>", "1001"))
               and not (getattr(idb, "source", "") == 'SEQUENCE' and os.path.isdir(os.path.dirname(_abspath(idb))))]
    out.append(('FAIL', "Missing Files", f"{len(missing)} missing: {_short(missing)}", "find_missing")
               if missing else ('OK', "Missing Files", "All external files found", None))

    libs = [lib.name for lib in bpy.data.libraries if not os.path.exists(_abspath(lib))]
    if libs:
        out.append(('FAIL', "Linked Libraries", f"{len(libs)} missing: {_short(libs)}", "find_missing"))
    elif bpy.data.libraries:
        out.append(('OK', "Linked Libraries", f"{len(bpy.data.libraries)} found", None))

    fonts = [f.name for f in _file_users() if isinstance(f, bpy.types.VectorFont)]
    if fonts:
        out.append(('WARN' if farm else 'OK', "Fonts",
                    f"{len(fonts)} not packed (render nodes may lack them): {_short(fonts)}", "pack_fonts"))
    packed = [idb.packed_file.size for kind in FILE_KINDS + ("libraries",)
              for idb in getattr(bpy.data, kind, ()) if getattr(idb, "packed_file", None)]
    if packed:
        out.append(('OK', "Packed Files", f"{len(packed)} packed, {sum(packed) / 1e6:.1f} MB inside the .blend", None))
    return out


def check_output(scene, farm):
    out = []
    r = scene.render
    fp = r.filepath
    inside = fp.startswith("//")
    if not inside and bpy.data.filepath:
        root = os.path.dirname(bpy.data.filepath)
        ap = os.path.normpath(bpy.path.abspath(fp))
        inside = os.path.splitdrive(root)[0] == os.path.splitdrive(ap)[0] and os.path.commonpath([root, ap]) == root
    if not fp.strip():
        out.append(('WARN', "Output Path", "Blank: frames go next to the .blend with no name", "output"))
    elif inside:
        out.append(('OK', "Output Path", fp, None))
    elif fp.replace("\\", "/").startswith("/tmp"):
        out.append(('WARN', "Output Path", f"Temp folder '{fp}' (lost on reboot, unreachable on a farm)", "output"))
    elif bpy.data.filepath and farm:
        out.append(('WARN', "Output Path", f"Absolute path outside the project: {fp}", "output"))
    else:
        out.append(('OK', "Output Path", fp, None))

    anim = scene.frame_end > scene.frame_start
    if r.is_movie_format and anim:
        out.append(('WARN' if farm else 'OK', "File Format",
                    "Video output can't be split across render nodes or resumed after a crash: "
                    "render an image sequence (PNG/EXR) and encode afterwards", "png"))
    else:
        out.append(('OK', "File Format", r.image_settings.file_format, None))

    if anim and not r.is_movie_format and not r.use_overwrite and not r.use_placeholder:
        out.append(('WARN', "Overwrite", "Overwrite off: old frames already on disk are kept", None))
    elif farm and anim and not r.is_movie_format:
        out.append(('OK', "Overwrite", f"Overwrite {'on' if r.use_overwrite else 'off'}, placeholders "
                    f"{'on' if r.use_placeholder else 'off'} (shared-folder rendering on several machines: "
                    "overwrite off + placeholders on)", None))

    if scene.frame_step > 1:  # end < start can't happen: RNA clamps frame_start down
        out.append(('WARN', "Frame Range", f"{scene.frame_start}-{scene.frame_end}, step {scene.frame_step}: "
                    f"only every {scene.frame_step}th frame renders", None))
    else:
        out.append(('OK', "Frame Range", f"{scene.frame_start}-{scene.frame_end} "
                    f"({scene.frame_end - scene.frame_start + 1} frames)", None))

    if r.resolution_percentage != 100:
        out.append(('WARN', "Resolution", f"{r.resolution_percentage}% of {r.resolution_x}x{r.resolution_y}",
                    "res100"))
    else:
        out.append(('OK', "Resolution", f"{r.resolution_x}x{r.resolution_y}", None))
    return out


def check_camera(scene, farm):
    marks = sorted((m for m in scene.timeline_markers if m.camera), key=lambda m: m.frame)
    first = [m for m in marks if m.frame <= scene.frame_start]
    # Blender renders with the last marker camera at/before the frame, else the earliest marker's camera.
    cam = first[-1].camera if first else marks[0].camera if marks else scene.camera
    if not cam:
        return [('FAIL', "Camera", "No scene camera", None)]
    out = [('OK', "Camera", cam.name, None)]
    outside = [f"{m.name} ({m.frame})" for m in marks if not scene.frame_start <= m.frame <= scene.frame_end]
    if outside:
        out.append(('WARN', "Camera Markers", f"Camera switch outside the frame range: {_short(outside)}", None))
    return out


def _gn_unbaked(ob, md):
    """Bake ids of a geometry nodes modifier that bake to disk but have no baked data there."""
    if not bpy.data.filepath:
        return []
    out = []
    for bk in md.bakes:
        target = md.bake_target if bk.bake_target == 'INHERIT' else bk.bake_target
        if target != 'DISK':
            continue  # ponytail: packed bakes have no "is baked" in the Python API, so they can't be checked
        d = bk.directory if bk.use_custom_path else md.bake_directory
        meta = os.path.join(bpy.path.abspath(d), str(bk.bake_id), "meta")
        if not (os.path.isdir(meta) and os.listdir(meta)):
            out.append(f"{ob.name}/{md.name}/{bk.node.name if bk.node else bk.bake_id}")
    return out


def check_caches(scene, farm):
    unbaked = []
    for ob in scene.objects:
        for md in ob.modifiers:
            if not md.show_render:
                continue
            if md.type in {'CLOTH', 'SOFT_BODY'} and not md.point_cache.is_baked:
                unbaked.append(f"{ob.name}/{md.name}")
            elif md.type == 'FLUID' and md.fluid_type == 'DOMAIN' and not md.domain_settings.has_cache_baked_any:
                unbaked.append(f"{ob.name}/{md.name}")
            elif md.type == 'NODES':
                unbaked += _gn_unbaked(ob, md)
        for ps in ob.particle_systems:
            if ps.settings.type == 'EMITTER' and ps.settings.physics_type != 'NO' and not ps.point_cache.is_baked:
                unbaked.append(f"{ob.name}/{ps.name}")
    rbw = scene.rigidbody_world
    if rbw and rbw.enabled and rbw.collection and not rbw.point_cache.is_baked:
        unbaked.append("Rigid Body World")
    if unbaked:
        return [('WARN', "Simulations", f"{len(unbaked)} not baked (each render node re-simulates "
                 f"from its first frame): {_short(unbaked)}", None)]
    return [('OK', "Simulations", "Nothing to bake", None)]


def check_scripts(scene, farm):
    out = []
    py = []
    for idb in _all_ids():
        ad = getattr(idb, "animation_data", None)
        for fc in (ad.drivers if ad else ()):
            d = fc.driver
            if d.type == 'SCRIPTED' and not d.is_simple_expression:
                py.append(f"{idb.name}: {d.expression}")
    if py:
        autorun = bpy.context.preferences.filepaths.use_scripts_auto_execute
        out.append(('OK' if autorun and not farm else 'WARN', "Python Drivers",
                    f"{len(py)} drivers need Python ({'auto-run off here' if not autorun else 'farm must allow auto-run'}"
                    f"): {_short(py, 3)}", None))
    custom = [t.name for t in bpy.data.node_groups if t.bl_idname not in BUILTIN_TREES]
    custom += [f"{t.name}/{n.name}" for t in _all_ids() if isinstance(t, bpy.types.NodeTree)
               and t.bl_idname in BUILTIN_TREES for n in t.nodes if n.type == 'CUSTOM']
    if custom:
        out.append(('WARN', "Add-on Nodes", f"{len(custom)} nodes/trees from add-ons (render nodes need the "
                    f"same add-on): {_short(custom)}", None))
    return out


def check_engine(scene, farm):
    out = []
    eng = scene.render.engine
    if eng == 'CYCLES':
        cy = scene.cycles
        if cy.device == 'GPU':
            prefs = bpy.context.preferences.addons.get("cycles")
            if prefs and prefs.preferences.compute_device_type == 'NONE':
                out.append(('WARN', "Cycles Device", "GPU selected but no GPU backend enabled in Preferences: "
                            "renders on CPU", None))
        if not cy.use_adaptive_sampling and cy.samples > 1024 or cy.samples > 8192:
            out.append(('WARN', "Cycles Samples", f"{cy.samples} samples"
                        f"{'' if cy.use_adaptive_sampling else ' without adaptive sampling'}", None))
        if cy.time_limit > 0:
            out.append(('WARN' if farm else 'OK', "Cycles Time Limit",
                        f"{cy.time_limit:g}s per frame: noise differs between machines", None))
        if scene.cycles.use_denoising:
            import _cycles
            if cy.denoiser == 'OPENIMAGEDENOISE' and not _cycles.with_openimagedenoise:
                out.append(('FAIL', "Denoiser", "OpenImageDenoise not available in this build", None))
            elif cy.denoiser == 'OPTIX':
                out.append(('WARN' if farm else 'OK', "Denoiser", "OptiX needs NVIDIA GPUs on every node", None))
        if not out:
            out.append(('OK', "Engine", f"Cycles, {cy.device}, {cy.samples} samples", None))
    elif eng == 'BLENDER_EEVEE':
        out.append(('OK', "Engine", "EEVEE (render nodes need a GPU)", None))
    elif eng == 'BLENDER_WORKBENCH':
        out.append(('OK', "Engine", "Workbench (viewport look only)", None))
    else:
        out.append(('WARN' if farm else 'OK', "Engine", f"{eng}: render nodes need this engine add-on", None))

    tree = scene.compositing_node_group if scene.render.use_compositing else None
    absf = [n.name for n in (tree.nodes if tree else ()) if n.bl_idname == "CompositorNodeOutputFile"
            and not n.mute and n.directory and not n.directory.startswith("//")]
    if absf:
        out.append(('WARN' if farm else 'OK', "Compositor Output",
                    f"File Output nodes with absolute paths: {_short(absf)}", None))
    return out


CHECKS = (check_file, check_output, check_camera, check_caches, check_scripts, check_engine)


def run_checks(scene=None, farm=None):
    scene = scene or bpy.context.scene
    farm = scene.render_preflight_farm if farm is None else farm
    _results[:] = [row for c in CHECKS for row in c(scene, farm)]
    return list(_results)


def report_text(results=None):
    rows = _results if results is None else results
    name = bpy.path.basename(bpy.data.filepath) or "(unsaved)"
    lines = [f"Render Preflight: {name}"]
    lines += [f"[{s:4}] {n}: {m}" for s, n, m, _ in rows]
    fails = sum(r[0] == 'FAIL' for r in rows)
    warns = sum(r[0] == 'WARN' for r in rows)
    lines.append(f"{fails} failed, {warns} warnings")
    return "\n".join(lines)


# ---------------------------------------------------------------- fixes

def _set_output(scene):
    scene.render.filepath = f"//renders/{bpy.path.clean_name(scene.name)}_"


def _set_png(scene):
    scene.render.image_settings.media_type = 'IMAGE'
    scene.render.image_settings.file_format = 'PNG'


def _pack_fonts(scene):
    for f in bpy.data.fonts:
        if f.filepath != "<builtin>" and not f.packed_file and not f.library and os.path.exists(_abspath(f)):
            f.pack()


FIXES = {
    "save": ("Save", lambda s: bpy.ops.wm.save_mainfile()),
    "save_as": ("Save As", lambda s: bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')),
    "relative": ("Make Relative", lambda s: bpy.ops.file.make_paths_relative()),
    "find_missing": ("Find Missing", lambda s: bpy.ops.file.find_missing_files('INVOKE_DEFAULT')),
    "pack_fonts": ("Pack Fonts", _pack_fonts),
    "output": ("Set //renders/", _set_output),
    "png": ("Use PNG", _set_png),
    "res100": ("Set 100%", lambda s: setattr(s.render, "resolution_percentage", 100)),
}


# ---------------------------------------------------------------- operators / UI

class RP_OT_run(bpy.types.Operator):
    bl_idname = "render_preflight.run"
    bl_label = "Run Checks"
    bl_description = "Check whether this file is ready to render locally or on a render farm"

    def execute(self, context):
        res = run_checks(context.scene)
        fails = sum(r[0] == 'FAIL' for r in res)
        warns = sum(r[0] == 'WARN' for r in res)
        self.report({'ERROR' if fails else 'WARNING' if warns else 'INFO'},
                    f"Preflight: {fails} failed, {warns} warnings")
        return {'FINISHED'}


class RP_OT_show(bpy.types.Operator):
    bl_idname = "render_preflight.show"
    bl_label = "Render Preflight"
    bl_description = "Run the render preflight checks and show the report"

    def invoke(self, context, event):
        run_checks(context.scene)
        return context.window_manager.invoke_popup(self, width=560)

    def draw(self, context):
        draw_report(self.layout, context)

    def execute(self, context):
        run_checks(context.scene)
        return {'FINISHED'}


class RP_OT_fix(bpy.types.Operator):
    bl_idname = "render_preflight.fix"
    bl_label = "Fix"
    bl_description = "Apply the suggested fix for this check, then check again"
    bl_options = {'REGISTER', 'UNDO'}

    what: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.what not in FIXES:
            return {'CANCELLED'}
        FIXES[self.what][1](context.scene)
        run_checks(context.scene)
        return {'FINISHED'}


class RP_OT_copy(bpy.types.Operator):
    bl_idname = "render_preflight.copy"
    bl_label = "Copy Report"
    bl_description = "Copy the preflight report to the clipboard as text"

    def execute(self, context):
        if not _results:
            run_checks(context.scene)
        context.window_manager.clipboard = report_text()
        self.report({'INFO'}, "Report copied")
        return {'FINISHED'}


def draw_report(layout, context):
    sc = context.scene
    row = layout.row(align=True)
    row.operator("render_preflight.run", icon='FILE_REFRESH')
    row.operator("render_preflight.copy", text="", icon='COPYDOWN')
    row = layout.row()
    row.prop(sc, "render_preflight_farm")
    row.prop(sc, "render_preflight_on_render")
    if not _results:
        layout.label(text="Not checked yet")
        return
    col = layout.column(align=True)
    for status, name, msg, fix in _results:
        row = col.row(align=True)
        row.alert = status == 'FAIL'
        row.label(text=f"{name}: {msg}", icon=ICONS[status])
        if fix and status != 'OK':
            row.operator("render_preflight.fix", text=FIXES[fix][0]).what = fix


class RP_PT_panel(bpy.types.Panel):
    bl_label = "Preflight"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_report(self.layout, context)


def file_menu(self, context):
    self.layout.operator("render_preflight.show", icon='CHECKMARK')


@persistent
def _on_render_init(scene, *_):
    if not getattr(scene, "render_preflight_on_render", False):
        return
    res = run_checks(scene)
    bad = [r for r in res if r[0] != 'OK']
    if bad:
        print(report_text(bad))


_classes = (RP_OT_run, RP_OT_show, RP_OT_fix, RP_OT_copy, RP_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.render_preflight_farm = bpy.props.BoolProperty(
        name="For Render Farm", default=True,
        description="Also warn about things that only break on render farms (video output, absolute paths, fonts)")
    bpy.types.Scene.render_preflight_on_render = bpy.props.BoolProperty(
        name="Check Before Render", default=False,
        description="Run the checks when a render starts and print warnings to the console (never blocks)")
    bpy.types.TOPBAR_MT_file.append(file_menu)
    bpy.app.handlers.render_init.append(_on_render_init)


def unregister():
    if _on_render_init in bpy.app.handlers.render_init:
        bpy.app.handlers.render_init.remove(_on_render_init)
    bpy.types.TOPBAR_MT_file.remove(file_menu)
    del bpy.types.Scene.render_preflight_on_render
    del bpy.types.Scene.render_preflight_farm
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
