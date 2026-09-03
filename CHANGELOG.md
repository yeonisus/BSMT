# Changelog

Version numbers are `major.minor.patch`. Every entry lists what changed and,
where a defect was fixed, what it actually was. The full design record is in
`PROJECT_SPEC.md`.

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
