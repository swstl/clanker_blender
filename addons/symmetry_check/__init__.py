"""Symmetry Check: find, select and fix broken mirror symmetry (local axis) with a report."""
import bmesh
import bpy
import numpy as np
from mathutils.kdtree import KDTree

AXES = (('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", ""))


# ---------------------------------------------------------------- pure functions (numpy)

def mirrored(co, axis):
    m = co.copy()
    m[..., axis] *= -1
    return m


def analyze(co, axis, tol, near_factor=10.0):
    """co: (n, 3) array. Returns a dict of per-vertex arrays plus summary numbers.

    mirror[i] = nearest vertex to the mirrored position of i, dist[i] = its distance.
    matched: dist <= tol, near: tol < dist <= tol*near_factor, unmatched: further away.
    centre: |co[axis]| < tol, centre_off: centre but not exactly 0.
    pair: matched or near and mutual (mirror[mirror[i]] == i), the vertices fixes act on."""
    co = np.asarray(co, dtype=np.float64)
    n = len(co)
    kd = KDTree(n)
    for i, c in enumerate(co.tolist()):
        kd.insert(c, i)
    kd.balance()
    found = [kd.find(c) for c in mirrored(co, axis).tolist()]
    mirror = np.array([f[1] for f in found], dtype=np.int64).reshape(n)
    dist = np.array([f[2] for f in found], dtype=np.float64).reshape(n)
    a = np.abs(co[:, axis])
    unmatched = dist > tol * near_factor
    near = ~unmatched & (dist > tol)
    pair = ~unmatched
    pair[pair] = mirror[mirror[pair]] == np.flatnonzero(pair)
    centre = a < tol
    return {
        "mirror": mirror, "dist": dist, "matched": dist <= tol, "near": near, "unmatched": unmatched,
        "pair": pair, "centre": centre, "centre_off": centre & (a > 0),
        "max_dev": float(dist[~unmatched].max()) if (~unmatched).any() else 0.0,
    }


def pairs(res, mask=None):
    """(i, j) index arrays of mutual mirror pairs, each pair once (i <= j; i == j for centre verts).
    mask: optional bool array, keep pairs where either vertex is set."""
    i = np.flatnonzero(res["pair"])
    j = res["mirror"][i]
    keep = i <= j
    if mask is not None:
        keep &= mask[i] | mask[j]
    return i[keep], j[keep]


def fix_average(co, axis, i, j):
    """Move both vertices of each pair to the average of p and mirror(q): exact mirror images."""
    p = (co[i] + mirrored(co[j], axis)) / 2
    co[i] = p
    co[j] = mirrored(p, axis)
    return co


def fix_copy(co, axis, i, j, positive=True):
    """Copy the positive (or negative) side onto the other side. Centre verts get axis = 0."""
    i_pos = co[i, axis] >= co[j, axis]
    src = np.where(i_pos == positive, i, j)
    dst = np.where(i_pos == positive, j, i)
    co[dst] = mirrored(co[src], axis)
    same = src == dst
    co[src[same], axis] = 0.0
    return co


def snap_centre(co, axis, res, mask=None):
    sel = res["centre"] if mask is None else res["centre"] & mask
    co[sel, axis] = 0.0
    return int(sel.sum())


def topology_bad(edges, res):
    """Paired vertices whose edge count differs from their mirror, or that have an edge whose
    mirror edge does not exist."""
    mirror, ok = res["mirror"], res["pair"]
    n = len(mirror)
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    deg = np.bincount(edges.ravel(), minlength=n)
    bad = ok & (deg != deg[mirror])
    a, b = edges[:, 0], edges[:, 1]
    both = ok[a] & ok[b]
    key = np.minimum(a, b) * n + np.maximum(a, b)
    ma, mb = mirror[a], mirror[b]
    mkey = np.minimum(ma, mb) * n + np.maximum(ma, mb)
    missing = both & ~np.isin(mkey, key)
    bad[edges[missing].ravel()] = True
    return bad


def uv_asymmetry(loop_verts, uv, res, tol):
    """Pairs whose UVs are not mirrored around U=0.5. Returns (count of bad verts, max deviation).
    ponytail: one mean UV per vertex, so verts on UV seams average two islands; per-loop
    matching if that misreports."""
    mirror, ok = res["mirror"], res["pair"]
    n = len(mirror)
    uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
    cnt = np.bincount(loop_verts, minlength=n)
    u = np.bincount(loop_verts, uv[:, 0], n) / np.maximum(cnt, 1)
    v = np.bincount(loop_verts, uv[:, 1], n) / np.maximum(cnt, 1)
    i = np.flatnonzero(ok & (cnt > 0))
    j = mirror[i]
    dev = np.maximum(np.abs(u[i] - (1 - u[j])), np.abs(v[i] - v[j]))
    return int((dev > tol).sum()), float(dev.max()) if len(dev) else 0.0


def shape_key_asymmetry(basis, keys, axis, res):
    """keys: [(name, co array)]. Per key, max |delta_i - mirror(delta_j)| over the pairs."""
    i = np.flatnonzero(res["pair"])
    j = res["mirror"][i]
    out = []
    for name, kco in keys:
        d = np.asarray(kco, dtype=np.float64) - basis
        dev = np.linalg.norm(d[i] - mirrored(d[j], axis), axis=1)
        out.append((name, float(dev.max()) if len(dev) else 0.0))
    return out


# ---------------------------------------------------------------- Blender glue

def _get(seq, attr, count, width, dtype=np.float64):
    a = np.empty(count * width, dtype=dtype)
    seq.foreach_get(attr, a)
    return a.reshape(count, width) if width > 1 else a


def read_mesh(ob):
    """Local vertex positions, selection, edges (syncs edit-mode changes first)."""
    if ob.mode == 'EDIT':
        ob.update_from_editmode()
    me = ob.data
    n = len(me.vertices)
    return (_get(me.vertices, "co", n, 3), _get(me.vertices, "select", n, 1, bool),
            _get(me.edges, "vertices", len(me.edges), 2, np.int64))


def write_mesh(ob, co, changed):
    """Write positions of `changed` vertex indices through bmesh (keeps shape keys consistent)."""
    edit = ob.mode == 'EDIT'
    bm = bmesh.from_edit_mesh(ob.data) if edit else bmesh.new()
    if not edit:
        bm.from_mesh(ob.data)
    bm.verts.ensure_lookup_table()
    for k in np.flatnonzero(changed).tolist():
        bm.verts[k].co = co[k]
    if edit:
        bmesh.update_edit_mesh(ob.data, loop_triangles=True, destructive=False)
    else:
        bm.to_mesh(ob.data)
        bm.free()
        ob.data.update()


def settings(context):
    s = context.scene.symmetry_check
    return "XYZ".index(s.axis), s.tolerance, s.near_factor


def full_report(ob, axis, tol, near_factor):
    co, _sel, edges = read_mesh(ob)
    res = analyze(co, axis, tol, near_factor)
    res["topo"] = topology_bad(edges, res)
    me = ob.data
    res["uv"] = None
    if me.uv_layers.active:
        # UV layer data reads as empty in Edit Mode (even after update_from_editmode): use a copy.
        tmp = me.copy() if ob.mode == 'EDIT' else me
        lv = _get(tmp.loops, "vertex_index", len(tmp.loops), 1, np.int64)
        uv = _get(tmp.uv_layers.active.data, "uv", len(tmp.loops), 2)
        if tmp is not me:
            bpy.data.meshes.remove(tmp)
        res["uv"] = uv_asymmetry(lv, uv, res, tol)
    res["keys"] = []
    if me.shape_keys:
        ref = me.shape_keys.reference_key
        basis = _get(ref.data, "co", len(co), 3)
        keys = [(kb.name, _get(kb.data, "co", len(co), 3)) for kb in me.shape_keys.key_blocks if kb != ref]
        res["keys"] = shape_key_asymmetry(basis, keys, axis, res)
    return res


def _mesh_poll(context):
    ob = context.active_object
    return ob is not None and ob.type == 'MESH' and ob.mode in {'OBJECT', 'EDIT'}


class SYMC_OT_analyze(bpy.types.Operator):
    bl_idname = "symmetry_check.analyze"
    bl_label = "Analyze Symmetry"
    bl_description = "Match every vertex with its mirror on the chosen local axis and report problems"

    poll = classmethod(lambda cls, context: _mesh_poll(context))

    def execute(self, context):
        ob = context.active_object
        res = full_report(ob, *settings(context))
        r = context.window_manager.symmetry_check_results
        r.object = ob.name
        r.verts = len(res["mirror"])
        r.pairs = int((res["pair"] & ~res["centre"]).sum())
        r.centre = int(res["centre"].sum())
        for k in ("centre_off", "near", "unmatched", "topo"):
            setattr(r, k, int(res[k].sum()))
        r.max_dev = res["max_dev"]
        r.uv_bad, r.uv_dev = res["uv"] if res["uv"] else (-1, 0.0)
        r.keys.clear()
        for name, dev in res["keys"]:
            it = r.keys.add()
            it.name, it.dev = name, dev
        self.report({'INFO'}, f"{r.unmatched} unmatched, {r.near} near-miss, {r.centre_off} off-centre, "
                              f"{r.topo} topology, max deviation {r.max_dev:.6g}")
        return {'FINISHED'}


SELECT_KINDS = (
    ('unmatched', "Unmatched", "Vertices with no mirror within the near-miss distance"),
    ('near', "Near-miss", "Vertices whose mirror is off by more than the tolerance"),
    ('centre_off', "Centre Off", "Centre-line vertices not exactly on 0"),
    ('topo', "Topology", "Paired vertices whose connectivity differs from their mirror"),
)


class SYMC_OT_select(bpy.types.Operator):
    bl_idname = "symmetry_check.select"
    bl_label = "Select Asymmetric"
    bl_description = "Enter Edit Mode and select only these vertices"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(items=SELECT_KINDS)

    poll = classmethod(lambda cls, context: _mesh_poll(context))

    def execute(self, context):
        ob = context.active_object
        mask = full_report(ob, *settings(context))[self.kind]
        if ob.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        context.tool_settings.mesh_select_mode = (True, False, False)
        bm = bmesh.from_edit_mesh(ob.data)
        for seq in (bm.verts, bm.edges, bm.faces):
            for el in seq:
                el.select = False
        bm.verts.ensure_lookup_table()
        for k in np.flatnonzero(mask).tolist():
            bm.verts[k].select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        self.report({'INFO'}, f"Selected {int(mask.sum())}")
        return {'FINISHED'}


class SYMC_OT_fix(bpy.types.Operator):
    bl_idname = "symmetry_check.fix"
    bl_label = "Fix Symmetry"
    bl_description = "Snap vertices so the mesh becomes exactly symmetric"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(items=(
        ('CENTRE', "Snap Centre", "Set centre-line vertices to exactly 0 on the axis"),
        ('AVERAGE', "Average", "Move each mirror pair to the average of both sides"),
        ('POS_TO_NEG', "+ to -", "Copy the positive side onto the negative side"),
        ('NEG_TO_POS', "- to +", "Copy the negative side onto the positive side"),
    ))
    only_selected: bpy.props.BoolProperty(name="Only Selected", description="Only fix selected vertices "
                                          "(a pair is fixed when either side is selected)")

    poll = classmethod(lambda cls, context: _mesh_poll(context))

    def execute(self, context):
        ob = context.active_object
        axis, tol, near_factor = settings(context)
        co, sel, _edges = read_mesh(ob)
        res = analyze(co, axis, tol, near_factor)
        mask = sel if self.only_selected else None
        before = co.copy()
        if self.mode == 'CENTRE':
            snap_centre(co, axis, res, mask)
        else:
            i, j = pairs(res, mask)
            if self.mode == 'AVERAGE':
                fix_average(co, axis, i, j)
            else:
                fix_copy(co, axis, i, j, positive=self.mode == 'POS_TO_NEG')
        changed = (co != before).any(axis=1)
        write_mesh(ob, co, changed)
        bpy.ops.symmetry_check.analyze()
        self.report({'INFO'}, f"Moved {int(changed.sum())} vertices")
        return {'FINISHED'}


class SYMC_Key(bpy.types.PropertyGroup):
    dev: bpy.props.FloatProperty()


class SYMC_Results(bpy.types.PropertyGroup):
    object: bpy.props.StringProperty()
    verts: bpy.props.IntProperty()
    pairs: bpy.props.IntProperty()
    centre: bpy.props.IntProperty()
    centre_off: bpy.props.IntProperty()
    near: bpy.props.IntProperty()
    unmatched: bpy.props.IntProperty()
    topo: bpy.props.IntProperty()
    max_dev: bpy.props.FloatProperty()
    uv_bad: bpy.props.IntProperty(default=-1)
    uv_dev: bpy.props.FloatProperty()
    keys: bpy.props.CollectionProperty(type=SYMC_Key)


class SYMC_Settings(bpy.types.PropertyGroup):
    axis: bpy.props.EnumProperty(name="Axis", items=AXES, description="Local mirror axis")
    tolerance: bpy.props.FloatProperty(name="Tolerance", default=1e-4, min=0.0, precision=5, step=0.001,
                                       subtype='DISTANCE', description="Max distance to count as symmetric")
    near_factor: bpy.props.FloatProperty(name="Near-miss ×", default=10.0, min=1.0,
                                         description="Mirrors within tolerance × this are near-misses (fixable)")
    only_selected: bpy.props.BoolProperty(name="Only Selected", description="Fixes only touch selected vertices")


class SYMC_PT_panel(bpy.types.Panel):
    bl_label = "Symmetry"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    poll = classmethod(lambda cls, context: _mesh_poll(context))

    def draw(self, context):
        s = context.scene.symmetry_check
        layout = self.layout
        layout.row().prop(s, "axis", expand=True)
        col = layout.column(align=True)
        col.prop(s, "tolerance")
        col.prop(s, "near_factor")
        layout.operator("symmetry_check.analyze", icon='VIEWZOOM')
        r = context.window_manager.symmetry_check_results
        if r.object != context.active_object.name:
            return
        col = layout.column(align=True)
        col.label(text=f"{r.pairs} pairs, {r.centre} centre of {r.verts}")
        col.label(text=f"Max deviation: {r.max_dev:.6g}")
        for kind, label, _d in SELECT_KINDS:
            n = getattr(r, kind)
            row = col.row(align=True)
            row.alert = n > 0
            row.label(text=f"{label}: {n}")
            sub = row.row(align=True)
            sub.enabled = n > 0
            sub.operator("symmetry_check.select", text="", icon='RESTRICT_SELECT_OFF').kind = kind
        if r.uv_bad >= 0:
            row = col.row()
            row.alert = r.uv_bad > 0
            row.label(text=f"UV asymmetric: {r.uv_bad} (max {r.uv_dev:.4g})", icon='UV')
        for k in r.keys:
            row = col.row()
            row.alert = k.dev > s.tolerance
            row.label(text=f"{k.name}: {k.dev:.4g}", icon='SHAPEKEY_DATA')

        box = layout.box()
        box.label(text="Fix", icon='TOOL_SETTINGS')
        box.prop(s, "only_selected")
        a = s.axis
        for mode, text in (('CENTRE', "Snap Centre to 0"), ('AVERAGE', "Average Pairs"),
                           ('POS_TO_NEG', f"+{a} → -{a}"), ('NEG_TO_POS', f"-{a} → +{a}")):
            op = box.operator("symmetry_check.fix", text=text)
            op.mode = mode
            op.only_selected = s.only_selected


_classes = (SYMC_Key, SYMC_Results, SYMC_Settings, SYMC_OT_analyze, SYMC_OT_select, SYMC_OT_fix,
            SYMC_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.symmetry_check_results = bpy.props.PointerProperty(type=SYMC_Results)
    bpy.types.Scene.symmetry_check = bpy.props.PointerProperty(type=SYMC_Settings)


def unregister():
    del bpy.types.Scene.symmetry_check
    del bpy.types.WindowManager.symmetry_check_results
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
