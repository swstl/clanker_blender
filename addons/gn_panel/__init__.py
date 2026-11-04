"""Geometry Nodes modifier inputs in the 3D View sidebar (tab "GN").

Inputs are drawn straight from the modifier RNA (`md.properties.inputs.<identifier>`, 5.2), so
they stay live-editable; panels use the modifier's own open/closed state.
"""
import json

import bpy

PIN_KEY = "gn_panel_pins"  # JSON list of pinned socket identifiers, stored on the node group


# ---------------------------------------------------------------- core API

def input_items(ng):
    """Interface sockets the modifier exposes as inputs (no Geometry, no hidden)."""
    return [i for i in ng.interface.items_tree
            if i.item_type == 'SOCKET' and i.in_out == 'INPUT'
            and i.socket_type != 'NodeSocketGeometry' and not getattr(i, "hide_in_modifier", False)]


def get_pins(ng):
    try:
        return set(json.loads(ng.get(PIN_KEY, "[]")))
    except (TypeError, ValueError):
        return set()


def set_pin(ng, identifier, pinned):
    pins = get_pins(ng)
    (pins.add if pinned else pins.discard)(identifier)
    ng[PIN_KEY] = json.dumps(sorted(pins))


def shown_identifiers(ng, pinned_only):
    """Identifiers to show (and copy): all inputs, or only pinned ones, in interface order."""
    pins = get_pins(ng)
    return [i.identifier for i in input_items(ng) if not pinned_only or i.identifier in pins]


def _input(md, identifier):
    return getattr(md.properties.inputs, identifier, None)


def _plain(v):
    return v if isinstance(v, (str, bpy.types.ID)) or not hasattr(v, "__len__") else tuple(v)


def set_value(md, identifier, value):
    _input(md, identifier).value = value
    md.id_data.update_tag()  # 5.2: setting md.properties.inputs from Python doesn't re-evaluate


def reset_input(md, identifier):
    item = next(i for i in input_items(md.node_group) if i.identifier == identifier)
    inp = _input(md, identifier)
    if hasattr(item, "default_value"):
        try:
            inp.value = item.default_value
        except (TypeError, ValueError):  # e.g. menu without items
            pass
    if "type" in inp.bl_rna.properties:
        inp.type = 'VALUE'
    md.id_data.update_tag()


def copy_inputs(src, dst, identifiers=None):
    """Copy input values (and attribute mode/name) from modifier src to dst. Returns True if changed."""
    changed = False
    for ident in identifiers if identifiers is not None else shown_identifiers(src.node_group, False):
        a, b = _input(src, ident), _input(dst, ident)
        if a is None or b is None:
            continue
        for prop in ("value", "type", "attribute_name"):
            if prop in a.bl_rna.properties and _plain(getattr(a, prop)) != _plain(getattr(b, prop)):
                setattr(b, prop, getattr(a, prop))
                changed = True
    if changed:
        dst.id_data.update_tag()
    return changed


def copy_to_objects(md, objects, identifiers=None):
    """Copy md's inputs to every Nodes modifier using the same group on `objects`. Returns count changed."""
    n = 0
    for ob in objects:
        if ob is None or ob == md.id_data:
            continue
        for m in ob.modifiers:
            if m.type == 'NODES' and m.node_group == md.node_group and copy_inputs(md, m, identifiers):
                n += 1
    return n


def sync_selected(active, selected):
    """Edit-all mode: push every Nodes modifier of `active` to the selected objects."""
    n = 0
    for md in getattr(active, "modifiers", ()):
        if md.type == 'NODES' and md.node_group:
            n += copy_to_objects(md, selected)
    return n


def _nodes_mods(ob):
    return [m for m in ob.modifiers if m.type == 'NODES'] if ob else []


# ---------------------------------------------------------------- auto sync

def _sync_timer():
    ctx = bpy.context
    if getattr(ctx.scene, "gn_panel_auto_sync", False) and ctx.view_layer:
        sel = [o for o in ctx.scene.objects if o.select_get()]
        sync_selected(ctx.view_layer.objects.active, sel)
    return None


@bpy.app.handlers.persistent
def _on_depsgraph(scene, _depsgraph):
    # ponytail: 0.15 s debounce timer, writes only differing values so it settles after one pass
    if scene.gn_panel_auto_sync and not bpy.app.timers.is_registered(_sync_timer):
        bpy.app.timers.register(_sync_timer, first_interval=0.15)


# ---------------------------------------------------------------- operators

class _ModInput:
    modifier: bpy.props.StringProperty(options={'SKIP_SAVE'})
    identifier: bpy.props.StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return bool(_nodes_mods(context.active_object))

    def _md(self, context):
        md = context.active_object.modifiers.get(self.modifier)
        return md if md and md.type == 'NODES' and md.node_group else None


class GNP_OT_toggle_pin(_ModInput, bpy.types.Operator):
    bl_idname = "gn_panel.toggle_pin"
    bl_label = "Pin Input"
    bl_description = "Pin or unpin this input (stored on the node group); 'Pinned only' shows just the pinned ones"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        md = self._md(context)
        if not md:
            return {'CANCELLED'}
        set_pin(md.node_group, self.identifier, self.identifier not in get_pins(md.node_group))
        return {'FINISHED'}


class GNP_OT_reset_input(_ModInput, bpy.types.Operator):
    bl_idname = "gn_panel.reset_input"
    bl_label = "Reset Input"
    bl_description = "Reset this input to the node group's default value"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        md = self._md(context)
        if not md:
            return {'CANCELLED'}
        reset_input(md, self.identifier)
        return {'FINISHED'}


class GNP_OT_copy_to_selected(bpy.types.Operator):
    bl_idname = "gn_panel.copy_to_selected"
    bl_label = "Copy Inputs to Selected"
    bl_description = ("Copy this modifier's input values to modifiers using the same node group "
                      "on the other selected objects")
    bl_options = {'REGISTER', 'UNDO'}

    modifier: bpy.props.StringProperty(options={'SKIP_SAVE'})
    pinned_only: bpy.props.BoolProperty(name="Pinned Only", options={'SKIP_SAVE'},
                                        description="Copy only the pinned inputs")

    @classmethod
    def poll(cls, context):
        return bool(_nodes_mods(context.active_object))

    def execute(self, context):
        md = context.active_object.modifiers.get(self.modifier)
        if not md or md.type != 'NODES' or not md.node_group:
            return {'CANCELLED'}
        n = copy_to_objects(md, context.selected_objects,
                            shown_identifiers(md.node_group, self.pinned_only))
        self.report({'INFO'}, f"Updated {n} modifier(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

def _draw_input(layout, md, item, pins):
    inp = _input(md, item.identifier)
    if inp is None:
        return
    row = layout.row(align=True)
    attr = "type" in inp.bl_rna.properties
    use_attr = attr and inp.type == 'ATTRIBUTE'
    row.column(align=True).prop(inp, "attribute_name" if use_attr else "value", text=item.name)
    if attr:
        op = row.operator("object.geometry_nodes_input_attribute_toggle", text="", icon='SPREADSHEET',
                          depress=use_attr)
        op.modifier_name, op.input_name = md.name, item.identifier
    op = row.operator("gn_panel.reset_input", text="", icon='LOOP_BACK')
    op.modifier, op.identifier = md.name, item.identifier
    op = row.operator("gn_panel.toggle_pin", text="", emboss=False,
                      icon='PINNED' if item.identifier in pins else 'UNPINNED')
    op.modifier, op.identifier = md.name, item.identifier


def _draw_tree(layout, md, pins):
    """Inputs grouped in collapsible panels, using the modifier's own panel open state."""
    ng = md.node_group
    shown = {i.identifier for i in input_items(ng)}
    lays = {None: layout}  # panel identifier -> body layout (None value = closed)
    key = lambda p: None if p.parent is None else p.identifier  # noqa: E731 (root panel has no parent)
    for item in ng.interface.items_tree:
        parent = lays.get(key(item.parent))
        if item.item_type == 'PANEL':
            if parent is None:
                lays[item.identifier] = None
                continue
            toggle = next((c for c in item.interface_items
                           if c.item_type == 'SOCKET' and getattr(c, "is_panel_toggle", False)), None)
            prop = f"open_{item.identifier}"
            if hasattr(md.properties.panels, prop):
                header, body = parent.panel_prop(md.properties.panels, prop)
            else:
                body = header = parent.box()
            if toggle and _input(md, toggle.identifier):
                header.prop(_input(md, toggle.identifier), "value", text=item.name)
            else:
                header.label(text=item.name)
            lays[item.identifier] = body
        elif parent is not None and item.identifier in shown and not getattr(item, "is_panel_toggle", False):
            _draw_input(parent, md, item, pins)


class GNP_PT_panel(bpy.types.Panel):
    bl_label = "Geometry Nodes"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GN"

    @classmethod
    def poll(cls, context):
        return bool(_nodes_mods(context.active_object))

    def draw(self, context):
        layout = self.layout
        sc = context.scene
        row = layout.row(align=True)
        row.prop(sc, "gn_panel_pinned_only", toggle=True, icon='PINNED')
        row.prop(sc, "gn_panel_auto_sync", toggle=True, icon='UV_SYNC_SELECT')
        for md in _nodes_mods(context.active_object):
            header, body = layout.panel_prop(md, "show_expanded")
            header.prop(md, "show_viewport", text="", emboss=False)
            header.label(text=md.name, icon='GEOMETRY_NODES')
            if body is None:
                continue
            body.prop(md, "node_group", text="")
            ng = md.node_group
            if not ng:
                continue
            op = body.operator("gn_panel.copy_to_selected", icon='PASTEDOWN')
            op.modifier, op.pinned_only = md.name, sc.gn_panel_pinned_only
            pins = get_pins(ng)
            if not sc.gn_panel_pinned_only:
                _draw_tree(body, md, pins)
                continue
            items = [i for i in input_items(ng) if i.identifier in pins]
            if not items:
                body.label(text="No pinned inputs", icon='INFO')
            for item in items:
                _draw_input(body, md, item, pins)


classes = (GNP_OT_toggle_pin, GNP_OT_reset_input, GNP_OT_copy_to_selected, GNP_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.gn_panel_pinned_only = bpy.props.BoolProperty(
        name="Pinned Only", description="Show (and copy) only pinned inputs")
    bpy.types.Scene.gn_panel_auto_sync = bpy.props.BoolProperty(
        name="Edit All Selected",
        description="Push input changes on the active object to the same modifiers on selected objects")
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if bpy.app.timers.is_registered(_sync_timer):
        bpy.app.timers.unregister(_sync_timer)
    del bpy.types.Scene.gn_panel_auto_sync
    del bpy.types.Scene.gn_panel_pinned_only
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
