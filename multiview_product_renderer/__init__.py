bl_info = {
    "name": "MultiView Product Renderer",
    "author": "MultiView",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > MultiView",
    "description": "Import STEP files, set up cameras and 3-point lighting, batch render products across views.",
    "category": "Render",
}

import bpy
import os
import math
import subprocess
import tempfile
import shutil
import time
from mathutils import Vector, Matrix
from bpy.props import (
    StringProperty, EnumProperty, BoolProperty, FloatProperty,
    IntProperty, FloatVectorProperty, CollectionProperty, PointerProperty,
)
from bpy.types import (
    Operator, Panel, PropertyGroup, AddonPreferences, UIList,
)
from bpy_extras.io_utils import ImportHelper


VIEWS_ALL = [
    ("Front",     "ortho",  ( 0, -1,  0)),
    ("Back",      "ortho",  ( 0,  1,  0)),
    ("Top",       "ortho",  ( 0,  0,  1)),
    ("Bottom",    "ortho",  ( 0,  0, -1)),
    ("Right",     "ortho",  ( 1,  0,  0)),
    ("Left",      "ortho",  (-1,  0,  0)),
    ("Persp_FTR", "persp",  ( 1, -1,  0.9)),
    ("Persp_FTL", "persp",  (-1, -1,  0.9)),
    ("Persp_BTR", "persp",  ( 1,  1,  0.9)),
]
VIEW_SETS = {
    "ALL":   VIEWS_ALL,
    "ORTHO": VIEWS_ALL[:6],
    "STD":   [VIEWS_ALL[0], VIEWS_ALL[2], VIEWS_ALL[4], VIEWS_ALL[6]],
    "MIN":   [VIEWS_ALL[0], VIEWS_ALL[2], VIEWS_ALL[4]],
}

FRONT_AXIS_ANGLES = {'-Y': 0.0, '+X': 90.0, '+Y': 180.0, '-X': 270.0}

LIGHTING_PRESETS = {
    "STUDIO_SOFT": {
        "label": "Studio Soft",
        "lights": [
            {"name": "Key",  "pos": ( 1.5, -1.2, 1.6), "size": 3.0, "energy": 800, "color": (1, 1, 1)},
            {"name": "Fill", "pos": (-1.8, -0.8, 1.0), "size": 4.0, "energy": 300, "color": (1, 1, 1)},
            {"name": "Rim",  "pos": ( 0.0,  2.0, 2.0), "size": 2.0, "energy": 600, "color": (1, 1, 1)},
        ],
    },
    "STUDIO_HARD": {
        "label": "Studio Hard",
        "lights": [
            {"name": "Key",  "pos": ( 1.4, -1.2, 1.4), "size": 0.8, "energy": 1200, "color": (1, 1, 1)},
            {"name": "Fill", "pos": (-1.6, -0.6, 0.9), "size": 1.5, "energy":  350, "color": (1, 1, 1)},
            {"name": "Rim",  "pos": ( 0.0,  2.2, 2.2), "size": 0.6, "energy":  900, "color": (1, 1, 1)},
        ],
    },
    "DRAMATIC": {
        "label": "Dramatic",
        "lights": [
            {"name": "Key",  "pos": ( 2.0, -1.5, 1.8), "size": 1.2, "energy": 1500, "color": (1.0, 0.98, 0.94)},
            {"name": "Fill", "pos": (-2.0, -0.5, 0.6), "size": 2.5, "energy":  120, "color": (0.85, 0.9, 1.0)},
            {"name": "Rim",  "pos": (-0.5,  2.5, 2.5), "size": 0.8, "energy": 1000, "color": (1, 1, 1)},
        ],
    },
    "HIGH_KEY": {
        "label": "High Key",
        "lights": [
            {"name": "Key",  "pos": ( 1.2, -1.2, 1.8), "size": 4.0, "energy": 1000, "color": (1, 1, 1)},
            {"name": "Fill", "pos": (-1.4, -1.0, 1.4), "size": 4.0, "energy":  800, "color": (1, 1, 1)},
            {"name": "Rim",  "pos": ( 0.0,  1.8, 2.4), "size": 3.5, "energy":  900, "color": (1, 1, 1)},
        ],
    },
}

CAMERA_COLL = "MV_Cameras"
LIGHT_COLL = "MV_Lighting"
PRODUCT_COLL = "MV_Products"

GUIDE_LINES = [
    "1. Scene Setup: pick a lighting preset and background, then Apply.",
    "2. Import: point to FreeCAD or STEPper, add STEP files, click Import.",
    "   Each file becomes a collection under MV_Products.",
    "   Fit on Import scales each one into a Target Size cube at the origin.",
    "   You can also create empty product collections and drop meshes in,",
    "   then click Standardize Products to fit them the same way.",
    "3. Cameras: pick a view set and front axis, click Build.",
    "4. Render: set output folder, mode (full/clay/both), starting index,",
    "   then Render All. Files are named PREFIX_NN_Product_View.",
    "",
    "Render engine, samples, resolution and file format stay under your",
    "control in Blender's Output and Render properties.",
]


class MV_AddonPreferences(AddonPreferences):
    bl_idname = __name__

    step_import_mode: EnumProperty(
        name="STEP Import Mode",
        items=[
            ('AUTO', "Auto", "Try STEPper first, then FreeCAD"),
            ('STEPPER', "STEPper Addon", "Use the STEPper addon"),
            ('FREECAD', "FreeCAD Subprocess", "Convert STEP to OBJ with FreeCAD"),
        ],
        default='AUTO',
    )
    freecad_path: StringProperty(
        name="FreeCAD Executable",
        subtype='FILE_PATH',
        description="Path to freecadcmd.exe (Windows) or freecadcmd (Linux/Mac)",
        default="",
    )
    tessellation_deflection: FloatProperty(
        name="Tessellation Deflection",
        description="Lower = finer mesh (FreeCAD mode)",
        default=0.1, min=0.001, max=5.0,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "step_import_mode")
        layout.prop(self, "freecad_path")
        layout.prop(self, "tessellation_deflection")


class MV_StepFileItem(PropertyGroup):
    path: StringProperty(name="Path", subtype='FILE_PATH')


class MV_CustomView(PropertyGroup):
    name: StringProperty(name="Name", default="CustomView")
    dir_x: FloatProperty(name="X", default=1.0)
    dir_y: FloatProperty(name="Y", default=-1.0)
    dir_z: FloatProperty(name="Z", default=1.0)
    is_ortho: BoolProperty(name="Ortho", default=False)


class MV_Settings(PropertyGroup):
    step_files: CollectionProperty(type=MV_StepFileItem)
    step_files_index: IntProperty(default=0)

    view_set: EnumProperty(
        name="Views",
        items=[
            ("ALL",   "All",          "Front, Back, Top, Bottom, Right, Left, three perspectives"),
            ("ORTHO", "Orthographic", "Front, Back, Top, Bottom, Right, Left"),
            ("STD",   "Standard",     "Front, Top, Side, Perspective"),
            ("MIN",   "Minimal",      "Front, Top, Side"),
        ],
        default="ALL",
    )

    front_axis: EnumProperty(
        name="Front Axis",
        description="World axis pointing out of the front of your product",
        items=[
            ('+X', "+X", "Front camera looks along -X (matches most CAD exports)"),
            ('-Y', "-Y", "Blender default"),
            ('+Y', "+Y", ""),
            ('-X', "-X", ""),
        ],
        default='+X',
    )

    lighting_preset: EnumProperty(
        name="Lighting",
        items=[(k, v["label"], v["label"]) for k, v in LIGHTING_PRESETS.items()],
        default="STUDIO_SOFT",
    )
    lighting_intensity: FloatProperty(
        name="Intensity",
        description="Multiplier on preset light energies",
        default=1.0, min=0.0, soft_max=5.0,
    )

    transparent_bg: BoolProperty(name="Transparent Background", default=False)
    bg_color: FloatVectorProperty(
        name="Background Color", subtype='COLOR', size=4,
        min=0.0, max=1.0, default=(0.5, 0.5, 0.5, 1.0),
    )

    render_output_dir: StringProperty(
        name="Output Directory", subtype='DIR_PATH', default="//renders/",
    )
    render_mode: EnumProperty(
        name="Render Mode",
        items=[
            ('FULL', "Full Render", "Standard render using your current engine"),
            ('CLAY', "Clay (Workbench)", "Viewport-style clay render"),
            ('BOTH', "Both", "Save both a full render and a clay render"),
        ],
        default='CLAY',
    )
    clay_color: FloatVectorProperty(
        name="Clay Color", subtype='COLOR', size=3,
        min=0.0, max=1.0, default=(0.75, 0.75, 0.75),
    )

    start_prefix: StringProperty(
        name="Start Prefix",
        description="Two-letter prefix for the first product (AA, BB, ...)",
        default="AA", maxlen=3,
    )
    start_number: IntProperty(
        name="Start Number",
        description="Number for the first product in this batch (1-50)",
        default=1, min=1, max=50,
    )
    products_per_letter: IntProperty(
        name="Products per Letter",
        description="Bump to the next letter pair after this many products",
        default=50, min=1, max=999,
    )

    frame_padding_factor: FloatProperty(
        name="Framing Padding", default=1.4, min=1.0, max=5.0,
    )

    custom_views: CollectionProperty(type=MV_CustomView)
    custom_views_index: IntProperty(default=0)

    recenter: BoolProperty(
        name="Recenter",
        description="Move each product's bounding box centre onto the world origin, "
                    "keeping its parts in their relative positions",
        default=True,
    )
    rescale: BoolProperty(
        name="Rescale",
        description="Uniformly scale every product to fit inside a cube of Target Size",
        default=True,
    )
    target_size: FloatProperty(
        name="Target Size",
        description="Max dimension (m) each product is scaled to fit within",
        default=1.0, min=0.0001, soft_max=10.0,
    )
    auto_smooth: BoolProperty(
        name="Auto Smooth",
        description="Clear the sharp edges and split normals a CAD tessellation "
                    "imports with, then shade smooth under the angle threshold",
        default=True,
    )
    smooth_angle: FloatProperty(
        name="Smooth Angle",
        description="Faces meeting at a sharper angle than this keep a hard edge",
        default=math.radians(30.0), min=0.0, max=math.radians(180.0),
        subtype='ANGLE',
    )
    fit_on_import: BoolProperty(
        name="Fit on Import",
        description="Straight after importing, scale each product to fit inside a cube of "
                    "Target Size and centre it on the world origin",
        default=True,
    )

    show_products_list: BoolProperty(default=False)


def ensure_collection(name, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def get_products_root():
    return ensure_collection(PRODUCT_COLL)


def get_products():
    return list(get_products_root().children)


def bbox_of(objs):
    lo = Vector((float("inf"),) * 3)
    hi = Vector((float("-inf"),) * 3)
    for o in objs:
        if o.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}:
            continue
        for corner in o.bound_box:
            world = o.matrix_world @ Vector(corner)
            for i in range(3):
                if world[i] < lo[i]: lo[i] = world[i]
                if world[i] > hi[i]: hi[i] = world[i]
    if lo.x == float("inf"):
        return Vector((0, 0, 0)), Vector((1, 1, 1))
    return (lo + hi) * 0.5, hi - lo


def rotate_dir_z(vec, degrees):
    theta = math.radians(degrees)
    c, s = math.cos(theta), math.sin(theta)
    x, y, z = vec
    return (x * c - y * s, x * s + y * c, z)


def look_at(location, target, up=Vector((0, 0, 1))):
    forward = (target - location).normalized()
    z = -forward
    if abs(z.dot(up)) > 0.999:
        up = Vector((0, 1, 0))
    x = up.cross(z).normalized()
    y = z.cross(x).normalized()
    return Matrix((
        (x.x, y.x, z.x, location.x),
        (x.y, y.y, z.y, location.y),
        (x.z, y.z, z.z, location.z),
        (0.0, 0.0, 0.0, 1.0),
    ))


def clear_collection_objects(coll):
    if coll is None:
        return
    for obj in list(coll.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def collection_meshes(coll):
    return [o for o in coll.all_objects if o.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}]


def collection_roots(coll):
    """Objects in coll whose parent lives outside it. Transforming these moves
    the whole assembly once; children follow through their parent."""
    members = set(coll.all_objects)
    return [o for o in members if o.parent is None or o.parent not in members]


def fit_collection(coll, target_size=None, recenter=True):
    """Uniformly scale a product so its bounding box fits inside a cube of
    target_size, and optionally move that box onto the world origin.

    Pass target_size=None to only recenter. Returns False if the collection
    holds no geometry. The whole product is treated as one rigid assembly, so
    parts keep their relative positions.
    """
    objs = collection_meshes(coll)
    if not objs:
        return False

    center, size = bbox_of(objs)
    factor = 1.0
    if target_size:
        max_dim = max(size.x, size.y, size.z)
        if max_dim > 1e-9:
            factor = target_size / max_dim

    dest = Vector((0, 0, 0)) if recenter else center
    if abs(factor - 1.0) < 1e-9 and (dest - center).length < 1e-9:
        return True

    xform = (Matrix.Translation(dest)
             @ Matrix.Scale(factor, 4)
             @ Matrix.Translation(-center))
    for obj in collection_roots(coll):
        obj.matrix_world = xform @ obj.matrix_world
    return True


def parent_depth(obj):
    depth, parent = 0, obj.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def push_empty_scale_down(coll):
    """Empties can carry scale but hold no data to bake it into, so a parented
    assembly would keep its fit scale stuck on the root.

    Walk the hierarchy top-down clearing each empty's scale while pinning its
    children's world matrices, which slides the scale onto the meshes at the
    leaves where transform_apply can reach it.
    """
    empties = [o for o in coll.all_objects if o.type == 'EMPTY']
    for empty in sorted(empties, key=parent_depth):
        if all(abs(v - 1.0) < 1e-9 for v in empty.scale):
            continue
        bpy.context.view_layer.update()
        pinned = [(c, c.matrix_world.copy()) for c in empty.children]
        empty.scale = (1.0, 1.0, 1.0)
        bpy.context.view_layer.update()
        for child, world in pinned:
            child.matrix_world = world


def bake_product(coll, apply_scale=True, origin_to_world=True):
    """Fold a fitted product's transform into its data: scale values back to
    1.0, and every object origin parked on the world origin.

    Geometry does not move -- this only changes where the transform is stored,
    so the product keeps the position and size fit_collection gave it while
    reporting clean values in the N panel.

    Blender refuses to edit multi-user or linked data, so those objects are
    returned as skipped rather than silently half-processed.
    """
    ctx = bpy.context
    members = collection_meshes(coll)
    if not members:
        return [], []
    if ctx.mode != 'OBJECT':
        return [], [o.name for o in members]

    editable = [o for o in members
                if o.name in ctx.view_layer.objects
                and o.library is None
                and o.data is not None
                and o.data.library is None
                and o.data.users == 1]
    skipped = [o.name for o in members if o not in editable]
    if not editable:
        return [], skipped

    if apply_scale:
        push_empty_scale_down(coll)

    ctx.view_layer.update()
    cursor = ctx.scene.cursor
    saved_cursor = cursor.location.copy()
    cursor.location = (0.0, 0.0, 0.0)
    try:
        with ctx.temp_override(active_object=editable[0], object=editable[0],
                               selected_objects=editable,
                               selected_editable_objects=editable):
            if apply_scale:
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            if origin_to_world:
                bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
    finally:
        cursor.location = saved_cursor
    return [o.name for o in editable], skipped


def smooth_product(coll, angle):
    """Give a tessellated CAD import the shading it should have had.

    A STEP tessellation arrives faceted twice over: FreeCAD writes per-face
    normals that Blender keeps as custom split normals, and some Blender
    versions additionally mark most edges sharp on import. Either one pins the
    flat look no matter what shading you ask for, which is why the fix needs
    clearing before smoothing rather than smoothing alone.

    Sharpness is then re-derived from the angle between neighbouring faces and
    baked into the mesh, rather than left to a Smooth by Angle modifier: a
    batch here runs to millions of triangles and the modifier would be
    re-evaluated on every render.

    Returns (done, skipped) name lists.
    """
    ctx = bpy.context
    meshes = [o for o in collection_meshes(coll) if o.type == 'MESH']
    if not meshes:
        return [], []
    if ctx.mode != 'OBJECT':
        return [], [o.name for o in meshes]

    editable = [o for o in meshes
                if o.name in ctx.view_layer.objects
                and o.library is None
                and o.data is not None
                and o.data.library is None]
    skipped = [o.name for o in meshes if o not in editable]
    if not editable:
        return [], skipped

    for obj in editable:
        me = obj.data
        if me.has_custom_normals:
            try:
                with ctx.temp_override(object=obj, active_object=obj,
                                       selected_objects=[obj],
                                       selected_editable_objects=[obj]):
                    bpy.ops.mesh.customdata_custom_splitnormals_clear()
            except Exception:
                pass
        if len(me.edges):
            me.edges.foreach_set("use_edge_sharp", [False] * len(me.edges))
        if len(me.polygons):
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
        me.update()

    with ctx.temp_override(object=editable[0], active_object=editable[0],
                           selected_objects=editable,
                           selected_editable_objects=editable):
        if hasattr(bpy.ops.object, "shade_smooth_by_angle"):
            bpy.ops.object.shade_smooth_by_angle(angle=angle)
        else:
            # Blender 4.0 kept auto smooth as a mesh property instead.
            try:
                bpy.ops.object.shade_smooth(use_auto_smooth=True,
                                            auto_smooth_angle=angle)
            except TypeError:
                bpy.ops.object.shade_smooth()
                for obj in editable:
                    if hasattr(obj.data, "use_auto_smooth"):
                        obj.data.use_auto_smooth = True
                        obj.data.auto_smooth_angle = angle
    return [o.name for o in editable], skipped


def find_layer_coll(layer_coll, name):
    if layer_coll.collection.name == name:
        return layer_coll
    for child in layer_coll.children:
        found = find_layer_coll(child, name)
        if found:
            return found
    return None


def alpha_pair(idx):
    idx = max(0, idx)
    if idx <= 25:
        c = chr(ord('A') + idx)
        return c + c
    n = 2 + (idx - 26) // 26
    c = chr(ord('A') + ((idx - 26) % 26))
    return c * n


def alpha_pair_to_idx(prefix):
    prefix = (prefix or "AA").strip().upper()
    if not prefix:
        return 0
    return max(0, ord(prefix[0]) - ord('A'))


def product_label(start_prefix, start_number, offset, per_letter):
    base = alpha_pair_to_idx(start_prefix)
    linear = (max(1, start_number) - 1) + offset
    bumps = linear // per_letter
    num = (linear % per_letter) + 1
    return f"{alpha_pair(base + bumps)}_{num:02d}"


STEPPER_OPERATORS = (
    "import_scene.occ_import_step",
    "import_scene.occ",
    "wm.stepper_import",
    "import_scene.stepper",
)

# Wall-clock ceiling for one FreeCAD conversion. Generous, because Esc now
# cancels an import that is genuinely wedged.
FREECAD_TIMEOUT = 900.0


def try_stepper_import(filepath):
    """True only if a STEPper-style operator actually reported success.

    Blender operators signal failure by RETURNING {'CANCELLED'} rather than
    raising, so the result set has to be inspected. Not doing that made AUTO
    mode read a cancelled import as a success and skip the FreeCAD fallback.
    """
    for op_path in STEPPER_OPERATORS:
        module, func = op_path.split(".", 1)
        mod = getattr(bpy.ops, module, None)
        op = getattr(mod, func, None) if mod else None
        if op is None:
            continue
        for kw in ("filepath", "filename"):
            try:
                if 'FINISHED' in op(**{kw: filepath}):
                    return True
            except TypeError:
                continue
            except Exception:
                break
    return False


def freecad_prepare(step_path, freecad_exe, deflection):
    """Write the conversion script for one STEP file. None if FreeCAD is not
    configured. Nothing is launched yet."""
    if not freecad_exe or not os.path.isfile(freecad_exe):
        return None
    tmp_dir = tempfile.mkdtemp(prefix="mv_step_")
    obj_out = os.path.join(tmp_dir, os.path.splitext(os.path.basename(step_path))[0] + ".obj")
    quoted_step = step_path.replace("\\", "\\\\").replace("'", "\'")
    quoted_obj = obj_out.replace("\\", "\\\\").replace("'", "\'")
    script = (
        "import Import, MeshPart, FreeCAD\n"
        "Import.open('%s')\n" % quoted_step +
        "doc = FreeCAD.ActiveDocument\n"
        "meshes = []\n"
        "for obj in doc.Objects:\n"
        "    if hasattr(obj, 'Shape') and obj.Shape and not obj.Shape.isNull():\n"
        "        try:\n"
        "            meshes.append(MeshPart.meshFromShape(Shape=obj.Shape, "
        "LinearDeflection=%r, AngularDeflection=0.5, Relative=False))\n" % float(deflection) +
        "        except Exception as e:\n"
        "            print('mesh failed', obj.Name, e)\n"
        "if meshes:\n"
        "    m = meshes[0]\n"
        "    for extra in meshes[1:]:\n"
        "        m.addMesh(extra)\n"
        "    m.write('%s')\n" % quoted_obj
    )
    script_path = os.path.join(tmp_dir, "convert.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    return {"tmp_dir": tmp_dir, "obj": obj_out, "script": script_path,
            "log": os.path.join(tmp_dir, "freecad.log"), "exe": freecad_exe,
            "proc": None, "handle": None}


def freecad_start(job):
    """Launch freecadcmd without waiting on it.

    stdin is closed rather than inherited: a FreeCAD build that stops to ask a
    question used to sit there holding Blender hostage for the whole timeout.
    Output goes to a file, not a pipe, so a chatty conversion cannot deadlock
    against a pipe buffer nobody is draining while we poll.
    """
    handle = open(job["log"], "w", encoding="utf-8", errors="replace")
    job["handle"] = handle
    job["proc"] = subprocess.Popen(
        [job["exe"], job["script"]],
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return job


def freecad_running(job):
    proc = job.get("proc")
    return proc is not None and proc.poll() is None


def freecad_collect(job):
    """Reap a finished job: (obj path or None, whatever FreeCAD printed)."""
    if job.get("handle") is not None:
        job["handle"].close()
        job["handle"] = None
    log = ""
    try:
        with open(job["log"], "r", encoding="utf-8", errors="replace") as f:
            log = f.read()
    except OSError:
        pass
    obj = job["obj"]
    if os.path.isfile(obj) and os.path.getsize(obj) > 0:
        return obj, log
    return None, log


def kill_process_tree(proc):
    """Stop the converter and anything it spawned.

    proc.kill() only signals the immediate child. A launcher that re-execs
    leaves the real worker alive, still holding the temp dir open, so a
    cancelled import would leak its scratch directory every time.
    """
    if proc is None or proc.poll() is not None:
        return
    if os.name == 'nt':
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def freecad_cleanup(job):
    kill_process_tree(job.get("proc"))
    if job.get("handle") is not None:
        job["handle"].close()
        job["handle"] = None
    # Windows keeps the log file locked for a moment after the writer dies.
    tmp_dir = job["tmp_dir"]
    for _ in range(5):
        if not os.path.isdir(tmp_dir):
            return
        try:
            shutil.rmtree(tmp_dir)
            return
        except OSError:
            time.sleep(0.1)
    shutil.rmtree(tmp_dir, ignore_errors=True)


def import_obj_file(path):
    """FreeCAD works Z-up, so tell the OBJ importer that instead of letting it
    apply its Y-up default and silently rotating every product 90 degrees."""
    if hasattr(bpy.ops.wm, "obj_import"):
        try:
            bpy.ops.wm.obj_import(filepath=path, forward_axis='Y', up_axis='Z')
        except TypeError:
            bpy.ops.wm.obj_import(filepath=path)
        return True
    if hasattr(bpy.ops.import_scene, "obj"):
        try:
            bpy.ops.import_scene.obj(filepath=path, axis_forward='Y', axis_up='Z')
        except TypeError:
            bpy.ops.import_scene.obj(filepath=path)
        return True
    return False


PERSP_LENS = 50.0
SENSOR_WIDTH = 36.0


def image_aspect(scene):
    """Effective pixel dimensions of the render, including pixel aspect."""
    r = scene.render
    rx = max(r.resolution_x * r.pixel_aspect_x, 1e-6)
    ry = max(r.resolution_y * r.pixel_aspect_y, 1e-6)
    return rx, ry


def sensor_tangents(rx, ry, lens=PERSP_LENS, sensor=SENSOR_WIDTH):
    """Half-FOV tangents on the image x and y axes, for sensor_fit='AUTO'.

    Blender fits the sensor width to the LONGER image axis, so the shorter axis
    sees a proportionally smaller sensor and a narrower field of view.
    """
    if rx >= ry:
        sx, sy = sensor, sensor * ry / rx
    else:
        sx, sy = sensor * rx / ry, sensor
    lens = max(lens, 1e-6)
    return (sx * 0.5) / lens, (sy * 0.5) / lens


def view_extents(basis, center, corners):
    """Where the product sits in one camera's frame: half-width, half-height,
    how far it reaches toward the camera, and the per-corner coordinates.

    Only the rotation of `basis` matters -- sliding a camera along its own view
    axis never changes where a point lands horizontally or vertically in frame.
    """
    right, up, back = basis.col[0].xyz, basis.col[1].xyz, basis.col[2].xyz
    ex = ey = 0.0
    depth = -float("inf")
    local = []
    for p in corners:
        rel = p - center
        cx, cy, cz = abs(rel.dot(right)), abs(rel.dot(up)), rel.dot(back)
        ex = max(ex, cx)
        ey = max(ey, cy)
        depth = max(depth, cz)
        local.append((cx, cy, cz))
    return ex, ey, depth, local


def ortho_span(ex, ey, rx, ry):
    """Half-extent ortho_scale has to cover.

    ortho_scale spans the LONGER image axis, so the shorter axis gets
    proportionally less room. Ignoring that is what made the old
    `ortho_scale = max_dim * pad` crop every view at 16:9.
    """
    if rx >= ry:
        return max(ex, ey * rx / ry)
    return max(ey, ex * ry / rx)


def persp_distance(local, tan_x, tan_y, pad):
    """Closest distance at which every corner still clears both frame edges."""
    distance = 0.0
    for cx, cy, cz in local:
        distance = max(distance, pad * cx / tan_x + cz, pad * cy / tan_y + cz)
    return distance


def build_cameras(context):
    settings = context.scene.mv_settings
    coll = ensure_collection(CAMERA_COLL)
    clear_collection_objects(coll)

    objs = []
    for pc in get_products():
        objs.extend(list(pc.all_objects))
    if not objs:
        objs = [o for o in bpy.data.objects if o.type == 'MESH']

    center, size = bbox_of(objs)
    half = size * 0.5
    corners = [center + Vector((x, y, z))
               for x in (-half.x, half.x)
               for y in (-half.y, half.y)
               for z in (-half.z, half.z)]
    diag = max(max((p - center).length for p in corners), 1e-3)
    pad = max(settings.frame_padding_factor, 1.0)
    rx, ry = image_aspect(context.scene)
    tan_x, tan_y = sensor_tangents(rx, ry)

    angle = FRONT_AXIS_ANGLES[settings.front_axis]
    views = list(VIEW_SETS[settings.view_set])
    for cv in settings.custom_views:
        views.append((cv.name, "ortho" if cv.is_ortho else "persp",
                      (cv.dir_x, cv.dir_y, cv.dir_z)))

    # Pass 1: measure every view and take the worst case, so one ortho framing
    # and one perspective framing cover the whole set. Sizing each camera to its
    # own silhouette would frame tighter but make the product change apparent
    # size from view to view, which defeats the point of a multiview sheet.
    plan = []
    skipped = []
    ortho_scale = 0.0
    persp_dist = 0.0
    reach = -float("inf")
    for name, kind, direction in views:
        d = Vector(rotate_dir_z(direction, angle))
        if d.length < 1e-9:
            skipped.append(name)
            continue
        d.normalize()
        ex, ey, depth, local = view_extents(look_at(center + d, center), center, corners)
        reach = max(reach, depth)
        if kind == "ortho":
            ortho_scale = max(ortho_scale, 2.0 * pad * ortho_span(ex, ey, rx, ry))
        else:
            persp_dist = max(persp_dist, persp_distance(local, tan_x, tan_y, pad))
        plan.append((name, kind, d))

    if not plan:
        return skipped
    ortho_scale = max(ortho_scale, 1e-4)
    ortho_dist = reach + diag
    persp_dist = max(persp_dist, reach + diag * 0.1)

    # Pass 2: build them on the shared framing.
    for name, kind, d in plan:
        cam_data = bpy.data.cameras.new(f"MV_Cam_{name}")
        if kind == "ortho":
            cam_data.type = 'ORTHO'
            cam_data.ortho_scale = ortho_scale
            distance = ortho_dist
        else:
            cam_data.type = 'PERSP'
            cam_data.lens = PERSP_LENS
            distance = persp_dist
        cam_data.clip_start = max(distance * 0.01, 1e-5)
        cam_data.clip_end = (distance + diag) * 4.0

        cam_obj = bpy.data.objects.new(f"MV_Cam_{name}", cam_data)
        cam_obj.matrix_world = look_at(center + d * distance, center)
        coll.objects.link(cam_obj)

    return skipped


def build_lighting(context, preset_key):
    settings = context.scene.mv_settings
    coll = ensure_collection(LIGHT_COLL)
    clear_collection_objects(coll)

    objs = []
    for pc in get_products():
        objs.extend(list(pc.all_objects))
    if not objs:
        objs = [o for o in bpy.data.objects if o.type == 'MESH']
    center, size = bbox_of(objs)
    max_dim = max(size.x, size.y, size.z, 1e-3)
    angle = FRONT_AXIS_ANGLES[settings.front_axis]
    intensity = settings.lighting_intensity

    for spec in LIGHTING_PRESETS[preset_key]["lights"]:
        ld = bpy.data.lights.new(f"MV_Light_{spec['name']}", type='AREA')
        ld.size = spec["size"] * max_dim * 0.5
        ld.energy = spec["energy"] * max_dim * intensity
        ld.color = spec["color"]
        obj = bpy.data.objects.new(f"MV_Light_{spec['name']}", ld)
        pos = Vector(rotate_dir_z(spec["pos"], angle)) * max_dim
        obj.matrix_world = look_at(center + pos, center)
        coll.objects.link(obj)


def apply_world(context):
    settings = context.scene.mv_settings
    scene = context.scene
    world = scene.world or bpy.data.worlds.new("MV_World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    bg = nodes.new("ShaderNodeBackground")
    out = nodes.new("ShaderNodeOutputWorld")
    bg.inputs["Color"].default_value = settings.bg_color
    bg.inputs["Strength"].default_value = 1.0
    links.new(bg.outputs["Background"], out.inputs["Surface"])
    scene.render.film_transparent = settings.transparent_bg


class MV_OT_AddStepFile(Operator, ImportHelper):
    bl_idname = "mv.add_step_file"
    bl_label = "Add STEP File"
    filter_glob: StringProperty(default="*.step;*.stp;*.STEP;*.STP", options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        s = context.scene.mv_settings
        if self.files:
            for f in self.files:
                if not f.name:
                    continue
                item = s.step_files.add()
                item.path = os.path.join(self.directory, f.name)
        elif self.filepath:
            item = s.step_files.add()
            item.path = self.filepath
        return {'FINISHED'}


class MV_OT_RemoveStepFile(Operator):
    bl_idname = "mv.remove_step_file"
    bl_label = "Remove"
    index: IntProperty()

    def execute(self, context):
        s = context.scene.mv_settings
        if 0 <= self.index < len(s.step_files):
            s.step_files.remove(self.index)
        return {'FINISHED'}


class MV_OT_ClearStepFiles(Operator):
    bl_idname = "mv.clear_step_files"
    bl_label = "Clear List"

    def execute(self, context):
        context.scene.mv_settings.step_files.clear()
        return {'FINISHED'}


class MV_OT_ImportAllSteps(Operator):
    """Import every STEP file in the list, one at a time.

    Runs modally: the conversion subprocess is polled between timer ticks
    instead of being waited on, so Blender keeps redrawing, the status bar
    shows which file is in flight, and Esc aborts. Called from a script it
    falls back to running straight through.
    """
    bl_idname = "mv.import_all_steps"
    bl_label = "Import STEP Files"
    bl_options = {'REGISTER', 'UNDO'}

    def _setup(self, context):
        s = context.scene.mv_settings
        if not s.step_files:
            self.report({'ERROR'}, "No STEP files in the list.")
            return False
        self._prefs = context.preferences.addons[__name__].preferences
        self._queue = [bpy.path.abspath(item.path) for item in s.step_files]
        self._total = len(self._queue)
        self._done = 0
        self._imported = 0
        self._problems = []
        self._unbaked = set()
        self._job = None
        self._job_path = None
        self._job_started = 0.0
        self._before = set()
        self._timer = None
        self._cancelled = False
        return True

    # -- one file ---------------------------------------------------------
    def _fail(self, path, reason):
        self._problems.append(f"{os.path.basename(path)}: {reason}")
        self._done += 1

    def _link_new(self, context, path, new_objs):
        root = get_products_root()
        base = os.path.splitext(os.path.basename(path))[0]
        product_coll = bpy.data.collections.get(base) or bpy.data.collections.new(base)
        if product_coll.name not in root.children:
            root.children.link(product_coll)
        for obj in new_objs:
            for c in list(obj.users_collection):
                c.objects.unlink(obj)
            product_coll.objects.link(obj)

        s = context.scene.mv_settings
        if s.fit_on_import:
            context.view_layer.update()
            if fit_collection(product_coll, s.target_size, recenter=True):
                _, unbaked = bake_product(product_coll)
                self._unbaked.update(unbaked)
        self._imported += 1
        self._done += 1

    def _new_objects(self):
        return [o for o in bpy.data.objects if o not in self._before]

    def _start_file(self, context, path):
        self._job_path = path
        if not os.path.isfile(path):
            self._fail(path, "file not found")
            return
        self._before = set(bpy.data.objects)
        mode = self._prefs.step_import_mode

        if mode in {'AUTO', 'STEPPER'} and try_stepper_import(path):
            new = self._new_objects()
            if new:
                self._link_new(context, path, new)
                return
            if mode == 'STEPPER':
                self._fail(path, "STEPper reported success but added no geometry")
                return

        if mode == 'STEPPER':
            self._fail(path, "STEPper import failed")
            return

        job = freecad_prepare(path, self._prefs.freecad_path,
                              self._prefs.tessellation_deflection)
        if job is None:
            self._fail(path, "no importer available (set the FreeCAD path in preferences)")
            return
        freecad_start(job)
        self._job = job
        self._job_started = time.monotonic()

    def _collect_job(self, context):
        job, self._job = self._job, None
        path = self._job_path
        obj_path, log = freecad_collect(job)
        if obj_path and import_obj_file(obj_path):
            new = self._new_objects()
            if new:
                self._link_new(context, path, new)
            else:
                self._fail(path, "converted mesh contained no geometry")
        else:
            tail = " ".join(log.split())[-160:]
            self._fail(path, "FreeCAD conversion failed"
                             + (f" - {tail}" if tail else " (no output)"))
        freecad_cleanup(job)

    # -- driver -----------------------------------------------------------
    def _advance(self, context, blocking):
        """One slice of work. False once everything is finished."""
        if self._job is not None:
            if blocking:
                self._job["proc"].wait()
            elif freecad_running(self._job):
                if time.monotonic() - self._job_started > FREECAD_TIMEOUT:
                    job, self._job = self._job, None
                    freecad_cleanup(job)
                    self._fail(self._job_path,
                               f"FreeCAD exceeded {int(FREECAD_TIMEOUT)}s and was stopped")
                return True
            self._collect_job(context)
            return bool(self._queue)
        if not self._queue:
            return False
        self._start_file(context, self._queue.pop(0))
        return True

    def _status(self):
        name = os.path.basename(self._job_path) if self._job_path else ""
        waiting = " (converting)" if self._job is not None else ""
        return f"MultiView: {self._done}/{self._total}  {name}{waiting}  -  Esc to cancel"

    def _report_result(self):
        if self._cancelled:
            level, msg = {'WARNING'}, f"Import cancelled after {self._imported} file(s)."
        elif not self._imported:
            level, msg = {'ERROR'}, "Nothing imported."
        else:
            level, msg = {'INFO'}, f"Imported {self._imported} of {self._total} file(s)."
        if self._unbaked:
            msg += (f" {len(self._unbaked)} object(s) kept their transform "
                    "(multi-user or linked data).")
        if self._problems:
            level = {'WARNING'} if self._imported else {'ERROR'}
            msg += " " + " | ".join(self._problems[:2])
            if len(self._problems) > 2:
                msg += f" | +{len(self._problems) - 2} more (see system console)"
            for problem in self._problems:
                print("[MultiView] " + problem)
        self.report(level, msg)
        return {'FINISHED'} if self._imported else {'CANCELLED'}

    def _teardown(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        wm.progress_end()
        try:
            context.workspace.status_text_set(None)
        except AttributeError:
            pass
        if self._job is not None:
            freecad_cleanup(self._job)
            self._job = None
        return self._report_result()

    # -- entry points -----------------------------------------------------
    def execute(self, context):
        if not self._setup(context):
            return {'CANCELLED'}
        while self._advance(context, blocking=True):
            pass
        return self._report_result()

    def invoke(self, context, event):
        if not self._setup(context):
            return {'CANCELLED'}
        wm = context.window_manager
        wm.progress_begin(0, self._total)
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC' and event.value == 'PRESS':
            self._cancelled = True
            return self._teardown(context)
        if event.type != 'TIMER':
            # Swallow everything else: the scene is mid-edit, so letting clicks
            # through here would be a good way to corrupt it.
            return {'RUNNING_MODAL'}

        more = self._advance(context, blocking=False)
        context.window_manager.progress_update(self._done)
        try:
            context.workspace.status_text_set(self._status())
        except AttributeError:
            pass
        if not more:
            return self._teardown(context)
        return {'RUNNING_MODAL'}


class MV_OT_ApplySceneSetup(Operator):
    bl_idname = "mv.apply_scene_setup"
    bl_label = "Apply Scene Setup"

    def execute(self, context):
        s = context.scene.mv_settings
        apply_world(context)
        build_lighting(context, s.lighting_preset)
        if get_products():
            build_cameras(context)
        self.report({'INFO'}, "Lighting, background, and cameras updated.")
        return {'FINISHED'}


class MV_OT_BuildCameras(Operator):
    bl_idname = "mv.build_cameras"
    bl_label = "Build Cameras"

    def execute(self, context):
        skipped = build_cameras(context)
        if skipped:
            self.report({'WARNING'},
                        "Skipped custom view(s) with a zero direction vector: "
                        + ", ".join(skipped))
        return {'FINISHED'}


class MV_OT_BuildLighting(Operator):
    bl_idname = "mv.build_lighting"
    bl_label = "Rebuild Lighting"

    def execute(self, context):
        build_lighting(context, context.scene.mv_settings.lighting_preset)
        return {'FINISHED'}


class MV_OT_ApplyWorld(Operator):
    bl_idname = "mv.apply_world"
    bl_label = "Apply Background"

    def execute(self, context):
        apply_world(context)
        return {'FINISHED'}


class MV_OT_AddCustomView(Operator):
    bl_idname = "mv.add_custom_view"
    bl_label = "Add Custom View"

    def execute(self, context):
        cv = context.scene.mv_settings.custom_views.add()
        cv.name = f"Custom_{len(context.scene.mv_settings.custom_views)}"
        return {'FINISHED'}


class MV_OT_RemoveCustomView(Operator):
    bl_idname = "mv.remove_custom_view"
    bl_label = "Remove"
    index: IntProperty()

    def execute(self, context):
        s = context.scene.mv_settings
        if 0 <= self.index < len(s.custom_views):
            s.custom_views.remove(self.index)
        return {'FINISHED'}


class MV_OT_Standardize(Operator):
    bl_idname = "mv.standardize"
    bl_label = "Standardize Products"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.mv_settings
        products = get_products()
        if not products:
            self.report({'ERROR'}, "No products to standardize.")
            return {'CANCELLED'}

        if not (s.recenter or s.rescale or s.auto_smooth):
            self.report({'WARNING'}, "Enable Recenter, Rescale or Auto Smooth first.")
            return {'CANCELLED'}

        context.view_layer.update()
        done = 0
        unbaked = set()
        smoothed = 0
        for pc in products:
            if s.recenter or s.rescale:
                if fit_collection(pc,
                                  target_size=s.target_size if s.rescale else None,
                                  recenter=s.recenter):
                    done += 1
                    _, skipped_objs = bake_product(pc,
                                                   apply_scale=s.rescale,
                                                   origin_to_world=s.recenter)
                    unbaked.update(skipped_objs)
            elif collection_meshes(pc):
                done += 1
            if s.auto_smooth:
                shaded, skipped_objs = smooth_product(pc, s.smooth_angle)
                smoothed += len(shaded)
                unbaked.update(skipped_objs)

        empty = len(products) - done
        msg = f"Standardized {done} product(s)."
        if s.auto_smooth:
            msg += f" Smoothed {smoothed} mesh(es)."
        if empty:
            msg += f" Skipped {empty} with no geometry."
        if unbaked:
            msg += (f" {len(unbaked)} object(s) kept their transform "
                    "(multi-user or linked data).")
        self.report({'INFO'} if not unbaked else {'WARNING'}, msg)
        return {'FINISHED'}


class MV_OT_AddProductCollection(Operator):
    bl_idname = "mv.add_product_collection"
    bl_label = "Add Empty Product Collection"
    name: StringProperty(name="Name", default="Product")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        root = get_products_root()
        n = self.name or "Product"
        if bpy.data.collections.get(n) is None:
            root.children.link(bpy.data.collections.new(n))
        return {'FINISHED'}


def _do_full_render(scene, filepath):
    scene.render.filepath = filepath
    bpy.ops.render.render(write_still=True)


def _do_clay_render(scene, filepath, clay_color):
    saved = {
        'engine': scene.render.engine,
        'shading_type': scene.display.shading.type,
        'light': scene.display.shading.light,
        'color_type': scene.display.shading.color_type,
        'single_color': tuple(scene.display.shading.single_color),
        'show_specular': scene.display.shading.show_specular_highlight,
        'show_cavity': scene.display.shading.show_cavity,
    }
    try:
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.display.shading.type = 'SOLID'
        scene.display.shading.light = 'STUDIO'
        scene.display.shading.color_type = 'SINGLE'
        scene.display.shading.single_color = clay_color
        scene.display.shading.show_specular_highlight = False
        scene.display.shading.show_cavity = True
        scene.render.filepath = filepath
        bpy.ops.render.render(write_still=True)
    finally:
        scene.render.engine = saved['engine']
        scene.display.shading.type = saved['shading_type']
        scene.display.shading.light = saved['light']
        scene.display.shading.color_type = saved['color_type']
        scene.display.shading.single_color = saved['single_color']
        scene.display.shading.show_specular_highlight = saved['show_specular']
        scene.display.shading.show_cavity = saved['show_cavity']


class MV_OT_RenderAll(Operator):
    bl_idname = "mv.render_all"
    bl_label = "Render All"

    def execute(self, context):
        scene = context.scene
        s = scene.mv_settings

        cam_coll = bpy.data.collections.get(CAMERA_COLL)
        cameras = [o for o in cam_coll.objects if o.type == 'CAMERA'] if cam_coll else []
        if not cameras:
            self.report({'ERROR'}, "No cameras. Build cameras first.")
            return {'CANCELLED'}

        products = get_products()
        if not products:
            self.report({'ERROR'}, f"No product collections under '{PRODUCT_COLL}'.")
            return {'CANCELLED'}

        out_root = bpy.path.abspath(s.render_output_dir)
        os.makedirs(out_root, exist_ok=True)

        saved_hide = {}
        for pc in products:
            lc = find_layer_coll(context.view_layer.layer_collection, pc.name)
            if lc:
                saved_hide[pc.name] = lc.exclude
        saved_cam = scene.camera
        saved_path = scene.render.filepath

        mode = s.render_mode
        both = mode == 'BOTH'
        total_per_pair = (2 if both else 1) * len(cameras)
        total = len(products) * total_per_pair
        done = 0

        try:
            for i, pc in enumerate(products):
                for other in products:
                    lc = find_layer_coll(context.view_layer.layer_collection, other.name)
                    if lc:
                        lc.exclude = (other.name != pc.name)

                label = product_label(s.start_prefix, s.start_number, i, s.products_per_letter)
                product_dir = os.path.join(out_root, f"{label}_{pc.name}")
                os.makedirs(product_dir, exist_ok=True)

                for cam in cameras:
                    scene.camera = cam
                    view_name = cam.name.replace("MV_Cam_", "")
                    base = f"{label}_{pc.name}_{view_name}"

                    if mode == 'FULL':
                        _do_full_render(scene, os.path.join(product_dir, base))
                        done += 1
                    elif mode == 'CLAY':
                        _do_clay_render(scene, os.path.join(product_dir, base + "_clay"),
                                        tuple(s.clay_color))
                        done += 1
                    else:
                        _do_full_render(scene, os.path.join(product_dir, base + "_render"))
                        done += 1
                        _do_clay_render(scene, os.path.join(product_dir, base + "_clay"),
                                        tuple(s.clay_color))
                        done += 1
                    print(f"[MultiView] {done}/{total}: {scene.render.filepath}")
        finally:
            for pc in products:
                lc = find_layer_coll(context.view_layer.layer_collection, pc.name)
                if lc and pc.name in saved_hide:
                    lc.exclude = saved_hide[pc.name]
            scene.camera = saved_cam
            scene.render.filepath = saved_path

        self.report({'INFO'}, f"Wrote {done} image(s).")
        return {'FINISHED'}


class MV_UL_StepFiles(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=os.path.basename(item.path) or "(empty)", icon='FILE')
        row.operator("mv.remove_step_file", text="", icon='X').index = index


class MV_UL_CustomViews(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="")
        row.prop(item, "dir_x", text="X")
        row.prop(item, "dir_y", text="Y")
        row.prop(item, "dir_z", text="Z")
        row.prop(item, "is_ortho", text="", icon='VIEW_ORTHO' if item.is_ortho else 'VIEW_PERSPECTIVE')
        row.operator("mv.remove_custom_view", text="", icon='X').index = index


class MV_PT_Guide(Panel):
    bl_label = "Guide"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiView"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        for line in GUIDE_LINES:
            box.label(text=line)


class MV_PT_SceneSetup(Panel):
    bl_label = "Scene Setup"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiView"

    def draw(self, context):
        layout = self.layout
        s = context.scene.mv_settings

        col = layout.column(align=True)
        col.label(text="Lighting:")
        col.prop(s, "lighting_preset", text="")
        col.prop(s, "lighting_intensity", slider=True)

        col = layout.column(align=True)
        col.label(text="Background:")
        col.prop(s, "transparent_bg")
        row = col.row()
        row.enabled = not s.transparent_bg
        row.prop(s, "bg_color", text="")

        layout.separator()
        layout.operator("mv.apply_scene_setup", icon='SCENE_DATA')

        row = layout.row(align=True)
        row.operator("mv.build_lighting", text="Lighting Only", icon='LIGHT')
        row.operator("mv.apply_world", text="Background Only", icon='WORLD')


class MV_PT_Import(Panel):
    bl_label = "Import Products"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiView"

    def draw(self, context):
        layout = self.layout
        s = context.scene.mv_settings
        prefs = context.preferences.addons[__name__].preferences

        box = layout.box()
        box.label(text="STEP Importer:")
        box.prop(prefs, "step_import_mode", text="Mode")
        col = box.column()
        col.enabled = prefs.step_import_mode in {'AUTO', 'FREECAD'}
        col.prop(prefs, "freecad_path", text="FreeCAD")

        layout.separator()
        row = layout.row()
        row.template_list("MV_UL_StepFiles", "", s, "step_files", s, "step_files_index", rows=3)
        col = row.column(align=True)
        col.operator("mv.add_step_file", text="", icon='ADD')
        col.operator("mv.clear_step_files", text="", icon='TRASH')

        layout.operator("mv.import_all_steps", icon='IMPORT')
        layout.operator("mv.add_product_collection", icon='OUTLINER_COLLECTION')

        prods = get_products()
        header = layout.row(align=True)
        header.prop(s, "show_products_list", text="",
                    icon='TRIA_DOWN' if s.show_products_list else 'TRIA_RIGHT', emboss=False)
        header.label(text=f"Products under '{PRODUCT_COLL}' ({len(prods)})")
        if s.show_products_list:
            box = layout.box()
            if not prods:
                box.label(text="(none)", icon='INFO')
            else:
                for pc in prods:
                    box.label(text=f"{pc.name}  ({len(pc.all_objects)} objs)", icon='OUTLINER_COLLECTION')

        layout.separator()
        box = layout.box()
        box.label(text="Standardization:")
        box.prop(s, "fit_on_import")
        row = box.row(align=True)
        row.prop(s, "recenter", toggle=True)
        row.prop(s, "rescale", toggle=True)
        sub = box.row()
        sub.enabled = s.rescale or s.fit_on_import
        sub.prop(s, "target_size")
        row = box.row(align=True)
        row.prop(s, "auto_smooth", toggle=True)
        sub = row.row()
        sub.enabled = s.auto_smooth
        sub.prop(s, "smooth_angle", text="")
        box.operator("mv.standardize", icon='FULLSCREEN_EXIT')


class MV_PT_Cameras(Panel):
    bl_label = "Cameras / Views"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiView"

    def draw(self, context):
        layout = self.layout
        s = context.scene.mv_settings
        layout.prop(s, "view_set")
        layout.prop(s, "front_axis")
        layout.prop(s, "frame_padding_factor")

        layout.label(text="Custom Views (direction from target):")
        row = layout.row()
        row.template_list("MV_UL_CustomViews", "", s, "custom_views", s, "custom_views_index", rows=2)
        row.column(align=True).operator("mv.add_custom_view", text="", icon='ADD')

        layout.operator("mv.build_cameras", icon='CAMERA_DATA')


class MV_PT_Render(Panel):
    bl_label = "Batch Render"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MultiView"

    def draw(self, context):
        layout = self.layout
        s = context.scene.mv_settings
        layout.prop(s, "render_output_dir")
        layout.prop(s, "render_mode")
        if s.render_mode in {'CLAY', 'BOTH'}:
            layout.prop(s, "clay_color")

        box = layout.box()
        box.label(text="Naming:")
        row = box.row(align=True)
        row.prop(s, "start_prefix")
        row.prop(s, "start_number")
        box.prop(s, "products_per_letter")

        prods = get_products()
        if prods:
            first = product_label(s.start_prefix, s.start_number, 0, s.products_per_letter)
            last = product_label(s.start_prefix, s.start_number, len(prods) - 1, s.products_per_letter)
            box.label(text=f"First: {first}_{prods[0].name}", icon='FILE')
            box.label(text=f"Last:  {last}_{prods[-1].name}", icon='FILE')

        layout.operator("mv.render_all", icon='RENDER_STILL')

        cam_coll = bpy.data.collections.get(CAMERA_COLL)
        n_cams = len([o for o in cam_coll.objects if o.type == 'CAMERA']) if cam_coll else 0
        mult = 2 if s.render_mode == 'BOTH' else 1
        layout.label(text=f"{len(prods)} products x {n_cams} views x {mult} = {len(prods) * n_cams * mult} images")
        layout.label(text="Engine/samples/resolution/format: Blender Output props", icon='INFO')


classes = (
    MV_AddonPreferences,
    MV_StepFileItem,
    MV_CustomView,
    MV_Settings,
    MV_OT_AddStepFile,
    MV_OT_RemoveStepFile,
    MV_OT_ClearStepFiles,
    MV_OT_ImportAllSteps,
    MV_OT_ApplySceneSetup,
    MV_OT_BuildCameras,
    MV_OT_BuildLighting,
    MV_OT_ApplyWorld,
    MV_OT_AddCustomView,
    MV_OT_RemoveCustomView,
    MV_OT_AddProductCollection,
    MV_OT_Standardize,
    MV_OT_RenderAll,
    MV_UL_StepFiles,
    MV_UL_CustomViews,
    MV_PT_Guide,
    MV_PT_SceneSetup,
    MV_PT_Import,
    MV_PT_Cameras,
    MV_PT_Render,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.mv_settings = PointerProperty(type=MV_Settings)


def unregister():
    del bpy.types.Scene.mv_settings
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
