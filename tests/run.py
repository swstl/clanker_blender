"""Headless test runner. Run with:  ./scripts/test.sh  [addon_id ...]

Inside Blender: registers addons/ as a temporary extension repo "clanker",
enables every add-on (or the ones named), then runs test_*() functions from
tests/test_<addon_id>.py. Exits non-zero on any failure.
"""
import importlib.util
import os
import sys
import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = os.path.join(ROOT, "addons")
TESTS = os.path.join(ROOT, "tests")
sys.path.insert(0, TESTS)
import drawcheck  # noqa: E402


def clean():
    """Remove all data a test may have created."""
    for coll in (bpy.data.objects, bpy.data.meshes, bpy.data.armatures, bpy.data.actions,
                 bpy.data.materials, bpy.data.cameras, bpy.data.lights, bpy.data.curves,
                 bpy.data.images, bpy.data.node_groups, bpy.data.lattices):
        for item in list(coll):
            coll.remove(item)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ids = argv or sorted(d for d in os.listdir(ADDONS)
                         if os.path.isfile(os.path.join(ADDONS, d, "blender_manifest.toml")))

    repos = bpy.context.preferences.extensions.repos
    if "clanker" not in {r.module for r in repos}:
        repos.new(name="clanker", module="clanker", custom_directory=ADDONS, source='SYSTEM')

    failures = 0
    for addon_id in ids:
        mod = "bl_ext.clanker." + addon_id
        try:
            bpy.ops.preferences.addon_enable(module=mod)
            assert mod in bpy.context.preferences.addons, "did not enable"
        except Exception:
            failures += 1
            print(f"FAIL enable {addon_id}\n{traceback.format_exc()}")
            continue
        clean()
        for err in drawcheck.check_module(mod):
            failures += 1
            print(f"FAIL {addon_id} draw: {err}")
        path = os.path.join(TESTS, f"test_{addon_id}.py")
        if not os.path.isfile(path):
            print(f"ok   {addon_id} (enabled, no tests)")
            continue
        spec = importlib.util.spec_from_file_location(f"test_{addon_id}", path)
        tmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tmod)
        for name in sorted(n for n in dir(tmod) if n.startswith("test_")):
            clean()
            try:
                getattr(tmod, name)()
                print(f"ok   {addon_id}.{name}")
            except Exception:
                failures += 1
                print(f"FAIL {addon_id}.{name}\n{traceback.format_exc()}")
        # Disabling must also work cleanly (unregister bugs are common).
        try:
            bpy.ops.preferences.addon_disable(module=mod)
            bpy.ops.preferences.addon_enable(module=mod)
        except Exception:
            failures += 1
            print(f"FAIL re-register {addon_id}\n{traceback.format_exc()}")

    print(f"\n{'FAILED' if failures else 'PASSED'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)


main()
