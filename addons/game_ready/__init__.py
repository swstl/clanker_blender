"""Game Ready: the per-asset chores before export (Unreal UCX_/UBX_, Godot -col, Unity LODGroup names)."""
import bmesh
import bpy
from mathutils import Vector

ENGINES = [('UNREAL', "Unreal", "UCX_/UBX_/USP_ prefixes, SOCKET_, _LOD1"),
           ('GODOT', "Godot", "-convcolonly / -colonly suffixes, _LOD1 (import hints)"),
           ('UNITY', "Unity", "_Collider suffix, _LOD1 (LODGroup naming)")]


def collision_name(engine, kind, base, index):
    if engine == 'UNREAL':
        prefix = {'HULL': "UCX", 'BOX': "UBX", 'SPHERE': "USP"}[kind]
        return f"{prefix}_{base}_{index:02d}"
    if engine == 'GODOT':
        return f"{base}_{index:02d}-{'convcolonly' if kind == 'HULL' else 'colonly'}"
    return f"{base}_Collider_{index:02d}"


def lod_name(base, level):
    return f"{base}_LOD{level}"


def _next_index(engine, kind, base):
    i = 1
    while collision_name(engine, kind, base, i) in bpy.data.objects:
        i += 1
    return i


def make_collision(context, ob, kind, engine, offset=0.0, selected_only=False):
    """Create a collision object for ob (world-space geometry), parented to ob. Returns it."""
    dg = context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    pts = [v.co.copy() for v in me.vertices if (v.select or not selected_only)]
    ev.to_mesh_clear()
    if len(pts) < 4:
        raise ValueError(f"{ob.name}: not enough vertices")
    bm = bmesh.new()
    if kind == 'BOX':
        mn = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
        mx = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
        bmesh.ops.create_cube(bm, size=1)
        for v in bm.verts:
            v.co = Vector(tuple(mn[i] + (v.co[i] + 0.5) * (mx[i] - mn[i]) for i in range(3)))
    elif kind == 'SPHERE':
        c = sum(pts, Vector()) / len(pts)
        r = max((p - c).length for p in pts)
        bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=r)
        bmesh.ops.translate(bm, vec=c, verts=bm.verts)
    else:
        for p in pts:
            bm.verts.new(p)
        res = bmesh.ops.convex_hull(bm, input=bm.verts[:])
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
        _ = res
    if offset:
        for v in bm.verts:
            v.co += v.normal * offset
    name = collision_name(engine, kind, ob.name, _next_index(engine, kind, ob.name))
    cme = bpy.data.meshes.new(name)
    bm.to_mesh(cme)
    bm.free()
    col = bpy.data.objects.new(name, cme)
    for c in ob.users_collection:
        c.objects.link(col)
    col.parent = ob  # same local space as ob's mesh data
    col.display_type = 'WIRE'
    col.hide_render = True
    return col


class GR_OT_collision(bpy.types.Operator):
    bl_idname = "game_ready.collision"
    bl_label = "Add Collision"
    bl_description = "Create an engine-named collision mesh (convex hull, box or sphere) for each selected mesh"
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(items=[('HULL', "Convex Hull", ""), ('BOX', "Box", ""), ('SPHERE', "Sphere", "")])
    offset: bpy.props.FloatProperty(name="Inflate", default=0.0, unit='LENGTH')
    selected_verts: bpy.props.BoolProperty(name="Selected Vertices Only",
                                           description="In Edit Mode: build from selected vertices (multiple hulls per object)")

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects) or context.mode == 'EDIT_MESH'

    def execute(self, context):
        engine = context.scene.game_ready_engine
        was_edit = context.mode == 'EDIT_MESH'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
        made = []
        for ob in [o for o in context.selected_objects if o.type == 'MESH']:
            try:
                made.append(make_collision(context, ob, self.kind, engine, self.offset,
                                           self.selected_verts or was_edit))
            except ValueError as e:
                self.report({'WARNING'}, str(e))
        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')
        self.report({'INFO'}, f"Created {len(made)} collision meshes")
        return {'FINISHED'}


def make_lods(context, ob, levels, ratio):
    """Duplicate ob into LOD1..levels with a Decimate (collapse) modifier, applied. Renames ob to LOD0."""
    base = ob.name[:-5] if ob.name.endswith("_LOD0") else ob.name
    out = []
    for lvl in range(1, levels + 1):
        name = lod_name(base, lvl)
        old = bpy.data.objects.get(name)
        if old:
            bpy.data.objects.remove(old)
        dup = ob.copy()
        dup.data = ob.data.copy()
        dup.name = dup.data.name = name
        for c in ob.users_collection:
            c.objects.link(dup)
        md = dup.modifiers.new("LOD", 'DECIMATE')
        md.ratio = ratio ** lvl
        md.use_collapse_triangulate = True
        dg = context.evaluated_depsgraph_get()
        me = bpy.data.meshes.new_from_object(dup.evaluated_get(dg))
        old_me = dup.data
        dup.modifiers.clear()
        dup.data = me
        me.name = name
        bpy.data.meshes.remove(old_me)
        out.append(dup)
    ob.name = lod_name(base, 0)
    return out


class GR_OT_lods(bpy.types.Operator):
    bl_idname = "game_ready.lods"
    bl_label = "Generate LODs"
    bl_description = "Create decimated _LOD1.._LODn copies of each selected mesh (original becomes _LOD0)"
    bl_options = {'REGISTER', 'UNDO'}

    levels: bpy.props.IntProperty(name="Levels", default=3, min=1, max=8)
    ratio: bpy.props.FloatProperty(name="Ratio per Level", default=0.5, min=0.01, max=0.99)

    def execute(self, context):
        n = 0
        for ob in [o for o in context.selected_objects if o.type == 'MESH']:
            n += len(make_lods(context, ob, self.levels, self.ratio))
        self.report({'INFO'}, f"Created {n} LODs")
        return {'FINISHED'}


class GR_OT_socket(bpy.types.Operator):
    bl_idname = "game_ready.socket"
    bl_label = "Add Socket at Cursor"
    bl_description = "Add an engine socket/attachment empty at the 3D cursor, parented to the active object"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="Attach")

    @classmethod
    def poll(cls, context):
        return context.active_object

    def execute(self, context):
        target = context.active_object
        prefix = "SOCKET_" if context.scene.game_ready_engine == 'UNREAL' else ""
        e = bpy.data.objects.new(prefix + self.name, None)
        e.empty_display_type = 'ARROWS'
        e.empty_display_size = 0.2
        for c in target.users_collection:
            c.objects.link(e)
        e.matrix_world = context.scene.cursor.matrix
        mw = e.matrix_world.copy()
        e.parent = target
        e.matrix_parent_inverse = target.matrix_world.inverted()
        e.matrix_world = mw
        return {'FINISHED'}


def add_lightmap_uv(context, ob, name="Lightmap", margin=0.05):
    me = ob.data
    uv = me.uv_layers.get(name) or me.uv_layers.new(name=name)
    if uv is None:
        raise ValueError(f"{ob.name}: max UV maps reached")
    render = next((u for u in me.uv_layers if u.active_render), None)
    me.uv_layers.active = uv
    with context.temp_override(active_object=ob, object=ob, selected_objects=[ob],
                               selected_editable_objects=[ob]):
        bpy.ops.uv.lightmap_pack(PREF_CONTEXT='ALL_FACES', PREF_MARGIN_DIV=margin)
    if render:
        render.active_render = True  # keep the texture UV as the render/export default
    me.uv_layers.active = me.uv_layers[0]
    return uv


class GR_OT_lightmap(bpy.types.Operator):
    bl_idname = "game_ready.lightmap_uv"
    bl_label = "Add Lightmap UV"
    bl_description = "Add a second UV map packed without overlaps (for baked lighting in engines)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        n = 0
        for ob in [o for o in context.selected_objects if o.type == 'MESH']:
            add_lightmap_uv(context, ob)
            n += 1
        self.report({'INFO'}, f"Lightmap UVs on {n} objects")
        return {'FINISHED'}


class GR_PT_panel(bpy.types.Panel):
    bl_label = "Game Ready"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Export"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.prop(context.scene, "game_ready_engine", text="Engine")
        col.label(text="Collision:")
        row = col.row(align=True)
        for k, label in (('HULL', "Hull"), ('BOX', "Box"), ('SPHERE', "Sphere")):
            row.operator("game_ready.collision", text=label).kind = k
        col.separator()
        col.operator("game_ready.lods", icon='MOD_DECIM')
        col.operator("game_ready.socket", icon='EMPTY_ARROWS')
        col.operator("game_ready.lightmap_uv", icon='UV')


_classes = (GR_OT_collision, GR_OT_lods, GR_OT_socket, GR_OT_lightmap, GR_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.game_ready_engine = bpy.props.EnumProperty(name="Engine", items=ENGINES)


def unregister():
    del bpy.types.Scene.game_ready_engine
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
