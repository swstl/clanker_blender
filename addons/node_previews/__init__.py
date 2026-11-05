"""Node Previews: turn on Blender's built-in node preview (drawn above the node) for every
shader node at once, and automatically for nodes you add later."""
import bpy

SEEN = "node_previews_seen"  # custom prop: node already handled, keep the user's own on/off choice


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def set_previews(tree, state):
    """Set the preview flag on every node of a shader tree. Returns how many nodes changed."""
    n = 0
    for node in tree.nodes:
        node[SEEN] = 1
        if node.show_preview != state:
            node.show_preview = state
            n += 1
    return n


def preview_new_nodes(tree):
    """Turn on previews only for nodes this add-on has not seen yet."""
    for node in tree.nodes:
        if SEEN not in node:
            node[SEEN] = 1
            node.show_preview = True


def _tick():
    # ponytail: polls the open shader editors (a depsgraph handler misses unassigned materials);
    # cheap since only visible edit trees are scanned.
    if _prefs().auto:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                sp = area.spaces.active
                if area.type == 'NODE_EDITOR' and sp.tree_type == 'ShaderNodeTree' and sp.edit_tree:
                    preview_new_nodes(sp.edit_tree)
    return 0.5


class NP_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    auto: bpy.props.BoolProperty(
        name="Auto-preview new nodes", default=True,
        description="Turn on the preview of every shader node you add")

    def draw(self, context):
        self.layout.prop(self, "auto")


class NP_OT_set_all(bpy.types.Operator):
    bl_idname = "node_previews.set_all"
    bl_label = "Preview All Nodes"
    bl_description = "Show or hide the preview above every node in this shader tree"
    bl_options = {'REGISTER', 'UNDO'}

    state: bpy.props.BoolProperty(name="Show", default=True, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return sp and sp.type == 'NODE_EDITOR' and sp.tree_type == 'ShaderNodeTree' and sp.edit_tree

    def execute(self, context):
        sp = context.space_data
        n = set_previews(sp.edit_tree, self.state)
        if self.state:
            sp.overlay.show_overlays = sp.overlay.show_previews = True
        self.report({'INFO'}, f"{'Showing' if self.state else 'Hid'} {n} node previews")
        return {'FINISHED'}


def _header(self, context):
    if not NP_OT_set_all.poll(context):
        return
    row = self.layout.row(align=True)
    row.operator(NP_OT_set_all.bl_idname, text="", icon='HIDE_OFF').state = True
    row.operator(NP_OT_set_all.bl_idname, text="", icon='HIDE_ON').state = False
    row.prop(_prefs(), "auto", text="", icon='AUTO')


CLASSES = (NP_Prefs, NP_OT_set_all)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.NODE_HT_header.append(_header)
    if not bpy.app.background:
        bpy.app.timers.register(_tick, first_interval=1, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.types.NODE_HT_header.remove(_header)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
