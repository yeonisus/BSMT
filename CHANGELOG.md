# Changelog

Version numbers are `major.minor.patch`. Every entry lists what changed and,
where a defect was fixed, what it actually was. The full design record is in
`PROJECT_SPEC.md`.

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
