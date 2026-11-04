import importlib
import bpy

m = importlib.import_module("bl_ext.clanker.pie_builder")


class FakeOp:
    pass


class FakePie:
    def __init__(self):
        self.calls = []

    def operator(self, idname, text="", icon='NONE'):
        self.calls.append(("op", idname, text, icon))
        op = FakeOp()
        self.calls.append(op)
        return op

    def label(self, text="", icon='NONE'):
        self.calls.append(("label", text))

    def separator(self):
        self.calls.append(("sep",))


class FakeLayout:
    def __init__(self):
        self.pie = FakePie()

    def menu_pie(self):
        return self.pie


def test_parse_props():
    assert m.parse_props('mode="EDIT", toggle=True') == {"mode": "EDIT", "toggle": True}
    assert m.parse_props("") == {}
    for bad in ("__import__('os')", "x=open('f')"):
        try:
            m.parse_props(bad)
            assert False, bad
        except (ValueError, SyntaxError):
            pass


def test_pie_draw_and_keymap():
    p = m._prefs()
    p.pies.clear()
    bpy.ops.pie_builder.add_pie()
    pie = p.pies[0]
    pie.key, pie.keymap = "F13", "3D View"
    pie.items[0].operator, pie.items[0].label, pie.items[0].props = "object.mode_set", "Edit", 'mode="EDIT"'
    pie.items[1].operator = "nope.missing"
    pie.items[2].icon = "BOGUS"
    lay = FakeLayout()
    m.draw_pie(lay, pie)
    kinds = [c[0] for c in lay.pie.calls if isinstance(c, tuple)]
    assert kinds[:3] == ["op", "label", "sep"]
    assert lay.pie.calls[1].mode == "EDIT"
    assert m.rebuild_keymaps() == 1
    km, kmi = m._keymaps[0]
    assert kmi.type == 'F13' and kmi.alt and kmi.shift and kmi.properties.name == "PB_MT_pie_0"
    bpy.ops.pie_builder.remove_pie(index=0)
    assert not m._keymaps
