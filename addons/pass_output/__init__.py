"""Render Passes to Files: wire every enabled render pass of each view layer into a
File Output node ("PassOutput_<view layer>"), as one multilayer EXR or one file per pass.

Blender 5.x: the compositor is a node group in scene.compositing_node_group, File Output
inputs are scene-independent items (node.file_output_items), Render Layers outputs only
exist for enabled passes. Re-running updates the node instead of duplicating it.
"""
import re

import bpy

SKIP = {"Alpha"}  # already the A channel of Image
SOCKET_TYPES = {'VALUE': 'FLOAT', 'RGBA': 'RGBA', 'VECTOR': 'VECTOR'}


def clean(name):
    """File/layer safe name (also strips {} so Blender's path templates don't kick in)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "pass"


def fill_path(template, scene, view_layer):
    return template.replace("{scene}", clean(scene.name)).replace("{viewlayer}", clean(view_layer.name))


def ensure_tree(scene, view_layer):
    """Return the scene's compositor node group, creating Render Layers -> Group Output if missing."""
    tree = scene.compositing_node_group
    if tree is None:
        tree = bpy.data.node_groups.new(f"{scene.name} Compositing", 'CompositorNodeTree')
        tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
        out = tree.nodes.new("NodeGroupOutput")
        out.location = (300, 250)
        tree.links.new(render_layers_node(tree, scene, view_layer).outputs["Image"], out.inputs["Image"])
        scene.compositing_node_group = tree
    scene.render.use_compositing = True
    return tree


def ensure_node(tree, idname, name):
    n = tree.nodes.get(name)
    if n is None or n.bl_idname != idname:
        n = tree.nodes.new(idname)
        n.name = name
    n.label = name
    return n


def render_layers_node(tree, scene, view_layer):
    for n in tree.nodes:
        if n.bl_idname == "CompositorNodeRLayers" and n.layer == view_layer.name and n.scene in (scene, None):
            return n
    n = tree.nodes.new("CompositorNodeRLayers")
    n.scene, n.layer = scene, view_layer.name
    return n


def build(scene, view_layer, settings):
    """Create/update the File Output node for one view layer. Returns it."""
    tree = ensure_tree(scene, view_layer)
    rl = render_layers_node(tree, scene, view_layer)
    fo = ensure_node(tree, "CompositorNodeOutputFile", "PassOutput_" + view_layer.name)
    multi = settings.mode == 'MULTILAYER'

    fmt = fo.format
    if multi:
        fmt.media_type = 'MULTI_LAYER_IMAGE'
    else:
        fmt.media_type = 'IMAGE'
        fmt.file_format = settings.file_format
    if fmt.file_format in {'OPEN_EXR', 'OPEN_EXR_MULTILAYER'}:
        fmt.color_depth = settings.color_depth
        fmt.exr_codec = settings.exr_codec
    else:
        fmt.color_depth = '16'
    fo.directory = fill_path(settings.path, scene, view_layer)
    fo.file_name = clean(view_layer.name) + "_" if multi else ""

    wanted = {}  # item name -> Render Layers output
    for out in rl.outputs:
        if out.enabled and out.name not in SKIP:
            p = clean(out.name)
            wanted[p if multi else f"{p}/{p}_"] = out

    items = fo.file_output_items
    for name in [it.name for it in items if it.name not in wanted]:
        items.remove(next(it for it in items if it.name == name))
    have = {it.name for it in items}
    for name, out in wanted.items():
        if name not in have:
            items.new(SOCKET_TYPES.get(out.type, 'RGBA'), name)

    # Denoise node on Image, fed by Cycles denoising data passes.
    dn_name = "PassDenoise_" + view_layer.name
    outs = rl.outputs
    can_denoise = all(k in outs and outs[k].enabled for k in ("Denoising Albedo", "Denoising Normal"))
    dn = None
    if settings.denoise and can_denoise:
        dn = ensure_node(tree, "CompositorNodeDenoise", dn_name)
        for a, b in (("Image", "Image"), ("Denoising Albedo", "Albedo"), ("Denoising Normal", "Normal")):
            tree.links.new(outs[a], dn.inputs[b])
    elif dn_name in tree.nodes:
        tree.nodes.remove(tree.nodes[dn_name])

    for inp in fo.inputs:
        for link in list(inp.links):
            tree.links.remove(link)
    for it, inp in zip(items, fo.inputs):  # inputs follow item order (+ trailing extend socket)
        src = wanted[it.name]
        if dn and src.name == "Image":
            src = dn.outputs["Image"]
        tree.links.new(src, inp)
    return fo


def arrange(tree, scene, layers):
    """Stack Render Layers -> (Denoise) -> File Output rows top to bottom."""
    y = 0
    for vl in layers:
        rl = render_layers_node(tree, scene, vl)
        fo = tree.nodes.get("PassOutput_" + vl.name)
        dn = tree.nodes.get("PassDenoise_" + vl.name)
        rl.location = (0, y)
        if dn:
            dn.location = (300, y)
        if fo:
            fo.location = (550 if dn else 350, y)
            fo.width = 260
        rows = max(len([o for o in rl.outputs if o.enabled]), len(fo.inputs) if fo else 0)
        y -= rows * 24 + 220


class PO_Settings(bpy.types.PropertyGroup):
    mode: bpy.props.EnumProperty(
        name="Mode", items=[('MULTILAYER', "Multilayer EXR", "All passes as layers of one EXR per frame"),
                            ('SEPARATE', "Separate Files", "One file per pass in <pass>/<pass>_####")])
    file_format: bpy.props.EnumProperty(
        name="Format", description="File format for separate files",
        items=[('OPEN_EXR', "OpenEXR", ""), ('PNG', "PNG", "16-bit PNG (data passes get clamped)")])
    color_depth: bpy.props.EnumProperty(
        name="Depth", description="EXR bit depth",
        items=[('16', "Half (16)", "16-bit float"), ('32', "Full (32)", "32-bit float")])
    exr_codec: bpy.props.EnumProperty(
        name="Codec", description="EXR compression", default='ZIP',
        items=[('ZIP', "ZIP", "Lossless"), ('PIZ', "PIZ", "Lossless, good for grainy images"),
               ('DWAA', "DWAA", "Lossy"), ('NONE', "None", "")])
    path: bpy.props.StringProperty(
        name="Path", default="//renders/{scene}/{viewlayer}/", subtype='DIR_PATH',
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
        description="Output folder; {scene} and {viewlayer} are replaced by the names")
    all_layers: bpy.props.BoolProperty(
        name="All View Layers", default=True,
        description="Wire every view layer used for rendering (off: only the active one)")
    denoise: bpy.props.BoolProperty(
        name="Denoise Image",
        description="Insert a Denoise node on Image using the Cycles Denoising Albedo/Normal passes")


class PO_OT_wire(bpy.types.Operator):
    bl_idname = "pass_output.wire"
    bl_label = "Wire Passes to Files"
    bl_description = ("Create or update a File Output node per view layer that saves every enabled "
                      "render pass (AOVs and Cryptomatte included)")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        st = scene.pass_output
        layers = [vl for vl in scene.view_layers if vl.use] if st.all_layers else [context.view_layer]
        if not layers:
            self.report({'WARNING'}, "No view layer is enabled for rendering")
            return {'CANCELLED'}
        nodes = [build(scene, vl, st) for vl in layers]
        arrange(scene.compositing_node_group, scene, layers)
        if st.denoise and not any(n.name.startswith("PassDenoise_") for n in scene.compositing_node_group.nodes):
            self.report({'WARNING'}, "Denoise skipped: enable Cycles Denoising Data passes")
        n = sum(len(fo.file_output_items) for fo in nodes)
        self.report({'INFO'}, f"Wired {n} pass(es) in {len(nodes)} File Output node(s)")
        return {'FINISHED'}


def _toggles(scene, vl):
    t = [("Z", vl, "use_pass_z"), ("Mist", vl, "use_pass_mist"), ("Normal", vl, "use_pass_normal"),
         ("AO", vl, "use_pass_ambient_occlusion"), ("Emission", vl, "use_pass_emit"),
         ("Diff Dir", vl, "use_pass_diffuse_direct"), ("Diff Ind", vl, "use_pass_diffuse_indirect"),
         ("Diff Col", vl, "use_pass_diffuse_color"), ("Gloss Dir", vl, "use_pass_glossy_direct"),
         ("Gloss Ind", vl, "use_pass_glossy_indirect"), ("Gloss Col", vl, "use_pass_glossy_color"),
         ("Crypto Obj", vl, "use_pass_cryptomatte_object"), ("Crypto Mat", vl, "use_pass_cryptomatte_material")]
    if scene.render.engine == 'CYCLES':
        t += [("Shadow Catcher", vl.cycles, "use_pass_shadow_catcher"),
              ("Denoise Data", vl.cycles, "denoising_store_passes")]
    else:
        t.append(("Shadow", vl, "use_pass_shadow"))
    return t


def draw_ui(layout, context):
    scene, vl = context.scene, context.view_layer
    st = scene.pass_output
    layout.label(text="Quick Passes")
    grid = layout.grid_flow(columns=3, even_columns=True, align=True)
    for label, data, prop in _toggles(scene, vl):
        grid.prop(data, prop, text=label, toggle=True)

    col = layout.column()
    col.prop(st, "mode", expand=True)
    if st.mode == 'SEPARATE':
        col.prop(st, "file_format")
    if st.mode == 'MULTILAYER' or st.file_format == 'OPEN_EXR':
        row = col.row(align=True)
        row.prop(st, "color_depth", text="")
        row.prop(st, "exr_codec", text="")
    col.prop(st, "path")
    col.prop(st, "all_layers")
    col.prop(st, "denoise")
    layout.operator(PO_OT_wire.bl_idname, icon='NODE_COMPOSITING')


class PO_PT_view_layer(bpy.types.Panel):
    bl_label = "Passes to Files"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "view_layer"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_ui(self.layout, context)


class PO_PT_compositor(bpy.types.Panel):
    bl_label = "Passes to Files"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Passes"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'CompositorNodeTree'

    def draw(self, context):
        draw_ui(self.layout, context)


_classes = (PO_Settings, PO_OT_wire, PO_PT_view_layer, PO_PT_compositor)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.pass_output = bpy.props.PointerProperty(type=PO_Settings)


def unregister():
    del bpy.types.Scene.pass_output
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
