# Test files

Scratch folder for STEP files used to exercise the add-on by hand. **Everything in here is
gitignored except this README**, so you can drop real CAD parts in without them ending up in
the repo or a release.

Drop `.step` / `.stp` files straight in, or group them in subfolders — subfolders are ignored
too.

## Using them

1. `3D Viewport → Sidebar (N) → MultiView → Import Products`
2. `+` on the file list, select the files from this folder
3. **Import STEP Files**

Each file becomes its own collection under `MV_Products`. With **Fit on Import** on (the
default) each one is scaled into a `Target Size` cube and centred on the world origin as it
lands.

Renders default to `//renders/`, which is gitignored separately — they will not appear here.

## Worth having on hand

- a **single-solid** part (simplest path)
- a **multi-part assembly** — this is what catches origin and scale bugs, since the parts
  have to keep their positions relative to each other
- something **large**, a few hundred mm across, to check unit handling
- a **broken or truncated** file, to confirm one bad file is skipped with FreeCAD's real
  error rather than stopping the batch

If an import misbehaves, `Window → Toggle System Console` shows the per-file report.
