"""Tension Map: per-vertex stretch/compression of a deformed mesh, for wrinkle maps and
cloth/skin shading.

Pure Geometry Nodes, so it is realtime and render-safe (no Python handlers): the object's
"Rest Position" option stores the undeformed positions as the `rest_position` attribute
before shape keys and modifiers; a node group modifier at the end of the stack compares
each edge's current length to its rest length and averages that ratio onto the vertices.
"""
import bpy
import numpy as np

GROUP = "Tension Map"
MOD = "Tension Map"
MAT = "Tension Preview"
# (socket name, type, default, extra settings)
INPUTS = (
    ("Strength", 'NodeSocketFloat', 1.0, {"min_value": 0.0}),
    ("Blur", 'NodeSocketInt', 1, {"min_value": 0, "max_value": 100}),
    ("Tension Name", 'NodeSocketString', "tension", {}),
    ("Stretch Name", 'NodeSocketString', "stretch", {}),
    ("Compress Name", 'NodeSocketString', "compress", {}),
    ("Color Name", 'NodeSocketString', "tension_color", {}),
)


def _math(tree, op, a, b=None, clamp=False):
    n = tree.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    for sock, v in zip(n.inputs, (a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            sock.default_value = v
        else:
            tree.links.new(v, sock)
    return n.outputs[0]


def _store(tree, geo, name, value, data_type='FLOAT'):
    n = tree.nodes.new("GeometryNodeStoreNamedAttribute")
    n.data_type, n.domain = data_type, 'POINT'
    tree.links.new(geo, n.inputs["Geometry"])
    tree.links.new(name, n.inputs["Name"])
    tree.links.new(value, n.inputs["Value"])
    return n.outputs[0]


def build_group():
    """(Re)build the shared node group."""
    tree = bpy.data.node_groups.get(GROUP)
    if tree is None or tree.bl_idname != 'GeometryNodeTree':
        tree = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    tree.nodes.clear()
    tree.interface.clear()
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    for name, typ, default, extra in INPUTS:
        s = tree.interface.new_socket(name, in_out='INPUT', socket_type=typ)
        s.default_value = default
        for k, v in extra.items():
            setattr(s, k, v)
    if hasattr(tree, "is_modifier"):
        tree.is_modifier = True
    gi, go = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    gi.location, go.location = (-900, 0), (900, 0)
    nl = tree.nodes.new
    L = tree.links.new

    # Edge strain = current length / rest length - 1, evaluated on edges.
    ev = nl("GeometryNodeInputMeshEdgeVertices")
    rest = nl("GeometryNodeInputNamedAttribute")
    rest.data_type = 'FLOAT_VECTOR'
    rest.inputs["Name"].default_value = "rest_position"
    ends = []
    for idx in ("Vertex Index 1", "Vertex Index 2"):
        at = nl("GeometryNodeFieldAtIndex")
        at.domain, at.data_type = 'POINT', 'FLOAT_VECTOR'
        L(rest.outputs["Attribute"], at.inputs["Value"])
        L(ev.outputs[idx], at.inputs["Index"])
        ends.append(at.outputs[0])
    lens = []
    for a, b in ((ends[0], ends[1]), (ev.outputs["Position 1"], ev.outputs["Position 2"])):
        d = nl("ShaderNodeVectorMath")
        d.operation = 'DISTANCE'
        L(a, d.inputs[0])
        L(b, d.inputs[1])
        lens.append(d.outputs["Value"])
    strain = _math(tree, 'SUBTRACT', _math(tree, 'DIVIDE', lens[1], lens[0]), 1.0)

    # Edge -> point (average of connected edges), optional blur, scale by strength.
    on = nl("GeometryNodeFieldOnDomain")
    on.domain, on.data_type = 'EDGE', 'FLOAT'
    L(strain, on.inputs[0])
    blur = nl("GeometryNodeBlurAttribute")
    blur.data_type = 'FLOAT'
    L(on.outputs[0], blur.inputs["Value"])
    L(gi.outputs["Blur"], blur.inputs["Iterations"])
    t = _math(tree, 'MULTIPLY', blur.outputs[0], gi.outputs["Strength"])
    geo = _store(tree, gi.outputs["Geometry"], gi.outputs["Tension Name"], t)

    # Derived maps read the stored tension back so the edge pass runs once.
    tn = nl("GeometryNodeInputNamedAttribute")
    L(gi.outputs["Tension Name"], tn.inputs["Name"])
    t = tn.outputs["Attribute"]
    stretch = _math(tree, 'MAXIMUM', t, 0.0)
    compress = _math(tree, 'MAXIMUM', _math(tree, 'MULTIPLY', t, -1.0), 0.0)
    geo = _store(tree, geo, gi.outputs["Stretch Name"], stretch)
    geo = _store(tree, geo, gi.outputs["Compress Name"], compress)
    # White at rest, red when stretched, blue when compressed.
    col = nl("FunctionNodeCombineColor")
    L(_math(tree, 'SUBTRACT', 1.0, compress, clamp=True), col.inputs["Red"])
    L(_math(tree, 'SUBTRACT', 1.0, _math(tree, 'ABSOLUTE', t), clamp=True), col.inputs["Green"])
    L(_math(tree, 'SUBTRACT', 1.0, stretch, clamp=True), col.inputs["Blue"])
    geo = _store(tree, geo, gi.outputs["Color Name"], col.outputs[0], 'FLOAT_COLOR')
    L(geo, go.inputs["Geometry"])
    return tree


def get_group():
    tree = bpy.data.node_groups.get(GROUP)
    if tree is None or tree.bl_idname != 'GeometryNodeTree':
        tree = build_group()
    return tree


def find_mod(ob):
    return next((m for m in ob.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name.startswith(GROUP)), None)


def mod_input(md, name):
    """Value of a group input on the modifier (5.2 API, IDProperty fallback for older)."""
    item = next(i for i in md.node_group.interface.items_tree
                if getattr(i, "in_out", None) == 'INPUT' and i.name == name)
    props = getattr(md, "properties", None)
    if props is not None:
        return getattr(props.inputs, item.identifier).value
    return md[item.identifier]


def _meshes(context):
    return [o for o in context.selected_objects if o.type == 'MESH']


def add_tension(ob):
    md = find_mod(ob)
    if md is None:
        md = ob.modifiers.new(MOD, 'NODES')
        md.node_group = get_group()
    else:  # keep it last so it sees the final deformation
        ob.modifiers.move(list(ob.modifiers).index(md), len(ob.modifiers) - 1)
    ob.add_rest_position_attribute = True
    return md


def preview_material(name):
    mat = bpy.data.materials.get(MAT) or bpy.data.materials.new(MAT)
    tree = mat.node_tree
    tree.nodes.clear()
    attr = tree.nodes.new("ShaderNodeAttribute")
    attr.attribute_type, attr.attribute_name = 'GEOMETRY', name
    # tension -1..1 -> ramp 0..1
    remap = tree.nodes.new("ShaderNodeMath")
    remap.operation = 'MULTIPLY_ADD'
    remap.use_clamp = True
    remap.inputs[1].default_value = remap.inputs[2].default_value = 0.5
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].color, els[1].color = (0.0, 0.1, 1.0, 1.0), (1.0, 0.05, 0.0, 1.0)
    els.new(0.5).color = (1.0, 1.0, 1.0, 1.0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    for i, n in enumerate((attr, remap, ramp, bsdf, out)):
        n.location = (i * 300 - 900, 0)
    tree.links.new(attr.outputs["Fac"], remap.inputs[0])
    tree.links.new(remap.outputs[0], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    tree.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def apply_preview(ob, mat):
    """Show mat via object-linked slots, so the mesh's own materials stay untouched."""
    if not ob.material_slots:
        ob.data.materials.append(None)
    for slot in ob.material_slots:
        slot.link = 'OBJECT'
        slot.material = mat


def clear_preview(ob):
    for slot in ob.material_slots:
        if slot.link == 'OBJECT' and slot.material and slot.material.name.startswith(MAT):
            slot.material = None
            slot.link = 'DATA'


def bake_groups(ob, depsgraph, stretch_group, compress_group):
    """Write the evaluated stretch/compress maps to vertex groups. Returns error text or None."""
    md = find_mod(ob)
    if md is None:
        return f"{ob.name}: no Tension Map modifier"
    me = ob.evaluated_get(depsgraph).data
    if len(me.vertices) != len(ob.data.vertices):
        return f"{ob.name}: modifiers change the vertex count, cannot bake to vertex groups"
    for attr_input, group in (("Stretch Name", stretch_group), ("Compress Name", compress_group)):
        attr = me.attributes.get(mod_input(md, attr_input))
        if attr is None:
            return f"{ob.name}: attribute missing from evaluated mesh"
        w = np.empty(len(me.vertices), np.float32)
        attr.data.foreach_get("value", w)
        w = np.clip(w, 0.0, 1.0)
        vg = ob.vertex_groups.get(group)
        if vg:
            ob.vertex_groups.remove(vg)
        vg = ob.vertex_groups.new(name=group)
        # ponytail: one add() per weighted vertex; fine up to ~100k verts
        for i in np.flatnonzero(w).tolist():
            vg.add([i], float(w[i]), 'REPLACE')
    return None


class TM_OT_add(bpy.types.Operator):
    bl_idname = "tension_map.add"
    bl_label = "Add Tension Map"
    bl_description = ("Add a Geometry Nodes modifier (last in the stack) that writes tension, stretch, "
                      "compress and color attributes, and enable Rest Position on selected meshes")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_meshes(context))

    def execute(self, context):
        for ob in _meshes(context):
            add_tension(ob)
        return {'FINISHED'}


class TM_OT_material(bpy.types.Operator):
    bl_idname = "tension_map.add_material"
    bl_label = "Add Preview Material"
    bl_description = ("Show a blue/white/red tension material on selected meshes (object-linked "
                      "material slots, the mesh materials are kept)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_meshes(context))

    def execute(self, context):
        obs = _meshes(context)
        md = next((m for m in map(find_mod, obs) if m), None)
        mat = preview_material(mod_input(md, "Tension Name") if md else "tension")
        for ob in obs:
            apply_preview(ob, mat)
        return {'FINISHED'}


class TM_OT_bake(bpy.types.Operator):
    bl_idname = "tension_map.bake"
    bl_label = "Bake to Vertex Groups"
    bl_description = "Store the current frame's stretch and compress maps as vertex groups (weights 0-1)"
    bl_options = {'REGISTER', 'UNDO'}

    stretch_group: bpy.props.StringProperty(name="Stretch Group", default="tension_stretch")
    compress_group: bpy.props.StringProperty(name="Compress Group", default="tension_compress")

    @classmethod
    def poll(cls, context):
        return any(find_mod(o) for o in _meshes(context))

    def execute(self, context):
        dg = context.evaluated_depsgraph_get()
        errors = [e for ob in _meshes(context) if find_mod(ob)
                  for e in [bake_groups(ob, dg, self.stretch_group, self.compress_group)] if e]
        for e in errors:
            self.report({'WARNING'}, e)
        return {'FINISHED'}


class TM_OT_remove(bpy.types.Operator):
    bl_idname = "tension_map.remove"
    bl_label = "Remove Tension Map"
    bl_description = "Remove the Tension Map modifier and preview material, and disable Rest Position"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_meshes(context))

    def execute(self, context):
        for ob in _meshes(context):
            md = find_mod(ob)
            if md:
                ob.modifiers.remove(md)
                ob.add_rest_position_attribute = False
            clear_preview(ob)
        return {'FINISHED'}


class TM_PT_panel(bpy.types.Panel):
    bl_label = "Tension Map"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Rig"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("tension_map.add", icon='MOD_NOISE')
        col.operator("tension_map.add_material", icon='MATERIAL')
        col.operator("tension_map.bake", icon='GROUP_VERTEX')
        col.operator("tension_map.remove", icon='X')
        ob = context.object
        md = find_mod(ob) if ob and ob.type == 'MESH' else None
        if md and getattr(md, "properties", None) is not None:
            box = self.layout.column()
            for item in md.node_group.interface.items_tree:
                if getattr(item, "in_out", None) == 'INPUT' and item.socket_type != 'NodeSocketGeometry':
                    box.prop(getattr(md.properties.inputs, item.identifier), "value", text=item.name)


classes = (TM_OT_add, TM_OT_material, TM_OT_bake, TM_OT_remove, TM_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
