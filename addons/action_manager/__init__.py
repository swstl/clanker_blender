"""Action Manager: list, switch, protect and organize the many actions of one rig."""
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bl_ui.anim import draw_action_and_slot_selector_for_id


# ---------------------------------------------------------------- core

def is_compatible(ob, act):
    """Action has no slots yet, or a slot for this ID type / the last used slot identifier."""
    if not act.slots:
        return True
    last = ob.animation_data.last_slot_identifier if ob.animation_data else ""
    return any(s.target_id_type in {ob.id_type, 'UNSPECIFIED'} or s.identifier == last
               for s in act.slots)


def compatible_actions(ob):
    return [a for a in bpy.data.actions if is_compatible(ob, a)]  # bpy.data is name-sorted


def reset_pose(ob):
    """Rest pose for armatures so channels the next action doesn't key don't keep old values."""
    if ob.type != 'ARMATURE':
        return
    for pb in ob.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.rotation_axis_angle = (0, 0, 1, 0)
        pb.scale = (1, 1, 1)


def apply_range(scene, act):
    if act.is_empty and not act.use_frame_range:
        return
    start, end = act.frame_range  # manual range when use_frame_range, else key range
    scene.frame_start = int(start)
    scene.frame_end = max(int(start), round(end))


def assign_action(context, ob, act, set_range=True, reset=False):
    ad = ob.animation_data or ob.animation_data_create()
    if ad.use_tweak_mode:
        raise RuntimeError("Exit NLA tweak mode first")
    if reset:
        reset_pose(ob)
    ad.action = act
    if act and ad.action_slot is None:  # no slot matched last_slot_identifier
        slots = list(ad.action_suitable_slots)
        if slots:
            ad.action_slot = next((s for s in slots if s.name_display == ob.name), slots[0])
    if act and set_range:
        apply_range(context.scene, act)


def cycle_action(context, ob, step):
    acts = compatible_actions(ob)
    if not acts:
        return None
    cur = ob.animation_data.action if ob.animation_data else None
    i = acts.index(cur) + step if cur in acts else (0 if step > 0 else -1)
    act = acts[i % len(acts)]
    st = context.scene.action_manager
    assign_action(context, ob, act, st.set_range, st.reset_pose)
    return act


def push_to_nla(ob, stash=False):
    """Native action.push_down / action.stash, usable outside the Action Editor."""
    ad = ob.animation_data
    act = ad.action
    tr = ad.nla_tracks.new()
    tr.strips.new(act.name, int(act.frame_range[0]), act)
    if stash:  # keep it active, park a muted copy so it's never lost
        tr.name, tr.mute = "[Action Stash]", True
    else:
        tr.name = act.name
        ad.action = None


def protect_all():
    n = 0
    for a in bpy.data.actions:
        if not a.library and not a.use_fake_user:
            a.use_fake_user = True
            n += 1
    return n


def remove_unused():
    dead = [a for a in bpy.data.actions if a.users == 0 and not a.use_fake_user]
    for a in dead:
        bpy.data.actions.remove(a)
    return len(dead)


def batch_rename(actions, prefix="", suffix="", find="", replace=""):
    for a in actions:
        name = a.name.replace(find, replace) if find else a.name
        a.name = prefix + name + suffix


@persistent
def _on_save(*_args):
    if bpy.context.preferences.addons[__package__].preferences.auto_protect:
        protect_all()


# ---------------------------------------------------------------- data

class ACTION_MANAGER_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    auto_protect: BoolProperty(
        name="Auto-protect Actions", default=True,
        description="Turn on Fake User for every action on save, so unassigned actions are never lost")

    def draw(self, context):
        self.layout.prop(self, "auto_protect")


def _on_index(self, context):
    ob = context.active_object
    if ob and 0 <= self.index < len(bpy.data.actions):
        try:
            assign_action(context, ob, bpy.data.actions[self.index], self.set_range, self.reset_pose)
        except RuntimeError:
            pass


class ACTION_MANAGER_Settings(bpy.types.PropertyGroup):
    index: IntProperty(update=_on_index, description="Click an action to assign it")
    show_all: BoolProperty(name="All Actions", description="List every action, not only compatible ones")
    set_range: BoolProperty(name="Set Scene Range", default=True,
                            description="Set the scene frame range to the action's range on switch")
    reset_pose: BoolProperty(name="Reset Pose", default=True,
                             description="Reset the armature to rest pose before switching, "
                                         "so channels not keyed in the new action don't keep old values")


# ---------------------------------------------------------------- operators

def _action(name, context):
    if name:
        return bpy.data.actions.get(name)
    ad = context.active_object.animation_data if context.active_object else None
    return ad.action if ad else None


class _ObOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None


class ACTION_MANAGER_OT_assign(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.assign"
    bl_label = "Assign Action"
    bl_description = "Assign this action (and a matching slot) to the active object"
    action: StringProperty()

    def execute(self, context):
        st = context.scene.action_manager
        try:
            assign_action(context, context.active_object, bpy.data.actions.get(self.action),
                          st.set_range, st.reset_pose)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        return {'FINISHED'}


class ACTION_MANAGER_OT_play(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.play"
    bl_label = "Play Action"
    bl_description = "Assign this action, set the scene range to it and start playback"
    action: StringProperty()

    def execute(self, context):
        act = _action(self.action, context)
        if not act:
            return {'CANCELLED'}
        assign_action(context, context.active_object, act, True,
                      context.scene.action_manager.reset_pose)
        context.scene.frame_current = context.scene.frame_start
        if context.screen and not context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class ACTION_MANAGER_OT_cycle(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.cycle"
    bl_label = "Cycle Action"
    bl_description = "Switch to the previous/next compatible action"
    step: IntProperty(default=1)

    def execute(self, context):
        act = cycle_action(context, context.active_object, self.step)
        if act:
            self.report({'INFO'}, act.name)
        return {'FINISHED'} if act else {'CANCELLED'}


class ACTION_MANAGER_OT_new(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.new"
    bl_label = "New Action"
    bl_description = "Create a new empty action (or duplicate the current one) and assign it"
    duplicate: BoolProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        ob = context.active_object
        cur = _action("", context)
        act = cur.copy() if self.duplicate and cur else bpy.data.actions.new(ob.name + "Action")
        act.use_fake_user = True
        assign_action(context, ob, act, set_range=False)
        return {'FINISHED'}


class ACTION_MANAGER_OT_delete(bpy.types.Operator):
    bl_idname = "action_manager.delete"
    bl_label = "Delete Action"
    bl_description = "Permanently delete the action selected in the list"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return 0 <= context.scene.action_manager.index < len(bpy.data.actions)

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        bpy.data.actions.remove(bpy.data.actions[context.scene.action_manager.index])
        return {'FINISHED'}


class ACTION_MANAGER_OT_protect_all(bpy.types.Operator):
    bl_idname = "action_manager.protect_all"
    bl_label = "Fake User for All"
    bl_description = "Turn on Fake User for every action so none is lost on save"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"Protected {protect_all()} action(s)")
        return {'FINISHED'}


class ACTION_MANAGER_OT_remove_unused(bpy.types.Operator):
    bl_idname = "action_manager.remove_unused"
    bl_label = "Remove Unused"
    bl_description = "Delete actions with 0 users and no Fake User"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        self.report({'INFO'}, f"Removed {remove_unused()} action(s)")
        return {'FINISHED'}


class ACTION_MANAGER_OT_batch_rename(bpy.types.Operator):
    bl_idname = "action_manager.batch_rename"
    bl_label = "Batch Rename Actions"
    bl_description = "Add a prefix/suffix and find/replace text in action names"
    bl_options = {'REGISTER', 'UNDO'}
    prefix: StringProperty(name="Prefix")
    suffix: StringProperty(name="Suffix")
    find: StringProperty(name="Find")
    replace: StringProperty(name="Replace")
    scope: EnumProperty(name="Actions", items=[
        ('COMPATIBLE', "Compatible", "Actions usable by the active object"),
        ('ALL', "All", "Every action in the file")])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        ob = context.active_object
        acts = compatible_actions(ob) if self.scope == 'COMPATIBLE' and ob else list(bpy.data.actions)
        batch_rename(acts, self.prefix, self.suffix, self.find, self.replace)
        return {'FINISHED'}


class ACTION_MANAGER_OT_push_nla(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.push_nla"
    bl_label = "Push Down"
    bl_description = "Push the current action onto a new NLA track (stash: muted track, keep it active)"
    stash: BoolProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _action("", context) is not None

    def execute(self, context):
        push_to_nla(context.active_object, self.stash)
        return {'FINISHED'}


class ACTION_MANAGER_OT_range_from_keys(_ObOp, bpy.types.Operator):
    bl_idname = "action_manager.range_from_keys"
    bl_label = "Range from Keys"
    bl_description = "Set the action's manual frame range to its first/last keyframe"
    action: StringProperty()

    def execute(self, context):
        act = _action(self.action, context)
        if not act:
            return {'CANCELLED'}
        act.use_frame_range = True
        act.frame_start, act.frame_end = act.curve_frame_range
        if context.scene.action_manager.set_range:
            apply_range(context.scene, act)
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class ACTION_MANAGER_UL_actions(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index=0):
        ob = context.active_object
        cur = ob is not None and ob.animation_data is not None and ob.animation_data.action == item
        row = layout.row(align=True)
        row.operator("action_manager.assign", text="", emboss=False,
                     icon='RADIOBUT_ON' if cur else 'RADIOBUT_OFF').action = item.name
        row.prop(item, "name", text="", emboss=False)
        s, e = item.frame_range
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.label(text=f"{s:g}-{e:g}  {item.users}u {len(item.slots)}s")
        row.prop(item, "use_fake_user", text="", emboss=False)

    def filter_items(self, context, data, prop):
        acts = getattr(data, prop)
        flags = bpy.types.UI_UL_list.filter_items_by_name(
            self.filter_name, self.bitflag_filter_item, acts) if self.filter_name \
            else [self.bitflag_filter_item] * len(acts)
        ob = context.active_object
        if ob and not context.scene.action_manager.show_all:
            flags = [f if is_compatible(ob, a) else 0 for f, a in zip(flags, acts)]
        return flags, []


class _Panel:
    bl_label = "Action Manager"
    bl_region_type = 'UI'

    def draw(self, context):
        layout = self.layout
        st = context.scene.action_manager
        ob = context.active_object
        row = layout.row(align=True)
        row.prop(st, "show_all", toggle=True)
        row.prop(st, "set_range", toggle=True, text="Range")
        row.prop(st, "reset_pose", toggle=True, text="Rest")

        row = layout.row()
        row.template_list("ACTION_MANAGER_UL_actions", "", bpy.data, "actions", st, "index", rows=6)
        col = row.column(align=True)
        col.operator("action_manager.new", text="", icon='ADD')
        col.operator("action_manager.new", text="", icon='DUPLICATE').duplicate = True
        col.operator("action_manager.delete", text="", icon='REMOVE')
        col.separator()
        col.operator("action_manager.cycle", text="", icon='TRIA_UP').step = -1
        col.operator("action_manager.cycle", text="", icon='TRIA_DOWN').step = 1

        if ob:
            box = layout.box()
            draw_action_and_slot_selector_for_id(box, ob)
            act = _action("", context)
            if act:
                row = box.row(align=True)
                row.operator("action_manager.play", icon='PLAY')
                row.operator("action_manager.push_nla", icon='NLA_PUSHDOWN')
                row.operator("action_manager.push_nla", text="Stash", icon='NLA').stash = True
                row = box.row(align=True)
                row.prop(act, "use_frame_range", text="Manual Range")
                row.prop(act, "use_cyclic")
                row = box.row(align=True)
                row.active = act.use_frame_range
                row.prop(act, "frame_start", text="Start")
                row.prop(act, "frame_end", text="End")
                row.operator("action_manager.range_from_keys", text="", icon='KEYFRAME')

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("action_manager.protect_all", icon='FAKE_USER_ON')
        row.operator("action_manager.remove_unused", icon='TRASH')
        col.operator("action_manager.batch_rename", icon='SORTALPHA')
        prefs = context.preferences.addons[__package__].preferences
        col.prop(prefs, "auto_protect")


class ACTION_MANAGER_PT_view3d(_Panel, bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_category = "Animation"


class ACTION_MANAGER_PT_dopesheet(_Panel, bpy.types.Panel):
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_category = "Action"


classes = (
    ACTION_MANAGER_Prefs, ACTION_MANAGER_Settings,
    ACTION_MANAGER_OT_assign, ACTION_MANAGER_OT_play, ACTION_MANAGER_OT_cycle,
    ACTION_MANAGER_OT_new, ACTION_MANAGER_OT_delete, ACTION_MANAGER_OT_protect_all,
    ACTION_MANAGER_OT_remove_unused, ACTION_MANAGER_OT_batch_rename,
    ACTION_MANAGER_OT_push_nla, ACTION_MANAGER_OT_range_from_keys,
    ACTION_MANAGER_UL_actions, ACTION_MANAGER_PT_view3d, ACTION_MANAGER_PT_dopesheet,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.action_manager = PointerProperty(type=ACTION_MANAGER_Settings)
    bpy.app.handlers.save_pre.append(_on_save)


def unregister():
    if _on_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save)
    del bpy.types.Scene.action_manager
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
