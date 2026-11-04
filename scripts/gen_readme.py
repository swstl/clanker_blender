"""Regroup the README add-on table by category (scripts/categories.py). Run after adding rows."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from categories import CATEGORIES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(ROOT, "README.md")
text = open(path).read()
start = text.index("## Add-ons")
end = text.index("## Development")
rows = {}
for line in text[start:end].splitlines():
    m = re.match(r"^\| (.+?) \| (.+) \|$", line)
    if m and m.group(1) not in ("Add-on", "---") and not m.group(1).startswith("-"):
        rows[m.group(1)] = m.group(2)
out = [f"## Add-ons ({len(rows)})", ""]
placed = set()
for cat, names in list(CATEGORIES.items()) + [("Other", sorted(set(rows) - {n for v in CATEGORIES.values() for n in v}))]:
    present = [n for n in names if n in rows]
    if not present:
        continue
    out += [f"### {cat}", "", "| Add-on | What it does |", "|---|---|"]
    out += [f"| {n} | {rows[n]} |" for n in present]
    out.append("")
    placed.update(present)
assert placed == set(rows), set(rows) - placed
open(path, "w").write(text[:start] + "\n".join(out) + "\n" + text[end:])
print(f"{len(rows)} add-ons in README")
