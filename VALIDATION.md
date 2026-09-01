# BSMT Phase 1 — Manual Validation Procedure

Nothing in this file has been executed by the assistant: Blender is not
installed on this machine, so every step below must be run by you inside
Blender. The only part that *was* verified offline is the pure distance /
unit maths in `measurement.py` (see "What was already verified").

---

## 0. Install

1. Zip the package so the folder `body_surface_measurement` is at the top level
   of the archive:

   ```
   cd /Users/yeoni/BSMT
   zip -r body_surface_measurement.zip body_surface_measurement -x "*__pycache__*"
   ```

2. Blender → `Edit > Preferences > Add-ons > Install...` (Blender 4.2+:
   the drop-down arrow at the top right → `Install from Disk...`).
3. Select `body_surface_measurement.zip`.
4. Tick the checkbox next to **Body Surface Measurement Tool (BSMT)**.

Alternative (faster while developing): symlink instead of installing, so edits
take effect after `Blender > System > Reload Scripts` (F3 → "Reload Scripts"):

```
ln -s /Users/yeoni/BSMT/body_surface_measurement \
  "$HOME/Library/Application Support/Blender/<VERSION>/scripts/addons/body_surface_measurement"
```

5. In the 3D Viewport press **N** → a tab named **BSMT** appears → panel
   **Body Measurement**.

If the tab does not appear, open `Window > Toggle System Console` (Windows) or
launch Blender from a terminal (macOS) and check for a registration traceback.

---

## 1. Reference test — a cube with two points exactly 100 mm apart

This is the primary correctness test. It uses a known-size cube so the expected
answer is exact.

### 1a. Build the test cube (unit = mm)

1. `File > New > General`. Delete nothing that matters — the default cube is fine
   to remove with X if present.
2. `Add > Mesh > Cube`. In the "Add Cube" redo panel (bottom left), set
   **Size = 100**.
   The cube now spans from -50 to +50 on every axis, i.e. **each edge is exactly
   100 units long**.
3. In the BSMT panel set **Coordinate Unit = Millimeters (mm)**.
   One unit = 1 mm, so one cube edge = 100 mm.

### 1b. Measure one edge

1. Click **Pick Point A**. The header/status bar reads
   `BSMT: Left click on the mesh to set Point A | ESC or Right click: cancel`.
2. Left click **exactly on one corner** of the cube. A red sphere appears.
   Point A shows `Selected` plus its XYZ.
3. Click **Pick Point B**, left click on the corner at the other end of the same
   edge. A blue sphere appears.
4. Click **Calculate Distance**.

**Expected:** `Straight Distance: 100.00 mm` (± your clicking accuracy — you are
clicking on the surface, not snapping to the vertex, so a few tenths of a mm of
error at the corners is normal and expected).

### 1c. Remove the clicking error — the exact check

Clicking by hand cannot land exactly on a corner. To verify the arithmetic to
full precision, compare against the XYZ values the panel prints:

1. Read the two coordinates shown under `Point A:` and `Point B:`.
2. Compute by hand / calculator:
   `sqrt((Ax-Bx)^2 + (Ay-By)^2 + (Az-Bz)^2) * multiplier`
   with multiplier = 1 (mm), 10 (cm), 1000 (m).
3. It must equal the displayed `Straight Distance` to 2 decimals.

A cleaner exact variant: pick both points anywhere on **two opposite faces** of
the 100-size cube — e.g. click the +X face, then orbit and click the -X face at
visually the same spot. The X difference is exactly 100; any deviation you see
is your Y/Z aim, which you can confirm from the printed XYZ.

### 1d. Unit conversion check

Without re-picking anything, change **Coordinate Unit**:

| Unit setting | Expected result for the same 100-unit edge |
|---|---|
| mm | `100.00 mm` |
| cm | `1000.00 mm` |
| m  | `100000.00 mm` |

The displayed value must update immediately when the enum changes (the result is
recomputed from the stored coordinates; the points themselves never move).

---

## 2. Translated mesh

1. Select the cube, press `G X 250 Enter` (move it 250 units along X).
   *Do not apply the transform — the add-on must not need it.*
2. **Clear Points**, then pick A and B on the same edge again.
3. **Calculate Distance**.

**Expected:** still `100.00 mm`. Translation must not change the result. The
markers must appear on the cube in its new position, not at the old one.

---

## 3. Rotated mesh

1. With the cube still translated, press `R X 37 Enter`, then `R Z 22 Enter`
   (arbitrary non-axis-aligned rotation). Again, do not apply the transform.
2. **Clear Points**, pick A and B on the same edge, **Calculate Distance**.

**Expected:** still `100.00 mm`. This confirms the hit location is world-space
and the object's rotation matrix is already accounted for.

Also try a **non-uniform scaled** object (`S X 2`) as a sanity check: the edge is
now physically 200 units long and the tool should report `200.00 mm`, because it
measures the world-space surface, not the pre-transform mesh data.

---

## 4. Clearing and re-picking

1. With both points set and a line drawn, press **Clear Points**.
   - `BSMT_Point_A`, `BSMT_Point_B`, `BSMT_Straight_Line` disappear from the
     Outliner, and the `BSMT_Helpers` collection is removed once empty.
   - Point A / Point B both read `Not Selected`, result reads `--`.
   - **The cube is still there.** Confirm in the Outliner.
2. Pick A, pick B, calculate — everything works again from a clean state.
3. Re-pick only Point A after a calculation: the old red marker moves to the new
   spot, the yellow line is removed, and the result reverts to `--` until you
   press Calculate Distance again. (This is intentional — a stale line and a
   stale number are worse than none.)

---

## 5. Cancel / miss behaviour

1. Click **Pick Point A**, then press **ESC** (or right click).
   → status text clears, no marker is created, any previously stored Point A is
   left untouched.
2. Click **Pick Point A**, then left click on **empty space** (the background).
   → warning in the status bar: `BSMT: no mesh surface under the cursor...`
   The operator stays active so you can click again. ESC to leave.
3. While picking, orbit with the middle mouse button and zoom with the wheel —
   navigation still works, and no point is committed until a left click hits
   geometry.
4. Click **Pick Point B** and then left click on the existing red Point A marker.
   → the ray passes *through* the marker and lands on the mesh behind it. BSMT
   markers are never valid pick targets.

---

## 5b. Display controls (Phase 1 usability update)

1. **Marker Size (mm)** — default 5 mm diameter, range 1-30 mm. With both points
   picked, drag the value: the spheres resize live. At unit = mm a 5 mm marker
   has a radius of 2.5 coordinate units; at unit = m it has a radius of 0.0025.
   Check the size stays *physically* constant by switching Coordinate Unit -
   the marker must not visibly change size relative to the body, because the
   mm figure is what is fixed.
2. **Line Thickness (mm)** — default 2 mm, range 0.2-20 mm. The line is a
   bevelled curve drawn in front of the scan, so it stays visible over dark or
   busy textures.
3. **Show Markers / Show Straight Line** — untick either box:
   - the helper disappears from the viewport,
   - `Point A: Selected` / `Point B: Selected` and the XYZ values **do not
     change**,
   - `Straight Distance` **does not change**,
   - re-ticking brings the helper back at the same place, with no re-picking.
   Confirm in the Outliner that the objects still exist (they are hidden, not
   deleted).
4. Hidden markers are also skipped by ray casting, so you can hide Point A and
   still pick Point B on the surface underneath it.

---

## 6. Safety checks

- Select the cube in the Outliner and press **Clear Points** — the cube must not
  be deleted. Deletion is gated on a custom property (`bsmt_helper`) that only
  objects created by this add-on carry; a name match alone is not enough.
- The cube's mesh data, vertex count, and transform must be unchanged after any
  BSMT operation. Check `Object Properties > Transform` and the statistics
  overlay before/after.
- No file is written by the add-on.
- With **Coordinate Unit** unchanged, no unit scaling other than the one you
  selected is applied. Blender's own Scene Unit settings are deliberately
  ignored — BSMT interprets raw coordinate units only.

---

## What was already verified (offline, without Blender)

- All 7 Python files compile (`python3 -m py_compile`).
- `measurement.py` was executed against a stub `mathutils.Vector` and passed:
  mm/cm/m multipliers, 100-unit → 100 mm for each unit setting, the
  100-unit cube diagonal (173.205...), translation invariance, argument-order
  invariance, `"100.00 mm"` formatting, and a `KeyError` for an unknown unit
  (no silent fallback multiplier).

## What could NOT be verified here

Everything that requires a running Blender: add-on registration, the panel
drawing, the modal operator's event handling, `scene.ray_cast` behaviour,
marker/line creation, and the actual on-screen picking. Those are exactly what
sections 1–6 above are for.

## Known Phase 1 limitations (by design)

- Points are stored as **world-space** coordinates at pick time. If you move or
  rotate the scan *after* picking, the stored points and markers do **not**
  follow it; re-pick them. Section 2/3 above tests the supported order
  (transform first, then pick).
- Quad view (`Ctrl+Alt+Q`) is not specifically handled; use a single viewport.
- Undo of a pick is not pushed onto the undo stack; use **Clear Points**.
