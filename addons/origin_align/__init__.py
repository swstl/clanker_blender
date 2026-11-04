"""Origin & Align: set origins without moving geometry, align / distribute / drop objects."""
import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty
from mathutils import Matrix, Vector

FRAC = {'MIN': 0.0, 'CENTER': 0.5, 'MAX': 1.0}
SIDES = [('MIN', "Min", ""), ('CENTER', "Center", ""), ('MAX', "Max", "")]
AXES = [('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")]
ALIGN_TARGETS = [('ACTIVE', "Active", "Active object's bounding box"),
                 ('CURSOR', "3D Cursor", ""), ('WORLD', "World Origin", "")]


# ---------------------------------------------------------------- core math

def world_bounds(ob, depsgraph):
    """World-space axis-aligned (min, max) of the evaluated object's bounding box."""
    pts = [ob.matrix_world @ Vector(c) for c in ob.evaluated_get(depsgraph).bound_box]
    return (Vector([min(p[i] for p in pts) for i in range(3)]),
            Vector([max(p[i] for p in pts) for i in range(3)]))


def blocker(ob):
    """Why the object's origin cannot be changed, or None."""
    d = ob.data
    if d is None or not hasattr(d, "transform"):
        return "no geometry"
    if d.library or ob.library:
        return "linked"
    if d.users - d.use_fake_user > 1:
        return "shared data"
    if ob.matrix_world.determinant() == 0:
        return "zero scale"
    return None


def set_frame(ob, new):
    """Give ob a new world matrix while its geometry and children stay put in world space."""
    old = ob.matrix_world.copy()
    delta = new.inverted() @ old
    if "shape_keys" in ob.data.bl_rna.functions["transform"].parameters:
        ob.data.transform(delta, shape_keys=True)
    else:
        ob.data.transform(delta)
    ob.matrix_world = new
    for ch in ob.children:  # child_world = parent_world @ parent_inverse @ basis
        ch.matrix_parent_inverse = delta @ ch.matrix_parent_inverse
    ob.data.update_tag()


def set_origin(ob, point):
    new = ob.matrix_world.copy()
    new.translation = point
    set_frame(ob, new)


def selected_points(context):
    """World positions of selected vertices on selected meshes (edit-mesh selection in Edit Mode)."""
    pts = []
    for ob in context.selected_objects:
        if ob.type != 'MESH':
            continue
        mw = ob.matrix_world
        if ob.mode == 'EDIT':
            pts += [mw @ v.co for v in bmesh.from_edit_mesh(ob.data).verts if v.select]
        else:
            pts += [mw @ v.co for v in ob.data.vertices if v.select]
    return pts


def run_origin(op, context, objects, frame_for):
    """Apply frame_for(ob) -> new world matrix to objects in Object Mode, then restore Edit Mode."""
    edit = context.object is not None and context.object.mode == 'EDIT'
    if edit:
        bpy.ops.object.mode_set(mode='OBJECT')
        context.view_layer.update()
    done, skipped = 0, []
    try:
        for ob in objects:
            why = blocker(ob)
            if why:
                skipped.append(f"{ob.name} ({why})")
                continue
            set_frame(ob, frame_for(ob))
            context.view_layer.update()
            done += 1
    finally:
        if edit:
            bpy.ops.object.mode_set(mode='EDIT')
    if skipped:
        op.report({'WARNING'}, "Skipped: " + ", ".join(skipped))
    return {'FINISHED'} if done else {'CANCELLED'}


def movable(context):
    """Selected editable objects, minus those whose ancestor is also selected (they move along)."""
    sel = set(context.selected_editable_objects)

    def has_selected_parent(o):
        while o.parent:
            o = o.parent
            if o in sel:
                return True
        return False
    return [o for o in sel if not has_selected_parent(o)]


def move(ob, delta):
    m = ob.matrix_world.copy()
    m.translation += delta
    ob.matrix_world = m


def axis_vec(i, value):
    v = Vector()
    v[i] = value
    return v


# ---------------------------------------------------------------- origin operators

class OA_OT_origin_bounds(bpy.types.Operator):
    bl_idname = "origin_align.origin_bounds"
    bl_label = "Origin to Bounds"
    bl_description = ("Move the origin of selected objects to a point on their local bounding box "
                      "without moving geometry")
    bl_options = {'REGISTER', 'UNDO'}
    x: EnumProperty(name="X", items=SIDES, default='CENTER')
    y: EnumProperty(name="Y", items=SIDES, default='CENTER')
    z: EnumProperty(name="Z", items=SIDES, default='CENTER')

    @classmethod
    def poll(cls, context):
        return context.selected_editable_objects

    def execute(self, context):
        dg = context.evaluated_depsgraph_get()

        def frame(ob):
            bb = [Vector(c) for c in ob.evaluated_get(dg).bound_box]
            lo = Vector([min(c[i] for c in bb) for i in range(3)])
            hi = Vector([max(c[i] for c in bb) for i in range(3)])
            f = (FRAC[self.x], FRAC[self.y], FRAC[self.z])
            m = ob.matrix_world.copy()
            m.translation = ob.matrix_world @ Vector([lo[i] + (hi[i] - lo[i]) * f[i] for i in range(3)])
            return m
        return run_origin(self, context, context.selected_editable_objects, frame)


class OA_OT_origin_to(bpy.types.Operator):
    bl_idname = "origin_align.origin_to"
    bl_label = "Set Origin"
    bl_description = "Move the origin of selected objects without moving geometry (works from Edit Mode)"
    bl_options = {'REGISTER', 'UNDO'}
    target: EnumProperty(name="Target", items=[
        ('CURSOR', "3D Cursor", "Origin to the 3D cursor"),
        ('WORLD', "World Origin", "Origin to the world origin"),
        ('SELECTION', "Selection", "Origin to the median of selected vertices"),
        ('SELECTION_BOUNDS', "Selection Bounds", "Origin to the bounding box center of selected vertices"),
        ('ACTIVE_VERT', "Active Vertex", "Origin to the active vertex (Edit Mode)"),
    ])

    @classmethod
    def poll(cls, context):
        return context.selected_editable_objects

    def execute(self, context):
        t = self.target
        if t == 'CURSOR':
            p = context.scene.cursor.location.copy()
        elif t == 'WORLD':
            p = Vector()
        elif t == 'ACTIVE_VERT':
            ob = context.object
            v = None
            if ob and ob.type == 'MESH' and ob.mode == 'EDIT':
                v = bmesh.from_edit_mesh(ob.data).select_history.active
            if not isinstance(v, bmesh.types.BMVert):
                self.report({'ERROR'}, "Needs an active vertex in Edit Mode")
                return {'CANCELLED'}
            p = ob.matrix_world @ v.co
        else:
            pts = selected_points(context)
            if not pts:
                self.report({'ERROR'}, "No selected vertices")
                return {'CANCELLED'}
            if t == 'SELECTION':
                p = sum(pts, Vector()) / len(pts)
            else:
                p = Vector([(min(q[i] for q in pts) + max(q[i] for q in pts)) / 2 for i in range(3)])

        def frame(ob):
            m = ob.matrix_world.copy()
            m.translation = p
            return m
        return run_origin(self, context, context.selected_editable_objects, frame)


class OA_OT_origin_face_normal(bpy.types.Operator):
    bl_idname = "origin_align.origin_face_normal"
    bl_label = "Align Origin to Face"
    bl_description = ("Rotate the active object's origin so local Z follows the active face normal "
                      "(X along its first edge); geometry stays put")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        ob = context.object
        me = ob.data
        if ob.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(me)
            f = bm.faces.active or next((f for f in bm.faces if f.select), None)
            face = f and (f.normal.copy(), [v.co.copy() for v in f.verts[:2]])
        else:
            i = me.polygons.active
            f = me.polygons[i] if 0 <= i < len(me.polygons) else None
            face = f and (f.normal.copy(), [me.vertices[v].co.copy() for v in f.vertices[:2]])
        if not face:
            self.report({'ERROR'}, "Needs an active or selected face")
            return {'CANCELLED'}
        n, (a, b) = face
        mw = ob.matrix_world
        z = (mw.to_3x3().inverted_safe().transposed() @ n).normalized()
        x = mw.to_3x3() @ (b - a)
        x = (x - z * x.dot(z)).normalized()
        if x.length == 0:
            x = z.orthogonal().normalized()
        rot = Matrix((x, z.cross(x), z)).transposed().to_4x4()
        new = Matrix.Translation(mw.translation) @ rot @ Matrix.Diagonal(mw.to_scale()).to_4x4()
        return run_origin(self, context, [ob], lambda _ob: new)


# ---------------------------------------------------------------- align operators

class OA_OT_align(bpy.types.Operator):
    bl_idname = "origin_align.align"
    bl_label = "Align Objects"
    bl_description = "Align the bounding boxes of selected objects to the active object, 3D cursor or world origin"
    bl_options = {'REGISTER', 'UNDO'}
    axis: EnumProperty(name="Axis", items=AXES)
    side: EnumProperty(name="Side", items=SIDES, default='CENTER')
    target: EnumProperty(name="Target", items=ALIGN_TARGETS)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_editable_objects

    def execute(self, context):
        i, f = "XYZ".index(self.axis), FRAC[self.side]
        dg = context.evaluated_depsgraph_get()
        active = context.active_object
        objs = movable(context)
        if self.target == 'ACTIVE':
            if not active:
                self.report({'ERROR'}, "No active object")
                return {'CANCELLED'}
            lo, hi = world_bounds(active, dg)
            ref = lo[i] + (hi[i] - lo[i]) * f
            objs = [o for o in objs if o != active]
        else:
            ref = context.scene.cursor.location[i] if self.target == 'CURSOR' else 0.0
        for ob in objs:
            lo, hi = world_bounds(ob, dg)
            move(ob, axis_vec(i, ref - (lo[i] + (hi[i] - lo[i]) * f)))
        return {'FINISHED'}


class OA_OT_distribute(bpy.types.Operator):
    bl_idname = "origin_align.distribute"
    bl_label = "Distribute Objects"
    bl_description = ("Space selected objects evenly along an axis by centers or by equal gaps; "
                      "the first and last stay put")
    bl_options = {'REGISTER', 'UNDO'}
    axis: EnumProperty(name="Axis", items=AXES)
    mode: EnumProperty(name="Mode", items=[('CENTERS', "Centers", "Equal distance between centers"),
                                           ('GAPS', "Gaps", "Equal gaps between bounding boxes")])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and len(context.selected_editable_objects) > 2

    def execute(self, context):
        i = "XYZ".index(self.axis)
        dg = context.evaluated_depsgraph_get()
        items = sorted(((o, *world_bounds(o, dg)) for o in movable(context)),
                       key=lambda t: t[1][i] + t[2][i])
        if len(items) < 3:
            self.report({'ERROR'}, "Select at least 3 objects")
            return {'CANCELLED'}
        n = len(items)
        if self.mode == 'CENTERS':
            c0 = (items[0][1][i] + items[0][2][i]) / 2
            step = ((items[-1][1][i] + items[-1][2][i]) / 2 - c0) / (n - 1)
            for k, (ob, lo, hi) in enumerate(items):
                move(ob, axis_vec(i, c0 + k * step - (lo[i] + hi[i]) / 2))
        else:
            size = sum(hi[i] - lo[i] for _, lo, hi in items)
            gap = (items[-1][2][i] - items[0][1][i] - size) / (n - 1)
            pos = items[0][1][i]
            for ob, lo, hi in items:
                move(ob, axis_vec(i, pos - lo[i]))
                pos += hi[i] - lo[i] + gap
        return {'FINISHED'}


class OA_OT_drop(bpy.types.Operator):
    bl_idname = "origin_align.drop"
    bl_label = "Drop Objects"
    bl_description = "Move selected objects so their bounding box bottom sits on Z=0, or stack them on the active object"
    bl_options = {'REGISTER', 'UNDO'}
    target: EnumProperty(name="Target", items=[('FLOOR', "Floor", "Bottom touches Z=0"),
                                               ('ACTIVE', "Stack on Active", "Stack on top of the active object")])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_editable_objects

    def execute(self, context):
        dg = context.evaluated_depsgraph_get()
        objs = movable(context)
        top = 0.0
        if self.target == 'ACTIVE':
            active = context.active_object
            if not active:
                self.report({'ERROR'}, "No active object")
                return {'CANCELLED'}
            top = world_bounds(active, dg)[1].z
            objs = [o for o in objs if o != active]
        items = sorted(((o, *world_bounds(o, dg)) for o in objs), key=lambda t: t[1].z)
        for ob, lo, hi in items:
            move(ob, Vector((0, 0, top - lo.z)))
            if self.target == 'ACTIVE':
                top += hi.z - lo.z
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class OA_Settings(bpy.types.PropertyGroup):
    x: EnumProperty(name="X", items=SIDES, default='CENTER')
    y: EnumProperty(name="Y", items=SIDES, default='CENTER')
    z: EnumProperty(name="Z", items=SIDES, default='MIN')
    axis: EnumProperty(name="Axis", items=AXES)
    target: EnumProperty(name="Align To", items=ALIGN_TARGETS)


def op(layout, idname, text, icon='NONE', **props):
    o = layout.operator("origin_align." + idname, text=text, icon=icon)
    for k, v in props.items():
        setattr(o, k, v)
    return o


class OA_PT_panel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Align"
    bl_label = "Origin & Align"

    def draw(self, context):
        s = context.window_manager.origin_align
        lay = self.layout

        col = lay.column(align=True)
        col.label(text="Origin")
        row = col.row(align=True)
        op(row, "origin_bounds", "Bottom", z='MIN')
        op(row, "origin_bounds", "Center")
        op(row, "origin_bounds", "Top", z='MAX')
        box = lay.box().column(align=True)
        for a in "xyz":
            box.row(align=True).prop(s, a, expand=True)
        op(box, "origin_bounds", "Origin to Bounds Point", x=s.x, y=s.y, z=s.z)
        col = lay.column(align=True)
        row = col.row(align=True)
        op(row, "origin_to", "Cursor", 'PIVOT_CURSOR', target='CURSOR')
        op(row, "origin_to", "World", 'WORLD', target='WORLD')
        row = col.row(align=True)
        op(row, "origin_to", "Selection", target='SELECTION')
        op(row, "origin_to", "Sel. Bounds", target='SELECTION_BOUNDS')
        row = col.row(align=True)
        op(row, "origin_to", "Active Vertex", 'VERTEXSEL', target='ACTIVE_VERT')
        op(row, "origin_face_normal", "Face Normal", 'ORIENTATION_NORMAL')

        col = lay.column(align=True)
        col.label(text="Align")
        col.prop(s, "target", text="")
        col.row(align=True).prop(s, "axis", expand=True)
        row = col.row(align=True)
        for side in ('MIN', 'CENTER', 'MAX'):
            op(row, "align", side.title(), axis=s.axis, side=side, target=s.target)
        col.label(text="Distribute")
        row = col.row(align=True)
        op(row, "distribute", "Centers", axis=s.axis, mode='CENTERS')
        op(row, "distribute", "Gaps", axis=s.axis, mode='GAPS')
        col.label(text="Drop")
        row = col.row(align=True)
        op(row, "drop", "Floor", 'TRIA_DOWN_BAR', target='FLOOR')
        op(row, "drop", "Stack on Active", target='ACTIVE')


class OA_MT_pie(bpy.types.Menu):
    bl_idname = "OA_MT_pie"
    bl_label = "Origin & Align"

    def draw(self, context):
        pie = self.layout.menu_pie()  # order: W E S N NW NE SW SE
        op(pie, "origin_bounds", "Origin to Bottom", 'TRIA_DOWN', z='MIN')
        op(pie, "origin_bounds", "Origin to Center", 'PIVOT_BOUNDBOX')
        op(pie, "origin_to", "Origin to Cursor", 'PIVOT_CURSOR', target='CURSOR')
        op(pie, "origin_bounds", "Origin to Top", 'TRIA_UP', z='MAX')
        op(pie, "origin_to", "Origin to Selection", 'RESTRICT_SELECT_OFF', target='SELECTION')
        op(pie, "drop", "Drop to Floor", 'TRIA_DOWN_BAR', target='FLOOR')
        op(pie, "origin_to", "Origin to Active Vertex", 'VERTEXSEL', target='ACTIVE_VERT')
        op(pie, "origin_face_normal", "Align Origin to Face", 'ORIENTATION_NORMAL')


_keymaps = []


def keymap_remove():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def keymap_add():
    keymap_remove()
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc:
        return
    for name in ("Object Mode", "Mesh"):
        km = kc.keymaps.new(name=name, space_type='EMPTY')
        kmi = km.keymap_items.new("wm.call_menu_pie", 'O', 'PRESS', alt=True, shift=True)
        kmi.properties.name = OA_MT_pie.bl_idname
        _keymaps.append((km, kmi))


class OA_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    use_keymap: BoolProperty(
        name="Pie menu on Alt+Shift+O", default=True,
        description="Object Mode and Edit Mesh shortcut for the Origin & Align pie",
        update=lambda self, ctx: keymap_add() if self.use_keymap else keymap_remove())

    def draw(self, context):
        self.layout.prop(self, "use_keymap")


classes = (OA_Settings, OA_OT_origin_bounds, OA_OT_origin_to, OA_OT_origin_face_normal,
           OA_OT_align, OA_OT_distribute, OA_OT_drop, OA_PT_panel, OA_MT_pie, OA_Prefs)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.origin_align = bpy.props.PointerProperty(type=OA_Settings)
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None or addon.preferences.use_keymap:
        keymap_add()


def unregister():
    keymap_remove()
    del bpy.types.WindowManager.origin_align
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
