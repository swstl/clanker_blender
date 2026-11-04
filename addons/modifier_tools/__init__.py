"""Modifier Tools: bulk chores on modifier stacks (copy, toggle, apply, remove, sort, find)."""
import bpy

DEFORM = {'CAST', 'CURVE', 'DISPLACE', 'HOOK', 'LAPLACIANDEFORM', 'LATTICE', 'MESH_DEFORM',
          'SHRINKWRAP', 'SIMPLE_DEFORM', 'SMOOTH', 'CORRECTIVE_SMOOTH', 'LAPLACIANSMOOTH',
          'SURFACE_DEFORM', 'WARP', 'WAVE'}
# Fix Order: lower rank goes higher in the stack. Unlisted types (generate, edit, nodes...) = 2.
RANK = {'ARMATURE': 0, **{t: 1 for t in DEFORM}, 'SUBSURF': 3, 'TRIANGULATE': 4,
        'NORMAL_EDIT': 5, 'WEIGHTED_NORMAL': 5}

_items_cache = []  # Blender needs Python to keep dynamic enum strings alive


def _type_items(objs):
    present = {m.type for o in objs for m in o.modifiers}
    items = [('ALL', "All Types", "Every modifier", 'MODIFIER', 0)]  # 0 = enum default
    for e in bpy.types.Modifier.bl_rna.properties['type'].enum_items:
        if e.identifier in present:
            items.append((e.identifier, e.name, "", e.icon, e.value))
    _items_cache[:] = items
    return items


def _selected_types(self, context):
    return _type_items(context.selected_objects if context else [])


def _scene_types(self, context):
    return _type_items(context.scene.objects if context else [])


def _match(md, mod_type):
    return mod_type == 'ALL' or md.type == mod_type


class MT_OT_copy_stack(bpy.types.Operator):
    bl_idname = "modifier_tools.copy_stack"
    bl_label = "Copy Stack to Selected"
    bl_description = ("Copy every modifier of the active object, with all settings, "
                      "to the other selected objects")
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(items=[
        ('REPLACE', "Replace", "Remove the targets' modifiers first"),
        ('APPEND', "Append", "Add after the targets' existing modifiers"),
    ])

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.modifiers)

    def execute(self, context):
        src = context.active_object
        targets = [o for o in context.selected_editable_objects if o is not src]
        if not targets:
            self.report({'WARNING'}, "Select target objects, then the source last (active)")
            return {'CANCELLED'}
        if self.mode == 'REPLACE':
            for t in targets:
                t.modifiers.clear()
        sel = [src] + targets
        # Native op copies everything incl. pointers and geometry-nodes inputs.
        with context.temp_override(object=src, active_object=src,
                                   selected_objects=sel, selected_editable_objects=sel):
            for md in src.modifiers:
                bpy.ops.object.modifier_copy_to_selected(modifier=md.name)
        self.report({'INFO'}, f"Copied {len(src.modifiers)} modifiers to {len(targets)} objects")
        return {'FINISHED'}


class MT_OT_visibility(bpy.types.Operator):
    bl_idname = "modifier_tools.visibility"
    bl_label = "Modifier Visibility"
    bl_description = "Show or hide modifiers in viewport or render on the selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    mod_type: bpy.props.EnumProperty(name="Type", items=_selected_types)
    target: bpy.props.EnumProperty(items=[('VIEWPORT', "Viewport", ""), ('RENDER', "Render", "")])
    state: bpy.props.EnumProperty(items=[
        ('TOGGLE', "Toggle", "Hide all if any is shown, else show all"),
        ('ON', "Show", ""), ('OFF', "Hide", "")])

    def execute(self, context):
        attr = 'show_viewport' if self.target == 'VIEWPORT' else 'show_render'
        mods = [m for o in context.selected_objects for m in o.modifiers if _match(m, self.mod_type)]
        on = not any(getattr(m, attr) for m in mods) if self.state == 'TOGGLE' else self.state == 'ON'
        for m in mods:
            setattr(m, attr, on)
        self.report({'INFO'}, f"{'Shown' if on else 'Hidden'}: {len(mods)} modifiers")
        return {'FINISHED'}


class MT_OT_apply(bpy.types.Operator):
    bl_idname = "modifier_tools.apply"
    bl_label = "Apply All Except Armature"
    bl_description = "Apply every modifier on the selected meshes but keep Armature modifiers in the stack"
    bl_options = {'REGISTER', 'UNDO'}

    stop_at_armature: bpy.props.BoolProperty(
        name="Only Above Armature", default=False,
        description="Apply only the modifiers above the first Armature, keep the ones after it")
    single_user: bpy.props.BoolProperty(
        name="Make Single User", default=False,
        description="Give objects with shared mesh data their own copy instead of skipping them")

    def execute(self, context):
        applied, skipped = 0, []
        for ob in [o for o in context.selected_editable_objects if o.type == 'MESH']:
            if ob.data.shape_keys:
                skipped.append(f"{ob.name} (shape keys)")
                continue
            if ob.data.users > 1:
                if not self.single_user:
                    skipped.append(f"{ob.name} (shared data)")
                    continue
                ob.data = ob.data.copy()
            names = []
            for md in ob.modifiers:
                if md.type == 'ARMATURE':
                    if self.stop_at_armature:
                        break
                    continue
                names.append(md.name)
            with context.temp_override(object=ob, active_object=ob):
                for n in names:
                    try:
                        bpy.ops.object.modifier_apply(modifier=n)
                        applied += 1
                    except RuntimeError as e:  # e.g. disabled modifier
                        skipped.append(f"{ob.name}/{n}: {str(e).strip()}")
        self.report({'INFO'}, f"Applied {applied} modifiers")
        if skipped:
            hint = (" - for shape keys use 'Apply Modifiers (Keep Shape Keys)'"
                    if any("shape keys" in s for s in skipped) else "")
            self.report({'WARNING'}, "Skipped: " + ", ".join(skipped) + hint)
        return {'FINISHED'}


class MT_OT_remove(bpy.types.Operator):
    bl_idname = "modifier_tools.remove"
    bl_label = "Remove Modifiers"
    bl_description = "Remove all modifiers, or all of one type, from the selected objects"
    bl_options = {'REGISTER', 'UNDO'}

    mod_type: bpy.props.EnumProperty(name="Type", items=_selected_types)

    def execute(self, context):
        n = 0
        for ob in context.selected_editable_objects:
            for m in [m for m in ob.modifiers if _match(m, self.mod_type)]:
                ob.modifiers.remove(m)
                n += 1
        self.report({'INFO'}, f"Removed {n} modifiers")
        return {'FINISHED'}


class MT_OT_expand(bpy.types.Operator):
    bl_idname = "modifier_tools.expand"
    bl_label = "Expand/Collapse All"
    bl_description = "Expand or collapse every modifier panel of the active object"

    expand: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        for m in context.active_object.modifiers:
            m.show_expanded = self.expand
        return {'FINISHED'}


class MT_OT_fix_order(bpy.types.Operator):
    bl_idname = "modifier_tools.fix_order"
    bl_label = "Fix Order"
    bl_description = ("Sort the stack of selected objects: Armature, other deforms, generators, "
                      "then Subdivision, Triangulate and normal modifiers last")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for ob in context.selected_editable_objects:
            order = sorted((m for m in ob.modifiers if not m.use_pin_to_last),
                           key=lambda m: RANK.get(m.type, 2))  # stable: keeps order within a group
            for i, name in enumerate([m.name for m in order]):
                ob.modifiers.move(ob.modifiers.find(name), i)
        return {'FINISHED'}


class MT_OT_select_by_type(bpy.types.Operator):
    bl_idname = "modifier_tools.select_by_type"
    bl_label = "Select by Modifier"
    bl_description = "Select objects in the scene that have this modifier type"
    bl_options = {'REGISTER', 'UNDO'}

    mod_type: bpy.props.StringProperty(default='ALL')
    name: bpy.props.StringProperty(description="Select only this object")

    def execute(self, context):
        hits = [o for o in context.view_layer.objects
                if (not self.name or o.name == self.name)
                and any(_match(m, self.mod_type) for m in o.modifiers)]
        for o in context.view_layer.objects:
            o.select_set(o in hits)
        if hits:
            context.view_layer.objects.active = hits[0]
        self.report({'INFO'}, f"Selected {len(hits)} objects")
        return {'FINISHED'}


class MT_OT_add_weighted_normal(bpy.types.Operator):
    bl_idname = "modifier_tools.add_weighted_normal"
    bl_label = "Add Weighted Normal"
    bl_description = "Add a Weighted Normal modifier (Keep Sharp) to selected meshes that lack one"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = 0
        for ob in context.selected_editable_objects:
            if ob.type == 'MESH' and not any(m.type == 'WEIGHTED_NORMAL' for m in ob.modifiers):
                ob.modifiers.new("WeightedNormal", 'WEIGHTED_NORMAL').keep_sharp = True
                n += 1
        self.report({'INFO'}, f"Added to {n} objects")
        return {'FINISHED'}


def draw_tools(layout, context):
    col = layout.column(align=True)
    col.label(text="Copy Stack (active to selected):")
    row = col.row(align=True)
    row.operator("modifier_tools.copy_stack", text="Replace", icon='PASTEDOWN').mode = 'REPLACE'
    row.operator("modifier_tools.copy_stack", text="Append", icon='ADD').mode = 'APPEND'

    col.separator()
    col.label(text="Selected Objects:")
    row = col.row(align=True)
    row.operator_menu_enum("modifier_tools.visibility", "mod_type", text="Viewport",
                           icon='RESTRICT_VIEW_OFF').target = 'VIEWPORT'
    row.operator_menu_enum("modifier_tools.visibility", "mod_type", text="Render",
                           icon='RESTRICT_RENDER_OFF').target = 'RENDER'
    row = col.row(align=True)
    row.operator("modifier_tools.apply", text="Apply (Keep Armature)", icon='CHECKMARK')
    row.operator_menu_enum("modifier_tools.remove", "mod_type", text="Remove", icon='X')
    row = col.row(align=True)
    row.operator("modifier_tools.fix_order", icon='SORTSIZE')
    row = col.row(align=True)
    row.operator("modifier_tools.add_weighted_normal", text="Weighted Normal", icon='MOD_NORMALEDIT')
    row.operator("object.shade_auto_smooth", text="Smooth by Angle", icon='MOD_SMOOTH')

    col.separator()
    row = col.row(align=True)
    row.label(text="Active Panels:")
    row.operator("modifier_tools.expand", text="", icon='TRIA_DOWN').expand = True
    row.operator("modifier_tools.expand", text="", icon='TRIA_RIGHT').expand = False

    col.separator()
    sc = context.scene
    row = col.row(align=True)
    row.prop(sc, "modifier_tools_search", text="Find")
    row.operator("modifier_tools.select_by_type", text="", icon='RESTRICT_SELECT_OFF').mod_type = \
        sc.modifier_tools_search
    hits = [o for o in sc.objects if any(_match(m, sc.modifier_tools_search) for m in o.modifiers)]
    box = col.box().column(align=True)
    for o in hits[:20]:  # ponytail: capped list, add a UIList if scenes need scrolling
        op = box.operator("modifier_tools.select_by_type", text=o.name, icon='OBJECT_DATA', emboss=False)
        op.mod_type, op.name = sc.modifier_tools_search, o.name
    if len(hits) > 20:
        box.label(text=f"... {len(hits) - 20} more")


class MT_PT_properties(bpy.types.Panel):
    bl_label = "Modifier Tools"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'modifier'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_tools(self.layout, context)


class MT_PT_sidebar(bpy.types.Panel):
    bl_label = "Modifier Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    def draw(self, context):
        draw_tools(self.layout, context)


class MT_MT_menu(bpy.types.Menu):
    bl_label = "Modifier Tools"
    bl_idname = "MT_MT_modifier_tools"

    def draw(self, context):
        l = self.layout
        l.operator("modifier_tools.copy_stack", text="Copy Stack (Replace)").mode = 'REPLACE'
        l.operator("modifier_tools.copy_stack", text="Copy Stack (Append)").mode = 'APPEND'
        l.operator("modifier_tools.apply")
        l.operator("modifier_tools.fix_order")
        l.operator_menu_enum("modifier_tools.remove", "mod_type", text="Remove")
        l.operator_menu_enum("modifier_tools.visibility", "mod_type", text="Toggle Viewport").target = 'VIEWPORT'
        l.operator_menu_enum("modifier_tools.visibility", "mod_type", text="Toggle Render").target = 'RENDER'
        l.operator("modifier_tools.add_weighted_normal")


def menu_func(self, context):
    self.layout.separator()
    self.layout.menu(MT_MT_menu.bl_idname)


_classes = (MT_OT_copy_stack, MT_OT_visibility, MT_OT_apply, MT_OT_remove, MT_OT_expand,
            MT_OT_fix_order, MT_OT_select_by_type, MT_OT_add_weighted_normal,
            MT_PT_properties, MT_PT_sidebar, MT_MT_menu)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.modifier_tools_search = bpy.props.EnumProperty(
        name="Find Modifier", description="List objects that have this modifier type", items=_scene_types)
    bpy.types.VIEW3D_MT_object_modifiers.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_object_modifiers.remove(menu_func)
    del bpy.types.Scene.modifier_tools_search
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
