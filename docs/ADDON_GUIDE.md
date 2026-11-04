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
