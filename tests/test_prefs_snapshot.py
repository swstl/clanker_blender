import importlib
import json
import os
import tempfile
import zipfile

m = importlib.import_module("bl_ext.clanker.prefs_snapshot")


def test_snapshot_roundtrip():
    cfg = tempfile.mkdtemp()
    out = tempfile.mkdtemp()
    open(os.path.join(cfg, "userpref.blend"), "wb").write(b"PREFS1")
    open(os.path.join(cfg, "unrelated.txt"), "w").write("x")
    path = m.make_snapshot(out, cfg, note="before theme")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        man = json.loads(z.read("manifest.json"))
    assert "config/userpref.blend" in names and "config/unrelated.txt" not in names
    assert "bl_ext.clanker.prefs_snapshot" in man["addons"]
    open(os.path.join(cfg, "userpref.blend"), "wb").write(b"BROKEN")
    assert m.restore_snapshot(path, cfg) == ["userpref.blend"]
    assert open(os.path.join(cfg, "userpref.blend"), "rb").read() == b"PREFS1"
    assert open(os.path.join(cfg, "userpref.blend.bak"), "rb").read() == b"BROKEN"
    assert m.list_snapshots(out) == [path]


def test_restore_ignores_unexpected_paths():
    out = tempfile.mkdtemp()
    cfg = tempfile.mkdtemp()
    bad = os.path.join(out, "bad.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("config/../../evil.txt", "x")
        z.writestr("config/startup.blend", "S")
    assert m.restore_snapshot(bad, cfg) == ["startup.blend"]
    assert not os.path.exists(os.path.join(os.path.dirname(cfg), "evil.txt"))
