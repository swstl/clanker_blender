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
| Apply Modifiers Keep Shape Keys | Apply Subdivision/Mirror/Solidify/Bevel... on meshes with shape keys, keeping key settings and drivers. Object > Apply, or button in Modifiers tab. |
| Render Queue | Queue render jobs (per camera/marker, overrides, path tokens), render in background Blender processes or export a shell script; Draft/Preview/Final presets. Properties > Output. |
| Project Files | External files report (missing in red), collect outside files into textures/ sounds/ … with relative paths, one-click project folders + render output path. File > Project Files. |
| Mesh Check | Counts n-gons, tris, non-manifold, loose geometry, zero-area faces, overlapping verts, flipped normals and poles; one click to select or fix each. N > Edit. |
| Edge Weights Pie | Ctrl+Alt+E pie in Edit Mesh: exact bevel weight / crease values (0.25–1), mark/clear sharp and seam, bevel+sharp combo. |
| Texture Set Loader | Pick a folder or files of PBR textures and build one Principled material per set (ORM packing, DirectX normals, displacement, box projection, assign or mark as asset). File > Import. |
| Animation Layers | Layer stack on top of NLA: add/remove/reorder layers, mute, solo, lock, influence (keyable), blend mode; click a layer to key into it with upper layers still visible; merge down, bake to base. N > Animation (3D View, Graph, Dope Sheet). |
| Mesh Onion Skin | Ghosts past/future poses of animated meshes (or a rig's meshes) every N frames or at keyframes, colored and faded, auto-updating. N > Animation. |
| Quick Boolean | Ctrl+Numpad -/+/*// = Difference/Union/Intersect/Slice; cutters become wire, render-hidden, parented and collected in 'Cutters'; apply all + delete cutters. Object > Quick Boolean. |
| Key Tweener | AnimAide-style keyframe sliders: tween, blend to neighbor, ease, push/pull, scale average, blend to default, smooth, time offset. Drag or click presets. N > Animation. |
| Modifier Tools | Copy whole stack to selected, toggle viewport/render by type, apply all but Armature, remove by type, fix stack order, find objects by modifier type, Weighted Normal / Smooth by Angle quick add. Properties > Modifiers. |
| Texture Auto Reload | Watches image files and reloads them when saved in Photoshop/Krita/Substance; never discards unsaved paint. Image > Auto Reload. |
| Bone Tools | Fix L/R bone naming (vertex groups follow), strip prefixes like mixamorig:, bone chains from edge paths or curves, roll to cursor, numbered subdivide, deform/skinning checks, rig-to-rig Copy Transforms and bake. N > Rig. |
| Shape Key Tools | Split a shape key into .L/.R with a soft center blend (halves sum to original), remove empty keys, reset all values. Shape key specials menu. |
| Quick Retarget | Auto-map bones (Mixamo, Rigify, custom) and bake animation from one armature onto another, A/T-pose aware, root motion scaling, batch all actions, JSON map presets. N > Retarget. |
| Action Manager | List all (or compatible) actions, click to assign with right slot + frame range, prev/next/play, auto fake-user on save, range edit, push/stash, batch rename, remove unused. N > Animation. |
| Quick Lattice | Fit a lattice around selected objects or Edit Mode vertices (auto vertex group), edit it, then apply-and-delete or remove. Right-click menu. |
| Select Through | Box/lasso select occluded Edit Mesh verts/edges/faces without X-Ray (Shift+Alt+B / Shift+Alt+L), Shift extend, Ctrl subtract, optional front-faces-only. Select menu. |

## Development

- `./scripts/test.sh [addon_id ...]` runs headless tests from `tests/test_<addon_id>.py` (Blender in background, factory settings).
- See `docs/ADDON_GUIDE.md` for conventions.
