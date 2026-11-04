"""Group Under Empty (Ctrl+Alt+P group, Shift+Alt+P ungroup): transform groups like Maya's Ctrl+G."""
from mathutils import Vector

import bpy

GROUP_TAG = "is_group"


def _top_level(objects):
    """Selected objects whose parent is not also selected (so nested picks keep their hierarchy)."""
    s = set(objects)
    return [o for o in objects if not any(p in s for p in _parents(o))]


def _parents(o):
    p = o.parent
    while p:
        yield p
        p = p.parent


def _bounds(objects):
    pts = []
    for o in objects:
        bb = [o.matrix_world @ Vector(c) for c in o.bound_box]
        pts += bb if any(c.length for c in map(Vector, o.bound_box)) else [o.matrix_world.translation]
    mn = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
    mx = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
    return mn, mx


def group(context, objects, name="Group", pivot='BOTTOM'):
    tops = _top_level(objects)
    mn, mx = _bounds(tops)
    loc = {'CENTER': (mn + mx) / 2, 'BOTTOM': Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z)),
           'CURSOR': context.scene.cursor.location.copy(), 'ACTIVE': None}[pivot]
    if loc is None:
        act = context.active_object if context.active_object in objects else tops[0]
        loc = act.matrix_world.translation.copy()
    # Shared parent (if all tops had the same parent) stays above the new group.
    common = tops[0].parent if all(o.parent == tops[0].parent for o in tops) else None
    g = bpy.data.objects.new(name, None)
    g.empty_display_type = 'PLAIN_AXES'
    g.empty_display_size = max((mx - mn).length * 0.15, 0.1)
    g[GROUP_TAG] = True
    coll = tops[0].users_collection[0] if tops[0].users_collection else context.collection
    coll.objects.link(g)
    if common:
        g.parent = common
        context.view_layer.update()
    g.matrix_world.translation = loc
    context.view_layer.update()
    for o in tops:
        mw = o.matrix_world.copy()
        o.parent = g
        o.matrix_parent_inverse = g.matrix_world.inverted()
        o.matrix_world = mw
    return g


def ungroup(context, g):
    """Move children to the group's parent (keeping world transforms) and delete the group empty."""
    kids = list(g.children)
    new_parent = g.parent
    for o in kids:
        mw = o.matrix_world.copy()
        o.parent = new_parent
        o.matrix_parent_inverse = new_parent.matrix_world.inverted() if new_parent else o.matrix_parent_inverse.Identity(4)
        o.matrix_world = mw
    bpy.data.objects.remove(g)
    return kids


class GE_OT_group(bpy.types.Operator):
    bl_idname = "group_empty.group"
    bl_label = "Group Under Empty"
    bl_description = "Parent the selected objects under a new empty (keeps world transforms)"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="Name", default="Group")
    pivot: bpy.props.EnumProperty(name="Pivot", items=[
        ('BOTTOM', "Bottom Center", ""), ('CENTER', "Bounds Center", ""),
        ('CURSOR', "3D Cursor", ""), ('ACTIVE', "Active Object", "")])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        g = group(context, list(context.selected_objects), self.name, self.pivot)
        for o in context.selected_objects:
            o.select_set(False)
        g.select_set(True)
        context.view_layer.objects.active = g
        return {'FINISHED'}


class GE_OT_ungroup(bpy.types.Operator):
    bl_idname = "group_empty.ungroup"
    bl_label = "Ungroup"
    bl_description = "Remove the selected group empties, keeping their children where they are"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(o.type == 'EMPTY' for o in context.selected_objects)

    def execute(self, context):
        kids = []
        for g in [o for o in context.selected_objects if o.type == 'EMPTY' and o.children]:
            kids += ungroup(context, g)
        for o in kids:
            o.select_set(True)
        return {'FINISHED'}


class GE_OT_select_group(bpy.types.Operator):
    bl_idname = "group_empty.select_group"
    bl_label = "Select Whole Group"
    bl_description = "Select the top group of the active object and everything inside it"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        ob = context.active_object
        if not ob:
            return {'CANCELLED'}
        top = ob
        for p in _parents(ob):
            if p.get(GROUP_TAG):
                top = p
        top.select_set(True)
        for c in top.children_recursive:
            if c.visible_get():
                c.select_set(True)
        context.view_layer.objects.active = top
        return {'FINISHED'}


def menu(self, context):
    self.layout.separator()
    self.layout.operator("group_empty.group", icon='EMPTY_AXIS')
    self.layout.operator("group_empty.ungroup")
    self.layout.operator("group_empty.select_group")


_classes = (GE_OT_group, GE_OT_ungroup, GE_OT_select_group)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_object_parent.append(menu)
    bpy.types.OUTLINER_MT_object.append(menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name="Object Mode", space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new("group_empty.group", 'P', 'PRESS', ctrl=True, alt=True)))
        _keymaps.append((km, km.keymap_items.new("group_empty.ungroup", 'P', 'PRESS', shift=True, alt=True)))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.OUTLINER_MT_object.remove(menu)
    bpy.types.VIEW3D_MT_object_parent.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
