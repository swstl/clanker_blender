"""ship.py <id> <idea#|-> "<Name>" "<desc>" "<commit msg>": test, add README row (regrouped), mark idea done, commit + push.

Run from repo root. Add new add-on names to scripts/categories.py first."""
import re, subprocess, sys
aid, idea, name, desc, msg = sys.argv[1:6]
r = subprocess.run(["./scripts/test.sh", aid], capture_output=True, text=True)
if r.returncode:
    sys.exit("tests failed, not shipping:\n" + r.stdout[-2000:])
r = open("README.md").read()
lines = r.split("\n")
i = max(k for k, l in enumerate(lines) if l.startswith("| ") and "|---" not in l and k < lines.index("## Development"))
lines.insert(i + 1, f"| {name} | {desc} |")
open("README.md", "w").write("\n".join(lines))
subprocess.run([sys.executable, "scripts/gen_readme.py"], check=True)
if idea != "-":
    d = open("docs/IDEAS.md").read()
    d = re.sub(rf"^(\| {idea} \|.*\| )(todo|wip[^|]*)\|$", rf"\1done ({aid}) |", d, flags=re.M)
    open("docs/IDEAS.md", "w").write(d)
subprocess.run(["git", "add", "README.md", "docs/IDEAS.md", f"addons/{aid}", f"tests/test_{aid}.py"], check=True)
subprocess.run(["git", "commit", "-qm", msg], check=True)
subprocess.run(["git", "push", "-q"], check=True)
print("shipped", aid)
