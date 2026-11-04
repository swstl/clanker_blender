# Errors

- **`blender --command extension ...` loads all user add-ons**: put `--background --factory-startup` before `--command`. (2026-11-04)
- **Bash call with `ls -R` at end hung >120s**: `ls` is aliased (eza); avoid `ls -R` in scripts. (2026-11-04)
