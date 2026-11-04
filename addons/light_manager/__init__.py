"""Light Manager: one panel for all lights and emissive meshes (solo, Kelvin, aim, exposure, HDRI)."""
import json

import bpy
from mathutils import Matrix, Vector

KELVIN_PRESETS = (("Candle", 1900), ("Tungsten", 2700), ("Halogen", 3200),
                  ("Daylight", 5600), ("Overcast", 6500), ("Blue Sky", 10000))
LIGHT_ICONS = {'POINT': 'LIGHT_POINT', 'SUN': 'LIGHT_SUN', 'SPOT': 'LIGHT_SPOT', 'AREA': 'LIGHT_AREA'}


def emission_sockets(ob):
    """(color, strength) socket pairs of emissive Principled/Emission nodes on ob's materials."""
    out = []
    if ob.type != 'MESH':
        return out
    for slot in ob.material_slots:
        mat = slot.material
        if not (mat and mat.node_tree):
            continue
        for n in mat.node_tree.nodes:
            if n.bl_idname == 'ShaderNodeBsdfPrincipled':
                c, s = n.inputs["Emission Color"], n.inputs["Emission Strength"]
            elif n.bl_idname == 'ShaderNodeEmission':
                c, s = n.inputs["Color"], n.inputs["Strength"]
            else:
                continue
            if s.default_value > 0:
                out.append((c, s))
    return out


def list_lights(scene, emissive=None):
    """Lights (and optionally emissive meshes) of the scene, sorted by name."""
    if emissive is None:
        emissive = scene.lm_show_emissive
    obs = [o for o in scene.objects if o.type == 'LIGHT' or (emissive and emission_sockets(o))]
    return sorted(obs, key=lambda o: o.name.lower())


def _selected_lights(context):
    return [o for o in context.selected_objects if o.type == 'LIGHT']


def _target(context, mode, exclude=None):
    if mode == 'CURSOR':
        return context.scene.cursor.location.copy()
    ob = context.active_object
    return ob.matrix_world.translation.copy() if ob and ob != exclude else None


def aim_at(ob, target):
    """Rotate ob so its local -Z points at target, keeping location and scale."""
    loc, _rot, scale = ob.matrix_world.decompose()
    d = Vector(target) - loc
    if d.length < 1e-9:
        return
    ob.matrix_world = Matrix.LocRotScale(loc, d.to_track_quat('-Z', 'Y'), scale)


# --- solo -------------------------------------------------------------------

def solo(scene, name):
    """Hide every other listed light; calling again (any name) restores the stored state."""
    state = json.loads(scene.lm_solo_state or "{}")
    if state:
        for n, (hv, hr) in state.get("prev", {}).items():
            ob = scene.objects.get(n)
            if ob:
                ob.hide_viewport, ob.hide_render = hv, hr
        scene.lm_solo_state = ""
        if state.get("solo") == name:
            return
    obs = list_lights(scene)
    prev = {o.name: (o.hide_viewport, o.hide_render) for o in obs}
    for o in obs:
        hidden = o.name != name
        o.hide_viewport = o.hide_render = hidden
    scene.lm_solo_state = json.dumps({"solo": name, "prev": prev})


class LM_OT_solo(bpy.types.Operator):
    bl_idname = "light_manager.solo"
    bl_label = "Solo Light"
    bl_description = "Hide all other lights in viewport and render; click again to restore the previous visibility"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        solo(context.scene, self.name)
        return {'FINISHED'}


class LM_OT_select(bpy.types.Operator):
    bl_idname = "light_manager.select"
    bl_label = "Select Light"
    bl_description = "Select this light (Shift: add to selection)"

    name: bpy.props.StringProperty()
    extend: bpy.props.BoolProperty()

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        ob = context.scene.objects.get(self.name)
        if not ob:
            return {'CANCELLED'}
        if not self.extend:
            for o in context.selected_objects:
                o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


class LM_OT_temperature(bpy.types.Operator):
    bl_idname = "light_manager.temperature"
    bl_label = "Set Color Temperature"
    bl_description = "Set the selected lights' color from a blackbody temperature (Kelvin)"
    bl_options = {'REGISTER', 'UNDO'}

    kelvin: bpy.props.FloatProperty(name="Kelvin", default=6500, min=1000, max=12000)

    def execute(self, context):
        lights = _selected_lights(context)
        if not lights:
            self.report({'WARNING'}, "No lights selected")
            return {'CANCELLED'}
        for o in lights:
            # Native blackbody (Blender 4.5+), shared by EEVEE and Cycles.
            o.data.use_temperature = True
            o.data.temperature = self.kelvin
        return {'FINISHED'}


class LM_OT_scale_energy(bpy.types.Operator):
    bl_idname = "light_manager.scale_energy"
    bl_label = "Scale Energy"
    bl_description = "Multiply the power of the selected lights (all lights if none are selected)"
    bl_options = {'REGISTER', 'UNDO'}

    factor: bpy.props.FloatProperty(name="Factor", default=2.0, min=0.0)

    def execute(self, context):
        lights = _selected_lights(context) or [o for o in context.scene.objects if o.type == 'LIGHT']
        for data in {o.data for o in lights}:  # shared light data scales once
            data.energy *= self.factor
        return {'FINISHED'}


_TARGETS = [('CURSOR', "3D Cursor", ""), ('ACTIVE', "Active Object", "")]


class LM_OT_aim(bpy.types.Operator):
    bl_idname = "light_manager.aim"
    bl_label = "Aim Lights"
    bl_description = "Rotate the selected lights so they point at the target (location is kept)"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(items=_TARGETS)

    def execute(self, context):
        act = context.active_object if self.target == 'ACTIVE' else None
        lights = [o for o in _selected_lights(context) if o != act]
        tgt = _target(context, self.target)
        if not lights or tgt is None:
            self.report({'WARNING'}, "Select lights (and an active target object)")
            return {'CANCELLED'}
        for o in lights:
            aim_at(o, tgt)
        return {'FINISHED'}


class LM_OT_place(bpy.types.Operator):
    bl_idname = "light_manager.place"
    bl_label = "Place at Distance"
    bl_description = "Move the selected lights along their current direction so they sit at a distance from the target"
    bl_options = {'REGISTER', 'UNDO'}

    target: bpy.props.EnumProperty(items=_TARGETS)
    distance: bpy.props.FloatProperty(name="Distance", default=5.0, min=0.0, subtype='DISTANCE')

    def execute(self, context):
        act = context.active_object if self.target == 'ACTIVE' else None
        lights = [o for o in _selected_lights(context) if o != act]
        tgt = _target(context, self.target)
        if not lights or tgt is None:
            self.report({'WARNING'}, "Select lights (and an active target object)")
            return {'CANCELLED'}
        for o in lights:
            fwd = (o.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
            mw = o.matrix_world.copy()
            mw.translation = tgt - fwd * self.distance
            o.matrix_world = mw
        return {'FINISHED'}


class LM_OT_track(bpy.types.Operator):
    bl_idname = "light_manager.track"
    bl_label = "Add Track To"
    bl_description = "Add a Track To constraint on the selected lights targeting the active object"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        act = context.active_object
        lights = [o for o in _selected_lights(context) if o != act]
        if not act or not lights:
            self.report({'WARNING'}, "Select lights and make the target object active")
            return {'CANCELLED'}
        for o in lights:
            c = o.constraints.new('TRACK_TO')
            c.target = act
            c.track_axis, c.up_axis = 'TRACK_NEGATIVE_Z', 'UP_Y'
        return {'FINISHED'}


# --- world / HDRI -----------------------------------------------------------

def world_nodes(world):
    """(background node, environment texture node, mapping node or None) of a world."""
    if not (world and world.node_tree):
        return None, None, None
    nodes = world.node_tree.nodes
    bg = next((n for n in nodes if n.bl_idname == 'ShaderNodeBackground'), None)
    env = next((n for n in nodes if n.bl_idname == 'ShaderNodeTexEnvironment'), None)
    mp = None
    if env and env.inputs["Vector"].is_linked:
        src = env.inputs["Vector"].links[0].from_node
        mp = src if src.bl_idname == 'ShaderNodeMapping' else None
    return bg, env, mp


def ensure_mapping(world):
    """Insert a Mapping node in front of the world's Environment Texture and return it."""
    _bg, env, mp = world_nodes(world)
    if env is None or mp:
        return mp
    tree = world.node_tree
    vec = env.inputs["Vector"]
    if vec.is_linked:
        src = vec.links[0].from_socket
    else:
        tc = tree.nodes.new('ShaderNodeTexCoord')
        tc.location = env.location + Vector((-500, 0))
        src = tc.outputs["Generated"]
    mp = tree.nodes.new('ShaderNodeMapping')
    mp.location = env.location + Vector((-250, 0))
    tree.links.new(src, mp.inputs["Vector"])
    tree.links.new(mp.outputs["Vector"], vec)
    return mp


class LM_OT_hdri_mapping(bpy.types.Operator):
    bl_idname = "light_manager.hdri_mapping"
    bl_label = "Add HDRI Rotation"
    bl_description = "Add a Mapping node to the world's Environment Texture so it can be rotated"
    bl_options = {'REGISTER', 'UNDO'}

    rotation: bpy.props.FloatProperty(name="Rotation", subtype='ANGLE')

    def execute(self, context):
        mp = ensure_mapping(context.scene.world)
        if mp is None:
            self.report({'WARNING'}, "World has no Environment Texture")
            return {'CANCELLED'}
        mp.inputs["Rotation"].default_value[2] = self.rotation
        return {'FINISHED'}


# --- UI ---------------------------------------------------------------------

def _draw_details(box, ob):
    ld = ob.data
    t = ld.type
    col = box.column(align=True)
    if t == 'SUN':
        col.prop(ld, "angle")
    elif t == 'AREA':
        col.prop(ld, "shape")
        if ld.shape in {'RECTANGLE', 'ELLIPSE'}:
            col.prop(ld, "size", text="Size X")
            col.prop(ld, "size_y", text="Size Y")
        else:
            col.prop(ld, "size")
        col.prop(ld, "spread")
    else:
        col.prop(ld, "shadow_soft_size", text="Radius")
        col.prop(ld, "use_soft_falloff")
    if t == 'SPOT':
        col.prop(ld, "spot_size")
        col.prop(ld, "spot_blend")
        col.prop(ld, "show_cone")
    col.prop(ld, "use_shadow")
    row = box.row(align=True)
    row.prop(ld, "use_temperature", text="")
    sub = row.row(align=True)
    sub.active = ld.use_temperature
    sub.prop(ld, "temperature")
    ll = ob.light_linking
    box.template_ID(ll, "receiver_collection", new="object.light_linking_receiver_collection_new")
    box.template_ID(ll, "blocker_collection", new="object.light_linking_blocker_collection_new")


class LM_PT_lights(bpy.types.Panel):
    bl_label = "Lights"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"

    def draw(self, context):
        sc = context.scene
        lay = self.layout
        lay.prop(sc, "lm_show_emissive")
        soloed = json.loads(sc.lm_solo_state or "{}").get("solo")
        col = lay.column(align=True)
        for ob in list_lights(sc):
            active = ob == context.active_object
            box = col.box() if active and ob.type == 'LIGHT' else col
            row = box.row(align=True)
            if ob.type == 'LIGHT':
                row.label(text="", icon=LIGHT_ICONS.get(ob.data.type, 'LIGHT'))
            else:
                row.label(text="", icon='MATERIAL')
            row.operator("light_manager.select", text=ob.name, emboss=active,
                         depress=ob.select_get()).name = ob.name
            row.prop(ob, "hide_viewport", text="", emboss=False)
            row.prop(ob, "hide_render", text="", emboss=False)
            row.operator("light_manager.solo", text="", emboss=False,
                         icon='SOLO_ON' if soloed == ob.name else 'SOLO_OFF').name = ob.name
            sub = row.row(align=True)
            sub.ui_units_x = 5
            if ob.type == 'LIGHT':
                if ob.data.use_temperature:
                    sub.prop(ob.data, "temperature_color", text="")
                else:
                    sub.prop(ob.data, "color", text="")
                sub.prop(ob.data, "energy", text="")
            else:
                c, s = emission_sockets(ob)[0]
                sub.prop(c, "default_value", text="")
                sub.prop(s, "default_value", text="")
            if active and ob.type == 'LIGHT':
                _draw_details(box, ob)


class LM_PT_tools(bpy.types.Panel):
    bl_label = "Light Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"

    def draw(self, context):
        lay = self.layout
        lay.label(text="Temperature (selected)")
        grid = lay.grid_flow(columns=3, align=True)
        for name, k in KELVIN_PRESETS:
            grid.operator("light_manager.temperature", text=f"{name} {k}K").kelvin = k
        row = lay.row(align=True)
        row.label(text="Energy")
        row.operator("light_manager.scale_energy", text="x0.5").factor = 0.5
        row.operator("light_manager.scale_energy", text="x2").factor = 2.0
        lay.label(text="Aim (selected)")
        row = lay.row(align=True)
        row.operator("light_manager.aim", text="At Cursor", icon='PIVOT_CURSOR').target = 'CURSOR'
        row.operator("light_manager.aim", text="At Active", icon='PIVOT_ACTIVE').target = 'ACTIVE'
        row = lay.row(align=True)
        row.operator("light_manager.track", icon='CON_TRACKTO')
        row.operator("light_manager.place", icon='DRIVER_DISTANCE').target = 'CURSOR'


class LM_PT_world(bpy.types.Panel):
    bl_label = "World"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"

    def draw(self, context):
        lay = self.layout
        world = context.scene.world
        bg, env, mp = world_nodes(world)
        if bg is None:
            lay.label(text="World has no Background node")
            return
        lay.prop(bg.inputs["Strength"], "default_value", text="Strength")
        if env is None:
            return
        lay.template_ID(env, "image", open="image.open")
        if mp:
            lay.prop(mp.inputs["Rotation"], "default_value", index=2, text="Rotation")
        else:
            lay.operator("light_manager.hdri_mapping", icon='ORIENTATION_GIMBAL')


_classes = (LM_OT_solo, LM_OT_select, LM_OT_temperature, LM_OT_scale_energy, LM_OT_aim, LM_OT_place,
            LM_OT_track, LM_OT_hdri_mapping, LM_PT_lights, LM_PT_tools, LM_PT_world)


def register():
    S = bpy.types.Scene
    S.lm_show_emissive = bpy.props.BoolProperty(name="Emissive Meshes", default=True,
                                                description="Also list meshes with emissive materials")
    S.lm_solo_state = bpy.props.StringProperty(options={'HIDDEN'})
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.lm_show_emissive
    del bpy.types.Scene.lm_solo_state
