# Changelog

Version numbers are `major.minor.patch`. Every entry lists what changed and,
where a defect was fixed, what it actually was. The full design record is in
`PROJECT_SPEC.md`.

## 0.26.0 — Milestone 3.25, the tool states what it has actually been tested for

Two things arrive together, because neither is worth much alone: an export
format a second reader can trust, and a written record of what has and has not
been verified about the numbers in it.

### CSV export, schema v2

- **Every row now carries `schema_version`, as its first column.** A reader
  that has to guess which columns a file has is a reader that will one day
  guess wrong; a script can now refuse a layout it does not know instead of
  silently reading the wrong field. This is version **2**; 0.18.0's layout was
  version 1.
- **Stable ids are exported alongside protocol ids.** `measurement_id` is the
  researcher's protocol code and is *optional* — a scene where nobody filled
  it in used to export rows that could not be told apart. `measurement_stable_id`,
  `from_landmark_stable_id`, `to_landmark_stable_id` and `landmark_stable_id`
  are BSMT's own monotonic ids, never reused within a scene and never blank.
  Both are exported, so a row can always be identified and a protocol code can
  still be the join key when there is one.
- **The landmark stable ids on a measurement row come from the definition**,
  not from the resolved landmark, so they stay meaningful after the landmark
  they name has been deleted.
- **Both files name the protocols that produced them** (`landmark_protocol`,
  `measurement_protocol`). An exported measurement is only reproducible if the
  reader knows *which* protocol produced it — a landmark called "Acromion"
  means one thing under one protocol and something slightly different under
  another. These are recorded when a protocol or template is loaded, so
  nothing is invented.
- Layout is now 33 measurement columns and 27 landmark columns. Nothing that
  existed in version 1 was renamed or removed.

### docs/VALIDATION.md — a validation record, with its limits stated

New document separating **software verification** (does the program do what it
is specified to do) from **numerical validation** (how does the computed number
relate to a mathematically known answer), and marking every section with
whether it was executed, authored but not run, not implemented, or needs human
data. Executed evidence, all on Blender 4.5.13 / macOS ARM64:

- **Analytic geometry** (`tests/test_analytic_validation.py`, 50 checks). Plane,
  cylinder and sphere against closed-form answers. On the tested planar
  fixtures the surface distance matched the analytic diagonal to zero absolute
  error. On the cylinder and sphere the gap to the smooth surface shrank at
  every refinement, at a rate consistent with second order over the tested
  range. Recorded as what it is: the solver is exact **on the polyhedral mesh
  it is given**, and the remaining gap is the mesh's discretisation, not solver
  error.
- **Rigid-transform invariance** (`tests/test_invariance_blender.py`, 63
  checks). Surface distances bit-identical under pure translation and pure
  rotation; worst deviation anywhere 1.5e-05 mm with the scan 28 m from the
  origin. Geometry hash unchanged across every pose, and the solver called
  **zero** times after a transform.
- **Decimation sensitivity** (`tests/test_decimation_sensitivity.py`, 28
  checks). Four paths at 608k / 500k / 350k / 200k triangles; worst deviation
  0.0081 mm (0.0023%). Recorded explicitly as **characterisation, not
  validation** — the fixture is a torus, not a body, and most of the measured
  differences sit below the noise floor of re-attaching a landmark. It does
  **not** establish 350k as a validated default.
- **Failure and stale-state policy** (`tests/test_stale_state_blender.py`, 68
  checks — new). Geometry edit, scale change, unit change, landmark re-pick,
  density guard, cross-component pair and blocking topology, each asserted to
  reach the state the policy specifies, to drop rather than reuse the old
  number, and — where the operation is meant to be refused before solving — to
  construct pygeodesic **zero** times.
- **Repair locality** (`tests/test_repair_locality_blender.py`, 40 checks —
  new). A repair confined to one cap of a sphere: the source scan stayed
  bit-identical, no vertex outside the repaired region moved at all, and a
  measurement 119 mm away returned a bit-identical distance afterwards.
  Separates the **global** data-state invalidation policy (every landmark on a
  repaired mesh goes non-VALID; nothing is re-projected) from the **local**
  geometric reality.

**Not validated, and said so in the document:** real-scan repeatability, intra-
and inter-rater landmark reproducibility, comparison against reference
software or manual anthropometry, and the scientific justification of any
default decimation target. None of these can be settled by code, and BSMT does
not claim them.

### Also

- The workflow-order test's prose said "seven stages" and "no eighth panel"
  while asserting against the correct eight-stage tuple. Wording only; the
  assertion and the panel order were already right.

## 0.25.2 — the selection ring belongs to Landmark Manager

**Reported:** the selected landmark keeps its emphasis ring after the
researcher has moved on to Measurement Manager, Measurement Visualization or
Results and Export.

The ring says *"this is the row your next pick belongs to"*. That is worth
saying while landmarks are being picked and worth nothing afterwards — in a
later stage it marks one landmark out from its neighbours for a reason nobody
looking at the viewport could reconstruct.

- **The emphasis now follows the workflow stage.** `props.ui_stage` records
  which stage is being worked in, `readiness.landmark_emphasis_visible()`
  answers the one question that reads it, and the overlay draws the ring only
  in `LANDMARKS`.
- **Selection and emphasis are now two different things** in
  `overlay.entries`. `selected` is still which row the list is on and still
  decides what SELECTED label scope means — suppressing it would have hidden a
  label, which is a different change. `emphasised` is only the ring and the
  selected label's size bonus.
- **Nothing is rebuilt and nothing is hidden.** Every disc is still built at
  its configured radius and colour; the ring batch simply comes back empty.
  The test asserts the disc batches are identical either way.
- **No data changes.** `landmark_index` is untouched, and the test asserts
  `enter_stage` writes exactly one property and calls nothing from the
  measurement or geodesic modules. No geodesic recomputation, no measurement
  invalidation, no SurfacePoint change.
- **The stage is recorded by a table applied once at registration** rather
  than by a line added to thirty operator bodies, which keeps a viewport
  decoration out of the operators that do the work and makes the rule readable
  in one place. An operator the table does not name leaves the stage alone, so
  omitting one is inert rather than wrong. Selecting a row in either list is a
  property change rather than an operator, so the two index callbacks say it
  too.

One consequence worth knowing: Blender fires a property update only when the
value actually *changes*, so clicking the landmark row that is already active
does not by itself bring the ring back. Every other Landmark Manager control —
marker size, colour, label settings, visibility mode, a different row, or any
landmark operator — does.

**Verified:** `tests/test_overlay.py` at 147 offline checks, including that
the emphasis can be switched off with byte-identical disc batches, unchanged
radii, colours, positions and labels. `tests/test_workflow_ui.py` at 126
checks in Blender: select a landmark → ring visible; interact with Measurement
Manager → ring gone, landmark still drawn at its configured size, selection
data untouched; calculating, visualisation and export likewise; return to
Landmark Manager → ring back for the still-selected landmark.

## 0.25.1 — a tolerance with no dimensions refused a correct alignment

**Reported:** on the real repaired PLY, with **good** references — about 0.4°
off perpendicular, which BSMT itself calls *"Good — the references are close
to perpendicular"* — Apply Alignment moved the object and then reported
*"'scan (1)_BSMT' moved, but the result FAILED validation - "*, with nothing
after the dash.

**Which criterion failed, measured before changing anything.** Reproduced by
rebuilding the real geometry: a millimetre-scale body at
`(-28570, -2692, -176)` whose **mesh data was never recentred**, so the
vertices carry the matching offset — an ordinary scanner export. Exactly one
criterion failed, and every angular one passed by orders of magnitude:

| criterion | measured | limit | |
|---|---|---|---|
| SI · +Z / LR · +X / posterior · +Y | 0.000000° off | 0.05° | pass |
| max \|BᵀB − I\| | 2.5e-19 | 1e-6 | pass |
| LR · +X vs cos(residual) | 1.1e-12 | 1e-6 | pass |
| **INFERIOR distance from origin** | **1.008e-03** | **1e-4** | **FAIL** |

**Root cause: the transform was right; the validator's one world-unit
tolerance had no dimensions.** `_ORIGIN_TOLERANCE` was a fixed `1e-4` world
units. Blender stores an object pose as single-precision loc/rot/scale, so a
point reconstructed through `matrix_world` carries about 1e-7 of the
*magnitude of the coordinates involved* — here `R @ local + t` with both terms
near 30,000, giving ~1e-3 mm, one micrometre. A fixed 1e-4 is a tenth of a
millimetre on a scan in metres and a tenth of a *micrometre* on this one, ten
times finer than the storage can hold. Even the cases that passed in 0.25.0
were using 31% of that budget.

- **`alignment.position_tolerance(reach)`** replaces it: `1e-6` of the
  coordinate reach — the largest magnitude that actually went through the
  object transform, supplied by `attach.live_reference_points_and_reach()`.
  On the real scan that is 2.9e-2 mm against a measured 1.0e-3, a 29× margin;
  a translation that is genuinely wrong misses by millimetres or metres. The
  angular criteria are unchanged and still 0.05°: an angle has no scale.
- **The message could not say what failed** because the origin check lived in
  the operator, outside `validation["failures"]`, so the list it printed was
  empty. Every criterion — including the origin — is now judged inside
  `validate_world_frame`, which returns a `criteria` list of individual
  PASS/FAIL judgements, and `ok` is just "all of them passed". There is one
  verdict, reached in one place.
- **A persistent Alignment Validation panel section** shows the whole table:
  raw angle, residual, LR · +X with its expected value and angular error,
  SI · +Z with its angular error, the posterior axis, anterior · +Z,
  max \|BᵀB − I\|, det(basis), the INFERIOR distance with its limit and the
  reach it was derived from, then PASS/FAIL per criterion. Re-measured every
  draw. A toast is not a report.
- **Apply Alignment is now transactional.** 0.25.0 left the scan transformed
  under a FAILED banner — neither the original pose nor a valid one. A refusal
  now restores `matrix_basis` (bit-exact, unlike round-tripping `matrix_world`
  through single precision, and the only correct thing to restore on a
  parented or constrained object), puts every status property back as it found
  it, keeps the reference points untouched, and returns `CANCELLED`. The
  criterion table measured on the trial pose is kept in
  `align_refusal_report`, because that pose no longer exists and cannot be
  re-derived.
- **`det(basis)` is checked and reported**, and the status names the failing
  criteria by key rather than quoting one number.

**Verified:** `tests/test_alignment_blender.py` grows to 221 checks. New: the
real-scan fixture (mm, un-recentred, 0.4° residual, scale 1) must be
*accepted*; a scale sweep in metres/centimetres/millimetres; and a
transactional-failure case forced honestly with a Copy Rotation constraint —
Blender declines the requested pose, validation catches it, and the test
asserts `matrix_world` is bit-identical to the pre-Apply matrix, the status is
not ALIGNED, the references survive, the refusal names the criterion, and the
same references then align cleanly once the obstruction is removed.
`tests/test_alignment.py` at 123 offline checks.

## 0.25.0 — alignment proves its own result

**Reported:** BSMT said *Status: Aligned (Landmark)* while the viewport, still
in Top Orthographic, plainly showed the subject from the **front**. The panel's
own convention says `+Z = superior`, and a Top view looks along +Z, so if the
status were true the view would show the crown of the head. An earlier session
had also seen a residual of about 14.53°.

**The rotation was correct.** Measured in Blender 4.5.13 against the real
operator, with the four references reconstructed in world space and the axes
measured rather than read off Euler angles: `SI · +Z = +1.000000` after Apply
in every case — identity, 90° about X, arbitrary XYZ, large translation plus
rotation, an object with a delta rotation, and a parented object. There was no
matrix bug to fix.

**Root cause: the status was a latch, not a measurement.** `align_applied` was
set by the fact that an operator had run, and the panel read it as a statement
about the object's *pose*. Nothing ever re-checked the second claim. Align
correctly, then rotate the scan 90° about X by hand: `SI · +Z` becomes
`-0.000000` — superior now runs along −Y, which is exactly what a Top view
renders as a frontal silhouette — and the panel still said *Aligned
(Landmark)*. Undo, a parent moving, or any other add-on reach the same state.

A quieter half of the same defect: only one of the three axes was ever exact.
`LR · +X` is `cos(residual)` by construction — at 14.53° that is 0.968, the
subject's left sitting 14.5° off +X. The residual was shown; this consequence
of it never was.

- **`alignment.validate_world_frame()`** measures the contract from the four
  world reference points: per-axis error against +X/+Y/+Z, `max |BᵀB − I|`, raw
  `LR · +X` and `SI · +Z`, the raw angle between the picked directions, and the
  anterior-posterior axis dotted with world up — 0 when +Z really is superior,
  ±1 in exactly the reported situation. `LR · +X` is checked to **equal**
  `cos(residual)`, never required to be 1: that would refuse every real pair of
  landmarks.
- **The references are reconstructed from the live `matrix_world`**, never from
  the cached `world_xyz`. A check that reads the cache can only prove the cache
  self-consistent. The rotation is now built from the same live path.
- **Apply Alignment validates before it claims anything**, after a
  `view_layer.update()` — `matrix_world` is a *request* on a parented,
  delta-transformed or constrained object. A failure reports the measured axis
  errors instead of "Aligned".
- **The panel status is re-measured every draw**, and prints the live dot
  products beside the words: *verified*, *FAILED validation: SI · +Z = …*, or
  *NOT validated: …*.
- **Flip Front/Back exchanges the LEFT and RIGHT references with the body.** It
  is the correction for swapped labels; turning without relabelling would leave
  the stored LEFT reference on the subject's right.
- **Preview Axes now draws the basis Apply will use.** It drew the *world* axes
  — the same three arms whatever the references were — so it agreed with every
  alignment, including a wrong one. Its length also divided a world-unit span
  by the millimetre multiplier, making it 1000× too short on a scan in metres,
  and `alignment_report` printed those world-unit spans labelled `mm`. Both are
  unit-correct now.
- **`metric_key` was not rigid-invariant**, contrary to sect. 6.3 and
  alignment's own docstring: `METRIC_QUANTISATION` was 1e-9, finer than the
  single precision Blender stores a transform in, so `T = LᵀL` came back as
  `diag(0.99999997, 0.99999997, 1.0)` and a pure rotation silently forced every
  geodesic to be recomputed. Quantised at 1e-6, above that noise floor and
  still 1 ppm — two micrometres on a two-metre subject.

**Verified:** `tests/test_alignment_blender.py` (new, 176 checks under
Blender) covers identity, 90° X, arbitrary XYZ, large translation + rotation,
non-orthogonal picks, Move To World Origin on and off, the reported status
defect, Preview/Apply agreement, Flip, Reset and the panel text — asserting
transformed world reference vectors throughout, plus the rigid invariants
(geometry hash, metric key, mesh vertices, SurfacePoint triangle/barycentric,
scale and all six pairwise reference distances). `tests/test_alignment.py`
grows to 121 offline checks. Full regression 2,563 offline checks across
twenty-one suites plus 630 in Blender across seven suites, 0 failures.

## 0.24.2 — picking says why, instead of blaming the cursor

**Reported:** on a real preprocessed and repaired PLY measurement mesh at
location `(-28570, -2692, -176)`, rotation `(90.1, -2.3, -0.6)`, unit scale,
Alignment → Pick LEFT answered *"no mesh surface under the cursor of 'scan
(1)_BSMT'"* while the body was plainly on screen under the cursor. The
transform was the natural suspect.

**The transform was innocent.** Measured before changing anything:
`picking.ray_cast_object` hits correctly at identity, at that translation, at
that rotation, and at both together, on a body-scale mesh and after a full
Preprocessing → Mesh Repair pipeline. It inverts `matrix_world`, casts in
local space and transforms the hit back out, and a rigid transform cannot
make it miss.

**Alignment has no picking code of its own.** `bsmt.pick_alignment_reference`
delegates to `bsmt.pick_point` with `target='ALIGN'` — the same modal picker,
the same single `ray_cast_surface` call site, that Landmark Manager and Quick
Measure use. There was no legacy raycast to unify.

**Root cause: the target object was hidden in the viewport.** BSMT restricts
the cast to the active object — deliberately, because a measurement mesh sits
at exactly its source's transform and a scene-wide cast would silently return
the wrong one. But **BSMT's own Source / Measurement buttons set
`hide_viewport`** on that very object. Measured on Blender 4.5.13:
`hide_viewport`, a collection hidden in the viewport, and a collection
excluded from the view layer all make `Object.ray_cast` **raise**, while
`hide_set()` (the eye icon) leaves it working — and `evaluated.data` reports a
full mesh in all four, so evaluability cannot be probed without attempting a
cast.

So the sequence was: press *Source* to compare → the measurement mesh is
hidden but stays the **active object** → the researcher sees the source scan,
coincident and identical → clicks it → the pick, aimed at the hidden copy,
finds nothing and reports the cursor.

- **`picking.pick_blocker()`** now answers the researcher's question — can
  this object be seen and therefore clicked? — before any cast, and the picker
  reports *"'scan (1)_BSMT' is hidden in the viewport, so there is nothing on
  screen to click. Show it again — Scan Preprocessing > Measurement, or the
  eye and monitor icons in the Outliner…"* instead of blaming the cursor.
  Refusing is the safe direction: a pick on an object nobody can see would
  record a reference against geometry never inspected.
- **`ray_cast_object` falls back to BSMT's canonical BVH** when
  `Object.ray_cast` cannot answer, so a momentary depsgraph gap on a *visible*
  object no longer reads as "nothing there". Same BVH the SurfacePoint is
  built against, so the two cannot disagree about where the surface is.
- No transform is applied, no geometry is edited, and no alignment
  mathematics changed.

New `tests/test_picking_transforms.py`, 45 checks in Blender: identity,
translation, rotation, translation + rotation and the reported transform all
hit and land on the surface; empty space still misses; every hidden state is
reported with its real cause; BSMT's own *Show Source* button reproduces the
original failure and *Show Both* clears it.

## 0.24.1 — Mesh Repair is a workflow stage again

**The Mesh Repair panel was invisible.** Reported against a confirmed 0.24.0
install, and reproduced on a clean Blender config from the shipped ZIP: the
BSMT sidebar showed seven panels and Mesh Repair was not among them.

**Root cause — a design mistake made in 0.22.0, not a packaging or
registration failure.** The panel class existed, was in the registration
tuple, was registered by `panels.register()`, was present in
`bsmt-0.24.0.zip`, and Blender had it registered with the right category,
space and region. It carried:

    bl_parent_id = "BSMT_PT_preprocessing"
    bl_options = {'DEFAULT_CLOSED'}

so it was a **sub-panel of Scan Preprocessing**, which is itself closed by
default. It therefore never appeared as a workflow stage, and a researcher
whose mesh reported `NOT READY` had no visible route to the one panel that
could act on it. 0.19.0's Mesh Repair v1 made that considerably worse.

The nesting was introduced by the 0.22.0 sidebar reorganisation to keep the
top level to exactly the seven stages that milestone listed. That was the
wrong call: repair is a step of the research workflow, not a detail of
preprocessing.

- **Mesh Repair is now a top-level panel**, `bl_order` 30, between Scan
  Preprocessing (20) and Alignment (40). Stage numbers renumbered to keep
  gaps of ten.
- `readiness.STAGE_REPAIR` added so the panel order and the stage vocabulary
  stay in step, with a stage hint — *"Repair the blocking defects, then
  re-analyze."* — shown only when the mesh verdict is `NOT_READY`.
- **New regression test that would have caught this.** Reading
  `panels.classes` never could: the class was always there. The test now
  enumerates what **Blender itself** has registered in the BSMT category,
  requires the eight top-level labels in workflow order, and asserts Mesh
  Repair specifically is top level, in the BSMT category, with matching space
  and region, and with no `poll()` or `draw_header()` that could suppress it.

Final order: Scan Setup → Scan Preprocessing → **Mesh Repair** → Alignment →
Landmark Manager → Measurement Manager → Measurement Visualization → Results
and Export.

No repair behaviour, repair algorithm or readiness policy was changed.

2,525 offline checks across twenty-one suites, 0 failures; workflow-UI 89 →
98, mesh-repair 76/76, degenerate-policy 35/35, preprocessing 110/110 and
path visualization 90/90 unchanged. Verified visible from a clean-config
install of `bsmt-0.24.1.zip`.

## 0.24.0 — Mesh Repair v1: bounded local repair of degenerate triangles

Since 0.23.0 a degenerate triangle hard-blocks exact surface measurement,
which is correct and left a real scan stuck: ~351,220 triangles, 1 component,
0 boundary edges, 0 non-manifold edges — measurable in every respect except
that a handful of collapsed vertices had produced zero-area triangles. This
milestone gives that scan a way forward without welding anything the
researcher did not ask for.

**Detect → locate → inspect → preview → repair → re-analyze**, all in the
existing **Mesh Repair** panel, on the **measurement mesh** only.

- **Analyze Mesh now lists degenerate triangles**, classified as either a
  *collapse* (two or more corners at bit-identically the same position, so a
  local merge fixes it) or a *sliver* (three distinct positions, which no
  automatic repair may touch). The list comes from the existing Analyze pass,
  not a second operator.
- **Show Degenerate Triangles** marks each one with a 3D cross, because a
  zero-area triangle has no outline to draw — highlighting it as edges would
  draw nothing. The selected defect gets a second, larger white marker.
  Helpers live in the BSMT helper collection, are unselectable, and are
  removed by Clear Highlights.
- **Defect i / N with Previous / Next**, and **Focus Selected Defect**, which
  moves the *view* only — the scan is never moved, rotated or scaled.
- **Preview Repair** states exactly what will happen — "Repair will merge 2
  exact coincident vertices and remove 1 zero-area face." — and changes
  nothing. When there is no safe local repair it says so instead of guessing.
- **Apply Repair** merges *only* the exactly coincident vertices inside the
  chosen degenerate triangles, using `bmesh.ops.weld_verts` with an explicit
  targetmap. **There is no distance tolerance anywhere in the repair path**,
  and no global merge-by-distance — asserted by tests against the code with
  comments and docstrings stripped. A vertex is only ever welded onto one at
  bit-identically the same coordinates, so no surface point moves.
- **Validity guard (this earned its place).** The first fixture — 14 adjacent
  vertices collapsed onto one point — would have introduced 2 non-manifold
  edges. The guard caught it, restored the mesh from its backup, and reported
  why. A repair that raises non-manifold edges, boundary edges, components or
  degenerate triangles, or that loses appearance data, is reverted rather
  than reported as a success with a caveat.
- **After a repair**: the canonical cache is rebuilt, landmarks on that mesh
  are re-classified by the existing status rules (STALE, **never**
  re-projected), measurement results and cached paths on that mesh are
  invalidated, diagnostics re-run, and the verdict is re-derived by the same
  `blocking_defects` → `classify_ready` / `preflight` policy. No
  Repair-specific readiness rule exists.
- **Landmark warning before repairing**, in the panel, naming how many will
  need re-picking.
- **Provenance is appended**, never overwritten: repair applied, type,
  degenerate before/after, vertices merged, faces removed, version and
  timestamp sit alongside the preprocessing record.
- Not in v1, deliberately: global welding, tolerance-based near-coincident
  merging, hole filling, remeshing, smoothing. Boundary edges and non-manifold
  edges can still be *shown* by the existing buttons and are not auto-repaired
  here.

**A threshold worth knowing.** BSMT calls a triangle degenerate when its area
is at or below `(1e-9 × bounding-box diagonal)²`. That catches triangles that
are *exactly* flat, not merely thin: a sliver of area ~7e-09 on a metre-scale
mesh is not flagged. The rule is unchanged — this is what it has always
meant, now stated.
- Tests: `tests/test_repair.py` 182 → 238 offline; new
  `tests/test_mesh_repair_blender.py`, 76 checks in Blender. 2,516 offline
  checks across twenty-one suites, 0 failures; degenerate-policy 35/35,
  workflow-UI 89/89, preprocessing 110/110 and path visualization 90/90
  unchanged.

## 0.23.0 — one degenerate-triangle policy

Closes the divergence recorded as an open item in 0.22.1: the sidebar said
`NOT READY` on a mesh with degenerate triangles and the solver ran on it
anyway.

- `preprocess.classify_ready` treated degenerate triangles as **blocking**.
- `preprocess.preflight` treated them as a **warning**, leaving `allowed`
  True, so an exact surface distance or path was computed on the same mesh
  the UI had just refused.

**One list, two presentations.** New `preprocess.blocking_defects(report)` is
now the only place that decides which mesh conditions make exact measurement
unsafe. `classify_ready` renders it as status wording and `preflight` renders
it as refusal wording; neither decides membership, so a condition added there
is enforced in both places at once.

Hard block — no canonical mesh, no triangles, non-manifold edges > 0,
degenerate triangles > 0.
Warning only — boundary edges, connected components > 1, coincident and
near-coincident vertices.
Density stays a **separate guarded** refusal, still overridable via the
density guard, so it is not on the defect list.

- **Both routes refuse.** Verified by counting constructions of
  `PyGeodesicAlgorithmExact`: on a degenerate mesh, the surface-distance
  operator, the A-to-B surface-distance operator and the surface-path
  operator each refuse with **zero** native solver constructions. All three
  already funnelled through one `solver_preflight` chokepoint, so no new gate
  was needed.
- **Refusal message**: "Surface calculation refused: the measurement mesh
  contains N degenerate (zero-area) triangle(s). Use the Mesh Repair panel
  and re-analyze before exact geodesic measurement." The brief suggested
  "Scan Repair"; the panel is called **Mesh Repair**, and pointing at a panel
  that does not exist would be worse than following the brief's wording
  literally.
- **Coincident vertices remain warning-only**, as required. They are
  diagnostic and matter only through the degenerate triangles they can
  produce — which the list already catches. Pinned by tests on both the pure
  policy and a real Blender mesh with a loose duplicate vertex and no
  degenerate triangles.
- **Same-component validation is unchanged**: several components is still a
  warning on the mesh, and a cross-component landmark pair is still refused
  individually by the solver's `DISCONNECTED` rule.
- Behaviour change worth noting: `preflight({})` — an empty topology report —
  now refuses instead of allowing. A report with no triangle count is a
  report that cannot be trusted, and an unknown mesh is what this gate exists
  to keep away from a native solver.
- Checked before adopting the policy: collapse decimation produces **zero**
  degenerate triangles across five mesh types at 50/25/10/5% ratios, so this
  hard block cannot affect meshes BSMT produces itself. Degenerate triangles
  come from the input scan.
- Fixed a `preflight` docstring that said "measurement copy".
- Tests: `tests/test_preprocess.py` 149 → 195 offline (scenarios A–E plus a
  cross-product check that NOT READY holds if and only if the solve is
  refused); new `tests/test_degenerate_policy.py`, 35 checks in Blender.
  2,460 offline checks across twenty suites, 0 failures; workflow-UI 89/89,
  preprocessing 110/110 and path visualization 90/90 unchanged.

## 0.22.1 — one readiness verdict, not three

Fixes a reported contradiction: a real measurement mesh with 351,220
triangles, 1 connected component, 0 boundary edges and 0 non-manifold edges,
but with degenerate triangles and 14 exact coincident vertices, was shown as
`Topology: Ready` in Scan Setup while Topology Diagnostics reported the
defects.

**Root cause: two UI surfaces carried their own private readiness rule, and
both tested non-manifold edges only.**

- `panels._draw_measurement_target` labelled the mesh `"Ready" if
  non_manifold == 0`, ignoring degenerate triangles entirely.
- `readiness.evaluate` did the same in its own way — a bare `non_manifold > 0`
  test — so the headline said `READY` on the same mesh.

Neither consulted `preprocess.classify_ready`, the authoritative policy added
in 0.21.0, which blocks on degenerate triangles. Reproduced before fixing: a
sphere with 14 vertices collapsed onto one another reports 1 component, 0
boundary, 0 non-manifold, 16 degenerate, 14 duplicate vertices — and the panel
said `Topology: Ready` while `classify_ready` said `NOT_READY`.

- **One authoritative source.** `state.mesh_verdict()` is now the only way any
  surface reaches a mesh verdict. It locates the current topology report and
  hands it to `preprocess.classify_ready`; it decides nothing itself. Both
  private rules are deleted, and tests assert on the *function bodies* that
  neither has grown back.
- **The policy is unchanged.** `classify_ready`'s rules are untouched, and so
  is `preprocess.preflight`, the solver gate. Non-manifold topology still
  blocks exactly as before; what changed is that two surfaces that were
  failing to apply the policy now apply it.
- **Stale diagnostics cannot leave an old status.** New
  `meshcache.peek_current(obj)` / `is_current(obj)` return a cached report
  only while its cheap fingerprint still matches the live object; the status
  paths use it, so a report that no longer describes the object reads as "not
  analyzed yet" instead of rendering as a confident label. Verified: after a
  geometry edit the depsgraph handler drops the entry, the verdict reports
  NOT ANALYSED, and re-analysing then reports NOT READY.
- `readiness.REASON_NON_MANIFOLD` is renamed `REASON_MESH_NOT_READY` — the
  code now covers every way the mesh itself can block, because the text it
  carries is `classify_ready`'s. Its pointer is still *Mesh Repair*.

**Coincident vertices — current policy, reported not changed (as asked).**
Exact-coincident and near-coincident vertex counts are **diagnostic only**.
They are counted and displayed in Topology Diagnostics and in the
before/after table, and they are read by **neither** `classify_ready` **nor**
`preflight` — so they change no verdict and refuse no solve. The 14 exact
coincident vertices in the report therefore had no effect on readiness by
themselves; it was the degenerate triangles they produced that should have
blocked it. A test now pins this, so changing it later has to be deliberate.

**A divergence worth knowing about, deliberately left alone.** Degenerate
triangles are *blocking* for `classify_ready` (NOT READY) but only a
*warning* for `preflight`, so the exact solver will still run on a mesh the
verdict calls NOT READY. That is the existing policy in both places and
changing either would be a policy change, which this task excluded.

## 0.22.0 — workflow-ordered sidebar

UI and workflow organisation only. No measurement, preprocessing, solver or
caching behaviour changed, and no operator gained or lost a capability.

**The sidebar used to open with the Phase 1 A-to-B ruler and put landmarks and
measurements above preprocessing and alignment** — roughly the reverse of the
order the research workflow runs in. A first-time user reading top to bottom
was led through the steps backwards.

- **Seven stages, in workflow order**: Scan Setup → Scan Preprocessing →
  Alignment → Landmark Manager → Measurement Manager → Measurement
  Visualization → Results and Export.
- **New *Scan Setup* panel** at the top: the readiness line, which mesh is the
  measurement target, the coordinate unit, and *Analyze Scan*. It defines no
  diagnostics of its own — *Analyze Scan* is the existing
  `bsmt.diagnose_topology` operator, and every number is read from the
  canonical mesh cache with `peek()`, which never builds one.
- **Quick Measure (A to B) is demoted** to a closed child of Scan Setup and
  labelled as a spot check that stores nothing. Mesh Diagnostics and Geodesic
  Backend (Developer) moved with it.
- **Mesh Repair** is now a child of Scan Preprocessing — it is mesh-quality
  work belonging to that stage. **Measurement Visualization** and **Results
  and Export** were promoted out of Measurement Manager to top-level stages 6
  and 7; *Session and Export* is renamed *Results and Export*.
- **Ordering is explicit.** Every panel declares `bl_order`; registration
  order no longer decides anything. Verified on Blender 4.5.13 that panels
  registered third/first/second with `bl_order` 30/10/20 lay out first,
  second, third — and a test reverses the registration tuple and asserts the
  sidebar order is unchanged. Nesting is one level deep, asserted by test.
- **Short per-stage guidance**, e.g. "Analyze the scan before preprocessing.",
  "Create or load landmarks before defining measurements.", "Resolve critical
  mesh issues before exact surface measurement." A stage with nothing to say
  says nothing. This is guidance, not a wizard and not enforcement: **no panel
  disables a later stage**, asserted by test. What may actually run is still
  decided by each operator's `poll()` and by the solver gate, neither of which
  this touched.
- **Status is stated once.** The readiness line and the full measurement-target
  block are each drawn in exactly one place; Measurement Manager keeps a single
  "Measuring on: <mesh>" line so a result can still be tied to its mesh.
- Alignment and CSV export were already implemented (0.14.0 and 0.18.0), so
  both are real panels in their workflow positions — no placeholders were
  added.
- Fixed two docstrings that said "measurement copy", against the project's
  single vocabulary; a test now enforces it in `panels.py` and `readiness.py`.
- Tests: new `tests/test_panel_order.py` (49 offline) and
  `tests/test_workflow_ui.py` (69 in Blender); `tests/test_readiness.py` grows
  102 → 149. 2,378 offline checks across nineteen suites, 0 failures;
  preprocessing 110/110 and path visualization 90/90 unchanged.

## 0.21.0 — scan preprocessing v1

Builds on the preprocessing shipped in 0.11.0 rather than replacing it. The
non-destructive copy, target-count decimation, the before/after topology table
and the provenance record were already there and are unchanged; what was
missing was everything to do with **colour**, and a plain verdict.

- **Colour attributes are now recorded, carried and verified.** This was the
  real gap: a PLY scan normally arrives with no UV map, no material and no
  image, and its entire appearance is a per-vertex colour attribute. The old
  comparison looked only at UV layers, materials and image textures, so it
  would have called such a scan "nothing to preserve" and reported a copy that
  had lost its colour as measurement-ready. A lost colour attribute is now a
  preprocessing FAILURE, exactly like a lost UV layer.
- A changed colour **domain** or a changed **active** colour attribute is
  reported as a note rather than a failure — the colour is still there.
- **A material slot that no face uses any more is reported.** Measured on
  Blender 4.5.13: a slot survives collapse decimation even when every face
  that referenced it has been collapsed away, so slot presence alone is a
  weaker check than it looks.
- **New: a one-line measurement-ready verdict** — `MEASUREMENT READY`,
  `WARNING` or `NOT READY` — computed from the existing topology diagnostics,
  never from a second definition of them. NOT READY for non-manifold edges,
  degenerate triangles, a canonical mesh that will not build, or lost
  appearance data. WARNING for several components, boundary edges, or a copy
  still above the density threshold. **Several connected components is
  deliberately not a blocker**: a real scan can legitimately contain more than
  one, and connectivity is a property of a landmark *pair*, enforced
  per-measurement by the solver's own validation.
- **The Scan Preprocessing panel now shows the whole picture**: mesh name,
  vertices, triangles, connected components, boundary edges, non-manifold
  edges, degenerate triangles, coincident vertices, UV maps, colour
  attributes, materials and image textures — plus an *Analyze Scan* button
  when the scan has not been analysed yet. The topology numbers are read from
  the cached canonical mesh and never build one, so a redraw cannot cost
  seconds on a dense scan.
- **Explicit *Source* / *Measurement* / *Both* visibility buttons**, replacing
  a single blind toggle, so silhouette, landmark regions and texture or colour
  registration can be compared directly. Visibility only; nothing is deleted
  or irreversibly hidden.
- **Preprocessing now warns when the source already carries landmarks.** They
  are never copied to the measurement mesh and never re-projected onto it — a
  decimated mesh is a different polyhedral surface, so a stored triangle index
  does not name the same point, and re-projecting one would move a
  researcher's landmark silently. The report says so and asks for a re-pick.
- Decimation and diagnostic elapsed times are recorded and shown.
- The density threshold message now names it as operational, not mathematical,
  and says what to do about it. The solver safety gate itself is unchanged.
- Still no repair of any kind: no welding, no merge-by-distance, no hole
  filling, no remeshing, no smoothing. Asserted by test.
- Found while building the acceptance fixtures, and worth knowing: a Blender
  UV sphere is watertight at 224, 3,968 and 65,024 triangles but reports **40
  boundary edges at 1,046,528** — in the source mesh, before any decimation.
  Collapse decimation did not open it further (40 → 30). Analyze the scan
  before trusting it; a dense mesh that looks closed on screen may not be.
- Tests: `tests/test_preprocess.py` grows from 107 to 147 offline checks, and
  the new `tests/test_preprocess_blender.py` adds 110 checks under real
  Blender covering scenarios A–D plus naming, provenance, landmark isolation
  and panel draw. 2,282 offline checks in total, 0 failures.

## 0.20.0 — measurement path cache

- **Fixed the real cause of slow measurement lines on large scans.** The
  computed geodesic polyline lived in exactly one place: the points of the
  helper Curve that drew it. The display *was* the storage, so every route
  that removed the helper destroyed a solve worth tens of seconds — minutes at
  1M+ triangles — and the only way back was to run the unbounded solver again.
  `path_is_current()` literally required the helper object to exist.
- **The polyline now lives in a cache datablock of its own** (`pathcache.py`),
  in the scan's LOCAL coordinates, with its surface normals, kept across a save
  by a fake user. The helper Curve is rebuilt from it and is now disposable:
  clearing, hiding, restyling or deleting a helper costs nothing to undo.
- **A cache entry carries its full identity**: both landmark stable ids, both
  endpoint SurfacePoints (triangle + barycentric), the geometry hash, the
  metric tensor and metric key, the polyline, its length and its solve time.
  Validating one reads properties only — no geometry, no canonical mesh, and
  no solver.
- **Path state is explicit**: NOT COMPUTED / CACHED / STALE / INVALID, named in
  the panel with the reason. A stale path is *marked*, not deleted and never
  silently recomputed — recomputing is a button press, and the panel says so.
- Added **Show/Hide Path** and **Clear Cached Path**. `Clear Selected` and
  `Clear All` now keep the cached path, which is what their descriptions have
  always promised; discarding a solve is a separate, deliberate action.
- **Every helper write is compared before it is made.** Assigning `bevel_depth`,
  a colour or `matrix_world` re-tags the datablock whether or not the value
  changed, and the colour picker fires on every mouse move — one full curve
  rebuild per pixel of drag. A redisplay that would produce identical points is
  skipped entirely by comparing a draw signature.
- **Canonical mesh invalidation is targeted**: editing one object no longer
  throws away every other scan's canonical mesh, and it marks the paths solved
  on that object stale rather than leaving them looking current.
- Added **stage timing** (`timing.py`) for cache validation, solver
  construction, exact path solve, result copy, and helper create/update, with a
  panel readout and a console report. It answers whether slowness is the
  solver, a cache miss, curve construction or handler churn. Logging is off by
  default and throttled to one line per stage every two seconds.
- Measured end to end on an 801,024-triangle scan: the exact path solve is
  **410.8 s** — 6.8 minutes, of which 0.28 s is building the solver and 409.9 s
  is propagation. That is what a destroyed cache used to cost. Immediately
  afterwards: hide→show 0.18 ms, colour 0.20 ms, thickness 1.11 ms, refresh
  0.13 ms, rigid translate 0.11 ms, zero pygeodesic calls.
- Measured on a 1,046,528-triangle scan with a 20,000-point path: cache
  validation 0.04 ms, hide→show 0.17 ms, colour change 0.19 ms, redisplay from
  cache 0.02 ms, rigid transform 0.13 ms — none of them reaching pygeodesic.
  Thickness change costs 15 ms, which is Blender re-evaluating the curve bevel
  and re-lifting the path; measured, not changed.
- Added `tests/test_pathcache.py` (offline) and
  `tests/test_path_visualization.py` (needs Blender), the latter counting every
  construction of `PyGeodesicAlgorithmExact` so "no solve happened" is proved
  rather than assumed. 2,242 offline checks plus 90 in Blender, 0 failures.

## 0.19.0 — cross-platform packaging

- **Ships as a Blender extension** with per-platform wheels. One ZIP installs
  on Windows x64 and macOS ARM64; Blender picks the right solver build and the
  user never runs pip.
- **Verified that Blender does not resolve wheel dependencies.** The pygeodesic
  wheel declares `numpy>=2` while Blender ships 1.26.4; measured after a real
  install, NumPy is untouched.
- **Fixed a packaging defect**: Blender *removes* `bl_info` from a module
  installed as an extension, and BSMT read it at runtime. Installing the
  extension failed with `NameError: bl_info`. Version now comes from a
  `VERSION` constant that survives both packaging modes.
- Added an **About BSMT** summary — version, Blender, platform, Python, NumPy,
  solver status — for bug reports.
- Added `tools/build_release.py`, `README.md`, `INSTALL.md`,
  `docs/LICENSING.md` and `docs/windows_acceptance.md`.
- Audited the source for Windows assumptions: no shell, no hard-coded paths,
  no hand-rolled path splitting, and every file open declares its encoding —
  which is what keeps Korean text intact on a Korean Windows.
- **Windows is packaged, not verified.** No one has run BSMT on Windows.

## 0.18.1 — picking robustness

- Fixed a rare pick that reported nothing hit. The rule judging whether a BVH
  hit belonged to its triangle was measuring a **ratio**, so it refused a point
  58 nanometres out of place while — because `barycentric()` silently projects
  — accepting a point a *kilometre* off the surface. Replaced with a geometric
  rule bounded by float32 precision: stricter off the plane, looser only within
  numerical noise in it.

## 0.18.0 — export, session metadata, protocol reuse

- Measurements and landmarks export to CSV; session metadata (Subject ID,
  Condition, Scan ID) travels on every row.
- A value that was never calculated exports as a **blank field, never a zero**.
- Protocols carry landmark and measurement definitions between subjects, keyed
  by stable id, and refuse to hold any scan, result or subject data.
- UTF-8 with BOM, so Excel reads Korean labels correctly.

## 0.17.1 — landmark visibility

- *Visible Surface Only* hides landmarks the mesh is in front of, by ray test
  rather than depth buffer, preserving exact pixel sizing.

## 0.17.0 — screen-space landmark markers

- Markers moved to a screen-space overlay: every landmark the same size at any
  zoom, sized in pixels, and no selectable objects added to the file.

## 0.16.0 — landmark labels and display controls

- Landmark names drawn in the viewport by a draw handler, with marker and label
  colour, size, offset and scope controls.

## 0.15.0 — UI wording, measurement drafts, readiness

- One vocabulary throughout the UI; a measurement cannot exist half-written;
  one global readiness line.
- Fixed a blocking repair defect: every manual repair was refused once
  non-manifold reached 0.

## 0.14.0 — rigid anatomical alignment

- Manual and landmark-based alignment. Object transform only; landmarks stay
  valid because `geometry_hash` excludes the transform and invalidation
  compares the rotation-invariant metric tensor.

## 0.13.x — mesh repair

- Automatic local non-manifold repair, one region at a time, each step reverted
  unless it strictly improves the topology.
- Fixed an object-identity defect where measurements could validate against the
  original scan instead of its measurement mesh.

## 0.12.0 — controlled mesh repair
## 0.11.0 — scan preprocessing and the solver safety gate
## 0.10.0 — measurement visualization and surface paths
## 0.9.0 — measurement manager
## 0.8.0 — landmark manager
## 0.7.0 — exact bounded MMP surface distance
## 0.5.x — canonical mesh, BVH picking, SurfacePoint
## 0.1.0 — straight-line distance between two picked points
