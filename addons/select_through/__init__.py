"""Select Through: box/lasso select occluded Edit Mesh elements without turning on X-Ray."""
import bmesh
import bpy
import numpy as np
from mathutils import Vector


def points_in_polygon(pts, poly):
    """Even-odd test: (N,2) points against a polygon (list of (x, y)). Returns bool (N,)."""
    poly = np.asarray(poly, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), dtype=bool)
    for (x1, y1), (x2, y2) in zip(poly, np.roll(poly, -1, axis=0)):
        crosses = (y1 > y) != (y2 > y)
        with np.errstate(divide='ignore', invalid='ignore'):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (x < xi)
    return inside


def _to_camera(persp_matrix, co):
    """Per-vertex world vectors pointing from the vertex toward the viewer."""
    h = persp_matrix.inverted() @ Vector((0, 0, 1, 0))  # clip-space point at w=0 = the eye
    if abs(h.w) > 1e-9:  # perspective: eye position
        return np.array(h.xyz / h.w) - co
    d = h.xyz  # orthographic: a direction, flip so it points toward smaller depth (the viewer)
    if (persp_matrix @ Vector((*d, 0))).z > 0:
        d = -d
    return np.broadcast_to(np.array(d), co.shape)


def select_in_polygon(objects, poly, persp_matrix, region_size, mode='SET',
                      select_mode=None, use_any=False, front_only=False):
    """Select edit-mesh elements whose verts project inside `poly` (region pixels), ignoring occlusion.

    mode: 'SET' (new selection), 'ADD' (extend), 'SUB' (subtract).
    select_mode: (vert, edge, face) bools, default the scene's mesh select mode.
    use_any: edges/faces count when any vert is inside (default: all verts).
    front_only: only verts whose normal faces the viewer (cheap visibility guess).
    Returns the number of elements hit.
    """
    if select_mode is None:
        select_mode = tuple(bpy.context.tool_settings.mesh_select_mode)
    P = np.array(persp_matrix, dtype=np.float64)
    w, h = region_size
    hits = 0
    for ob in objects:
        bm = bmesh.from_edit_mesh(ob.data)
        bm.verts.index_update()
        if not bm.verts:
            continue
        mw = ob.matrix_world
        co = np.array([v.co for v in bm.verts], dtype=np.float64)
        co = co @ np.array(mw.to_3x3()).T + np.array(mw.translation)
        clip = np.c_[co, np.ones(len(co))] @ P.T
        cw = clip[:, 3]
        ok = cw > 1e-9  # behind the viewer: never selectable
        safe = np.where(ok, cw, 1.0)
        px = np.c_[(clip[:, 0] / safe + 1) * 0.5 * w, (clip[:, 1] / safe + 1) * 0.5 * h]
        inside = ok & points_in_polygon(px, poly)
        if front_only:
            nm = np.array(mw.to_3x3().inverted_safe().transposed())
            nrm = np.array([v.normal for v in bm.verts], dtype=np.float64) @ nm.T
            inside &= np.einsum('ij,ij->i', nrm, _to_camera(persp_matrix, co)) > 0

        if select_mode[0]:
            elems = [v for v in bm.verts if inside[v.index]]
        else:
            test = any if use_any else all
            seq = bm.edges if select_mode[1] else bm.faces
            elems = [e for e in seq if test(inside[v.index] for v in e.verts)]
        elems = [e for e in elems if not e.hide]
        hits += len(elems)

        if mode == 'SET':
            for seq in (bm.verts, bm.edges, bm.faces):
                for e in seq:
                    e.select = False
        for e in elems:
            e.select_set(mode != 'SUB')
        bm.select_flush_mode()
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)
    return hits


class ST_Point(bpy.types.PropertyGroup):
    loc: bpy.props.FloatVectorProperty(size=2)


class _SelectThrough:
    bl_options = {'REGISTER', 'UNDO'}
    lasso = False

    mode: bpy.props.EnumProperty(items=(
        ('SET', "Set", "New selection"),
        ('ADD', "Extend", "Add to selection (hold Shift)"),
        ('SUB', "Subtract", "Remove from selection (hold Ctrl)")))
    use_any: bpy.props.BoolProperty(
        name="Touch", description="Edges/faces count when any of their vertices is inside (else all)")
    front_only: bpy.props.BoolProperty(
        name="Front Faces Only",
        description="Only vertices whose normal faces the view: a cheap visibility guess without X-Ray")
    path: bpy.props.CollectionProperty(type=ST_Point, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.space_data and context.space_data.type == 'VIEW_3D'
                and context.region and context.region.type == 'WINDOW')  # not the header (F3 there)

    def execute(self, context):
        if len(self.path) < 3 or context.region_data is None:
            return {'CANCELLED'}
        r = context.region
        n = select_in_polygon(context.objects_in_mode, [p.loc[:] for p in self.path],
                              context.region_data.perspective_matrix, (r.width, r.height),
                              self.mode, use_any=self.use_any, front_only=self.front_only)
        self.report({'INFO'}, f"{n} hit")
        return {'FINISHED'}

    def invoke(self, context, event):
        self.start = None
        self.pts = []
        self._region = context.region
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        context.workspace.status_text_set(
            "LMB drag: select through  |  Shift: extend  Ctrl: subtract  |  RMB/Esc: cancel")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
        if context.workspace:
            context.workspace.status_text_set(None)
        if context.area:
            context.area.tag_redraw()

    def cancel(self, context):  # file load / window close: don't leave a handler on a freed operator
        self._finish(context)

    def _outline(self):
        if not self.pts:
            return []
        if self.lasso:
            return self.pts
        (x1, y1), (x2, y2) = self.pts[0], self.pts[-1]
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

    def _draw(self):
        poly = self._outline()
        if len(poly) < 2 or bpy.context.region != self._region:  # handler runs in every 3D view
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        shader.uniform_float("color", (1, 1, 1, 0.9))
        batch_for_shader(shader, 'LINE_STRIP', {"pos": poly + [poly[0]]}).draw(shader)
        gpu.state.blend_set('NONE')

    def modal(self, context, event):
        context.area.tag_redraw()
        pos = (event.mouse_region_x, event.mouse_region_y)
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.pts = [pos, pos]
        elif event.type == 'MOUSEMOVE' and self.pts:
            if self.lasso:
                self.pts.append(pos)
            else:
                self.pts[-1] = pos
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.pts:
            self._finish(context)
            self.mode = 'SUB' if event.ctrl else 'ADD' if event.shift else 'SET'
            self.path.clear()
            for p in self._outline():
                self.path.add().loc = p
            return self.execute(context)
        return {'RUNNING_MODAL'}


class ST_OT_box(_SelectThrough, bpy.types.Operator):
    bl_idname = "select_through.box"
    bl_label = "Box Select Through"
    bl_description = "Box select vertices/edges/faces including occluded ones, without X-Ray"


class ST_OT_lasso(_SelectThrough, bpy.types.Operator):
    bl_idname = "select_through.lasso"
    bl_label = "Lasso Select Through"
    bl_description = "Lasso select vertices/edges/faces including occluded ones, without X-Ray"
    lasso = True


_keymaps = []


def _set_keymaps(on):
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    kc = bpy.context.window_manager.keyconfigs.addon
    if not (on and kc):
        return
    km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
    for op, key in (("select_through.box", 'B'), ("select_through.lasso", 'L')):
        _keymaps.append((km, km.keymap_items.new(op, key, 'PRESS', shift=True, alt=True)))


class ST_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    use_hotkeys: bpy.props.BoolProperty(
        name="Hotkeys (Shift+Alt+B box, Shift+Alt+L lasso)", default=True,
        update=lambda self, ctx: _set_keymaps(self.use_hotkeys))

    def draw(self, context):
        self.layout.prop(self, "use_hotkeys")


def _menu(self, context):
    self.layout.separator()
    self.layout.operator("select_through.box", icon='SELECT_SET')
    self.layout.operator("select_through.lasso", icon='SELECT_SET')


_classes = (ST_Point, ST_OT_box, ST_OT_lasso, ST_Prefs)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_select_edit_mesh.append(_menu)
    _set_keymaps(bpy.context.preferences.addons[__package__].preferences.use_hotkeys)


def unregister():
    _set_keymaps(False)
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
