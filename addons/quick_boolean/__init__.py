"""Quick Boolean: select cutters, then the target (active), press Ctrl+Numpad -/+/* or slash.

Cutters become wire-displayed, render-hidden, parented to the target and stored in a "Cutters"
collection, so the boolean stays editable.
"""
import bpy

OPS = {'DIFFERENCE': "Difference", 'UNION': "Union", 'INTERSECT': "Intersect", 'SLICE': "Slice"}


def cutters_collection(scene):
    coll = bpy.data.collections.get("Cutters")
    if coll is None:
        coll = bpy.data.collections.new("Cutters")
        coll.hide_render = True
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def prep_cutter(cutter, target, scene):
    cutter.display_type = 'WIRE'
    cutter.hide_render = True
    cutter.visible_camera = cutter.visible_shadow = False
    if cutter.parent != target:
        mw = cutter.matrix_world.copy()
        cutter.parent = target
        cutter.matrix_world = mw
    coll = cutters_collection(scene)
    for c in list(cutter.users_collection):
        c.objects.unlink(cutter)
    coll.objects.link(cutter)


def add_boolean(target, cutter, operation, solver):
    md = target.modifiers.new(f"QB_{cutter.name}", 'BOOLEAN')
    md.operation = operation
    md.object = cutter
    md.solver = solver
    # Keep Bevel / Weighted Normal at the end of the stack so booleans get bevelled.
    tail = [m for m in target.modifiers if m.type in {'BEVEL', 'WEIGHTED_NORMAL', 'TRIANGULATE'}]
    for i, m in enumerate(tail):
        if target.modifiers.find(m.name) < target.modifiers.find(md.name):
            target.modifiers.move(target.modifiers.find(md.name), target.modifiers.find(m.name))
    return md


class QB_OT_boolean(bpy.types.Operator):
    bl_idname = "quick_boolean.apply_op"
    bl_label = "Quick Boolean"
    bl_description = "Cut the active object with the other selected objects (non-destructive)"
    bl_options = {'REGISTER', 'UNDO'}

    operation: bpy.props.EnumProperty(items=[(k, v, "") for k, v in OPS.items()])
    solver: bpy.props.EnumProperty(name="Solver", items=[('EXACT', "Exact", ""), ('FLOAT', "Fast", "")],
                                   default='EXACT')

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return context.mode == 'OBJECT' and ob and ob.type == 'MESH' and len(context.selected_objects) > 1

    def execute(self, context):
        target = context.active_object
        cutters = [o for o in context.selected_objects if o != target and o.type == 'MESH']
        if not cutters:
            self.report({'WARNING'}, "Select mesh cutters plus the target (active)")
            return {'CANCELLED'}
        if self.operation == 'SLICE':
            # Slice = keep target minus cutter, plus a new piece = target intersect cutter.
            piece = target.copy()
            piece.data = target.data.copy()
            piece.name = target.name + "_slice"
            for c in target.users_collection:
                c.objects.link(piece)
            for c in cutters:
                add_boolean(piece, c, 'INTERSECT', self.solver)
                add_boolean(target, c, 'DIFFERENCE', self.solver)
        else:
            for c in cutters:
                add_boolean(target, c, self.operation, self.solver)
        for c in cutters:
            prep_cutter(c, target, context.scene)
            c.select_set(False)
        target.select_set(True)
        context.view_layer.objects.active = target
        return {'FINISHED'}


def apply_booleans(target, delete_cutters=True):
    """Apply every boolean modifier on target; optionally delete cutters no longer used elsewhere."""
    cutters = set()
    with bpy.context.temp_override(object=target, active_object=target):
        for md in [m for m in target.modifiers if m.type == 'BOOLEAN']:
            if md.object:
                cutters.add(md.object)
            bpy.ops.object.modifier_apply(modifier=md.name)
    if delete_cutters:
        still_used = {m.object for o in bpy.data.objects for m in o.modifiers
                      if m.type == 'BOOLEAN' and m.object}
        for c in cutters - still_used:
            bpy.data.objects.remove(c)
    return len(cutters)


class QB_OT_apply(bpy.types.Operator):
    bl_idname = "quick_boolean.apply_all"
    bl_label = "Apply Booleans"
    bl_description = "Apply all boolean modifiers on selected objects and delete their unused cutters"
    bl_options = {'REGISTER', 'UNDO'}

    delete_cutters: bpy.props.BoolProperty(name="Delete Cutters", default=True)

    def execute(self, context):
        n = 0
        for ob in context.selected_objects:
            if ob.type == 'MESH' and ob.data.users == 1:
                n += apply_booleans(ob, self.delete_cutters)
        self.report({'INFO'}, f"Applied {n} booleans")
        return {'FINISHED'}


class QB_OT_toggle_cutters(bpy.types.Operator):
    bl_idname = "quick_boolean.toggle_cutters"
    bl_label = "Show/Hide Cutters"
    bl_description = "Toggle viewport visibility of the Cutters collection"

    def execute(self, context):
        lc = next((c for c in context.view_layer.layer_collection.children if c.name == "Cutters"), None)
        if lc:
            lc.hide_viewport = not lc.hide_viewport
        return {'FINISHED'}


class QB_MT_menu(bpy.types.Menu):
    bl_idname = "QB_MT_menu"
    bl_label = "Quick Boolean"

    def draw(self, context):
        lay = self.layout
        for k, v in OPS.items():
            lay.operator("quick_boolean.apply_op", text=v).operation = k
        lay.separator()
        lay.operator("quick_boolean.apply_all")
        lay.operator("quick_boolean.toggle_cutters")


def object_menu(self, context):
    self.layout.menu("QB_MT_menu", icon='MOD_BOOLEAN')


class QB_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        self.layout.label(text="Object Mode: Ctrl+Numpad Minus/Plus/Asterisk/Slash = Difference/Union/Intersect/Slice")
        self.layout.label(text="Change or disable in Keymap > Object Mode")


_classes = (QB_OT_boolean, QB_OT_apply, QB_OT_toggle_cutters, QB_MT_menu, QB_Prefs)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object.append(object_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        for key, op in (('NUMPAD_MINUS', 'DIFFERENCE'), ('NUMPAD_PLUS', 'UNION'),
                        ('NUMPAD_ASTERIX', 'INTERSECT'), ('NUMPAD_SLASH', 'SLICE')):
            kmi = km.keymap_items.new("quick_boolean.apply_op", key, 'PRESS', ctrl=True)
            kmi.properties.operation = op
            _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.VIEW3D_MT_object.remove(object_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
