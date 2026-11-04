"""Texture Set Loader: build Principled BSDF materials from PBR texture sets on disk.

Handles Substance Painter, Quixel/Fab, Poly Haven, ambientCG and Texturing.xyz style names:
the map type is the right-most known token in the file name, everything left after removing
map/resolution/format tokens is the set (= material) name.

DirectX normals: green is flipped with Separate Color -> (1 - G) -> Combine Color before the
Normal Map node (the Normal Map node itself has no invert option).
"""
import os
import re

import bpy

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".tga", ".bmp", ".webp", ".hdr"}
EXT_RANK = {".exr": 4, ".tif": 3, ".tiff": 3, ".png": 2}  # everything else 1

# token (lowercase, separators removed) -> map kind
MAP_TOKENS = {}
for _kind, _toks in {
    'color': "basecolor albedo diffuse diff col color colour",
    'roughness': "roughness rough",
    'metallic': "metallic metalness metal",
    'normal': "normal nor nrm normalgl norgl nrmgl",
    'normal_dx': "normaldx nordx nrmdx",
    'height': "height displacement disp",
    'ao': "ao ambientocclusion occlusion",
    'opacity': "opacity alpha mask",
    'emission': "emissive emission",
    'specular': "specular spec",
    'orm': "orm arm",
}.items():
    MAP_TOKENS.update(dict.fromkeys(_toks.split(), _kind))

DATA_MAPS = {'roughness', 'metallic', 'normal', 'normal_dx', 'height', 'ao', 'opacity', 'specular', 'orm'}
FORMAT_TOKENS = {"jpg", "jpeg", "png", "exr", "tif", "tiff", "tga", "gl", "dx"}
RES_RE = re.compile(r"^(\d+)k$")


def parse_name(path):
    """Return (set_name, map_kind, resolution_k) or None if no map token is found."""
    stem = os.path.splitext(os.path.basename(path))[0]
    toks = [t for t in re.split(r"[_\-\s.]+", stem) if t]
    low = [t.lower() for t in toks]
    hit = None
    for i in range(len(low) - 1, -1, -1):  # right-most map token wins ("Metal_basecolor" -> color)
        if i and low[i - 1] + low[i] in MAP_TOKENS:  # two-token forms: base_color, nor_gl, ambient_occlusion
            hit = (i - 1, i + 1, MAP_TOKENS[low[i - 1] + low[i]])
            break
        if low[i] in MAP_TOKENS:
            hit = (i, i + 1, MAP_TOKENS[low[i]])
            break
    if not hit:
        return None
    a, b, kind = hit
    res = 0
    rest = []
    for j, (t, l) in enumerate(zip(toks, low)):
        if a <= j < b:
            continue
        m = RES_RE.match(l)
        if m:
            res = int(m.group(1))
        elif l not in FORMAT_TOKENS:
            rest.append(t)
    return "_".join(rest) or "TextureSet", kind, res


def group_files(paths, resolution='HIGHEST'):
    """{set_name: {kind: path}}, picking one file per map by resolution preference then format."""
    cands = {}
    for p in paths:
        if os.path.splitext(p)[1].lower() not in IMAGE_EXTS:
            continue
        parsed = parse_name(p)
        if parsed:
            name, kind, res = parsed
            cands.setdefault(name, {}).setdefault(kind, []).append((res, p))

    def key(item):
        res, p = item
        ext = EXT_RANK.get(os.path.splitext(p)[1].lower(), 1)
        if resolution == 'LOWEST':
            return (0, -res, ext)
        if resolution != 'HIGHEST':
            return (res == int(resolution[:-1]), res, ext)  # exact match first, else highest
        return (0, res, ext)

    return {name: {k: max(v, key=key)[1] for k, v in maps.items()} for name, maps in cands.items()}


def _sock(sockets, identifier):
    return next(s for s in sockets if s.identifier == identifier)


def build_material(name, maps, scale=1.0, box=False, ao_multiply=False, displacement='BUMP',
                   disp_scale=0.1):
    """Create one material from {kind: path}. Returns the material."""
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:  # pre-5.0
        mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()

    def node(kind, x, y, **attrs):
        n = nodes.new(kind)
        n.location = (x, y)
        for k, v in attrs.items():
            setattr(n, k, v)
        return n

    out = node("ShaderNodeOutputMaterial", 400, 300)
    bsdf = node("ShaderNodeBsdfPrincipled", 0, 300)
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    coord = node("ShaderNodeTexCoord", -1400, 300)
    mapping = node("ShaderNodeMapping", -1200, 300)
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coord.outputs["Object" if box else "UV"], mapping.inputs["Vector"])

    order = ['color', 'ao', 'orm', 'metallic', 'roughness', 'specular', 'opacity', 'emission',
             'normal', 'normal_dx', 'height']
    tex = {}
    y = 600
    for kind in order:
        if kind not in maps:
            continue
        t = node("ShaderNodeTexImage", -900, y, label=kind.replace('_', ' ').title())
        y -= 300
        t.image = bpy.data.images.load(maps[kind], check_existing=True)
        if kind in DATA_MAPS:
            t.image.colorspace_settings.name = 'Non-Color'
        if box:
            t.projection, t.projection_blend = 'BOX', 0.2
        links.new(mapping.outputs["Vector"], t.inputs["Vector"])
        tex[kind] = t

    mid = -450  # column for helper nodes between textures and BSDF
    orm = None
    if 'orm' in tex:
        orm = node("ShaderNodeSeparateColor", mid, tex['orm'].location.y)
        links.new(tex['orm'].outputs["Color"], orm.inputs["Color"])
    ao_out = tex['ao'].outputs["Color"] if 'ao' in tex else (orm.outputs["Red"] if orm else None)

    if 'color' in tex:
        col = tex['color'].outputs["Color"]
        if ao_multiply and ao_out:
            mix = node("ShaderNodeMix", mid, tex['color'].location.y, data_type='RGBA', blend_type='MULTIPLY',
                       label="AO Multiply")
            _sock(mix.inputs, "Factor_Float").default_value = 1.0
            links.new(col, _sock(mix.inputs, "A_Color"))
            links.new(ao_out, _sock(mix.inputs, "B_Color"))
            col = _sock(mix.outputs, "Result_Color")
        links.new(col, bsdf.inputs["Base Color"])

    for kind, inp in (('roughness', "Roughness"), ('metallic', "Metallic"), ('specular', "Specular IOR Level"),
                      ('opacity', "Alpha")):
        if kind in tex:
            links.new(tex[kind].outputs["Color"], bsdf.inputs[inp])
        elif orm and kind in ('roughness', 'metallic'):
            links.new(orm.outputs["Green" if kind == 'roughness' else "Blue"], bsdf.inputs[inp])

    if 'emission' in tex:
        links.new(tex['emission'].outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0

    if 'opacity' in tex:
        # Dithered (default) gives correct depth/sorting for cut-out masks in EEVEE; keep transparent shadows.
        mat.surface_render_method = 'DITHERED'
        if hasattr(mat, "use_transparent_shadow"):
            mat.use_transparent_shadow = True

    nkind = 'normal' if 'normal' in tex else 'normal_dx' if 'normal_dx' in tex else None
    if nkind:
        t = tex[nkind]
        nmap = node("ShaderNodeNormalMap", -200, t.location.y)
        src = t.outputs["Color"]
        if nkind == 'normal_dx':  # DirectX -> OpenGL: invert green
            sep = node("ShaderNodeSeparateColor", mid, t.location.y + 60)
            inv = node("ShaderNodeMath", mid, t.location.y - 120, operation='SUBTRACT', label="Flip Green (DX)")
            comb = node("ShaderNodeCombineColor", -300, t.location.y + 60)
            nmap.location.x = -100
            inv.inputs[0].default_value = 1.0
            links.new(src, sep.inputs["Color"])
            links.new(sep.outputs["Green"], inv.inputs[1])
            links.new(sep.outputs["Red"], comb.inputs["Red"])
            links.new(inv.outputs["Value"], comb.inputs["Green"])
            links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            src = comb.outputs["Color"]
        links.new(src, nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    if 'height' in tex and displacement != 'NONE':
        disp = node("ShaderNodeDisplacement", 150, tex['height'].location.y)
        disp.inputs["Midlevel"].default_value = 0.5
        disp.inputs["Scale"].default_value = disp_scale
        links.new(tex['height'].outputs["Color"], disp.inputs["Height"])
        links.new(disp.outputs["Displacement"], out.inputs["Displacement"])
        mat.displacement_method = displacement
    return mat


def collect_paths(directory, filenames):
    names = [f for f in filenames if f]
    if not names:  # folder picked without selecting files -> whole folder
        names = sorted(os.listdir(directory)) if os.path.isdir(directory) else []
    return [os.path.join(directory, n) for n in names if os.path.isfile(os.path.join(directory, n))]


class TS_OT_load(bpy.types.Operator):
    bl_idname = "texture_sets.load"
    bl_label = "Load Texture Set"
    bl_description = ("Pick texture files or a folder and build one Principled BSDF material per "
                      "PBR texture set found")
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    resolution: bpy.props.EnumProperty(
        name="Resolution", description="Which file to use when a map exists in several resolutions",
        items=[('HIGHEST', "Highest", ""), ('LOWEST', "Lowest", ""), ('1K', "1K", ""), ('2K', "2K", ""),
               ('4K', "4K", ""), ('8K', "8K", "")], default='HIGHEST')
    scale: bpy.props.FloatProperty(name="Scale", default=1.0, min=0.0001,
                                   description="Texture scale (set on the shared Mapping node)")
    box_projection: bpy.props.BoolProperty(name="Box Projection",
                                           description="Triplanar: Object coordinates + Box projection, no UVs needed")
    ao_multiply: bpy.props.BoolProperty(name="Multiply AO into Base Color")
    displacement: bpy.props.EnumProperty(
        name="Displacement", description="Use the height map as material displacement",
        items=[('NONE', "None", "Ignore height maps"), ('BUMP', "Bump Only", ""),
               ('DISPLACEMENT', "Displacement Only", "Real displacement (Cycles, needs dense mesh)"),
               ('BOTH', "Displacement and Bump", "")], default='BUMP')
    assign: bpy.props.EnumProperty(
        name="Assign", description="Assign the new materials to the selected objects",
        items=[('NONE', "Don't Assign", ""), ('APPEND', "Add Slots", "Append every new material as a new slot"),
               ('REPLACE', "Replace Active Slot", "Put the first new material in the active slot")],
        default='APPEND')
    mark_asset: bpy.props.BoolProperty(name="Mark as Asset", description="Mark materials as assets with a preview")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        sets = group_files(collect_paths(bpy.path.abspath(self.directory), [f.name for f in self.files]),
                           self.resolution)
        if not sets:
            self.report({'WARNING'}, "No PBR texture maps recognised")
            return {'CANCELLED'}
        mats = [build_material(name, maps, self.scale, self.box_projection, self.ao_multiply, self.displacement)
                for name, maps in sorted(sets.items())]
        for m in mats:
            if self.mark_asset:
                m.asset_mark()
                m.asset_generate_preview()
        if self.assign != 'NONE':
            for ob in context.selected_objects:
                if not hasattr(ob.data, "materials"):
                    continue
                if self.assign == 'APPEND':
                    for m in mats:
                        ob.data.materials.append(m)
                elif ob.material_slots:
                    ob.material_slots[ob.active_material_index].material = mats[0]
                else:
                    ob.data.materials.append(mats[0])
        self.report({'INFO'}, f"Created {len(mats)} material(s): {', '.join(m.name for m in mats)}")
        return {'FINISHED'}


class TS_PT_panel(bpy.types.Panel):
    bl_label = "Texture Sets"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Texture Sets"

    @classmethod
    def poll(cls, context):
        return context.space_data.tree_type == 'ShaderNodeTree'

    def draw(self, context):
        self.layout.operator(TS_OT_load.bl_idname, icon='FILE_IMAGE')


def _menu(self, context):
    self.layout.operator(TS_OT_load.bl_idname, text="PBR Texture Set (Material)")


_classes = (TS_OT_load, TS_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_menu)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
