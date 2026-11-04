"""Archviz Walls: blockout walls, floor and ceiling from a floor plan drawn as edges (or a curve).

The walls object is empty except for a Geometry Nodes modifier that reads the plan object live:
plan edges -> curves (closed loops made counter-clockwise) -> Curve to Mesh with a line profile
of wall thickness, scaled 1/cos(half corner angle) for true mitred corners -> extruded to height.
Doors/windows are box cutters in the "Cutters" collection (same as Quick Boolean) with Boolean
modifiers after the node group, so plan, sizes and openings all stay editable.
"""
import math

import bmesh
import bpy
from mathutils import Vector, geometry

GROUP = "Archviz Walls"
MOD = "Archviz Walls"
ALIGN = (("Center", 0.0), ("Inside", -0.5), ("Outside", 0.5))  # profile shift in thicknesses
INPUTS = (  # (name, socket type, default, extra settings)
    ("Plan", 'NodeSocketObject', None, {}),
    ("Height", 'NodeSocketFloat', 2.7, {"min_value": 0.0, "subtype": 'DISTANCE'}),
    ("Thickness", 'NodeSocketFloat', 0.2, {"min_value": 0.001, "subtype": 'DISTANCE'}),
    ("Alignment", 'NodeSocketMenu', None, {}),
    ("Walls", 'NodeSocketBool', True, {}),
    ("Floor", 'NodeSocketBool', False, {}),
    ("Ceiling", 'NodeSocketBool', False, {}),
    ("Slab Thickness", 'NodeSocketFloat', 0.2, {"min_value": 0.001, "subtype": 'DISTANCE'}),
)


def _n(tree, idname, ins=None, **props):
    """New node with properties set, inputs (by name or index) linked or given defaults."""
    n = tree.nodes.new(idname)
    for k, v in props.items():
        setattr(n, k, v)
    for k, v in (ins or {}).items():
        if isinstance(v, bpy.types.NodeSocket):
            tree.links.new(v, n.inputs[k])
        else:
            n.inputs[k].default_value = v
    return n


def _math(tree, op, a, b, node="ShaderNodeMath"):
    return _n(tree, node, {0: a, 1: b}, operation=op).outputs[0]


def _join(tree, *geos):
    j = _n(tree, "GeometryNodeJoinGeometry")
    for g in reversed(geos):
        tree.links.new(g, j.inputs[0])
    return j.outputs[0]


def _solid(tree, mesh, thickness):
    """Close flat faces into a watertight slab growing up by `thickness`."""
    nz = _n(tree, "ShaderNodeSeparateXYZ", {0: _n(tree, "GeometryNodeInputNormal").outputs[0]}).outputs["Z"]
    down = _n(tree, "FunctionNodeCompare", {0: nz, 1: 0.0}, data_type='FLOAT', operation='LESS_THAN').outputs[0]
    up = _n(tree, "GeometryNodeFlipFaces", {"Mesh": mesh, "Selection": down}).outputs[0]
    off = _n(tree, "ShaderNodeCombineXYZ", {"Z": thickness}).outputs[0]
    top = _n(tree, "GeometryNodeExtrudeMesh", {"Mesh": up, "Offset": off, "Individual": False}, mode='FACES').outputs["Mesh"]
    bottom = _n(tree, "GeometryNodeFlipFaces", {"Mesh": up}).outputs[0]
    return _n(tree, "GeometryNodeMergeByDistance",
              {"Geometry": _join(tree, top, bottom), "Distance": 1e-4}).outputs[0]


def _switch(tree, flag, geo):
    return _n(tree, "GeometryNodeSwitch", {"Switch": flag, "True": geo}, input_type='GEOMETRY').outputs[0]


def build_group():
    tree = bpy.data.node_groups.new(GROUP, 'GeometryNodeTree')
    tree.is_modifier = True
    tree.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    for name, typ, default, extra in INPUTS:
        s = tree.interface.new_socket(name, in_out='INPUT', socket_type=typ)
        if default is not None:
            s.default_value = default
        for k, v in extra.items():
            setattr(s, k, v)
    gi = tree.nodes.new("NodeGroupInput")
    go = tree.nodes.new("NodeGroupOutput")
    H, T, S = gi.outputs["Height"], gi.outputs["Thickness"], gi.outputs["Slab Thickness"]

    # Plan (mesh edges and/or curves) -> poly curves.
    plan = _n(tree, "GeometryNodeObjectInfo", {"Object": gi.outputs["Plan"]}, transform_space='RELATIVE')
    parts = _n(tree, "GeometryNodeSeparateComponents", {0: plan.outputs["Geometry"]})
    curves = _join(tree, _n(tree, "GeometryNodeMeshToCurve", {"Mesh": parts.outputs["Mesh"]}).outputs[0],
                   _n(tree, "GeometryNodeResampleCurve", {"Curve": parts.outputs["Curve"],
                                                          "Mode": 'Evaluated'}).outputs[0])

    # Make closed loops counter-clockwise (shoelace sum per curve) so Inside/Outside is consistent.
    pos = _n(tree, "GeometryNodeInputPosition").outputs[0]

    def neighbour(offset):
        o = _n(tree, "GeometryNodeOffsetPointInCurve", {"Offset": offset})
        p = _n(tree, "GeometryNodeFieldAtIndex", {"Value": pos, "Index": o.outputs["Point Index"]},
               domain='POINT', data_type='FLOAT_VECTOR').outputs[0]
        return o.outputs["Is Valid Offset"], p

    _, nxt = neighbour(1)
    cross = _n(tree, "ShaderNodeSeparateXYZ", {0: _math(tree, 'CROSS_PRODUCT', pos, nxt, "ShaderNodeVectorMath")})
    area = _n(tree, "GeometryNodeAccumulateField",
              {"Value": cross.outputs["Z"],
               "Group ID": _n(tree, "GeometryNodeCurveOfPoint").outputs["Curve Index"]},
              data_type='FLOAT', domain='POINT').outputs["Total"]
    cw = _n(tree, "FunctionNodeCompare", {0: area, 1: 0.0}, data_type='FLOAT', operation='LESS_THAN').outputs[0]
    cyclic = _n(tree, "GeometryNodeInputSplineCyclic").outputs[0]
    sel = _n(tree, "FunctionNodeBooleanMath", {0: cw, 1: cyclic}, operation='AND').outputs[0]
    curves = _n(tree, "GeometryNodeReverseCurve", {"Curve": curves, "Selection": sel}).outputs[0]
    oriented = _n(tree, "GeometryNodeSetCurveNormal", {"Curve": curves, "Mode": 'Z Up'}).outputs[0]

    # Mitre: at a corner the profile sits on the bisector, so widen it by 1/cos(half angle).
    valid, prev = neighbour(-1)
    d_in = _n(tree, "ShaderNodeVectorMath", {0: _math(tree, 'SUBTRACT', pos, prev, "ShaderNodeVectorMath")},
              operation='NORMALIZE').outputs[0]
    cos = _n(tree, "ShaderNodeVectorMath", {0: _n(tree, "GeometryNodeInputTangent").outputs[0], 1: d_in},
             operation='DOT_PRODUCT').outputs["Value"]
    # ponytail: corners sharper than ~150 deg are capped at 4x width instead of a true spike.
    miter = _math(tree, 'DIVIDE', 1.0, _math(tree, 'MAXIMUM', cos, 0.25))
    scale = _n(tree, "GeometryNodeSwitch", {"Switch": valid, "False": 1.0, "True": miter},
               input_type='FLOAT').outputs[0]

    # Profile: a line across the wall, shifted by the alignment.
    ms = _n(tree, "GeometryNodeMenuSwitch", data_type='FLOAT')
    ms.enum_items.clear()
    for name, shift in ALIGN:
        ms.enum_items.new(name)
        ms.inputs[name].default_value = shift
    tree.links.new(gi.outputs["Alignment"], ms.inputs["Menu"])
    tree.interface.items_tree["Alignment"].default_value = "Center"
    ends = [_n(tree, "ShaderNodeCombineXYZ",
               {"X": _math(tree, 'MULTIPLY', _math(tree, 'ADD', ms.outputs[0], h), T)}).outputs[0]
            for h in (-0.5, 0.5)]
    profile = _n(tree, "GeometryNodeCurvePrimitiveLine", {"Start": ends[0], "End": ends[1]}).outputs[0]
    ribbon = _n(tree, "GeometryNodeCurveToMesh",
                {"Curve": oriented, "Profile Curve": profile, "Scale": scale}).outputs[0]
    walls = _solid(tree, ribbon, H)

    # Floor below z=0 and ceiling on top of the walls, filled from closed loops only.
    loops = _n(tree, "GeometryNodeSeparateGeometry", {"Geometry": curves, "Selection": cyclic},
               domain='CURVE').outputs["Selection"]
    slab = _solid(tree, _n(tree, "GeometryNodeFillCurve", {"Curve": loops, "Mode": 'N-gons'}).outputs[0], S)
    down = _n(tree, "ShaderNodeCombineXYZ", {"Z": _math(tree, 'MULTIPLY', S, -1.0)}).outputs[0]
    floor = _n(tree, "GeometryNodeTransform", {"Geometry": slab, "Translation": down}).outputs[0]
    ceiling = _n(tree, "GeometryNodeTransform",
                 {"Geometry": slab, "Translation": _n(tree, "ShaderNodeCombineXYZ", {"Z": H}).outputs[0]}).outputs[0]

    out = _join(tree, _switch(tree, gi.outputs["Walls"], walls), _switch(tree, gi.outputs["Floor"], floor),
                _switch(tree, gi.outputs["Ceiling"], ceiling))
    flat = _n(tree, "GeometryNodeSetShadeSmooth", {"Geometry": out, "Shade Smooth": False})
    tree.links.new(flat.outputs[0], go.inputs[0])
    return tree


def get_group():
    tree = bpy.data.node_groups.get(GROUP)
    return tree if tree and tree.bl_idname == 'GeometryNodeTree' else build_group()


def walls_modifier(ob):
    if ob is None:
        return None
    return next((m for m in ob.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name.startswith(GROUP)), None)


def _ident(md, name):
    return next(i.identifier for i in md.node_group.interface.items_tree
                if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == name)


def get_input(md, name):
    return getattr(md.properties.inputs, _ident(md, name)).value


def set_input(md, name, value):
    getattr(md.properties.inputs, _ident(md, name)).value = value
    md.id_data.update_tag()  # GN inputs set from Python don't re-evaluate on their own (5.2)


def cutters_collection(scene):
    """Shared with the Quick Boolean add-on."""
    coll = bpy.data.collections.get("Cutters")
    if coll is None:
        coll = bpy.data.collections.new("Cutters")
        coll.hide_render = True
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def nearest_segment(plan, point, depsgraph):
    """(closest point, unit direction, segment length) of the plan edge nearest to point (in XY)."""
    ev = plan.evaluated_get(depsgraph)
    me = ev.to_mesh()
    mw = plan.matrix_world
    best = None
    for e in me.edges:
        a, b = (mw @ me.vertices[i].co for i in e.vertices)
        if (b - a).xy.length < 1e-6:
            continue
        p, fac = geometry.intersect_point_line(point, a, b)
        p = a.lerp(b, min(max(fac, 0.0), 1.0))
        d = (p - point).xy.length
        if best is None or d < best[0]:
            best = (d, a, b)
    ev.to_mesh_clear()
    return best and best[1:]


class ARCHVIZ_OT_add_walls(bpy.types.Operator):
    bl_idname = "archviz_walls.add_walls"
    bl_label = "Walls from Plan"
    bl_description = ("Create a live walls object from the active floor plan (mesh edges or curve); "
                      "editing the plan updates the walls")
    bl_options = {'REGISTER', 'UNDO'}

    height: bpy.props.FloatProperty(name="Height", default=2.7, min=0.0, unit='LENGTH')
    thickness: bpy.props.FloatProperty(name="Thickness", default=0.2, min=0.001, unit='LENGTH')
    alignment: bpy.props.EnumProperty(name="Alignment", items=[(a, a, "") for a, _ in ALIGN])
    floor: bpy.props.BoolProperty(name="Floor", default=True)
    ceiling: bpy.props.BoolProperty(name="Ceiling", default=False)
    slab: bpy.props.FloatProperty(name="Slab Thickness", default=0.2, min=0.001, unit='LENGTH')

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return context.mode == 'OBJECT' and ob and ob.type in {'MESH', 'CURVE'} and not walls_modifier(ob)

    def execute(self, context):
        plan = context.active_object
        ob = bpy.data.objects.new(plan.name + "_Walls", bpy.data.meshes.new(plan.name + "_Walls"))
        for c in plan.users_collection:
            c.objects.link(ob)
        md = ob.modifiers.new(MOD, 'NODES')
        md.node_group = get_group()
        for name, v in (("Plan", plan), ("Height", self.height), ("Thickness", self.thickness),
                        ("Alignment", self.alignment), ("Floor", self.floor), ("Ceiling", self.ceiling),
                        ("Slab Thickness", self.slab)):
            set_input(md, name, v)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        return {'FINISHED'}


class ARCHVIZ_OT_add_opening(bpy.types.Operator):
    bl_idname = "archviz_walls.add_opening"
    bl_label = "Add Opening"
    bl_description = ("Cut a door/window into the active walls at the plan edge nearest to the 3D cursor "
                      "(editable box cutter + Boolean modifier)")
    bl_options = {'REGISTER', 'UNDO'}

    kind: bpy.props.EnumProperty(name="Type", items=[('DOOR', "Door", ""), ('WINDOW', "Window", "")])
    width: bpy.props.FloatProperty(name="Width", default=0.9, min=0.01, unit='LENGTH')
    height: bpy.props.FloatProperty(name="Height", default=2.1, min=0.01, unit='LENGTH')
    sill: bpy.props.FloatProperty(name="Sill Height", default=0.0, unit='LENGTH')

    @classmethod
    def poll(cls, context):
        md = walls_modifier(context.active_object)
        return context.mode == 'OBJECT' and md is not None and get_input(md, "Plan") is not None

    def execute(self, context):
        walls = context.active_object
        md = walls_modifier(walls)
        seg = nearest_segment(get_input(md, "Plan"), context.scene.cursor.location, context.evaluated_depsgraph_get())
        if not seg:
            self.report({'WARNING'}, "The plan has no edges")
            return {'CANCELLED'}
        a, b = seg
        length = (b - a).xy.length
        fac = geometry.intersect_point_line(context.scene.cursor.location, a, b)[1]
        half = min(self.width / 2 / length, 0.5)  # keep the opening on its wall segment
        p = a.lerp(b, min(max(fac, half), 1.0 - half))

        name = self.kind.title()
        me = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        # Depth covers the wall for any alignment (wall lies within one thickness of the plan line).
        depth = 2 * get_input(md, "Thickness") + 0.1
        for v in bm.verts:
            v.co = Vector((v.co.x * self.width, v.co.y * depth, (v.co.z + 0.5) * self.height))
        bm.to_mesh(me)
        bm.free()
        cutter = bpy.data.objects.new(name, me)
        cutter.location = (p.x, p.y, p.z + self.sill)
        cutter.rotation_euler.z = math.atan2(b.y - a.y, b.x - a.x)
        cutter.display_type = 'WIRE'
        cutter.hide_render = True
        cutter.visible_camera = cutter.visible_shadow = False
        cutters_collection(context.scene).objects.link(cutter)
        cutter.parent = walls
        cutter.matrix_parent_inverse = walls.matrix_world.inverted()

        bm_mod = walls.modifiers.new(f"QB_{cutter.name}", 'BOOLEAN')
        bm_mod.operation, bm_mod.object, bm_mod.solver = 'DIFFERENCE', cutter, 'EXACT'
        return {'FINISHED'}


class ARCHVIZ_OT_apply(bpy.types.Operator):
    bl_idname = "archviz_walls.apply"
    bl_label = "Apply Walls"
    bl_description = "Bake the active walls into a plain mesh (applies node group and openings, deletes their cutters)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and walls_modifier(context.active_object) is not None

    def execute(self, context):
        ob = context.active_object
        cutters = {m.object for m in ob.modifiers if m.type == 'BOOLEAN' and m.object}
        with context.temp_override(object=ob, active_object=ob):
            for m in list(ob.modifiers):
                bpy.ops.object.modifier_apply(modifier=m.name)
        still_used = {m.object for o in bpy.data.objects for m in o.modifiers if m.type == 'BOOLEAN' and m.object}
        for c in cutters - still_used:
            bpy.data.objects.remove(c)
        return {'FINISHED'}


class ARCHVIZ_PT_panel(bpy.types.Panel):
    bl_label = "Archviz Walls"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Archviz"

    def draw(self, context):
        lay = self.layout
        md = walls_modifier(context.active_object)
        if md is None:
            lay.operator("archviz_walls.add_walls", icon='MOD_BUILD')
            lay.label(text="Select a plan (edges or curve)")
            return
        col = lay.column(align=True)
        for name, *_ in INPUTS:
            col.prop(getattr(md.properties.inputs, _ident(md, name)), "value", text=name)
        lay.label(text="Openings at 3D cursor:")
        row = lay.row(align=True)
        op = row.operator("archviz_walls.add_opening", text="Door")
        op.kind, op.width, op.height, op.sill = 'DOOR', 0.9, 2.1, 0.0
        op = row.operator("archviz_walls.add_opening", text="Window")
        op.kind, op.width, op.height, op.sill = 'WINDOW', 1.2, 1.2, 0.9
        lay.operator("archviz_walls.apply", icon='CHECKMARK')


_classes = (ARCHVIZ_OT_add_walls, ARCHVIZ_OT_add_opening, ARCHVIZ_OT_apply, ARCHVIZ_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
