"""Auto Key Alert: draw a colored frame + label in every 3D viewport while auto keying is enabled."""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

_handle = [None]


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def should_draw(context):
    ts = context.scene.tool_settings if context.scene else None
    return bool(ts and ts.use_keyframe_insert_auto)


def frame_rects(w, h, t):
    """Four rectangles (x0, y0, x1, y1) forming a border of thickness t inside a w*h region."""
    return [(0, 0, w, t), (0, h - t, w, h), (0, 0, t, h), (w - t, 0, w, h)]


def _draw():
    ctx = bpy.context
    if not should_draw(ctx):
        return
    p = _prefs()
    region = ctx.region
    verts, idx = [], []
    for x0, y0, x1, y1 in frame_rects(region.width, region.height, p.thickness):
        i = len(verts)
        verts += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        idx += [(i, i + 1, i + 2), (i, i + 2, i + 3)]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=idx)
    gpu.state.blend_set('ALPHA')
    shader.bind()
    shader.uniform_float("color", tuple(p.color))
    batch.draw(shader)
    gpu.state.blend_set('NONE')
    if p.show_label:
        import blf
        blf.size(0, 14 * ctx.preferences.view.ui_scale)
        blf.color(0, *p.color[:3], 1.0)
        tw, _ = blf.dimensions(0, "AUTO KEY")
        blf.position(0, (region.width - tw) / 2, p.thickness + 8, 0)
        blf.draw(0, "AUTO KEY")


class AKA_Prefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    color: bpy.props.FloatVectorProperty(name="Color", subtype='COLOR', size=4, min=0, max=1,
                                         default=(0.9, 0.15, 0.15, 0.8))
    thickness: bpy.props.IntProperty(name="Thickness (px)", default=4, min=1, max=40)
    show_label: bpy.props.BoolProperty(name="Show 'AUTO KEY' label", default=True)

    def draw(self, context):
        col = self.layout.column()
        for p in ("color", "thickness", "show_label"):
            col.prop(self, p)


def register():
    bpy.utils.register_class(AKA_Prefs)
    _handle[0] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    if _handle[0]:
        bpy.types.SpaceView3D.draw_handler_remove(_handle[0], 'WINDOW')
        _handle[0] = None
    bpy.utils.unregister_class(AKA_Prefs)
