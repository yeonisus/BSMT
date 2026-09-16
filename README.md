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
| **Mesh Repair** | Finds and conservatively repairs non-manifold edges and small holes, one region at a time, reverting anything that does not improve the topology. A defect you have inspected and judged to be a scan artefact can be removed by deleting the connected component it sits in — never the body, and never automatically. A defect *on* the body can have its local flap of faces removed, but only when the surrounding topology names one unambiguous candidate |
| **Alignment** | Rigidly aligns the scan to an anatomical frame, by hand or from four reference points. Object transform only — no vertex is moved |
| **Landmarks** | Named points picked on the surface, stored as triangle + barycentric coordinates rather than as an XYZ, so they survive any rigid transform |
| **Measurements** | Researcher-defined pairs of landmarks. Straight distance, exact surface distance, or both |
| **Surface Path** | The exact geodesic polyline a surface distance follows, drawn in the viewport. Solved only when you press the button, then cached per measurement — showing, hiding, restyling or moving it never re-solves |
| **Surface Regions** | A researcher-defined **closed boundary** on the mesh, specified by an **ordered set of landmarks**. Consecutive landmarks — including the final-to-first pair — are joined by cached surface geodesic paths. Measurement Manager is not involved |
| **Surface Interior** | Which side of a computed boundary is the region. Triangles are classified as inside, outside, or cut — and the cut ones are clipped exactly. Drawn as a semi-transparent fill on the body surface. It does **not** itself report an area — *Compute Area* is a separate explicit press that measures this classification |
| **Surface Area** | The **mesh surface area of the selected region on the triangular body mesh** — whole interior triangles plus exactly-clipped boundary polygons. Reported in mm² and cm². It is **not** true anatomical surface area |
| **Thickness Preview** | The selected interior offset outward along the surface normals and closed with a side wall, so it reads as a panel. A **visualization**, not a physical simulation and not a manufacturing model |
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
| 8 | **Surface Regions** | list landmarks in order, compute the closed boundary they describe |
| 9 | **Results and Export** | session metadata, CSV, protocols |


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
   - **Scan artefacts.** Real scans carry small detached fragments, and one
     of them is often what holds the blocking defect. *Show Edges* draws the
     defects, *Focus* frames them, *Defect i / N* steps between them, and
     *Preview Artifact* highlights the entire connected component the focused
     defect belongs to and states its size, extent and share of the mesh.
     If — and only if — you judge that geometry to be an irrelevant artefact,
     *Delete Artifact* removes that whole component after a confirmation that
     spells out what goes. **BSMT never decides this for you**, and it
     refuses outright when the defect is on the main body component.
   - **A defect on the body itself.** When the defect turns out to be on the
     primary body component, deleting the component is — correctly — refused,
     and *Local Defect Repair* is the second strategy. *Inspect Local
     Topology* walks the surface outward from the defect with the defect's own
     edges as a wall, and reports what it found: a **small dangling flap**, a
     **small local branch**, a **duplicated face** (which it hands to *Remove
     Duplicate Faces* rather than repeating), or **ambiguous**. Only the first
     two offer a removal. *Preview Candidate Faces* highlights exactly the
     faces that would go — never the component — and *Remove Local Artifact
     Faces* removes them inside the same transaction every other repair uses.
     If the local topology is ambiguous, BSMT says so and asks for a manual
     edit rather than guessing.
6. **Alignment** → align the scan, if the study needs a common frame.
7. **Landmark Manager** → add landmarks, pick each one on the surface.
   **Do this on the measurement mesh**, after preprocessing — see below.
8. **Measurement Manager** → *Add Measurement*, choose From and To, then
   *Calculate All*.
9. **Surface paths only when you need one.** They are computed on request and
   then cached, so showing, hiding or restyling one never re-solves.
10. **Surface Regions** (optional) → define a closed boundary by listing
    landmarks in the order they run round it: *New Region*, then *Add
    Selected* for each landmark, reorder with the arrows, and press *Compute
    Boundary*. The boundary closes back to the first landmark automatically,
    so there is nothing to reverse and no measurement to define. **You do not
    need Measurement Manager for this.**

    *Compute Boundary* is the only button here that runs the solver — one
    exact surface path per consecutive pair — and it does so only when you
    press it. Editing the list, reordering it, renaming the region, showing,
    hiding and saving all reuse what it cached. Editing the landmarks after
    a computation marks the boundary **stale**; BSMT says so and waits rather
    than re-solving minutes of work unasked. No area is calculated — this
    milestone establishes the boundary only.

11. **Surface Interior** (optional) → once a boundary is computed, press
    *Compute Interior* to work out which side of it is the region. Every
    triangle is classified as wholly inside, wholly outside, or **cut by the
    boundary**, and the cut ones are clipped exactly — so the region's shape
    is right up to its edge rather than rounded to whole faces.

    A closed loop on a closed surface bounds **two** regions, so you choose:
    *Smaller Side* (the default) or *Complement Side*. *Show Fill* draws the
    classified surface, and the faces it draws **are** the analysis, not a
    decorative overlay. Compute Interior runs **no solver**.

12. **Surface Area** (optional) → press *Compute Area*. It reports the
    **mesh surface area of the selected region on the triangular body mesh**,
    in mm² and cm², split into what came from whole triangles and what came
    from the clipped ones at the boundary. It is a separate press on purpose:
    nothing computes an area on its own, so a number on screen always has a
    provenance.

13. **Thickness Preview** (optional) → set *Thickness (mm)* and press *Show
    Preview* to see the region offset outward along the body surface normals,
    with a side wall making it read as a panel. It is built from the
    classified interior, so changing the thickness, colour or opacity rebuilds
    only the preview — never the boundary or the interior, and never the
    solver.

    **It is a visualization, not a physical simulation and not a
    manufacturing model.** See the scientific notes below.
14. **Results and Export** → fill in Subject ID / Condition / Scan ID, then
    *Measurements CSV* and *Landmarks CSV*.
15. *Save Protocol* once, and load it for every later subject.

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
- **Artifact deletion is a researcher’s decision, not an automatic clean-up.**
  BSMT does not determine whether geometry is anatomically relevant, and being
  small is no evidence of being unwanted — hair, a garment or a held object
  can all be legitimate small components. *Delete Artifact* removes only the
  connected component holding the defect you inspected, from the measurement
  mesh only, after an explicit confirmation. Deleting the **primary body
  component** — the one with the most triangles — is refused, as is a mesh
  with only one component, as is a tie for largest. The deletion is
  transactional: the mesh is restored if the result would be unsafe, if a
  degenerate triangle appeared, if more than that component went, or if the
  body’s triangle count changed.
- **Local face repair is offered only when the topology is unambiguous.**
  BSMT does not decide anatomical relevance here either. *Remove Local
  Artifact Faces* acts only when treating the focused defect's non-manifold
  edges as a separator leaves exactly one small branch standing against one
  continuing body surface. Two comparable branches, three or more branches, a
  branch that keeps going, a candidate above the software safety caps, or a
  removal that is predicted not to reduce the non-manifold count are all
  **refused**, with the reason stated. The caps — a 512-face inspection limit,
  a 64-face candidate limit, and a body branch at least 8× the candidate — are
  software safety caps, not anatomical criteria, and the panel states them.
  The edit is transactional and verified: the mesh is restored unless the
  surviving surface is bit-identical outside the approved candidate, the
  focused defect's own edges are gone, no non-manifold edge appeared anywhere
  that was not there before, no degenerate triangle appeared, and exactly the
  approved faces and their stranded vertices went. Boundary edges may rise —
  removing a flap exposes the boundary it was covering — and that is reported,
  not refused.
- **Three repairs, three different topologies.** *Weld Non-Manifold Region*
  merges coincident vertices at the non-manifold edges only. *Delete Artifact*
  removes a whole detached component that is not the body. *Remove Local
  Artifact Faces* removes a few faces inside a component, including the
  primary body. None of them is a global cleanup, and none of them runs
  without an explicit press.
- **A Surface Region is a boundary, not an area.** It is a closed boundary
  specified by an ordered set of landmarks; consecutive landmarks, including
  the final-to-first pair, are joined by cached surface geodesic paths on the
  triangular mesh. The area it encloses is not part of the definition: it
  comes from two separate explicit presses, *Compute Interior* then
  *Compute Area*, and a region changes no geometry.
- **A region is defined by landmarks, not by measurements.** You never have
  to create a measurement to define one, and deleting, renaming or editing a
  measurement cannot affect a region. The two share the geodesic backend and
  nothing else.
- **A computed boundary is a cached result of the definition that produced
  it.** Editing the landmark list — adding, removing or reordering — makes it
  **stale**, and so does re-picking a boundary landmark, deleting one, or
  changing the geometry. BSMT says so and stops: it never rebuilds a
  boundary, re-projects it, or substitutes another landmark, because a solve
  is minutes of work and silently redoing it is not a decision software
  should make. A boundary it cannot vouch for is drawn in a warning colour
  rather than passing as a trusted one.
- **A Surface Interior is the selected side, not "the inside".** A closed
  boundary on a closed surface bounds two regions, and nothing about the
  geometry says which one a researcher meant. BSMT computes both, offers the
  smaller by default, and never calls either anatomically inside.
- **Triangles the boundary cuts are clipped exactly.** They are neither
  rounded up to whole faces nor dropped. The measured worst error in tiling a
  cut triangle is 3.6e-07 of its own area, which is the float32 precision the
  cached boundary is stored at. The drawn fill *is* that classification, so
  what you see is what a later area calculation will sum.
- **An interior BSMT cannot determine is refused.** A boundary that does not
  cut its surface component in two — which an open surface can leave genuinely
  ambiguous — gets a refusal with a reason, not a guess.
- **Computing the interior adds one extra self-intersection check.** Inside a
  single triangle the boundary is straight between samples, so a crossing
  there is an ordinary segment intersection and *is* detected — which the
  boundary's own shared-point test cannot do. This is an **additional
  triangle-local** check. It is **not** complete or general on-surface
  self-intersection detection, and BSMT does not claim it is.
- **Surface Area is mesh area, not anatomical area.** It is the area of the
  selected region *on the triangular mesh you gave BSMT*. A triangulation is a
  chord approximation of a curved surface, so its area reads systematically a
  little under the smooth surface it was sampled from — the same property that
  makes a polyhedral geodesic shorter than a smooth one. BSMT does not claim
  true anatomical surface area, actual human surface area, or an exact
  smooth-body area.
- **The area is summed from the same classification that is drawn.** Whole
  interior triangles plus the exactly-clipped polygons at the boundary — never
  whole boundary triangles, and never a projected polygon. The strongest check
  on it is that the selected side and its complement add up to the surface
  component's own area; on a sphere fixture that holds to **3.3e-09**
  relative.
- **Area is computed in float64 and stored at float32** (about seven
  significant digits, ~0.004 mm² on a 400 cm² region) because that is what a
  Blender property is. That is far finer than the mesh's own fidelity to a
  body.
- **Nothing computes an area on its own.** Not Compute Boundary, not Compute
  Interior, not a panel draw, not a save. When anything it depends on changes
  the area goes **stale**, and a stale area is never shown as a number — only
  as a status, because a figure on screen is read as a result whatever label
  sits above it.
- **Thickness has nothing to do with the area.** The area is the body-surface
  region only: not the offset outer surface, not the side wall, not a shell
  area and not a volume.
- **Thickness Preview is a visualization, not a simulation.** It offsets each
  point along its own surface normal, so the perpendicular gap is exactly the
  thickness you asked for — but on a curved body the *shape* distorts: the
  outer surface stretches over convex areas, compresses over concave ones, and
  where the thickness exceeds the local radius of curvature it folds through
  itself. The material a real panel would need is **not** uniform. BSMT
  reports obvious fold-through as a warning; that is a warning, not a
  guarantee of validity.
- **The preview is outward only, and refuses when outward is undefined.**
  Outward is taken from the enclosed volume of a closed surface component. On
  an open component nothing defines which side is out, so the preview is
  refused rather than silently extruded into the body.
- **Region self-intersection checking is deliberately incomplete.** It detects
  a landmark the loop returns to, the same landmark twice in a row, and
  non-adjacent boundary segments that share a point. It does *not* detect a crossing that
  happens between two sampled points of a path. No projected-polygon test is
  used, because a boundary that wraps a limb self-intersects in every flat
  projection while being simple on the surface. Because that check is
  incomplete, BSMT never reports a boundary as *simple* — only as **closed**.
  The panel and the validation report say which checks a verdict actually
  ran: the shared-point test belongs to an explicit *Validate Region*, not to
  a redraw, and a region stops counting as validated the moment its boundary
  is edited.
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
