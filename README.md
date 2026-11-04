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

## Add-ons (70)

### Animation

| Add-on | What it does |
|---|---|
| Animation Layers | Layer stack on top of NLA: add/remove/reorder layers, mute, solo, lock, influence (keyable), blend mode; click a layer to key into it with upper layers still visible; merge down, bake to base. N > Animation (3D View, Graph, Dope Sheet). |
| Key Tweener | AnimAide-style keyframe sliders: tween, blend to neighbor, ease, push/pull, scale average, blend to default, smooth, time offset. Drag or click presets. N > Animation. |
| Stepped Preview | Maya-style stepped playback toggle for blocking: hold each key (originals restored exactly) or sample every N frames via a removable Stepped modifier. Button in Dope Sheet/Graph playback header. |
| Playblast | One-click viewport animation preview to //playblasts/<file>_v###.mp4 with burn-ins (frame, camera, lens, marker, note); render settings swapped and always restored. View menu + N > View. |
| Space Switch | Dynamic parenting for objects and bones: add parents (Child Of), switch at current frame without popping, list/jump to switches, bake range back to world. N > Animation. |
| Pose Tools | Paste a pose over a frame range (flipped/additive), mirror animation L↔R with time offset (walk cycles), sparse visual bake (keys only / every N), key whole character, reset incl. custom props, select keyed/constrained. Pose Mode N > Animation. |
| Action Manager | List all (or compatible) actions, click to assign with right slot + frame range, prev/next/play, auto fake-user on save, range edit, push/stash, batch rename, remove unused. N > Animation. |
| Time Tools | Insert/remove frames at the playhead (keys, NLA, markers), change FPS keeping timing, snap subframe keys. Dope Sheet / Graph Editor > Key menu. |
| Mesh Onion Skin | Ghosts past/future poses of animated meshes (or a rig's meshes) every N frames or at keyframes, colored and faded, auto-updating. N > Animation. |
| Motion Trail | Editable motion paths for objects and pose bones: drag keys in the viewport, Ctrl+click path to add a key, X to delete; fading trail with frame ticks. N > Animation. |
| Quick Retarget | Auto-map bones (Mixamo, Rigify, custom) and bake animation from one armature onto another, A/T-pose aware, root motion scaling, batch all actions, JSON map presets. N > Retarget. |
| Auto Key Alert | Red frame + 'AUTO KEY' label in every 3D viewport while auto keying is on (color/thickness in prefs). |
| Shape Key Tools | Split a shape key into .L/.R with a soft center blend (halves sum to original), remove empty keys, reset all values. Shape key specials menu. |

### Rigging & Weights

| Add-on | What it does |
|---|---|
| Weight Layers | Non-destructive weight painting layers per vertex group (Mix/Add/Subtract/Multiply/Max/Min, opacity, toggle), auto-composite, flatten, mirror layer. Properties > Data, or N > Weights in Weight Paint. |
| Weight Tools | Mirror all L/R vertex groups, remove empty/non-bone groups, one-click game-ready cleanup (clean, limit 4, normalize), select unweighted/over-influenced verts, brush weight presets. N > Weights. |
| Bone Tools | Fix L/R bone naming (vertex groups follow), strip prefixes like mixamorig:, bone chains from edge paths or curves, roll to cursor, numbered subdivide, deform/skinning checks, rig-to-rig Copy Transforms and bake. N > Rig. |
| Tension Map | Realtime stretch/compression attributes via Geometry Nodes (render-safe), blue/white/red preview material, bake to stretch/compress vertex groups for wrinkle maps. N > Rig. |
| IK/FK Snap | Snap FK↔IK on any custom rig (not only Rigify): auto-detect chains from IK constraints, mirror L/R, pole placement, switch property, bake over a frame range. Pose Mode N > Rig. |

### Modeling & UV

| Add-on | What it does |
|---|---|
| Origin & Align | Set origins to any bounding-box point, cursor, selection (from Edit Mode) or face normal without moving geometry; align, distribute, drop to floor, stack. N > Align, or Alt+Shift+O pie. |
| Group Under Empty | Maya-style transform groups: Ctrl+Alt+P parents the selection under an empty (bottom/center/cursor/active pivot, keeps world transforms and nested hierarchies), Shift+Alt+P ungroups, select whole group. Object > Parent. |
| Curve Tools | Live Geometry Nodes objects-along-curve (count/spacing/corners, upright/tangent, random pick, jitter, realize) and sagging cables between objects or verts with hooks, end caps and bundles. N > Edit, Add > Curve. |
| Quick Boolean | Ctrl+Numpad -/+/*// = Difference/Union/Intersect/Slice; cutters become wire, render-hidden, parented and collected in 'Cutters'; apply all + delete cutters. Object > Quick Boolean. |
| Quick Lattice | Fit a lattice around selected objects or Edit Mode vertices (auto vertex group), edit it, then apply-and-delete or remove. Right-click menu. |
| Edge Weights Pie | Ctrl+Alt+E pie in Edit Mesh: exact bevel weight / crease values (0.25–1), mark/clear sharp and seam, bevel+sharp combo. |
| Mesh Check | Counts n-gons, tris, non-manifold, loose geometry, zero-area faces, overlapping verts, flipped normals and poles; one click to select or fix each. N > Edit. |
| Select Through | Box/lasso select occluded Edit Mesh verts/edges/faces without X-Ray (Shift+Alt+B / Shift+Alt+L), Shift extend, Ctrl subtract, optional front-faces-only. Select menu. |
| Modifier Tools | Copy whole stack to selected, toggle viewport/render by type, apply all but Armature, remove by type, fix stack order, find objects by modifier type, Weighted Normal / Smooth by Angle quick add. Properties > Modifiers. |
| Apply Modifiers Keep Shape Keys | Apply Subdivision/Mirror/Solidify/Bevel... on meshes with shape keys, keeping key settings and drivers. Object > Apply, or button in Modifiers tab. |
| UV Tools | Seams by angle/sharp, batch unwrap selected objects with equal texel density, texel density measure/set/copy, checker material toggle (restores originals), straighten to grid, select flipped, UV map sync across objects. UV Editor N > UV Tools. |
| Dimensions Overlay | Live X/Y/Z size of the selection (scene units) and distance between two selected objects drawn in the viewport. Toggle in Viewport Overlays > Objects. |

### Sculpting & Physics

| Add-on | What it does |
|---|---|
| Sculpt Masks | Mask / face sets ↔ vertex groups, mask from color attribute, stored named masks with replace/add/subtract/intersect, quick invert/clear/smooth. Sculpt Mode N > Sculpt. |
| Remesh Tools | Voxel/Quadriflow remesh with hidden backup, detail reprojection (shrinkwrap or multires), face-count estimate, restore from backup, quick decimated LOD copy. N > Sculpt. |
| Physics Helpers | Drop/settle objects with a temporary rigid body sim (cleans up after itself) or raycast drop to surface; quick cloth with presets, pin group from selection, hook, collision, apply/shape key at frame. N > Physics. |

### Materials & Textures

| Add-on | What it does |
|---|---|
| Texture Set Loader | Pick a folder or files of PBR textures and build one Principled material per set (ORM packing, DirectX normals, displacement, box projection, assign or mark as asset). File > Import. |
| Image Palette | k-means main colors of any image → Palette (set for paint modes), optional swatch materials, hex codes copied to clipboard. Image Editor N > Image. |
| Material Swatches | Preview grid of all materials with filter; click assigns to selected objects (replace/active slot/add) or Edit Mode faces, select users, new random swatch, distinct random viewport colors. N > Material. |
| Batch Material Edit | Set/multiply/add Principled inputs (roughness, metallic, IOR, alpha, emission…) and backface culling / render method across selected or all materials, skipping texture-linked inputs; sync viewport colors from base color. N > Material. |
| Bake Helper | One-click Cycles PBR baking (color, roughness, metallic, normal, AO, emission, ORM...) per object, atlas or high-to-low, auto image nodes + full restore, baked material. N > Bake or Properties > Render. |
| Texture Auto Reload | Watches image files and reloads them when saved in Photoshop/Krita/Substance; never discards unsaved paint. Image > Auto Reload. |
| Node Finder | File-wide node search (materials, worlds, GN, compositor, nested groups) with jump-to, where-used, missing images, node group swap and unused-node cleanup. Node Editor N > Find. |
| Node Tidy | Straighten links, align/distribute, reroute chain straighten/remove/merge, quick pastel frames, grid snap in any node editor. Node > Tidy, N > Node, Shift+Alt+Q pie. |

### Rendering & Cameras

| Add-on | What it does |
|---|---|
| Render Queue | Queue render jobs (per camera/marker, overrides, path tokens), render in background Blender processes or export a shell script; Draft/Preview/Final presets. Properties > Output. |
| Product Stage | One click: curved cyclorama backdrop, key/fill/rim area lights (power scales with size) and a framed camera around the selection; optional shadow catcher; rebuild or remove. N > Light. |
| Render Notify | Desktop notification + sound when a render finishes (or is cancelled). |
| Render Passes to Files | One click wires every enabled render pass (AOVs, Cryptomatte) per view layer to a multilayer EXR or per-pass files, re-run safe, optional denoise, pass toggle grid. Properties > View Layer. |
| Render Border from Selection | Fit the render region to selected objects from the camera (with padding), or follow them every frame for animated crops. View > View Regions, Properties > Output. |
| Camera Manager | All cameras in one list, one-click switch/look-through, next/prev, per-camera resolution (portrait + landscape in one file), bind to marker, camera from view. 3D View > N > View. |
| Light Manager | All lights + emissive meshes in one list with solo, visibility, color, power; Kelvin presets, aim at cursor/active, place at distance, exposure x0.5/x2, HDRI strength and rotation. N > Light. |
| Turntable | Camera framed on the selection with a seamless 360° loop (camera orbit or objects spin), elevation, margin, lens. Add > Camera > Add Turntable. |

### Scene, Files & Export

| Add-on | What it does |
|---|---|
| Batch Export | Export each object, hierarchy or collection to its own FBX/glTF/OBJ/USD file with Unity/Unreal/Godot presets, origin reset and separate animation files. 3D View > N > Export. |
| Game Ready | Engine-named collision meshes (Unreal UCX_/UBX_/USP_, Godot -convcolonly, Unity _Collider) from convex hull/box/sphere, _LOD0.._LODn decimated copies, SOCKET_ empties at cursor, non-overlapping lightmap UV. N > Export. |
| Randomizer | Seeded random materials (per-object slots, shared mesh safe), object colors, scale, rotation, and thin-out selection for many objects. N > Edit. |
| Scene Cleanup | Merge .001 duplicate materials/node groups/images, remove unused/empty material slots, rename data to object, recursive purge, select objects with bad scale/no material/empty mesh. N > Cleanup. |
| Collection Tools | Move selection to new collection named after active, isolate/show all, sort A-Z, remove empty, select contents, instance at cursor. Outliner right-click > Collection Tools. |
| Project Files | External files report (missing in red), collect outside files into textures/ sounds/ … with relative paths, one-click project folders + render output path. File > Project Files. |
| Version Snapshots | Timed timestamped backups in `_versions/` next to the .blend, keep last N, named milestones, restore from 3D View > N > View. |
| Asset Helper | Collections → asset catalogs (merges cats file, keeps UUIDs), batch mark/clear objects/materials/node groups/actions with tags, studio-lit custom previews, register folder as asset library. N > Assets. |
| Paste Reference | Ctrl+Shift+V pastes a clipboard image as a view-aligned reference empty, image plane or camera background (saved to //references/); load image files as references side by side; reference list with opacity/lock. Object Mode. |

### Viewport & UI

| Add-on | What it does |
|---|---|
| Object Selection Sets | Named object selection sets stored in the .blend, across collections: select/select only, assign, remove, hide/show; Select > Selection Sets menu. N > Select. |
| Saved Views | Viewport bookmarks stored in the .blend: Ctrl+Alt+Shift+1-9 save, Ctrl+Alt+1-9 recall, named list, camera from a saved view. N > View. |
| Cursor Tools | 3D cursor history (go back to previous position), named cursor bookmarks, cursor to active face/object with rotation, reset rotation. Snap menu (Shift+S) + N > View. |
| Display Pie | Shift+Alt+D in Object Mode: display as bounds/wire/solid/textured, wireframe, in front, name, render visibility, selectable for all selected; view transform switch (AgX/Standard/PBR Neutral/False Color) + exposure reset. |
| Pie Builder | Build your own pie menus in Preferences: 8 slots of operator + properties + icon + label, hotkey per pie in any editor, no code. Preferences > Add-ons > Pie Builder. |
| Heavy Object Finder | Ranks visible objects by triangles after modifiers; one-click bounds/wire display or heavy modifiers off in viewport. 3D View > N > View. |

### Video, 2D & Motion Graphics

| Add-on | What it does |
|---|---|
| VSE Tools | Ripple delete (Shift+Delete), ripple trim to playhead (Alt+[ / Alt+]), insert/remove gap, batch blend/opacity/volume, frame-rate check, channel mute — 5.3-style ripple editing for 5.2 LTS. Sequencer Strip > Ripple. |
| GP Timing | Grease Pencil v3 timing: exposure on 1s–4s with ripple, insert/remove holds, retime keys, jump between keys on all layers, solo/lock layer navigator. Dope Sheet N > Timing. |
| Text Animator | Typewriter (chars/words/lines, blinking cursor) and number counter text that work in renders, split text into letters/words/lines, staggered scale/drop/rotate animation. Text Properties + N > Text. |

### System & Development

| Add-on | What it does |
|---|---|
| Script Watcher | Watch a .py file (re-run on save) or an add-on folder (disable, purge modules, re-enable on save); shows last result/error. Text Editor N > Text. |
| Keymap Tools | Find shortcut conflicts across user + add-on keymaps (report in a text block) and export a searchable HTML keymap cheat sheet. Help menu. |
| Preferences Snapshot | Zip preferences, keymaps, startup file + add-on/repo list; restore any snapshot (old files kept as .bak) to sync machines or undo bad settings. Preferences > Add-ons > Preferences Snapshot. |

## Development

- `./scripts/test.sh [addon_id ...]` runs headless tests from `tests/test_<addon_id>.py` (Blender in background, factory settings).
- See `docs/ADDON_GUIDE.md` for conventions.
