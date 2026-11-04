"""Bake Helper: one-click Cycles texture baking (SimpleBake style).

For every target material a temporary Image Texture node is added and made active, the bake runs,
then everything is put back in a try/finally (nodes, links, active node, engine, samples, selection).

Cycles has no Base Color / Metallic / Alpha pass, so those use the emission trick: the shader's
input socket (link or default value) is wired into a temporary Emission shader on the material
output and baked as EMIT. Base Color uses this too (the DIFFUSE color pass is darkened by metallic).
"""
import os

import bpy
import numpy as np

# key: (label, bake type, pass filter, emit-trick sockets + fallback value, non-color, lit)
MAPS = {
    'base_color': ("Base Color", 'EMIT', set(), (("Base Color", "Color"), 0.0), False, False),
    'roughness': ("Roughness", 'ROUGHNESS', set(), None, True, False),
    'metallic': ("Metallic", 'EMIT', set(), (("Metallic",), 0.0), True, False),
    'normal': ("Normal", 'NORMAL', set(), None, True, False),
    'emission': ("Emission", 'EMIT', set(), None, False, False),
    'ao': ("AO", 'AO', set(), None, True, True),
    'alpha': ("Alpha", 'EMIT', set(), (("Alpha",), 1.0), True, False),
    'combined': ("Combined", 'COMBINED', {'EMIT', 'DIRECT', 'INDIRECT', 'DIFFUSE', 'GLOSSY', 'TRANSMISSION'},
                 None, False, True),
    'glossy': ("Glossy", 'GLOSSY', {'DIRECT', 'INDIRECT', 'COLOR'}, None, False, True),
}
TAG = "bake_helper_tmp"


def _get_image(name, size, is_float, non_color):
    img = bpy.data.images.get(name)
    if img is None or tuple(img.size) != (size, size) or img.is_float != is_float:
        img = bpy.data.images.new(name, size, size, float_buffer=is_float)
    img.colorspace_settings.name = 'Non-Color' if non_color else 'sRGB'
    return img


def _store(img, folder, save):
    if save:
        ext = ".exr" if img.is_float else ".png"
        img.filepath_raw = os.path.join(folder, img.name + ext)
        img.file_format = 'OPEN_EXR' if img.is_float else 'PNG'
        img.save()
    else:
        img.pack()


def _emit_rewire(mat, sockets, fallback):
    """Wire the surface shader's socket into a temp Emission shader. Returns an undo callable."""
    nt = mat.node_tree
    out = nt.get_output_node('CYCLES') if nt else None
    if out is None:
        return lambda: None
    surf = out.inputs["Surface"]
    old = surf.links[0].from_socket if surf.is_linked else None
    shader = old.node if old else None
    src = next((s for s in (shader.inputs.get(n) for n in sockets) if s), None) if shader else None
    em = nt.nodes.new("ShaderNodeEmission")
    em.label = TAG
    col = em.inputs["Color"]
    if src is not None and src.is_linked:
        nt.links.new(src.links[0].from_socket, col)
    else:
        v = src.default_value if src is not None else fallback
        col.default_value = (*tuple(v)[:3], 1.0) if hasattr(v, "__len__") else (v, v, v, 1.0)
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs["Emission"], surf)

    def undo():
        nt.nodes.remove(em)
        if old is not None:
            nt.links.new(old, surf)
    return undo


def _materials(objs):
    mats = []
    for o in objs:
        for slot in o.material_slots:
            if slot.material and slot.material not in mats:
                mats.append(slot.material)
    return mats


def _ensure_uv(context, objs, mode):
    """Make sure every object has the UV map used for baking; Smart UV Project the ones missing it.
    Returns the uv_layer name to pass to the bake operator ("" = active)."""
    name = "Bake" if mode == 'BAKE' else ""
    missing = [o for o in objs if (o.data.uv_layers.get("Bake") is None if name else not o.data.uv_layers)]
    if not missing:
        return name
    prev = {o.name: o.data.uv_layers.active_index for o in missing if name and o.data.uv_layers}
    for o in missing:
        o.data.uv_layers.active = o.data.uv_layers.new(name=name or "UVMap")
    vl = context.view_layer
    for o in vl.objects:
        o.select_set(o in missing)
    vl.objects.active = missing[0]
    bpy.ops.object.mode_set(mode='EDIT')  # multi-object edit: one shared UV layout (atlas-friendly)
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')
    for o in missing:  # keep the user's working UV map active
        if o.name in prev:
            o.data.uv_layers.active_index = prev[o.name]
    return name


def pack_orm(name, ao, rough, metal):
    """Channel-pack AO/Roughness/Metallic (R channel of each, None -> 1/1/0) into one image."""
    ref = next(i for i in (ao, rough, metal) if i)
    w, h = ref.size
    out = np.ones((w * h, 4), dtype=np.float32)
    for ch, img, default in ((0, ao, 1.0), (1, rough, 1.0), (2, metal, 0.0)):
        if img is None:
            out[:, ch] = default
            continue
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        out[:, ch] = buf[0::4]
    orm = _get_image(name, w, ref.is_float, True)
    orm.pixels.foreach_set(out.ravel())
    orm.update()
    return orm


def build_material(name, imgs, uv_name=""):
    """Principled material from {map key: image}."""
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:  # pre-5.0
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    uvn = None
    if uv_name:
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map, uvn.location = uv_name, (-900, 0)
    y = 400

    def tex(key):
        nonlocal y
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image, t.label, t.location = imgs[key], key.replace('_', ' ').title(), (-600, y)
        y -= 280
        if uvn:
            nt.links.new(uvn.outputs["UV"], t.inputs["Vector"])
        return t

    links = nt.links
    if 'base_color' in imgs:
        links.new(tex('base_color').outputs["Color"], bsdf.inputs["Base Color"])
    if 'orm' in imgs and not ('roughness' in imgs and 'metallic' in imgs):
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (-300, y + 280)
        links.new(tex('orm').outputs["Color"], sep.inputs["Color"])
        links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
    for key, inp in (('roughness', "Roughness"), ('metallic', "Metallic"), ('alpha', "Alpha")):
        if key in imgs:
            links.new(tex(key).outputs["Color"], bsdf.inputs[inp])
    if 'emission' in imgs:
        links.new(tex('emission').outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0
    if 'normal' in imgs:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location, nm.uv_map = (-300, y), uv_name
        links.new(tex('normal').outputs["Color"], nm.inputs["Color"])
        links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def assign_material(ob, mat, mode):
    me = ob.data
    if mode == 'REPLACE':
        me.materials.clear()
        me.materials.append(mat)
    elif mode == 'KEEP':
        # ponytail: all faces go to the baked slot 0, originals stay in later slots; per-face
        # original assignment is lost (store it in an attribute if multi-material switching back matters)
        mats = [m for m in me.materials]
        me.materials.clear()
        for m in [mat] + mats:
            me.materials.append(m)
        me.polygons.foreach_set("material_index", [0] * len(me.polygons))
    me.update()


def bake(context, s):
    """Run the bake described by BakeHelperSettings `s`. Returns {group name: {map key: image}}."""
    scene, vl = context.scene, context.view_layer
    sel, active = list(context.selected_objects), context.view_layer.objects.active
    if s.selected_to_active:
        if active is None or active.type != 'MESH' or len(sel) < 2:
            raise RuntimeError("Selected to Active needs a selected high-poly and an active low-poly mesh")
        targets, sources = [active], [o for o in sel if o != active]
    else:
        targets, sources = [o for o in sel if o.type == 'MESH'], None
    if not targets:
        raise RuntimeError("Select at least one mesh object")
    for o in targets:
        if not _materials([o]):
            raise RuntimeError(f"'{o.name}' has no material to bake through")
    keys = [k for k in MAPS if getattr(s, "map_" + k)]
    if s.map_orm:
        keys += [k for k in ('ao', 'roughness', 'metallic') if k not in keys]
    if not keys:
        raise RuntimeError("Pick at least one map")
    folder = bpy.path.abspath(s.output_dir)
    if s.save and (folder.startswith("//") or not folder):
        raise RuntimeError("Save the .blend file first or choose an absolute output folder")
    if s.save:
        os.makedirs(folder, exist_ok=True)

    groups = [(s.atlas_name, targets)] if s.atlas and not s.selected_to_active else [(o.name, [o]) for o in targets]
    results = {}
    undo = []
    eng, smp = scene.render.engine, scene.cycles.samples
    undo.append(lambda: (setattr(scene.render, "engine", eng), setattr(scene.cycles, "samples", smp)))
    try:
        scene.render.engine = 'CYCLES'
        uv_name = _ensure_uv(context, targets, s.uv_mode)
        for gname, objs in groups:
            nodes = []  # temp image node per target material
            for mat in _materials(objs):
                nt = mat.node_tree
                n = nt.nodes.new("ShaderNodeTexImage")
                n.label = TAG
                prev = nt.nodes.active
                nt.nodes.active = n
                nodes.append(n)
                undo.append(lambda nt=nt, n=n, prev=prev: (nt.nodes.remove(n),
                                                           prev and setattr(nt.nodes, "active", prev)))
            for o in vl.objects:
                o.select_set(o in objs or (sources is not None and o in sources))
            vl.objects.active = objs[0]
            imgs = results[gname] = {}
            for key in keys:
                label, btype, passes, emit, non_color, lit = MAPS[key]
                img = _get_image(s.name_pattern.format(object=gname, map=label.replace(" ", "")), s.resolution,
                                 s.use_float and key == 'normal', non_color)
                for n in nodes:
                    n.image = img
                scene.cycles.samples = s.samples if lit else s.flat_samples
                rewired = [_emit_rewire(m, *emit) for m in _materials(sources or objs)] if emit else []
                try:
                    bpy.ops.object.bake(
                        type=btype, pass_filter=passes, margin=s.margin, use_clear=True,
                        use_selected_to_active=s.selected_to_active, cage_extrusion=s.cage_extrusion,
                        max_ray_distance=s.max_ray_distance, normal_space='TANGENT',
                        target='IMAGE_TEXTURES', save_mode='INTERNAL', uv_layer=uv_name)
                finally:
                    for u in rewired:
                        u()
                imgs[key] = img
            if s.map_orm:
                imgs['orm'] = pack_orm(s.name_pattern.format(object=gname, map="ORM"),
                                       imgs['ao'], imgs['roughness'], imgs['metallic'])
            for key, img in imgs.items():
                if key in ('ao', 'roughness', 'metallic') and s.map_orm and not getattr(s, "map_" + key):
                    continue  # only baked to build the ORM
                _store(img, folder, s.save)
    finally:
        for u in reversed(undo):
            u()
        for o in vl.objects:
            o.select_set(o in sel)
        vl.objects.active = active

    if s.create_material:
        for gname, objs in groups:
            imgs = {k: v for k, v in results[gname].items()
                    if k not in ('ao', 'combined', 'glossy') and getattr(s, "map_" + k)}
            mat = build_material(gname + "_baked", imgs, uv_name)
            if s.assign != 'NONE':
                for o in objs:
                    assign_material(o, mat, s.assign)
    return results


class BakeHelperSettings(bpy.types.PropertyGroup):
    atlas: bpy.props.BoolProperty(
        name="Shared Atlas", description="Bake all selected objects into one shared set of textures "
        "(their UVs must not overlap; the dedicated Bake UV map packs them together)")
    atlas_name: bpy.props.StringProperty(name="Atlas Name", default="Atlas")
    selected_to_active: bpy.props.BoolProperty(
        name="Selected to Active", description="Bake the selected (high-poly) objects onto the active (low-poly)")
    cage_extrusion: bpy.props.FloatProperty(name="Extrusion", default=0.05, min=0.0, subtype='DISTANCE',
                                            description="Inflate the active object for ray casting")
    max_ray_distance: bpy.props.FloatProperty(name="Max Ray Distance", default=0.0, min=0.0, subtype='DISTANCE',
                                              description="Maximum ray distance (0 = unlimited)")
    uv_mode: bpy.props.EnumProperty(name="UV", items=[
        ('ACTIVE', "Active UV Map", "Bake into the active UV map, Smart UV Project a new one if the mesh has none"),
        ('BAKE', "Dedicated 'Bake' UV", "Bake into a UV map named 'Bake', created with Smart UV Project if missing"),
    ], default='ACTIVE')
    resolution: bpy.props.IntProperty(name="Resolution", default=1024, min=8, max=16384, soft_min=512,
                                      soft_max=8192, subtype='PIXEL')
    margin: bpy.props.IntProperty(name="Margin", default=16, min=0, max=256, subtype='PIXEL',
                                  description="Extend baked pixels past UV island borders")
    samples: bpy.props.IntProperty(name="Lit Samples", default=128, min=1,
                                   description="Samples for lit passes (Combined, AO, Glossy)")
    flat_samples: bpy.props.IntProperty(name="Flat Samples", default=16, min=1,
                                        description="Samples for non-lit passes (color, roughness, normal...)")
    use_float: bpy.props.BoolProperty(name="32-bit Float Normal", description="Bake the normal map to a float "
                                      "image (saved as OpenEXR)")
    output_dir: bpy.props.StringProperty(name="Folder", default="//bakes/", subtype='DIR_PATH',
                                         options={'PATH_SUPPORTS_BLEND_RELATIVE'})
    name_pattern: bpy.props.StringProperty(name="Name", default="{object}_{map}",
                                           description="Image name; {object} and {map} are replaced")
    save: bpy.props.BoolProperty(name="Save to Disk", default=True,
                                 description="Save images to the folder (otherwise pack them into the .blend)")
    create_material: bpy.props.BoolProperty(name="Create Baked Material",
                                            description="Build a Principled material from the baked maps")
    assign: bpy.props.EnumProperty(name="Assign", items=[
        ('NONE', "Don't Assign", ""),
        ('KEEP', "Keep Original", "Baked material in slot 1, original materials kept in later slots"),
        ('REPLACE', "Replace", "Replace all material slots with the baked material"),
    ], default='KEEP')
    map_orm: bpy.props.BoolProperty(name="ORM (packed)", description="Channel-pack AO, Roughness, Metallic into RGB")


for _k, _v in MAPS.items():
    BakeHelperSettings.__annotations__["map_" + _k] = bpy.props.BoolProperty(
        name=_v[0], default=_k in ('base_color', 'roughness', 'normal'))


class BAKEHELPER_OT_bake(bpy.types.Operator):
    bl_idname = "bake_helper.bake"
    bl_label = "Bake Maps"
    bl_description = ("Bake the checked maps of the selected objects with Cycles, adding and removing the "
                      "image nodes automatically")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        try:
            res = bake(context, context.scene.bake_helper)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        n = sum(len(v) for v in res.values())
        self.report({'INFO'}, f"Baked {n} image(s) for {len(res)} target(s)")
        return {'FINISHED'}


def _draw(layout, context):
    s = context.scene.bake_helper
    col = layout.column()
    col.prop(s, "selected_to_active")
    if s.selected_to_active:
        sub = col.column(align=True)
        sub.prop(s, "cage_extrusion")
        sub.prop(s, "max_ray_distance")
    else:
        row = col.row(align=True)
        row.prop(s, "atlas")
        if s.atlas:
            row.prop(s, "atlas_name", text="")
    col.prop(s, "uv_mode")
    box = layout.box()
    box.label(text="Maps")
    grid = box.grid_flow(columns=2, even_columns=True)
    for k in MAPS:
        grid.prop(s, "map_" + k)
    grid.prop(s, "map_orm")
    col = layout.column()
    col.prop(s, "resolution")
    col.prop(s, "margin")
    sub = col.column(align=True)
    sub.prop(s, "flat_samples")
    sub.prop(s, "samples")
    col.prop(s, "use_float")
    col.prop(s, "save")
    sub = col.column()
    sub.active = s.save
    sub.prop(s, "output_dir")
    col.prop(s, "name_pattern")
    col.prop(s, "create_material")
    if s.create_material:
        col.prop(s, "assign")
    layout.operator(BAKEHELPER_OT_bake.bl_idname, icon='RENDER_STILL')


class BAKEHELPER_PT_view3d(bpy.types.Panel):
    bl_label = "Bake Helper"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Bake"

    def draw(self, context):
        _draw(self.layout, context)


class BAKEHELPER_PT_render(bpy.types.Panel):
    bl_label = "Bake Helper"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "render"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == 'CYCLES'

    def draw(self, context):
        _draw(self.layout, context)


_classes = (BakeHelperSettings, BAKEHELPER_OT_bake, BAKEHELPER_PT_view3d, BAKEHELPER_PT_render)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.bake_helper = bpy.props.PointerProperty(type=BakeHelperSettings)


def unregister():
    del bpy.types.Scene.bake_helper
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
