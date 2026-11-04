"""Cursor Tools: undo cursor moves (history), bookmark positions, snap cursor with rotation."""
import bmesh
import bpy
from mathutils import Matrix, Vector

HISTORY_MAX = 30
_last = [None]


class CT_Mark(bpy.types.PropertyGroup):
    location: bpy.props.FloatVectorProperty(size=3)
    rotation: bpy.props.FloatVectorProperty(size=4, default=(1, 0, 0, 0))


def _cur(scene):
    c = scene.cursor
    return tuple(c.location), tuple(c.rotation_quaternion) if c.rotation_mode == 'QUATERNION' \
        else tuple(c.rotation_euler.to_quaternion())


def record(scene):
    """Push the cursor onto the history if it moved since the last record. Returns True if pushed."""
    state = _cur(scene)
    if _last[0] is not None and _rounded(state) == _rounded(_last[0]):
        return False
    hist = scene.cursor_tools_history
    if _last[0] is not None:
        m = hist.add()
        m.location, m.rotation = _last[0]
        while len(hist) > HISTORY_MAX:
            hist.remove(0)
    _last[0] = state
    return True


def _rounded(state):
    return tuple(round(v, 5) for part in state for v in part)


def apply_mark(scene, m):
    c = scene.cursor
    c.location = m.location
    q = Vector(m.rotation)
    if c.rotation_mode == 'QUATERNION':
        c.rotation_quaternion = q
    else:
        from mathutils import Quaternion
        c.rotation_euler = Quaternion(q).to_euler(c.rotation_mode)


def _timer():
    try:
        for sc in bpy.data.scenes:
            if sc == bpy.context.scene:
                record(sc)
    except Exception:
        pass
    return 0.5


class CT_OT_back(bpy.types.Operator):
    bl_idname = "cursor_tools.back"
    bl_label = "Previous Cursor Position"
    bl_description = "Move the 3D cursor back to where it was before the last move"

    def execute(self, context):
        sc = context.scene
        record(sc)
        hist = sc.cursor_tools_history
        if not hist:
            self.report({'INFO'}, "No cursor history")
            return {'CANCELLED'}
        apply_mark(sc, hist[-1])
        hist.remove(len(hist) - 1)
        _last[0] = _cur(sc)  # don't record the jump itself
        return {'FINISHED'}


class CT_OT_bookmark(bpy.types.Operator):
    bl_idname = "cursor_tools.bookmark"
    bl_label = "Bookmark Cursor"
    bl_description = "Save the current cursor location/rotation under a name"

    name: bpy.props.StringProperty(name="Name", default="Mark")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        m = context.scene.cursor_tools_marks.add()
        m.name = self.name
        m.location, m.rotation = _cur(context.scene)
        return {'FINISHED'}


class CT_OT_goto(bpy.types.Operator):
    bl_idname = "cursor_tools.goto"
    bl_label = "Go to Bookmark"
    bl_description = "Put the cursor at this bookmark"

    index: bpy.props.IntProperty()
    remove: bpy.props.BoolProperty()

    def execute(self, context):
        marks = context.scene.cursor_tools_marks
        if not 0 <= self.index < len(marks):
            return {'CANCELLED'}
        if self.remove:
            marks.remove(self.index)
        else:
            apply_mark(context.scene, marks[self.index])
        return {'FINISHED'}


def active_face_frame(ob):
    """(world center, world rotation matrix 3x3) of the active/selected face of an edit mesh, or None."""
    bm = bmesh.from_edit_mesh(ob.data)
    f = bm.faces.active if bm.faces.active and bm.faces.active.select else next((f for f in bm.faces if f.select), None)
    if f is None:
        return None
    mw = ob.matrix_world
    n = (mw.to_3x3().inverted_safe().transposed() @ f.normal).normalized()
    edge = max(f.edges, key=lambda e: e.calc_length())
    t = (mw.to_3x3() @ (edge.verts[1].co - edge.verts[0].co))
    t = (t - n * t.dot(n)).normalized()
    b = n.cross(t)
    rot = Matrix((t, b, n)).transposed()
    return mw @ f.calc_center_median(), rot


class CT_OT_to_face(bpy.types.Operator):
    bl_idname = "cursor_tools.to_face"
    bl_label = "Cursor to Face (With Rotation)"
    bl_description = "Place the cursor on the active face, Z along its normal, X along its longest edge"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        res = active_face_frame(context.edit_object)
        if not res:
            self.report({'WARNING'}, "Select a face")
            return {'CANCELLED'}
        c = context.scene.cursor
        c.location = res[0]
        c.matrix = Matrix.Translation(res[0]) @ res[1].to_4x4()
        return {'FINISHED'}


class CT_OT_to_active(bpy.types.Operator):
    bl_idname = "cursor_tools.to_active"
    bl_label = "Cursor to Active (With Rotation)"
    bl_description = "Copy the active object's location and rotation to the cursor"

    @classmethod
    def poll(cls, context):
        return context.active_object

    def execute(self, context):
        mw = context.active_object.matrix_world
        context.scene.cursor.matrix = Matrix.Translation(mw.translation) @ mw.to_quaternion().to_matrix().to_4x4()
        return {'FINISHED'}


class CT_OT_reset_rotation(bpy.types.Operator):
    bl_idname = "cursor_tools.reset_rotation"
    bl_label = "Reset Cursor Rotation"
    bl_description = "Keep location, clear rotation"

    def execute(self, context):
        c = context.scene.cursor
        c.rotation_euler = (0, 0, 0)
        c.rotation_quaternion = (1, 0, 0, 0)
        return {'FINISHED'}


class CT_PT_panel(bpy.types.Panel):
    bl_label = "Cursor Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        sc = context.scene
        col = self.layout.column(align=True)
        col.operator("cursor_tools.back", icon='LOOP_BACK')
        col.operator("cursor_tools.to_active", icon='OBJECT_ORIGIN')
        col.operator("cursor_tools.to_face", icon='FACESEL')
        col.operator("cursor_tools.reset_rotation", icon='ORIENTATION_CURSOR')
        col.separator()
        col.operator("cursor_tools.bookmark", icon='BOOKMARKS')
        for i, m in enumerate(sc.cursor_tools_marks):
            row = col.row(align=True)
            row.operator("cursor_tools.goto", text=m.name, icon='PIVOT_CURSOR').index = i
            op = row.operator("cursor_tools.goto", text="", icon='X')
            op.index, op.remove = i, True


def snap_menu(self, context):
    self.layout.separator()
    self.layout.operator("cursor_tools.back", icon='LOOP_BACK')
    self.layout.operator("cursor_tools.to_active", icon='OBJECT_ORIGIN')
    if context.mode == 'EDIT_MESH':
        self.layout.operator("cursor_tools.to_face", icon='FACESEL')


_classes = (CT_Mark, CT_OT_back, CT_OT_bookmark, CT_OT_goto, CT_OT_to_face, CT_OT_to_active,
            CT_OT_reset_rotation, CT_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.cursor_tools_history = bpy.props.CollectionProperty(type=CT_Mark)
    S.cursor_tools_marks = bpy.props.CollectionProperty(type=CT_Mark)
    bpy.types.VIEW3D_MT_snap.append(snap_menu)
    if not bpy.app.background:
        bpy.app.timers.register(_timer, first_interval=1, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.types.VIEW3D_MT_snap.remove(snap_menu)
    del bpy.types.Scene.cursor_tools_marks
    del bpy.types.Scene.cursor_tools_history
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    _last[0] = None
