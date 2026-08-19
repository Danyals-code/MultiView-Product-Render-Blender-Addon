bl_info = {
    "name": "MultiView Product Renderer",
    "author": "MultiView",
    "version": (1, 2, 0),
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
    "   You can also create empty product collections and drop meshes in.",
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
        description="For each product: origin to 3D cursor, then geometry to origin (product ends up at world origin)",
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


def recenter_collection(coll):
    objs = collection_meshes(coll)
    if not objs:
        return
    original_cursor = tuple(bpy.context.scene.cursor.location)
    bpy.context.scene.cursor.location = (0.0, 0.0, 0.0)
    try:
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
        bpy.ops.object.origin_set(type='GEOMETRY_ORIGIN')
    finally:
        bpy.context.scene.cursor.location = original_cursor


def collection_max_dim(coll):
    objs = collection_meshes(coll)
    if not objs:
        return 0.0
    _, size = bbox_of(objs)
    return max(size.x, size.y, size.z, 1e-9)


def scale_collection(coll, factor, pivot=Vector((0, 0, 0))):
    if abs(factor - 1.0) < 1e-9:
        return
    for obj in collection_meshes(coll):
        obj.location = pivot + (obj.location - pivot) * factor
        obj.scale = Vector((obj.scale.x * factor, obj.scale.y * factor, obj.scale.z * factor))


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


def try_stepper_import(filepath):
    candidates = [
        "import_scene.occ_import_step",
        "import_scene.occ",
        "wm.stepper_import",
        "import_scene.stepper",
    ]
    for op_path in candidates:
        module, func = op_path.split(".", 1)
        mod = getattr(bpy.ops, module, None)
        op = getattr(mod, func, None) if mod else None
        if op is None:
            continue
        for kw in ("filepath", "filename"):
            try:
                op(**{kw: filepath})
                return True
            except TypeError:
                continue
            except Exception:
                break
    return False


def freecad_convert(step_path, freecad_exe, deflection):
    if not freecad_exe or not os.path.isfile(freecad_exe):
        return None
    tmp_dir = tempfile.mkdtemp(prefix="mv_step_")
    obj_out = os.path.join(tmp_dir, os.path.splitext(os.path.basename(step_path))[0] + ".obj")
    script = (
        "import Import, MeshPart, FreeCAD\n"
        f"Import.open(r'''{step_path}''')\n"
        "doc = FreeCAD.ActiveDocument\n"
        "meshes = []\n"
        "for obj in doc.Objects:\n"
        "    if hasattr(obj, 'Shape') and obj.Shape and not obj.Shape.isNull():\n"
        "        try:\n"
        f"            meshes.append(MeshPart.meshFromShape(Shape=obj.Shape, LinearDeflection={deflection}, AngularDeflection=0.5, Relative=False))\n"
        "        except Exception as e:\n"
        "            print('mesh failed', obj.Name, e)\n"
        "if meshes:\n"
        "    m = meshes[0]\n"
        "    for extra in meshes[1:]:\n"
        "        m.addMesh(extra)\n"
        f"    m.write(r'''{obj_out}''')\n"
    )
    script_path = os.path.join(tmp_dir, "convert.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)
    try:
        subprocess.run([freecad_exe, script_path], capture_output=True, text=True, timeout=300)
    except Exception as e:
        print("FreeCAD subprocess error:", e)
        return None
    return obj_out if os.path.isfile(obj_out) and os.path.getsize(obj_out) > 0 else None


def import_obj_file(path):
    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=path)
        return True
    if hasattr(bpy.ops.import_scene, "obj"):
        bpy.ops.import_scene.obj(filepath=path)
        return True
    return False


def import_step(filepath, prefs):
    before = set(bpy.data.objects)
    ok = False
    if prefs.step_import_mode in {'AUTO', 'STEPPER'}:
        ok = try_stepper_import(filepath)
    if not ok and prefs.step_import_mode in {'AUTO', 'FREECAD'}:
        obj_path = freecad_convert(filepath, prefs.freecad_path, prefs.tessellation_deflection)
        if obj_path:
            ok = import_obj_file(obj_path)
    if not ok:
        return None
    return list(set(bpy.data.objects) - before)


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
    max_dim = max(size.x, size.y, size.z, 1e-3)
    pad = settings.frame_padding_factor
    distance = max_dim * pad * 2.0
    ortho_scale = max_dim * pad

    angle = FRONT_AXIS_ANGLES[settings.front_axis]
    views = list(VIEW_SETS[settings.view_set])
    for cv in settings.custom_views:
        views.append((cv.name, "ortho" if cv.is_ortho else "persp",
                      (cv.dir_x, cv.dir_y, cv.dir_z)))

    for name, kind, direction in views:
        d = Vector(rotate_dir_z(direction, angle)).normalized()
        loc = center + d * distance
        cam_data = bpy.data.cameras.new(f"MV_Cam_{name}")
        if kind == "ortho":
            cam_data.type = 'ORTHO'
            cam_data.ortho_scale = ortho_scale
        else:
            cam_data.type = 'PERSP'
            cam_data.lens = 50.0
        cam_obj = bpy.data.objects.new(f"MV_Cam_{name}", cam_data)
        cam_obj.matrix_world = look_at(loc, center)
        coll.objects.link(cam_obj)


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
    bl_idname = "mv.import_all_steps"
    bl_label = "Import STEP Files"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        s = context.scene.mv_settings
        if not s.step_files:
            self.report({'ERROR'}, "No STEP files in the list.")
            return {'CANCELLED'}

        root = get_products_root()
        imported = 0
        for item in s.step_files:
            path = bpy.path.abspath(item.path)
            if not os.path.isfile(path):
                self.report({'WARNING'}, f"Missing file: {path}")
                continue
            base = os.path.splitext(os.path.basename(path))[0]
            product_coll = bpy.data.collections.get(base) or bpy.data.collections.new(base)
            if product_coll.name not in root.children:
                root.children.link(product_coll)

            new_objs = import_step(path, prefs)
            if not new_objs:
                self.report({'WARNING'}, f"Failed to import: {path}")
                continue
            for obj in new_objs:
                for c in list(obj.users_collection):
                    c.objects.unlink(obj)
                product_coll.objects.link(obj)
            imported += 1

        if not imported:
            return {'CANCELLED'}
        self.report({'INFO'}, f"Imported {imported} file(s).")
        return {'FINISHED'}


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
        build_cameras(context)
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

    def execute(self, context):
        s = context.scene.mv_settings
        products = get_products()
        if not products:
            self.report({'ERROR'}, "No products to standardize.")
            return {'CANCELLED'}

        if not (s.recenter or s.rescale):
            self.report({'WARNING'}, "Enable Recenter and/or Rescale first.")
            return {'CANCELLED'}

        if s.recenter:
            for pc in products:
                recenter_collection(pc)

        if s.rescale:
            target = max(s.target_size, 1e-6)
            for pc in products:
                cur = collection_max_dim(pc)
                if cur > 0:
                    scale_collection(pc, target / cur)

        self.report({'INFO'}, f"Standardized {len(products)} product(s).")
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
        row = box.row(align=True)
        row.prop(s, "recenter", toggle=True)
        row.prop(s, "rescale", toggle=True)
        sub = box.row()
        sub.enabled = s.rescale
        sub.prop(s, "target_size")
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
