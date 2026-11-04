"""Clanker Hub: control where the Clanker add-ons put their sidebar (N panel) tabs.

Modes: keep each add-on's own tab, merge everything into one tab, or rename tabs one by one.
Works by re-registering the panels with a new bl_category; originals are remembered.
"""
import json

import bpy

PREFIX = "bl_ext.clanker."
_original = {}  # panel class -> original bl_category


def clanker_panels():
    """Sidebar panels registered by Clanker add-ons (excluding this hub)."""
    out = []
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name, None)
        if (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)
                and getattr(cls, "__module__", "").startswith(PREFIX)
                and not cls.__module__.startswith(__package__)
                and getattr(cls, "bl_region_type", "") == 'UI' and hasattr(cls, "bl_category")):
            out.append(cls)
    return out


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def target_category(original, prefs):
    if prefs.mode == 'MERGE':
        return prefs.merged_name or "Clanker"
    if prefs.mode == 'RENAME':
        return json.loads(prefs.renames or "{}").get(original) or original
    return original


def _reregister(classes, category_of):
    # Children (bl_parent_id) must go before parents on unregister, after them on register.
    kids = [c for c in classes if getattr(c, "bl_parent_id", "")]
    roots = [c for c in classes if not getattr(c, "bl_parent_id", "")]
    for c in kids + roots:
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
    for c in roots + kids:
        c.bl_category = category_of(c)
        bpy.utils.register_class(c)


def apply_tabs():
    """Move every Clanker panel to the tab the preferences ask for. Returns number of panels moved."""
    prefs = _prefs()
    panels = clanker_panels()
    for c in panels:
        _original.setdefault(c, c.bl_category)
    changed = [c for c in panels if c.bl_category != target_category(_original[c], prefs)]
    if changed:
        # Parents and children must move together.
        ids = {c.__name__ for c in changed} | {getattr(c, "bl_parent_id", "") for c in changed}
        group = [c for c in panels if c.__name__ in ids or getattr(c, "bl_parent_id", "") in ids]
        _reregister(group, lambda c: target_category(_original[c], prefs))
    return len(changed)


def restore_tabs():
    panels = [c for c in clanker_panels() if c in _original]
    _reregister(panels, lambda c: _original[c])
    _original.clear()


def _update(self, context):
    apply_tabs()


class CH_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    mode: bpy.props.EnumProperty(name="Sidebar Tabs", update=_update, items=[
        ('KEEP', "Keep Own Tabs", "Each add-on uses its own sidebar tab"),
        ('MERGE', "Merge Into One Tab", "All Clanker panels in a single sidebar tab"),
        ('RENAME', "Rename Tabs", "Pick a new tab name per original tab")])
    merged_name: bpy.props.StringProperty(name="Tab Name", default="Clanker", update=_update)
    renames: bpy.props.StringProperty(default="{}")  # JSON {original: new}

    def draw(self, context):
        lay = self.layout
        lay.prop(self, "mode", expand=True)
        if self.mode == 'MERGE':
            lay.prop(self, "merged_name")
        elif self.mode == 'RENAME':
            mapping = json.loads(self.renames or "{}")
            cats = sorted({_original.get(c, c.bl_category) for c in clanker_panels()})
            for cat in cats:
                row = lay.row()
                row.label(text=cat)
                op = row.operator("clanker_hub.rename", text=mapping.get(cat, cat))
                op.original = cat
        lay.operator("clanker_hub.apply", icon='FILE_REFRESH')
        lay.label(text="Re-apply after enabling more add-ons. Save Preferences to keep the choice.",
                  icon='INFO')


class CH_OT_rename(bpy.types.Operator):
    bl_idname = "clanker_hub.rename"
    bl_label = "Rename Tab"
    bl_description = "Choose the tab name for panels that use this tab"

    original: bpy.props.StringProperty(options={'SKIP_SAVE'})
    new_name: bpy.props.StringProperty(name="New Tab Name", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        self.new_name = json.loads(_prefs().renames or "{}").get(self.original, self.original)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        p = _prefs()
        mapping = json.loads(p.renames or "{}")
        mapping[self.original] = self.new_name.strip() or self.original
        p.renames = json.dumps(mapping)
        apply_tabs()
        return {'FINISHED'}


class CH_OT_apply(bpy.types.Operator):
    bl_idname = "clanker_hub.apply"
    bl_label = "Apply Tab Layout"
    bl_description = "Move all Clanker sidebar panels according to the chosen mode"

    def execute(self, context):
        self.report({'INFO'}, f"Moved {apply_tabs()} panels")
        return {'FINISHED'}


def _deferred():
    try:
        if _prefs().mode != 'KEEP':
            apply_tabs()
    except Exception as e:
        print("Clanker Hub:", e)
    return None


_classes = (CH_Prefs, CH_OT_rename, CH_OT_apply)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    # Other Clanker add-ons may register after this one at startup: apply once everything loaded.
    bpy.app.timers.register(_deferred, first_interval=1.0)


def unregister():
    if bpy.app.timers.is_registered(_deferred):
        bpy.app.timers.unregister(_deferred)
    restore_tabs()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
