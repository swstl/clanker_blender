# Decisions

- Ship a finished add-on with `python3 scripts/ship.py <id> <idea#|-> "<Name>" "<desc>" "<msg>"` (runs its tests first). README is regrouped by scripts/gen_readme.py using scripts/categories.py.
- Workflow that worked (2026-11-04): research agent -> ranked ideas into docs/IDEAS.md; 3-6 builder agents in parallel, each limited to addons/<id>/ + tests/test_<id>.py and told NOT to commit; coordinator verifies with test.sh and ships. Then review agents for UI-only code (modal, draw handlers, timers, remembered operator props).
- New add-on names must be added to scripts/categories.py or they land in "Other".
- Clanker Hub merges sidebar tabs; panels need `bl_category` + `bl_region_type = 'UI'` to be picked up.
