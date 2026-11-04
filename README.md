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

## Development

- `./scripts/test.sh [addon_id ...]` runs headless tests from `tests/test_<addon_id>.py` (Blender in background, factory settings).
- See `docs/ADDON_GUIDE.md` for conventions.
