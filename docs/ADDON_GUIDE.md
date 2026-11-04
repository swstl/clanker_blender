# Add-on conventions

- One folder per add-on: `addons/<id>/` with `blender_manifest.toml` and `__init__.py`
  (more modules allowed, use relative imports). `id` = folder name, snake_case.
- Manifest: `schema_version = "1.0.0"`, `maintainer = "swstl"`, `license = ["SPDX:GPL-3.0-or-later"]`,
  `blender_version_min = "4.2.0"` (raise it if you use newer API, e.g. layered actions need "4.4.0"),
  declare `[permissions]` only when used (files, network, clipboard).
- Prefix every class/operator id with a short add-on prefix to avoid clashes
  (`bl_idname = "<id>.<verb>"`). Add-on preferences use `bl_idname = __package__`.
- Must register AND unregister cleanly (the test runner disables and re-enables every add-on).
  Remove handlers, timers, keymaps and draw handlers in `unregister()`.
- UI: put panels in the sidebar (N panel) under a tab named after the area, or extend existing
  menus with `.append()`. Keep it discoverable: every operator gets a `bl_description`.
- Operators that change data: `bl_options = {'REGISTER', 'UNDO'}`.
- Tests: `tests/test_<id>.py` with plain `test_*()` functions using `assert`. They run inside Blender
  in background mode with the add-on enabled. Get the module with
  `importlib.import_module("bl_ext.clanker.<id>")`. Call operators directly
  (`bpy.ops.<id>.<op>()`) — use `bpy.context.temp_override(...)` when an operator needs a specific
  area/object. Test the core logic, not the drawing.
- Run `./scripts/test.sh <id>` until it prints PASSED.

## Blender 5.x API gotchas (verified on 5.2.3)

- `Action.fcurves` / `Action.groups` are gone (5.0). Use
  `bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot).fcurves`
  (or `animdata_get_channelbag_for_assigned_slot`). After assigning `anim_data.action = act`,
  also set `anim_data.action_slot = act.slots[0]`. One layer / one strip per Action: real
  animation layering still needs NLA.
- `bpy.props` values are no longer reachable via `id["prop"]` (5.0); use attribute access.
- `bgl` is gone. Draw with `gpu` builtin shaders: `UNIFORM_COLOR`, `FLAT_COLOR`, `SMOOTH_COLOR`,
  `POLYLINE_*` (wide lines), `POINT_*` (points), `IMAGE*`. No `3D_*` names.
  In `--background` call `gpu.init()` first (5.2) or skip drawing.
- Nodes: `tree.interface.new_socket(...)`; `scene.node_tree` -> `scene.compositing_node_group`.
- VSE: `Sequence` -> `Strip`, `sequence_editor.strips`. EEVEE engine id is `'BLENDER_EEVEE'`.
- Grease Pencil v3 is `bpy.types.GreasePencil`; old GP types are now `Annotation*`.
- Bone select flags moved to pose bones (`pbone.select`) in 5.0.
- Manifest `tagline` max 64 chars, must not end with punctuation (same for permission reasons).
  Valid tags: 3D View, Add Curve, Add Mesh, Animation, Bake, Camera, Compositing, Development,
  Game Engine, Geometry Nodes, Grease Pencil, Import-Export, Lighting, Material, Mesh, Modeling,
  Node, Object, Paint, Physics, Pipeline, Render, Rigging, Scene, Sculpt, Sequencer, System,
  Text Editor, Tracking, User Interface, UV.
- Native already (don't duplicate): batch rename (Ctrl+F2), Copy Global Transform, pose library,
  breakdowner/blend-to-neighbor, Gaussian smooth, bone collections, Collection Exporters (4.2),
  Save Incremental (Ctrl+Alt+S), recursive purge.
- Some operators ignore `temp_override(object=...)` and act only on the real view-layer
  selection (e.g. `vertex_group_clean`, `vertex_group_limit_total`,
  `material_slot_remove_unused`); they silently return FINISHED. Check results or implement directly.
- `FILE_PATH`/`DIR_PATH` string props: add `options={'PATH_SUPPORTS_BLEND_RELATIVE'}` for `//` paths (5.2).
- Brush weight (5.x): `tool_settings.weight_paint.unified_paint_settings.weight`.
- `view_layer.objects` can be stale right after linking objects: call `view_layer.update()`.
- Exporter "selected only" flags differ: FBX/glTF `use_selection`, OBJ `export_selected_objects`,
  USD `selected_objects_only`.
