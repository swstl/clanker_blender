import importlib
import os
import tempfile
import bpy

m = importlib.import_module("bl_ext.clanker.batch_import")


def _export(folder):
    for name, fn in (("crate", lambda p: bpy.ops.wm.obj_export(filepath=p, export_selected_objects=True)),
                     ("barrel", lambda p: bpy.ops.export_scene.gltf(filepath=p, use_selection=True,
                                                                    export_format='GLB'))):
        bpy.ops.mesh.primitive_cube_add(size=2)
        ob = bpy.context.active_object
        ob.name = name
        ext = ".obj" if name == "crate" else ".glb"
        fn(os.path.join(folder, name + ext))
        bpy.data.objects.remove(ob)
    open(os.path.join(folder, "broken.stl"), "w").write("not an stl")
    open(os.path.join(folder, "notes.txt"), "w").write("skip me")


def test_folder_import_grid_and_failures():
    d = tempfile.mkdtemp()
    _export(d)
    root, ok, failed = m.run(bpy.context, d, exts={".obj", ".glb", ".stl"}, spacing=1.0)
    names = sorted(c.name for c in root.children)
    assert "barrel" in names and "crate" in names
    assert len(ok) + len(failed) == 3
    crate = bpy.data.collections["crate"].objects[0]
    barrel = [o for o in bpy.data.collections["barrel"].all_objects if o.type == 'MESH'][0]
    bpy.context.view_layer.update()
    # Side by side, not overlapping, sitting on the floor.
    a = m._world_bounds([crate])
    b = m._world_bounds([barrel])
    assert abs(min(a[0].z, b[0].z)) < 1e-4
    assert a[1].x <= b[0].x + 1e-4 or b[1].x <= a[0].x + 1e-4
