"""Hair Tools: mirror hair curves, convert them to hair cards, batch helpers for Curves hair."""
import random

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

AXES = [('X', "X", "Mirror across the surface's local X"),
        ('Y', "Y", "Mirror across the surface's local Y"),
        ('Z', "Z", "Mirror across the surface's local Z")]


# ---------------------------------------------------------------- data access

def _key_dtype(a):
    t = a.data_type
    if t in {'FLOAT_VECTOR', 'FLOAT2'}:
        return 'vector', np.float32
    if t in {'FLOAT_COLOR', 'BYTE_COLOR'}:
        return 'color', np.float32
    if t == 'BOOLEAN':
        return 'value', bool
    if t in {'INT', 'INT8', 'INT16_2D', 'INT32_2D'}:
        return 'value', np.int32
    if t == 'STRING':
        return None, None
    return 'value', np.float32  # FLOAT, FLOAT4, QUATERNION, FLOAT4X4


def read_attr(a):
    """Attribute values as (n, components) array, or None for unsupported types."""
    key, dt = _key_dtype(a)
    n = len(a.data)
    if key is None or n == 0:
        return None
    comp = np.asarray(getattr(a.data[0], key)).size
    buf = np.empty(n * comp, dtype=dt)
    a.data.foreach_get(key, buf)
    return buf.reshape(n, comp)


def write_attr(a, arr):
    a.data.foreach_set(_key_dtype(a)[0], np.ascontiguousarray(arr).ravel())


def offsets(c):
    off = np.empty(len(c.curves) + 1, dtype=np.int32)
    c.curve_offset_data.foreach_get("value", off)
    return off


def positions(c):
    v = read_attr(c.attributes["position"])
    return np.zeros((0, 3)) if v is None else v.astype(np.float64)


def selected_curves(c):
    """Curve indices selected in Sculpt Curves mode (no '.selection' attribute = everything)."""
    n = len(c.curves)
    a = c.attributes.get(".selection")
    if a is None or n == 0:
        return np.arange(n)
    v = read_attr(a)[:, 0] > 0
    if a.domain == 'POINT':
        v = np.logical_or.reduceat(v, offsets(c)[:-1])
    return np.flatnonzero(v)


def _point_index(off, curves):
    """Point indices of `curves`, concatenated in order."""
    if not len(curves):
        return np.empty(0, dtype=np.int64)
    return np.concatenate([np.arange(off[i], off[i + 1]) for i in curves])


def _xform(m, pts):
    m = np.array(m)
    return pts @ m[:3, :3].T + m[:3, 3]


def curve_lengths(c):
    pos, off = positions(c), offsets(c)
    seg = np.linalg.norm(np.diff(pos, axis=0), axis=1)
    seg[off[1:-1] - 1] = 0.0  # no segment between the end of one curve and the start of the next
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    return cum[off[1:] - 1] - cum[off[:-1]]


# ---------------------------------------------------------------- mirror

def _space(ob):
    """Curves-local -> mirror-space matrix (the surface's object space if there is one)."""
    surf = ob.data.surface
    space = surf.matrix_world if surf else ob.matrix_world
    return space.inverted() @ ob.matrix_world


def snap_to_surface(context, ob, curves=None):
    """Attach curve roots to the surface: native operator (also sets surface_uv_coordinate),
    BVH fallback moving only `curves` (no UVs)."""
    c = ob.data
    surf = c.surface
    if surf is None or surf.type != 'MESH':
        return False
    try:
        with context.temp_override(object=ob, active_object=ob, selected_objects=[ob],
                                   selected_editable_objects=[ob]):
            if 'FINISHED' in bpy.ops.curves.snap_curves_to_surface(attach_mode='NEAREST'):
                return True
    except RuntimeError:
        pass
    snap_bvh(context, ob, curves)
    return True


def snap_bvh(context, ob, curves=None):
    """Move each curve so its root sits on the closest surface point (no surface UVs)."""
    c = ob.data
    surf = c.surface
    bvh = BVHTree.FromObject(surf, context.evaluated_depsgraph_get())
    to_s = _space(ob)
    from_s = to_s.inverted()
    pos, off = positions(c), offsets(c)
    for i in (range(len(c.curves)) if curves is None else curves):
        root = to_s @ Vector(pos[off[i]])
        hit = bvh.find_nearest(root)[0]
        if hit is not None:
            pos[off[i]:off[i + 1]] += np.array(from_s.to_3x3() @ (hit - root))
    write_attr(c.attributes["position"], pos.astype(np.float32))
    c.update_tag()


def mirror_curves(context, ob, axis='X', side='BOTH', replace=False, dedupe=True, tol=1e-3, snap=True):
    """Add mirrored copies of the selected curves of hair object `ob`. Returns the number added."""
    c = ob.data
    ax = "XYZ".index(axis)
    to_s = _space(ob)
    mirror = Matrix.Diagonal([-1.0 if i == ax else 1.0 for i in range(3)]).to_4x4()
    m = to_s.inverted() @ mirror @ to_s

    def root_coord():
        off = offsets(c)
        return _xform(to_s, positions(c)[off[:-1]])[:, ax]

    sign = {'POSITIVE': 1.0, 'NEGATIVE': -1.0}.get(side)
    if replace and sign and len(c.curves):
        gone = np.flatnonzero(root_coord() * sign < -tol)
        if len(gone):
            c.remove_curves(indices=gone.tolist())
    if not len(c.curves):
        return 0
    off, pos = offsets(c), positions(c)
    src = selected_curves(c)
    if sign:
        src = src[root_coord()[src] * sign > tol]
    new_roots = _xform(m, pos[off[src]])
    if dedupe and len(src):
        kd = KDTree(len(off) - 1)
        for i, p in enumerate(pos[off[:-1]]):
            kd.insert(p, i)
        kd.balance()
        keep = [kd.find(Vector(p))[2] > tol for p in new_roots]
        src = src[np.array(keep, dtype=bool)]
    if not len(src):
        return 0

    n_curves, n_points = len(c.curves), len(c.points)
    pidx = _point_index(off, src)
    old = [(a.name, a.domain, read_attr(a)) for a in c.attributes
           if a.domain in {'POINT', 'CURVE'} and not a.is_internal]
    c.add_curves(sizes=(off[src + 1] - off[src]).tolist())
    for name, domain, vals in old:
        a = c.attributes.get(name)
        if a is None or vals is None:
            continue
        full = read_attr(a)
        if domain == 'POINT':
            part = vals[pidx]
            if name == "position":
                part = _xform(m, part.astype(np.float64)).astype(np.float32)
            full[n_points:] = part
        else:
            full[n_curves:] = vals[src]
        write_attr(a, full)
    c.update_tag()
    if snap:
        snap_to_surface(context, ob, range(n_curves, len(c.curves)))
    return len(src)


# ---------------------------------------------------------------- cards

def _resample(pts, n):
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    if cum[-1] <= 0:
        return np.repeat(pts[:1], n, axis=0)
    t = np.linspace(0.0, cum[-1], n)
    return np.stack([np.interp(t, cum, pts[:, k]) for k in range(3)], axis=1)


def _perp(t):
    return t.cross(Vector((1, 0, 0)) if abs(t.x) < 0.9 else Vector((0, 1, 0))).normalized()


def make_cards(context, ob, width_root=0.02, width_tip=0.005, twist=0.0, facing='SURFACE', resample=0,
               step=1, columns=4, random_columns=True, seed=0, selected_only=True):
    """Build (or rebuild) the mesh object '<name>_cards': one UV-mapped ribbon per hair curve."""
    c = ob.data
    off, pos = offsets(c), positions(c)
    curves = selected_curves(c) if selected_only else np.arange(len(c.curves))
    curves = curves[::max(1, step)]
    surf = c.surface if c.surface and c.surface.type == 'MESH' else None
    if surf:
        surf_eval = surf.evaluated_get(context.evaluated_depsgraph_get())
        to_s = surf.matrix_world.inverted() @ ob.matrix_world
        n_back = (to_s.inverted().to_3x3())  # surface-local normal -> curves-local (rotation part)
    cam = context.scene.camera
    cam_pos = ob.matrix_world.inverted() @ cam.matrix_world.translation if cam else None
    rng = random.Random(seed)

    verts, faces, uvs = [], [], []
    for k, ci in enumerate(curves):
        pts = pos[off[ci]:off[ci + 1]]
        if len(pts) < 2:
            continue
        if resample >= 2:
            pts = _resample(pts, resample)
        pts = [Vector(p) for p in pts]
        n = len(pts)
        col = rng.randrange(columns) if random_columns else k % columns
        u0, u1 = col / columns, (col + 1) / columns
        up = Vector((0, 0, 1))
        if surf:
            ok, _loc, nrm, _i = surf_eval.closest_point_on_mesh(to_s @ pts[0])
            if ok:
                up = (n_back @ nrm).normalized()
        base, prev = len(verts), None
        for i, p in enumerate(pts):
            t = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized()
            face = (cam_pos - p).normalized() if (facing == 'CAMERA' and cam_pos is not None) else up
            side = t.cross(face)
            side = side.normalized() if side.length > 1e-5 else (_perp(t) if prev is None else prev)
            if prev is not None and side.dot(prev) < 0:
                side = -side
            prev = side
            f = i / (n - 1)
            s = Quaternion(t, twist * f) @ side if twist else side
            half = s * (0.5 * (width_root + (width_tip - width_root) * f))
            verts += [p - half, p + half]
            uvs += [(u0, f), (u1, f)]
        faces += [(base + 2 * i, base + 2 * i + 1, base + 2 * i + 3, base + 2 * i + 2) for i in range(n - 1)]

    name = f"{ob.name}_cards"
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    uv = me.uv_layers.new(name="UVMap")
    if faces:
        lv = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", lv)
        uv.data.foreach_set("uv", np.array(uvs, dtype=np.float32)[lv].ravel())
    mats = [m for m in c.materials if m] or [bpy.data.materials.get("HairCards") or bpy.data.materials.new("HairCards")]
    for mat in mats:
        me.materials.append(mat)
    me.update()

    out = bpy.data.objects.get(name)
    if out and out.type == 'MESH':
        old, out.data = out.data, me
        if old.users == 0:
            bpy.data.meshes.remove(old)
        me.name = name
    else:
        out = bpy.data.objects.new(name, me)
        (ob.users_collection[0] if ob.users_collection else context.collection).objects.link(out)
    out.matrix_world = ob.matrix_world
    return out


# ---------------------------------------------------------------- helpers

def set_radius(ob, root, tip, selected_only=True):
    c = ob.data
    off = offsets(c)
    a = c.attributes.get("radius") or c.attributes.new("radius", 'FLOAT', 'POINT')
    rad = read_attr(a)[:, 0]
    for i in (selected_curves(c) if selected_only else range(len(c.curves))):
        rad[off[i]:off[i + 1]] = np.linspace(root, tip, off[i + 1] - off[i])
    write_attr(a, rad)
    c.update_tag()


def delete_short(ob, min_length, selected_only=True):
    c = ob.data
    if not len(c.curves):
        return 0
    cand = selected_curves(c) if selected_only else np.arange(len(c.curves))
    gone = cand[curve_lengths(c)[cand] < min_length]
    if len(gone):
        c.remove_curves(indices=gone.tolist())
        c.update_tag()
    return len(gone)


# ---------------------------------------------------------------- operators

class _HairOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'CURVES' and context.mode in {'OBJECT', 'SCULPT_CURVES'}


SELECTED_ONLY = bpy.props.BoolProperty(
    name="Selected Only", default=True,
    description="Use the curves selected in Sculpt Curves mode (all curves when nothing is masked)")


class HAIR_OT_mirror(_HairOp, bpy.types.Operator):
    bl_idname = "hair_tools.mirror"
    bl_label = "Mirror Hair"
    bl_description = ("Add mirrored copies of the selected hair curves across the surface object's local axis "
                      "and re-attach their roots to the surface")

    axis: bpy.props.EnumProperty(name="Axis", items=AXES, default='X')
    side: bpy.props.EnumProperty(name="Source Side", default='BOTH', items=[
        ('BOTH', "Both", "Mirror selected curves from both sides"),
        ('POSITIVE', "+ to -", "Mirror only curves rooted on the positive side"),
        ('NEGATIVE', "- to +", "Mirror only curves rooted on the negative side")])
    replace: bpy.props.BoolProperty(
        name="Replace Other Side", default=False,
        description="Delete curves rooted on the target side first (needs a source side)")
    dedupe: bpy.props.BoolProperty(
        name="Skip Duplicates", default=True,
        description="Skip mirrored curves whose root lands on an existing root")
    tolerance: bpy.props.FloatProperty(name="Tolerance", default=0.001, min=0.0, subtype='DISTANCE',
                                       description="Distance for duplicate roots and the mirror plane")
    snap: bpy.props.BoolProperty(name="Snap to Surface", default=True,
                                 description="Re-attach mirrored roots to the surface")

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        lay.prop(self, "axis", expand=True)
        lay.prop(self, "side")
        row = lay.row()
        row.active = self.side != 'BOTH'
        row.prop(self, "replace")
        lay.prop(self, "dedupe")
        lay.prop(self, "tolerance")
        lay.prop(self, "snap")

    def execute(self, context):
        n = mirror_curves(context, context.active_object, self.axis, self.side, self.replace,
                          self.dedupe, self.tolerance, self.snap)
        self.report({'INFO'}, f"Added {n} mirrored curves")
        return {'FINISHED'}


class HAIR_OT_cards(_HairOp, bpy.types.Operator):
    bl_idname = "hair_tools.cards"
    bl_label = "Hair Cards"
    bl_description = ("Convert hair curves into a game-ready card mesh '<name>_cards': one tapered ribbon per "
                      "curve, UVs on a vertical strip of a hair atlas")

    selected_only: SELECTED_ONLY
    step: bpy.props.IntProperty(name="Every Nth Curve", default=1, min=1,
                                description="Use only every Nth curve as a guide (1 = all)")
    resample: bpy.props.IntProperty(name="Points", default=8, min=0, max=256,
                                    description="Points along each card (0 = keep the curve's points)")
    width_root: bpy.props.FloatProperty(name="Root Width", default=0.02, min=0.0, subtype='DISTANCE')
    width_tip: bpy.props.FloatProperty(name="Tip Width", default=0.005, min=0.0, subtype='DISTANCE')
    twist: bpy.props.FloatProperty(name="Twist", default=0.0, subtype='ANGLE',
                                   description="Rotation of the card around the curve from root to tip")
    facing: bpy.props.EnumProperty(name="Facing", default='SURFACE', items=[
        ('SURFACE', "Surface", "Cards lie flat along the surface normal at the root"),
        ('CAMERA', "Camera", "Cards face the scene camera")])
    columns: bpy.props.IntProperty(name="Atlas Columns", default=4, min=1, max=64,
                                   description="Vertical strips in the hair texture atlas")
    random_columns: bpy.props.BoolProperty(name="Random Column", default=True,
                                           description="Random atlas strip per card (else cycle through them)")
    seed: bpy.props.IntProperty(name="Seed", default=0, min=0)

    def execute(self, context):
        ob = context.active_object
        out = make_cards(context, ob, self.width_root, self.width_tip, self.twist, self.facing, self.resample,
                         self.step, self.columns, self.random_columns, self.seed, self.selected_only)
        self.report({'INFO'}, f"{out.name}: {len(out.data.polygons)} faces")
        return {'FINISHED'}


class HAIR_OT_radius(_HairOp, bpy.types.Operator):
    bl_idname = "hair_tools.set_radius"
    bl_label = "Set Radius"
    bl_description = "Set the radius of the selected hair curves, blending from root to tip"

    selected_only: SELECTED_ONLY
    root: bpy.props.FloatProperty(name="Root", default=0.005, min=0.0, subtype='DISTANCE')
    tip: bpy.props.FloatProperty(name="Tip", default=0.001, min=0.0, subtype='DISTANCE')

    def execute(self, context):
        set_radius(context.active_object, self.root, self.tip, self.selected_only)
        return {'FINISHED'}


class HAIR_OT_delete_short(_HairOp, bpy.types.Operator):
    bl_idname = "hair_tools.delete_short"
    bl_label = "Delete Short Curves"
    bl_description = "Delete hair curves shorter than a length"

    selected_only: SELECTED_ONLY
    length: bpy.props.FloatProperty(name="Min Length", default=0.01, min=0.0, subtype='DISTANCE')

    def execute(self, context):
        n = delete_short(context.active_object, self.length, self.selected_only)
        self.report({'INFO'}, f"Deleted {n} curves")
        return {'FINISHED'}


class HAIR_PT_panel(bpy.types.Panel):
    bl_label = "Hair Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Hair"

    @classmethod
    def poll(cls, context):
        return _HairOp.poll(context)

    def draw(self, context):
        lay = self.layout
        c = context.active_object.data
        col = lay.column(align=True)
        col.label(text=f"Curves: {len(c.curves):,}   Points: {len(c.points):,}", icon='CURVES')
        sel = c.attributes.get(".selection")
        if sel is not None and len(c.curves):
            col.label(text=f"Selected: {len(selected_curves(c)):,}")
        if c.surface is None:
            col.label(text="No surface object", icon='ERROR')
        lay.label(text="Mirror")
        row = lay.row(align=True)
        for ax in "XYZ":
            row.operator("hair_tools.mirror", text=ax).axis = ax
        lay.operator("hair_tools.cards", icon='MESH_GRID')
        col = lay.column(align=True)
        col.operator("hair_tools.set_radius", icon='PROP_CON')
        col.operator("hair_tools.delete_short", icon='TRASH')


_classes = (HAIR_OT_mirror, HAIR_OT_cards, HAIR_OT_radius, HAIR_OT_delete_short, HAIR_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
