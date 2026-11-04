#!/usr/bin/env bash
# Register this repo's addons/ folder as a Blender extension repository ("clanker")
# and enable every add-on in it. Re-run after `git pull` to enable new add-ons.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-$(command -v blender || echo "$HOME/.local/share/Steam/steamapps/common/Blender/blender")}"

# Add the repo (idempotent: ignore "already exists").
"$BLENDER" --background --factory-startup --command extension repo-add clanker \
  --name "Clanker QoL" --directory "$ROOT/addons" --source SYSTEM 2>&1 | grep -iv "^$" || true

# Enable all add-ons and save preferences.
"$BLENDER" --background --python-expr "
import bpy, os
root = r'$ROOT/addons'
for name in sorted(os.listdir(root)):
    if os.path.isfile(os.path.join(root, name, 'blender_manifest.toml')):
        mod = 'bl_ext.clanker.' + name
        try:
            bpy.ops.preferences.addon_enable(module=mod)
            print('enabled', mod)
        except Exception as e:
            print('FAILED', mod, e)
bpy.ops.wm.save_userpref()
"
