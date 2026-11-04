#!/usr/bin/env bash
# Build one installable .zip per add-on into dist/ (for "Install from Disk" or sharing),
# plus an index.json so dist/ can be used as a remote repository.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-$(command -v blender || echo "$HOME/.local/share/Steam/steamapps/common/Blender/blender")}"
mkdir -p "$ROOT/dist"
for d in "$ROOT"/addons/*/; do
  "$BLENDER" --factory-startup --command extension build --source-dir "$d" --output-dir "$ROOT/dist"
done
"$BLENDER" --factory-startup --command extension server-generate --repo-dir "$ROOT/dist"
