"""Precision Edit: exact edge lengths, angles, distances and positions in Edit Mesh.

All math runs in world space (object scale/rotation included) on mathutils Vectors;
operators read/write bmesh coordinates through the object matrix.
"""
import math

import bmesh
import bpy
from bpy.utils import units
from mathutils import Quaternion, Vector

EPS = 1e-9


# ---------------------------------------------------------------- pure math

def set_length(a, b, length, move='B'):
    """Return new (a, b) so |b - a| == length. move: 'A', 'B' or 'CENTER' (both, from the middle)."""
    d = b - a
    d = d.normalized() if d.length > EPS else Vector((1.0, 0.0, 0.0))
    if move == 'A':
        return b - d * length, b.copy()
    if move == 'CENTER':
        mid = (a + b) / 2
        return mid - d * (length / 2), mid + d * (length / 2)
    return a.copy(), a + d * length


def angle_at(corner, a, b):
    """Angle (radians) between the arms corner->a and corner->b."""
    u, w = a - corner, b - corner
    return u.angle(w, 0.0) if u.length > EPS and w.length > EPS else 0.0


def set_angle(corner, fixed, moving, angle):
    """New position of `moving` so the angle at `corner` is `angle`, rotating in the arms' plane
    and keeping the moving arm's length."""
    u, w = fixed - corner, moving - corner
    if u.length < EPS:
        return moving.copy()
    axis = u.cross(w)
    if axis.length < EPS:  # collinear arms: no plane, pick any perpendicular
        axis = u.orthogonal()
    return corner + (Quaternion(axis.normalized(), angle) @ u.normalized()) * w.length


def scale_about(p, pivot, factor):
    return pivot + (p - pivot) * factor


def on_b_side(p, a, b):
    """True if p projects onto the b half of the line a->b."""
    d = b - a
    return d.length_squared > EPS and (p - a).dot(d) / d.length_squared >= 0.5


# ---------------------------------------------------------------- selection helpers

def _selection(bm):
    # ponytail: O(n) scan per call (also on panel redraw); fine to ~100k verts, cache via msgbus if needed
    verts = [v for v in bm.verts if v.select]
    edges = [e for e in bm.edges if e.select]
    return verts, edges


def _active_vert(bm):
    a = bm.select_history.active
    return a if isinstance(a, bmesh.types.BMVert) and a.select else None


def _active_edge(bm, edges):
    a = bm.select_history.active
    if isinstance(a, bmesh.types.BMEdge) and a.select:
        return a
    if len(edges) == 1:
        return edges[0]
    v = _active_vert(bm)
    near = [e for e in edges if v in e.verts] if v else []
    return near[0] if len(near) == 1 else None


def _ends(edge, active):
    """(A, B) verts of an edge; B is the active vertex if it is on the edge."""
    a, b = edge.verts
    return (b, a) if a is active else (a, b)


def angle_triplet(bm, verts, edges):
    """(corner, fixed, moving) BMVerts: 3 verts with the active one as corner,
    or 2 selected edges sharing a vertex (the non-active edge moves)."""
    act = _active_vert(bm)
    if len(verts) == 3 and act in verts:
        hist = [v for v in bm.select_history if isinstance(v, bmesh.types.BMVert) and v in verts and v is not act]
        rest = hist + sorted((v for v in verts if v is not act and v not in hist), key=lambda v: v.index)
        return act, rest[0], rest[1]
    if len(edges) == 2:
        e1, e2 = edges
        shared = set(e1.verts) & set(e2.verts)
        if len(shared) == 1:
            c = shared.pop()
            if bm.select_history.active is e2:
                e1, e2 = e2, e1
            return c, e1.other_vert(c), e2.other_vert(c)
    return None


def readouts(ob, bm):
    """[(label, value, kind)] for the current selection; kind is 'LENGTH', 'ROTATION' or 'POSITION'."""
    n = ob.data.total_vert_sel
    if not 1 <= n <= 3:
        return []
    verts, edges = _selection(bm)
    mw = ob.matrix_world
    if n == 1:
        return [("Position", mw @ verts[0].co, 'POSITION')]
    if n == 2:
        a, b = (mw @ v.co for v in verts)
        return [("Edge Length" if edges else "Distance", (b - a).length, 'LENGTH')]
    t = angle_triplet(bm, verts, edges)
    if not t:
        return []
    c, f, m = (mw @ v.co for v in t)
    return [("Angle", angle_at(c, f, m), 'ROTATION'),
            ("Arm 1", (f - c).length, 'LENGTH'), ("Arm 2", (m - c).length, 'LENGTH')]


def fmt(scene, value, kind):
    us = scene.unit_settings
    if kind == 'ROTATION':
        return f"{math.degrees(value):.4f}°" if us.system_rotation == 'DEGREES' else f"{value:.5f} rad"
    if us.system == 'NONE':
        return f"{value:.5g}"
    return units.to_string(us.system, 'LENGTH', value * us.scale_length, precision=5)


# ---------------------------------------------------------------- operators

MOVE_ITEMS = (
    ('B', "End B (Active)", "Keep end A, move end B (the active vertex if it is on the edge)"),
    ('A', "End A", "Keep end B (the active vertex), move end A"),
    ('CENTER', "Both", "Move both ends, keeping the middle in place"),
)


class _EditOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def _begin(self, context):
        ob = context.edit_object
        mw = ob.matrix_world
        return ob, bmesh.from_edit_mesh(ob.data), mw, mw.inverted_safe()

    def _fail(self, msg):
        self.report({'ERROR'}, msg)
        return {'CANCELLED'}

    def _prefill(self, context, prop, value):
        if not self.properties.is_property_set(prop) and value is not None:
            setattr(self, prop, value)
        return context.window_manager.invoke_props_dialog(self)


def _length_prop(name, desc):
    return bpy.props.FloatProperty(name=name, description=desc, default=1.0, min=0.0,
                                   subtype='DISTANCE', unit='LENGTH', options={'SKIP_SAVE'})


class PE_OT_set_edge_length(_EditOp, bpy.types.Operator):
    bl_idname = "precision_edit.set_edge_length"
    bl_label = "Set Edge Length"
    bl_description = "Give each selected edge an exact world-space length"

    length: _length_prop("Length", "Target edge length (world space)")
    move: bpy.props.EnumProperty(name="Move", items=MOVE_ITEMS)
    stretch: bpy.props.BoolProperty(
        name="Move Selected Side",
        description="Use only the active edge and move every selected vertex on the moving side "
                    "by the same offset (stretch)")

    def invoke(self, context, event):
        ob, bm, mw, _ = self._begin(context)
        e = _active_edge(bm, _selection(bm)[1])
        cur = (mw @ e.verts[0].co - mw @ e.verts[1].co).length if e else None
        return self._prefill(context, "length", cur)

    def execute(self, context):
        ob, bm, mw, imw = self._begin(context)
        verts, edges = _selection(bm)
        if self.stretch:
            edges = [_active_edge(bm, edges)] if _active_edge(bm, edges) else []
        if not edges:
            return self._fail("Select an edge (the active edge when stretching)")
        act = _active_vert(bm)
        # ponytail: edges sharing a moving vertex are solved one after another (last one wins)
        for e in edges:
            va, vb = _ends(e, act)
            a, b = mw @ va.co, mw @ vb.co
            na, nb = set_length(a, b, self.length, self.move)
            if self.stretch:
                for v in verts:
                    p = mw @ v.co
                    v.co = imw @ (p + (nb - b if on_b_side(p, a, b) else na - a))
            else:
                va.co, vb.co = imw @ na, imw @ nb
        bmesh.update_edit_mesh(ob.data)
        return {'FINISHED'}


class PE_OT_set_distance(_EditOp, bpy.types.Operator):
    bl_idname = "precision_edit.set_distance"
    bl_label = "Set Distance"
    bl_description = "Move two selected vertices along their line to an exact world-space distance"

    distance: _length_prop("Distance", "Target distance between the two vertices (world space)")
    move: bpy.props.EnumProperty(name="Move", items=MOVE_ITEMS)

    def _pair(self, bm):
        verts = _selection(bm)[0]
        if len(verts) != 2:
            return None
        return (verts[1], verts[0]) if verts[0] is _active_vert(bm) else tuple(verts)

    def invoke(self, context, event):
        ob, bm, mw, _ = self._begin(context)
        p = self._pair(bm)
        return self._prefill(context, "distance", (mw @ p[1].co - mw @ p[0].co).length if p else None)

    def execute(self, context):
        ob, bm, mw, imw = self._begin(context)
        p = self._pair(bm)
        if not p:
            return self._fail("Select exactly two vertices")
        va, vb = p
        na, nb = set_length(mw @ va.co, mw @ vb.co, self.distance, self.move)
        va.co, vb.co = imw @ na, imw @ nb
        bmesh.update_edit_mesh(ob.data)
        return {'FINISHED'}


class PE_OT_set_angle(_EditOp, bpy.types.Operator):
    bl_idname = "precision_edit.set_angle"
    bl_label = "Set Angle"
    bl_description = ("Rotate one arm around the corner to an exact angle. Corner: the active of 3 "
                      "selected vertices, or the vertex shared by 2 selected edges")

    angle: bpy.props.FloatProperty(name="Angle", default=math.radians(90), min=0.0, max=math.pi,
                                   subtype='ANGLE', options={'SKIP_SAVE'})
    flip: bpy.props.BoolProperty(name="Move Other Arm",
                                 description="Rotate the other arm instead (first selected / active edge)")

    def invoke(self, context, event):
        ob, bm, mw, _ = self._begin(context)
        t = angle_triplet(bm, *_selection(bm))
        return self._prefill(context, "angle", angle_at(*(mw @ v.co for v in t)) if t else None)

    def execute(self, context):
        ob, bm, mw, imw = self._begin(context)
        t = angle_triplet(bm, *_selection(bm))
        if not t:
            return self._fail("Select 3 vertices (active = corner) or 2 edges sharing a vertex")
        c, f, m = t
        if self.flip:
            f, m = m, f
        m.co = imw @ set_angle(mw @ c.co, mw @ f.co, mw @ m.co, self.angle)
        bmesh.update_edit_mesh(ob.data)
        return {'FINISHED'}


class PE_OT_scale_to_edge(_EditOp, bpy.types.Operator):
    bl_idname = "precision_edit.scale_to_edge"
    bl_label = "Scale to Edge Length"
    bl_description = "Scale the selection uniformly so the active edge gets an exact world-space length"

    length: _length_prop("Length", "Target length of the active edge (world space)")
    pivot: bpy.props.EnumProperty(name="Pivot", items=(
        ('MEDIAN', "Median", "Median of the selected vertices"),
        ('CURSOR', "3D Cursor", "The 3D cursor"),
        ('ACTIVE', "Active Vertex", "The active vertex (the active edge's middle if there is none)"),
    ))

    def invoke(self, context, event):
        ob, bm, mw, _ = self._begin(context)
        e = _active_edge(bm, _selection(bm)[1])
        return self._prefill(context, "length", (mw @ e.verts[0].co - mw @ e.verts[1].co).length if e else None)

    def execute(self, context):
        ob, bm, mw, imw = self._begin(context)
        verts, edges = _selection(bm)
        e = _active_edge(bm, edges)
        if not e:
            return self._fail("Select edges with one active edge")
        a, b = (mw @ v.co for v in e.verts)
        if (b - a).length < EPS:
            return self._fail("Active edge has zero length")
        factor = self.length / (b - a).length
        if self.pivot == 'CURSOR':
            pivot = context.scene.cursor.location.copy()
        elif self.pivot == 'ACTIVE':
            act = _active_vert(bm)
            pivot = mw @ act.co if act else (a + b) / 2
        else:
            pivot = sum((mw @ v.co for v in verts), Vector()) / len(verts)
        for v in verts:
            v.co = imw @ scale_about(mw @ v.co, pivot, factor)
        bmesh.update_edit_mesh(ob.data)
        return {'FINISHED'}


class PE_OT_set_position(_EditOp, bpy.types.Operator):
    bl_idname = "precision_edit.set_position"
    bl_label = "Move Active To"
    bl_description = ("Move the selection so the active vertex (or the selection median) lands "
                      "on exact coordinates")

    location: bpy.props.FloatVectorProperty(name="Location", subtype='TRANSLATION', unit='LENGTH',
                                            options={'SKIP_SAVE'})
    space: bpy.props.EnumProperty(name="Space", items=(
        ('WORLD', "World", "Location is in global coordinates"),
        ('LOCAL', "Local", "Location is in object coordinates"),
    ))

    def _pivot(self, bm, verts):
        act = _active_vert(bm)
        return act.co.copy() if act else sum((v.co for v in verts), Vector()) / len(verts)

    def invoke(self, context, event):
        ob, bm, mw, _ = self._begin(context)
        verts = _selection(bm)[0]
        cur = None
        if verts:
            cur = self._pivot(bm, verts)
            cur = cur if self.space == 'LOCAL' else mw @ cur
        return self._prefill(context, "location", cur)

    def execute(self, context):
        ob, bm, mw, imw = self._begin(context)
        verts = _selection(bm)[0]
        if not verts:
            return self._fail("Select something")
        target = Vector(self.location)
        if self.space == 'WORLD':
            target = imw @ target
        d = target - self._pivot(bm, verts)  # local delta: same for every vertex
        for v in verts:
            v.co += d
        bmesh.update_edit_mesh(ob.data)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class PE_PT_panel(bpy.types.Panel):
    bl_label = "Precision"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH' and context.edit_object is not None

    def draw(self, context):
        layout = self.layout
        ob = context.edit_object
        rows = readouts(ob, bmesh.from_edit_mesh(ob.data))
        box = layout.box().column(align=True)
        if not rows:
            box.label(text="Select 1-3 vertices or an edge pair", icon='INFO')
        for label, value, kind in rows:
            if kind == 'POSITION':
                for axis, x in zip("XYZ", value):
                    r = box.row()
                    r.label(text=f"{label} {axis}")
                    r.label(text=fmt(context.scene, x, 'LENGTH'))
            else:
                r = box.row()
                r.label(text=label)
                r.label(text=fmt(context.scene, value, kind))
        col = layout.column(align=True)
        col.operator(PE_OT_set_edge_length.bl_idname, icon='DRIVER_DISTANCE')
        col.operator(PE_OT_set_distance.bl_idname, icon='ARROW_LEFTRIGHT')
        col.operator(PE_OT_set_angle.bl_idname, icon='DRIVER_ROTATIONAL_DIFFERENCE')
        col.operator(PE_OT_scale_to_edge.bl_idname, icon='FULLSCREEN_ENTER')
        col.operator(PE_OT_set_position.bl_idname, icon='PIVOT_ACTIVE')


_classes = (PE_OT_set_edge_length, PE_OT_set_distance, PE_OT_set_angle, PE_OT_scale_to_edge,
            PE_OT_set_position, PE_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
