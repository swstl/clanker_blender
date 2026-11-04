"""Paste Reference: clipboard / file images as reference empties, image planes or camera backgrounds."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import bpy
from bpy_extras.view3d_utils import region_2d_to_location_3d
from mathutils import Matrix, Quaternion, Vector

NO_IMAGE = ("No image in the clipboard. Copy an image first"
            + (" (on Linux, wl-clipboard or xclip helps as fallback)" if sys.platform == "linux" else ""))
# Tried when Blender's own clipboard paste finds nothing (e.g. X11 builds without image support).
EXTERNAL_CMDS = (["wl-paste", "--no-newline", "--type", "image/png"],
                 ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"])


# ---------------------------------------------------------------- clipboard

def blender_paste(context):
    """Run image.clipboard_paste by briefly turning the current area into an Image Editor."""
    area = context.area
    if area is None or context.window is None:
        return None
    before = {i.name for i in bpy.data.images}
    old = area.ui_type
    try:
        area.ui_type = 'IMAGE_EDITOR'
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with context.temp_override(area=area, region=region, space_data=area.spaces.active):
            bpy.ops.image.clipboard_paste()
    except (RuntimeError, StopIteration):
        pass
    finally:
        area.ui_type = old
    new = [i for i in bpy.data.images if i.name not in before]
    return new[0] if new else None


def external_paste():
    """PNG bytes from wl-paste / xclip, or None."""
    if sys.platform != "linux":
        return None
    for cmd in EXTERNAL_CMDS:
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if out.startswith(b"\x89PNG"):
            return out
    return None


def ref_path(base_dir):
    os.makedirs(base_dir, exist_ok=True)
    stem = "ref_" + time.strftime("%Y%m%d_%H%M%S")
    path, i = os.path.join(base_dir, stem + ".png"), 1
    while os.path.exists(path):
        path, i = os.path.join(base_dir, f"{stem}_{i}.png"), i + 1
    return path


def _rel(path):
    return bpy.path.relpath(path) if bpy.data.filepath else path


def persist(img, base_dir):
    """Save `img` as PNG into base_dir (e.g. //references) or pack it when there is no folder."""
    if base_dir:
        path = ref_path(base_dir)
        img.file_format = 'PNG'
        img.save(filepath=path)
        img.filepath = _rel(path)
        img.name = os.path.splitext(os.path.basename(path))[0]
    elif not img.packed_file:
        img.pack()
        if not img.packed_file:  # untouched generated buffers refuse to pack until dirty
            img.pixels[0] = img.pixels[0]
            img.pack()
    return img


def image_from_png(data, base_dir):
    if base_dir:
        path = ref_path(base_dir)
        with open(path, "wb") as f:
            f.write(data)
        img = bpy.data.images.load(path)
        img.filepath = _rel(path)
        return img
    fd, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        img = bpy.data.images.load(path)
        img.pack()
        img.name = "ref_" + time.strftime("%Y%m%d_%H%M%S")
    finally:
        os.remove(path)
    return img


# ---------------------------------------------------------------- placement

def view_info(context, fill):
    """(view rotation matrix, reference height): `fill` of the view height measured at the 3D cursor."""
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return Matrix(), 4.0 * fill
    rv3d = area.spaces.active.region_3d
    region = next(r for r in area.regions if r.type == 'WINDOW')
    cur = context.scene.cursor.location
    top = region_2d_to_location_3d(region, rv3d, (region.width / 2, region.height), cur)
    bot = region_2d_to_location_3d(region, rv3d, (region.width / 2, 0), cur)
    return rv3d.view_rotation.to_matrix(), max((top - bot).length, 1e-3) * fill


def _aspect(img):
    w, h = img.size
    return w / h if w and h else 1.0


def _material(img, opacity):
    mat = bpy.data.materials.new(img.name)
    if mat.node_tree is None:  # < 5.0
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    n = nt.nodes.new
    tex, emit, tr = n("ShaderNodeTexImage"), n("ShaderNodeEmission"), n("ShaderNodeBsdfTransparent")
    mul, mix, out = n("ShaderNodeMath"), n("ShaderNodeMixShader"), n("ShaderNodeOutputMaterial")
    tex.image = img
    mul.operation = 'MULTIPLY'
    mul.inputs[1].default_value = opacity
    for a, b in ((tex.outputs["Color"], emit.inputs["Color"]), (tex.outputs["Alpha"], mul.inputs[0]),
                 (mul.outputs[0], mix.inputs[0]), (tr.outputs[0], mix.inputs[1]),
                 (emit.outputs[0], mix.inputs[2]), (mix.outputs[0], out.inputs["Surface"])):
        nt.links.new(a, b)
    for i, node in enumerate((tex, mul, mix, out)):
        node.location = (i * 250 - 500, 0)
    emit.location, tr.location = (-250, -200), (-250, 200)
    mat.surface_render_method = 'BLENDED'
    return mat


def create_reference(context, img, mode, rot, location, height, opacity=0.5, depth='DEFAULT', front=False):
    """Make one reference for `img`. Returns the new object (or the camera for mode 'CAMERA')."""
    if mode == 'CAMERA':
        cam = context.scene.camera
        if cam is None or cam.type != 'CAMERA':
            raise ValueError("The scene has no camera")
        cam.data.show_background_images = True
        bg = cam.data.background_images.new()
        bg.image = img
        bg.alpha = opacity
        bg.display_depth = 'FRONT' if depth == 'FRONT' else 'BACK'
        return cam
    if mode == 'EMPTY':
        ob = bpy.data.objects.new(img.name, None)
        ob.empty_display_type = 'IMAGE'
        ob.data = img
        w, h = img.size
        ob.empty_display_size = height * max(w, h) / h if w and h else height  # size spans the long side
        ob.empty_image_offset = (-0.5, -0.5)
        ob.use_empty_image_alpha = True
        ob.color[3] = opacity
        ob.empty_image_depth = depth
    else:
        hw, hh = height * _aspect(img) / 2, height / 2
        me = bpy.data.meshes.new(img.name)
        me.from_pydata([(-hw, -hh, 0), (hw, -hh, 0), (hw, hh, 0), (-hw, hh, 0)], [], [(0, 1, 2, 3)])
        uv = me.uv_layers.new()
        for i, co in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            uv.data[i].uv = co
        me.materials.append(_material(img, opacity))
        ob = bpy.data.objects.new(img.name, me)
    ob.show_in_front = front
    ob.matrix_world = Matrix.Translation(location) @ rot.to_4x4()
    context.collection.objects.link(ob)
    return ob


def create_references(context, images, mode, rot, location, height, gap=0.1, **kw):
    """Create references for all images, side by side along the view's X axis, centered on `location`."""
    if isinstance(rot, Quaternion):
        rot = rot.to_matrix()
    rot = rot.to_3x3()
    widths = [height * _aspect(i) for i in images]
    x = -(sum(widths) + gap * height * (len(images) - 1)) / 2
    obs = []
    for img, w in zip(images, widths):
        center = Vector(location) + rot @ Vector((x + w / 2, 0, 0))
        obs.append(create_reference(context, img, mode, rot, center, height, **kw))
        x += w + gap * height
    if mode != 'CAMERA':
        for o in context.selected_objects:
            o.select_set(False)
        for o in obs:
            o.select_set(True)
        context.view_layer.objects.active = obs[-1]
    return obs


# ---------------------------------------------------------------- operators

class _Placement:
    mode: bpy.props.EnumProperty(name="Type", items=[
        ('EMPTY', "Reference Empty", "Image empty aligned to the view"),
        ('PLANE', "Image Plane", "Mesh plane with an emissive, transparent material"),
        ('CAMERA', "Camera Background", "Background image of the scene camera")], default='EMPTY')
    fill: bpy.props.FloatProperty(name="View Fill", default=0.5, min=0.01, max=10,
                                  description="Height as a fraction of the visible view height at the 3D cursor")
    opacity: bpy.props.FloatProperty(name="Opacity", default=0.5, min=0, max=1, subtype='FACTOR')
    depth: bpy.props.EnumProperty(name="Depth", items=[
        ('DEFAULT', "Default", "Sort with the scene"), ('FRONT', "Front", "Draw in front"),
        ('BACK', "Back", "Draw behind everything")], default='DEFAULT')
    front: bpy.props.BoolProperty(name="Show In Front", default=False)

    def place(self, context, images):
        rot, height = view_info(context, self.fill)
        try:
            obs = create_references(context, images, self.mode, rot, context.scene.cursor.location, height,
                                    opacity=self.opacity, depth=self.depth, front=self.front)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Added {len(images)} reference(s)" if obs else "Nothing added")
        return {'FINISHED'}


class PR_OT_paste(_Placement, bpy.types.Operator):
    bl_idname = "paste_reference.paste"
    bl_label = "Paste Image as Reference"
    bl_description = ("Paste the clipboard image as a reference empty, image plane or camera background, "
                      "saved to //references (or packed when the file is unsaved)")
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})  # redo reuses the saved file

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        path = bpy.path.abspath(self.filepath) if self.filepath else ""
        if path and os.path.isfile(path):
            img = bpy.data.images.load(path, check_existing=True)
        else:
            base = bpy.path.abspath("//references") if bpy.data.filepath else None
            img = blender_paste(context)
            if img:
                persist(img, base)
            else:
                data = external_paste()
                img = image_from_png(data, base) if data else None
            if img is None:
                self.report({'ERROR'}, NO_IMAGE)
                return {'CANCELLED'}
            self.filepath = img.filepath if not img.packed_file else ""
        return self.place(context, [img])


class PR_OT_load(_Placement, bpy.types.Operator):
    bl_idname = "paste_reference.load"
    bl_label = "Image Files as References"
    bl_description = "Load one or more image files as references, arranged side by side"
    bl_options = {'REGISTER', 'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        images = []
        for f in self.files:
            try:
                images.append(bpy.data.images.load(os.path.join(self.directory, f.name), check_existing=True))
            except RuntimeError as e:
                self.report({'WARNING'}, str(e))
        if not images:
            self.report({'ERROR'}, "No images loaded")
            return {'CANCELLED'}
        return self.place(context, images)


def reference_empties(scene):
    return [o for o in scene.objects if o.type == 'EMPTY' and o.empty_display_type == 'IMAGE']


class PR_OT_set_all(bpy.types.Operator):
    bl_idname = "paste_reference.set_all"
    bl_label = "Set All References"
    bl_description = "Hide, show, lock or unlock all reference image empties in the scene"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=[
        ('HIDE', "Hide", ""), ('SHOW', "Show", ""), ('LOCK', "Lock", ""), ('UNLOCK', "Unlock", "")])

    def execute(self, context):
        for ob in reference_empties(context.scene):
            if self.action in {'HIDE', 'SHOW'}:
                ob.hide_set(self.action == 'HIDE')
            else:
                ob.hide_select = self.action == 'LOCK'
        return {'FINISHED'}


class PR_PT_panel(bpy.types.Panel):
    bl_label = "References"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "View"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        lay = self.layout
        row = lay.row(align=True)
        row.operator("paste_reference.paste", icon='PASTEDOWN', text="Paste")
        row.operator("paste_reference.load", icon='FILE_IMAGE', text="Load")
        refs = reference_empties(context.scene)
        if not refs:
            lay.label(text="No reference empties")
            return
        row = lay.row(align=True)
        for action, icon in (('HIDE', 'HIDE_ON'), ('SHOW', 'HIDE_OFF'),
                             ('LOCK', 'RESTRICT_SELECT_ON'), ('UNLOCK', 'RESTRICT_SELECT_OFF')):
            row.operator("paste_reference.set_all", text=action.title(), icon=icon).action = action
        col = lay.column(align=True)
        for ob in refs:
            row = col.row(align=True)
            row.label(text=ob.name, icon='IMAGE_REFERENCE')
            row.prop(ob, "color", index=3, text="", slider=True)
            row.prop(ob, "show_in_front", text="", icon='XRAY')
            row.prop(ob, "show_empty_image_perspective", text="", icon='VIEW_PERSPECTIVE')
            row.prop(ob, "hide_select", text="", emboss=False)


def menu(self, context):
    self.layout.operator("paste_reference.paste", icon='PASTEDOWN')
    self.layout.operator("paste_reference.load", icon='FILE_IMAGE')


def view_menu(self, context):
    self.layout.separator()
    self.layout.operator("paste_reference.paste", icon='PASTEDOWN')


_classes = (PR_OT_paste, PR_OT_load, PR_OT_set_all, PR_PT_panel)
_keymaps = []


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_image_add.append(menu)
    bpy.types.VIEW3D_MT_view.append(view_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:  # Ctrl+Shift+V is free in Object Mode (used in Pose/GP/editors only)
        km = kc.keymaps.new("Object Mode", space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new(PR_OT_paste.bl_idname, 'V', 'PRESS', ctrl=True, shift=True)))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    bpy.types.VIEW3D_MT_view.remove(view_menu)
    bpy.types.VIEW3D_MT_image_add.remove(menu)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
