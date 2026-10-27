# Basic IFC Import for IngeTrazo

**Version 0.2.0 — experimental. Targets IngeTrazo v0.5.9 (extension API 2).**

Import an IFC as a reference for modelling and structural review: one selectable group per element, with visibility layers by building storey. Runs locally, using free software. This is an independent community extension, not an official IngeTrazo feature.

## What it does

- **Scan categories** before importing: list represented IFC classes and subtypes with element counts, without generating geometry. Browsing to an IFC starts the scan automatically.
- Check exactly which categories to import. **Structural review** preselects walls (including subtypes), columns, beams, slabs, footings and piles. **Members and plates are unchecked** because these classes can also contain mullions and glazing panels.
- Subtypes distinguish, for example, `IfcMember / MULLION` and `IfcPlate / CURTAIN_PANEL`, using the element or its assigned IFC type. `NOTDEFINED` means the exporter supplied no useful subtype. Exporter-defined subtype text is displayed as provided.
- **Select all** enables all importable categories, including doors, windows and furniture. **Select none** clears the selection. Category selection is exact: class + subtype, not a guessed name or material filter.
- Spaces, opening objects, subtraction features and annotations are always excluded. **Openings are still cut into the walls/slabs by the IFC geometry engine.**
- Separate top-level group per element, so clicking a wall does not select the whole building.
- Storey visibility layers; stable IFC storey IDs distinguish storeys that have identical names. Elements without a storey use an Unassigned layer.
- Element names, IFC classes and IFC GlobalIds preserved. Full source path, STEP ID, storey and offset are stored with the group in its extension metadata.
- IFC geometry converted to metres, with object placements applied. Optional explicit XYZ offset in metres; no automatic centring.
- Surface colours and transparency. Coplanar triangulation seams are softened; sharp corners remain.
- Progress, cancellation, an adjustable triangle limit, and a JSON report of failures and counts.
- A completed import is **one Undo step**. Cancelled or failed conversion adds no geometry. Saving as `.igz` retains the groups and metadata.

This imports **triangulated reference geometry**. It does not reconstruct parametric BIM objects or convert walls into native push/pull solids. Tiny triangles below the host's weld resolution may be skipped and are counted in the report. Hiding planar triangle seams does not merge their faces.

## Updating from 0.1.x

Download and extract the updated repository ZIP, close IngeTrazo, run **`install_windows.bat`**, and restart. You do **not** need to rerun the helper setup. The dialog title should say **0.2.0**.

The previous **Structural only** checkbox is replaced by a category checklist. Browse to your IFC (or paste its path and click **Scan categories**), review the ticked rows, and click **Import**. Keep mullion/panel rows unchecked. Members and plates are off by default; explicitly check the relevant rows if you need structural bracing or steel plates.

The default triangle limit remains **2,000,000**, adjustable up to 5,000,000. A category scan reports element counts, not triangle estimates. It parses the IFC without tessellating its geometry. A changed file/path requires rescanning; a cancelled scan never adds geometry or enables import from an old list.

## Windows: first-time setup

No administrator rights are required for the helper or extension installation.

1. Install **64-bit Python 3.13** (3.12 or 3.11 are also suitable), including the **Python launcher / `py` command**. If Python is already installed, open Command Prompt and check:

   ```bat
   py -3 --version
   ```

2. On this repository page, click **Code → Download ZIP**. Right-click the downloaded ZIP → **Extract All**. Work from the extracted folder, not inside the ZIP.
3. Close IngeTrazo.
4. Double-click **`setup_windows.bat`**. This creates a separate helper environment and installs IfcOpenShell. **This setup step needs internet access.** Wait for “Helper ready”. If it fails, keep the error text; do not continue as if it succeeded.
5. Double-click **`install_windows.bat`**. It copies only the extension files to your user plugins folder.
6. Start IngeTrazo **v0.5.9**. Choose **Extensions → Import IFC (basic)…**.

The helper's default path is filled in automatically:

```text
%LOCALAPPDATA%\IngeTrazo-IFC-Import\venv\Scripts\python.exe
```

The extension is installed in:

```text
%APPDATA%\ingetrazo\plugins\ifc_basic_import.py
%APPDATA%\ingetrazo\plugins\ifc_import_support\
```

**After setup, importing is offline.** It does not upload the IFC or contact an external service. The helper Python is separate because installing packages into Python on your PC does not install them into IngeTrazo's bundled Python.

If several Python versions are installed and `py -3` chooses an unsupported version, create the helper explicitly with:

```bat
py -3.13 -m venv "%LOCALAPPDATA%\IngeTrazo-IFC-Import\venv"
"%LOCALAPPDATA%\IngeTrazo-IFC-Import\venv\Scripts\python.exe" -m pip install -r ifc_import_support\requirements.txt
```

Run these from the extracted repository folder, then run `install_windows.bat`.

## First test — use the included small model

1. Start a new empty IngeTrazo document. Exit any open group before importing.
2. Choose **Extensions → Import IFC (basic)…**.
3. Browse to **`examples/structural-demo.ifc`** in the extracted folder.
4. The category scan runs automatically after browsing. Keep **Structural review** selected (wall and column checked), offsets at **0**, and the triangle limit at **2,000,000**. You can also click **Scan categories** to refresh the list.
5. Click **Import**. Expect **2 groups**: a rotated wall with a door opening, and a column. The IFC uses millimetres; the imported wall is **4 m long, 0.30 m thick and 3 m high**. Its opening is **1 m wide and 2 m high**. The wall occupies X = 9.7–10 m, Y = 20–24 m and Z = 3–6 m; the column is near the origin.
6. Close the dialog and use IngeTrazo's **Zoom Extents** command to find the model. Click each element separately.
7. In the Layers panel, toggle the two IFC storey layers. They deliberately have the same Hebrew name but different IDs.
8. Check the wall opening, measure the wall, and try a section plane using IngeTrazo's normal tools. Section-plane rendering is a manual check, not an automated test in this repository.
9. Undo: both imported groups and their newly created layers disappear. Redo restores them.
10. Save as `.igz`, close and reopen it. Check the groups and layers again.

With **Select all**, the sample imports **3 groups**, adding a furniture box. Spaces and opening objects appear disabled in the checklist and are never imported separately.

## Importing your own IFC

Start with a small structural IFC before trying a large architectural export. The importer appends to the current document; importing the same file again creates additional groups rather than updating the previous import.

- **Structural review** is recommended for review of walls, columns and slabs. Adjust the checkboxes if you need fewer classes. Some exporters classify building parts as `IfcBuildingElementProxy`; check that row explicitly if needed. The importer preserves the exporter's classification. It cannot reliably distinguish glazing from structural parts if the exporter puts them in the same class and subtype; deselect the whole row or use a cleaner source export in that case.
- **Maximum triangles** defaults to 2,000,000, an initial guard rather than a performance guarantee. Exceeding it stops conversion without adding a partial model. A single complex element can take time/memory to tessellate before its triangle count is known. Increase the limit cautiously, or export a smaller model/category set.
- **Offsets:** keep all three at zero unless model coordinates are far from the origin. For survey coordinates, enter a chosen common XYZ origin in metres; the importer subtracts it. Use exactly the same offset for related architecture/structure IFCs. The report and group metadata record it. IFC projected-CRS/map-conversion transforms are not applied; this is not a georeferencing tool.
- **Cancellation:** click Cancel import. The helper process stops, or staged groups are discarded. The document changes only once all staging is complete. The final commit/viewport refresh and host save operations can still briefly block the UI.
- **Report:** before closing the import dialog, click **Save report…**. It includes schema, source file, helper version, offset, selected categories, per-category candidate/import/failure counts, imported groups, triangle counts, skipped triangles, per-element errors and elapsed time. It contains the local source path; review that before sharing the report publicly.
- A successful import may still be **incomplete** if individual elements failed. Check the reported failures and compare the model against a trusted IFC viewer.

## Troubleshooting

| Symptom | What to check |
|---|---|
| No import menu | Restart IngeTrazo; confirm v0.5.9 and both installed plugin paths above. Look for a plugin load error in Extensions. |
| Helper executable missing | Run `setup_windows.bat`, then browse to the helper's `python.exe`. Do not select IngeTrazo.exe or pythonw.exe. |
| `No module named ifcopenshell` | Dependencies were installed into a different Python. Re-run setup or install using the exact helper executable selected in the dialog. |
| Triangle limit exceeded | Uncheck unwanted categories, export fewer storeys, or deliberately increase the limit. |
| Zero elements imported | Save the report. Check IFC classes, failed-element errors, and whether the file contains 3D representations. |
| Import succeeded but nothing visible | Use Zoom Extents; check layer visibility and coordinates. Existing storey layers retain their current visibility/lock state. |
| Distorted geometry far from origin | Use a common explicit offset. IngeTrazo uses float32 positions and a roughly 0.1 mm weld grid. |
| Large model slow | Use a smaller IFC. This version builds ordinary editable host meshes and does not stream lightweight GPU-only reference geometry. |

## Limitations and test status

Tested locally on Linux with **Python 3.12, IfcOpenShell 0.9.0.post1, PySide6 6.12.0**, against the real **IngeTrazo v0.5.9 source**, commit `24e7ada6df9f0b4f5f761f287aaade9e40e6728c`.

Automated tests cover a real IFC4 millimetre-unit model with rotated placement, a Boolean opening, Unicode paths/names, storey separation, legacy physical/structural filters, category scans without tessellation, subtype selection from instances and assigned types, facade exclusion, checkbox presets and stale-scan invalidation, offsets, triangle-limit rollback, failed geometry reporting, plugin discovery, the Qt helper-to-import pipeline, cancellation, undo/redo and `.igz` save/reopen.

**The Windows packaged application, large real project IFCs, and visual section output have not yet been verified locally.** The Windows scripts and frozen-app environment handling are provided for the first user trial. CI runs the tests on Windows and Linux; consult the Actions tab for its actual results. IFC2x3 is accepted by IfcOpenShell but is not covered by the included fixture yet.

No property-set browser, automatic reimport/update, model federation management, textures, native BIM editing, IFC round-trip guarantee, geometry repair, or automatic CRS alignment. Host plugin APIs may change after v0.5.9.

The helper processes elements individually so a failed element can be reported precisely. This is simpler but potentially slower than IfcOpenShell's cached multicore iterator. The UI uses a separate process for tessellation and builds detached groups in batches on the GUI thread.

## Linux / source installation

From the extracted repository:

```sh
python3 -m venv "$HOME/.local/share/ingetrazo-ifc-import/venv"
"$HOME/.local/share/ingetrazo-ifc-import/venv/bin/python" -m pip install -r ifc_import_support/requirements.txt
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/ingetrazo/plugins"
cp ifc_basic_import.py "${XDG_DATA_HOME:-$HOME/.local/share}/ingetrazo/plugins/"
cp -R ifc_import_support "${XDG_DATA_HOME:-$HOME/.local/share}/ingetrazo/plugins/"
```

Restart IngeTrazo. Flatpak confinement may block access to external Python; running IngeTrazo from source or an unconfined installation is recommended for that test. macOS is not tested.

## Development and tests

```sh
python -m pip install -r requirements-dev.txt
git clone https://github.com/ingelibre/ingetrazo.git ../ingetrazo-v059
git -C ../ingetrazo-v059 checkout 24e7ada6df9f0b4f5f761f287aaade9e40e6728c
INGETRAZO_SOURCE="$(cd ../ingetrazo-v059 && pwd)" QT_QPA_PLATFORM=offscreen python -m pytest tests -q
```

Tests require the upstream source; it is not vendored into this repository. On PowerShell set `$env:INGETRAZO_SOURCE` to its full path before running `python -m pytest tests -q`.

The helper can also be run from a terminal, independently of IngeTrazo. To scan:

```sh
python ifc_import_support/worker.py --input examples/structural-demo.ifc --output categories.json --scan
```

To import exactly selected categories, save a JSON list such as:

```json
[{"class": "IfcWall", "predefined_type": "NOTDEFINED"}, {"class": "IfcColumn", "predefined_type": "NOTDEFINED"}]
```

Use the exact class/subtype values from the scan. Pass that file with `--selection selection.json`. Empty selections are rejected; they never mean “import all.” The older command-line filters remain available for scripts:

```sh
python ifc_import_support/worker.py --input examples/structural-demo.ifc --output geometry.jsonl --mode structural --max-triangles 2000000
```

It emits progress as JSON lines on stdout, writes completed geometry to the requested JSONL file, and exits nonzero on fatal errors. Element-level failures are in the final summary. Generated geometry/report files are not needed after import.

## Uninstall / update

Close IngeTrazo. Remove `ifc_basic_import.py` and `ifc_import_support` from the user plugins directory to uninstall. To remove the separate helper too, delete `%LOCALAPPDATA%\IngeTrazo-IFC-Import\venv` on Windows. This does not delete saved models. To update, download and extract the new repository files, close IngeTrazo, and rerun both batch files.

## License and acknowledgements

GPL-3.0-or-later; see LICENSE. Uses IngeTrazo's public Python APIs and IfcOpenShell. IngeTrazo is by Marco Sumari Tellez and its contributors; IfcOpenShell has its own license and is installed separately by pip. No upstream application source or third-party binaries are bundled here.
