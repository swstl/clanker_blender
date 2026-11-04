"""Print Split: cut meshes with a helper plane into capped, watertight parts, add peg/hole
connectors with print tolerance, and check parts against a printer bed and for manifoldness.

Sidebar (N) > Print. Lengths are stored in real millimetres and shown in scene units, so they
keep their physical size whatever the scene unit scale is.
"""
from itertools import permutations

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

BEDS = {  # id: (label, x, y, z) in mm
    'ENDER': ("220 x 220 x 250 (Ender 3, i3)", 220, 220, 250),
    'BAMBU': ("256 x 256 x 256 (Bambu X1/P1)", 256, 256, 256),
    'MINI': ("180 x 180 x 180 (Bambu A1 mini)", 180, 180, 180),
    'PRUSA': ("250 x 210 x 220 (Prusa MK4)", 250, 210, 220),
    'LARGE': ("300 x 300 x 400 (CR-10, Neptune)", 300, 300, 400),
}


# ---------------------------------------------------------------- settings

def mm_prop(key, mm, name, desc):
    """Length property backed by a millimetre value, displayed in scene units."""
    def get(self):
        return self.get(key, mm) / 1000 / self.id_data.unit_settings.scale_length

    def set_(self, v):
        self[key] = v * 1000 * self.id_data.unit_settings.scale_length

    return bpy.props.FloatProperty(name=name, description=desc, subtype='DISTANCE', unit='LENGTH',
                                   min=0, get=get, set=set_)


def apply_bed_preset(self, context):
    if self.bed_preset in BEDS:
        _, self["bed_x"], self["bed_y"], self["bed_z"] = BEDS[self.bed_preset]


class PS_Settings(bpy.types.PropertyGroup):
    plane: bpy.props.PointerProperty(name="Cut Plane", type=bpy.types.Object,
                                     description="Plane object used for cutting (its local Z is the normal)")
    delete_original: bpy.props.BoolProperty(name="Delete Original", default=False,
                                            description="Delete the original instead of hiding it")
    connectors: bpy.props.BoolProperty(name="Connectors", default=True,
                                       description="Add pegs to part A and matching holes to part B")
    peg_shape: bpy.props.EnumProperty(name="Shape", items=[('CYLINDER', "Cylinder", ""), ('SQUARE', "Square", "")])
    peg_count: bpy.props.IntProperty(name="Count", default=2, min=1, max=32,
                                     description="Number of pegs (fewer if the cut face is too small)")
    peg_radius: mm_prop("peg_radius", 2.5, "Radius", "Peg radius (half width for square pegs)")
    peg_length: mm_prop("peg_length", 5.0, "Length", "How far the peg sticks out of part A")
    tolerance: mm_prop("tolerance", 0.2, "Tolerance", "Clearance added to hole radius and depth")
    margin: mm_prop("margin", 2.0, "Edge Margin", "Minimum wall between a hole and the cut outline")
    bed_preset: bpy.props.EnumProperty(name="Printer", update=apply_bed_preset, default='ENDER',
                                       items=[(k, v[0], "") for k, v in BEDS.items()] + [('CUSTOM', "Custom", "")])
    bed_x: mm_prop("bed_x", 220, "Bed X", "Printable width")
    bed_y: mm_prop("bed_y", 220, "Bed Y", "Printable depth")
    bed_z: mm_prop("bed_z", 250, "Bed Z", "Printable height")


# ---------------------------------------------------------------- core

def plane_of(plane_ob):
    m = plane_ob.matrix_world
    return m.translation.copy(), (m.to_3x3() @ Vector((0, 0, 1))).normalized()


def capped_half(ob, depsgraph, co, no, positive):
    """BMesh (local space) of the evaluated mesh on one side of the world plane, cut face filled.
    Also returns the cut outline as world-space segments."""
    mw = ob.matrix_world
    co_l = mw.inverted() @ co
    no_l = (mw.to_3x3().transposed() @ no).normalized()
    bm = bmesh.new()
    bm.from_object(ob, depsgraph)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], dist=1e-6,
                           plane_co=co_l, plane_no=no_l, clear_inner=positive, clear_outer=not positive)
    edges = [e for e in bm.edges if e.is_boundary]
    segs = [(mw @ e.verts[0].co, mw @ e.verts[1].co) for e in edges]
    if edges:
        # triangle_fill (scanfill) handles nested outlines, e.g. ring-shaped cuts.
        bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=True, edges=edges, normal=no_l)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    return bm, segs


def place_pegs(segs, co, no, count, clear, spacing):
    """Pick up to `count` points inside the cut outline (even-odd rule, so holes are respected)
    at least `clear` from its edges and `spacing` from each other (farthest-point sampling)."""
    if not segs:
        return []
    u = no.orthogonal().normalized()
    v = no.cross(u)
    s = np.array([[((p - co).dot(u), (p - co).dot(v)) for p in e] for e in segs])
    a, d = s[:, 0], s[:, 1] - s[:, 0]
    lo, hi = s.reshape(-1, 2).min(0), s.reshape(-1, 2).max(0)
    step = max((hi - lo).max() / 40, 1e-9)
    gx, gy = np.meshgrid(np.arange(lo[0] + step / 2, hi[0], step), np.arange(lo[1] + step / 2, hi[1], step))
    pts = np.stack([gx.ravel(), gy.ravel()], 1)
    l2 = np.maximum((d * d).sum(1), 1e-30)
    inside, dist = [], []
    for i in range(0, len(pts), 256):  # chunks keep memory low on dense cuts
        p = pts[i:i + 256, None, :]
        px, py = p[..., 0], p[..., 1]
        crosses = (a[:, 1] > py) != (s[:, 1, 1] > py)
        with np.errstate(divide='ignore', invalid='ignore'):
            xint = a[:, 0] + (py - a[:, 1]) * d[:, 0] / d[:, 1]
        inside.append((crosses & (px < xint)).sum(1) % 2 == 1)
        t = np.clip(((p - a) * d).sum(2) / l2, 0, 1)
        dist.append(np.linalg.norm(p - (a + t[..., None] * d), axis=2).min(1))
    inside, dist = np.concatenate(inside), np.concatenate(dist)
    ok = inside & (dist >= clear)
    pts, dist = pts[ok], dist[ok]
    if not len(pts):
        return []
    # One peg: deepest point. More: start at the extreme point so pegs spread to the outline.
    first = dist.argmax() if count == 1 else np.linalg.norm(pts - pts.mean(0), axis=1).argmax()
    picked = [pts[first]]
    mind = np.full(len(pts), np.inf)
    while len(picked) < count:
        mind = np.minimum(mind, np.linalg.norm(pts - picked[-1], axis=1))
        i = mind.argmax()
        if mind[i] < spacing:
            break
        picked.append(pts[i])
    return [co + u * x + v * y for x, y in picked]


def peg_mesh(points, axis, radius, z0, z1, shape, segments=32):
    """Pegs as one world-space mesh: prisms along `axis` from z0 to z1 (relative to each point)."""
    bm = bmesh.new()
    rot = axis.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    for p in points:
        mat = Matrix.Translation(p + axis * ((z0 + z1) / 2)) @ rot
        if shape == 'SQUARE':
            bmesh.ops.create_cube(bm, size=1, matrix=mat @ Matrix.Diagonal((2 * radius, 2 * radius, z1 - z0, 1)))
        else:
            bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius, radius2=radius,
                                  depth=z1 - z0, matrix=mat)
    me = bpy.data.meshes.new("PrintSplitPeg")
    bm.to_mesh(me)
    bm.free()
    return me


def apply_boolean(ob, cutter_mesh, operation):
    """Apply an EXACT boolean with a temporary cutter, without operators."""
    scene = bpy.context.scene
    cutter = bpy.data.objects.new("PrintSplitCutter", cutter_mesh)
    scene.collection.objects.link(cutter)
    md = ob.modifiers.new("PrintSplit", 'BOOLEAN')
    md.operation, md.solver, md.object = operation, 'EXACT', cutter
    new = bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    ob.modifiers.remove(md)
    old, ob.data = ob.data, new
    new.name = old.name
    bpy.data.meshes.remove(old)
    bpy.data.objects.remove(cutter)
    bpy.data.meshes.remove(cutter_mesh)


def add_connectors(a, b, segs, co, no, s):
    """Pegs grow from part A (positive side) into B's side; B gets clearance holes. Returns count."""
    clear = s.peg_radius * (2 ** 0.5 if s.peg_shape == 'SQUARE' else 1) + s.tolerance + s.margin
    # ponytail: only the outline is checked, not B's thickness behind the cut; deep holes in thin parts
    # can break through. Raycast along the axis if that bites.
    pts = place_pegs(segs, co, no, s.peg_count, clear, 2 * clear)
    if pts:
        axis = -no
        apply_boolean(a, peg_mesh(pts, axis, s.peg_radius, -s.peg_length / 4, s.peg_length, s.peg_shape), 'UNION')
        r = s.peg_radius + s.tolerance
        apply_boolean(b, peg_mesh(pts, axis, r, -s.tolerance, s.peg_length + s.tolerance, s.peg_shape), 'DIFFERENCE')
    return len(pts)


def split_object(ob, plane_ob, context, settings=None):
    """Split ob by the plane into "<name>_A" (plane normal side) and "<name>_B". Returns (A, B, pegs)
    or None when the plane misses the mesh."""
    co, no = plane_of(plane_ob)
    dg = context.evaluated_depsgraph_get()
    halves = [capped_half(ob, dg, co, no, side) for side in (True, False)]
    if not all(bm.faces for bm, _ in halves):
        for bm, _ in halves:
            bm.free()
        return None
    parts = []
    for (bm, _), suffix in zip(halves, ("_A", "_B")):
        me = bpy.data.meshes.new(ob.name + suffix)
        bm.to_mesh(me)
        bm.free()
        for m in ob.data.materials:
            me.materials.append(m)
        part = bpy.data.objects.new(ob.name + suffix, me)
        part.matrix_world = ob.matrix_world.copy()
        for c in ob.users_collection:
            c.objects.link(part)
        parts.append(part)
    pegs = add_connectors(*parts, halves[0][1], co, no, settings) if settings and settings.connectors else 0
    return parts[0], parts[1], pegs


def non_manifold_count(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    n = sum(not e.is_manifold for e in bm.edges)
    bm.free()
    return n


def world_size(ob):
    pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    return [max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3)]


def bed_fit(size, bed):
    """Axis order (permutation of 0..2) that fits size into bed, trying 90 degree turns; None if none.
    (0, 1, 2) means it fits as is."""
    return next((p for p in permutations(range(3)) if all(size[p[i]] <= bed[i] + 1e-9 for i in range(3))), None)


# ---------------------------------------------------------------- operators

class PS_OT_add_plane(bpy.types.Operator):
    bl_idname = "print_split.add_plane"
    bl_label = "Add Cut Plane"
    bl_description = "Add a wire cut plane at the active object's center or the 3D cursor; move/rotate it, then Split"
    bl_options = {'REGISTER', 'UNDO'}

    orient: bpy.props.EnumProperty(name="Normal", default='Z', items=[
        ('X', "X", "Cut across X"), ('Y', "Y", "Cut across Y"), ('Z', "Z", "Horizontal cut"),
        ('VIEW', "View", "Plane faces the viewer")])
    at: bpy.props.EnumProperty(name="Location", items=[
        ('OBJECT', "Object Center", "Bounding box center of the active object"), ('CURSOR', "3D Cursor", "")])

    def execute(self, context):
        target = context.active_object if context.active_object and context.active_object.type == 'MESH' else None
        if self.at == 'OBJECT' and target:
            loc = sum((target.matrix_world @ Vector(c) for c in target.bound_box), Vector()) / 8
            size = max(world_size(target)) * 1.5 or 2.0
        else:
            loc, size = context.scene.cursor.location.copy(), (max(world_size(target)) * 1.5 if target else 2.0)
        if self.orient == 'VIEW':
            rv3d = getattr(context, "region_data", None)
            rot = rv3d.view_rotation.copy() if rv3d else Matrix().to_quaternion()
        else:
            rot = Vector([float(self.orient == k) for k in "XYZ"]).to_track_quat('Z', 'Y')
        h = size / 2
        me = bpy.data.meshes.new("SplitPlane")
        me.from_pydata([(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)], [], [(0, 1, 2, 3)])
        plane = bpy.data.objects.new("SplitPlane", me)
        plane.matrix_world = Matrix.LocRotScale(loc, rot, None)
        plane.display_type = 'WIRE'
        plane.show_in_front = True
        plane.hide_render = True
        if target:
            plane["print_split_target"] = target
        context.collection.objects.link(plane)
        for o in context.selected_objects:
            o.select_set(False)
        plane.select_set(True)
        context.view_layer.objects.active = plane
        context.scene.print_split.plane = plane
        return {'FINISHED'}


class PS_OT_split(bpy.types.Operator):
    bl_idname = "print_split.split"
    bl_label = "Split"
    bl_description = ("Cut the selected meshes (or the plane's original target) along the cut plane into "
                      "two closed parts _A/_B, with optional peg connectors")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.scene.print_split.plane is not None

    def execute(self, context):
        s = context.scene.print_split
        plane = s.plane
        targets = [o for o in context.selected_objects if o.type == 'MESH' and o != plane]
        if not targets and isinstance(plane.get("print_split_target"), bpy.types.Object):
            targets = [plane["print_split_target"]]
        if not targets:
            self.report({'WARNING'}, "Select the mesh(es) to split")
            return {'CANCELLED'}
        done, pegs, missed = [], [], []
        for ob in targets:
            res = split_object(ob, plane, context, s)
            if res is None:
                missed.append(ob.name)
                continue
            done += res[:2]
            pegs.append(res[2])
            if s.delete_original:
                bpy.data.objects.remove(ob)
            else:
                ob.hide_set(True)
                ob.hide_render = True
        for o in context.selected_objects:
            o.select_set(False)
        for o in done:
            o.select_set(True)
        if done:
            context.view_layer.objects.active = done[0]
        msg = f"Split {len(done) // 2} object(s)"
        if s.connectors:
            msg += f", pegs: {pegs}"
        if missed:
            msg += f"; plane misses {', '.join(missed)}"
        self.report({'WARNING'} if missed or (s.connectors and 0 in pegs) else {'INFO'}, msg)
        return {'FINISHED'} if done else {'CANCELLED'}


class PS_OT_bed_check(bpy.types.Operator):
    bl_idname = "print_split.bed_check"
    bl_label = "Check Bed Fit"
    bl_description = "Check whether each selected part fits the print bed (also turned 90 degrees); too big = red"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.print_split
        bed = (s.bed_x, s.bed_y, s.bed_z)
        obs = [o for o in context.selected_objects if o.type == 'MESH']
        bad = []
        for ob in obs:
            fit = bed_fit(world_size(ob), bed)
            ob.color = (1, 1, 1, 1) if fit else (1, 0.1, 0.1, 1)
            if fit is None:
                bad.append(ob.name)
            elif fit != (0, 1, 2):
                self.report({'INFO'}, f"{ob.name}: fits when turned (bed X/Y/Z = part {''.join('XYZ'[i] for i in fit)})")
        space = context.space_data
        if bad and space and space.type == 'VIEW_3D':
            space.shading.color_type = 'OBJECT'
        if bad:
            self.report({'WARNING'}, f"Too big for the bed: {', '.join(bad)}")
        else:
            self.report({'INFO'}, f"All {len(obs)} part(s) fit the bed")
        return {'FINISHED'}


class PS_OT_manifold_check(bpy.types.Operator):
    bl_idname = "print_split.manifold_check"
    bl_label = "Check Watertight"
    bl_description = "Count non-manifold (open or shared by 3+ faces) edges on each selected part"

    def execute(self, context):
        res = {o.name: non_manifold_count(o) for o in context.selected_objects if o.type == 'MESH'}
        bad = {k: v for k, v in res.items() if v}
        if bad:
            self.report({'WARNING'}, "Non-manifold edges: " + ", ".join(f"{k} ({v})" for k, v in bad.items()))
        else:
            self.report({'INFO'}, f"All {len(res)} part(s) watertight")
        return {'FINISHED'}


class PS_PT_panel(bpy.types.Panel):
    bl_label = "Print Split"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Print"

    def draw(self, context):
        s = context.scene.print_split
        col = self.layout.column()
        row = col.row(align=True)
        row.operator("print_split.add_plane", text="Plane Z").orient = 'Z'
        row.operator("print_split.add_plane", text="X").orient = 'X'
        row.operator("print_split.add_plane", text="Y").orient = 'Y'
        row.operator("print_split.add_plane", text="View").orient = 'VIEW'
        col.prop(s, "plane")
        col.prop(s, "delete_original")
        col.prop(s, "connectors")
        sub = col.column(align=True)
        sub.active = s.connectors
        sub.row().prop(s, "peg_shape", expand=True)
        for p in ("peg_count", "peg_radius", "peg_length", "tolerance", "margin"):
            sub.prop(s, p)
        col.operator("print_split.split", icon='MOD_BOOLEAN')
        col.separator()
        col.prop(s, "bed_preset", text="")
        sub = col.column(align=True)
        for p in ("bed_x", "bed_y", "bed_z"):
            sub.prop(s, p)
        row = col.row(align=True)
        row.operator("print_split.bed_check", icon='CHECKMARK')
        row.operator("print_split.manifold_check", icon='MOD_EDGESPLIT')


_classes = (PS_Settings, PS_OT_add_plane, PS_OT_split, PS_OT_bed_check, PS_OT_manifold_check, PS_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.print_split = bpy.props.PointerProperty(type=PS_Settings)


def unregister():
    del bpy.types.Scene.print_split
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
