"""Batch Material Edit: set/multiply/add Principled inputs and settings across materials."""
import bpy

FLOATS = ("Metallic", "Roughness", "IOR", "Alpha", "Specular IOR Level", "Transmission Weight",
          "Coat Weight", "Subsurface Weight", "Emission Strength")
COLORS = ("Base Color", "Emission Color")


def _key(name):
    return name.lower().replace(" ", "_")


def target_materials(context, scope):
    if scope == 'ALL':
        return [m for m in bpy.data.materials if not m.library and not m.is_grease_pencil]
    mats = []
    obs = context.selected_objects if scope == 'SELECTED' else [context.object]
    for ob in obs:
        for s in getattr(ob, "material_slots", ()):
            if s.material and not s.material.library and s.material not in mats:
                mats.append(s.material)
    return mats


def principled_nodes(mat):
    if not mat.node_tree:
        return []
    return [n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']


def apply_settings(mats, s):
    """Apply the enabled fields of settings `s` to materials. Returns number of inputs changed."""
    n = 0
    for mat in mats:
        for node in principled_nodes(mat):
            for name in FLOATS:
                k = _key(name)
                if not getattr(s, "use_" + k):
                    continue
                sock = node.inputs.get(name)
                if sock is None or sock.is_linked:
                    continue  # driven by a texture: leave it
                v = getattr(s, k)
                if s.float_mode == 'MULTIPLY':
                    v = sock.default_value * v
                elif s.float_mode == 'ADD':
                    v = sock.default_value + v
                sock.default_value = max(sock.bl_rna.properties["default_value"].hard_min,
                                         min(v, sock.bl_rna.properties["default_value"].hard_max))
                n += 1
            for name in COLORS:
                k = _key(name)
                sock = node.inputs.get(name)
                if getattr(s, "use_" + k) and sock is not None and not sock.is_linked:
                    sock.default_value = getattr(s, k)
                    n += 1
        if s.use_backface_culling:
            mat.use_backface_culling = s.backface_culling
        if s.use_render_method and hasattr(mat, "surface_render_method"):
            mat.surface_render_method = s.render_method
    return n


def sync_viewport_colors(mats):
    """Viewport Display color/metallic/roughness from the first Principled node (Solid mode looks right)."""
    n = 0
    for mat in mats:
        nodes = principled_nodes(mat)
        if not nodes:
            continue
        p = nodes[0]
        mat.diffuse_color = p.inputs["Base Color"].default_value
        mat.metallic = p.inputs["Metallic"].default_value
        mat.roughness = p.inputs["Roughness"].default_value
        n += 1
    return n


def _props():
    ann = {
        "scope": bpy.props.EnumProperty(name="Materials", items=[
            ('SELECTED', "Selected Objects", "All materials on selected objects"),
            ('ACTIVE', "Active Object", "Materials of the active object"),
            ('ALL', "Whole File", "Every local material")]),
        "float_mode": bpy.props.EnumProperty(name="Number Mode", items=[
            ('SET', "Set", ""), ('MULTIPLY', "Multiply", ""), ('ADD', "Add", "")]),
        "use_backface_culling": bpy.props.BoolProperty(name="Backface Culling"),
        "backface_culling": bpy.props.BoolProperty(name="On", default=True),
        "use_render_method": bpy.props.BoolProperty(name="Render Method"),
        "render_method": bpy.props.EnumProperty(name="Method", items=[
            ('DITHERED', "Dithered", ""), ('BLENDED', "Blended", "")]),
    }
    for name in FLOATS:
        k = _key(name)
        ann["use_" + k] = bpy.props.BoolProperty(name=name)
        ann[k] = bpy.props.FloatProperty(name=name, default=0.5 if name != "IOR" else 1.45)
    for name in COLORS:
        k = _key(name)
        ann["use_" + k] = bpy.props.BoolProperty(name=name)
        ann[k] = bpy.props.FloatVectorProperty(name=name, subtype='COLOR', size=4, min=0, max=1,
                                               default=(0.8, 0.8, 0.8, 1))
    return ann


class BM_Settings(bpy.types.PropertyGroup):
    __annotations__ = _props()


class BM_OT_apply(bpy.types.Operator):
    bl_idname = "batch_materials.apply"
    bl_label = "Apply to Materials"
    bl_description = "Write the ticked values into every target material (texture-linked inputs are skipped)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.batch_materials
        mats = target_materials(context, s.scope)
        n = apply_settings(mats, s)
        self.report({'INFO'}, f"{len(mats)} materials, {n} inputs changed")
        return {'FINISHED'}


class BM_OT_sync(bpy.types.Operator):
    bl_idname = "batch_materials.sync_viewport"
    bl_label = "Viewport Colors from Base Color"
    bl_description = "Copy base color/metallic/roughness into Viewport Display so Solid mode matches"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.batch_materials
        n = sync_viewport_colors(target_materials(context, s.scope))
        self.report({'INFO'}, f"Synced {n} materials")
        return {'FINISHED'}


class BM_PT_panel(bpy.types.Panel):
    bl_label = "Batch Material Edit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Material"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.batch_materials
        col = self.layout.column()
        col.prop(s, "scope")
        col.prop(s, "float_mode")
        for name in COLORS + FLOATS:
            k = _key(name)
            row = col.row(align=True)
            row.prop(s, "use_" + k, text="")
            sub = row.row(align=True)
            sub.active = getattr(s, "use_" + k)
            sub.prop(s, k)
        row = col.row(align=True)
        row.prop(s, "use_backface_culling", text="")
        row.prop(s, "backface_culling", text="Backface Culling")
        row = col.row(align=True)
        row.prop(s, "use_render_method", text="")
        row.prop(s, "render_method", text="")
        col.operator("batch_materials.apply", icon='CHECKMARK')
        col.operator("batch_materials.sync_viewport", icon='SHADING_SOLID')


_classes = (BM_Settings, BM_OT_apply, BM_OT_sync, BM_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.batch_materials = bpy.props.PointerProperty(type=BM_Settings)


def unregister():
    del bpy.types.Scene.batch_materials
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
