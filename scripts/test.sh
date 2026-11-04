#!/usr/bin/env bash
# Run headless tests: ./scripts/test.sh [addon_id ...]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BLENDER="${BLENDER:-$(command -v blender || echo "$HOME/.local/share/Steam/steamapps/common/Blender/blender")}"
"$BLENDER" --background --factory-startup --python-exit-code 1 --python "$ROOT/tests/run.py" -- "$@" 2>&1 \
  | grep -E '^(ok|FAIL|PASSED|FAILED|Traceback|  |[A-Za-z]*Error)' 
exit "${PIPESTATUS[0]}"
