"""Node Finder: file-wide node search with jump-to, group usage, missing images, group swap, unused-node cleanup."""
import json
import os
from collections import namedtuple

import bpy

# One Hit per node instance. trees = root..leaf, groups = the group nodes entered on the way.
Hit = namedtuple("Hit", "kind owner extra trees groups node")
COLL = {"MATERIAL": "materials", "WORLD": "worlds", "LIGHT": "lights", "TEXTURE": "textures",
        "SCENE": "scenes", "MODIFIER": "objects", "NODE_GROUP": "node_groups"}
ICON = {"MATERIAL": 'MATERIAL', "WORLD": 'WORLD', "LIGHT": 'LIGHT', "TEXTURE": 'TEXTURE',
        "SCENE": 'SCENE_DATA', "MODIFIER": 'MODIFIER', "NODE_GROUP": 'NODETREE'}


# ---------------------------------------------------------------- traversal (pure, over bpy.data)

def root_tree(kind, owner, extra=""):
    if kind == "SCENE":
        return owner.compositing_node_group
    if kind == "MODIFIER":
        md = owner.modifiers.get(extra)
        return md.node_group if md and md.type == 'NODES' else None
    if kind == "NODE_GROUP":
        return owner
    return owner.node_tree


def roots():
    """(kind, owner, extra). Owners with a real editor context come first, so they claim shared groups."""
    for kind in ("MATERIAL", "WORLD", "LIGHT", "TEXTURE", "SCENE"):
        for idb in getattr(bpy.data, COLL[kind]):
            yield kind, idb, ""
    for ob in bpy.data.objects:
        for md in ob.modifiers:
            if md.type == 'NODES':
                yield "MODIFIER", ob, md.name
    for ng in bpy.data.node_groups:
        yield "NODE_GROUP", ng, ""


def walk():
    """Every node in the file exactly once, nested groups entered from their first user."""
    seen = set()

    def rec(kind, owner, extra, trees, groups):
        seen.add(trees[-1].as_pointer())
        for n in trees[-1].nodes:
            yield Hit(kind, owner, extra, trees, groups, n)
            nt = getattr(n, "node_tree", None) if n.type == 'GROUP' else None
            if nt and nt.as_pointer() not in seen:
                yield from rec(kind, owner, extra, trees + (nt,), groups + (n,))

    for kind, owner, extra in roots():
        tree = root_tree(kind, owner, extra)
        if tree is not None and tree.as_pointer() not in seen:
            yield from rec(kind, owner, extra, (tree,), ())


def read_only(h):
    if h.node is None:  # modifier entry
        return bool(h.owner.library)
    t = h.trees[-1]
    return bool(t.library or (t.is_embedded_data and h.owner.library))


def path_label(h):
    parts = [h.owner.name] + ([h.extra] if h.extra else []) + [g.node_tree.name for g in h.groups]
    if h.node is not None:
        parts.append(h.node.label or h.node.name)
    return " > ".join(parts)


def type_name(node):
    return node.bl_rna.name


def search(query="", node_type="", image="", group=""):
    q, im, gr = query.lower(), image.lower(), group.lower()
    out = []
    for h in walk():
        n = h.node
        if q and q not in f"{n.name}\n{n.label}".lower():
            continue
        if node_type and type_name(n) != node_type:
            continue
        if im:
            img = getattr(n, "image", None)
            if not isinstance(img, bpy.types.Image) or im not in img.name.lower():
                continue
        if gr:
            nt = getattr(n, "node_tree", None) if n.type == 'GROUP' else None
            if not nt or gr not in nt.name.lower():
                continue
        out.append(h)
    return out


def node_types():
    return sorted({type_name(h.node) for h in walk()})


def where_used(group):
    hits = [h for h in walk() if h.node.type == 'GROUP' and getattr(h.node, "node_tree", None) == group]
    hits += [Hit("MODIFIER", ob, md.name, (group,), (), None) for ob in bpy.data.objects
             for md in ob.modifiers if md.type == 'NODES' and md.node_group == group]
    return hits


def image_missing(img):
    if img is None:
        return True
    # ponytail: only FILE/MOVIE checked; UDIM tiles and sequences need per-tile/frame path expansion
    if img.source not in {'FILE', 'MOVIE'} or img.packed_file:
        return False
    return not os.path.exists(bpy.path.abspath(img.filepath, library=img.library))


def missing_images():
    return [h for h in walk() if "image" in h.node.bl_rna.properties and image_missing(h.node.image)]


def nested_groups(tree, acc=None):
    acc = set() if acc is None else acc
    for n in tree.nodes:
        nt = getattr(n, "node_tree", None) if n.type == 'GROUP' else None
        if nt and nt not in acc:
            acc.add(nt)
            nested_groups(nt, acc)
    return acc


def replace_group(a, b):
    """Point every editable use of group `a` at `b`. Returns the count."""
    if a == b or a.bl_idname != b.bl_idname:
        raise ValueError("Groups must differ and be of the same tree type")
    if a in nested_groups(b):
        raise ValueError(f"'{b.name}' contains '{a.name}': replacing would make a recursive group")
    n = 0
    for h in where_used(a):
        if read_only(h) or (h.node is not None and h.trees[-1] == b):
            continue
        if h.node is None:
            h.owner.modifiers[h.extra].node_group = b
        else:
            h.node.node_tree = b
        n += 1
    return n


def unused_in_tree(tree):
    """Names of nodes that feed no output node. Trees without any output node are left alone."""
    outs = [n.name for n in tree.nodes if not n.outputs and n.type != 'FRAME']
    if not outs:
        return set()
    feeds = {}
    for link in tree.links:
        feeds.setdefault(link.to_node.name, []).append(link.from_node.name)
    keep, stack = set(), outs
    while stack:
        x = stack.pop()
        if x not in keep:
            keep.add(x)
            stack.extend(feeds.get(x, ()))
    return {n.name for n in tree.nodes if n.name not in keep and n.type != 'FRAME'}


def find_unused():
    cache, out = {}, []
    for h in walk():
        t = h.trees[-1]
        if t.as_pointer() not in cache:
            cache[t.as_pointer()] = unused_in_tree(t)
        if h.node.name in cache[t.as_pointer()] and not read_only(h):
            out.append(h)
    return out


def remove_hits(hits):
    for h in hits:
        h.trees[-1].nodes.remove(h.node)
    return len(hits)


# ---------------------------------------------------------------- results + jump

def store(settings, hits):
    settings.results.clear()
    for h in hits:
        r = settings.results.add()
        r.name = path_label(h)
        r.kind, r.owner, r.extra = h.kind, h.owner.name, h.extra
        r.lib = h.owner.library.filepath if h.owner.library else ""
        r.groups = json.dumps([g.name for g in h.groups])
        r.node = h.node.name if h.node else ""
        r.ntype = type_name(h.node) if h.node else "Modifier"
        r.read_only = read_only(h)
    settings.index = -1


def resolve(item):
    """(owner, chain) with chain = [(root_tree, None), (group_tree, group_node), ...] or None."""
    owner = getattr(bpy.data, COLL[item.kind]).get((item.owner, item.lib or None))
    tree = owner and root_tree(item.kind, owner, item.extra)
    if tree is None:
        return None
    chain = [(tree, None)]
    for name in json.loads(item.groups or "[]"):
        gn = tree.nodes.get(name)
        tree = gn and getattr(gn, "node_tree", None)
        if tree is None:
            return None
        chain.append((tree, gn))
    return owner, chain


def user_object(kind, owner, extra, view_layer):
    """(object, material slot index) that makes the node editor show `owner` without pinning."""
    obs = [o for o in view_layer.objects if o]
    if view_layer.objects.active in obs:
        obs.insert(0, view_layer.objects.active)
    for o in obs:
        if kind == "MATERIAL":
            for i, s in enumerate(o.material_slots):
                if s.material == owner:
                    return o, i
        elif kind == "LIGHT" and o.data == owner or kind == "MODIFIER" and o == owner:
            return o, None
    return None, None


def _activate(context, kind, owner, extra):
    """Change selection so the editor's own context shows the tree. False -> pin instead."""
    if kind == "WORLD":
        return context.scene.world == owner
    if kind == "SCENE":
        return context.scene == owner
    if kind not in {"MATERIAL", "LIGHT", "MODIFIER"}:
        return False
    ob, slot = user_object(kind, owner, extra, context.view_layer)
    if ob is None:
        return False
    try:
        ob.select_set(True)
    except RuntimeError:
        pass
    context.view_layer.objects.active = ob
    if slot is not None:
        ob.active_material_index = slot
    if kind == "MODIFIER":
        ob.modifiers[extra].is_active = True
    return True


def jump(context, item):
    """Show the item's tree in a node editor, select and frame the node. Returns an error or None."""
    r = resolve(item)
    if r is None:
        return "Result no longer exists, search again"
    owner, chain = r
    root, leaf = chain[0][0], chain[-1][0]
    node = leaf.nodes.get(item.node) if item.node else None
    if node and not item.read_only:
        for n in leaf.nodes:
            n.select = False
        node.select = True
        leaf.nodes.active = node
    in_context = _activate(context, item.kind, owner, item.extra)

    screen = context.screen
    area = context.area if context.area and context.area.type == 'NODE_EDITOR' else \
        next((a for a in (screen.areas if screen else ()) if a.type == 'NODE_EDITOR'), None)
    if area is None:
        return None
    sp = area.spaces.active
    sp.tree_type = root.bl_idname
    sub = {"WORLD": ("shader_type", 'WORLD'), "MATERIAL": ("shader_type", 'OBJECT'),
           "LIGHT": ("shader_type", 'OBJECT'), "MODIFIER": ("node_tree_sub_type", 'MODIFIER'),
           "SCENE": ("node_tree_sub_type", 'SCENE')}.get(item.kind)
    if in_context and sub:
        try:
            setattr(sp, *sub)
        except TypeError:
            pass
    sp.pin = not in_context
    if not in_context:
        sp.path.start(root)
    win = context.window

    def finish():
        # Runs after the editor refreshed its context (which resets the path to the root tree).
        try:
            if not len(sp.path) or sp.path[0].node_tree != root:
                sp.pin = True
                sp.path.start(root)
            while len(sp.path) > 1:
                sp.path.pop()
            for tree, gn in chain[1:]:
                sp.path.append(tree, node=gn)
            if node:  # node bounds exist only once the editor drew this tree: frame after a redraw
                area.tag_redraw()
                bpy.app.timers.register(frame, first_interval=0.1)
        except (ReferenceError, RuntimeError):
            pass  # area closed meanwhile

    def frame():
        try:
            region = next(rg for rg in area.regions if rg.type == 'WINDOW')
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.node.view_selected()
        except (ReferenceError, RuntimeError, StopIteration):
            pass

    bpy.app.timers.register(finish, first_interval=0.05)
    return None


def _context_group(context):
    sp = context.space_data
    tree = getattr(sp, "edit_tree", None)
    if tree and not tree.is_embedded_data and tree.name in bpy.data.node_groups:
        return tree
    node = tree.nodes.active if tree else None
    return getattr(node, "node_tree", None) if node and node.type == 'GROUP' else None


# ---------------------------------------------------------------- properties

class NF_Result(bpy.types.PropertyGroup):
    kind: bpy.props.StringProperty()
    owner: bpy.props.StringProperty()
    lib: bpy.props.StringProperty()
    extra: bpy.props.StringProperty()
    groups: bpy.props.StringProperty()
    node: bpy.props.StringProperty()
    ntype: bpy.props.StringProperty()
    read_only: bpy.props.BoolProperty()


def _on_index(self, context):
    if 0 <= self.index < len(self.results):
        jump(context, self.results[self.index])


class NF_Settings(bpy.types.PropertyGroup):
    query: bpy.props.StringProperty(name="Name", description="Node name or label contains")
    node_type: bpy.props.StringProperty(name="Type", description="Node type (refresh the list to fill it)")
    image: bpy.props.StringProperty(name="Image", description="Image used by the node contains")
    group: bpy.props.StringProperty(name="Group", description="Node group used by the node contains")
    types: bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
    results: bpy.props.CollectionProperty(type=NF_Result)
    index: bpy.props.IntProperty(default=-1, update=_on_index)
    group_a: bpy.props.StringProperty(name="Group", description="Node group to look up or replace (empty: active group)")
    group_b: bpy.props.StringProperty(name="With", description="Replacement node group")


def _settings(context):
    return context.window_manager.node_finder


def _fill_types(s):
    s.types.clear()
    for t in node_types():
        s.types.add().name = t


# ---------------------------------------------------------------- operators

class NF_OT_search(bpy.types.Operator):
    bl_idname = "node_finder.search"
    bl_label = "Search"
    bl_description = "Search all node trees in the file (materials, worlds, lights, compositor, modifiers, groups)"

    def execute(self, context):
        s = _settings(context)
        _fill_types(s)
        store(s, search(s.query, s.node_type, s.image, s.group))
        self.report({'INFO'}, f"{len(s.results)} node(s) found")
        return {'FINISHED'}


class NF_OT_refresh_types(bpy.types.Operator):
    bl_idname = "node_finder.refresh_types"
    bl_label = "Refresh Types"
    bl_description = "Rebuild the node type list from all nodes in the file"

    def execute(self, context):
        _fill_types(_settings(context))
        return {'FINISHED'}


class NF_OT_jump(bpy.types.Operator):
    bl_idname = "node_finder.jump"
    bl_label = "Jump to Node"
    bl_description = "Open this node's tree in the node editor, select and frame the node"
    index: bpy.props.IntProperty()

    def execute(self, context):
        s = _settings(context)
        if not 0 <= self.index < len(s.results):
            return {'CANCELLED'}
        err = jump(context, s.results[self.index])
        if err:
            self.report({'WARNING'}, err)
            return {'CANCELLED'}
        return {'FINISHED'}


class NF_OT_where_used(bpy.types.Operator):
    bl_idname = "node_finder.where_used"
    bl_label = "Where Used"
    bl_description = "List every node and modifier using the node group (field, or the active group)"

    def execute(self, context):
        s = _settings(context)
        g = bpy.data.node_groups.get(s.group_a) if s.group_a else _context_group(context)
        if g is None:
            self.report({'WARNING'}, "Pick a node group or open one in the editor")
            return {'CANCELLED'}
        store(s, where_used(g))
        self.report({'INFO'}, f"'{g.name}' used {len(s.results)} time(s)")
        return {'FINISHED'}


class NF_OT_missing_images(bpy.types.Operator):
    bl_idname = "node_finder.missing_images"
    bl_label = "Find Missing Images"
    bl_description = "List image nodes with no image or an image file that does not exist"

    def execute(self, context):
        s = _settings(context)
        store(s, missing_images())
        self.report({'INFO'}, f"{len(s.results)} image node(s) with missing images")
        return {'FINISHED'}


class NF_OT_replace_group(bpy.types.Operator):
    bl_idname = "node_finder.replace_group"
    bl_label = "Replace Node Group"
    bl_description = "Swap every use of the first group for the second, file-wide (linked data is skipped)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        a, b = bpy.data.node_groups.get(s.group_a), bpy.data.node_groups.get(s.group_b)
        if not a or not b:
            self.report({'WARNING'}, "Pick both node groups")
            return {'CANCELLED'}
        try:
            n = replace_group(a, b)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Replaced {n} use(s) of '{a.name}' with '{b.name}'")
        return {'FINISHED'}


class NF_OT_remove_unused(bpy.types.Operator):
    bl_idname = "node_finder.remove_unused"
    bl_label = "Remove Unused Nodes"
    bl_description = ("Delete nodes that feed no output in every editable node tree of the file "
                      "(frames kept; dry run only lists them)")
    bl_options = {'REGISTER', 'UNDO'}
    dry_run: bpy.props.BoolProperty(name="Dry Run", description="Only list the nodes", default=True)

    def execute(self, context):
        s = _settings(context)
        hits = find_unused()
        trees = len({h.trees[-1].as_pointer() for h in hits})
        if self.dry_run:
            store(s, hits)
            self.report({'INFO'}, f"{len(hits)} unused node(s) in {trees} tree(s) (dry run)")
        else:
            s.results.clear()
            self.report({'INFO'}, f"Removed {remove_hits(hits)} unused node(s) from {trees} tree(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class NF_UL_results(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon=ICON.get(item.kind, 'NODE'))
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text=item.ntype, icon='LINKED' if item.read_only else 'NONE')
        row.operator(NF_OT_jump.bl_idname, text="", icon='ZOOM_SELECTED', emboss=False).index = index


class NF_PT_find(bpy.types.Panel):
    bl_label = "Node Finder"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Find"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column(align=True)
        col.prop(s, "query", icon='VIEWZOOM')
        row = col.row(align=True)
        row.prop_search(s, "node_type", s, "types")
        row.operator(NF_OT_refresh_types.bl_idname, text="", icon='FILE_REFRESH')
        col.prop(s, "image", icon='IMAGE_DATA')
        col.prop(s, "group", icon='NODETREE')
        self.layout.operator(NF_OT_search.bl_idname, icon='VIEWZOOM')
        self.layout.template_list("NF_UL_results", "", s, "results", s, "index", rows=6)


class NF_PT_tools(bpy.types.Panel):
    bl_label = "File-wide Tools"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Find"
    bl_parent_id = "NF_PT_find"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column(align=True)
        col.prop_search(s, "group_a", bpy.data, "node_groups")
        col.operator(NF_OT_where_used.bl_idname, icon='VIEWZOOM')
        col.separator()
        col.prop_search(s, "group_b", bpy.data, "node_groups")
        col.operator(NF_OT_replace_group.bl_idname, icon='FILE_REFRESH')
        col.separator()
        col.operator(NF_OT_missing_images.bl_idname, icon='LIBRARY_DATA_BROKEN')
        row = col.row(align=True)
        row.operator(NF_OT_remove_unused.bl_idname, text="Preview Unused", icon='VIEWZOOM').dry_run = True
        row.operator(NF_OT_remove_unused.bl_idname, text="Remove", icon='TRASH').dry_run = False


classes = (NF_Result, NF_Settings, NF_OT_search, NF_OT_refresh_types, NF_OT_jump, NF_OT_where_used,
           NF_OT_missing_images, NF_OT_replace_group, NF_OT_remove_unused, NF_UL_results, NF_PT_find, NF_PT_tools)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.node_finder = bpy.props.PointerProperty(type=NF_Settings)


def unregister():
    del bpy.types.WindowManager.node_finder
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
