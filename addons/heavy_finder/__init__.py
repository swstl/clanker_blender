"""Heavy Object Finder: rank objects by evaluated triangle count and fix the worst offenders."""
import bpy

HEAVY_MODS = {'SUBSURF', 'MULTIRES', 'REMESH', 'ARRAY', 'BOOLEAN', 'SOLIDIFY', 'BEVEL',
              'SCREW', 'PARTICLE_SYSTEM', 'NODES', 'CLOTH', 'OCEAN'}


def tri_count(ob, depsgraph):
    """Triangles of the evaluated object (after modifiers), 0 for non-geometry."""
    if ob.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
        return 0
    ev = ob.evaluated_get(depsgraph)
    try:
        me = ev.to_mesh()
    except RuntimeError:
        return 0
    try:
        if me is None:
            return 0
        me.calc_loop_triangles()
        return len(me.loop_triangles)
    finally:
        ev.to_mesh_clear()


class HF_Item(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object)
    tris: bpy.props.IntProperty()
    base_tris: bpy.props.IntProperty()


def scan(context, limit=50):
    dg = context.evaluated_depsgraph_get()
    rows = []
    for ob in context.view_layer.objects:
        if not ob.visible_get():
            continue
        t = tri_count(ob, dg)
        if t:
            base = len(ob.data.polygons) if ob.type == 'MESH' else 0
            rows.append((t, base, ob))
    rows.sort(key=lambda r: r[0], reverse=True)
    items = context.scene.heavy_finder_items
    items.clear()
    for t, base, ob in rows[:limit]:
        it = items.add()
        it.obj, it.tris, it.base_tris = ob, t, base
    return sum(r[0] for r in rows)


class HF_OT_scan(bpy.types.Operator):
    bl_idname = "heavy_finder.scan"
    bl_label = "Scan Scene"
    bl_description = "Count triangles after modifiers for every visible object"

    def execute(self, context):
        total = scan(context)
        context.scene.heavy_finder_total = total
        self.report({'INFO'}, f"{total:,} triangles in view")
        return {'FINISHED'}


def _targets(context, only_heavy_list):
    if only_heavy_list:
        return [it.obj for it in context.scene.heavy_finder_items[:context.scene.heavy_finder_top] if it.obj]
    return list(context.selected_objects)


class HF_OT_tame(bpy.types.Operator):
    bl_idname = "heavy_finder.tame"
    bl_label = "Tame"
    bl_description = "Make objects cheaper to draw in the viewport (render is not affected)"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[
        ('BOUNDS', "Display as Bounds", ""),
        ('WIRE', "Display as Wire", ""),
        ('TEXTURED', "Restore Display", "Display as textured again"),
        ('MODS_OFF', "Heavy Modifiers Off in Viewport", "Disable subdivision, booleans, arrays... in viewport only"),
        ('MODS_ON', "Modifiers On in Viewport", ""),
        ('SUBD_LOW', "Subdivision Viewport Level 0", "Keep render levels"),
    ])
    use_list: bpy.props.BoolProperty(name="Top of List", default=False, options={'SKIP_SAVE'},
                                     description="Apply to the top N heaviest objects instead of the selection")

    def execute(self, context):
        n = 0
        for ob in _targets(context, self.use_list):
            if self.action in {'BOUNDS', 'WIRE', 'TEXTURED'}:
                ob.display_type = self.action
            for md in ob.modifiers:
                if self.action == 'MODS_OFF' and md.type in HEAVY_MODS:
                    md.show_viewport = False
                elif self.action == 'MODS_ON':
                    md.show_viewport = True
                elif self.action == 'SUBD_LOW' and md.type in {'SUBSURF', 'MULTIRES'}:
                    md.levels = 0
            n += 1
        self.report({'INFO'}, f"Updated {n} objects")
        return {'FINISHED'}


class HF_OT_select(bpy.types.Operator):
    bl_idname = "heavy_finder.select"
    bl_label = "Select"
    bl_description = "Select this object and make it active"

    name: bpy.props.StringProperty()

    def execute(self, context):
        ob = bpy.data.objects.get(self.name)
        if not ob:
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


class HF_UL_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if not item.obj:
            layout.label(text="(deleted)")
            return
        row = layout.row(align=True)
        row.operator("heavy_finder.select", text=item.obj.name, icon='OBJECT_DATA', emboss=False).name = item.obj.name
        row.label(text=f"{item.tris:,}")


class HF_PT_panel(bpy.types.Panel):
    bl_label = "Heavy Objects"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"

    def draw(self, context):
        sc = context.scene
        col = self.layout.column()
        col.operator("heavy_finder.scan", icon='VIEWZOOM')
        if sc.heavy_finder_items:
            col.label(text=f"Visible triangles: {sc.heavy_finder_total:,}")
            col.template_list("HF_UL_items", "", sc, "heavy_finder_items", sc, "heavy_finder_index", rows=6)
        col.separator()
        col.label(text="Selected objects:")
        grid = col.grid_flow(columns=2, align=True)
        for a, label in (('BOUNDS', "Bounds"), ('WIRE', "Wire"), ('TEXTURED', "Restore"),
                         ('MODS_OFF', "Mods Off"), ('MODS_ON', "Mods On"), ('SUBD_LOW', "SubD 0")):
            grid.operator("heavy_finder.tame", text=label).action = a
        row = col.row(align=True)
        row.prop(sc, "heavy_finder_top", text="Top N")
        op = row.operator("heavy_finder.tame", text="Bounds")
        op.action, op.use_list = 'BOUNDS', True
        op = row.operator("heavy_finder.tame", text="Mods Off")
        op.action, op.use_list = 'MODS_OFF', True


_classes = (HF_Item, HF_OT_scan, HF_OT_tame, HF_OT_select, HF_UL_items, HF_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.heavy_finder_items = bpy.props.CollectionProperty(type=HF_Item)
    S.heavy_finder_index = bpy.props.IntProperty()
    S.heavy_finder_total = bpy.props.IntProperty()
    S.heavy_finder_top = bpy.props.IntProperty(default=5, min=1)


def unregister():
    S = bpy.types.Scene
    for p in ("heavy_finder_items", "heavy_finder_index", "heavy_finder_total", "heavy_finder_top"):
        delattr(S, p)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
