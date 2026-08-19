# MultiView Product Renderer

A Blender add-on for turning CAD products into consistent, batch-rendered image sets.

Import STEP files, standardize them at the world origin, build a rig of orthographic and
perspective cameras plus 3-point studio lighting, then render every product across every
view in one click, with predictable file names like `AA_01_Widget_Front_clay.png`.

- **Blender:** 4.0 and newer
- **Panel location:** `3D Viewport → Sidebar (N key) → MultiView`
- **Version:** 1.2.0

---

## Install

### Option A: download the release zip (recommended)

1. Go to the [**Releases**](../../releases/latest) page and download
   **`multiview_product_renderer.zip`**.
2. In Blender: `Edit → Preferences → Add-ons`.
   - Blender **4.2+**: `Install from Disk...` (in the top-right dropdown menu)
   - Blender **4.0 / 4.1**: `Install...`
3. Pick the zip you downloaded. **Do not unzip it first.**
4. Tick the checkbox next to **MultiView Product Renderer** to enable it.
5. Press `N` in the 3D viewport and open the **MultiView** tab.

### Option B: clone the repo

```bash
git clone https://github.com/Danyals-code/MultiView-Product-Render-Blender-Addon.git
```

Copy the `multiview_product_renderer` folder into your Blender add-ons directory:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\Blender Foundation\Blender\4.x\scripts\addons\` |
| macOS | `~/Library/Application Support/Blender/4.x/scripts/addons/` |
| Linux | `~/.config/blender/4.x/scripts/addons/` |

Restart Blender and enable the add-on as in step 4 above.

---

## STEP import setup (optional)

You only need this if you want to import `.step` / `.stp` files directly. If you already
have meshes in your scene, skip it. The add-on works with any geometry.

Open `Edit → Preferences → Add-ons → MultiView Product Renderer` and expand the
preferences:

| Setting | What it does |
| --- | --- |
| **STEP Import Mode** | `Auto` tries the STEPper add-on first, then falls back to FreeCAD. Or force one. |
| **FreeCAD Executable** | Path to `freecadcmd.exe` (Windows) or `freecadcmd` (macOS/Linux). |
| **Tessellation Deflection** | FreeCAD mode only. Lower = finer mesh, bigger files. Default `0.1`. |

**FreeCAD route**: install [FreeCAD](https://www.freecad.org/) and point the preference at
its console binary, typically:

```
C:\Program Files\FreeCAD 0.21\bin\freecadcmd.exe
```

The add-on shells out to FreeCAD, tessellates each solid into a mesh, writes a temporary
OBJ, and imports that. Nothing is left behind in your FreeCAD install.

**STEPper route**: if you own the STEPper add-on, install and enable it; MultiView detects
and uses its import operator automatically.

---

## Workflow

The sidebar is laid out in the order you should use it.

### 1. Scene Setup

Pick a lighting preset and a background, then **Apply Scene Setup**. This builds an
`MV_Lights` collection and sets the world background.

| Lighting preset | Look |
| --- | --- |
| **Studio Soft** | Large soft area lights, even fill. Default. |
| **Studio Hard** | Smaller lights, crisper shadows and highlights. |
| **Dramatic** | Strong key, deep falloff, minimal fill. |
| **High Key** | Bright, near-shadowless, white-on-white product look. |

**Intensity** multiplies the preset's light energies. **Transparent Background** renders
alpha instead of the **Background Color**. `Rebuild Lighting` and `Apply Background` re-run
either half on its own.

### 2. Import Products

Add one or more STEP files to the list and click **Import STEP Files**. Each file becomes
its own collection under `MV_Products`. That grouping is what the batch renderer iterates
over.

You can also click **Add Empty Product Collection** and drag existing meshes into it, which
is how you use MultiView with geometry that didn't come from STEP.

**Standardize Products** makes everything comparable across a batch:

- **Recenter**: moves each product's geometry to the world origin.
- **Rescale**: uniformly scales each product so its largest dimension equals **Target
  Size** (default `1.0 m`). This is what makes a tiny screw and a large housing frame
  identically in the final images.

### 3. Cameras / Views

Choose a view set and click **Build Cameras**. Cameras land in `MV_Cameras`, named
`MV_Cam_<View>`.

| View set | Cameras |
| --- | --- |
| **All** | Front, Back, Top, Bottom, Right, Left + 3 perspectives (9) |
| **Orthographic** | Front, Back, Top, Bottom, Right, Left (6) |
| **Standard** | Front, Top, Right, Perspective (4) |
| **Minimal** | Front, Top, Right (3) |

**Front Axis** tells the add-on which world axis points out of the front of your product.
CAD exports usually land on `+X` (the default); Blender's own convention is `-Y`. Getting
this right is what makes the "Front" render actually the front.

**Framing Padding** is the margin around the product in frame. `1.4` leaves 40% headroom.

Need an angle that isn't in the presets? Add a **Custom View** with an X/Y/Z direction
vector and an ortho/perspective toggle, and it gets built alongside the rest.

### 4. Batch Render

Set an **Output Directory**, choose a **Render Mode**, and hit **Render All**.

| Render mode | Output |
| --- | --- |
| **Full Render** | Uses your current engine (Cycles/EEVEE) and materials. |
| **Clay (Workbench)** | Fast single-colour clay pass with cavity shading. Default. |
| **Both** | Writes a `_render` and a `_clay` image per view. |

Every product is isolated in turn (all others excluded from the view layer), so nothing
bleeds between shots. Progress prints to the system console.

**Render engine, sample count, resolution, and file format stay in Blender's own Output and
Render Properties.** MultiView only drives cameras, visibility, and file paths. It never
overwrites your render settings, and the clay pass restores your engine when it finishes.

#### File naming

Output is organised per product and named deterministically:

```
renders/
  AA_01_Widget/
    AA_01_Widget_Front.png
    AA_01_Widget_Top.png
    AA_01_Widget_Persp_FTR.png
  AA_02_Bracket/
    AA_02_Bracket_Front.png
    ...
```

The `AA_01` label is controlled by three fields:

- **Start Prefix**: the letter pair for the first product (`AA`, `BB`, ...).
- **Start Number**: the number the batch starts at, so a second batch can continue from
  `AA_11` instead of restarting at `AA_01`.
- **Products per Letter**: how many products before the prefix rolls over to the next
  letter pair.

---

## Collections the add-on creates

| Collection | Contents |
| --- | --- |
| `MV_Products` | One child collection per product. This is what gets iterated and isolated. |
| `MV_Cameras` | Generated cameras, named `MV_Cam_<View>`. |
| `MV_Lights` | The lights from the active lighting preset. |

Rebuilding cameras or lighting clears and regenerates only that collection. Your products
are never touched.

---

## Troubleshooting

**"No cameras. Build cameras first."**: run *Build Cameras* in the Cameras / Views panel.

**"No product collections under 'MV_Products'."**: import a STEP file, or add an empty
product collection and put your meshes inside it. Meshes sitting loose in the scene
collection are not seen as products.

**STEP import does nothing**: check that the FreeCAD path in add-on preferences points at
`freecadcmd`, not the GUI `freecad` binary. Open `Window → Toggle System Console` (Windows)
to see the subprocess output.

**Products are all different sizes in the renders**: run *Standardize Products* with
**Rescale** enabled.

**The "Front" view isn't the front**: change **Front Axis** in Cameras / Views and rebuild
the cameras.

**Imported STEP geometry is blocky**: lower **Tessellation Deflection** in preferences
(try `0.02`) and re-import.

---

## License

MIT. See [LICENSE](LICENSE).
