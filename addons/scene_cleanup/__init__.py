"""Scene Cleanup: merge .001 duplicates, tidy material slots, rename data, find problem objects."""
import os
import re

import bpy
import numpy as np

DUP_RE = re.compile(r"^(.+)\.\d{3,}$")
KINDS = ("images", "node_groups", "materials")  # merge order: dependencies first
_NODE_BASE = set(bpy.types.Node.bl_rna.properties.keys())


# ---------------------------------------------------------------- signatures

def _val(v, name, depth=0):
    """Hashable, comparable snapshot of an RNA value. ID pointers become (remapped) names."""
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, bpy.types.ID):
        return name(v)
    if isinstance(v, set):
        return tuple(sorted(v))
    if isinstance(v, bpy.types.bpy_struct):  # color ramps, curve mappings, image users
        if depth > 4:
            return None
        out = []
        for p in v.bl_rna.properties:
            if p.identifier != "rna_type":
                try:
                    out.append((p.identifier, _val(getattr(v, p.identifier), name, depth + 1)))
                except Exception:
                    pass
        return tuple(out)
    try:
        return tuple(_val(x, name, depth + 1) for x in v)
    except TypeError:
        return repr(v)


def tree_sig(tree, name):
    if tree is None:
        return None
    nodes = tuple(sorted(
        (n.name, n.bl_idname, n.mute,
         tuple((k, _val(getattr(n, k), name)) for k in n.bl_rna.properties.keys() if k not in _NODE_BASE),
         tuple((s.identifier, _val(s.default_value, name))
               for s in n.inputs if not s.is_linked and hasattr(s, "default_value")))
        for n in tree.nodes))
    links = tuple(sorted((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier)
                         for l in tree.links))
    iface = tuple((i.item_type, getattr(i, "in_out", None), getattr(i, "socket_type", None), i.name)
                  for i in tree.interface.items_tree) if hasattr(tree, "interface") else ()
    return tree.bl_idname, nodes, links, iface


def _image_sig(img, name):
    if not img.filepath:  # generated / unsaved: never "identical"
        return ("id", img.as_pointer())
    return os.path.normpath(bpy.path.abspath(img.filepath, library=img.library)), img.source


def _material_sig(mat, name):
    return (tree_sig(mat.node_tree, name), _val(mat.diffuse_color, name),
            round(mat.metallic, 4), round(mat.roughness, 4))


SIG = {"images": _image_sig, "node_groups": tree_sig, "materials": _material_sig}


def find_merges(identical=True, kinds=KINDS):
    """List of (kind, dupe, base) in apply order. 'Name.001' merges into 'Name' if it exists."""
    remap = {}  # dupe pointer -> base, so later signatures see earlier merges

    def name(idb):
        return remap.get(idb.as_pointer(), idb).name

    plan = []
    for kind in KINDS:
        if kind not in kinds:
            continue
        coll = getattr(bpy.data, kind)
        changed = True
        while changed:  # repeat: nested node groups only match after their children merged
            changed = False
            for idb in coll:
                m = DUP_RE.match(idb.name)
                if not m or idb.library or idb.as_pointer() in remap:
                    continue
                base = coll.get(m.group(1))
                if base is None or base.library:
                    continue
                base = remap.get(base.as_pointer(), base)
                if identical and SIG[kind](idb, name) != SIG[kind](base, name):
                    continue
                remap[idb.as_pointer()] = base
                plan.append((kind, idb, base))
                changed = True
    return plan


def apply_merges(plan):
    for kind, dupe, base in plan:
        dupe.user_remap(base)
        getattr(bpy.data, kind).remove(dupe)
    return len(plan)


# ---------------------------------------------------------------- object queries

def _scale_bad(o):
    return any(s <= 1e-6 for s in o.scale)


CATEGORIES = {
    "NO_MATERIAL": ("No Material", lambda o, vl: hasattr(o.data, "materials")
                    and not any(s.material for s in o.material_slots)),
    "BAD_SCALE": ("Zero / Negative Scale", lambda o, vl: _scale_bad(o)),
    "UNAPPLIED_SCALE": ("Unapplied Scale", lambda o, vl: any(abs(s - 1) > 1e-6 for s in o.scale)),
    "EMPTY_MESH": ("Empty Mesh", lambda o, vl: o.type == 'MESH' and not o.data.vertices),
    "OUTSIDE_LAYER": ("Not in View Layer", lambda o, vl: o.name not in vl.objects),
}


def find_objects(category, view_layer):
    fn = CATEGORIES[category][1]
    pool = bpy.data.objects if category == "OUTSIDE_LAYER" else view_layer.objects
    return [o for o in pool if o and fn(o, view_layer)]  # vl.objects can hold None until resynced


def _targets(context, all_objects):
    return list(context.view_layer.objects if all_objects else context.selected_objects)


def rename_data(objects):
    n = 0
    for o in objects:
        d = o.data
        if d is None or d.library or d.users > 1 or d.name == o.name:
            continue  # shared data: whose name would win?
        d.name = o.name
        n += 1
    return n


def _used_indices(o):
    """Material indices actually used by faces/splines; None for types we can't inspect."""
    if o.type == 'MESH':
        idx = np.zeros(len(o.data.polygons), dtype=np.int32)
        o.data.polygons.foreach_get("material_index", idx)
        return set(np.unique(idx).tolist())
    if o.type in {'CURVE', 'SURFACE'}:
        return {s.material_index for s in o.data.splines}
    return None  # text, metaballs, ...: leave alone


def _remove_slot(o, i):
    # Native op shifts face indices and handles object- vs data-linked slots.
    # (material_slot_remove_unused ignores context overrides and uses the real selection.)
    o.active_material_index = i
    with bpy.context.temp_override(object=o, active_object=o):
        bpy.ops.object.material_slot_remove()


def remove_unused_slots(objects):
    n = 0
    for o in objects:
        used = _used_indices(o) if o.material_slots and not o.data.library else None
        if used is None:
            continue
        for i in reversed(range(len(o.material_slots))):
            if i not in used:
                _remove_slot(o, i)
                n += 1
    return n


def remove_empty_slots(objects):
    n = 0
    for o in objects:
        for i in reversed(range(len(o.material_slots))):
            if o.material_slots[i].material is None:
                _remove_slot(o, i)
                n += 1
    return n


# ---------------------------------------------------------------- operators

class SC_OT_merge_duplicates(bpy.types.Operator):
    bl_idname = "scene_cleanup.merge_duplicates"
    bl_label = "Merge Duplicate Data"
    bl_description = "Merge 'Name.001' style duplicate materials, node groups and images into the original"
    bl_options = {'REGISTER', 'UNDO'}

    identical: bpy.props.BoolProperty(
        name="Only if Identical", default=True,
        description="Images: same file. Materials/node groups: same nodes, values and links. "
                    "Off: merge by name only")
    materials: bpy.props.BoolProperty(name="Materials", default=True)
    node_groups: bpy.props.BoolProperty(name="Node Groups", default=True)
    images: bpy.props.BoolProperty(name="Images", default=True)

    def _plan(self):
        return find_merges(self.identical, [k for k in KINDS if getattr(self, k)])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420, confirm_text="Merge")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "identical")
        row = col.row()
        for k in ("materials", "node_groups", "images"):
            row.prop(self, k, toggle=True)
        plan = self._plan()
        box = col.box()
        if not plan:
            box.label(text="Nothing to merge", icon='CHECKMARK')
        for kind, dupe, base in plan[:25]:
            box.label(text=f"{dupe.name}  →  {base.name}",
                      icon={"materials": 'MATERIAL', "images": 'IMAGE_DATA'}.get(kind, 'NODETREE'))
        if len(plan) > 25:
            box.label(text=f"... and {len(plan) - 25} more")

    def execute(self, context):
        n = apply_merges(self._plan())
        self.report({'INFO'}, f"Merged {n} duplicate data-block(s)")
        return {'FINISHED'}


class SC_OT_purge(bpy.types.Operator):
    bl_idname = "scene_cleanup.purge"
    bl_label = "Purge Orphans (Recursive)"
    bl_description = "Delete all unused data-blocks, including ones only used by other unused data"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        before = len(bpy.data.user_map())
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
        self.report({'INFO'}, f"Purged {before - len(bpy.data.user_map())} data-block(s)")
        return {'FINISHED'}


class _ObjectsOp:
    bl_options = {'REGISTER', 'UNDO'}
    all_objects: bpy.props.BoolProperty(name="All Objects", default=False,
                                        description="Act on every object in the view layer, not just the selection")

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        n = self.run(_targets(context, self.all_objects))
        self.report({'INFO'}, self.msg.format(n))
        return {'FINISHED'}


class SC_OT_remove_unused_slots(_ObjectsOp, bpy.types.Operator):
    bl_idname = "scene_cleanup.remove_unused_slots"
    bl_label = "Remove Unused Slots"
    bl_description = "Remove material slots not assigned to any face"
    msg = "Removed {} unused material slot(s)"
    run = staticmethod(remove_unused_slots)


class SC_OT_remove_empty_slots(_ObjectsOp, bpy.types.Operator):
    bl_idname = "scene_cleanup.remove_empty_slots"
    bl_label = "Remove Empty Slots"
    bl_description = "Remove material slots that have no material"
    msg = "Removed {} empty material slot(s)"
    run = staticmethod(remove_empty_slots)


class SC_OT_rename_data(_ObjectsOp, bpy.types.Operator):
    bl_idname = "scene_cleanup.rename_data"
    bl_label = "Rename Data to Object"
    bl_description = "Name mesh/curve/armature/... data after its object (shared data is skipped)"
    msg = "Renamed {} data-block(s)"
    run = staticmethod(rename_data)


class SC_OT_select(bpy.types.Operator):
    bl_idname = "scene_cleanup.select"
    bl_label = "Select Problem Objects"
    bl_description = "Select objects matching the chosen problem"
    bl_options = {'REGISTER', 'UNDO'}

    category: bpy.props.EnumProperty(items=[(k, v[0], "") for k, v in CATEGORIES.items()])

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        vl = context.view_layer
        found = find_objects(self.category, vl)
        if self.category == "OUTSIDE_LAYER":  # can't select what the view layer doesn't hold
            names = ", ".join(o.name for o in found[:10]) + (" ..." if len(found) > 10 else "")
            self.report({'INFO'}, f"{len(found)} object(s) not in view layer: {names}")
            return {'FINISHED'}
        for o in vl.objects:
            o.select_set(o in found)
        if found:
            vl.objects.active = found[0]
        self.report({'INFO'}, f"Selected {len(found)} object(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class SC_PT_panel(bpy.types.Panel):
    bl_label = "Scene Cleanup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Cleanup"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator(SC_OT_merge_duplicates.bl_idname, icon='AUTOMERGE_ON', text="Merge Duplicates...")
        col.operator(SC_OT_purge.bl_idname, icon='ORPHAN_DATA')

        for op, label in ((SC_OT_remove_unused_slots, "Unused Slots"),
                          (SC_OT_remove_empty_slots, "Empty Slots"),
                          (SC_OT_rename_data, "Data Names")):
            self.layout.label(text=label + ":")
            row = self.layout.row(align=True)
            row.operator(op.bl_idname, text="Selected").all_objects = False
            row.operator(op.bl_idname, text="All").all_objects = True

        box = self.layout.box()
        box.label(text="Find & Select", icon='VIEWZOOM')
        col = box.column(align=True)
        # ponytail: counts rescan all objects per redraw; cache on depsgraph update if scenes get huge
        for key, (label, _) in CATEGORIES.items():
            col.operator(SC_OT_select.bl_idname,
                         text=f"{label} ({len(find_objects(key, context.view_layer))})").category = key
        op = self.layout.operator("object.transform_apply", text="Apply Scale to Selected", icon='FULLSCREEN_ENTER')
        op.location, op.rotation, op.scale = False, False, True


def menu_draw(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(SC_OT_merge_duplicates.bl_idname, text="Merge Duplicate Data...")
    layout.operator(SC_OT_remove_unused_slots.bl_idname, text="Remove Unused Material Slots (All)").all_objects = True
    layout.operator(SC_OT_remove_empty_slots.bl_idname, text="Remove Empty Material Slots (All)").all_objects = True
    layout.operator(SC_OT_rename_data.bl_idname, text="Rename Data to Object (All)").all_objects = True


classes = (SC_OT_merge_duplicates, SC_OT_purge, SC_OT_remove_unused_slots, SC_OT_remove_empty_slots,
           SC_OT_rename_data, SC_OT_select, SC_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_cleanup.append(menu_draw)


def unregister():
    bpy.types.TOPBAR_MT_file_cleanup.remove(menu_draw)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
