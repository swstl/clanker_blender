"""Rig Doctor: file-wide audit of drivers and constraints, retargeting and bulk constraint fixes."""
import re
from collections import namedtuple

import bpy

# kind: DRIVER/CONSTRAINT, owner: ID, bone: pose bone name or "", item: driven path or constraint name
Problem = namedtuple("Problem", "kind owner bone item text")
CON_TYPES = [('ALL', "All Types", "")] + [
    (e.identifier, e.name, "") for e in bpy.types.Constraint.bl_rna.properties['type'].enum_items]
ID_TYPES = [(k, k.replace("_", " ").title(), "", i) for i, k in enumerate(
    ("objects", "meshes", "armatures", "curves", "lattices", "materials", "shape_keys", "node_groups",
     "lights", "cameras", "worlds", "scenes"))]
BONE_RE = re.compile(r'pose\.bones\["((?:[^"\\]|\\.)*)"\]')


# ---------------------------------------------------------------- drivers

def driver_owners():
    """(id_owner, anim_holder, path_prefix): every ID plus its embedded node tree, in stable order."""
    for idb in sorted(bpy.data.user_map(), key=lambda i: (i.id_type, i.name, bool(i.library))):
        yield idb, idb, ""
        nt = getattr(idb, "node_tree", None)
        if isinstance(nt, bpy.types.NodeTree) and nt.is_embedded_data:
            yield idb, nt, "node_tree."


def drivers():
    """(owner ID, holder, prefix, fcurve) for every driver in the file."""
    for owner, holder, prefix in driver_owners():
        ad = getattr(holder, "animation_data", None)
        for fc in (ad.drivers if ad else ()):
            yield owner, holder, prefix, fc


def resolves(idb, path):
    try:
        idb.path_resolve(path)
        return True
    except (ValueError, TypeError):
        return False


def autorun_blocked():
    return bpy.app.autoexec_fail or not bpy.context.preferences.filepaths.use_scripts_auto_execute


def var_value(var):
    if var.type != 'SINGLE_PROP':
        return 1.0  # ponytail: transform/diff values not computed, only matters for eval errors
    t = var.targets[0]
    try:
        return t.id.path_resolve(t.data_path)
    except (AttributeError, ValueError, TypeError):
        return t.fallback_value if t.use_fallback_value else 0.0


def eval_error(drv):
    """Evaluate a *simple* expression (Blender's safe subset) with current variable values."""
    ns = {k: v for k, v in bpy.app.driver_namespace.items() if not k.startswith("__")}
    ns["frame"] = bpy.context.scene.frame_current
    ns.update({v.name: var_value(v) for v in drv.variables})
    ns["__builtins__"] = {}
    try:
        eval(compile(drv.expression, "<driver>", "eval"), ns)
    except Exception as e:  # ZeroDivisionError, ValueError (math domain), TypeError, NameError...
        return f"evaluation error: {type(e).__name__}: {e}"
    return None


def driver_problems(holder, fc):
    out = []
    drv = fc.driver
    if fc.mute:
        out.append("muted")
    if not fc.is_valid or not resolves(holder, fc.data_path):
        out.append(f"driven property '{fc.data_path}' not found")
    for v in drv.variables:
        if not v.is_name_valid:
            out.append(f"{v.name}: invalid variable name")
        if v.type == 'CONTEXT_PROP':
            continue
        for t in v.targets:
            if t.id is None:
                out.append(f"{v.name}: target missing")
            elif v.type == 'SINGLE_PROP':
                if not t.use_fallback_value and not resolves(t.id, t.data_path):
                    out.append(f"{v.name}: path '{t.data_path}' not found on {t.id.name}")
            elif t.bone_target and t.id.type == 'ARMATURE' and t.bone_target not in t.id.data.bones:
                out.append(f"{v.name}: bone '{t.bone_target}' not in {t.id.name}")
    if drv.type == 'SCRIPTED':
        try:
            compile(drv.expression, "<driver>", "eval")
        except SyntaxError as e:
            out.append(f"syntax error: {e.msg}")
        else:
            if not drv.is_simple_expression:
                if autorun_blocked():
                    out.append("needs Auto Run Python Scripts")
            elif not out:  # bad targets already explain a failing evaluation
                err = eval_error(drv)
                if err:
                    out.append(err)
    return out


# ---------------------------------------------------------------- constraints

def constraint_owners(objects=None):
    """(object, pose bone or None, constraint) for all (or the given) objects."""
    for ob in bpy.data.objects if objects is None else objects:
        for con in ob.constraints:
            yield ob, None, con
        for pb in (ob.pose.bones if ob.pose else ()):
            for con in pb.constraints:
                yield ob, pb, con


def _target_pairs(con):
    if con.type == 'ARMATURE':
        return [(t.target, t.subtarget, "target") for t in con.targets]
    pairs = []
    if hasattr(con, "target"):
        pairs.append((con.target, getattr(con, "subtarget", ""), "target"))
    if getattr(con, "pole_target", None):
        pairs.append((con.pole_target, con.pole_subtarget, "pole"))
    if 'CUSTOM' in (con.owner_space, con.target_space):
        pairs.append((con.space_object, con.space_subtarget, "custom space"))
    return pairs


def constraint_broken(con):
    """Hard problems: missing targets and subtargets."""
    out = []
    if con.type == 'ARMATURE' and not len(con.targets):
        out.append("no targets")
    optional = con.type == 'PIVOT' or con.type == 'ACTION' and getattr(con, "use_eval_time", False)
    for ob, sub, what in _target_pairs(con):
        if ob is None:
            if not (what == "target" and optional):
                out.append(f"{what} missing")
        elif sub and ob.type == 'ARMATURE' and sub not in ob.data.bones:
            out.append(f"{what} bone '{sub}' not in {ob.name}")
        elif sub and ob.type in {'MESH', 'LATTICE'} and sub not in ob.vertex_groups:
            out.append(f"{what} vertex group '{sub}' not on {ob.name}")
    return out


def constraint_problems(con, include_muted=False):
    out = constraint_broken(con)
    if con.influence == 0:
        out.append("zero influence")
    if include_muted and con.mute:
        out.append("muted")
    return out


# ---------------------------------------------------------------- scan

def scan(include_muted=False):
    found = []
    for owner, holder, prefix, fc in drivers():
        text = driver_problems(holder, fc)
        if text:
            m = BONE_RE.match(fc.data_path)
            bone = m.group(1).replace('\\"', '"') if m and isinstance(owner, bpy.types.Object) else ""
            found.append(Problem("DRIVER", owner, bone, f"{prefix}{fc.data_path}[{fc.array_index}]",
                                 "; ".join(text)))
    for ob, pb, con in constraint_owners():
        text = constraint_problems(con, include_muted)
        if text:
            found.append(Problem("CONSTRAINT", ob, pb.name if pb else "", con.name, "; ".join(text)))
    return found


def label(p):
    return " > ".join(x for x in (p.owner.name, p.bone if p.kind == "CONSTRAINT" else "", p.item) if x)


# ---------------------------------------------------------------- fixes

def scope_ids(objects):
    """IDs whose drivers count as 'on' these objects: the objects, their data and shape keys."""
    ids = set()
    for ob in objects:
        ids.add(ob)
        if ob.data:
            ids.add(ob.data)
            if getattr(ob.data, "shape_keys", None):
                ids.add(ob.data.shape_keys)
    return ids


def _editable_vars(objects):
    ids = None if objects is None else scope_ids(objects)
    for owner, _h, _p, fc in drivers():
        if not owner.library and (ids is None or owner in ids):
            yield from fc.driver.variables


def replace_target(a, b, objects=None):
    """Point driver variables and constraints using ID `a` at `b`. objects=None: whole file."""
    if a is None or b is None or a == b or a.id_type != b.id_type:
        raise ValueError("Pick two different IDs of the same type")
    n = 0
    for v in _editable_vars(objects):
        for t in v.targets:
            if v.type != 'CONTEXT_PROP' and t.id == a:
                t.id = b
                n += 1
    if isinstance(a, bpy.types.Object):
        for ob, _pb, con in constraint_owners(objects):
            if ob.library:
                continue
            holders = [con] + (list(con.targets) if con.type == 'ARMATURE' else [])
            for h in holders:
                for p in h.bl_rna.properties:
                    if p.type == 'POINTER' and not p.is_readonly and getattr(h, p.identifier) == a:
                        setattr(h, p.identifier, b)
                        n += 1
    return n


def swap_prefix(old, new, objects=None):
    """Replace the start `old` with `new` in driver variable data paths. Returns the count."""
    if not old:
        raise ValueError("Prefix to find is empty")
    n = 0
    for v in _editable_vars(objects):
        if v.type == 'SINGLE_PROP':
            t = v.targets[0]
            if t.data_path.startswith(old):
                t.data_path = new + t.data_path[len(old):]
                n += 1
    return n


def selection_constraints(objects):
    """Object constraints of `objects`; in Pose Mode only selected bones, else every bone."""
    for ob, pb, con in constraint_owners(objects):
        if ob.library or pb and ob.mode == 'POSE' and not pb.select:
            continue
        yield ob, pb, con


def set_mute(objects, con_type, mute):
    n = 0
    for _ob, _pb, con in selection_constraints(objects):
        if con_type in {'ALL', con.type} and con.mute != mute:
            con.mute = mute
            n += 1
    return n


def remove_broken(objects=None):
    doomed = [(pb or ob, con) for ob, pb, con in constraint_owners(objects)
              if not ob.library and constraint_broken(con)]
    for holder, con in doomed:
        holder.constraints.remove(con)
    return len(doomed)


# ---------------------------------------------------------------- results + select

def find_id(id_type, name, lib):
    return next((i for i in bpy.data.user_map() if i.id_type == id_type and i.name == name
                 and (i.library.filepath if i.library else "") == lib), None)


def user_object(idb, objects):
    if isinstance(idb, bpy.types.Object):
        return idb
    for o in objects:
        data = o.data
        if data == idb or getattr(data, "shape_keys", None) == idb or any(s.material == idb for s in o.material_slots):
            return o
    return None


def select_result(context, r):
    """Select the result's object (and bone) and make it active. Returns an error or None."""
    idb = find_id(r.id_type, r.owner, r.lib)
    if idb is None:
        return "Result no longer exists, scan again"
    ob = user_object(idb, context.scene.objects)
    if ob is None:
        return f"No object in the scene uses '{idb.name}'"
    for o in context.selected_objects:
        o.select_set(False)
    try:
        ob.select_set(True)
    except RuntimeError:
        pass  # not in the view layer
    context.view_layer.objects.active = ob
    pb = ob.pose.bones.get(r.bone) if r.bone and ob.pose else None
    if pb:
        for b in ob.pose.bones:
            b.select = b == pb
        ob.data.bones.active = pb.bone
    return None


def store(s, problems):
    s.results.clear()
    for p in problems:
        r = s.results.add()
        r.name, r.kind, r.problem = label(p), p.kind, p.text
        r.id_type, r.owner, r.bone = p.owner.id_type, p.owner.name, p.bone
        r.lib = p.owner.library.filepath if p.owner.library else ""
    s.index = -1


# ---------------------------------------------------------------- properties

class RD_Result(bpy.types.PropertyGroup):
    kind: bpy.props.StringProperty()
    problem: bpy.props.StringProperty()
    id_type: bpy.props.StringProperty()
    owner: bpy.props.StringProperty()
    lib: bpy.props.StringProperty()
    bone: bpy.props.StringProperty()


def _on_index(self, context):
    if 0 <= self.index < len(self.results):
        select_result(context, self.results[self.index])


class RD_Settings(bpy.types.PropertyGroup):
    results: bpy.props.CollectionProperty(type=RD_Result)
    index: bpy.props.IntProperty(default=-1, update=_on_index)
    include_muted: bpy.props.BoolProperty(name="Report Muted", description="Also list muted constraints")
    only_selected: bpy.props.BoolProperty(
        name="Only Selected", description="Limit retarget, prefix swap and removal to selected objects")
    id_type: bpy.props.EnumProperty(name="Type", items=ID_TYPES, description="Data-block type to retarget")
    id_a: bpy.props.StringProperty(name="Replace", description="Data-block the drivers and constraints use now")
    id_b: bpy.props.StringProperty(name="With", description="Data-block to use instead")
    old_prefix: bpy.props.StringProperty(name="Find", description="Start of driver variable data paths",
                                         default='pose.bones["')
    new_prefix: bpy.props.StringProperty(name="Replace", description="New start of the data paths")
    con_type: bpy.props.EnumProperty(name="Type", items=CON_TYPES, description="Constraint type to mute or unmute")


def _settings(context):
    return context.window_manager.rig_doctor


def _scope(context):
    return list(context.selected_objects) if _settings(context).only_selected else None


# ---------------------------------------------------------------- operators

class RD_OT_scan(bpy.types.Operator):
    bl_idname = "rig_doctor.scan"
    bl_label = "Scan File"
    bl_description = ("Check every driver and constraint in the file for missing targets, bad paths, "
                      "bad bones, expression errors and Auto Run needs")

    def execute(self, context):
        s = _settings(context)
        store(s, scan(s.include_muted))
        self.report({'INFO'}, f"{len(s.results)} problem(s) found")
        return {'FINISHED'}


class RD_OT_replace_target(bpy.types.Operator):
    bl_idname = "rig_doctor.replace_target"
    bl_label = "Replace Target"
    bl_description = "Point every driver variable and constraint using the first data-block at the second"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        coll = getattr(bpy.data, s.id_type)
        try:
            n = replace_target(coll.get(s.id_a), coll.get(s.id_b), _scope(context))
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Retargeted {n} reference(s)")
        return {'FINISHED'}


class RD_OT_swap_prefix(bpy.types.Operator):
    bl_idname = "rig_doctor.swap_prefix"
    bl_label = "Swap Path Prefix"
    bl_description = "Replace the start of driver variable data paths (e.g. a renamed bone prefix)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        try:
            n = swap_prefix(s.old_prefix, s.new_prefix, _scope(context))
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Changed {n} data path(s)")
        return {'FINISHED'}


class RD_OT_mute(bpy.types.Operator):
    bl_idname = "rig_doctor.mute"
    bl_label = "Mute Constraints"
    bl_description = ("Mute or unmute constraints of the chosen type on selected objects "
                      "(Pose Mode: selected bones only)")
    bl_options = {'REGISTER', 'UNDO'}
    mute: bpy.props.BoolProperty(name="Mute", default=True)

    def execute(self, context):
        n = set_mute(context.selected_objects, _settings(context).con_type, self.mute)
        self.report({'INFO'}, f"{'Muted' if self.mute else 'Unmuted'} {n} constraint(s)")
        return {'FINISHED'}


class RD_OT_remove_broken(bpy.types.Operator):
    bl_idname = "rig_doctor.remove_broken"
    bl_label = "Remove Broken Constraints"
    bl_description = "Delete constraints with a missing target, bone or vertex group"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        n = remove_broken(_scope(context))
        s = _settings(context)
        store(s, scan(s.include_muted))
        self.report({'INFO'}, f"Removed {n} broken constraint(s)")
        return {'FINISHED'}


# ---------------------------------------------------------------- UI

class RD_UL_results(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon = 'DRIVER' if item.kind == "DRIVER" else 'CONSTRAINT_BONE' if item.bone else 'CONSTRAINT'
        row = layout.split(factor=0.5)
        row.label(text=item.name, icon=icon)
        row.label(text=item.problem)


class RD_Main:
    bl_label = "Rig Doctor"
    bl_region_type = 'UI'
    bl_category = "Rig"

    def draw(self, context):
        s = _settings(context)
        row = self.layout.row(align=True)
        row.operator(RD_OT_scan.bl_idname, icon='VIEWZOOM')
        row.prop(s, "include_muted", text="", icon='HIDE_ON')
        self.layout.template_list("RD_UL_results", "", s, "results", s, "index", rows=5)
        if 0 <= s.index < len(s.results):
            col = self.layout.column(align=True)
            col.label(text=s.results[s.index].name)
            col.label(text=s.results[s.index].problem, icon='ERROR')


class RD_Fix:
    bl_label = "Fix"
    bl_region_type = 'UI'
    bl_category = "Rig"

    def draw(self, context):
        s = _settings(context)
        lay = self.layout
        lay.prop(s, "only_selected")
        col = lay.column(align=True)
        col.prop(s, "id_type")
        col.prop_search(s, "id_a", bpy.data, s.id_type)
        col.prop_search(s, "id_b", bpy.data, s.id_type)
        col.operator(RD_OT_replace_target.bl_idname, icon='FILE_REFRESH')
        col = lay.column(align=True)
        col.prop(s, "old_prefix")
        col.prop(s, "new_prefix")
        col.operator(RD_OT_swap_prefix.bl_idname, icon='SORTALPHA')
        col = lay.column(align=True)
        col.prop(s, "con_type", text="")
        row = col.row(align=True)
        row.operator(RD_OT_mute.bl_idname, text="Mute", icon='HIDE_ON').mute = True
        row.operator(RD_OT_mute.bl_idname, text="Unmute", icon='HIDE_OFF').mute = False
        lay.operator(RD_OT_remove_broken.bl_idname, icon='TRASH')


class RD_PT_view3d(RD_Main, bpy.types.Panel):
    bl_space_type = 'VIEW_3D'


class RD_PT_view3d_fix(RD_Fix, bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_parent_id = "RD_PT_view3d"
    bl_options = {'DEFAULT_CLOSED'}


def _drivers_editor(context):
    return getattr(getattr(context, "space_data", None), "mode", "") == 'DRIVERS'


class RD_PT_drivers(RD_Main, bpy.types.Panel):
    bl_space_type = 'GRAPH_EDITOR'
    poll = classmethod(lambda cls, context: _drivers_editor(context))


class RD_PT_drivers_fix(RD_Fix, bpy.types.Panel):
    bl_space_type = 'GRAPH_EDITOR'
    bl_parent_id = "RD_PT_drivers"
    bl_options = {'DEFAULT_CLOSED'}


classes = (RD_Result, RD_Settings, RD_OT_scan, RD_OT_replace_target, RD_OT_swap_prefix, RD_OT_mute,
           RD_OT_remove_broken, RD_UL_results, RD_PT_view3d, RD_PT_view3d_fix, RD_PT_drivers, RD_PT_drivers_fix)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.WindowManager.rig_doctor = bpy.props.PointerProperty(type=RD_Settings)


def unregister():
    del bpy.types.WindowManager.rig_doctor
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
