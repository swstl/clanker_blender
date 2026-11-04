"""Headless smoke test for UI code: run every Panel/Menu/UIList draw() of an add-on
against a fake layout that validates property names and operator ids.

Background Blender never draws UI, so typos like layout.prop(obj, "lenz") or a renamed
operator only show up when a user opens the panel. This catches most of them.
"""
import bpy


class DrawError(Exception):
    pass


def _check_prop(data, name):
    if data is None:
        raise DrawError(f"prop({name!r}) on None")
    if isinstance(data, Loose):
        return
    rna = getattr(data, "bl_rna", None)
    if rna is not None and name in rna.properties:
        return
    if hasattr(data, name):
        return
    raise DrawError(f"{type(data).__name__} has no property {name!r}")


def _check_op(idname):
    mod, _, name = idname.partition(".")
    try:
        getattr(getattr(bpy.ops, mod), name).get_rna_type()
    except (KeyError, AttributeError):
        raise DrawError(f"unknown operator {idname!r}")


CALLS = []  # (idname, OpProps) for every layout.operator() call during a check


class OpProps:
    def __init__(self, idname):
        object.__setattr__(self, "_props", getattr(getattr(bpy.ops, idname.split(".")[0]),
                                                   idname.split(".")[1]).get_rna_type().properties)
        object.__setattr__(self, "_id", idname)

    def __setattr__(self, k, v):
        if k not in self._props:
            raise DrawError(f"operator {self._id} has no property {k!r}")
        self.__dict__.setdefault("_set", set()).add(k)

    def __getattr__(self, k):
        return Loose()


class Loose:
    """Permissive stand-in for things background mode lacks (space_data, region...)."""

    def __getattr__(self, k):
        return Loose()

    def __call__(self, *a, **k):
        return Loose()

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __eq__(self, o):
        return False

    def __hash__(self):
        return 0

    def __lt__(self, o):
        return False

    __gt__ = __le__ = __ge__ = __lt__

    def __float__(self):
        return 0.0

    def __int__(self):
        return 0

    def __index__(self):
        return 0

    def __str__(self):
        return ""

    def __getitem__(self, k):
        return Loose()


class FakeLayout:
    def __getattr__(self, name):
        # row(), column(), box(), split(), grid_flow(), menu_pie(), panel()...
        return lambda *a, **k: FakeLayout() if name != "panel" else (FakeLayout(), FakeLayout())

    def __setattr__(self, k, v):
        pass

    def prop(self, data, prop, *a, **k):
        _check_prop(data, prop)
        if "icon" in k:
            _check_icon(k["icon"])

    def props_enum(self, data, prop):
        _check_prop(data, prop)

    def prop_enum(self, data, prop, *a, **k):
        _check_prop(data, prop)

    prop_menu_enum = prop_tabs_enum = props_enum

    def prop_search(self, data, prop, search_data, search_prop, *a, **k):
        _check_prop(data, prop)
        _check_prop(search_data, search_prop)

    def operator(self, idname, *a, **k):
        _check_op(idname)
        if "icon" in k:
            _check_icon(k["icon"])
        op = OpProps(idname)
        CALLS.append((idname, op))
        return op

    def operator_menu_enum(self, idname, prop, *a, **k):
        _check_op(idname)
        return OpProps(idname)

    def template_list(self, listtype, list_id, data, prop, active_data, active_prop, *a, **k):
        if listtype not in dir(bpy.types) and not hasattr(bpy.types, listtype):
            raise DrawError(f"unknown UIList {listtype!r}")
        _check_prop(data, prop)
        _check_prop(active_data, active_prop)

    def menu(self, name, *a, **k):
        if "icon" in k:
            _check_icon(k["icon"])
        if not hasattr(bpy.types, name):
            raise DrawError(f"unknown menu {name!r}")

    def popover(self, panel, *a, **k):
        if not hasattr(bpy.types, panel):
            raise DrawError(f"unknown panel {panel!r}")

    def label(self, *a, **k):
        if "icon" in k:
            _check_icon(k["icon"])

    def panel_prop(self, data, prop):
        _check_prop(data, prop)
        return FakeLayout(), FakeLayout()


_ICONS = None


def _check_icon(icon):
    global _ICONS
    if _ICONS is None:
        _ICONS = set(bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys())
    if icon not in _ICONS:
        raise DrawError(f"unknown icon {icon!r}")


class Ctx:
    """bpy.context with Loose fallbacks for UI-only members."""

    def __init__(self, **over):
        object.__setattr__(self, "_over", over)

    def __getattr__(self, k):
        if k in self._over:
            return self._over[k]
        v = getattr(bpy.context, k, None)
        return Loose() if v is None and k in {"space_data", "region", "area", "region_data", "edit_text"} else v


def _setup_scene():
    """Give draw code something realistic to look at: mesh (active), armature, camera, light."""
    import bmesh
    sc = bpy.context.scene
    me = bpy.data.meshes.new("DrawMesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bm.to_mesh(me)
    ob = bpy.data.objects.new("DrawMesh", me)
    sc.collection.objects.link(ob)
    ob.vertex_groups.new(name="Bone")
    ob.shape_key_add(name="Basis")
    ob.shape_key_add(name="Key")
    ob.data.materials.append(bpy.data.materials.new("DrawMat"))
    arm = bpy.data.objects.new("DrawRig", bpy.data.armatures.new("DrawRig"))
    sc.collection.objects.link(arm)
    cam = bpy.data.objects.new("DrawCam", bpy.data.cameras.new("DrawCam"))
    sc.collection.objects.link(cam)
    sc.camera = cam
    light = bpy.data.objects.new("DrawLight", bpy.data.lights.new("DrawLight", 'POINT'))
    sc.collection.objects.link(light)
    ob.location.x = 1
    ob.keyframe_insert("location", frame=1)
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    return ob, arm, cam, light


def _draw_targets(module):
    """(label, kind, callable(self_like, ctx)) for everything the add-on draws."""
    out = []
    seen = set()
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name, None)
        if not isinstance(cls, type):
            continue
        if getattr(cls, "__module__", "").startswith(module) and cls not in seen:
            seen.add(cls)
            if issubclass(cls, (bpy.types.Panel, bpy.types.Menu)):
                out.append((cls.__name__, cls))
            if issubclass(cls, bpy.types.UIList):
                out.append((cls.__name__, cls))
        # Functions appended to existing menus/panels/headers.
        draw = getattr(cls, "draw", None)
        for fn in getattr(draw, "_draw_funcs", ()):
            if getattr(fn, "__module__", "").startswith(module):
                out.append((f"{name}+{fn.__name__}", fn))
    return out


class _Self:
    """Stand-in for a Panel/Menu instance (bl_rna instance can't be created headless)."""

    def __init__(self, cls):
        object.__setattr__(self, "layout", FakeLayout())
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "bl_label", getattr(cls, "bl_label", ""))

    def __getattr__(self, k):
        attr = getattr(self._cls, k)
        return attr.__get__(self) if hasattr(attr, "__get__") and callable(attr) else attr


def remembered_prop_errors(module_prefix):
    """Props set by some buttons but left unset by other buttons of the same operator.

    In the UI, unset operator props reuse their last value unless they are SKIP_SAVE, so such a
    button silently acts with whatever the other button set last."""
    by_op = {}
    for idname, op in CALLS:
        by_op.setdefault(idname, []).append(op.__dict__.get("_set", set()))
    errs = []
    for idname, sets in by_op.items():
        if len(sets) < 2:
            continue
        mod, _, name = idname.partition(".")
        props = getattr(getattr(bpy.ops, mod), name).get_rna_type().properties
        varying = set().union(*sets) - set.intersection(*sets)
        for p in sorted(varying):
            if p in props and not props[p].is_skip_save:
                errs.append(f"{idname}: '{p}' is set by some buttons but not others; "
                            f"add options={{'SKIP_SAVE'}} or set it everywhere")
    return errs


def check_module(module):
    """Run all draw code of `module` in a few modes. Returns list of error strings."""
    errors = []
    CALLS.clear()
    ob, arm, cam, light = _setup_scene()
    contexts = [("mesh", {}), ("armature", {"object": arm, "active_object": arm}),
                ("camera", {"object": cam, "active_object": cam}),
                ("light", {"object": light, "active_object": light})]
    for label, target in _draw_targets(module):
        ran = False
        for cname, over in contexts:
            ctx = Ctx(**over)
            try:
                if isinstance(target, type) and issubclass(target, bpy.types.UIList):
                    # Draw one item from each CollectionProperty the add-on registered? Too implicit:
                    # just ensure draw_item exists and is callable.
                    ran = callable(getattr(target, "draw_item", None))
                    break
                if isinstance(target, type):
                    poll = getattr(target, "poll", None)
                    if poll and not poll(ctx):
                        continue
                    target.draw(_Self(target), ctx)
                else:
                    target(_Self(type("X", (), {})), ctx)
                ran = True
            except DrawError as e:
                errors.append(f"{label} [{cname}]: {e}")
                ran = True
                break
            except Exception as e:  # real bug in draw code
                import traceback
                tb = traceback.extract_tb(e.__traceback__)[-1]
                errors.append(f"{label} [{cname}]: {type(e).__name__}: {e} (line {tb.lineno})")
                ran = True
                break
        if not ran:
            pass  # poll false in every context we can fake (e.g. needs edit mode); skipped
    # Only judge this add-on's own operators (calls to native ops are fine).
    own = {i for i, _ in CALLS if i.split(".")[0] == module.rsplit(".", 1)[-1]}
    errors += [e for e in remembered_prop_errors(module) if e.split(":")[0] in own]
    return errors
