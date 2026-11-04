# Clanker QoL — quality-of-life add-ons for Blender

A collection of small, focused Blender add-ons (extension format, Blender 4.2+ / tested on 5.2 LTS)
that fix everyday annoyances. Each add-on lives in its own folder under `addons/` and can be
enabled or disabled on its own.

## Install (recommended: whole repo, auto-updates with `git pull`)

```bash
git clone git@github.com:swstl/clanker_blender.git
cd clanker_blender
./scripts/install.sh            # registers addons/ as the "Clanker QoL" repository and enables all add-ons
```

Set `BLENDER=/path/to/blender` if Blender is not on `PATH` (Steam installs are found automatically).
After `git pull`, restart Blender (re-run `install.sh` to enable newly added add-ons).
Disable single add-ons in *Edit > Preferences > Add-ons* (filter by "Clanker").

### Manual, without the script
*Edit > Preferences > Get Extensions > Repositories (dropdown) > + > Add Local Repository*,
point it at this repo's `addons/` folder, then enable the add-ons you want.

### As zip files
`./scripts/build.sh` writes one `.zip` per add-on to `dist/`. Drag a zip into Blender or use
*Install from Disk*.

## Add-ons

| Add-on | What it does |
|---|---|
| Render Notify | Desktop notification + sound when a render finishes (or is cancelled). |
| Time Tools | Insert/remove frames at the playhead (keys, NLA, markers), change FPS keeping timing, snap subframe keys. Dope Sheet / Graph Editor > Key menu. |
| Heavy Object Finder | Ranks visible objects by triangles after modifiers; one-click bounds/wire display or heavy modifiers off in viewport. 3D View > N > View. |
| Version Snapshots | Timed timestamped backups in `_versions/` next to the .blend, keep last N, named milestones, restore from 3D View > N > View. |
| Camera Manager | All cameras in one list, one-click switch/look-through, next/prev, per-camera resolution (portrait + landscape in one file), bind to marker, camera from view. 3D View > N > View. |
| Collection Tools | Move selection to new collection named after active, isolate/show all, sort A-Z, remove empty, select contents, instance at cursor. Outliner right-click > Collection Tools. |
| Batch Export | Export each object, hierarchy or collection to its own FBX/glTF/OBJ/USD file with Unity/Unreal/Godot presets, origin reset and separate animation files. 3D View > N > Export. |
| Weight Layers | Non-destructive weight painting layers per vertex group (Mix/Add/Subtract/Multiply/Max/Min, opacity, toggle), auto-composite, flatten, mirror layer. Properties > Data, or N > Weights in Weight Paint. |
| Weight Tools | Mirror all L/R vertex groups, remove empty/non-bone groups, one-click game-ready cleanup (clean, limit 4, normalize), select unweighted/over-influenced verts, brush weight presets. N > Weights. |
| Origin & Align | Set origins to any bounding-box point, cursor, selection (from Edit Mode) or face normal without moving geometry; align, distribute, drop to floor, stack. N > Align, or Alt+Shift+O pie. |
| Scene Cleanup | Merge .001 duplicate materials/node groups/images, remove unused/empty material slots, rename data to object, recursive purge, select objects with bad scale/no material/empty mesh. N > Cleanup. |

## Development

- `./scripts/test.sh [addon_id ...]` runs headless tests from `tests/test_<addon_id>.py` (Blender in background, factory settings).
- See `docs/ADDON_GUIDE.md` for conventions.
