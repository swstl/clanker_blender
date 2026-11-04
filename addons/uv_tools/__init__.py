"""UV Tools: angle seams, batch unwrap, texel density, checker toggle, straighten,
flipped-face select and UV map sync. Native ops (seams from islands, select overlap,
follow active quads) are exposed rather than re-implemented."""
import math
from contextlib import contextmanager

import bmesh
import bpy
from bpy_extras.bmesh_utils import bmesh_linked_uv_islands
from mathutils import Vector

CHECKER = "UVT_Checker"
PROP = "uvt_checker"  # object custom prop: {"n": slot count, "<slot index>": original material}


# ---------------------------------------------------------------- helpers

@contextmanager
def edit_bm(ob, write=False):
    """BMesh of a mesh object in edit or object mode; writes back if `write`."""
    if ob.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(ob.data)
        yield bm
        if write:
            bmesh.update_edit_mesh(ob.data)
        return
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    try:
        yield bm
        if write:
            bm.to_mesh(ob.data)
            ob.data.update()
    finally:
        bm.free()


def targets(context):
    """(object, selected_faces_only) for edit mode or the selected objects in object mode."""
    if context.mode == 'EDIT_MESH':
        return [(o, True) for o in context.objects_in_mode if o.type == 'MESH']
    return [(o, False) for o in context.selected_objects if o.type == 'MESH']


def _faces(bm, sel):
    return [f for f in bm.faces if not f.hide and (f.select or not sel)]


def uv_area(f, uv):
    """Signed UV area (positive = counter-clockwise)."""
    p = [l[uv].uv for l in f.loops]
    return sum(a.x * b.y - b.x * a.y for a, b in zip(p, p[1:] + p[:1])) / 2


def world_area(f, mat):
    co = [mat @ v.co for v in f.verts]
    n = Vector()
    for a, b in zip(co, co[1:] + co[:1]):
        n += a.cross(b)
    return n.length / 2


def areas(faces, uv, mat):
    """(world area m^2, absolute UV area) of faces."""
    return (sum(world_area(f, mat) for f in faces), sum(abs(uv_area(f, uv)) for f in faces))


def density(a3, a2, size):
    """Texel density in px/m for a texture of `size` px."""
    return math.sqrt(a2 / a3) * size if a3 > 0 else 0.0


def measure(pairs, size):
    a3 = a2 = 0.0
    for ob, sel in pairs:
        with edit_bm(ob) as bm:
            uv = bm.loops.layers.uv.active
            if uv:
                x, y = areas(_faces(bm, sel), uv, ob.matrix_world)
                a3, a2 = a3 + x, a2 + y
    return density(a3, a2, size)


def set_density(ob, sel, target, size):
    """Scale each UV island (touching the selection) around its center to `target` px/m."""
    n = 0
    with edit_bm(ob, write=True) as bm:
        uv = bm.loops.layers.uv.active
        if not uv:
            return 0
        for isl in bmesh_linked_uv_islands(bm, uv):
            if sel and not any(f.select for f in isl):
                continue
            d = density(*areas(isl, uv, ob.matrix_world), size)
            if d <= 0:
                continue
            k = target / d
            pts = [l[uv].uv for f in isl for l in f.loops]
            c = Vector((min(p.x for p in pts) + max(p.x for p in pts),
                        min(p.y for p in pts) + max(p.y for p in pts))) / 2
            for f in isl:
                for l in f.loops:
                    l[uv].uv = c + (l[uv].uv - c) * k
            n += 1
    return n


def scale_uvs(ob, k):
    """Scale all UVs of an object around (0, 0)."""
    with edit_bm(ob, write=True) as bm:
        uv = bm.loops.layers.uv.active
        for f in bm.faces:
            for l in f.loops:
                l[uv].uv *= k


def equalize(obs, size):
    """Scale each object's whole UV layout so all objects share the lowest density."""
    obs = list({o.data: o for o in obs}.values())  # shared meshes only once
    ds = {o: measure([(o, False)], size) for o in obs}
    low = min((d for d in ds.values() if d > 0), default=0)
    for o, d in ds.items():
        if d > 0:
            scale_uvs(o, low / d)


def mark_angle_seams(bm, angle, use_sharp, sel=False):
    """Mark seams on edges sharper than `angle` (radians) or marked sharp. Returns count.
    With `sel`, only selected edges (all edges when none are selected)."""
    edges = (sel and [e for e in bm.edges if e.select]) or bm.edges
    n = 0
    for e in edges:
        if len(e.link_faces) == 2 and not e.seam and (
                e.calc_face_angle(0) > angle or (use_sharp and not e.smooth)):
            e.seam = True
            n += 1
    return n


def flipped_faces(faces, uv):
    """Faces whose UV winding differs from the majority."""
    signs = [(f, uv_area(f, uv)) for f in faces]
    bad = -1 if sum(a > 0 for _, a in signs) >= sum(a < 0 for _, a in signs) else 1
    return [f for f, a in signs if a * bad > 0]


def rectify_quad(f, uv):
    """Turn a quad's UVs into an axis-aligned rectangle with its 3D proportions."""
    v = [x.co for x in f.verts]
    w = ((v[1] - v[0]).length + (v[2] - v[3]).length) / 2
    h = ((v[3] - v[0]).length + (v[2] - v[1]).length) / 2
    a = uv_area(f, uv)
    s = math.sqrt(abs(a) / (w * h)) if a and w and h else 0.1 / max(w, h, 1e-6)
    c = sum((l[uv].uv for l in f.loops), Vector((0, 0))) / 4
    sx = -1 if a < 0 else 1  # keep the original winding
    for l, (x, y) in zip(f.loops, ((-1, -1), (1, -1), (1, 1), (-1, 1))):
        l[uv].uv = c + Vector((x * sx * w, y * h)) * s / 2


def checker_material():
    mat = bpy.data.materials.get(CHECKER)
    if mat:
        return mat
    img = bpy.data.images.get(CHECKER) or bpy.data.images.new(CHECKER, 1024, 1024)
    img.generated_type = 'UV_GRID'
    mat = bpy.data.materials.new(CHECKER)
    nt = mat.node_tree
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.location = (-400, 300)
    bsdf = next(n for n in nt.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    return mat


def checker_on(ob, mat):
    if PROP in ob:
        return
    # ID references in the custom prop keep the originals alive (users) while hidden.
    d = {"n": len(ob.material_slots)}
    d.update({str(i): s.material for i, s in enumerate(ob.material_slots)
              if s.material and s.material != mat})
    ob[PROP] = d
    if not ob.material_slots:
        ob.data.materials.append(mat)
    for s in ob.material_slots:
        s.material = mat


def checker_off(ob):
    d = ob.get(PROP)
    if d is None:
        return
    while len(ob.data.materials) > d["n"]:  # material_slots length lags behind pop()
        ob.data.materials.pop(index=len(ob.data.materials) - 1)
    for i, s in enumerate(ob.material_slots):
        if str(i) in d:
            s.material = d[str(i)]
        elif s.material and s.material.name == CHECKER:
            s.material = None
    del ob[PROP]


def match_uv_maps(me, names):
    """Rebuild a mesh's UV maps to exactly `names` in that order, keeping data by name
    (or by position for maps whose name is not in the list)."""
    layers = me.uv_layers
    data = {}
    for l in layers:
        arr = [0.0] * (len(me.loops) * 2)
        l.uv.foreach_get("vector", arr)
        data[l.name] = arr
    old = list(data)
    src = []
    for i, nm in enumerate(names):
        if nm in data:
            src.append(nm)
        elif i < len(old) and old[i] not in names:
            src.append(old[i])
        else:
            src.append(old[0] if old else None)
    while len(layers):
        layers.remove(layers[0])
    for nm, s in zip(names, src):
        l = layers.new(name=nm, do_init=s is None)
        if s:
            l.uv.foreach_set("vector", data[s])
    if len(layers):
        layers.active_index = 0
        layers[0].active_render = True


def _meshes(context):
    return list({o.data for o in context.selected_objects if o.type == 'MESH'})


def _settings(context):
    return context.scene.uv_tools


# ---------------------------------------------------------------- operators

class _EditOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'


class _ObjOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.type == 'MESH' for o in context.selected_objects)


class UVT_OT_angle_seams(bpy.types.Operator):
    bl_idname = "uv_tools.angle_seams"
    bl_label = "Seams from Angle"
    bl_description = ("Mark seams on edges whose face angle exceeds the threshold (or that are "
                      "marked sharp). Edit mode: selected edges, or all if none selected")
    bl_options = {'REGISTER', 'UNDO'}

    angle: bpy.props.FloatProperty(name="Angle", subtype='ANGLE', default=math.radians(60),
                                   min=0, max=math.pi)
    use_sharp: bpy.props.BoolProperty(name="Sharp Edges", default=True,
                                      description="Also mark edges already marked sharp")

    @classmethod
    def poll(cls, context):
        return bool(targets(context))

    def invoke(self, context, event):
        s = _settings(context)
        self.angle, self.use_sharp = s.seam_angle, s.seam_sharp
        return self.execute(context)

    def execute(self, context):
        n = 0
        for ob, sel in targets(context):
            with edit_bm(ob, write=True) as bm:
                n += mark_angle_seams(bm, self.angle, self.use_sharp, sel)
        self.report({'INFO'}, f"Marked {n} seam edge(s)")
        return {'FINISHED'}


class UVT_OT_batch_unwrap(_ObjOp, bpy.types.Operator):
    bl_idname = "uv_tools.batch_unwrap"
    bl_label = "Batch Unwrap"
    bl_description = "Unwrap every selected mesh object and pack them together or each on its own"

    method: bpy.props.EnumProperty(name="Method", items=(
        ('SMART', "Smart Project", "Automatic seams by angle"),
        ('ANGLE_BASED', "Angle Based", "Unwrap along marked seams"),
        ('CONFORMAL', "Conformal", "Unwrap along marked seams (LSCM)"),
        ('MINIMUM_STRETCH', "Minimum Stretch", "Unwrap along marked seams, less stretch")))
    angle_limit: bpy.props.FloatProperty(name="Angle Limit", subtype='ANGLE',
                                         default=math.radians(66), min=0, max=math.radians(89))
    pack: bpy.props.EnumProperty(name="Pack", items=(
        ('TOGETHER', "Together", "All objects share one UV layout"),
        ('SEPARATE', "Separately", "Each object fills its own 0-1 space")))
    keep_texel: bpy.props.BoolProperty(name="Keep Texel Density", default=True,
                                       description="Give every object the same texel density")
    margin: bpy.props.FloatProperty(name="Margin", default=0.01, min=0, max=1)

    def invoke(self, context, event):
        s = _settings(context)
        for k in ("method", "angle_limit", "pack", "keep_texel", "margin"):
            setattr(self, k, getattr(s, k))
        return self.execute(context)

    def execute(self, context):
        vl = context.view_layer
        obs = [o for o in context.selected_objects if o.type == 'MESH']
        active = vl.objects.active
        groups = [obs] if self.pack == 'TOGETHER' else [[o] for o in obs]
        for group in groups:
            for o in obs:
                o.select_set(o in group)
            vl.objects.active = group[0]
            for o in group:
                if not o.data.uv_layers:
                    o.data.uv_layers.new()
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.reveal(select=False)
            bpy.ops.mesh.select_all(action='SELECT')
            if self.method == 'SMART':
                bpy.ops.uv.smart_project(angle_limit=self.angle_limit, island_margin=self.margin)
            else:
                bpy.ops.uv.unwrap(method=self.method, margin=self.margin)
            if self.keep_texel and len(group) > 1:
                equalize(group, 1024)
                bpy.ops.uv.pack_islands(margin=self.margin)
            bpy.ops.object.mode_set(mode='OBJECT')
        if self.keep_texel and len(groups) > 1:
            equalize(obs, 1024)  # scales down around (0,0): layouts stay inside 0-1
        for o in obs:
            o.select_set(True)
        vl.objects.active = active
        self.report({'INFO'}, f"Unwrapped {len(obs)} object(s)")
        return {'FINISHED'}


class UVT_OT_texel_measure(bpy.types.Operator):
    bl_idname = "uv_tools.texel_measure"
    bl_label = "Measure"
    bl_description = "Measure texel density (px/m) of the selected faces, or of the selected objects"

    @classmethod
    def poll(cls, context):
        return bool(targets(context))

    def execute(self, context):
        s = _settings(context)
        s.density_measured = measure(targets(context), int(s.tex_size))
        self.report({'INFO'}, f"{s.density_measured:.1f} px/m at {s.tex_size} px")
        return {'FINISHED'}


class UVT_OT_texel_set(bpy.types.Operator):
    bl_idname = "uv_tools.texel_set"
    bl_label = "Set Density"
    bl_description = ("Scale UV islands of the selection (each around its own center) to the "
                      "target texel density")
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.FloatProperty(name="Target (px/m)", default=512, min=0.001)
    tex_size: bpy.props.IntProperty(name="Texture Size", default=1024, min=1)

    @classmethod
    def poll(cls, context):
        return bool(targets(context))

    def invoke(self, context, event):
        s = _settings(context)
        self.target, self.tex_size = s.density_target, int(s.tex_size)
        return self.execute(context)

    def execute(self, context):
        n = sum(set_density(o, sel, self.target, self.tex_size) for o, sel in targets(context))
        self.report({'INFO'}, f"Scaled {n} island(s)")
        return {'FINISHED'}


class UVT_OT_texel_copy(_ObjOp, bpy.types.Operator):
    bl_idname = "uv_tools.texel_copy"
    bl_label = "Copy Active to Selected"
    bl_description = "Give the selected objects' UV islands the active object's texel density"

    def execute(self, context):
        act = context.active_object
        if not act or act.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh")
            return {'CANCELLED'}
        d = measure([(act, False)], 1024)
        for o in context.selected_objects:
            if o.type == 'MESH' and o != act:
                set_density(o, False, d, 1024)
        _settings(context).density_target = d * int(_settings(context).tex_size) / 1024
        return {'FINISHED'}


class UVT_OT_checker(_ObjOp, bpy.types.Operator):
    bl_idname = "uv_tools.checker"
    bl_label = "Toggle Checker"
    bl_description = ("Swap the materials of the selected objects for a UV grid checker, or "
                      "restore their original materials")

    def execute(self, context):
        obs = [o for o in context.selected_objects if o.type == 'MESH']
        if any(PROP in o for o in obs):
            for o in obs:
                checker_off(o)
            self.report({'INFO'}, "Original materials restored")
        else:
            mat = checker_material()
            for o in obs:
                checker_on(o, mat)
            self.report({'INFO'}, "Checker on (view in Material Preview or Texture color)")
        return {'FINISHED'}


class UVT_OT_straighten(_EditOp, bpy.types.Operator):
    bl_idname = "uv_tools.straighten"
    bl_label = "Straighten to Grid"
    bl_description = ("Straighten selected quads into a UV grid: squares up the active quad "
                      "(or the one in the middle of the selection) and follows it")

    mode: bpy.props.EnumProperty(name="Edge Length", items=(
        ('LENGTH_AVERAGE', "Length Average", ""), ('LENGTH', "Length", ""), ('EVEN', "Even", "")))

    def execute(self, context):
        done = 0
        for ob in context.objects_in_mode:
            bm = bmesh.from_edit_mesh(ob.data)
            uv = bm.loops.layers.uv.verify()
            quads = [f for f in bm.faces if f.select and len(f.verts) == 4]
            if not quads:
                continue
            act = bm.faces.active
            if act not in quads:
                c = sum((f.calc_center_median() for f in quads), Vector()) / len(quads)
                act = min(quads, key=lambda f: (f.calc_center_median() - c).length)
            rectify_quad(act, uv)
            bm.faces.active = act
            bmesh.update_edit_mesh(ob.data)
            done += 1
        if not done:
            self.report({'WARNING'}, "Select some quads")
            return {'CANCELLED'}
        return bpy.ops.uv.follow_active_quads(mode=self.mode)


class UVT_OT_select_flipped(_EditOp, bpy.types.Operator):
    bl_idname = "uv_tools.select_flipped"
    bl_label = "Select Flipped"
    bl_description = "Select faces whose UVs are mirrored (winding opposite to most faces)"

    def execute(self, context):
        n = 0
        for ob in context.objects_in_mode:
            bm = bmesh.from_edit_mesh(ob.data)
            uv = bm.loops.layers.uv.active
            if not uv:
                continue
            bad = set(flipped_faces([f for f in bm.faces if not f.hide], uv))
            for f in bm.faces:
                f.select_set(False)
            for f in bad:  # after deselecting all, or shared verts get deselected again
                f.select_set(True)
            bm.select_flush_mode()
            if hasattr(bm, "uv_select_sync_from_mesh"):
                bm.uv_select_sync_from_mesh()
            bmesh.update_edit_mesh(ob.data)
            n += len(bad)
        self.report({'INFO'}, f"{n} flipped face(s)")
        return {'FINISHED'}


class UVT_OT_uv_maps(_ObjOp, bpy.types.Operator):
    bl_idname = "uv_tools.uv_maps"
    bl_label = "UV Maps"
    bl_description = "Add, rename, remove, activate or sync UV maps on all selected objects"

    action: bpy.props.EnumProperty(items=(
        ('ADD', "Add", "Add the UV map where missing"),
        ('RENAME', "Rename", "Rename the UV map to the new name"),
        ('REMOVE', "Remove", "Remove the UV map"),
        ('ACTIVE', "Set Active", "Make the UV map active and active for render"),
        ('MATCH', "Match List", "Make every object have exactly these UV maps in this order")))
    name: bpy.props.StringProperty(name="Name", default="UVMap")
    new_name: bpy.props.StringProperty(name="New Name", default="UVMap")
    names: bpy.props.StringProperty(name="Names", default="UVMap, Lightmap",
                                    description="Comma separated UV map names, in order")

    def execute(self, context):
        n = 0
        for me in _meshes(context):
            layers = me.uv_layers
            lay = layers.get(self.name)
            if self.action == 'ADD' and not lay:
                n += layers.new(name=self.name) is not None
            elif self.action == 'RENAME' and lay and self.new_name not in layers:
                lay.name = self.new_name
                n += 1
            elif self.action == 'REMOVE' and lay:
                layers.remove(lay)
                n += 1
            elif self.action == 'ACTIVE' and lay:
                lay.active = lay.active_render = True
                n += 1
            elif self.action == 'MATCH':
                match_uv_maps(me, [x.strip() for x in self.names.split(",") if x.strip()])
                n += 1
        self.report({'INFO'}, f"Changed {n} mesh(es)")
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class UVT_Settings(bpy.types.PropertyGroup):
    seam_angle: bpy.props.FloatProperty(name="Angle", subtype='ANGLE', default=math.radians(60),
                                        min=0, max=math.pi)
    seam_sharp: bpy.props.BoolProperty(name="Include Sharp", default=True)
    method: UVT_OT_batch_unwrap.__annotations__["method"]
    angle_limit: UVT_OT_batch_unwrap.__annotations__["angle_limit"]
    pack: UVT_OT_batch_unwrap.__annotations__["pack"]
    keep_texel: UVT_OT_batch_unwrap.__annotations__["keep_texel"]
    margin: UVT_OT_batch_unwrap.__annotations__["margin"]
    tex_size: bpy.props.EnumProperty(name="Texture", default='1024', items=[
        (s, f"{s} px", "") for s in ("512", "1024", "2048", "4096", "8192")])
    density_target: bpy.props.FloatProperty(name="Target px/m", default=512, min=0.001)
    density_measured: bpy.props.FloatProperty(name="Measured", default=0)
    uv_name: bpy.props.StringProperty(name="Name", default="UVMap")
    uv_new_name: bpy.props.StringProperty(name="New", default="Lightmap")
    uv_names: bpy.props.StringProperty(name="Names", default="UVMap, Lightmap")


def draw_seams(self, context):
    s, col = _settings(context), self.layout.column(align=True)
    row = col.row(align=True)
    row.prop(s, "seam_angle")
    row.prop(s, "seam_sharp", text="", icon='SHARPCURVE')
    col.operator("uv_tools.angle_seams", icon='MOD_EDGESPLIT')
    col.operator("uv.seams_from_islands", icon='UV_ISLANDSEL')


def draw_unwrap(self, context):
    s, col = _settings(context), self.layout.column(align=True)
    col.prop(s, "method", text="")
    if s.method == 'SMART':
        col.prop(s, "angle_limit")
    col.prop(s, "pack", expand=True)
    col.prop(s, "margin")
    col.prop(s, "keep_texel")
    col.operator("uv_tools.batch_unwrap", icon='UV')


def draw_texel(self, context):
    s, col = _settings(context), self.layout.column(align=True)
    col.prop(s, "tex_size", text="")
    row = col.row(align=True)
    row.operator("uv_tools.texel_measure", icon='DRIVER_DISTANCE')
    row.label(text=f"{s.density_measured:.1f} px/m")
    col.prop(s, "density_target")
    col.operator("uv_tools.texel_set", icon='FULLSCREEN_ENTER')
    col.operator("uv_tools.texel_copy", icon='PASTEDOWN')


def draw_islands(self, context):
    col = self.layout.column(align=True)
    col.operator("uv_tools.checker", icon='TEXTURE')
    col.separator()
    col.operator("uv_tools.straighten", icon='GRID')
    col.operator("uv_tools.select_flipped", icon='MOD_MIRROR')
    col.operator("uv.select_overlap", icon='SELECT_INTERSECT')


def draw_maps(self, context):
    s, col = _settings(context), self.layout.column(align=True)
    col.prop(s, "uv_name")
    row = col.row(align=True)
    for act, icon in (('ADD', 'ADD'), ('REMOVE', 'REMOVE'), ('ACTIVE', 'RESTRICT_RENDER_OFF')):
        op = row.operator("uv_tools.uv_maps", text="", icon=icon)
        op.action, op.name = act, s.uv_name
    row = col.row(align=True)
    row.prop(s, "uv_new_name")
    op = row.operator("uv_tools.uv_maps", text="Rename")
    op.action, op.name, op.new_name = 'RENAME', s.uv_name, s.uv_new_name
    col.separator()
    col.prop(s, "uv_names", text="")
    op = col.operator("uv_tools.uv_maps", text="Make Same Name & Order", icon='SORTALPHA')
    op.action, op.names = 'MATCH', s.uv_names


SECTIONS = (("seams", "Seams", draw_seams), ("unwrap", "Batch Unwrap", draw_unwrap),
            ("texel", "Texel Density", draw_texel), ("islands", "Checker & Islands", draw_islands),
            ("maps", "UV Maps", draw_maps))


def _panels():
    out = []
    for space, cat, pre in (('IMAGE_EDITOR', "UV Tools", "IMAGE"), ('VIEW_3D', "Edit", "VIEW3D")):
        base = dict(bl_space_type=space, bl_region_type='UI', bl_category=cat)
        parent = f"UVT_PT_{pre}_main"
        out.append(type(parent, (bpy.types.Panel,), dict(
            base, bl_label="UV Tools", draw=lambda self, context: None,
            bl_options={'DEFAULT_CLOSED'} if space == 'VIEW_3D' else set())))
        for key, label, fn in SECTIONS:
            out.append(type(f"UVT_PT_{pre}_{key}", (bpy.types.Panel,), dict(
                base, bl_label=label, bl_parent_id=parent, draw=fn)))
    return out


_classes = (UVT_Settings, UVT_OT_angle_seams, UVT_OT_batch_unwrap, UVT_OT_texel_measure,
            UVT_OT_texel_set, UVT_OT_texel_copy, UVT_OT_checker, UVT_OT_straighten,
            UVT_OT_select_flipped, UVT_OT_uv_maps, *_panels())


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.uv_tools = bpy.props.PointerProperty(type=UVT_Settings)


def unregister():
    del bpy.types.Scene.uv_tools
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
