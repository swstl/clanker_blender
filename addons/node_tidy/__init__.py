"""Node Tidy: straighten links, align/distribute, reroute and frame tools, grid snap. Any node editor."""
import colorsys
import random

import bpy

# ponytail: the Python API exposes no socket positions, so sockets are placed on a fixed row
# model (header + one row per visible socket, outputs above inputs). Option/button rows are
# ignored, so nodes with many buttons land a little off.
HEADER, ROW, COLLAPSED = 36.0, 22.0, 26.0


# ---------------------------------------------------------------- pure helpers (work on any tree)

def is_reroute(n):
    return n.bl_idname == "NodeReroute"


def visible(sockets):
    return [s for s in sockets if s.enabled and not s.hide]


def socket_y(sock):
    """Approximate absolute y of a socket's center."""
    n = sock.node
    y = n.location_absolute.y
    if is_reroute(n):
        return y
    if n.hide:
        return y - COLLAPSED / 2
    outs, ins = visible(n.outputs), visible(n.inputs)
    if sock.is_output:
        i = outs.index(sock) if sock in outs else 0
    else:
        i = len(outs) + (ins.index(sock) if sock in ins else 0)
    return y - HEADER - (i + 0.5) * ROW


def size(n):
    """(width, height). node.dimensions is only valid after the editor drew the node."""
    if is_reroute(n):
        return 0.0, 0.0
    w, h = n.dimensions
    if w > 0 and h > 0:
        s = bpy.context.preferences.system.ui_scale  # dimensions are in scaled pixels
        return w / s, h / s
    if n.hide:
        return n.width, COLLAPSED
    return n.width, HEADER + (len(visible(n.outputs)) + len(visible(n.inputs))) * ROW + 10


def box(n):
    """(left, top, right, bottom) in absolute space; reroutes are a point."""
    x, y = n.location_absolute
    w, h = size(n)
    return x, y, x + w, y - h


def move(n, dx, dy):
    # location is parent-relative, a delta is the same in both spaces (frames don't scale)
    n.location.x += dx
    n.location.y += dy


def set_abs(n, xy):
    move(n, xy[0] - n.location_absolute.x, xy[1] - n.location_absolute.y)


def straighten(nodes, upstream=True):
    """Move nodes vertically so their first linked input (or output) wire is horizontal."""
    done = 0
    for n in sorted(nodes, key=lambda n: n.location_absolute.x, reverse=not upstream):
        link = next((l for s in visible(n.inputs if upstream else n.outputs) for l in s.links), None)
        if link is None:
            continue
        mine, other = (link.to_socket, link.from_socket) if upstream else (link.from_socket, link.to_socket)
        move(n, 0, socket_y(other) - socket_y(mine))
        done += 1
    return done


def align(nodes, side):
    boxes = [box(n) for n in nodes]
    l, t = min(b[0] for b in boxes), max(b[1] for b in boxes)
    r, bt = max(b[2] for b in boxes), min(b[3] for b in boxes)
    for n, (bl, bt_, br, bb) in zip(nodes, boxes):
        if side == 'LEFT':
            move(n, l - bl, 0)
        elif side == 'RIGHT':
            move(n, r - br, 0)
        elif side == 'TOP':
            move(n, 0, t - bt_)
        elif side == 'BOTTOM':
            move(n, 0, bt - bb)
        elif side == 'CENTER_X':
            move(n, (l + r) / 2 - (bl + br) / 2, 0)
        elif side == 'CENTER_Y':
            move(n, 0, (t + bt) / 2 - (bt_ + bb) / 2)


def distribute(nodes, axis='X', gap=40.0):
    """Lay nodes out left->right (X) or top->bottom (Y) with a fixed gap, keeping the first in place."""
    if axis == 'X':
        nodes = sorted(nodes, key=lambda n: box(n)[0])
        cur = box(nodes[0])[0]
        for n in nodes:
            b = box(n)
            move(n, cur - b[0], 0)
            cur += b[2] - b[0] + gap
    else:
        nodes = sorted(nodes, key=lambda n: -box(n)[1])
        cur = box(nodes[0])[1]
        for n in nodes:
            b = box(n)
            move(n, 0, cur - b[1])
            cur -= b[1] - b[3] + gap


def straighten_reroutes(nodes):
    """Put each chain of linked reroutes on one horizontal line (the source socket's height)."""
    sel = {n.name for n in nodes if is_reroute(n)}
    done = set()
    for n in nodes:
        if n.name not in sel or n.name in done:
            continue
        head, seen = n, {n.name}
        while head.inputs[0].links and (p := head.inputs[0].links[0].from_node).name in sel - seen:
            head = p
            seen.add(p.name)
        src = head.inputs[0].links
        y = socket_y(src[0].from_socket) if src else head.location_absolute.y
        stack = [head]
        while stack:
            r = stack.pop()
            if r.name in done:
                continue
            done.add(r.name)
            move(r, 0, y - r.location_absolute.y)
            stack += [l.to_node for l in r.outputs[0].links if l.to_node.name in sel]
    return len(done)


def remove_reroutes(tree, nodes):
    """Delete reroutes, linking their source straight to every target. Unfed reroutes that still
    feed something are kept (removing them would change the target's value)."""
    count = 0
    for r in [n for n in nodes if is_reroute(n)]:
        src, outs = r.inputs[0].links, list(r.outputs[0].links)
        if not src and outs:
            continue
        targets = [l.to_socket for l in outs]
        for t in targets:
            tree.links.new(src[0].from_socket, t)
        tree.nodes.remove(r)
        count += 1
    return count


def merge_reroutes(tree, nodes):
    """Collapse reroutes fed by the same socket into one."""
    keep, count = {}, 0
    for r in [n for n in nodes if is_reroute(n) and n.inputs[0].links]:
        k = keep.setdefault(r.inputs[0].links[0].from_socket.as_pointer(), r)
        if k == r:
            continue
        for t in [l.to_socket for l in r.outputs[0].links]:
            tree.links.new(k.outputs[0], t)
        tree.nodes.remove(r)
        count += 1
    return count


def pastel():
    return (*colorsys.hsv_to_rgb(random.random(), 0.35, 0.75),)


def frame_nodes(tree, nodes, label=""):
    """New shrink-to-fit frame around nodes (first node names it), random pastel color."""
    names = {n.name for n in nodes}
    tops = [n for n in nodes if not (n.parent and n.parent.name in names)]
    f = tree.nodes.new("NodeFrame")
    f.label = label or nodes[0].label or nodes[0].name
    f.use_custom_color = True
    f.color = pastel()
    f.shrink = True
    p = tops[0].parent
    if all(n.parent == p for n in tops):
        f.parent = p
    for n in tops:
        xy = n.location_absolute.copy()
        n.parent = f
        set_abs(n, xy)
    return f


def inside(n, frame):
    p = n.parent
    while p:
        if p == frame:
            return True
        p = p.parent
    return False


def remove_frames(tree, frames):
    for f in frames:
        for k in [n for n in tree.nodes if n.parent == f]:
            xy = k.location_absolute.copy()
            k.parent = f.parent
            set_abs(k, xy)
        tree.nodes.remove(f)


def snap(nodes, grid=20.0):
    for n in nodes:
        x, y = n.location_absolute
        move(n, round(x / grid) * grid - x, round(y / grid) * grid - y)


# ---------------------------------------------------------------- operators

def _tree(context):
    return context.space_data.edit_tree


def _selected(context, frames=False):
    return [n for n in _tree(context).nodes if n.select and (frames or n.bl_idname != "NodeFrame")]


class _Tidy:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp is not None and sp.type == 'NODE_EDITOR' and sp.edit_tree is not None


class NT_OT_straighten(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.straighten"
    bl_label = "Straighten Links"
    bl_description = "Move selected nodes vertically so their main wire becomes horizontal"
    direction: bpy.props.EnumProperty(items=[
        ('UPSTREAM', "To Upstream", "Line the first linked input up with the output feeding it"),
        ('DOWNSTREAM', "To Downstream", "Line the first linked output up with the input it feeds")])

    def execute(self, context):
        n = straighten(_selected(context), self.direction == 'UPSTREAM')
        self.report({'INFO'}, f"Straightened {n} node(s)")
        return {'FINISHED'}


class NT_OT_align(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.align"
    bl_label = "Align Nodes"
    bl_description = "Align selected nodes to a common edge or center"
    side: bpy.props.EnumProperty(items=[(k, k.replace("_", " ").title(), "") for k in
                                        ('LEFT', 'RIGHT', 'TOP', 'BOTTOM', 'CENTER_X', 'CENTER_Y')])

    def execute(self, context):
        nodes = _selected(context)
        if len(nodes) < 2:
            return {'CANCELLED'}
        align(nodes, self.side)
        return {'FINISHED'}


class NT_OT_distribute(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.distribute"
    bl_label = "Distribute Nodes"
    bl_description = "Space selected nodes evenly with a fixed gap"
    axis: bpy.props.EnumProperty(items=[('X', "Horizontal", ""), ('Y', "Vertical", "")])
    gap: bpy.props.FloatProperty(name="Gap", default=40.0, min=0.0)

    def execute(self, context):
        nodes = _selected(context)
        if len(nodes) < 2:
            return {'CANCELLED'}
        distribute(nodes, self.axis, self.gap)
        return {'FINISHED'}


class NT_OT_reroute_straighten(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.reroute_straighten"
    bl_label = "Straighten Reroute Chains"
    bl_description = "Put each chain of selected reroutes on one horizontal line"

    def execute(self, context):
        straighten_reroutes(_selected(context))
        return {'FINISHED'}


class NT_OT_reroute_remove(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.reroute_remove"
    bl_label = "Remove Reroutes"
    bl_description = "Delete selected reroutes and link their source straight to the targets"

    def execute(self, context):
        self.report({'INFO'}, f"Removed {remove_reroutes(_tree(context), _selected(context))} reroute(s)")
        return {'FINISHED'}


class NT_OT_reroute_merge(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.reroute_merge"
    bl_label = "Collapse Reroutes"
    bl_description = "Merge selected reroutes that carry the same socket into one"

    def execute(self, context):
        self.report({'INFO'}, f"Merged {merge_reroutes(_tree(context), _selected(context))} reroute(s)")
        return {'FINISHED'}


class NT_OT_frame(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.frame"
    bl_label = "Frame Selected"
    bl_description = "Put selected nodes in a new pastel frame labeled after the active node or your text"
    label: bpy.props.StringProperty(name="Label", description="Empty: use the active node's name")

    def execute(self, context):
        nodes = _selected(context, frames=True)
        if not nodes:
            return {'CANCELLED'}
        act = _tree(context).nodes.active
        if act in nodes:
            nodes.remove(act)
            nodes.insert(0, act)
        for n in nodes:
            n.select = False
        f = frame_nodes(_tree(context), nodes, self.label)
        f.select = True
        _tree(context).nodes.active = f
        return {'FINISHED'}


class NT_OT_frame_shrink(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.frame_shrink"
    bl_label = "Shrink Frames"
    bl_description = "Make selected frames shrink to fit their content"

    def execute(self, context):
        for f in _selected(context, frames=True):
            if f.bl_idname == "NodeFrame":
                f.shrink = True
        return {'FINISHED'}


class NT_OT_frame_remove(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.frame_remove"
    bl_label = "Remove Frames"
    bl_description = "Delete selected frames, keeping their nodes in place"

    def execute(self, context):
        remove_frames(_tree(context), [n for n in _selected(context, frames=True) if n.bl_idname == "NodeFrame"])
        return {'FINISHED'}


class NT_OT_frame_select(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.frame_select"
    bl_label = "Select Frame Content"
    bl_description = "Select every node inside the selected frames"

    def execute(self, context):
        frames = [n for n in _selected(context, frames=True) if n.bl_idname == "NodeFrame"]
        for n in _tree(context).nodes:
            if any(inside(n, f) for f in frames):
                n.select = True
        return {'FINISHED'}


class NT_OT_snap(_Tidy, bpy.types.Operator):
    bl_idname = "node_tidy.snap"
    bl_label = "Snap to Grid"
    bl_description = "Round selected node locations to a grid"
    grid: bpy.props.FloatProperty(name="Grid", default=20.0, min=1.0)

    def execute(self, context):
        snap(_selected(context), self.grid)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

def _draw(layout):
    col = layout.column(align=True)
    col.operator_menu_enum(NT_OT_straighten.bl_idname, "direction", icon='IPO_LINEAR')
    row = col.row(align=True)
    for side, icon in (('LEFT', 'ALIGN_LEFT'), ('CENTER_X', 'ALIGN_CENTER'), ('RIGHT', 'ALIGN_RIGHT'),
                       ('TOP', 'ALIGN_TOP'), ('CENTER_Y', 'ALIGN_MIDDLE'), ('BOTTOM', 'ALIGN_BOTTOM')):
        row.operator(NT_OT_align.bl_idname, text="", icon=icon).side = side
    row = col.row(align=True)
    row.operator(NT_OT_distribute.bl_idname, text="Distribute X").axis = 'X'
    row.operator(NT_OT_distribute.bl_idname, text="Distribute Y").axis = 'Y'
    col.operator(NT_OT_snap.bl_idname, icon='SNAP_GRID')
    col = layout.column(align=True)
    col.label(text="Reroutes")
    col.operator(NT_OT_reroute_straighten.bl_idname, text="Straighten Chains")
    col.operator(NT_OT_reroute_remove.bl_idname, text="Remove")
    col.operator(NT_OT_reroute_merge.bl_idname, text="Collapse Duplicates")
    col = layout.column(align=True)
    col.label(text="Frames")
    col.operator(NT_OT_frame.bl_idname)
    col.operator(NT_OT_frame_shrink.bl_idname)
    col.operator(NT_OT_frame_select.bl_idname)
    col.operator(NT_OT_frame_remove.bl_idname)
    col.operator("node.detach", text="Detach from Frame")
    col = layout.column(align=True)
    col.label(text="Display")
    col.operator("node.hide_socket_toggle", text="Toggle Unused Sockets")
    col.operator("node.hide_toggle", text="Toggle Collapse")


class NT_MT_tidy(bpy.types.Menu):
    bl_label = "Tidy"

    def draw(self, context):
        _draw(self.layout)


class NT_MT_pie(bpy.types.Menu):
    bl_label = "Node Tidy"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # pie order: W, E, S, N, NW, NE, SW, SE
        pie.operator(NT_OT_align.bl_idname, text="Left", icon='ALIGN_LEFT').side = 'LEFT'
        pie.operator(NT_OT_align.bl_idname, text="Right", icon='ALIGN_RIGHT').side = 'RIGHT'
        pie.operator(NT_OT_align.bl_idname, text="Bottom", icon='ALIGN_BOTTOM').side = 'BOTTOM'
        pie.operator(NT_OT_align.bl_idname, text="Top", icon='ALIGN_TOP').side = 'TOP'
        pie.operator(NT_OT_straighten.bl_idname, text="Straighten", icon='IPO_LINEAR').direction = 'UPSTREAM'
        pie.operator(NT_OT_frame.bl_idname)
        pie.operator(NT_OT_distribute.bl_idname, text="Distribute Y").axis = 'Y'
        pie.operator(NT_OT_distribute.bl_idname, text="Distribute X").axis = 'X'


class NT_PT_tidy(bpy.types.Panel):
    bl_label = "Tidy"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Node"

    def draw(self, context):
        _draw(self.layout)


def _menu(self, context):
    self.layout.separator()
    self.layout.menu(NT_MT_tidy.__name__)


classes = (NT_OT_straighten, NT_OT_align, NT_OT_distribute, NT_OT_reroute_straighten, NT_OT_reroute_remove,
           NT_OT_reroute_merge, NT_OT_frame, NT_OT_frame_shrink, NT_OT_frame_remove, NT_OT_frame_select,
           NT_OT_snap, NT_MT_tidy, NT_MT_pie, NT_PT_tidy)
_keymaps = []


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.NODE_MT_node.append(_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:  # Shift+Alt+Q is free in the default Node Editor keymap (5.2)
        km = kc.keymaps.new("Node Editor", space_type='NODE_EDITOR')
        kmi = km.keymap_items.new("wm.call_menu_pie", 'Q', 'PRESS', shift=True, alt=True)
        kmi.properties.name = NT_MT_pie.__name__
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.NODE_MT_node.remove(_menu)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
