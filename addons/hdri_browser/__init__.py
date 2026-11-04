"""HDRI Browser: thumbnail grid of HDRI folders; one click puts an HDRI on the world."""
import hashlib
import os
import re

import bpy
import bpy.utils.previews
import numpy as np

EXTS = {".hdr", ".exr", ".jpg", ".jpeg", ".png"}
FLOAT_EXTS = {".hdr", ".exr"}
RES_RE = re.compile(r"[_\-. ](\d+)k$", re.I)
THUMB_W, THUMB_H = 256, 128
ENV, MAPPING = "HDRI_Env", "HDRI_Mapping"
RENDER_KEY, VIEW_KEY = "hdri_render_path", "hdri_view_path"

_entries = []   # [(name, {res: path})] sorted by name; res = "1k", "2k"... or "" (no token)
_queue = []     # entry names waiting for a thumbnail
_items = []     # enum items: Blender needs Python to keep the list alive
_pcoll = None


def _rnum(res):
    return int(res[:-1]) if res else 0


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# --- scanning ----------------------------------------------------------------

def scan(folders):
    """{base name: {res: path}} for all HDRIs below folders, resolution tokens stripped."""
    groups = {}
    for root in folders:
        root = bpy.path.abspath(root)
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                stem, ext = os.path.splitext(f)
                ext = ext.lower()
                if ext not in EXTS:
                    continue
                m = RES_RE.search(stem)
                base, res = (stem[:m.start()], m.group(1) + "k") if m else (stem, "")
                found = groups.setdefault(base, {})
                old = found.get(res)
                if old is None or ext in FLOAT_EXTS or os.path.splitext(old)[1].lower() not in FLOAT_EXTS:
                    found[res] = os.path.join(dirpath, f)  # an .hdr/.exr beats a .jpg/.png of the same size
    return groups


def pick(files, choice):
    """Path in files for a resolution choice: LOWEST, HIGHEST or '<n>K' (largest <= n, else smallest)."""
    keys = sorted(files, key=_rnum)
    if choice == 'HIGHEST':
        return files[keys[-1]]
    fit = [k for k in keys if choice != 'LOWEST' and _rnum(k) <= _rnum(choice)]
    return files[(fit or keys)[-1] if fit else keys[0]]


def refresh():
    """Rescan the folders, load cached thumbnails, queue missing ones."""
    global _entries
    _entries = sorted(scan([f.path for f in _prefs().folders if f.path]).items(), key=lambda e: e[0].lower())
    _pcoll.clear()
    _queue.clear()
    for name, files in _entries:
        src = pick(files, 'LOWEST')
        if _thumb_fresh(src):
            _pcoll.load(name, thumb_path(src), 'IMAGE')
        else:
            _queue.append(name)
    if _queue and not bpy.app.timers.is_registered(_thumb_timer):
        bpy.app.timers.register(_thumb_timer, first_interval=0.1)


# --- thumbnails --------------------------------------------------------------

def thumb_path(src):
    cache = bpy.utils.extension_path_user(__package__, path="thumbs", create=True)
    return os.path.join(cache, hashlib.md5(src.encode()).hexdigest()[:16] + ".png")


def _thumb_fresh(src):
    dst = thumb_path(src)
    return os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src)


def make_thumb(src, dst, exposure=1.0):
    """Scale src down, tonemap HDR data (Reinhard + gamma) and save it as a PNG at dst."""
    img = bpy.data.images.load(src, check_existing=False)
    try:
        img.scale(THUMB_W, THUMB_H)
        px = np.empty(THUMB_W * THUMB_H * 4, np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(-1, 4)
        if img.is_float:
            rgb = np.maximum(px[:, :3] * exposure, 0)
            px[:, :3] = (rgb / (1 + rgb)) ** (1 / 2.2)
        px[:, 3] = 1
        out = bpy.data.images.new("_hdri_thumb", THUMB_W, THUMB_H)  # byte image: pixels are written as-is
        try:
            out.pixels.foreach_set(np.clip(px, 0, 1).ravel())
            out.filepath_raw = dst
            out.file_format = 'PNG'
            out.save()
        finally:
            bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(img)


def thumb_step():
    """Make one queued thumbnail. Returns False when the queue is empty."""
    if not _queue:
        return False
    name = _queue.pop(0)
    files = dict(_entries).get(name)
    if files:
        src = pick(files, 'LOWEST')
        try:
            make_thumb(src, thumb_path(src))
            if name not in _pcoll:
                _pcoll.load(name, thumb_path(src), 'IMAGE')
        except (RuntimeError, OSError) as e:
            print(f"HDRI Browser: no thumbnail for {src}: {e}")
    return True


def generate_thumbs():
    """Make all queued thumbnails now (headless/tests)."""
    while thumb_step():
        pass


def _thumb_timer():
    thumb_step()
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return 0.05 if _queue else None


# --- applying ----------------------------------------------------------------

def world_setup(world):
    """(env, mapping, background) of world, reusing existing nodes; builds the chain if missing."""
    tree = world.node_tree
    nodes, links = tree.nodes, tree.links
    env = nodes.get(ENV) or next((n for n in nodes if n.bl_idname == 'ShaderNodeTexEnvironment'), None)
    if env is None:
        env = nodes.new('ShaderNodeTexEnvironment')
        env.location = (-300, 300)
    env.name = ENV
    mp = nodes.get(MAPPING)
    if mp is None and env.inputs["Vector"].is_linked:
        src = env.inputs["Vector"].links[0].from_node
        mp = src if src.bl_idname == 'ShaderNodeMapping' else None
    if mp is None:
        mp = nodes.new('ShaderNodeMapping')
        mp.location = env.location.x - 200, env.location.y
        tc = nodes.new('ShaderNodeTexCoord')
        tc.location = mp.location.x - 200, mp.location.y
        links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    mp.name = MAPPING
    if not env.inputs["Vector"].is_linked or env.inputs["Vector"].links[0].from_node != mp:
        links.new(mp.outputs["Vector"], env.inputs["Vector"])
    bg = next((n for n in nodes if n.bl_idname == 'ShaderNodeBackground'), None)
    if bg is None:
        bg = nodes.new('ShaderNodeBackground')
        bg.location = env.location.x + 300, env.location.y
    if not bg.inputs["Color"].is_linked or bg.inputs["Color"].links[0].from_node != env:
        links.new(env.outputs["Color"], bg.inputs["Color"])
    out = next((n for n in nodes if n.bl_idname == 'ShaderNodeOutputWorld'), None)
    if out is None:
        out = nodes.new('ShaderNodeOutputWorld')
        out.location = bg.location.x + 200, bg.location.y
    if not out.inputs["Surface"].is_linked:
        links.new(bg.outputs["Background"], out.inputs["Surface"])
    return env, mp, bg


def apply_hdri(world, view_path, render_path=None):
    """Load view_path on the world's environment node; render_path (if other) is swapped in at render time."""
    env, _mp, _bg = world_setup(world)
    old = env.image
    env.image = bpy.data.images.load(view_path, check_existing=True)
    if render_path and os.path.normpath(render_path) != os.path.normpath(view_path):
        env[RENDER_KEY] = render_path
    elif RENDER_KEY in env:
        del env[RENDER_KEY]
    if old and old != env.image and old.users == 0:
        bpy.data.images.remove(old)  # don't pile up 8k images while browsing
    return env


def apply_entry(context, name):
    files = dict(_entries).get(name)
    if not files:
        return None
    scene = context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    p = _prefs()
    view = pick(files, p.resolution)
    render = pick(files, 'HIGHEST') if p.lowres_viewport else view
    return apply_hdri(scene.world, view, render)


def _env(scene):
    w = scene.world
    return w.node_tree.nodes.get(ENV) if w and w.node_tree else None


@bpy.app.handlers.persistent
def _render_pre(scene, *_):
    env = _env(scene)
    if env and env.image and env.get(RENDER_KEY) and VIEW_KEY not in env:
        env[VIEW_KEY] = env.image.filepath
        env.image.filepath = env[RENDER_KEY]


@bpy.app.handlers.persistent
def _render_post(scene, *_):
    env = _env(scene)
    if env and env.image and VIEW_KEY in env:
        env.image.filepath = env[VIEW_KEY]
        del env[VIEW_KEY]


# --- properties --------------------------------------------------------------

def _visible(wm):
    flt = wm.hdri_browser_filter.lower()
    favs = {f.name for f in _prefs().favorites}
    return [i for i, (name, _f) in enumerate(_entries)
            if (flt in name.lower() and (not wm.hdri_browser_favorites_only or name in favs))
            or name == wm.hdri_browser_current]  # keep the active one so the enum stays valid


def _pick_items(self, context):
    _items.clear()
    for i in _visible(self):
        name, files = _entries[i]
        icon = _pcoll[name].icon_id if name in _pcoll else 'WORLD'
        res = ", ".join(r.upper() or "?" for r in sorted(files, key=_rnum))
        _items.append((name, name, res, icon, i))
    return _items


def _pick_get(self):
    return next((i for i, (n, _f) in enumerate(_entries) if n == self.hdri_browser_current), 0)


def _pick_set(self, value):
    if 0 <= value < len(_entries):
        self.hdri_browser_current = _entries[value][0]
        apply_entry(bpy.context, self.hdri_browser_current)


def _reapply(self, context):
    if context.window_manager.hdri_browser_current:
        apply_entry(context, context.window_manager.hdri_browser_current)


class HB_folder(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty(name="Folder", subtype='DIR_PATH',
                                   update=lambda s, c: refresh())


class HB_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    folders: bpy.props.CollectionProperty(type=HB_folder)
    favorites: bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
    resolution: bpy.props.EnumProperty(
        name="Resolution", default='2K', update=_reapply,
        description="Resolution loaded in the viewport (largest available up to this)",
        items=[('LOWEST', "Lowest", ""), ('1K', "1K", ""), ('2K', "2K", ""), ('4K', "4K", ""),
               ('8K', "8K", ""), ('HIGHEST', "Highest", "")])
    lowres_viewport: bpy.props.BoolProperty(
        name="Full Res for Render", default=True, update=_reapply,
        description="Low-res in viewport, full-res for render: the highest resolution is swapped in "
                    "while rendering and restored afterwards")

    def draw(self, context):
        lay = self.layout
        lay.label(text="HDRI folders (searched recursively for .hdr .exr .jpg .png):")
        for i, f in enumerate(self.folders):
            row = lay.row(align=True)
            row.prop(f, "path", text="")
            row.operator("hdri_browser.remove_folder", text="", icon='X').index = i
        lay.operator("hdri_browser.add_folder", icon='ADD')
        row = lay.row()
        row.prop(self, "resolution")
        row.prop(self, "lowres_viewport")


# --- operators ---------------------------------------------------------------

class HB_OT_add_folder(bpy.types.Operator):
    bl_idname = "hdri_browser.add_folder"
    bl_label = "Add HDRI Folder"
    bl_description = "Add a folder to search for HDRIs"

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.directory:
            return {'CANCELLED'}
        _prefs().folders.add().path = self.directory  # update callback rescans
        context.preferences.is_dirty = True
        return {'FINISHED'}


class HB_OT_remove_folder(bpy.types.Operator):
    bl_idname = "hdri_browser.remove_folder"
    bl_label = "Remove Folder"
    bl_description = "Stop searching this folder"

    index: bpy.props.IntProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        _prefs().folders.remove(self.index)
        context.preferences.is_dirty = True
        refresh()
        return {'FINISHED'}


class HB_OT_refresh(bpy.types.Operator):
    bl_idname = "hdri_browser.refresh"
    bl_label = "Rescan Folders"
    bl_description = "Rescan the HDRI folders and make missing thumbnails"

    def execute(self, context):
        refresh()
        self.report({'INFO'}, f"{len(_entries)} HDRIs")
        return {'FINISHED'}


class HB_OT_step(bpy.types.Operator):
    bl_idname = "hdri_browser.step"
    bl_label = "Previous/Next HDRI"
    bl_description = "Switch to the previous or next HDRI in the (filtered) list"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.IntProperty(default=1, options={'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        vis = _visible(wm)
        if not vis:
            return {'CANCELLED'}
        cur = _pick_get(wm)
        pos = vis.index(cur) + self.direction if cur in vis else 0
        _pick_set(wm, vis[pos % len(vis)])
        return {'FINISHED'}


class HB_OT_favorite(bpy.types.Operator):
    bl_idname = "hdri_browser.favorite"
    bl_label = "Toggle Favorite"
    bl_description = "Star or unstar this HDRI"

    name: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        favs = _prefs().favorites
        i = favs.find(self.name)
        if i >= 0:
            favs.remove(i)
        elif self.name:
            favs.add().name = self.name
        context.preferences.is_dirty = True
        return {'FINISHED'}


class HB_PT_browser(bpy.types.Panel):
    bl_label = "HDRI Browser"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Light"

    def draw(self, context):
        lay = self.layout
        wm = context.window_manager
        p = _prefs()
        if not p.folders:
            lay.operator("hdri_browser.add_folder", icon='FILE_FOLDER')
            return
        row = lay.row(align=True)
        row.prop(wm, "hdri_browser_filter", text="", icon='VIEWZOOM')
        row.prop(wm, "hdri_browser_favorites_only", text="", icon='SOLO_ON')
        row.operator("hdri_browser.refresh", text="", icon='FILE_REFRESH')
        if not _entries:
            lay.label(text="No HDRIs found (Rescan?)")
            return
        lay.template_icon_view(wm, "hdri_browser_pick", show_labels=True, scale=7, scale_popup=6)
        row = lay.row(align=True)
        row.operator("hdri_browser.step", text="", icon='TRIA_LEFT').direction = -1
        cur = wm.hdri_browser_current
        row.label(text=cur or "Pick an HDRI")
        if cur:
            fav = p.favorites.find(cur) >= 0
            row.operator("hdri_browser.favorite", text="", icon='SOLO_ON' if fav else 'SOLO_OFF').name = cur
        row.operator("hdri_browser.step", text="", icon='TRIA_RIGHT').direction = 1
        row = lay.row(align=True)
        row.prop(p, "resolution", text="")
        row.prop(p, "lowres_viewport", toggle=True)
        if _queue:
            lay.label(text=f"Making thumbnails... {len(_queue)} left", icon='TIME')


_classes = (HB_folder, HB_Preferences, HB_OT_add_folder, HB_OT_remove_folder, HB_OT_refresh,
            HB_OT_step, HB_OT_favorite, HB_PT_browser)
_handlers = ((bpy.app.handlers.render_pre, _render_pre), (bpy.app.handlers.render_post, _render_post),
             (bpy.app.handlers.render_cancel, _render_post))


def _initial_scan():
    if __package__ in bpy.context.preferences.addons:
        refresh()


def register():
    global _pcoll
    _pcoll = bpy.utils.previews.new()
    for c in _classes:
        bpy.utils.register_class(c)
    WM = bpy.types.WindowManager
    WM.hdri_browser_current = bpy.props.StringProperty(name="HDRI")
    WM.hdri_browser_pick = bpy.props.EnumProperty(name="HDRI", items=_pick_items, get=_pick_get, set=_pick_set)
    WM.hdri_browser_filter = bpy.props.StringProperty(name="Search", options={'TEXTEDIT_UPDATE'})
    WM.hdri_browser_favorites_only = bpy.props.BoolProperty(name="Favorites Only",
                                                            description="Show only starred HDRIs")
    for lst, fn in _handlers:
        lst.append(fn)
    bpy.app.timers.register(_initial_scan, first_interval=0.5)


def unregister():
    global _pcoll
    for t in (_initial_scan, _thumb_timer):
        if bpy.app.timers.is_registered(t):
            bpy.app.timers.unregister(t)
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
    for p in ("hdri_browser_current", "hdri_browser_pick", "hdri_browser_filter", "hdri_browser_favorites_only"):
        delattr(bpy.types.WindowManager, p)
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
    bpy.utils.previews.remove(_pcoll)
    _pcoll = None
    _entries.clear()
    _queue.clear()
    _items.clear()
