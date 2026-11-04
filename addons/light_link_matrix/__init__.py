"""Light Link Matrix: every light against every object; click a cell to cycle Default/Include/Exclude.

Uses Blender's native light linking (receiver / blocker collections), so renders are unaffected
by the add-on itself.
"""
import bpy

STATES = ('DEFAULT', 'INCLUDE', 'EXCLUDE')
ICONS = {'DEFAULT': 'DOT', 'INCLUDE': 'CHECKMARK', 'EXCLUDE': 'X'}


def _coll(light, kind, create):
    ll = light.light_linking
    attr = "receiver_collection" if kind == 'LIGHT' else "blocker_collection"
    c = getattr(ll, attr)
    if c is None and create:
        c = bpy.data.collections.new(f"{light.name}_{'receivers' if kind == 'LIGHT' else 'blockers'}")
        setattr(ll, attr, c)
    return c


def get_state(light, ob, kind='LIGHT'):
    c = _coll(light, kind, False)
    if c is None or ob.name not in c.objects:
        return 'DEFAULT'
    idx = list(c.objects).index(ob)
    return c.collection_objects[idx].light_linking.link_state


def set_state(light, ob, state, kind='LIGHT'):
    c = _coll(light, kind, state != 'DEFAULT')
    if c is None:
        return
    if state == 'DEFAULT':
        if ob.name in c.objects:
            c.objects.unlink(ob)
        return
    if ob.name not in c.objects:
        c.objects.link(ob)
    idx = list(c.objects).index(ob)
    c.collection_objects[idx].light_linking.link_state = state


def lights(scene):
    return sorted((o for o in scene.objects if o.type == 'LIGHT'), key=lambda o: o.name.lower())


def receivers(context, only_selected):
    obs = context.selected_objects if only_selected else context.scene.objects
    return sorted((o for o in obs if o.type in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META', 'VOLUME', 'CURVES'}),
                  key=lambda o: o.name.lower())


class LLM_OT_cycle(bpy.types.Operator):
    bl_idname = "light_link_matrix.cycle"
    bl_label = "Cycle Link State"
    bl_description = "Default → Include (only included objects get this light) → Exclude"
    bl_options = {'REGISTER', 'UNDO'}

    light: bpy.props.StringProperty(options={'SKIP_SAVE'})
    obj: bpy.props.StringProperty(options={'SKIP_SAVE'})
    kind: bpy.props.EnumProperty(items=[('LIGHT', "Light", ""), ('SHADOW', "Shadow", "")], options={'SKIP_SAVE'})

    def execute(self, context):
        lt, ob = bpy.data.objects.get(self.light), bpy.data.objects.get(self.obj)
        if not lt or not ob:
            return {'CANCELLED'}
        cur = get_state(lt, ob, self.kind)
        set_state(lt, ob, STATES[(STATES.index(cur) + 1) % 3], self.kind)
        return {'FINISHED'}


class LLM_OT_clear(bpy.types.Operator):
    bl_idname = "light_link_matrix.clear"
    bl_label = "Clear Linking"
    bl_description = "Remove light and shadow linking from the selected lights (or all lights)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        lts = [o for o in context.selected_objects if o.type == 'LIGHT'] or lights(context.scene)
        for lt in lts:
            for attr in ("receiver_collection", "blocker_collection"):
                c = getattr(lt.light_linking, attr)
                setattr(lt.light_linking, attr, None)
                if c and c.users == 0:
                    bpy.data.collections.remove(c)
        return {'FINISHED'}


class LLM_PT_panel(bpy.types.Panel):
    bl_label = "Light Link Matrix"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        sc = context.scene
        lay = self.layout
        row = lay.row(align=True)
        row.prop(sc, "light_link_matrix_kind", expand=True)
        row = lay.row(align=True)
        row.prop(sc, "light_link_matrix_selected")
        row.operator("light_link_matrix.clear", text="", icon='TRASH')
        lts = lights(sc)
        obs = receivers(context, sc.light_link_matrix_selected)[:40]
        if not lts or not obs:
            lay.label(text="Need lights and objects", icon='INFO')
            return
        grid = lay.grid_flow(row_major=True, columns=len(lts) + 1, even_columns=False, align=True)
        grid.label(text="")
        for lt in lts:
            grid.label(text=lt.name[:10], icon='LIGHT')
        kind = sc.light_link_matrix_kind
        for ob in obs:
            grid.label(text=ob.name[:18])
            for lt in lts:
                st = get_state(lt, ob, kind)
                op = grid.operator("light_link_matrix.cycle", text="", icon=ICONS[st], depress=st != 'DEFAULT')
                op.light, op.obj, op.kind = lt.name, ob.name, kind


_classes = (LLM_OT_cycle, LLM_OT_clear, LLM_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.light_link_matrix_kind = bpy.props.EnumProperty(
        name="Linking", items=[('LIGHT', "Light", "Which objects receive the light"),
                               ('SHADOW', "Shadow", "Which objects cast shadows from the light")])
    S.light_link_matrix_selected = bpy.props.BoolProperty(name="Selected Objects Only")


def unregister():
    del bpy.types.Scene.light_link_matrix_selected
    del bpy.types.Scene.light_link_matrix_kind
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
