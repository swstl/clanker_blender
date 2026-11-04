"""Curve Tools: live Geometry Nodes scatter along curves, and sagging cables between objects/verts."""
import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector

GROUP = "CT Objects Along Curve"
MOD = "Along Curve"


# ---------------------------------------------------------------- Objects Along Curve (GN)

def _link(tree, a, b):
    tree.links.new(a, b)


def build_group():
    """Get or create the shared node group. Inputs show up as modifier settings."""
    tree = bpy.data.node_groups.get(GROUP)
    if tree:
        return tree
    tree = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    tree.is_modifier = True
    io, N = tree.interface, tree.nodes

    def inp(name, stype, default=None, lo=None, hi=None, subtype=None):
        s = io.new_socket(name, in_out="INPUT", socket_type=stype)
        if subtype:
            s.subtype = subtype
        if default is not None:
            s.default_value = default
        if lo is not None:
            s.min_value = lo
        if hi is not None:
            s.max_value = hi
        return s

    io.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    io.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    inp("Collection", "NodeSocketCollection")
    inp("Mode", "NodeSocketMenu")
    inp("Count", "NodeSocketInt", 10, 1)
    inp("Spacing", "NodeSocketFloat", 1.0, 0.001, subtype='DISTANCE')
    inp("Include Ends", "NodeSocketBool", True)
    inp("Rotation", "NodeSocketMenu")
    inp("Random Pick", "NodeSocketBool", False)
    inp("Seed", "NodeSocketInt", 0)
    inp("Scale Jitter", "NodeSocketFloat", 0.0, 0.0, 1.0, subtype='FACTOR')
    inp("Rotation Jitter", "NodeSocketFloat", 0.0, 0.0, math.pi, subtype='ANGLE')
    inp("Normal Offset", "NodeSocketFloat", 0.0, subtype='DISTANCE')

    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    G = gi.outputs

    # Points: resample by count / by spacing / keep control points, optionally drop the ends.
    rc = N.new("GeometryNodeResampleCurve")
    rl = N.new("GeometryNodeResampleCurve")
    rl.inputs["Mode"].default_value = "Length"
    poly = N.new("GeometryNodeCurveSplineType")
    poly.spline_type = 'POLY'
    for n in (rc, rl, poly):
        _link(tree, G["Geometry"], n.inputs["Curve"])
    _link(tree, G["Count"], rc.inputs["Count"])
    _link(tree, G["Spacing"], rl.inputs["Length"])
    msw = N.new("GeometryNodeMenuSwitch")
    msw.data_type = 'GEOMETRY'
    msw.enum_items.clear()
    for name, src in (("Count", rc), ("Spacing", rl), ("Corners", poly)):
        msw.enum_items.new(name)
    for i, src in enumerate((rc, rl, poly)):
        _link(tree, src.outputs["Curve"], msw.inputs[i + 1])
    _link(tree, G["Mode"], msw.inputs["Menu"])

    ends = N.new("GeometryNodeCurveEndpointSelection")
    no_ends = N.new("FunctionNodeBooleanMath")
    no_ends.operation = 'NIMPLY'  # endpoint AND NOT include_ends
    _link(tree, ends.outputs[0], no_ends.inputs[0])
    _link(tree, G["Include Ends"], no_ends.inputs[1])
    dele = N.new("GeometryNodeDeleteGeometry")
    dele.domain = 'POINT'
    _link(tree, msw.outputs[0], dele.inputs["Geometry"])
    _link(tree, no_ends.outputs[0], dele.inputs["Selection"])
    c2p = N.new("GeometryNodeCurveToPoints")
    c2p.mode = 'EVALUATED'
    _link(tree, dele.outputs[0], c2p.inputs["Curve"])

    # Offset along the curve normal.
    off = N.new("ShaderNodeVectorMath")
    off.operation = 'SCALE'
    _link(tree, c2p.outputs["Normal"], off.inputs[0])
    _link(tree, G["Normal Offset"], off.inputs["Scale"])
    setp = N.new("GeometryNodeSetPosition")
    _link(tree, c2p.outputs["Points"], setp.inputs["Geometry"])
    _link(tree, off.outputs[0], setp.inputs["Offset"])

    # Rotation: tangent (X along curve, Z = normal) / upright (Z up, X toward tangent) / none.
    tan = N.new("FunctionNodeAxesToRotation")
    tan.primary_axis, tan.secondary_axis = 'X', 'Z'
    _link(tree, c2p.outputs["Tangent"], tan.inputs[0])
    _link(tree, c2p.outputs["Normal"], tan.inputs[1])
    up = N.new("FunctionNodeAxesToRotation")
    up.primary_axis, up.secondary_axis = 'Z', 'X'
    up.inputs[0].default_value = (0, 0, 1)
    _link(tree, c2p.outputs["Tangent"], up.inputs[1])
    rsw = N.new("GeometryNodeMenuSwitch")
    rsw.data_type = 'ROTATION'
    rsw.enum_items.clear()
    for name in ("Tangent", "Upright", "None"):
        rsw.enum_items.new(name)
    _link(tree, tan.outputs[0], rsw.inputs[1])
    _link(tree, up.outputs[0], rsw.inputs[2])
    _link(tree, G["Rotation"], rsw.inputs["Menu"])

    def rand(dtype, lo, hi, seed_add):
        r = N.new("FunctionNodeRandomValue")
        r.data_type = dtype
        for sock, v in ((r.inputs["Min"], lo), (r.inputs["Max"], hi)):
            if isinstance(v, (int, float)):
                sock.default_value = v
            else:
                _link(tree, v, sock)
        add = N.new("FunctionNodeIntegerMath")
        add.operation = 'ADD'
        _link(tree, G["Seed"], add.inputs[0])
        add.inputs[1].default_value = seed_add
        _link(tree, add.outputs[0], r.inputs["Seed"])
        return r.outputs[0]

    # Rotation jitter around local Z, scale jitter 1 +- j.
    neg = N.new("ShaderNodeMath")
    neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1
    _link(tree, G["Rotation Jitter"], neg.inputs[0])
    xyz = N.new("ShaderNodeCombineXYZ")
    _link(tree, rand('FLOAT', neg.outputs[0], G["Rotation Jitter"], 1), xyz.inputs["Z"])
    e2r = N.new("FunctionNodeEulerToRotation")
    _link(tree, xyz.outputs[0], e2r.inputs[0])
    rot = N.new("FunctionNodeRotateRotation")
    rot.rotation_space = 'LOCAL'
    _link(tree, rsw.outputs[0], rot.inputs["Rotation"])
    _link(tree, e2r.outputs[0], rot.inputs["Rotate By"])
    lo, hi = N.new("ShaderNodeMath"), N.new("ShaderNodeMath")
    lo.operation, hi.operation = 'SUBTRACT', 'ADD'
    for m in (lo, hi):
        m.inputs[0].default_value = 1.0
        _link(tree, G["Scale Jitter"], m.inputs[1])
    scale = rand('FLOAT', lo.outputs[0], hi.outputs[0], 2)

    # Which source: cycle in collection order, or random pick.
    idx = N.new("GeometryNodeInputIndex")
    isw = N.new("GeometryNodeSwitch")
    isw.input_type = 'INT'
    _link(tree, G["Random Pick"], isw.inputs["Switch"])
    _link(tree, idx.outputs[0], isw.inputs["False"])
    _link(tree, rand('INT', 0, 1 << 20, 3), isw.inputs["True"])

    col = N.new("GeometryNodeCollectionInfo")
    col.transform_space = 'ORIGINAL'
    col.inputs["Separate Children"].default_value = True
    col.inputs["Reset Children"].default_value = True
    _link(tree, G["Collection"], col.inputs["Collection"])
    iop = N.new("GeometryNodeInstanceOnPoints")
    iop.inputs["Pick Instance"].default_value = True
    _link(tree, setp.outputs[0], iop.inputs["Points"])
    _link(tree, col.outputs[0], iop.inputs["Instance"])
    _link(tree, isw.outputs[0], iop.inputs["Instance Index"])
    _link(tree, rot.outputs[0], iop.inputs["Rotation"])
    _link(tree, scale, iop.inputs["Scale"])

    join = N.new("GeometryNodeJoinGeometry")
    _link(tree, iop.outputs[0], join.inputs[0])
    _link(tree, G["Geometry"], join.inputs[0])
    _link(tree, join.outputs[0], go.inputs[0])
    for i, n in enumerate(tree.nodes):  # rough left-to-right so the tree is readable if opened
        n.location = (i * 180, 0)
    return tree


def set_input(md, name, value):
    """Set a GN modifier input by its interface name."""
    item = next(i for i in md.node_group.interface.items_tree if i.in_out == 'INPUT' and i.name == name)
    getattr(md.properties.inputs, item.identifier).value = value
    md.id_data.update_tag()  # 5.2: setting md.properties.inputs from Python doesn't re-evaluate


def get_input(md, name):
    item = next(i for i in md.node_group.interface.items_tree if i.in_out == 'INPUT' and i.name == name)
    return getattr(md.properties.inputs, item.identifier)


def along_modifier(ob):
    return next((m for m in ob.modifiers if m.type == 'NODES' and m.node_group
                 and m.node_group.name.startswith(GROUP)), None) if ob else None


class CT_OT_along_curve(bpy.types.Operator):
    bl_idname = "curve_tools.along_curve"
    bl_label = "Objects Along Curve"
    bl_description = ("Instance the selected objects along the active curve with a live Geometry Nodes "
                      "modifier (edit the curve and the copies follow)")
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(name="Mode", items=[
        ('Count', "Count", "Fixed number of copies"),
        ('Spacing', "Spacing", "One copy every Spacing units"),
        ('Corners', "Corners", "One copy per control point")], default='Spacing')
    count: bpy.props.IntProperty(name="Count", default=10, min=1)
    spacing: bpy.props.FloatProperty(name="Spacing", default=1.0, min=0.001, subtype='DISTANCE')
    include_ends: bpy.props.BoolProperty(name="Include Ends", default=True)
    rotation: bpy.props.EnumProperty(name="Rotation", items=[
        ('Tangent', "Align to Tangent", "Local X follows the curve"),
        ('Upright', "Keep Upright", "Z stays up, only turn around Z"),
        ('None', "No Rotation", "Keep the source orientation")], default='Tangent')
    random_pick: bpy.props.BoolProperty(name="Random Pick", description="Pick sources randomly instead of in order")
    seed: bpy.props.IntProperty(name="Seed")
    scale_jitter: bpy.props.FloatProperty(name="Scale Jitter", min=0, max=1, subtype='FACTOR')
    rotation_jitter: bpy.props.FloatProperty(name="Rotation Jitter", min=0, max=math.pi, subtype='ANGLE')
    normal_offset: bpy.props.FloatProperty(name="Normal Offset", subtype='DISTANCE')

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.active_object and context.active_object.type == 'CURVE'

    def execute(self, context):
        curve = context.active_object
        sources = [o for o in context.selected_objects if o != curve]
        md = along_modifier(curve) or curve.modifiers.new(MOD, 'NODES')
        md.node_group = build_group()
        if sources:
            # Hidden helper collection (not linked to the scene) holding the picked sources.
            name = f"{curve.name} Sources"
            coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
            for o in list(coll.objects):
                coll.objects.unlink(o)
            for o in sorted(sources, key=lambda o: o.name):
                coll.objects.link(o)
            set_input(md, "Collection", coll)
        for name, val in (("Mode", self.mode), ("Count", self.count), ("Spacing", self.spacing),
                          ("Include Ends", self.include_ends), ("Rotation", self.rotation),
                          ("Random Pick", self.random_pick), ("Seed", self.seed),
                          ("Scale Jitter", self.scale_jitter), ("Rotation Jitter", self.rotation_jitter),
                          ("Normal Offset", self.normal_offset)):
            set_input(md, name, val)
        if not get_input(md, "Collection").value:
            self.report({'WARNING'}, "No sources selected: pick a Collection in the modifier")
        return {'FINISHED'}


def realize(context, curve, as_mesh=False):
    """Turn the instances into real linked duplicates (or one joined mesh); drop the modifier."""
    md = along_modifier(curve)
    dg = context.evaluated_depsgraph_get()
    new = []
    for inst in dg.object_instances:
        if inst.is_instance and inst.parent and inst.parent.original == curve:
            src = inst.object.original
            ob = src.copy()  # linked duplicate (shares data)
            ob.parent = None
            ob.matrix_world = inst.matrix_world.copy()
            context.collection.objects.link(ob)
            new.append(ob)
    if md:
        curve.modifiers.remove(md)
    if as_mesh and any(o.type == 'MESH' for o in new):
        meshes = [o for o in new if o.type == 'MESH']
        rest = [o for o in new if o.type != 'MESH']
        for o in meshes:
            o.data = o.data.copy()
        with context.temp_override(active_object=meshes[0], object=meshes[0],
                                   selected_objects=meshes, selected_editable_objects=meshes):
            bpy.ops.object.join()
        meshes[0].name = f"{curve.name} Realized"
        new = [meshes[0]] + rest
    return new


class CT_OT_realize(bpy.types.Operator):
    bl_idname = "curve_tools.realize"
    bl_label = "Realize"
    bl_description = "Replace the live copies on the active curve with real objects (or one mesh)"
    bl_options = {'REGISTER', 'UNDO'}

    as_mesh: bpy.props.BoolProperty(name="Join to One Mesh", default=False)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and along_modifier(context.active_object)

    def execute(self, context):
        new = realize(context, context.active_object, self.as_mesh)
        self.report({'INFO'}, f"Created {len(new)} objects")
        return {'FINISHED'}


# ---------------------------------------------------------------- Cable Generator

def sag_for(chord, slack):
    """Parabola sag for a cable `slack` times longer than the chord (L ~ D + 8S^2/3D)."""
    return chord * math.sqrt(3 * slack / 8)


def chain_order(items, pos, start):
    """Greedy nearest-neighbour chain starting at `start`."""
    rest = [i for i in items if i is not start]
    out = [start]
    while rest:
        nxt = min(rest, key=lambda i: (pos(i) - pos(out[-1])).length)
        rest.remove(nxt)
        out.append(nxt)
    return out


def _perp(d):
    d = d.normalized() if d.length > 1e-9 else Vector((1, 0, 0))
    u = d.cross(Vector((0, 0, 1)))
    if u.length < 1e-6:
        u = d.cross(Vector((1, 0, 0)))
    u.normalize()
    return u, d.cross(u).normalized()


def build_cable(points, slack=0.1, cables=1, spread=0.0, sag_random=0.0, seed=0):
    """Bezier curve data: one spline per cable through `points`, each segment hanging like a parabola.
    Handles at 1/3 and 2/3 dropped by 4/3 of the sag make the cubic an exact parabola."""
    cu = bpy.data.curves.new("Cable", 'CURVE')
    cu.dimensions = '3D'
    rng = random.Random(seed)
    n = len(points)
    for k in range(cables):
        pts = []
        for i, p in enumerate(points):
            if k == 0 or spread <= 0:
                pts.append(p.copy())
                continue
            u, v = _perp(points[min(i + 1, n - 1)] - points[max(i - 1, 0)])
            a, r = rng.uniform(0, 2 * math.pi), spread * math.sqrt(rng.random())
            pts.append(p + (u * math.cos(a) + v * math.sin(a)) * r)
        sp = cu.splines.new('BEZIER')
        sp.bezier_points.add(n - 1)
        bps = sp.bezier_points
        for bp, p in zip(bps, pts):
            bp.handle_left_type = bp.handle_right_type = 'FREE'
            bp.co = p
        for i in range(n - 1):
            p0, p1 = pts[i], pts[i + 1]
            d = p1 - p0
            s = slack * (1 + rng.uniform(-sag_random, sag_random)) if k else slack
            drop = Vector((0, 0, -4 / 3 * sag_for(d.length, max(s, 0))))
            bps[i].handle_right = p0 + d / 3 + drop
            bps[i + 1].handle_left = p1 - d / 3 + drop
        bps[0].handle_left = 2 * pts[0] - bps[0].handle_right
        bps[-1].handle_right = 2 * pts[-1] - bps[-1].handle_left
    return cu


def bezier_eval(bp0, bp1, t):
    a, b, c, d = bp0.co, bp0.handle_right, bp1.handle_left, bp1.co
    m = 1 - t
    return m ** 3 * a + 3 * m * m * t * b + 3 * m * t * t * c + t ** 3 * d


def add_cap(name, at, direction, radius, depth, coll, parent=None):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=16, radius1=radius, radius2=radius, depth=depth)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    ob.matrix_world = Matrix.Translation(at) @ direction.to_track_quat('Z', 'Y').to_matrix().to_4x4()
    if parent:
        mw = ob.matrix_world.copy()
        ob.parent = parent
        ob.matrix_world = mw
    return ob


class CT_OT_cable(bpy.types.Operator):
    bl_idname = "curve_tools.cable"
    bl_label = "Cable"
    bl_description = ("Hang a sagging cable through the selected objects (nearest chain from the active one) "
                      "or selected vertices; hooks keep the ends attached when the objects move")
    bl_options = {'REGISTER', 'UNDO'}

    slack: bpy.props.FloatProperty(name="Slack", description="Extra cable length relative to the distance",
                                   default=0.1, min=0, max=2, subtype='FACTOR')
    thickness: bpy.props.FloatProperty(name="Radius", default=0.02, min=0, subtype='DISTANCE')
    resolution: bpy.props.IntProperty(name="Resolution", default=16, min=1, max=64)
    bevel_resolution: bpy.props.IntProperty(name="Round Resolution", default=4, min=0, max=32)
    order: bpy.props.EnumProperty(name="Order", items=[
        ('CHAIN', "Nearest Chain", "Start at the active object / first vertex, then always the nearest"),
        ('NAME', "Name", "Alphabetical object order")], default='CHAIN')
    hooks: bpy.props.BoolProperty(name="Hook Ends", default=True,
                                  description="Hook modifiers so moving the objects drags the cable")
    caps: bpy.props.BoolProperty(name="End Caps", default=False)
    cables: bpy.props.IntProperty(name="Cables", default=1, min=1, max=64)
    spread: bpy.props.FloatProperty(name="Bundle Spread", default=0.05, min=0, subtype='DISTANCE')
    sag_random: bpy.props.FloatProperty(name="Random Sag", default=0.3, min=0, max=1, subtype='FACTOR')
    seed: bpy.props.IntProperty(name="Seed")

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_MESH'}

    def execute(self, context):
        targets = None
        if context.mode == 'EDIT_MESH':
            ob = context.edit_object
            bm = bmesh.from_edit_mesh(ob.data)
            sel = [v for v in bm.verts if v.select]
            hist = [e for e in bm.select_history if isinstance(e, bmesh.types.BMVert)]
            if len(hist) == len(sel):
                sel = hist  # click order
            elif sel:
                sel = chain_order(sel, lambda v: v.co, hist[0] if hist else sel[0])
            points = [ob.matrix_world @ v.co for v in sel]
        else:
            objs = [o for o in context.selected_objects]
            if self.order == 'NAME':
                objs.sort(key=lambda o: o.name)
            elif objs:
                act = context.active_object if context.active_object in objs else objs[0]
                objs = chain_order(objs, lambda o: o.matrix_world.translation, act)
            points = [o.matrix_world.translation.copy() for o in objs]
            targets = objs
        if len(points) < 2:
            self.report({'WARNING'}, "Select at least two objects or vertices")
            return {'CANCELLED'}
        cu = build_cable(points, self.slack, self.cables, self.spread, self.sag_random, self.seed)
        cu.bevel_depth = self.thickness
        cu.bevel_resolution = self.bevel_resolution
        cu.resolution_u = self.resolution
        cu.use_fill_caps = True
        cable = bpy.data.objects.new("Cable", cu)
        context.collection.objects.link(cable)
        n = len(points)
        if targets and self.hooks:
            for i, t in enumerate(targets):
                md = cable.modifiers.new(f"Hook {t.name}", 'HOOK')
                md.object = t
                md.center = points[i]
                md.matrix_inverse = t.matrix_world.inverted()
                # Each bezier point is 3 hook verts: left handle, point, right handle.
                md.vertex_indices_set([3 * (k * n + i) + j for k in range(self.cables) for j in range(3)])
        if self.caps:
            bps = cu.splines[0].bezier_points
            r = self.thickness * 1.6 + (self.spread if self.cables > 1 else 0)
            for end, bp, h, tgt in ((0, bps[0], bps[0].handle_right, targets[0] if targets else None),
                                    (1, bps[-1], bps[-1].handle_left, targets[-1] if targets else None)):
                d = h - bp.co
                add_cap("Cable Cap", bp.co, d if d.length > 1e-9 else Vector((0, 0, 1)), r,
                        self.thickness * 4, context.collection, tgt or cable)
        if context.mode == 'OBJECT':
            for o in context.selected_objects:
                o.select_set(False)
            cable.select_set(True)
            context.view_layer.objects.active = cable
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class CT_PT_panel(bpy.types.Panel):
    bl_label = "Curve Tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Edit"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator("curve_tools.along_curve", icon='CURVE_PATH')
        col.operator("curve_tools.realize", icon='OUTLINER_OB_GROUP_INSTANCE')
        col.separator()
        col.operator("curve_tools.cable", icon='CURVE_BEZCURVE')
        md = along_modifier(context.active_object)
        if md:
            box = self.layout.box()
            for item in md.node_group.interface.items_tree:
                if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.socket_type != 'NodeSocketGeometry':
                    box.prop(getattr(md.properties.inputs, item.identifier), "value", text=item.name)


def menu(self, context):
    self.layout.separator()
    self.layout.operator("curve_tools.along_curve", icon='CURVE_PATH')
    self.layout.operator("curve_tools.cable", icon='CURVE_BEZCURVE')


_classes = (CT_OT_along_curve, CT_OT_realize, CT_OT_cable, CT_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_curve_add.append(menu)


def unregister():
    bpy.types.VIEW3D_MT_curve_add.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
