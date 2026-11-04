"""View Layer Matrix: edit collection visibility for every view layer at once (no layer switching)."""
import bpy

FLAGS = [('exclude', "Exclude", "CHECKBOX_HLT"), ('holdout', "Holdout", "HOLDOUT_ON"),
         ('indirect_only', "Indirect Only", "INDIRECT_ONLY_ON"), ('hide_viewport', "Hide", "HIDE_ON")]


def walk(lc, depth=0):
    for c in lc.children:
        yield c, depth
        yield from walk(c, depth + 1)


def find_lc(view_layer, coll_name):
    for lc, _ in walk(view_layer.layer_collection):
        if lc.collection.name == coll_name:
            return lc
    return None


def copy_state(src, dst):
    """Copy every collection flag from view layer src to dst. Returns number of collections updated."""
    n = 0
    for lc, _ in walk(src.layer_collection):
        other = find_lc(dst, lc.collection.name)
        if other:
            for f, _, _ in FLAGS:
                setattr(other, f, getattr(lc, f))
            n += 1
    return n


def layers_from_collections(scene):
    """One view layer per top-level collection, with only that collection included."""
    made = []
    tops = [c.name for c in scene.collection.children]
    for name in tops:
        vl = scene.view_layers.get(name) or scene.view_layers.new(name)
        for lc in vl.layer_collection.children:
            lc.exclude = lc.collection.name != name
        made.append(vl)
    return made


class VLM_OT_copy(bpy.types.Operator):
    bl_idname = "view_layer_matrix.copy"
    bl_label = "Copy Layer State"
    bl_description = "Copy all collection flags from one view layer to another"
    bl_options = {'REGISTER', 'UNDO'}

    src: bpy.props.StringProperty(name="From", options={'SKIP_SAVE'})
    dst: bpy.props.StringProperty(name="To", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.src = self.src or context.view_layer.name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop_search(self, "src", context.scene, "view_layers")
        self.layout.prop_search(self, "dst", context.scene, "view_layers")

    def execute(self, context):
        vls = context.scene.view_layers
        if self.src not in vls or self.dst not in vls or self.src == self.dst:
            self.report({'WARNING'}, "Pick two different view layers")
            return {'CANCELLED'}
        n = copy_state(vls[self.src], vls[self.dst])
        self.report({'INFO'}, f"Copied {n} collections")
        return {'FINISHED'}


class VLM_OT_from_collections(bpy.types.Operator):
    bl_idname = "view_layer_matrix.from_collections"
    bl_label = "View Layer per Collection"
    bl_description = "Create (or update) one view layer per top-level collection, including only that collection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = len(layers_from_collections(context.scene))
        self.report({'INFO'}, f"{n} view layers")
        return {'FINISHED'}


class VLM_PT_panel(bpy.types.Panel):
    bl_label = "View Layer Matrix"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "view_layer"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        sc = context.scene
        lay = self.layout
        lay.prop(sc, "view_layer_matrix_flag", expand=True)
        flag = sc.view_layer_matrix_flag
        icon = next(i for f, _, i in FLAGS if f == flag)
        vls = list(sc.view_layers)
        grid = lay.grid_flow(row_major=True, columns=len(vls) + 1, even_columns=False, align=True)
        grid.label(text="")
        for vl in vls:
            grid.label(text=vl.name[:12])
        for lc, depth in walk(vls[0].layer_collection):
            grid.label(text="  " * depth + lc.collection.name, icon='OUTLINER_COLLECTION')
            for vl in vls:
                other = find_lc(vl, lc.collection.name)
                if other:
                    grid.prop(other, flag, text="", icon=icon, emboss=True)
                else:
                    grid.label(text="-")
        row = lay.row(align=True)
        row.operator("view_layer_matrix.copy", icon='COPYDOWN')
        row.operator("view_layer_matrix.from_collections", icon='RENDERLAYERS')


_classes = (VLM_OT_copy, VLM_OT_from_collections, VLM_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.view_layer_matrix_flag = bpy.props.EnumProperty(
        name="Flag", items=[(f, n, "") for f, n, _ in FLAGS])


def unregister():
    del bpy.types.Scene.view_layer_matrix_flag
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
