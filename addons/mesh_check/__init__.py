"""Mesh Check: n-gons, triangles, non-manifold, loose, zero-area, doubles, inverted normals."""
import bmesh
import bpy

CHECKS = (
    # id, label, element type ('VERT'/'EDGE'/'FACE'), icon
    ("ngons", "N-gons", 'FACE', 'MESH_ICOSPHERE'),
    ("tris", "Triangles", 'FACE', 'MESH_CONE'),
    ("nonmanifold", "Non-manifold Edges", 'EDGE', 'MOD_EDGESPLIT'),
    ("loose_verts", "Loose Vertices", 'VERT', 'VERTEXSEL'),
    ("loose_edges", "Loose Edges", 'EDGE', 'EDGESEL'),
    ("zero_faces", "Zero-area Faces", 'FACE', 'X'),
    ("doubles", "Overlapping Vertices", 'VERT', 'AUTOMERGE_OFF'),
    ("flipped", "Inconsistent Normals", 'EDGE', 'NORMALS_FACE'),
    ("poles", "Poles (6+ edges)", 'VERT', 'PARTICLE_POINT'),
)


def find(bm, check, dist=1e-4, area=1e-8):
    """Return the list of offending bmesh elements for a check id."""
    if check == "ngons":
        return [f for f in bm.faces if len(f.verts) > 4]
    if check == "tris":
        return [f for f in bm.faces if len(f.verts) == 3]
    if check == "nonmanifold":
        return [e for e in bm.edges if not e.is_manifold and e.link_faces and not e.is_boundary]
    if check == "loose_verts":
        return [v for v in bm.verts if not v.link_edges]
    if check == "loose_edges":
        return [e for e in bm.edges if not e.link_faces]
    if check == "zero_faces":
        return [f for f in bm.faces if f.calc_area() <= area]
    if check == "doubles":
        found = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=dist)["targetmap"]
        return list(set(found.keys()) | set(found.values()))
    if check == "flipped":
        # An edge between two faces that use it in the same direction means one face is flipped.
        out = []
        for e in bm.edges:
            if len(e.link_loops) == 2:
                a, b = e.link_loops
                if a.vert == b.vert:
                    out.append(e)
        return out
    if check == "poles":
        return [v for v in bm.verts if len(v.link_edges) >= 6]
    raise ValueError(check)


def _bm(ob):
    if ob.mode == 'EDIT':
        return bmesh.from_edit_mesh(ob.data), False
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    return bm, True


def run_checks(ob):
    bm, owned = _bm(ob)
    try:
        return {c[0]: len(find(bm, c[0])) for c in CHECKS}
    finally:
        if owned:
            bm.free()


class MC_OT_scan(bpy.types.Operator):
    bl_idname = "mesh_check.scan"
    bl_label = "Check Mesh"
    bl_description = "Count common mesh problems on the active mesh"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        ob = context.active_object
        res = run_checks(ob)
        wm = context.window_manager
        wm.mesh_check_object = ob.name
        for k, v in res.items():
            setattr(wm.mesh_check_results, k, v)
        return {'FINISHED'}


class MC_OT_select(bpy.types.Operator):
    bl_idname = "mesh_check.select"
    bl_label = "Select Problem"
    bl_description = "Enter Edit Mode and select only these elements"
    bl_options = {'REGISTER', 'UNDO'}

    check: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        ob = context.active_object
        if ob.mode != 'EDIT':
            bpy.ops.object.mode_set(mode='EDIT')
        etype = next(c[2] for c in CHECKS if c[0] == self.check)
        context.tool_settings.mesh_select_mode = (etype == 'VERT', etype == 'EDGE', etype == 'FACE')
        bm = bmesh.from_edit_mesh(ob.data)
        for seq in (bm.verts, bm.edges, bm.faces):
            for el in seq:
                el.select = False
        items = find(bm, self.check)
        for el in items:
            el.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
        self.report({'INFO'}, f"Selected {len(items)}")
        return {'FINISHED'}


class MC_OT_fix(bpy.types.Operator):
    bl_idname = "mesh_check.fix"
    bl_label = "Fix"
    bl_description = "Apply the standard fix for this problem"
    bl_options = {'REGISTER', 'UNDO'}

    check: bpy.props.StringProperty()

    def execute(self, context):
        ob = context.active_object
        bm, owned = _bm(ob)
        items = find(bm, self.check)
        if self.check in {"loose_verts", "loose_edges"}:
            bmesh.ops.delete(bm, geom=items, context='VERTS' if self.check == "loose_verts" else 'EDGES')
        elif self.check == "zero_faces":
            bmesh.ops.dissolve_degenerate(bm, dist=1e-4, edges=bm.edges[:])
        elif self.check == "doubles":
            bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-4)
        elif self.check == "flipped":
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        elif self.check == "ngons":
            bmesh.ops.triangulate(bm, faces=items, quad_method='BEAUTY', ngon_method='BEAUTY')
        elif self.check == "tris":
            bmesh.ops.join_triangles(bm, faces=items, angle_face_threshold=0.7, angle_shape_threshold=0.7)
        else:
            if owned:
                bm.free()
            self.report({'WARNING'}, "No automatic fix, use Select")
            return {'CANCELLED'}
        if owned:
            bm.to_mesh(ob.data)
            bm.free()
        else:
            bmesh.update_edit_mesh(ob.data)
        ob.data.update()
        bpy.ops.mesh_check.scan()
        return {'FINISHED'}


FIXABLE = {"loose_verts", "loose_edges", "zero_faces", "doubles", "flipped", "ngons", "tris"}


class MC_Results(bpy.types.PropertyGroup):
    __annotations__ = {c[0]: bpy.props.IntProperty(default=-1) for c in CHECKS}


class MC_PT_panel(bpy.types.Panel):
    bl_label = "Mesh Check"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        wm = context.window_manager
        col = self.layout.column(align=True)
        col.operator("mesh_check.scan", icon='VIEWZOOM')
        if wm.mesh_check_object != context.active_object.name:
            return
        r = wm.mesh_check_results
        for cid, label, _, icon in CHECKS:
            n = getattr(r, cid)
            row = col.row(align=True)
            row.alert = n > 0 and cid not in {"tris", "poles"}
            row.label(text=f"{label}: {n}", icon=icon)
            sub = row.row(align=True)
            sub.enabled = n > 0
            sub.operator("mesh_check.select", text="", icon='RESTRICT_SELECT_OFF').check = cid
            if cid in FIXABLE:
                sub.operator("mesh_check.fix", text="", icon='TOOL_SETTINGS').check = cid


_classes = (MC_Results, MC_OT_scan, MC_OT_select, MC_OT_fix, MC_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.mesh_check_results = bpy.props.PointerProperty(type=MC_Results)
    bpy.types.WindowManager.mesh_check_object = bpy.props.StringProperty()


def unregister():
    del bpy.types.WindowManager.mesh_check_object
    del bpy.types.WindowManager.mesh_check_results
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
