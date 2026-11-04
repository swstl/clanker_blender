"""Texture Proxies: swap big textures for downscaled copies while you work.

Proxies are separate image datablocks saved to //proxies/; Image Texture nodes point at them and remember
the original. Optionally the originals are swapped back automatically for every render.
"""
import os

import bpy
from bpy.app.handlers import persistent

TAG = "proxy_original"   # node custom prop: name of the original image


def _prefs_scene():
    return bpy.context.scene.texture_proxies


def image_nodes(materials):
    for mat in materials:
        if not mat or not mat.node_tree:
            continue
        stack = [mat.node_tree]
        seen = set()
        while stack:
            tree = stack.pop()
            if tree in seen:
                continue
            seen.add(tree)
            for n in tree.nodes:
                if n.type == 'TEX_IMAGE':
                    yield n
                elif n.type == 'GROUP' and n.node_tree:
                    stack.append(n.node_tree)


def target_materials(context, scope):
    if scope == 'ALL':
        return list(bpy.data.materials)
    mats = set()
    for ob in context.selected_objects:
        for s in getattr(ob, "material_slots", ()):
            if s.material:
                mats.add(s.material)
    return list(mats)


def image_bytes(img):
    w, h = img.size
    return w * h * (16 if img.is_float else 4)


def make_proxy(img, max_size):
    """Return a downscaled copy of img (created once per size), or None if already small enough."""
    w, h = img.size
    if max(w, h) <= max_size or not w:
        return None
    name = f"{img.name}__proxy{max_size}"
    proxy = bpy.data.images.get(name)
    if proxy:
        return proxy
    f = max_size / max(w, h)
    proxy = img.copy()
    proxy.name = name
    proxy.colorspace_settings.name = img.colorspace_settings.name  # before scale: it reloads the buffer
    proxy.scale(max(1, int(w * f)), max(1, int(h * f)))
    if bpy.data.filepath:
        folder = bpy.path.abspath("//proxies")
        os.makedirs(folder, exist_ok=True)
        ext = ".exr" if img.is_float else ".png"
        path = os.path.join(folder, bpy.path.clean_name(name) + ext)
        proxy.filepath_raw = path
        proxy.file_format = 'OPEN_EXR' if img.is_float else 'PNG'
        proxy.save()
        proxy.source = 'FILE'  # a copied GENERATED image would regenerate at full size otherwise
        proxy.filepath = bpy.path.relpath(path)
        proxy.reload()
    else:
        proxy.pack()
    return proxy


def use_proxies(materials, max_size):
    """Point image nodes at proxies. Returns (nodes switched, bytes saved)."""
    n = saved = 0
    for node in image_nodes(materials):
        img = node.image
        if not img or TAG in node.keys() or img.source not in {'FILE', 'GENERATED'}:
            continue
        if img.size[0] == 0:
            continue
        proxy = make_proxy(img, max_size)
        if proxy:
            node[TAG] = img.name
            saved += image_bytes(img) - image_bytes(proxy)
            node.image = proxy
            n += 1
    return n, saved


def use_originals(materials=None):
    n = 0
    for node in image_nodes(materials if materials is not None else bpy.data.materials):
        orig = node.get(TAG)
        if orig and orig in bpy.data.images:
            node.image = bpy.data.images[orig]
            del node[TAG]
            n += 1
    return n


_render_swapped = []


@persistent
def _render_pre(scene, *_):
    if not scene.texture_proxies.full_res_render:
        return
    for node in image_nodes(bpy.data.materials):
        orig = node.get(TAG)
        if orig and orig in bpy.data.images:
            _render_swapped.append((node, node.image))
            node.image = bpy.data.images[orig]


@persistent
def _render_post(scene, *_):
    while _render_swapped:
        node, proxy = _render_swapped.pop()
        try:
            node.image = proxy
        except ReferenceError:
            pass


class TP_Settings(bpy.types.PropertyGroup):
    max_size: bpy.props.EnumProperty(name="Max Size", default='1024', items=[
        (s, s, "") for s in ("256", "512", "1024", "2048")])
    scope: bpy.props.EnumProperty(name="Materials", items=[('SELECTED', "Selected Objects", ""),
                                                           ('ALL', "Whole File", "")])
    full_res_render: bpy.props.BoolProperty(name="Full Res for Render", default=True,
                                            description="Swap originals in while rendering (F12, render queue)")


class TP_OT_proxies(bpy.types.Operator):
    bl_idname = "texture_proxies.use"
    bl_label = "Use Proxies"
    bl_description = "Downscale large textures and use the copies in the viewport"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.texture_proxies
        n, saved = use_proxies(target_materials(context, s.scope), int(s.max_size))
        if n and s.full_res_render:  # render handlers swap images from the render thread: needs the lock
            context.scene.render.use_lock_interface = True
        self.report({'INFO'}, f"{n} textures on proxies, ~{saved / 2**20:.0f} MB less")
        return {'FINISHED'}


class TP_OT_originals(bpy.types.Operator):
    bl_idname = "texture_proxies.originals"
    bl_label = "Use Originals"
    bl_description = "Point every image node back at its full resolution image"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, f"{use_originals()} textures restored")
        return {'FINISHED'}


class TP_PT_panel(bpy.types.Panel):
    bl_label = "Texture Proxies"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.texture_proxies
        col = self.layout.column()
        big = sum(image_bytes(i) for i in bpy.data.images if i.users and i.has_data)
        col.label(text=f"Loaded images: {big / 2**20:.0f} MB")
        col.prop(s, "max_size")
        col.prop(s, "scope")
        col.prop(s, "full_res_render")
        row = col.row(align=True)
        row.operator("texture_proxies.use", icon='TEXTURE')
        row.operator("texture_proxies.originals", icon='LOOP_BACK')


_classes = (TP_Settings, TP_OT_proxies, TP_OT_originals, TP_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.texture_proxies = bpy.props.PointerProperty(type=TP_Settings)
    bpy.app.handlers.render_pre.append(_render_pre)
    bpy.app.handlers.render_post.append(_render_post)
    bpy.app.handlers.render_cancel.append(_render_post)


def unregister():
    for lst, fn in ((bpy.app.handlers.render_pre, _render_pre), (bpy.app.handlers.render_post, _render_post),
                    (bpy.app.handlers.render_cancel, _render_post)):
        if fn in lst:
            lst.remove(fn)
    del bpy.types.Scene.texture_proxies
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
