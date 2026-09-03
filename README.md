# BSMT — Body Surface Measurement Tool

A Blender add-on for **anthropometric measurement on textured 3D human body
scans**. BSMT measures the distance between two points *across the surface* of
a scan — the exact geodesic distance — as well as the straight line between
them, and keeps the record needed to reproduce a measurement later.

**Research software.** BSMT is written for one lab's own studies. It is not a
validated medical or clinical instrument, it has not been through any
regulatory process, and its numbers should be treated as research data with
the caveats in [Limitations](#limitations).

---

## What it does

| Stage | What happens |
|---|---|
| **Import** | A textured OBJ (with MTL and image) or a PLY with vertex colors |
| **Scan Preprocessing** | Analyzes a scan, then builds a lighter *measurement mesh* at a target triangle count. UV maps, colour attributes, materials and image textures are carried across and verified; the source scan is never modified |
| **Mesh Repair** | Finds and conservatively repairs non-manifold edges and small holes, one region at a time, reverting anything that does not improve the topology |
| **Alignment** | Rigidly aligns the scan to an anatomical frame, by hand or from four reference points. Object transform only — no vertex is moved |
| **Landmarks** | Named points picked on the surface, stored as triangle + barycentric coordinates rather than as an XYZ, so they survive any rigid transform |
| **Measurements** | Researcher-defined pairs of landmarks. Straight distance, exact surface distance, or both |
| **Surface Path** | The exact geodesic polyline a surface distance follows, drawn in the viewport. Solved only when you press the button, then cached per measurement — showing, hiding, restyling or moving it never re-solves |
| **Export** | Measurements and landmarks as CSV, plus a reusable protocol as JSON |

Distances are always reported in **millimetres**.

## Requirements

- **Blender 4.5 LTS** (developed and validated on 4.5.13). Blender 4.2 is the
  minimum the extension package declares, but only 4.5 has been tested.
- No manual Python setup. The exact geodesic solver ships inside the release
  package as a platform wheel and Blender installs it for you.

## Validated platforms

| Platform | Status |
|---|---|
| macOS 26, Apple Silicon (ARM64), Blender 4.5.13 | **Validated** — full workflow, automated and manual |
| Windows 10/11 x64, Blender 4.5.13 | **Packaged, not yet verified on hardware.** The Windows wheel is the correct ABI and the source has been audited for platform assumptions, but no one has run BSMT on Windows. See [docs/windows_acceptance.md](docs/windows_acceptance.md) |
| Linux x64 | Not packaged. A wheel exists upstream; nothing has been tested |

## Installation

See **[INSTALL.md](INSTALL.md)**. In short: download the release ZIP, then in
Blender use *Preferences → Get Extensions → Install from Disk*, and enable it.
No Terminal or PowerShell is needed.

## Typical workflow

The BSMT sidebar is ordered as this workflow runs, top to bottom. Each stage
says in one short line what it still needs; nothing is hidden and nothing is
stepped through, so any panel can be opened at any time.

| # | Sidebar panel | Stage |
|---|---|---|
| 1 | **Scan Setup** | which scan is being worked on; *Analyze Scan* |
| 2 | **Scan Preprocessing** | build and check the measurement mesh |
| 3 | **Mesh Repair** | locate and fix the defects that block measurement |
| 4 | **Alignment** | rigid anatomical frame, if the study needs one |
| 5 | **Landmark Manager** | define and pick landmarks |
| 6 | **Measurement Manager** | define pairs, calculate |
| 7 | **Measurement Visualization** | draw chords and cached surface paths |
| 8 | **Results and Export** | session metadata, CSV, protocols |


1. Import the scan (PLY, textured OBJ, or whatever your scanner writes).
2. **Scan Setup** → *Analyze Scan*. Then in **Scan Preprocessing**, read the vertex and triangle
   counts, the connected components, the boundary and non-manifold edge
   counts, and which appearance data the scan carries — UV maps, colour
   attributes, materials, image textures.
3. **Scan Preprocessing** → *Create Measurement Mesh*. Pick a target triangle
   count first (High 500k / Standard 350k / Light 200k, or type your own).
4. Review the result: the before → after topology table, the appearance
   checks, and the one-line verdict — `MEASUREMENT READY`, `WARNING` or
   `NOT READY`. Use *Source* / *Measurement* / *Both* to compare the two by
   eye: silhouette, landmark regions, texture and colour registration.
5. **Mesh Repair** → *Analyze Mesh*. If the verdict is
   `NOT READY`, this is where you fix it:
   - **Degenerate triangles** block exact measurement. *Show Degenerate
     Triangles* marks them, *Defect i / N* steps through them, *Focus* frames
     the view on one, *Preview Repair* says exactly what would change, and
     *Apply Repair* merges only the exactly coincident vertices inside those
     triangles. Re-analysis and the verdict update automatically.
   - **Non-manifold edges** likewise block; *Repair Local Defects* handles
     small localised artefacts, and anything larger is reported for manual
     inspection.
6. **Alignment** → align the scan, if the study needs a common frame.
7. **Landmark Manager** → add landmarks, pick each one on the surface.
   **Do this on the measurement mesh**, after preprocessing — see below.
8. **Measurement Manager** → *Add Measurement*, choose From and To, then
   *Calculate All*.
9. **Surface paths only when you need one.** They are computed on request and
   then cached, so showing, hiding or restyling one never re-solves.
10. **Results and Export** → fill in Subject ID / Condition / Scan ID, then
    *Measurements CSV* and *Landmarks CSV*.
11. *Save Protocol* once, and load it for every later subject.

**Preprocess before you landmark.** A decimated mesh is a different polyhedral
surface, so a landmark's stored triangle and barycentric coordinates do not
name the same point on it. BSMT therefore never copies landmarks onto a
measurement mesh and never re-projects them — doing either would move a
researcher's landmark silently. If the source already carries landmarks, the
preprocessing report says so and asks you to re-pick them on the copy.

**Scan Setup** shows a single readiness line — `READY FOR MEASUREMENT` or
`NOT READY: <reason>` — which is the fastest way to find out what is missing.
It appears once, at the top, and is not repeated per panel.

Every mesh status in BSMT — the readiness line, the `Topology:` line in Scan
Setup, the preprocessing verdict, **and the solver's own refusal** — comes
from one list of blocking defects.

**Hard block** (the UI says `NOT READY` *and* exact surface calculation is
refused): the canonical mesh cannot be built, the mesh has no triangles, it
has non-manifold edges, or it has degenerate (zero-area) triangles.

**Warning only** (measurement is still allowed): several connected
components, boundary edges, and exact- or near-coincident vertices. A dense
mesh is warned about and, by default, its exact solve is refused by the
separate density guard — which you can switch off deliberately.

**Coincident vertices are diagnostic only.** They are reported but never
block on their own; they matter when they produce degenerate triangles, and
those do block. Several components never blocks the mesh either — a landmark
*pair* spanning two components is refused individually, which is the check
that actually matters.

Two panels sit under Scan Setup rather than in the workflow itself:
**Quick Measure (A to B)**, a Phase 1 spot check between two picked points
that stores nothing in the landmark or measurement lists, and **Geodesic
Backend (Developer)**. Both are closed by default.

## Limitations

- **The measurement mesh is a representation, not the scan.** Decimation
  changes the polyhedral surface representation. The measurement copy is not
  mathematically identical to the source mesh, so a surface distance on the
  copy is not identical to one on the original. The triangle counts, the
  ratio and the method are recorded on the copy and in every export.
- **The source scan is never modified.** Preprocessing runs entirely on a
  duplicate object with its own mesh datablock, and the operator verifies the
  source's counts and mesh name afterwards rather than merely promising it.
- **Preprocessing does not repair anything.** It decimates and copies. No
  welding, no merge-by-distance, no hole filling, no remeshing, no smoothing.
  On a human scan those silently fuse anatomically distinct surfaces that
  happen to touch — arm to torso, finger to finger, garment to skin — and a
  fused surface produces a confidently wrong, systematically short geodesic.
- **Mesh Repair v1 does not perform global welding or automatic hole
  filling.** It merges only vertices that are at bit-identically the same
  position *and* sit inside a degenerate triangle you have selected. There is
  no distance tolerance, for the reason above. Near-coincident vertices are
  reported and never merged; boundary edges can be shown and are never
  filled; non-manifold geometry beyond small local artefacts is reported for
  manual inspection.
- **A repair invalidates landmarks on that mesh.** They go STALE and must be
  re-picked; BSMT never re-projects them, because a stored triangle index
  does not name the same point on a changed surface.
- **"Degenerate" means exactly flat.** A triangle counts as degenerate when
  its area is at or below `(1e-9 × bounding-box diagonal)²`. A merely thin
  sliver is not flagged.
- **The 1,000,000-triangle density limit is operational, not mathematical.**
  It is where the exact solver becomes slow and has been observed to be
  unstable on real scans; it says nothing about what the MMP algorithm can
  represent.
- **Exact geodesic distance is exact for the mesh, not for the body.** It is
  the true shortest path across the triangulated surface it is given.
- **Unsafe topology is refused, not worked around.** BSMT will not produce a
  surface distance on a mesh with non-manifold edges or degenerate triangles.
  The refusal and the sidebar's `NOT READY` come from the same list, so they
  cannot disagree.
- **Nothing is silently approximated.** A measurement that cannot be computed
  reports a failure state; an uncalculated value exports as a blank field, not
  a zero.
- **A rigid alignment restores to single precision**, not bit-exactly:
  Blender's transform setters decompose to float32, so an Apply/Reset round
  trip leaves ~6e-08 units, about 1e-4 mm on a 1.7 m body. It does not affect
  any stored measurement.
- **No automatic landmark detection.** Every landmark is placed by a human.

## The exact geodesic dependency

Surface distance and surface paths use
[pygeodesic](https://github.com/mhogg/pygeodesic) 0.1.11, a Cython wrapper of
Kirsanov's implementation of the exact MMP algorithm (Mitchell, Mount and
Papadimitriou, 1987). It is a **native** extension module, so it must match the
platform and the Python ABI — which is why the release package carries one
wheel per platform.

If the solver is missing, BSMT still enables and everything except surface
distance and surface paths keeps working; those report a clear dependency
error. The *About BSMT* section of *Results and Export* says which state you
are in.

## Licensing

**Not yet decided.** See [docs/LICENSING.md](docs/LICENSING.md) for the
third-party licences involved and the decision the project owner needs to
make. The extension manifest currently carries a **provisional** value that
must be confirmed before BSMT is distributed to anyone.

## Documentation

- [INSTALL.md](INSTALL.md) — installation, and what to do when it goes wrong
- [CHANGELOG.md](CHANGELOG.md) — what changed in each version
- [docs/LICENSING.md](docs/LICENSING.md) — third-party licences, open decision
- [docs/windows_acceptance.md](docs/windows_acceptance.md) — the checklist a
  Windows machine must pass
- `PROJECT_SPEC.md` — the full design record, including every defect found and
  why each decision was made
