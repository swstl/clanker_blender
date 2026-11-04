"""Image Palette: k-means colors of an image -> Palette (paint modes), swatch materials, hex list."""
import numpy as np

import bpy


def image_pixels(img, max_samples=20000, seed=0):
    """(N, 3) linear RGB samples of opaque pixels, randomly subsampled."""
    w, h = img.size
    if not w or not h:
        raise ValueError("Image has no pixels (not loaded?)")
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    px = px[px[:, 3] > 0.5][:, :3]
    if len(px) > max_samples:
        px = px[np.random.default_rng(seed).choice(len(px), max_samples, replace=False)]
    return px


def kmeans(data, k, iters=25, seed=0):
    """Plain k-means with k-means++ init. Returns (centers sorted by cluster size desc, sizes)."""
    rng = np.random.default_rng(seed)
    k = min(k, len(np.unique(data, axis=0)))
    centers = [data[rng.integers(len(data))]]
    for _ in range(1, k):
        d = np.min(((data[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        centers.append(data[rng.choice(len(data), p=d / d.sum())] if d.sum() > 0 else data[rng.integers(len(data))])
    c = np.array(centers)
    for _ in range(iters):
        labels = np.argmin(((data[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
        new = np.array([data[labels == i].mean(0) if np.any(labels == i) else c[i] for i in range(k)])
        if np.allclose(new, c, atol=1e-5):
            break
        c = new
    labels = np.argmin(((data[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
    sizes = np.bincount(labels, minlength=k)
    order = np.argsort(-sizes)
    return c[order], sizes[order]


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def to_hex(rgb_linear):
    s = (linear_to_srgb(np.asarray(rgb_linear)) * 255 + 0.5).astype(int)
    return "#{:02X}{:02X}{:02X}".format(*s)


class IP_OT_extract(bpy.types.Operator):
    bl_idname = "image_palette.extract"
    bl_label = "Extract Palette"
    bl_description = "Find the main colors of the image and store them as a Palette (+ optional swatch materials)"
    bl_options = {'REGISTER', 'UNDO'}

    image: bpy.props.StringProperty(name="Image", options={'SKIP_SAVE'})
    count: bpy.props.IntProperty(name="Colors", default=6, min=2, max=16)
    materials: bpy.props.BoolProperty(name="Create Swatch Materials", default=False)
    copy_hex: bpy.props.BoolProperty(name="Copy Hex Codes", default=True)

    def invoke(self, context, event):
        sp = context.space_data
        if not self.image and sp and sp.type == 'IMAGE_EDITOR' and sp.image:
            self.image = sp.image.name
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        lay = self.layout
        lay.prop_search(self, "image", bpy.data, "images")
        for p in ("count", "materials", "copy_hex"):
            lay.prop(self, p)

    def execute(self, context):
        img = bpy.data.images.get(self.image)
        if not img:
            self.report({'WARNING'}, "Pick an image")
            return {'CANCELLED'}
        try:
            centers, sizes = kmeans(image_pixels(img), self.count)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        pal = bpy.data.palettes.get(img.name) or bpy.data.palettes.new(img.name)
        pal.colors.clear()
        hexes = []
        for c in centers:
            pc = pal.colors.new()
            pc.color = tuple(linear_to_srgb(c))  # palette colors are display (sRGB) values
            hexes.append(to_hex(c))
            if self.materials:
                name = f"{img.name}_{hexes[-1][1:]}"
                m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
                m.diffuse_color = (*c, 1)
                bsdf = m.node_tree.nodes.get("Principled BSDF") if m.node_tree else None
                if bsdf:
                    bsdf.inputs["Base Color"].default_value = (*c, 1)
        ts = context.tool_settings
        for mode in ("image_paint", "vertex_paint", "texture_paint"):
            settings = getattr(ts, mode, None)
            if settings is not None and hasattr(settings, "palette"):
                settings.palette = pal
        if self.copy_hex:
            context.window_manager.clipboard = " ".join(hexes)
        self.report({'INFO'}, f"Palette '{pal.name}': {' '.join(hexes)}")
        return {'FINISHED'}


class IP_PT_panel(bpy.types.Panel):
    bl_label = "Image Palette"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Image"

    def draw(self, context):
        sp = context.space_data
        col = self.layout.column()
        op = col.operator("image_palette.extract", icon='COLOR')
        if sp and sp.image:
            op.image = sp.image.name
            pal = bpy.data.palettes.get(sp.image.name)
            if pal:
                col.template_palette(context.tool_settings.image_paint, "palette")


_classes = (IP_OT_extract, IP_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
