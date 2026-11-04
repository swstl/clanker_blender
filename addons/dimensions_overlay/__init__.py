"""Dimensions Overlay: live world-space size of the selection drawn in the viewport."""
import blf
import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

_handle = [None]


def selection_bounds(objects, depsgraph=None):
    """World-space (min, max) over evaluated geometry of objects (bounding boxes)."""
    pts = []
    for ob in objects:
        ev = ob.evaluated_get(depsgraph) if depsgraph else ob
        pts += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    if not pts:
        return None
    mn = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
    mx = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
    return mn, mx


def measure_lines(mn, mx):
    """[(axis, start, end)] edges along X, Y, Z from the min corner."""
    return [("X", mn, Vector((mx.x, mn.y, mn.z))),
            ("Y", mn, Vector((mn.x, mx.y, mn.z))),
            ("Z", mn, Vector((mn.x, mn.y, mx.z)))]


def fmt(scene, value):
    us = scene.unit_settings
    if us.system == 'NONE':
        return f"{value:.3f}"
    return bpy.utils.units.to_string(us.system, 'LENGTH', value * us.scale_length, precision=3,
                                     split_unit=us.use_separate)


COLORS = {"X": (1, 0.3, 0.3, 1), "Y": (0.5, 1, 0.3, 1), "Z": (0.3, 0.6, 1, 1), "D": (1, 1, 0.3, 1)}


def _draw():
    ctx = bpy.context
    if not ctx.scene.dimensions_overlay:
        return
    obs = [o for o in ctx.selected_objects if o.type not in {'CAMERA', 'LIGHT'}]
    if not obs:
        return
    region, r3d = ctx.region, ctx.region_data
    b = selection_bounds(obs, ctx.evaluated_depsgraph_get())
    lines = [(a, s, e, (e - s).length) for a, s, e in measure_lines(*b)]
    if len(obs) == 2:
        p, q = obs[0].matrix_world.translation, obs[1].matrix_world.translation
        lines.append(("D", p, q, (q - p).length))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.line_width_set(2.0)
    blf.size(0, 13 * ctx.preferences.view.ui_scale)
    for axis, s, e, length in lines:
        s2, e2 = location_3d_to_region_2d(region, r3d, s), location_3d_to_region_2d(region, r3d, e)
        if s2 is None or e2 is None:
            continue
        batch = batch_for_shader(shader, 'LINES', {"pos": [s2, e2]})
        shader.bind()
        shader.uniform_float("color", COLORS[axis])
        batch.draw(shader)
        mid = (s2 + e2) / 2
        blf.color(0, *COLORS[axis])
        blf.position(0, mid.x + 6, mid.y + 6, 0)
        blf.draw(0, f"{axis if axis != 'D' else 'Dist'} {fmt(ctx.scene, length)}")
    gpu.state.line_width_set(1.0)


def overlay_toggle(self, context):
    self.layout.prop(context.scene, "dimensions_overlay")


def register():
    bpy.types.Scene.dimensions_overlay = bpy.props.BoolProperty(
        name="Dimensions", description="Draw width/depth/height of the selection (and distance between 2 objects)")
    _handle[0] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    bpy.types.VIEW3D_PT_overlay_object.append(overlay_toggle)


def unregister():
    bpy.types.VIEW3D_PT_overlay_object.remove(overlay_toggle)
    if _handle[0]:
        bpy.types.SpaceView3D.draw_handler_remove(_handle[0], 'WINDOW')
        _handle[0] = None
    del bpy.types.Scene.dimensions_overlay
