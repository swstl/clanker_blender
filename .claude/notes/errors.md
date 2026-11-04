# Errors

- **`blender --command extension ...` loads all user add-ons**: put `--background --factory-startup` before `--command`. (2026-11-04)
- **Bash call with `ls -R` at end hung >120s**: `ls` is aliased (eza); avoid `ls -R` in scripts. (2026-11-04)
- **`object.vertex_group_clean` / `vertex_group_limit_total` ignore `temp_override(object=...)`**: they act on view-layer-selected objects only (silent no-op otherwise). `normalize_all` and `quantize` respect the override. Weight paint brush weight lives at `tool_settings.weight_paint.unified_paint_settings.weight` (5.x, no `tool_settings.unified_paint_settings`). (2026-11-04)
- **`ShaderNodeMix.inputs["A"]` returns the hidden Float socket** even with `data_type='RGBA'`: look up by identifier (`A_Color`, `B_Color`, `Result_Color`). New materials already have a node tree in 5.x (`use_nodes` deprecated). (2026-11-04)
- **`view_layer.objects` yields `None` right after `bpy.data.objects.remove()`** (stale until a view-layer update), e.g. in tests after `run.py` clean(): iterate `bpy.data.objects` / `scene.objects` instead. Draw callbacks can be smoke-tested headless with `gpu.init()` + `with gpu.types.GPUOffScreen(64,64).bind(): draw()`. (2026-11-04)
