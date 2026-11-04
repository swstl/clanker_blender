"""Collection Tools: the outliner chores Blender makes you do by hand."""
import bpy


def walk_layer(lc):
    yield lc
    for c in lc.children:
        yield from walk_layer(c)


def _active_lc(context):
    return context.view_layer.active_layer_collection


class CT_OT_move_to_new(bpy.types.Operator):
    bl_idname = "collection_tools.move_to_new"
    bl_label = "Move to New Collection (Named After Active)"
    bl_description = "Move selected objects into a new collection named after the active object"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.selected_objects

    def execute(self, context):
        act = context.active_object or context.selected_objects[0]
        parent = _active_lc(context).collection
        new = bpy.data.collections.new(act.name)
        parent.children.link(new)
        for ob in context.selected_objects:
            for c in list(ob.users_collection):
                c.objects.unlink(ob)
            new.objects.link(ob)
        return {'FINISHED'}


class CT_OT_isolate(bpy.types.Operator):
    bl_idname = "collection_tools.isolate"
    bl_label = "Isolate Active Collection"
    bl_description = "Hide every collection except the active one and its parents/children (run again to show all)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        root = context.view_layer.layer_collection
        act = _active_lc(context)
        keep = set()

        def mark(lc, path):
            if lc == act:
                keep.update(path + [lc])
                keep.update(walk_layer(lc))
                return
            for c in lc.children:
                mark(c, path + [lc])

        mark(root, [])
        others = [lc for lc in walk_layer(root) if lc not in keep and lc != root]
        if others and all(lc.hide_viewport for lc in others):
            for lc in others:
                lc.hide_viewport = False  # toggle back
        else:
            for lc in walk_layer(root):
                if lc != root:
                    lc.hide_viewport = lc not in keep
        return {'FINISHED'}


class CT_OT_show_all(bpy.types.Operator):
    bl_idname = "collection_tools.show_all"
    bl_label = "Show / Include All Collections"
    bl_description = "Un-hide and re-include every collection in the view layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for lc in walk_layer(context.view_layer.layer_collection):
            lc.exclude = False
            lc.hide_viewport = False
            lc.collection.hide_viewport = False
        return {'FINISHED'}


def sort_children(coll, recursive=True):
    kids = sorted(coll.children, key=lambda c: c.name.lower())
    for c in kids:
        coll.children.unlink(c)
    for c in kids:
        coll.children.link(c)
        if recursive:
            sort_children(c, True)


class CT_OT_sort(bpy.types.Operator):
    bl_idname = "collection_tools.sort"
    bl_label = "Sort Collections A-Z"
    bl_description = "Sort child collections alphabetically (recursively) under the active collection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Relinking resets per-view-layer exclude/hide flags: remember and restore them.
        state = {lc.collection.name: (lc.exclude, lc.hide_viewport)
                 for lc in walk_layer(context.view_layer.layer_collection)}
        sort_children(_active_lc(context).collection)
        for lc in walk_layer(context.view_layer.layer_collection):
            if lc.collection.name in state:
                lc.exclude, lc.hide_viewport = state[lc.collection.name]
        return {'FINISHED'}


def remove_empty(scene):
    removed = 0
    changed = True
    while changed:  # nested empties become empty after their children go
        changed = False
        for c in list(bpy.data.collections):
            if not c.objects and not c.children and c.users and c.name in _scene_colls(scene):
                bpy.data.collections.remove(c)
                removed += 1
                changed = True
    return removed


def _scene_colls(scene):
    return {c.name for c in scene.collection.children_recursive}


class CT_OT_remove_empty(bpy.types.Operator):
    bl_idname = "collection_tools.remove_empty"
    bl_label = "Remove Empty Collections"
    bl_description = "Delete collections in this scene with no objects and no child collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"Removed {remove_empty(context.scene)} empty collections")
        return {'FINISHED'}


class CT_OT_select_objects(bpy.types.Operator):
    bl_idname = "collection_tools.select_objects"
    bl_label = "Select Objects in Active Collection"
    bl_description = "Select every object in the active collection (including nested collections)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = _active_lc(context).collection
        n = 0
        for ob in coll.all_objects:
            if ob.visible_get():
                ob.select_set(True)
                n += 1
        self.report({'INFO'}, f"Selected {n}")
        return {'FINISHED'}


class CT_OT_instance_at_cursor(bpy.types.Operator):
    bl_idname = "collection_tools.instance_at_cursor"
    bl_label = "Instance Active Collection at Cursor"
    bl_description = "Add an instance of the active collection at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        coll = _active_lc(context).collection
        if coll == context.scene.collection:
            self.report({'WARNING'}, "Pick a collection first")
            return {'CANCELLED'}
        ob = bpy.data.objects.new(coll.name + "_inst", None)
        ob.instance_type = 'COLLECTION'
        ob.instance_collection = coll
        ob.location = context.scene.cursor.location
        context.scene.collection.objects.link(ob)
        return {'FINISHED'}


class CT_OT_set_offset(bpy.types.Operator):
    bl_idname = "collection_tools.offset_from_cursor"
    bl_label = "Set Instance Offset from Cursor"
    bl_description = "Set the active collection's instance offset to the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _active_lc(context).collection.instance_offset = context.scene.cursor.location
        return {'FINISHED'}


_ops = (CT_OT_move_to_new, CT_OT_isolate, CT_OT_show_all, CT_OT_sort, CT_OT_remove_empty,
        CT_OT_select_objects, CT_OT_instance_at_cursor, CT_OT_set_offset)


class CT_MT_menu(bpy.types.Menu):
    bl_idname = "CT_MT_menu"
    bl_label = "Collection Tools"

    def draw(self, context):
        for op in _ops:
            self.layout.operator(op.bl_idname)


def outliner_menu(self, context):
    self.layout.separator()
    self.layout.menu("CT_MT_menu")


def object_menu(self, context):
    self.layout.operator(CT_OT_move_to_new.bl_idname)


_classes = _ops + (CT_MT_menu,)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.OUTLINER_MT_collection.append(outliner_menu)
    bpy.types.OUTLINER_MT_context_menu.append(outliner_menu)
    bpy.types.VIEW3D_MT_object_collection.append(object_menu)


def unregister():
    bpy.types.VIEW3D_MT_object_collection.remove(object_menu)
    bpy.types.OUTLINER_MT_context_menu.remove(outliner_menu)
    bpy.types.OUTLINER_MT_collection.remove(outliner_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
