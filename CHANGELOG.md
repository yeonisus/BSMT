# Changelog

Version numbers are `major.minor.patch`. Every entry lists what changed and,
where a defect was fixed, what it actually was. The full design record is in
`PROJECT_SPEC.md`.

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
