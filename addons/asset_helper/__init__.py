"""Asset Helper: collections -> catalogs, batch marking, consistent previews."""
import math
import os
import tempfile
import uuid
from array import array

import bpy
from mathutils import Vector

CATS = "blender_assets.cats.txt"
HEADER = """# This is an Asset Catalog Definition file for Blender.
#
# Empty lines and lines starting with `#` will be ignored.
# The first non-ignored line should be the version indicator.
# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"

VERSION 1

"""


def _clean(name):
    return name.replace("/", "-").replace("\\", "-").replace(":", "-").strip()


def write_catalogs(folder, paths):
    """Merge `paths` into folder/blender_assets.cats.txt. Existing lines are kept verbatim and
    their UUIDs reused. Returns {path: uuid} for every catalog in the file."""
    fp = os.path.join(folder, CATS)
    text = open(fp, encoding="utf-8").read() if os.path.isfile(fp) else HEADER
    known = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "VERSION")) and line.count(":") >= 1:
            uid, path = line.split(":")[:2]
            known.setdefault(path, uid)
    new = [p for p in dict.fromkeys(paths) if p not in known]
    if new:
        if not text.endswith("\n"):
            text += "\n"
        for p in new:
            known[p] = str(uuid.uuid4())
            text += f"{known[p]}:{p}:{p.replace('/', '-')}\n"
        with open(fp, "w", encoding="utf-8") as f:
            f.write(text)
    return known


def walk(coll, path):
    yield coll, path
    for c in coll.children:
        yield from walk(c, f"{path}/{_clean(c.name)}" if path else _clean(c.name))


def mark(idb, settings, catalog=None, tags=()):
    if idb.library:
        return False
    idb.asset_mark()
    ad = idb.asset_data
    if catalog:
        ad.catalog_id = catalog
    if settings.use_tags:
        for t in tags:
            if t not in ad.tags:
                ad.tags.new(t)
    if settings.author:
        ad.author = settings.author
    if settings.description:
        ad.description = settings.description
    return True


def clear(idb):
    if idb.library:
        return False
    idb.asset_clear()
    if idb.preview:
        with bpy.context.temp_override(id=idb):
            bpy.ops.ed.lib_id_remove_preview()
    return True


def _root(context):
    s = context.scene.asset_helper
    return s.root or context.view_layer.active_layer_collection.collection


class AH_OT_collections_to_catalogs(bpy.types.Operator):
    bl_idname = "asset_helper.collections_to_catalogs"
    bl_label = "Collections to Catalogs"
    bl_description = ("Mirror the collection hierarchy under the root collection into asset catalogs "
                      "(blender_assets.cats.txt next to the .blend) and mark its contents as assets")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the .blend first (the catalog file goes next to it)")
            return {'CANCELLED'}
        s = context.scene.asset_helper
        root = _root(context)
        # The scene collection itself is not a catalog: start at its children.
        entries = list(walk(root, "" if root == context.scene.collection else _clean(root.name)))
        cats = write_catalogs(os.path.dirname(bpy.data.filepath), [p for _, p in entries if p])
        seen, n = set(), 0
        for coll, path in entries:
            tags = path.split("/") if path else ()
            if s.mode == 'COLLECTIONS':
                if coll != root and not coll.children:  # leaf collection = one asset in its parent's catalog
                    parent = path.rpartition("/")[0]
                    n += mark(coll, s, cats.get(parent), tags[:-1])
                continue
            for ob in coll.objects:
                ids = [ob] if s.mode == 'OBJECTS' else [m for m in getattr(ob.data, "materials", ()) if m]
                for idb in ids:
                    if idb not in seen:
                        seen.add(idb)
                        n += mark(idb, s, cats.get(path), tags)
        self.report({'INFO'}, f"{len(cats)} catalogs in {CATS}, {n} assets marked")
        return {'FINISHED'}


def selected_ids(context):
    """[(ID, tags)] for the selected objects and the data types enabled in the settings."""
    s = context.scene.asset_helper
    out = {}
    for ob in context.selected_objects:
        tags = [c.name for c in ob.users_collection if c != context.scene.collection]
        mats = [m for m in getattr(ob.data, "materials", ()) if m]
        ids = []
        if s.use_objects:
            ids.append(ob)
        if s.use_materials:
            ids += mats
        if s.use_node_groups:
            ids += [md.node_group for md in ob.modifiers if getattr(md, "node_group", None)]
            ids += [n.node_tree for m in mats if m.node_tree for n in m.node_tree.nodes
                    if n.type == 'GROUP' and n.node_tree]
        if s.use_actions and ob.animation_data and ob.animation_data.action:
            ids.append(ob.animation_data.action)
        for idb in ids:
            out.setdefault(idb, tags)
    return list(out.items())


class AH_OT_batch(bpy.types.Operator):
    bl_idname = "asset_helper.batch"
    bl_label = "Batch Mark Assets"
    bl_description = ("Mark (or clear, with previews) the selected objects and/or their materials, "
                      "node groups and actions as assets, tagged with their collection names")
    bl_options = {'REGISTER', 'UNDO'}

    clear: bpy.props.BoolProperty(name="Clear", description="Clear asset status and previews instead")

    def execute(self, context):
        s = context.scene.asset_helper
        n = sum(clear(i) if self.clear else mark(i, s, None, t) for i, t in selected_ids(context))
        self.report({'INFO'}, f"{'Cleared' if self.clear else 'Marked'} {n} data-blocks")
        return {'FINISHED'}


def _sun(sc, name, direction, energy):
    lt = bpy.data.lights.new(name, 'SUN')
    lt.energy = energy
    ob = bpy.data.objects.new(name, lt)
    ob.rotation_euler = Vector(direction).normalized().to_track_quat('Z', 'Y').to_euler()
    sc.collection.objects.link(ob)
    return ob


def studio_preview(idb, engine='BLENDER_WORKBENCH', size=256, world_strength=0.5):
    """Render idb (Object, Collection or Material) in a temporary lit scene and load the
    result as its custom preview."""
    sc = bpy.data.scenes.new("_asset_helper_preview")
    junk = []
    path = os.path.join(tempfile.gettempdir(), f"asset_helper_{os.getpid()}.png")
    try:
        if isinstance(idb, bpy.types.Object):
            sc.collection.objects.link(idb)
            objs = [idb]
        elif isinstance(idb, bpy.types.Collection):
            sc.collection.children.link(idb)
            objs = list(idb.all_objects)
        else:
            import bmesh
            me = bpy.data.meshes.new("_ah_sphere")
            bm = bmesh.new()
            bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1)
            for f in bm.faces:
                f.smooth = True
            bm.to_mesh(me)
            bm.free()
            me.materials.append(idb)
            ob = bpy.data.objects.new("_ah_sphere", me)
            sc.collection.objects.link(ob)
            objs = [ob]
            junk += [(bpy.data.objects, ob), (bpy.data.meshes, me)]
        pts = [ob.matrix_world @ Vector(c) for ob in objs for c in ob.bound_box] or [Vector((-1,) * 3), Vector((1,) * 3)]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)

        cd = bpy.data.cameras.new("_ah_cam")
        cam = bpy.data.objects.new("_ah_cam", cd)
        sc.collection.objects.link(cam)
        sc.camera = cam
        d = Vector((1, -1, 0.7)).normalized()
        dist = radius / math.sin(cd.angle / 2) * 1.05  # sphere around the bbox fits the view
        cam.location = center + d * dist
        cam.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
        cd.clip_start, cd.clip_end = dist * 0.01, dist + radius * 4
        junk += [(bpy.data.objects, cam), (bpy.data.cameras, cd)]
        for name, direction, energy in (("key", (1, -1, 1), 4), ("fill", (-1, -1, 0.3), 1.5), ("rim", (0, 1, 1), 3)):
            ob = _sun(sc, "_ah_" + name, direction, energy)
            junk += [(bpy.data.objects, ob), (bpy.data.lights, ob.data)]

        world = sc.world = bpy.data.worlds.new("_ah_world")
        world.color = (0.05, 0.05, 0.05)
        bg = world.node_tree.nodes.get("Background") if world.node_tree else None
        if bg:
            bg.inputs["Strength"].default_value = world_strength
            bg.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1)
        junk.append((bpy.data.worlds, world))

        r = sc.render
        r.engine = engine
        r.resolution_x = r.resolution_y = size
        r.resolution_percentage = 100
        r.film_transparent = True
        r.image_settings.file_format = 'PNG'
        r.image_settings.color_mode = 'RGBA'
        r.filepath = path
        sc.view_settings.view_transform = 'Standard'
        if engine == 'BLENDER_EEVEE':
            sc.eevee.taa_render_samples = 16
        bpy.ops.render.render(write_still=True, scene=sc.name)

        img = bpy.data.images.load(path)
        try:
            px = array('f', bytes(4 * len(img.pixels)))
            img.pixels.foreach_get(px)
            pv = idb.preview_ensure()
            pv.image_size = img.size
            pv.image_pixels_float.foreach_set(px)
        finally:
            bpy.data.images.remove(img)
    finally:
        bpy.data.scenes.remove(sc)
        for data, item in junk:
            data.remove(item)
        if os.path.isfile(path):
            os.remove(path)


_ASSET_TYPES = ("objects", "collections", "materials", "node_groups", "actions", "worlds", "brushes")


def local_assets():
    return [i for t in _ASSET_TYPES for i in getattr(bpy.data, t) if i.asset_data and not i.library]


class AH_OT_previews(bpy.types.Operator):
    bl_idname = "asset_helper.previews"
    bl_label = "Generate Previews"
    bl_description = ("Generate previews for every asset in this file. Studio mode renders objects, "
                      "collections and materials in a consistent lit setup")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.asset_helper
        failed = 0
        for idb in local_assets():
            if s.studio and isinstance(idb, (bpy.types.Object, bpy.types.Collection, bpy.types.Material)):
                try:
                    studio_preview(idb, s.engine, s.size, s.world_strength)
                    continue
                except Exception as e:  # fall back to Blender's own preview
                    failed += 1
                    print(f"asset_helper: studio preview of {idb.name} failed: {e}")
            idb.asset_generate_preview()
        self.report({'WARNING'} if failed else {'INFO'},
                    f"{failed} studio renders fell back to default previews" if failed else "Previews generated")
        return {'FINISHED'}


class AH_OT_register_library(bpy.types.Operator):
    bl_idname = "asset_helper.register_library"
    bl_label = "Register Folder as Asset Library"
    bl_description = "Add the folder of the current .blend to Preferences > File Paths > Asset Libraries"

    def execute(self, context):
        if not bpy.data.filepath:
            self.report({'ERROR'}, "Save the .blend first")
            return {'CANCELLED'}
        folder = os.path.dirname(bpy.data.filepath)
        libs = context.preferences.filepaths.asset_libraries
        if any(os.path.normpath(bpy.path.abspath(l.path)) == os.path.normpath(folder) for l in libs):
            self.report({'INFO'}, "Already an asset library")
            return {'CANCELLED'}
        libs.new(name=os.path.basename(folder) or folder, directory=folder)
        context.preferences.is_dirty = True  # let auto-save preferences pick it up
        self.report({'INFO'}, f"Added asset library {folder}")
        return {'FINISHED'}


class AH_Settings(bpy.types.PropertyGroup):
    root: bpy.props.PointerProperty(type=bpy.types.Collection, name="Root",
                                    description="Collection whose hierarchy becomes catalogs (empty = active collection)")
    mode: bpy.props.EnumProperty(name="Mark", items=(
        ('OBJECTS', "Per Object", "Each object becomes an asset in its collection's catalog"),
        ('COLLECTIONS', "Per Child Collection", "Each leaf collection becomes one asset in its parent's catalog"),
        ('MATERIALS', "Materials Used", "Materials of the objects become assets in the object's collection catalog")))
    use_objects: bpy.props.BoolProperty(name="Objects", default=True, description="Selected objects")
    use_materials: bpy.props.BoolProperty(name="Materials", description="Materials of selected objects")
    use_node_groups: bpy.props.BoolProperty(name="Node Groups",
                                            description="Geometry Nodes modifiers and shader groups of selected objects")
    use_actions: bpy.props.BoolProperty(name="Actions", description="Active actions of selected objects (poses, animations)")
    use_tags: bpy.props.BoolProperty(name="Tags from Collections", default=True,
                                     description="Tag assets with the names of their collections")
    author: bpy.props.StringProperty(name="Author", description="Set as asset author (empty = leave as is)")
    description: bpy.props.StringProperty(name="Description", description="Set as asset description (empty = leave as is)")
    studio: bpy.props.BoolProperty(name="Studio Previews",
                                   description="Render objects, collections and materials with a fixed camera and light rig")
    engine: bpy.props.EnumProperty(name="Engine", items=(('BLENDER_WORKBENCH', "Workbench", "Fast"),
                                                         ('BLENDER_EEVEE', "EEVEE", "Lit, shows materials")))
    size: bpy.props.IntProperty(name="Size", default=256, min=32, max=1024)
    world_strength: bpy.props.FloatProperty(name="World Strength", default=0.5, min=0, soft_max=5)


class AH_PT_assets(bpy.types.Panel):
    bl_label = "Asset Helper"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Assets"

    def draw(self, context):
        s = context.scene.asset_helper
        col = self.layout.column()
        box = col.box()
        box.label(text="Collections to Catalogs", icon='OUTLINER_COLLECTION')
        box.prop(s, "root")
        box.prop(s, "mode")
        box.operator(AH_OT_collections_to_catalogs.bl_idname, icon='ASSET_MANAGER')
        box = col.box()
        box.label(text="Batch (Selected Objects)", icon='RESTRICT_SELECT_OFF')
        grid = box.grid_flow(columns=2, align=True)
        for p in ("use_objects", "use_materials", "use_node_groups", "use_actions"):
            grid.prop(s, p)
        box.prop(s, "use_tags")
        box.prop(s, "author")
        box.prop(s, "description")
        row = box.row(align=True)
        row.operator(AH_OT_batch.bl_idname, text="Mark", icon='ASSET_MANAGER').clear = False
        row.operator(AH_OT_batch.bl_idname, text="Clear + Previews", icon='X').clear = True
        box = col.box()
        box.label(text="Previews", icon='IMAGE_DATA')
        box.prop(s, "studio")
        if s.studio:
            box.prop(s, "engine")
            row = box.row(align=True)
            row.prop(s, "size")
            row.prop(s, "world_strength", text="World")
        box.operator(AH_OT_previews.bl_idname, icon='FILE_REFRESH')
        col.operator(AH_OT_register_library.bl_idname, icon='FILE_FOLDER')


class AH_MT_outliner(bpy.types.Menu):
    bl_idname = "AH_MT_outliner"
    bl_label = "Asset Helper"

    def draw(self, context):
        l = self.layout
        l.operator(AH_OT_collections_to_catalogs.bl_idname)
        l.operator(AH_OT_batch.bl_idname, text="Mark Selected").clear = False
        l.operator(AH_OT_batch.bl_idname, text="Clear Selected + Previews").clear = True
        l.operator(AH_OT_previews.bl_idname)
        l.operator(AH_OT_register_library.bl_idname)


def outliner_menu(self, context):
    self.layout.separator()
    self.layout.menu("AH_MT_outliner")


_classes = (AH_Settings, AH_OT_collections_to_catalogs, AH_OT_batch, AH_OT_previews,
            AH_OT_register_library, AH_PT_assets, AH_MT_outliner)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.asset_helper = bpy.props.PointerProperty(type=AH_Settings)
    bpy.types.OUTLINER_MT_collection.append(outliner_menu)
    bpy.types.OUTLINER_MT_context_menu.append(outliner_menu)


def unregister():
    bpy.types.OUTLINER_MT_context_menu.remove(outliner_menu)
    bpy.types.OUTLINER_MT_collection.remove(outliner_menu)
    del bpy.types.Scene.asset_helper
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
