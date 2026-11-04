"""Pie Builder: define pie menus in Preferences (8 slots each: operator + properties + icon + label)."""
import ast

import bpy

MAX_PIES = 8
KEYMAPS = [("Window", "Everywhere", ""), ("3D View", "3D View", ""), ("Object Mode", "Object Mode", ""),
           ("Mesh", "Edit Mesh", ""), ("Pose", "Pose Mode", ""), ("Sculpt", "Sculpt Mode", ""),
           ("Node Editor", "Node Editor", ""), ("Graph Editor", "Graph Editor", ""),
           ("Dopesheet", "Dope Sheet", ""), ("Image", "Image Editor", "")]
SPACE = {"3D View": 'VIEW_3D', "Node Editor": 'NODE_EDITOR', "Graph Editor": 'GRAPH_EDITOR',
         "Dopesheet": 'DOPESHEET_EDITOR', "Image": 'IMAGE_EDITOR'}
DIRS = ("Left", "Right", "Bottom", "Top", "Top Left", "Top Right", "Bottom Left", "Bottom Right")


def parse_props(text):
    """'mode="EDIT", toggle=True' -> dict. Raises ValueError on bad input (only literals allowed)."""
    text = text.strip()
    if not text:
        return {}
    tree = ast.parse(f"f({text})", mode="eval")
    call = tree.body
    if call.args:
        raise ValueError("use name=value pairs")
    return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}


def op_exists(idname):
    mod, _, name = idname.partition(".")
    if not mod or not name:
        return False
    try:
        getattr(getattr(bpy.ops, mod), name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


class PB_Item(bpy.types.PropertyGroup):
    label: bpy.props.StringProperty(name="Label")
    operator: bpy.props.StringProperty(name="Operator", description="e.g. object.shade_smooth  (hover a button "
                                       "with Python tooltips on to see its operator)")
    props: bpy.props.StringProperty(name="Properties", description='e.g. mode="EDIT", toggle=True')
    icon: bpy.props.StringProperty(name="Icon", default="NONE")


class PB_Pie(bpy.types.PropertyGroup):
    title: bpy.props.StringProperty(name="Title", default="My Pie")
    keymap: bpy.props.EnumProperty(name="Where", items=KEYMAPS, default="3D View")
    key: bpy.props.StringProperty(name="Key", default="Q", description="Event type, e.g. Q, W, F5, ACCENT_GRAVE")
    ctrl: bpy.props.BoolProperty(name="Ctrl")
    alt: bpy.props.BoolProperty(name="Alt", default=True)
    shift: bpy.props.BoolProperty(name="Shift", default=True)
    items: bpy.props.CollectionProperty(type=PB_Item)
    expanded: bpy.props.BoolProperty(default=True)


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def draw_pie(layout, pie_def):
    pie = layout.menu_pie()
    for it in list(pie_def.items)[:8]:
        if not it.operator:
            pie.separator()
            continue
        if not op_exists(it.operator):
            pie.label(text=f"? {it.operator}", icon='ERROR')
            continue
        try:
            props = parse_props(it.props)
        except (ValueError, SyntaxError):
            pie.label(text=f"bad props: {it.label or it.operator}", icon='ERROR')
            continue
        icon = it.icon if it.icon in _icon_names() else 'NONE'
        op = pie.operator(it.operator, text=it.label or it.operator, icon=icon)
        for k, v in props.items():
            try:
                setattr(op, k, v)
            except (AttributeError, TypeError):
                pass


_icons = []


def _icon_names():
    if not _icons:
        _icons.extend(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
    return _icons


def _make_menu(i):
    class Menu(bpy.types.Menu):
        bl_idname = f"PB_MT_pie_{i}"
        bl_label = "Pie"

        def draw(self, context):
            pies = _prefs().pies
            if i < len(pies):
                draw_pie(self.layout, pies[i])
    Menu.__name__ = f"PB_MT_pie_{i}"
    return Menu


_menus = [_make_menu(i) for i in range(MAX_PIES)]
_keymaps = []


def rebuild_keymaps():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc:
        return 0
    for i, p in enumerate(list(_prefs().pies)[:MAX_PIES]):
        if not p.key:
            continue
        km = kc.keymaps.new(name=p.keymap, space_type=SPACE.get(p.keymap, 'EMPTY'))
        try:
            kmi = km.keymap_items.new("wm.call_menu_pie", p.key.upper(), 'PRESS',
                                      ctrl=p.ctrl, alt=p.alt, shift=p.shift)
        except TypeError:
            continue  # invalid key name
        kmi.properties.name = f"PB_MT_pie_{i}"
        _keymaps.append((km, kmi))
    return len(_keymaps)


class PB_OT_add_pie(bpy.types.Operator):
    bl_idname = "pie_builder.add_pie"
    bl_label = "Add Pie"
    bl_description = "Add a new pie with 8 empty slots"

    def execute(self, context):
        pies = _prefs().pies
        if len(pies) >= MAX_PIES:
            self.report({'WARNING'}, f"Max {MAX_PIES} pies")
            return {'CANCELLED'}
        p = pies.add()
        for _ in range(8):
            p.items.add()
        return {'FINISHED'}


class PB_OT_remove_pie(bpy.types.Operator):
    bl_idname = "pie_builder.remove_pie"
    bl_label = "Remove Pie"
    bl_description = "Delete this pie"

    index: bpy.props.IntProperty()

    def execute(self, context):
        _prefs().pies.remove(self.index)
        rebuild_keymaps()
        return {'FINISHED'}


class PB_OT_apply(bpy.types.Operator):
    bl_idname = "pie_builder.apply"
    bl_label = "Apply Hotkeys"
    bl_description = "Re-create the hotkeys of all pies (also done on startup). Save Preferences to keep pies"

    def execute(self, context):
        self.report({'INFO'}, f"{rebuild_keymaps()} pie hotkeys active")
        return {'FINISHED'}


class PB_OT_test(bpy.types.Operator):
    bl_idname = "pie_builder.open"
    bl_label = "Open Pie"
    bl_description = "Open this pie now"

    index: bpy.props.IntProperty()

    def execute(self, context):
        bpy.ops.wm.call_menu_pie(name=f"PB_MT_pie_{self.index}")
        return {'FINISHED'}


class PB_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    pies: bpy.props.CollectionProperty(type=PB_Pie)

    def draw(self, context):
        lay = self.layout
        row = lay.row()
        row.operator("pie_builder.add_pie", icon='ADD')
        row.operator("pie_builder.apply", icon='FILE_REFRESH')
        for i, p in enumerate(self.pies):
            box = lay.box()
            row = box.row(align=True)
            row.prop(p, "expanded", text="", emboss=False, icon='DOWNARROW_HLT' if p.expanded else 'RIGHTARROW')
            row.prop(p, "title", text="")
            row.prop(p, "keymap", text="")
            row.prop(p, "ctrl", toggle=True)
            row.prop(p, "alt", toggle=True)
            row.prop(p, "shift", toggle=True)
            row.prop(p, "key", text="")
            row.operator("pie_builder.remove_pie", text="", icon='X').index = i
            if not p.expanded:
                continue
            for d, it in zip(DIRS, p.items):
                r = box.row(align=True)
                r.label(text=d)
                r.prop(it, "label", text="")
                sub = r.row(align=True)
                sub.alert = bool(it.operator) and not op_exists(it.operator)
                sub.prop(it, "operator", text="")
                r.prop(it, "props", text="")
                r.prop(it, "icon", text="")
        lay.label(text="Tip: enable Preferences > Interface > Python Tooltips to see operator names", icon='INFO')


_classes = (PB_Item, PB_Pie, PB_OT_add_pie, PB_OT_remove_pie, PB_OT_apply, PB_OT_test, PB_Prefs) + tuple(_menus)


def _deferred_keymaps():
    try:
        rebuild_keymaps()
    except Exception as e:
        print("Pie Builder:", e)
    return None


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    # Prefs are loaded after register on startup: build hotkeys a moment later.
    bpy.app.timers.register(_deferred_keymaps, first_interval=0.5)
    rebuild_keymaps()


def unregister():
    if bpy.app.timers.is_registered(_deferred_keymaps):
        bpy.app.timers.unregister(_deferred_keymaps)
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
