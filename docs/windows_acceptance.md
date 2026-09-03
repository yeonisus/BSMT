# Windows acceptance checklist

**Status: READY FOR MANUAL WINDOWS VALIDATION. Nothing below has been run on
Windows.**

BSMT 0.22.0 is packaged for Windows x64 and audited for platform assumptions
from macOS. That is not the same as working. This checklist is what a real
Windows machine has to confirm before anyone should call Windows *supported*.

## What is already established, and how

| Claim | How it was established | Confidence |
|---|---|---|
| The Windows wheel is the right ABI | `pygeodesic-0.1.11-cp311-cp311-win_amd64.whl` contains `geodesic.cp311-win_amd64.pyd`; Blender 4.5.13 runs CPython 3.11 (`SOABI cpython-311`) | High — mechanical |
| Blender installs the right wheel per platform | Installed the extension on macOS; Blender placed the darwin `.so` under `extensions/.local/lib/python3.11/site-packages/` | High on macOS, inferred for Windows |
| Blender does **not** pull NumPy 2 | The wheel declares `numpy>=2`; after a real install NumPy was still Blender's 1.26.4 | High — measured |
| The source has no Windows-hostile assumption | `tests/test_portability.py`: no Unix-only module imported at load, no shell, no hard-coded paths, no hand-rolled path splitting, every `open()` declares an encoding | High — static, exhaustive |
| The GPU overlay avoids removed/backend-specific APIs | No `TRI_FAN`, no `LINE_LOOP`, only the builtin `UNIFORM_COLOR` shader, no hand-written shader source | Medium — no Windows GPU has drawn it |
| Korean text survives CSV and JSON | Round-tripped through Korean directory and file names, UTF-8 verified at byte level | High on macOS; the Windows risk is the locale encoding, which is why every open is explicit |

**Not established:** that any of it runs on Windows.

---

## The checklist

Record the result of each line as **PASS**, **FAIL** or **N/A**, with the
Blender version and machine.

### Install

- [ ] Clean Blender 4.5.13 x64 installed from blender.org
- [ ] `bsmt-0.22.0.zip` downloaded **without the browser unzipping it**
- [ ] Preferences → Get Extensions → ▾ → Install from Disk succeeds
- [ ] BSMT appears and is enabled
- [ ] No error in **Window → Toggle System Console**
- [ ] Sidebar (**N**) shows a **BSMT** tab

### Dependency

- [ ] *Results and Export → About BSMT* reports `Windows x64`
- [ ] It reports `Python 3.11.x` and `NumPy 1.26.4`
- [ ] It reports **`Exact Geodesic: Available (pygeodesic 0.1.11)`**
- [ ] NumPy is still Blender's own — no second NumPy was installed
- [ ] *Geodesic Backend (Developer) → Run Backend Self-Test* passes

### Scan import

- [ ] Textured **OBJ** with its MTL and JPG/PNG in the same folder imports with
      the texture visible
- [ ] The same from a path with spaces:
      `C:\Users\Researcher\Documents\BSMT Data\scan.obj`
- [ ] The same from a **Korean** path, e.g. `C:\Users\연구원\문서\스캔\몸.obj`
- [ ] **PLY** with vertex colors imports and displays

### Workflow

- [ ] **Scan Preprocessing** → *Create Measurement Mesh* produces `<name>_BSMT`
- [ ] Texture, UV map and material survive on the copy
- [ ] **Mesh Repair** → *Analyze Mesh* reports topology
- [ ] *Repair Local Defects* runs and reduces non-manifold edges
- [ ] *Undo Repair* restores the mesh
- [ ] **Alignment** → manual ±90° rotations work
- [ ] Four reference points → *Apply Alignment*; landmarks stay VALID
- [ ] *Reset Alignment* restores the transform

### Landmarks and overlay

- [ ] *Add Landmark*, then *Pick Landmark*, positions on the surface
- [ ] A landmark with a **Korean name** displays correctly in the list
- [ ] Markers are drawn, all the same size
- [ ] Labels are drawn beside their markers
- [ ] Zooming in and out does **not** change the marker pixel size
- [ ] The selected landmark shows a ring, not a bigger dot
- [ ] Marker/label colour, size and offset controls all take effect
- [ ] *Visible Surface Only* hides landmarks behind the body; orbiting updates
- [ ] A stale landmark is drawn in its status colour and labelled `[STALE]`
- [ ] 50+ landmarks: viewport navigation stays responsive

### Measurement

- [ ] *Add Measurement*, choose From and To, *Calculate Selected*
- [ ] **Straight Distance** is produced, in millimetres
- [ ] **Surface Distance** is produced and exceeds the straight distance
- [ ] *Compute Surface Path* draws the geodesic polyline
- [ ] *Calculate All* runs a batch and reports its summary
- [ ] Pressing *Add Measurement* repeatedly leaves exactly one unfinished row

### Export and protocol

- [ ] Session Info accepts a **Korean** Subject ID
- [ ] *Measurements CSV* writes to a path with spaces
- [ ] *Landmarks CSV* writes to a **Korean** folder
- [ ] Both open in **Excel** with Korean text intact — the BOM check
- [ ] Uncalculated values are **blank**, not `0`
- [ ] STALE/FAILED rows carry their status and no numbers
- [ ] *Save Protocol* writes JSON; open it in Notepad and confirm Korean names
- [ ] *Load Protocol* into a fresh scene restores definitions, unpositioned

### Failure behaviour

- [ ] Uninstall the extension, install `body_surface_measurement-0.22.0.zip`
      (no solver) instead
- [ ] BSMT still enables
- [ ] *About BSMT* reports `Exact Geodesic: Unavailable` with a reason
- [ ] Straight Distance still works
- [ ] Surface Distance reports a clear dependency error and **does not crash**

---

## Reporting back

For each FAIL, record: the checklist line, the *About BSMT* block verbatim, the
System Console text, and what you were doing. The *About* block is what
distinguishes a Windows problem from a BSMT problem.

## Then update

When this checklist passes, change the platform table in `README.md` from
*Packaged, not yet verified on hardware* to **Validated**, naming the Windows
build and the date. Until then, that row must not say Windows is supported.
