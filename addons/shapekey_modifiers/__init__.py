"""Apply modifiers to a mesh that has shape keys (Blender refuses to do this natively).

Each shape key is evaluated on its own (Shape Key Lock) through the chosen modifiers, the base mesh
gets the modifiers applied natively, then the shape keys are rebuilt from the evaluated coordinates.
"""
import array

import bpy

# Order matters: slider range before value, or value gets clamped.
KEY_PROPS = ("slider_max", "slider_min", "slider_max", "value", "vertex_group", "mute",
             "interpolation", "lock_shape")


def default_names(ob):
    return [m.name for m in ob.modifiers if m.show_viewport and m.type != 'ARMATURE']


def _eval_shape(context, ob, index):
    """Vertex coords + edge topology of the evaluated mesh showing only shape key `index`."""
    ob.active_shape_key_index = index
    context.view_layer.update()
    ob_eval = ob.evaluated_get(context.evaluated_depsgraph_get())
    me = ob_eval.to_mesh()
    co = array.array('f', [0.0]) * (len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    edges = array.array('i', [0]) * (len(me.edges) * 2)
    me.edges.foreach_get("vertices", edges)
    ob_eval.to_mesh_clear()
    return co, edges


def apply_keep_shapes(context, ob, names):
    """Apply modifiers `names` to `ob`, keeping its shape keys. Returns an error string or None."""
    me = ob.data
    key = me.shape_keys
    if key is None:
        return "Object has no shape keys, use the regular Apply"
    if me.users > 1:
        return "Mesh is shared by several objects, make it single user first"
    names = [m.name for m in ob.modifiers if m.name in set(names)]
    if not names:
        return "No modifiers selected"

    kbs = key.key_blocks
    vis = {m.name: m.show_viewport for m in ob.modifiers}
    obj_state = ob.show_only_shape_key, ob.active_shape_key_index
    kb_state = [(kb.mute, kb.vertex_group) for kb in kbs]
    try:
        # Raw key coords: no mute, no vertex group weighting, only chosen modifiers.
        for m in ob.modifiers:
            m.show_viewport = m.name in names
        ob.show_only_shape_key = True
        for kb in kbs:
            kb.mute, kb.vertex_group = False, ""
        shapes = [_eval_shape(context, ob, i) for i in range(len(kbs))]
    finally:
        for kb, (mute, vg) in zip(kbs, kb_state):
            kb.mute, kb.vertex_group = mute, vg
        for m in ob.modifiers:
            m.show_viewport = vis[m.name]
        ob.show_only_shape_key, ob.active_shape_key_index = obj_state
        context.view_layer.update()

    base_co, base_edges = shapes[0]
    for kb, (co, edges) in zip(kbs[1:], shapes[1:]):
        if len(co) != len(base_co) or edges != base_edges:
            return (f"Shape key '{kb.name}' changes the topology ({len(co) // 3} vs {len(base_co) // 3} "
                    "vertices). Deselect topology-changing modifiers (Decimate, Weld, merge by distance...)")

    backup = me.copy()  # keeps the old Key (settings, drivers, action) alive while rebuilding
    old = backup.shape_keys
    try:
        ref = array.array('f', [0.0]) * (len(me.vertices) * 3)
        kbs[0].data.foreach_get("co", ref)
        me.vertices.foreach_set("co", ref)
        ob.shape_key_clear()
        for name in names:
            ob.modifiers[name].show_viewport = True  # modifier_apply skips disabled ones
        with context.temp_override(object=ob, active_object=ob):
            for name in names:
                bpy.ops.object.modifier_apply(modifier=name)
        if len(me.vertices) * 3 != len(base_co):
            raise RuntimeError("applied mesh does not match the evaluated basis")

        for okb, (co, _) in zip(old.key_blocks, shapes):
            kb = ob.shape_key_add(name=okb.name, from_mix=False)
            kb.data.foreach_set("co", co)
            for p in KEY_PROPS:
                setattr(kb, p, getattr(okb, p))
        new = me.shape_keys
        new.use_relative, new.eval_time = old.use_relative, old.eval_time
        for okb in old.key_blocks:
            new.key_blocks[okb.name].relative_key = new.key_blocks[okb.relative_key.name]

        if old.animation_data:
            ad = new.animation_data_create()
            if old.animation_data.action:
                ad.action = old.animation_data.action
                if hasattr(ad, "action_slot"):
                    ad.action_slot = old.animation_data.action_slot
            for fc in old.animation_data.drivers:
                for var in ad.drivers.from_existing(src_driver=fc).driver.variables:
                    for t in var.targets:
                        if t.id == old:  # corrective drivers reading other shape keys
                            t.id = new
        ob.show_only_shape_key, ob.active_shape_key_index = obj_state
    except Exception as e:
        backup.name = me.name + "_backup"
        backup.use_fake_user = True
        return f"Failed ({e}). Original mesh kept as '{backup.name}'"
    bpy.data.meshes.remove(backup)
    return None


class SKM_ModItem(bpy.types.PropertyGroup):
    apply: bpy.props.BoolProperty(name="Apply")


class SKM_OT_apply(bpy.types.Operator):
    bl_idname = "shapekey_modifiers.apply"
    bl_label = "Apply Modifiers (Keep Shape Keys)"
    bl_description = "Apply the chosen modifiers to the active mesh while keeping its shape keys and their drivers"
    bl_options = {'REGISTER', 'UNDO'}

    items: bpy.props.CollectionProperty(type=SKM_ModItem, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return bool(ob and ob.type == 'MESH' and ob.mode == 'OBJECT' and ob.modifiers and ob.data.shape_keys)

    def invoke(self, context, event):
        ob = context.active_object
        default = set(default_names(ob))
        self.items.clear()
        for m in ob.modifiers:
            it = self.items.add()
            it.name, it.apply = m.name, m.name in default
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column()
        col.label(text="Modifiers to apply:")
        mods = context.active_object.modifiers
        for it in self.items:
            m = mods.get(it.name)
            col.prop(it, "apply", text=it.name, icon=m.bl_rna.properties["type"].enum_items[m.type].icon if m else 'NONE')

    def execute(self, context):
        ob = context.active_object
        names = [it.name for it in self.items if it.apply] if self.items else default_names(ob)
        err = apply_keep_shapes(context, ob, names)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied {len(names)} modifier(s), kept {len(ob.data.shape_keys.key_blocks)} shape keys")
        return {'FINISHED'}


def menu_item(self, context):
    self.layout.operator(SKM_OT_apply.bl_idname, icon='SHAPEKEY_DATA')


def panel_item(self, context):
    ob = context.object
    if ob and ob.type == 'MESH' and ob.data.shape_keys and ob.modifiers:
        menu_item(self, context)


_menus = (
    (bpy.types.VIEW3D_MT_object_apply, menu_item),
    (bpy.types.VIEW3D_MT_object_modifiers, menu_item),
    (bpy.types.MESH_MT_shape_key_context_menu, menu_item),
)


def register():
    bpy.utils.register_class(SKM_ModItem)
    bpy.utils.register_class(SKM_OT_apply)
    for menu, fn in _menus:
        menu.append(fn)
    bpy.types.DATA_PT_modifiers.prepend(panel_item)


def unregister():
    bpy.types.DATA_PT_modifiers.remove(panel_item)
    for menu, fn in _menus:
        menu.remove(fn)
    bpy.utils.unregister_class(SKM_OT_apply)
    bpy.utils.unregister_class(SKM_ModItem)
