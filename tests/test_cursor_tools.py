import importlib
import bmesh
import bpy

m = importlib.import_module("bl_ext.clanker.cursor_tools")


def test_history_back():
    sc = bpy.context.scene
    sc.cursor_tools_history.clear()
    m._last[0] = None
    sc.cursor.location = (1, 0, 0)
    m.record(sc)
    sc.cursor.location = (2, 0, 0)
    m.record(sc)
    sc.cursor.location = (3, 0, 0)
    bpy.ops.cursor_tools.back()
    assert tuple(sc.cursor.location) == (2, 0, 0)
    bpy.ops.cursor_tools.back()
    assert tuple(sc.cursor.location) == (1, 0, 0)


def test_bookmark_and_face():
    sc = bpy.context.scene
    sc.cursor.location = (5, 5, 5)
    bpy.ops.cursor_tools.bookmark(name="Here")
    sc.cursor.location = (0, 0, 0)
    bpy.ops.cursor_tools.goto(index=len(sc.cursor_tools_marks) - 1)
    assert tuple(sc.cursor.location) == (5, 5, 5)
    me = bpy.data.meshes.new("C")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("C", me)
    sc.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(me)
    for f in bm.faces:
        f.select = f.normal.x > 0.5
    bpy.ops.cursor_tools.to_face()
    bpy.ops.object.mode_set(mode='OBJECT')
    assert abs(sc.cursor.location.x - 1) < 1e-5
    z = sc.cursor.matrix.to_3x3().col[2]
    assert abs(z.x - 1) < 1e-5


def test_remove_not_remembered():
    # The bookmark button leaves remove unset: after an X click it must not delete too.
    assert bpy.ops.cursor_tools.goto.get_rna_type().properties['remove'].is_skip_save
