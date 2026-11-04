"""Quick Lattice: fit a lattice around selection, hook it up, edit it, apply and clean up."""
import bmesh
import bpy
from mathutils import Matrix, Vector

VG_NAME = "QuickLattice"


def _bbox(points, space=Matrix()):
    """Center and size of points (world) measured in `space` (a rotation-only world matrix)."""
    inv = space.inverted()
    loc = [inv @ p for p in points]
    mn = Vector(tuple(min(p[i] for p in loc) for i in range(3)))
    mx = Vector(tuple(max(p[i] for p in loc) for i in range(3)))
    size = mx - mn
    floor = max(max(size) * 0.1, 1e-3)  # flat selections still get a usable box
    size = Vector(tuple(max(s, floor) for s in size))
    return space @ ((mn + mx) / 2), size


def make_lattice(context, points, rot, resolution, name="Lattice"):
    center, size = _bbox(points, rot)
    lt = bpy.data.lattices.new(name)
    lt.points_u, lt.points_v, lt.points_w = resolution
    lt.interpolation_type_u = lt.interpolation_type_v = lt.interpolation_type_w = 'KEY_BSPLINE'
    ob = bpy.data.objects.new(name, lt)
    context.collection.objects.link(ob)
    # Lattice points span -0.5..0.5, so scale = bbox size (slightly padded).
    ob.matrix_world = Matrix.Translation(center) @ rot @ Matrix.Diagonal(size * 1.02).to_4x4()
    return ob


class QL_OT_add(bpy.types.Operator):
    bl_idname = "quick_lattice.add"
    bl_label = "Quick Lattice"
    bl_description = ("Add a lattice fitted to the selected objects (Object Mode) or the selected vertices "
                      "(Edit Mode) and enter its edit mode")
    bl_options = {'REGISTER', 'UNDO'}

    resolution: bpy.props.IntVectorProperty(name="Resolution", size=3, default=(3, 3, 3), min=2, max=64)
    align: bpy.props.EnumProperty(name="Orientation", items=[
        ('WORLD', "World", "Axis-aligned box"),
        ('ACTIVE', "Active Object", "Rotate the lattice with the active object")], default='ACTIVE')
    edit: bpy.props.BoolProperty(name="Enter Edit Mode", default=True)

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'} and context.selected_objects

    def execute(self, context):
        act = context.active_object
        rot = act.matrix_world.to_quaternion().to_matrix().to_4x4() if (act and self.align == 'ACTIVE') else Matrix()
        edit_mode = context.mode == 'EDIT_MESH'
        targets, points = [], []
        if edit_mode:
            for ob in context.objects_in_mode_unique_data:
                bm = bmesh.from_edit_mesh(ob.data)
                sel = [v for v in bm.verts if v.select]
                if not sel:
                    continue
                points += [ob.matrix_world @ v.co for v in sel]
                targets.append((ob, [v.index for v in sel]))
            if not points:
                self.report({'WARNING'}, "Select some vertices")
                return {'CANCELLED'}
            bpy.ops.object.mode_set(mode='OBJECT')
        else:
            for ob in context.selected_objects:
                if ob.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'LATTICE'}:
                    points += [ob.matrix_world @ Vector(c) for c in ob.bound_box]
                    targets.append((ob, None))
            if not points:
                self.report({'WARNING'}, "Select mesh/curve objects")
                return {'CANCELLED'}
        lat = make_lattice(context, points, rot, tuple(self.resolution))
        for ob, idx in targets:
            md = ob.modifiers.new("QuickLattice", 'LATTICE')
            md.object = lat
            if idx is not None:
                vg = ob.vertex_groups.get(VG_NAME) or ob.vertex_groups.new(name=VG_NAME)
                vg.add(idx, 1.0, 'REPLACE')
                md.vertex_group = vg.name
        for o in context.selected_objects:
            o.select_set(False)
        lat.select_set(True)
        context.view_layer.objects.active = lat
        lat["quick_lattice_targets"] = [ob.name for ob, _ in targets]
        if self.edit:
            bpy.ops.object.mode_set(mode='EDIT')
        return {'FINISHED'}


def apply_lattice(lat, delete=True):
    """Apply the lattice modifiers that use `lat` on all objects, remove helper groups, delete lattice."""
    n = 0
    for ob in list(bpy.data.objects):
        for md in [m for m in ob.modifiers if m.type == 'LATTICE' and m.object == lat]:
            if ob.type == 'MESH' and ob.data.shape_keys:
                continue  # can't apply on shape keys; leave it live
            with bpy.context.temp_override(object=ob, active_object=ob):
                bpy.ops.object.modifier_apply(modifier=md.name)
            n += 1
            vg = ob.vertex_groups.get(VG_NAME)
            if vg and not any(m.type == 'LATTICE' and m.vertex_group == VG_NAME for m in ob.modifiers):
                ob.vertex_groups.remove(vg)
    if delete:
        data = lat.data
        bpy.data.objects.remove(lat)
        if data.users == 0:
            bpy.data.lattices.remove(data)
    return n


class QL_OT_apply(bpy.types.Operator):
    bl_idname = "quick_lattice.apply"
    bl_label = "Apply Lattice"
    bl_description = "Apply the active lattice to every object it deforms and delete it"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'LATTICE'

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        n = apply_lattice(context.active_object)
        self.report({'INFO'}, f"Applied to {n} objects")
        return {'FINISHED'}


class QL_OT_remove(bpy.types.Operator):
    bl_idname = "quick_lattice.remove"
    bl_label = "Remove Lattice"
    bl_description = "Delete the active lattice and its modifiers without applying"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'LATTICE'

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        lat = context.active_object
        for ob in bpy.data.objects:
            for md in [m for m in ob.modifiers if m.type == 'LATTICE' and m.object == lat]:
                ob.modifiers.remove(md)
            vg = ob.vertex_groups.get(VG_NAME) if hasattr(ob, "vertex_groups") else None
            if vg:
                ob.vertex_groups.remove(vg)
        bpy.data.objects.remove(lat)
        return {'FINISHED'}


def menu(self, context):
    self.layout.operator("quick_lattice.add", icon='MOD_LATTICE')


def lattice_menu(self, context):
    self.layout.separator()
    self.layout.operator("quick_lattice.apply")
    self.layout.operator("quick_lattice.remove")


_classes = (QL_OT_add, QL_OT_apply, QL_OT_remove)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(menu)
    bpy.types.VIEW3D_MT_edit_lattice.append(lattice_menu)
    bpy.types.VIEW3D_MT_object.append(lattice_menu)


def unregister():
    bpy.types.VIEW3D_MT_object.remove(lattice_menu)
    bpy.types.VIEW3D_MT_edit_lattice.remove(lattice_menu)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(menu)
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
