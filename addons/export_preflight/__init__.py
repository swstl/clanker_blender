"""Export Preflight: check selected objects (or a collection) against game engine rules before exporting."""
import os
import re

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

ICONS = {'ERROR': 'CANCEL', 'WARN': 'ERROR', 'INFO': 'INFO'}
RANK = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
PROFILES = {
    # influences: max bone weights per vertex; max_mats: material slots; uv_max: UV maps the importer reads
    'UNITY': dict(name="Unity", influences=4, max_mats=8, uv_max=8, lightmap=False, pot='WARN'),
    'UNREAL': dict(name="Unreal", influences=8, max_mats=8, uv_max=8, lightmap=True, pot='WARN'),
    'GODOT': dict(name="Godot", influences=4, max_mats=8, uv_max=2, lightmap=False, pot='INFO'),
    'GENERIC': dict(name="Generic", influences=4, max_mats=16, uv_max=8, lightmap=False, pot='INFO'),
}
COLLISION_RE = re.compile(r"^(UCX|UBX|USP|UCP)_")
SAFE = {"scale", "normalize"}  # what Fix All Safe applies

_findings = []  # last run: (severity, object name, problem, fix key or "", item name or "")
_checked = []  # object names of the last run, so fixes re-check the same set after Select changed it
_last = {"profile": ""}


def sanitize(name):
    return re.sub(r"[^A-Za-z0-9_\-.]+", "_", name).strip("_") or "unnamed"


def _name_problem(name):
    bad = []
    if " " in name:
        bad.append("spaces")
    if not name.isascii():
        bad.append("non-ASCII")
    if sanitize(name) != name and not bad:
        bad.append("special characters")
    return " and ".join(bad)


def _skinned_armature(ob):
    for md in ob.modifiers:
        if md.type == 'ARMATURE' and md.object:
            return md.object
    return ob.parent if ob.parent and ob.parent.type == 'ARMATURE' and ob.parent_type == 'ARMATURE' else None


def _is_skinned(ob):
    return any(md.type == 'ARMATURE' for md in ob.modifiers) or _skinned_armature(ob) is not None


def _deform_groups(ob):
    arm = _skinned_armature(ob)
    if arm is None:
        return {vg.index for vg in ob.vertex_groups}
    bones = {b.name for b in arm.data.bones if b.use_deform}
    return {vg.index for vg in ob.vertex_groups if vg.name in bones}


def _bottom_center(ob):
    """Bottom center and size of the mesh's local bounds (from the data: ob.bound_box lags after edits)."""
    co = np.empty(len(ob.data.vertices) * 3, dtype=np.float32)
    ob.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lo, hi = Vector(co.min(axis=0)), Vector(co.max(axis=0))
    return Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)), hi - lo


def _image_missing(img):
    if img.packed_file or img.source not in {'FILE', 'TILED', 'SEQUENCE'}:
        return False
    return not os.path.exists(bpy.path.abspath(img.filepath, library=img.library).replace("<UDIM>", "1001"))


def _pot(n):
    return n > 0 and n & (n - 1) == 0


# ---------------------------------------------------------------- checks
# Each check yields (severity, problem, fix key, item) for one object.

def check_transform(ob, p, prof):
    s = ob.scale
    if ob.type == 'MESH' and any(abs(v - 1) > 1e-4 for v in s):
        if any(v < 0 for v in s):
            what = "Negative scale (flips normals in most engines)"
        elif max(s) - min(s) > 1e-4:
            what = "Non-uniform scale"
        else:
            what = f"Unapplied scale {s.x:g}"
        notes = []
        if ob.children:
            notes.append(f"{len(ob.children)} children keep their look")
        if ob.data.users > 1:
            notes.append(f"mesh shared by {ob.data.users} objects, Fix makes it single-user")
        yield 'WARN', what + (f" ({'; '.join(notes)})" if notes else ""), "scale", ""
    if ob.type == 'MESH' and ob.matrix_basis.to_quaternion().angle > 1e-4:
        static = not _is_skinned(ob)
        sev = 'WARN' if p == 'UNREAL' and static else 'INFO'
        yield sev, "Unapplied rotation" + (" (Unreal static meshes import it baked)" if sev == 'WARN' else ""), \
            "rotation", ""
    if ob.type == 'ARMATURE' and any(abs(v - 1) > 1e-4 for v in s):
        yield 'WARN', f"Armature scale {s.x:g}, {s.y:g}, {s.z:g} (animations import scaled)", "arm_scale", ""
    if ob.type == 'MESH' and ob.data.vertices:
        bc, size = _bottom_center(ob)
        m3 = ob.matrix_world.to_3x3()
        if (m3 @ bc).length > 0.5 * (m3 @ size).length + 1e-4:
            yield 'INFO', "Origin far from the geometry's bottom center", "origin", ""


def check_mesh(ob, p, prof, budget, collision):
    if ob.type != 'MESH':
        return
    me = ob.data
    is_col = bool(COLLISION_RE.match(ob.name))
    bm = bmesh.new()
    bm.from_mesh(me)
    ngons = sum(len(f.verts) > 4 for f in bm.faces)
    loose_v = sum(not v.link_edges for v in bm.verts)
    loose_e = sum(not e.link_faces for e in bm.edges)
    zero = sum(f.calc_area() < 1e-10 for f in bm.faces)
    nonman = sum(not e.is_manifold for e in bm.edges if e.link_faces)
    bm.free()
    if ngons:
        yield 'INFO', f"{ngons} n-gons (the engine triangulates them its own way)", "", ""
    if loose_v or loose_e:
        yield 'WARN', f"{loose_v} loose vertices, {loose_e} loose edges", "loose", ""
    if zero:
        yield 'WARN', f"{zero} zero-area faces", "degenerate", ""
    if nonman:
        yield 'INFO', f"{nonman} non-manifold edges", "", ""

    if not is_col:
        nuv = len(me.uv_layers)
        if nuv == 0:
            yield 'ERROR', "No UV map", "", ""
        elif prof["lightmap"] and nuv < 2:
            yield 'WARN', "No second UV map for lightmaps (UV1)", "", ""
        if nuv > prof["uv_max"]:
            yield 'WARN', f"{nuv} UV maps, {prof['name']} reads {prof['uv_max']}", "", ""
        slots = ob.material_slots
        if not slots:
            yield 'WARN', "No material", "", ""
        else:
            empty = sum(not s.material for s in slots)
            if empty:
                yield 'WARN', f"{empty} empty material slot(s)", "", ""
            if len(slots) > prof["max_mats"]:
                yield 'WARN', f"{len(slots)} material slots (max {prof['max_mats']})", "", ""

    if budget:
        n = len(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices)
        if n > budget:
            yield 'WARN', f"{n} vertices (budget {budget})", "", ""
    mods = [md.name for md in ob.modifiers if md.type != 'ARMATURE' and (md.show_viewport or md.show_render)]
    if mods:
        yield 'INFO', f"Unapplied modifiers: {', '.join(mods)}", "", ""
        if me.shape_keys:
            yield 'WARN', "Shape keys with modifiers (exporters drop shape keys when applying modifiers)", "", ""

    if _is_skinned(ob):
        deform = _deform_groups(ob)
        over = unnorm = unweighted = 0
        for v in me.vertices:
            ws = [g.weight for g in v.groups if g.group in deform and g.weight > 0]
            over += len(ws) > prof["influences"]
            total = sum(ws)
            unweighted += total == 0
            unnorm += total > 0 and abs(total - 1) > 1e-3
        if over:
            yield 'WARN', f"{over} vertices with more than {prof['influences']} bone influences", "limit", ""
        if unnorm:
            yield 'WARN', f"{unnorm} vertices with weights not summing to 1", "normalize", ""
        if unweighted:
            yield 'WARN', f"{unweighted} vertices without bone weights", "", ""
    elif collision and p == 'UNREAL' and not is_col:
        pat = re.compile(r"^(UCX|UBX|USP|UCP)_" + re.escape(ob.name) + r"(_\d+)?$")
        if not any(pat.match(o.name) for o in bpy.data.objects):
            yield 'WARN', f"No collision mesh (UCX_{ob.name}_01)", "", ""


def check_textures(ob, prof, seen):
    for slot in ob.material_slots:
        mat = slot.material
        if not mat or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            img = getattr(node, "image", None) if node.type == 'TEX_IMAGE' else None
            if not img or img.name in seen:
                continue
            seen.add(img.name)
            if _image_missing(img):
                yield 'ERROR', f"Texture file missing: {img.name} ({img.filepath})", "", ""
                continue
            w, h = img.size
            if w and h and not (_pot(w) and _pot(h)):
                yield prof["pot"], f"Texture {img.name} is {w}x{h}, not power of two (no mipmaps/compression)", "", ""


def check_names(obs):
    """Spaces / non-ASCII / clashes after sanitizing for objects, their materials and bones."""
    out = []

    def scan(kind, items, fix):  # items: [(owner object name, name)]
        groups = {}
        for owner, name in items:
            groups.setdefault(sanitize(name).lower(), []).append((owner, name))
            bad = _name_problem(name)
            if bad:
                out.append(('WARN', owner, f"{kind} name '{name}' has {bad}", fix, name))
        for same in groups.values():
            names = sorted({n for _, n in same})
            if len(names) > 1:
                owner, _ = same[0]
                out.append(('WARN', owner, f"{kind} names clash after sanitizing: {', '.join(names)}", "", ""))

    scan("Object", [(o.name, o.name) for o in obs], "rename_ob")
    mats = {}
    for o in obs:
        for s in o.material_slots:
            if s.material:
                mats.setdefault(s.material.name, o.name)
    scan("Material", [(owner, m) for m, owner in mats.items()], "rename_mat")
    for o in obs:
        if o.type == 'ARMATURE':
            scan("Bone", [(o.name, b.name) for b in o.data.bones], "rename_bone")
    return out


def gather(context, s):
    if s.source == 'COLLECTION':
        return list(s.collection.all_objects) if s.collection else []
    return list(context.selected_objects)


def run_checks(context, objects=None):
    s = context.scene.export_preflight
    obs = gather(context, s) if objects is None else objects
    prof = PROFILES[s.profile]
    rows, seen = [], set()
    for ob in obs:
        if ob.mode == 'EDIT':
            ob.update_from_editmode()
        for gen in (check_transform(ob, s.profile, prof),
                    check_mesh(ob, s.profile, prof, s.vert_budget, s.unreal_collision),
                    check_textures(ob, prof, seen)):
            rows += [(sev, ob.name, msg, fix, item) for sev, msg, fix, item in gen]
    rows += check_names(obs)
    rows.sort(key=lambda r: RANK[r[0]])
    _findings[:] = rows
    _checked[:] = [o.name for o in obs]
    _last["profile"] = prof["name"]
    return list(rows)


def recheck(context):
    return run_checks(context, [bpy.data.objects[n] for n in _checked if n in bpy.data.objects])


def report_text(rows=None):
    rows = _findings if rows is None else rows
    lines = [f"Export Preflight ({_last['profile'] or '?'}): {len(_checked)} object(s)"]
    lines += [f"[{sev:5}] {ob}: {msg}" for sev, ob, msg, _, _ in rows]
    lines.append(", ".join(f"{sum(r[0] == k for r in rows)} {k.lower()}" for k in RANK) if rows else "No problems")
    return "\n".join(lines)


# ---------------------------------------------------------------- fixes
# Each fix takes (ob, item); raise ValueError to refuse.

def _apply(ob, **kw):
    with bpy.context.temp_override(selected_editable_objects=[ob], selected_objects=[ob],
                                   active_object=ob, object=ob):
        bpy.ops.object.transform_apply(location=False, isolate_users=True, properties=True, **kw)


def _fix_origin(ob, item):
    if ob.data.users > 1:
        raise ValueError("mesh is shared with other objects")
    bc, _ = _bottom_center(ob)
    mw = ob.matrix_world.copy()
    ob.data.transform(Matrix.Translation(-bc))
    ob.matrix_world = mw @ Matrix.Translation(bc)
    for ch in ob.children:
        ch.matrix_parent_inverse = Matrix.Translation(-bc) @ ch.matrix_parent_inverse


def _fix_loose(ob, item):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.link_faces], context='EDGES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_edges], context='VERTS')
    bm.to_mesh(ob.data)
    bm.free()


def _fix_degenerate(ob, item):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-5, edges=bm.edges[:])
    bm.to_mesh(ob.data)
    bm.free()


def normalize_weights(ob, limit=0):
    """Normalize deform weights per vertex; with limit, drop the smallest influences beyond it first."""
    deform = _deform_groups(ob)
    for v in ob.data.vertices:
        gs = sorted((g for g in v.groups if g.group in deform), key=lambda g: -g.weight)
        if limit and len(gs) > limit:
            for g in gs[limit:]:
                ob.vertex_groups[g.group].remove([v.index])
            gs = sorted((g for g in v.groups if g.group in deform), key=lambda g: -g.weight)
        total = sum(g.weight for g in gs)
        if total > 0:
            for g in gs:
                g.weight /= total


def _rename(owner, item):
    owner.name = sanitize(item)


FIXES = {
    "scale": ("Apply Scale", lambda ob, item: _apply(ob, rotation=False, scale=True)),
    "arm_scale": ("Apply Scale", lambda ob, item: _apply(ob, rotation=False, scale=True)),
    "rotation": ("Apply Rotation", lambda ob, item: _apply(ob, rotation=True, scale=False)),
    "origin": ("Origin to Bottom", _fix_origin),
    "loose": ("Delete Loose", _fix_loose),
    "degenerate": ("Dissolve", _fix_degenerate),
    "normalize": ("Normalize", lambda ob, item: normalize_weights(ob)),
    "limit": ("Limit", lambda ob, item: normalize_weights(
        ob, PROFILES[bpy.context.scene.export_preflight.profile]["influences"])),
    "rename_ob": ("Rename", lambda ob, item: _rename(ob, ob.name)),
    "rename_mat": ("Rename", lambda ob, item: _rename(bpy.data.materials[item], item)),
    "rename_bone": ("Rename", lambda ob, item: _rename(ob.data.bones[item], item)),
}


def apply_fix(fix, obj, item):
    ob = bpy.data.objects.get(obj)
    if ob is None or fix not in FIXES:
        raise ValueError(f"'{obj}' not found")
    if bpy.context.mode != 'OBJECT':
        raise ValueError("switch to Object Mode first")
    FIXES[fix][1](ob, item)


# ---------------------------------------------------------------- operators / UI

class EP_OT_run(bpy.types.Operator):
    bl_idname = "export_preflight.run"
    bl_label = "Check"
    bl_description = "Check the selected objects (or the chosen collection) against the export profile"

    def execute(self, context):
        rows = run_checks(context)
        errs = sum(r[0] == 'ERROR' for r in rows)
        warns = sum(r[0] == 'WARN' for r in rows)
        self.report({'ERROR' if errs else 'WARNING' if warns else 'INFO'},
                    f"Export preflight: {errs} errors, {warns} warnings ({len(_checked)} objects)")
        return {'FINISHED'}


class EP_OT_select(bpy.types.Operator):
    bl_idname = "export_preflight.select"
    bl_label = "Select"
    bl_description = "Select this object and make it active"
    bl_options = {'REGISTER', 'UNDO'}

    obj: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        ob = bpy.data.objects.get(self.obj)
        if ob is None or ob.name not in context.view_layer.objects:
            self.report({'WARNING'}, f"'{self.obj}' is not in this view layer")
            return {'CANCELLED'}
        for o in context.view_layer.objects:
            if o:
                o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


class EP_OT_fix(bpy.types.Operator):
    bl_idname = "export_preflight.fix"
    bl_label = "Fix"
    bl_description = "Apply the suggested fix for this finding, then check again"
    bl_options = {'REGISTER', 'UNDO'}

    fix: bpy.props.StringProperty(options={'SKIP_SAVE'})
    obj: bpy.props.StringProperty(options={'SKIP_SAVE'})
    item: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            apply_fix(self.fix, self.obj, self.item)
        except (ValueError, KeyError, RuntimeError) as e:
            self.report({'WARNING'}, f"{self.obj}: {e}")
            return {'CANCELLED'}
        recheck(context)
        return {'FINISHED'}


class EP_OT_fix_all(bpy.types.Operator):
    bl_idname = "export_preflight.fix_all"
    bl_label = "Fix All Safe"
    bl_description = ("Apply only the non-destructive fixes: apply mesh scale (not on shared meshes) "
                      "and normalize bone weights. Re-checks afterwards")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not _checked:
            run_checks(context)
        done = set()
        for _, obj, _, fix, item in list(_findings):
            ob = bpy.data.objects.get(obj)
            if fix not in SAFE or (fix, obj) in done or ob is None:
                continue
            if fix == "scale" and ob.data.users > 1:
                continue
            try:
                apply_fix(fix, obj, item)
                done.add((fix, obj))
            except (ValueError, RuntimeError) as e:
                self.report({'WARNING'}, f"{obj}: {e}")
        recheck(context)
        self.report({'INFO'}, f"Applied {len(done)} safe fix(es)")
        return {'FINISHED'}


class EP_OT_copy(bpy.types.Operator):
    bl_idname = "export_preflight.copy"
    bl_label = "Copy Report"
    bl_description = "Copy the preflight findings to the clipboard as text"

    def execute(self, context):
        if not _checked:
            run_checks(context)
        context.window_manager.clipboard = report_text()
        self.report({'INFO'}, "Report copied")
        return {'FINISHED'}


MAX_ROWS = 60


class EP_PT_panel(bpy.types.Panel):
    bl_label = "Export Preflight"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Export"

    def draw(self, context):
        s = context.scene.export_preflight
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(s, "profile")
        col.prop(s, "source")
        if s.source == 'COLLECTION':
            col.prop(s, "collection")
        col.prop(s, "vert_budget")
        if s.profile == 'UNREAL':
            col.prop(s, "unreal_collision")
        row = layout.row(align=True)
        row.operator("export_preflight.run", icon='VIEWZOOM')
        row.operator("export_preflight.fix_all", icon='CHECKMARK')
        row.operator("export_preflight.copy", text="", icon='COPYDOWN')
        if not _checked:
            layout.label(text="Not checked yet")
            return
        if not _findings:
            layout.label(text=f"{len(_checked)} object(s): no problems", icon='CHECKMARK')
            return
        col = layout.column(align=True)
        for sev, obj, msg, fix, item in _findings[:MAX_ROWS]:
            row = col.row(align=True)
            row.alert = sev == 'ERROR'
            row.label(text=f"{obj}: {msg}", icon=ICONS[sev])
            row.operator("export_preflight.select", text="", icon='RESTRICT_SELECT_OFF').obj = obj
            if fix:
                op = row.operator("export_preflight.fix", text=FIXES[fix][0])
                op.fix, op.obj, op.item = fix, obj, item
        if len(_findings) > MAX_ROWS:
            col.label(text=f"+{len(_findings) - MAX_ROWS} more (Copy Report for all)")


class EP_Settings(bpy.types.PropertyGroup):
    profile: bpy.props.EnumProperty(
        name="Profile", default='UNITY',
        items=[(k, v["name"], f"{v['name']}: max {v['influences']} bone influences, {v['max_mats']} materials, "
                f"{v['uv_max']} UV maps") for k, v in PROFILES.items()])
    source: bpy.props.EnumProperty(name="Check", items=[
        ('SELECTED', "Selected", "Check the selected objects"),
        ('COLLECTION', "Collection", "Check every object in a collection (including child collections)")])
    collection: bpy.props.PointerProperty(name="Collection", type=bpy.types.Collection)
    vert_budget: bpy.props.IntProperty(
        name="Vertex Budget", default=50000, min=0,
        description="Warn about objects with more vertices (after modifiers) than this. 0 = off")
    unreal_collision: bpy.props.BoolProperty(
        name="Require Collision", default=False,
        description="Warn about static meshes without a UCX_/UBX_/USP_/UCP_<name> collision mesh")


_classes = (EP_Settings, EP_OT_run, EP_OT_select, EP_OT_fix, EP_OT_fix_all, EP_OT_copy, EP_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.export_preflight = bpy.props.PointerProperty(type=EP_Settings)


def unregister():
    del bpy.types.Scene.export_preflight
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    _findings.clear()
    _checked.clear()
