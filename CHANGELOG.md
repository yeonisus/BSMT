# Changelog

Version numbers are `major.minor.patch`. Every entry lists what changed and,
where a defect was fixed, what it actually was. The full design record is in
`PROJECT_SPEC.md`.

## 0.29.1 — Milestone 3.37, one repair backup instead of eighteen

Opening a saved scan had become extremely slow. **The load path was not the
problem and has not been touched**: BSMT's `load_post` handler costs 0.0001 s,
0.007% of the open. The file was the problem. On a real 350,000-triangle scan,
**444.5 MB of a 554.5 MB `.blend` was repair backups** — 80.2% of it, eighteen
full copies of the mesh, of which seventeen could not be restored by any code
path and could not be freed by Blender either. Across eight working files,
1,309 MB of 2,048 MB was backups.

This release is a **storage-lifecycle fix only.** No scientific computation,
no repair algorithm, no measurement result and no geodesic behaviour changed.

#### The cause

Each repair backs the mesh up as a real datablock, so an undo cannot
half-succeed. The backup is marked `use_fake_user` so it survives a save and
reload — that part is deliberate and is unchanged. The cleanup beside it was
not:

    previous = bpy.data.meshes.get(obj.data.name + BACKUP_SUFFIX)
    if previous is not None and previous.users == 0:   # never true
        bpy.data.meshes.remove(previous)
    ...
    backup.use_fake_user = True                        # users >= 1, always

**A fake user is a user.** `previous.users` could never be 0, so the previous
backup was never removed; the name was still taken, so Blender suffixed the
new copy `.001`, `.002`, and every one of them stayed in the file. Only the
most recent is named by `props.repair_backup_mesh`, so the rest were
unreachable — not stale data the researcher might want, but data nothing could
ever read.

Two paths fed it: every successful repair deliberately keeps its backup for
*Undo Repair*, and the boundary auto-repair takes one **per region attempt,
inside a loop**.

#### The invariant, now enforced

For each repair target: **zero backups before the first repair, exactly one
after every successful repair**, and that one is the mesh as it stood
immediately before the most recent repair.

A backup is no longer identified by its name. It carries explicit ownership —
`bsmt_repair_backup`, `bsmt_repair_backup_owner`, `bsmt_repair_backup_mesh` —
written when it is created, so BSMT deletes a datablock only when it can prove
the datablock is its own. A researcher's mesh that merely reads like a backup
(`Body_BSMT_backup_of_mine`) is not one, and a backup anything still
references is left exactly as it was rather than deleted on a guess. Renaming
the object between two repairs no longer orphans the earlier backup.

The order is chosen so no failure can strand a repair: the new backup is
created, tagged and given its fake user **before** any older backup is
released, so there is never an instant with nothing to restore from.

**A fake user is no longer read as evidence that a datablock is wanted.** For
one BSMT can prove is its own stale backup, the fake user is cleared
deliberately.

#### Undo Repair is unchanged

One slot, one step: the state immediately before the most recent successful
repair. The backup is **not** consumed — pressing *Undo Repair* twice restores
the same state twice, exactly as before. This is not multi-level undo and was
not turned into one.

#### Clean Stale Repair Backups

The fix is preventive; it does not shrink a file that is already bloated. A
new explicit operator — **Clean Stale Repair Backups**, in Mesh Repair beside
*Undo Repair* — does that. It keeps the backup *Undo Repair* points at,
removes only identifiable BSMT repair backups, never touches a scan, a live
measurement mesh or any mesh still in use, lists every datablock and its size
before removing anything, and reports how many went and roughly how much was
reclaimed. Automatic cleanup on load was considered and **rejected**:
ownership of a backup written by an earlier BSMT can only be inferred from its
name, and an inference is not a licence to delete a researcher's geometry.

#### Measured, on the real file

| | before | after |
| --- | --- | --- |
| `_M13.blend` | 554.5 MB | **151.5 MB** (−72.7%) |
| backup datablocks | 18 | 1 |
| cold open, fresh process | 1.827 s | **0.384 s** |
| peak resident memory | 1.31 GB | **506 MB** |

The memory figure is the one that made the difference. On a 16 GB machine
these files pushed Blender into swap, which is where the tens of seconds came
from; halving the footprint is what removes them.

At scan density (358,800 triangles), eighteen repairs: 329.2 MB → **35.0 MB**,
open 0.928 s → 0.096 s.

#### Also fixed, found by the new suite

`restore_backup` assigned the live name while the old datablock still held it,
so Blender returned `<name>.001` and the mesh **drifted to a new name on every
undo**. The name is now freed before it is claimed. If something else still
holds the old mesh the suffix remains, which is then the correct answer.

#### Verified

`tests/test_repair_backup_lifecycle_blender.py` — **68 checks** in Blender:
one backup after 1, 2, 5 and 18 repairs; *Undo Repair* restoring coordinates,
connectivity, edges, the loop array, UVs, colour attributes and the datablock
name exactly; the restored mesh not being marked as a backup itself, which
would have let a later prune delete the mesh in use; save/reload persistence;
and nine checks that pruning never touches `obj.data`, a source scan, another
target's backup, a similar substring, or a suffixed mesh an object still uses.

Full regression: every suite, **0 failures**, including all nine repair suites
unchanged and green.

#### Unchanged

No solver, load-time handler, readiness policy, preprocessing, topology
analysis, artifact classification, local face repair logic, measurement mesh
content, source mesh, landmark invalidation, Surface Region, Surface Interior,
Surface Area or pygeodesic behaviour. `interior.py`, `surfacearea.py`,
`regions.py`, `interiorcache.py`, `pathcache.py`, `state.py`, `__init__.py`'s
handlers, `repair.py`, `preprocess.py`, `scancopy.py` and all of `geodesic/`
are untouched.

**The 0.29.0 package is superseded for this behaviour.** It installs and
measures correctly and every number it produces stands, but it accumulates a
full mesh copy per repair.

## 0.29.0 — Milestones 3.29 to 3.36

**0.28.0 was an internal test build and never shipped.** It was packaged four
times while this work was in progress, and each of those builds is superseded:
the last of them carries a Surface Region model that has since been replaced
outright. Nothing was released from it, so everything below ships here.

This release is a workflow that did not exist before: **outline a region on a
body scan with landmarks, and measure the area it encloses.**

    landmarks → Compute Boundary → Surface Interior → Surface Area
                                 → Region Fill / Thickness Preview

Eight milestones, described newest-first below.

| Milestone | What it added |
| --- | --- |
| **3.36** | **A boundary running along a mesh edge** — it severs an adjacency instead of cutting a triangle |
| **3.35** | **A real-scan tiling failure fixed** — overlapping clipped pieces, caused by float32 boundary storage |
| **3.34** | **Compute Interior made usable on a real scan** — hours to seconds, result unchanged |
| **3.33** | **Surface Area** on the selected region of the triangular mesh |
| **3.32** | **Surface Interior** (which side is the region), **Region Fill**, **Thickness Preview** |
| **3.31** | **Surface Regions defined by ordered landmarks** — Measurement Manager no longer involved |
| **3.30** | **Local Defect Repair** — removing a few faces inside a component |
| **3.29** | the first Surface Region model, built from measurement paths — **superseded by 3.31** |

#### The shape of the workflow

List landmarks in the order they run round a region; consecutive landmarks —
including the final-to-first pair — are joined by exact geodesic paths on the
mesh. **Compute Boundary** is the only thing in BSMT that runs the geodesic
solver, and it is a button you press. **Compute Interior** then decides which
side of that closed boundary is the region, classifying every triangle as
wholly inside, wholly outside, or **cut by the boundary** — and clipping the
cut ones exactly rather than rounding them to whole faces. **Compute Area**
measures that classification.

Each step is explicit, each goes **stale** when anything it depends on
changes, and none of them recomputes anything on its own.

#### What is NOT claimed

- Surface Area is the **mesh surface area of the selected region on the
  triangular body mesh** — not the true anatomical surface area, not the
  actual human surface area, and not an exact smooth-body area.
- The Surface Interior's **Smaller Side** default is a default, not a claim
  that it is anatomically inside.
- **Thickness Preview is a visualization**, not a physical simulation and not
  a manufacturing model.
- **Everything in this release is verified on synthetic geometry only** —
  planar grids, a closed sphere, a torus, two-component patches. No human
  scan has been measured, and none of the questions that would make these
  numbers anthropometrically meaningful has been asked yet. See
  `docs/VALIDATION.md` §L.7.

### Milestone 3.36 — a boundary that runs along a mesh edge

The re-test of 3.35 on the same ~350,000-triangle scan got past triangle
14527 and refused a different one:

    the boundary's crossing of triangle 6825 could not be split exactly

This is the refusal 3.35 recorded as its known remaining limitation, now met
on real data.

#### What the geometry actually is

An exact (MMP) geodesic has its breakpoints **on mesh edges**. Ordinarily it
enters a triangle through one edge and leaves through another, and the chord
between them cuts the triangle into two pieces. But when the shortest path
between two landmarks happens to *be* a chain of mesh edges — which is common
where a scan has a crease, a seam, or a run of near-coplanar strips — the
"chord" lies flat along one edge of the triangle.

Such a triangle **is not cut**. Asking the clipper to split it asks for a
piece with no interior, which is why it returned nothing and the computation
was refused. Nothing was wrong with the boundary or the mesh.

It was not assumed to be the cause. The split-failure path had no diagnostic
attached — only the *tiling* path did — so the first fix was to give it one,
and it is that record that classified triangle 6825:

    visit 1 lies ALONG mesh edge (16, 17) - such a traversal does not cut
    the triangle, and is handled as an edge-aligned boundary event

#### The fix: the edge becomes the barrier

A boundary has always partitioned the surface by cutting triangles. It can now
also partition it by **severing an adjacency**:

| the boundary … | what it does |
| --- | --- |
| crosses a triangle's interior | clips that triangle into pieces, exactly as before |
| runs along a mesh edge | cuts nothing; the two triangles sharing that edge stop being neighbours |

The uncut triangle keeps its **whole** area and belongs entirely to one side.

Detection is **topological, not a distance threshold.** Every boundary point
is already classified as on a vertex, on an edge, or inside a face. A run is
edge-aligned when *every* one of its points lies on one common mesh edge or at
one of that edge's two ends. A run with even one face-interior point is an
ordinary chord and is still clipped — so a boundary that enters and leaves
through the same edge but bulges into the triangle between is **not**
edge-aligned, and a boundary merely passing close to an edge is not either.
There is no new tolerance, and no existing one was changed.

The search stayed local: alignment is read off the per-point classification
the traversal already produced, so there is **no global triangle × boundary
scan**.

#### Equivalence

Every existing fixture is **numerically identical** to the 3.35 build: same
full-triangle sets, same partial parent ids, same clipped polygon
coordinates, same barycentric coordinates, same areas, same Smaller /
Complement selection, same Surface Area. Exhaustive float comparison across
five fixtures: **0 numeric differences, worst absolute 0.000e+00**.

That is what one would expect: none of those boundaries runs along an edge, so
none of them reaches the new path at all.

#### Diagnostics

A successful run now states how the boundary met the mesh, because the two
ways are not interchangeable:

    [BSMT]   interior boundary: 142 partial triangles / 3 edge-aligned mesh
             edges (3 adjacencies severed) / tiling max relative residual
             7.82e-15

A boundary that follows a crease shows few partials and many aligned edges,
which is the signature of this case and is now visible without instrumenting
anything.

#### Regression

`tests/test_interior.py` §H, 34 new checks:

* a closed loop running **entirely** along mesh edges computes, cuts **no**
  triangle, records 8 aligned edges and 8 severed adjacencies, and encloses
  **exactly** the 4.000000 mm² square it outlines
* the severed edge really does block the traversal — the two triangles that
  shared it are connected in the open graph and not through it in the closed one
* a run ending **at a vertex** resolves to the single edge it ran along, not
  to both edges meeting there
* a chord with an interior point is **not** classified edge-aligned even with
  both ends on one edge
* a loop crossing triangle **interiors** still produces partial triangles and
  **zero** aligned edges — near an edge is not on it
* **drift beyond tolerance is still refused**: 5% and 20% of a triangle are
  rejected, not absorbed. This path recognises a case that was always exact;
  it does not widen what counts as close enough
* both sides still tile the mesh exactly, worst relative residual **0.0**

The decisive one: **switch alignment detection off and you have the previous
implementation**, which refuses the along-edge loop with the scan's own
message. Switch it on and the same loop resolves to the exact area.

#### Performance

Unchanged: 358,800 triangles in **4.17 s** (3.35: 4.19 s), two sides summing
to the mesh area to 2.8e-13.


### Milestone 3.35 — a real-scan tiling failure, and what caused it

On a real ~350,000-triangle human scan Compute Interior finished and then
**correctly refused**:

    the pieces of triangle 14527 do not tile it (3.82042 vs 3.81668)

An **excess** of +9.8e-04 relative — the pieces **overlapped**. The invariant
was not relaxed, the tolerance was not raised and the failing triangle was not
skipped. The partition itself was wrong, and that is what was fixed.

#### The cause

A chord end stored a few microns off the triangle border.

The cached boundary is **float32** display geometry, so a point that
mathematically lies exactly on a shared mesh edge lands slightly to one side
of it. `split_polygon` **located** that end by projecting it onto the border —
and then inserted the **raw** point into both pieces. The two pieces therefore
shared a corner that was not on the border, their union bulged past the
triangle, and the two sides overlapped along the chord.

Reproduced exactly, on a 3.9 mm² triangle at scan scale:

| drift of the chord end | tiling residual |
| --- | --- |
| 0 | 0 |
| 1e-4 mm | +3.8e-05 |
| 1e-3 mm | +3.8e-04 |
| **3e-3 mm** | **+1.2e-03** |

which brackets the +9.8e-04 the scan reported.

#### The fix

`split_polygon` now inserts the **projected** point — the one actually on the
border — into both pieces. The union is then exactly the polygon, and the two
pieces meet along the chord sharing its two ends exactly.

The correction is the drift itself: microns, invisible, and applied
identically by both triangles sharing an edge because both project onto the
same segment. It is **numerical handling of storage precision, not a
simplification of the boundary.**

A second hole was closed with it: the per-chord tolerance used to widen to
twice the best border distance, which accepted a chord end arbitrarily far off
the border and built a polygon around it. It is now the fixed, mesh-scale
tolerance, so **a drift too large to be float32 is still refused** — half a
millimetre off a 3 mm triangle is a real fault, and inventing a projection for
it would hide one.

#### Equivalence

Every existing fixture is **numerically identical**: same full-triangle sets,
same partial parent ids, same clipped polygon coordinates, same areas, same
Smaller/Complement selection, same Surface Area. Exhaustive float comparison
across five fixtures: **0 numeric differences, worst absolute 0.000e+00**. The
only textual change is two barycentric zeros printing as `0.0` rather than
`-0.0`, which compares equal.

#### Diagnostics kept for the re-test

A tiling failure now prints an inspectable record for that one triangle: its
corners and area, how many times the boundary visited it, each chord end in
millimetres **and** barycentric coordinates, whether an end sits on a vertex
or an edge, **how far off the border it is**, each piece's corner count and
area, and the sum against the parent — with excess and deficit named as
overlap and gap.

A **successful** run now reports the invariant rather than staying silent:

    [BSMT] tiling: 140 boundary triangles | max abs residual 3.34e-13 mm^2
           | max relative 7.82e-15 | worst triangle 550

#### Regression

`tests/test_interior.py` §G reproduces the real configuration at the real
scale: drifts from 0 to 1e-2 mm all tile float-exactly; the 3e-3 mm case that
reproduces the scan's magnitude has **no excess**; the two pieces share
exactly the chord's two ends; a 0.5 mm drift is still refused; the report is
inspectable and does not dump the mesh. **Reintroducing the raw-point
insertion makes seven of these fail**, including the one at 1.15e-03 — the
scan's own magnitude.

#### Known remaining refusal — since resolved in 3.36

A chord lying **along** a triangle edge still returns no split and is refused.
Geometrically such a triangle is not cut at all and belongs wholly to one
side, but handling it needs the adjacency and seeding logic to agree that a
"visited" triangle may be uncut. It is recorded rather than quietly handled.

It was seen on a real scan on the next re-test, which is milestone 3.36.

#### Performance

Unchanged: 358,800 triangles in **4.19 s**, two sides summing to the mesh area
to 2.8e-13.


### Milestone 3.34 — Compute Interior on a real scan

Compute Interior passed every synthetic test and was **effectively hung** on a
~350,000-triangle human scan. Profiled before anything was changed.

#### The bottleneck, measured

`locate_point` compared each boundary point against **every edge in the
mesh**, in a Python loop — O(points × edges). Measured on a flat fixture:

| triangles | edges | points | point location |
| --- | --- | --- | --- |
| 800 | 1,240 | 32 | 0.15 s |
| 3,200 | 4,880 | 64 | 1.14 s |
| 12,800 | 19,360 | 128 | 9.13 s |
| 28,800 | 43,440 | 192 | **30.6 s** |

Quadratic in mesh linear size. Extrapolated to 350,000 triangles with a
realistic 2,000–10,000-point boundary: **1.1 to 5.4 hours**. That is the hang.

Two smaller costs sat behind it: `build_graph` computed edge-interval overlaps
for **every edge in the mesh** when only edges touching a cut triangle carry
any information, and the side classification **flooded the whole component
twice**, once per side.

#### What changed — and what did not

**Nothing about the result.** Boundary definition, side classification,
clipping, Smaller/Complement semantics, fill geometry and Surface Area are
untouched, and that is verified rather than asserted: a fingerprint of the
complete output — every full-triangle index, every clipped polygon and
barycentric corner to 12 decimals, every area — is **byte-identical** across
five fixtures before and after (`ed3e4185…`).

1. **A uniform grid over edge bounding boxes** shortlists candidate edges per
   boundary point. The grid only shortlists; the winning edge is still chosen
   by the same exact distance test, widening rings are tried before a full
   scan, and a test asserts the shortlist gives the same answer as scanning
   every edge. It can be faster or, at worst, no faster — never wrong.
2. **The closest-edge search is vectorised** — one numpy pass over the
   candidates instead of per-edge scalar arithmetic.
3. **`edge_map` and the vertex→triangle index are vectorised and built once.**
   `_triangles_at_vertex` used to scan the whole triangle array per query.
4. **Adjacency is CSR arrays over integer node ids**, not a dict of 350,000
   tuple-keyed sets. Whole-triangle-to-whole-triangle links — the
   overwhelming majority — are emitted in one numpy operation; the interval
   overlap runs only on edges touching a cut triangle.
5. **One flood, not two.** The complement is the rest of the component.
   Separation is still *checked*, not assumed: if the first side's flood
   reaches the other seed piece, the boundary did not cut the component in
   two and it is refused exactly as before.

#### Result

| triangles | boundary points | seconds |
| --- | --- | --- |
| 14,160 | 360 | 0.21 |
| 57,120 | 720 | 0.69 |
| 192,720 | 1,320 | 2.14 |
| **358,800** | **1,800** | **4.17** |

Linear, at about 86,000 triangles/second. The two sides sum to the mesh area
to **2.8e-13** relative at full scale.

#### A robustness fix found on the way

The face-location fallback for boundary corners only searched the *previous*
point's triangles and raised if the corner was not in one of them. It now
widens — neighbours, then a grid shortlist, then the whole mesh below a size
limit — so a point it used to place is placed identically and one it used to
refuse gets a fair chance.

#### Reporting

Compute Interior now prints its triangle and boundary-point counts *before*
it starts, and one line of stage timings when it finishes: edge map, index
build, boundary-to-triangles, clipping, adjacency, side traversal, side
description. No per-triangle logging — on a 350,000-triangle scan that would
cost more than the stage it measured.

#### Structural performance tests

Wall-clock is deliberately **not** a pass/fail criterion; it varies by machine
and does not say why anything is slow. Asserted instead: clipping runs once
per cut triangle and not once per triangle; the edge map is built once;
edge-interval work scales with the cut rather than the mesh; one flood, not
one per side; the grid shortlist agrees with a full scan; and no solver call.


### Milestone 3.33 — Surface Area

    ordered landmarks → closed boundary → Surface Interior → SURFACE AREA

**What the number is:** the *mesh surface area of the selected region on the
triangular body mesh*. It is **not** the true anatomical surface area, not
the actual human surface area and not an exact smooth-body area. A
triangulation is a chord approximation of a curved surface, so its area reads
systematically a little under the smooth surface it was sampled from — the
same property that makes a polyhedral geodesic shorter than a smooth one, and
the same wording `docs/VALIDATION.md` §0.3 already uses for distances.

#### It measures the classification that is already drawn

    A_region = Σ area(whole interior triangles)
             + Σ area(exactly-clipped boundary polygons)

Never whole boundary triangles, never a projected polygon, and never a second
interior algorithm. Surface Area does not decide which side is the region,
does not flood fill, does not clip anything and does not re-run any part of
the interior analysis. If the interior is missing, stale or invalid there is
no area — that is a refusal.

#### The units problem, and why barycentric storage solved it

The interior is classified in the scan's **object-local** space, so the areas
it carries are in local units² — not mm², and not convertible by one scale
factor when an object has a non-uniform scale.

Each clipped piece is stored **barycentrically** against its parent triangle,
which makes that a non-problem: the polygon is rebuilt against that triangle's
**physical-millimetre** corners and measured there. No projection, no
rescaling of an already-computed number, and every piece stays tied to the
surface it came from.

#### New: the classification is now persisted in float64

Until now the only thing surviving Compute Interior was the fill helper mesh —
the right faces, but in Blender's **float32** vertex storage and with no link
back to a clipped piece's parent triangle. Enough to draw; not enough to
measure, and not enough to ask "is this piece at most as big as the triangle
it came from".

So `interiorcache` stores the classification itself — whole-triangle indices,
each piece's parent, and its barycentric corners in float64 — in a datablock
with a fake user, the same mechanism `pathcache` uses. One analysis, three
consumers (area, fill, preview), no second algorithm and no copy that can
drift.

#### Two-side consistency: the strongest check available

The selected side plus its complement must equal the surface component's own
area. Nothing that is wrong near the boundary can satisfy it, because whatever
one side gains the other must lose.

| Fixture | Agreement |
| --- | --- |
| planar patch (offline, float64) | exact: 9.000000 + 27.000000 = 36.000000 |
| closed sphere (offline, float64) | **< 1e-09** relative |
| sphere through the real operators | **3.3e-09** relative |
| read back from stored properties | ~1e-07, which is float32 property storage |

Per cut triangle, the two sides' pieces tile it to **1e-12** relative offline.

#### Explicit, and stale when anything under it moves

*Compute Area* is its own press. Nothing computes an area during Compute
Boundary, Compute Interior, a panel draw, Show Fill, Show Preview, a thickness
change, a side switch, or a save.

Landmark change, reorder, re-pick or geometry edit → boundary stale → interior
stale → **area stale**. Thickness, fill colour, fill opacity, preview colour,
preview rebuilds and anything done to measurements → **the area does not
move**, asserted field by field.

**A stale area is never shown as a number.** Fixing that was a real change:
the panel had been echoing the stored result sentence, which contains the
figure, under a "stale" heading. It now shows the reason code instead — a
number on screen is read as a result whatever label sits above it.

#### Units

mm² is the only stored value; cm² is derived at display time at 100 mm²/cm².
The area is computed in **float64** and stored at **float32**, because that is
what a Blender property is — about seven significant digits, ~0.004 mm² on a
400 cm² region, far finer than the mesh's own fidelity to a body.

#### Refused rather than reported

A clipped piece that is negative, or larger than its parent triangle beyond
`1e-5` relative, means the clipping produced a plausible wrong number. The
area is refused with the triangle named. An empty selection is refused too,
rather than reported as 0 mm².

#### Not done, deliberately

- **No CSV integration.** Schema v2 is per-measurement rows with a fixed
  column tuple; region-area rows would either mutate those semantics or need a
  new export surface with its own schema. Deferred and documented, exactly as
  protocol integration for regions already is. **Schema v2 is untouched.**
- **No area in protocol files.** A reusable region definition is ordered
  landmarks; an area belongs to one scan. Asserted by test.
- **No thickness-derived quantity.** Not the offset outer surface, not the
  side wall, not a shell area, not a volume.

#### A name collision worth recording

The module was first written as `area.py`, and BSMT's own cross-module checker
caught `operators.py calls area.tag_redraw()` — `area` is Blender's ubiquitous
name for a UI region (`for area in context.screen.areas`). It runs, because a
local shadows a global, and it is exactly the kind of latent trap that bites
later. Renamed to `surfacearea.py`.

#### Verified on synthetic geometry only

`tests/test_surfacearea.py` (45) and `tests/test_surface_area_blender.py`
(55). Fixtures are planar grids, a closed sphere and a torus. **No human scan
has been measured**, and none of the questions that would make this
anthropometrically meaningful — landmark repeatability, sensitivity to
placement and to scan density, agreement with independent mesh software — has
been asked yet.


### Milestone 3.32 — Surface Interior, Region Fill and Thickness Preview

Milestone 3.31 (below) established a **closed boundary**. This one answers the
question that boundary raises and stops short of the one after it:

    ordered landmarks → cached closed boundary → SURFACE INTERIOR
                      → region fill → THICKNESS PREVIEW
                      → Surface Area [a later milestone]

**No area is calculated.** There is no face-area summation presented as a
result, no coverage percentage, no centroid, no mesh cutting and no
remeshing. What this milestone produces is the classified surface a later one
will measure.

#### The interior is not a point-in-polygon test

Projecting the loop to XY and asking which faces fall inside is wrong on a
body for the same reason it is wrong for self-intersection: a boundary that
wraps a limb encloses nothing in any axis-aligned projection while bounding a
perfectly real patch of skin. BSMT walks the **surface**.

What makes that tractable is a property of the boundary BSMT already
computes. An exact geodesic on a polyhedral surface is piecewise straight and
its breakpoints lie **on triangle edges** — measured on a real computed
boundary at 4e-6 of the bounding-box diagonal, about 1e-8 relative. So a
boundary is not a cloud of samples near the surface; it is an exact sequence
of edge crossings, and between two of them it is a straight chord across one
triangle.

#### Triangles the boundary cuts are clipped exactly

Every triangle is classified as **wholly inside**, **wholly outside**, or
**cut**. A cut triangle is split along the boundary into pieces that tile it
exactly — worst measured error **3.6e-07** of the triangle's own area, which
is the float32 precision the cached boundary is stored at, not slack. A
triangle crossed several times is split by each cut in turn; on a real
four-landmark region of a 2,208-triangle sphere, 128 triangles are cut and
several of those more than once, so this is the normal case rather than an
exotic one.

The two sides together are the whole surface component: **4.3e-11** relative
on that fixture. Nothing is created at the boundary and nothing is lost.

#### Two sides, and neither is called "inside"

A closed loop on a closed surface bounds two regions — a patch and everything
else — and nothing about the geometry says which one a researcher meant. Both
are computed; the choice is **Smaller Side** (the default) or **Complement
Side**. The smaller side is a default, *not* a claim that it is anatomically
inside, and both the enum description and the panel say so. Switching sides
runs no solver.

#### What is refused, rather than guessed

- a boundary that does **not** cut its component in two, which an open surface
  can leave genuinely ambiguous — `INTERIOR_UNDETERMINED_OPEN_SURFACE`;
- a boundary that does not lie on this mesh — `BOUNDARY_OFF_SURFACE`;
- a boundary that crosses itself — see below.

#### An additional triangle-local self-intersection check

Region validation's shared-point test is sound but incomplete by
construction: it cannot see a crossing that happens strictly *between* two
sampled boundary points, and says so. Inside a single triangle the boundary is
straight between samples, so there a crossing is an ordinary segment
intersection and **is** detected. Computing the interior therefore catches
self-intersections the boundary check cannot.

This is an **additional, triangle-local** capability. It is **not** complete or
general on-surface self-intersection detection, and nothing in BSMT claims it
is — the documented limitation on the boundary check stands unchanged.

#### Region Fill: the analysis, drawn

*Show Fill* draws the classified interior: one face per whole interior
triangle, one per exactly-clipped partial piece. Those faces **are** the
representation a later area milestone will sum — not a decorative overlay and
not a projected polygon. A fill that looked right while the analysis said
something else would be the most convincing wrong answer BSMT could give.
Semi-transparent by default (0.30) so landmarks and the scan stay visible.

#### Thickness Preview

*Thickness (mm)*, default 10, then *Show Preview*: the classified interior
offset **outward** along the body surface normals, closed with a side wall so
it reads as a panel. The wall follows the **clipped** polygon edges, so it is
continuous across triangles the boundary cuts — the regression asserts the
shell is closed with 128 clipped triangles on its border.

**It is a visualization, not a physical simulation and not a manufacturing
model**, and the operator prints that every time it runs. A normal offset
gives a perpendicular gap that is exactly the thickness asked for, but on a
curved body the shape distorts: the outer surface stretches over convex areas,
compresses over concave ones, and where the thickness exceeds the local radius
of curvature it folds through itself. The material a real panel would need is
not uniform. Obvious fold-through is reported as a warning — a warning, not a
guarantee.

**Outward is determined, not assumed.** It comes from the signed volume of the
closed surface component. On an **open** component nothing defines which side
is out, and the preview is refused rather than quietly extruded into the body.

An invalid thickness — zero, negative, non-finite, or outside 0.01–500 mm — is
**refused with a reason**, never silently clamped.

#### Caching and the dependency chain

    geometry → landmarks → boundary → interior → thickness preview

Editing landmarks makes the boundary stale, which makes the interior stale,
which invalidates the preview. Nothing is ever recomputed automatically.
Changing the thickness, the fill colour or the opacity rebuilds only the
helper geometry — the interior and the boundary are untouched, which the
regression asserts field by field.

**No solver call** on: Compute Interior, side switching, Show/Hide Fill, Show/
Hide Preview, thickness change, colour change, opacity change, save or load.
Only Compute Boundary reaches pygeodesic, and the counter proves it.

#### Helper geometry only

`BSMT_Region_<id>_Fill` and `BSMT_Region_<id>_Panel`, both tagged helpers in
the helper collection, unselectable, excluded from every diagnostic, following
rigid transforms by matrix. The source scan, the measurement mesh, the
landmarks and the boundary definition are never modified.

#### Verified on synthetic geometry only

`tests/test_interior.py` (64), `tests/test_surface_interior_blender.py` (62)
and `tests/test_thickness_preview_blender.py` (69). The fixtures are planar
grids, a closed sphere, a torus and a two-component patch. **Nothing here has
been validated against a real human scan**, and the thickness preview in
particular will behave worst exactly where a body is most curved.


### Milestone 3.31 — Surface Region, defined by landmarks

Milestone 3.29 (below) shipped a Surface Region as an ordered list of
**measurement path references**, each with a stored orientation. It was
technically sound and practically unusable. Defining a five-sided region meant
creating five measurements, computing five surface paths, then adding,
reordering and reversing five path references — and getting the direction of
each one right by hand. A researcher who wants to outline a patch of skin
should not have to do any of that.

#### The model, replaced

A measurement and a region answer different questions. A measurement asks
*how far is it from A to B*; a region asks *what closed boundary do these
landmarks describe*. Sharing the geodesic backend is right; making one the
authoritative model of the other was not.

The authoritative definition is now **the ordered landmark ids**, and the
segments are derived from them:

    Landmarks → ordered landmark definition → Compute Boundary
              → cached boundary segments → Surface Region
              → Surface Interior [later] → Surface Area [later]

n landmarks always mean exactly n segments, the last of which closes Ln back
to L1. **The closing segment is implicit and mandatory**, so there is no way
to express an open boundary and nothing to validate about one. Orientation
stops being something the researcher manages, because the order they typed is
the orientation.

#### The workflow

*New Region*, *Add Selected* for each landmark, reorder with the arrows,
*Compute Boundary*. The panel spells the loop out the way it reads —
`C08 → B03 → W05 → W08 → C10 → C08` — with the closing landmark shown, so the
list never looks like an open chain. **Measurement Manager is not part of
this.**

#### Compute Boundary is the only thing that solves

Adding, removing, reordering, renaming, validating, showing, hiding, saving
and loading all cost zero solver constructions. One button solves, it says how
many segments and that Blender will not redraw, and it is the only thing a
researcher ever waits for. This is measured, not asserted:
`PyGeodesicAlgorithmExact` is wrapped and counted across eleven `NoSolver`
blocks.

Boundary computation is **transactional**. Every segment is solved into memory
first and nothing is written until all of them have succeeded, so a region can
never end up part fresh and part stale — a drawing that looks authoritative
while describing two different definitions. A failure names the segment that
failed, writes nothing, and leaves the previous cache exactly as it was. The
preflight gate that guards every other route to the native solver guards this
one too; a region is n solves rather than one, so it is the last place to skip
it.

#### Regions own their boundary, and depend only on landmarks

Boundary segments are cached in `pathcache` under the region's own name space,
keyed by region and position — not by any measurement. So:

| Event | Effect on a region |
| --- | --- |
| measurement deleted, renamed or edited | **none at all** |
| every measurement cleared | **none at all** |
| landmark re-picked | STALE |
| landmark deleted | INVALID, reference kept and named |
| landmark list edited or reordered | STALE |
| geometry changed | STALE, through the centralized policy |
| rigid transform | unchanged, cache intact |

Nothing is ever recomputed automatically.

#### Migration: refused by name, never reinterpreted

0.28.0 was never released, so there is no supported file carrying the old
model. A dev file that does — a region with cached segments and no landmark
definition — is reported `INVALID / LEGACY_DEFINITION` with an instruction to
add its boundary landmarks again. It is **not** migrated: turning ordered
measurement references into ordered landmark ids means guessing which end of
each path was meant to come first, and a guess there would silently produce a
different boundary than the one the researcher defined. Old .blend files
without any region data load exactly as before.

#### Fixed on the way

`viz.sync_transforms` returned early when a file had no measurements, which
under the old model was harmless because a region was built from them. With
regions decoupled it would have stranded every boundary at the scan's old
transform in a measurement-free file.

#### Unchanged

Measurement definitions, results, path cache and CSV **schema v2** are
untouched, as are the solver, preflight, readiness, repair, decimation and
topology policy. Protocol export remains deferred (see below), and would now
carry ordered landmark identities rather than measurement references.

#### Minimum three landmarks, and what that costs

Three, not two: two landmarks give A→B and B→A, the same geodesic walked both
ways, which encloses nothing. The old model allowed two because two
*different* paths between one pair — round the front of an arm and round the
back — do bound a lune. A landmark pair cannot express that. It is the one
place this model is less expressive than the one it replaces, and it is
recorded as a known limitation rather than glossed over.


### Milestone 3.30 — Local Defect Repair

#### The gap this closes

Milestone 3.28 refuses to delete the connected component holding an inspected
defect when that component is the primary body. On the current real scan that
refusal is exactly right — Component 1 holds ~92% of the mesh — and it is
kept, unchanged, word for word. But it left the scan with **no repair at
all** for its single blocking defect: one non-manifold edge, two vertices,
about 0.93 mm across, on the body itself.

**Local Defect Repair** is the second strategy. It removes a handful of
*faces* from inside a component, where Delete Artifact removes a whole
component and Weld Non-Manifold Region merges vertices.

#### The structural test, and why size is not the criterion

Treat the focused defect's own non-manifold edges as a **wall** and walk the
surface outward from the faces incident to them. What comes back is the local
branches the defect separates. A branch that runs past the inspection cap is
a *continuation* — the walk stopped, the surface did not — and is the body.
A branch that closes is a local patch.

A removal is offered when, and only when, **exactly one** local patch stands
against a continuing body surface. Everything else is refused by name:

| Local topology | Outcome |
| --- | --- |
| one small branch, one continuing surface, no vertex shared with the survivor | `SMALL_DANGLING_FLAP` — removable |
| one small branch, one continuing surface, every vertex shared | `SMALL_LOCAL_BRANCH` — removable |
| the incident faces repeat a triangle | `LOCAL_DUPLICATE_FACE` — handed to *Remove Duplicate Faces* |
| two or more candidate branches | `AMBIGUOUS` — refused |
| two comparable branches, neither dominant | `AMBIGUOUS` — refused |
| every branch keeps going | `AMBIGUOUS` — refused |
| candidate above a safety cap | `AMBIGUOUS` — refused, cap named |
| removing it would not reduce the non-manifold count, or would create a new one | `AMBIGUOUS` — refused |

**A false refusal is acceptable; a false-positive destructive repair is not.**

#### Two continuations are one body

The first working version called a defect ambiguous whenever the walk
returned two unbounded branches — and that is what a bounded walk *always*
returns on a real body scan, because the two sides of a defect edge reconnect
only by going right round the torso, thousands of faces away. Pooling every
capped branch as "the surface keeps going" is what makes the feature work on
the scan it was written for rather than only on test fixtures.

#### The caps are software caps, and they are on screen

A 512-face inspection limit, a 64-face candidate limit, a body branch at
least 8× the candidate, and the existing adaptive `region_diagonal_limit`.
None is an anatomical claim; all four are stated in the panel and in the
confirmation dialog, so the rule that was applied is visible rather than
implied. Size is *evidence shown alongside* the structural test, never the
test itself.

#### Duplicate faces are delegated, not reimplemented

`repair.duplicate_faces` already finds exact and reversed-winding duplicates
and `Remove Duplicate Faces` already removes exactly the repeated copy. When
the defect is that case, the inspection classifies it and points at the
existing repair. There is no second duplicate-face implementation.

#### What a successful removal has to prove

The edit runs inside the existing `_RepairBase` transaction, and the
authoritative `repair.accept_repair` is asked first and in full. On top of
that it must hold that:

- the surviving surface is **bit-identical** to the surface before, minus the
  approved candidate and nothing else — checked by a position-keyed face
  signature, because deleting faces renumbers every index in the mesh;
- the focused defect's own non-manifold edges are gone, by midpoint position;
- no non-manifold edge exists anywhere that was not there before;
- no degenerate triangle appeared;
- exactly the approved faces went, and exactly the vertices they stranded.

Anything else, and the mesh is restored from its own backup — no dependence
on Blender's undo stack. Boundary edges may rise, because removing a flap
exposes the boundary it was covering; that is **reported**, not refused, and
`connected_components == 1` is not required.

#### New UI

Inside *Focused Defect*, below the component deletion block:

    Local Defect Repair
      [Inspect Local Topology]
      Classification / Candidate faces, vertices, area, bounding box,
      maximum reach, predicted non-manifold change, safety caps
      [Preview Candidate Faces]  [x]
      [Remove Local Artifact Faces]

The preview highlights **only the candidate faces**, in a colour of their
own, so it can never be mistaken for the whole-component preview. No preview
edits the mesh, none runs the solver, and a preview whose geometry hash no
longer matches is refused rather than redrawn.

#### Index stability

The stored inspection is a display cache with no authority. Stepping to
another defect, re-analysing, or any geometry change clears it, and the
removal re-derives the candidate from the live canonical mesh inside the
transaction before touching anything. Faces are addressed by **vertex set**,
never by polygon index.

#### Nothing else changed

Weld Non-Manifold Region, Delete Artifact and Remove Duplicate Faces behave
exactly as before, and the primary-component deletion block is untouched. No
operation on this path calls pygeodesic.

#### Tests

- `tests/test_localrepair.py` — 136 checks, pure numpy, no Blender.
- `tests/test_local_face_repair_blender.py` — 117 checks in Blender on a
  1.7 m body-scale, rotated, translated measurement copy, including four
  injected-failure rollbacks (over-reaching deletion, emptied mesh, moved
  unrelated geometry, manufactured degenerate triangle).

### Milestone 3.29 — Surface Region (superseded by 3.31 above)

A **Surface Region** is a researcher-defined **closed boundary** on the
measurement mesh, assembled from ordered surface paths that have already been
computed.

    Landmark → Surface Path → Surface Region → Surface Area [a later milestone]

#### This milestone stops at the boundary

There is **no area** anywhere in this change: no face-area summation, no
interior flood fill, no triangle clipping, no projected area, no coverage
percentage, no contact area, no mesh cutting, no remeshing. Nothing modifies
the source scan or the measurement mesh.

That order is deliberate. An area computed from a boundary nobody proved
closed is a plausible wrong number, so the boundary — and the proof that it
closes — comes first.

#### Why the boundary is made of paths

Joining the corner landmarks with straight 3D segments would be wrong for the
same reason a straight distance is not a surface distance: a chord cuts
*through* the body and encloses something that is not a patch of skin. A
region is therefore built from the exact geodesic polylines BSMT already
caches, and from nothing else.

#### New: Surface Regions, a workflow stage

Between **Measurement Visualization** and **Results and Export**:

- create, name and delete regions;
- add the selected measurement's path to the boundary, forwards or reversed;
- reorder, reverse and remove boundary paths — order is part of the
  definition, and reversing stores `(path, forward=False)` rather than
  creating a duplicate path;
- **Validate Region**, which reports one of DRAFT / NOT_READY / VALID / STALE
  / INVALID with a reason code and a sentence, and points at the segment at
  fault;
- **Show / Hide / Refresh / Clear** the boundary, drawn as one cyclic curve
  from the cached polylines, in the region's own colour.

A region BSMT cannot vouch for is still drawn — seeing where a broken
boundary runs is how you work out what to fix — but in a **warning colour**,
and the operator says so. It must never look like one that is trusted.

#### No region operation runs the solver

A surface path costs tens of seconds to minutes on a real scan. Renaming a
boundary must cost none of it, so validating, showing, hiding, refreshing,
reordering, reversing, renaming, adding, removing and deleting all read
properties and `pathcache` and stop there.

This is **measured, not asserted**: `PyGeodesicAlgorithmExact` — the only door
to the native solver — is wrapped and its constructions counted, and eleven
blocks of the regression complete with the counter unchanged.

#### Dependencies propagate, and nothing is rebuilt

Region status is *derived*, not trusted from storage, which makes propagation
correct by construction. A region is restated when a landmark is re-picked or
deleted, when a path is deleted or goes stale, and when the geometry changes —
through the existing centralized `invalidate_for_geometry_change`, which now
also reports `regions_restated`. Nothing is reprojected onto changed geometry
and no path is ever substituted. A **rigid transform does not** disturb a
region, matching the geometry-hash policy every other stored result follows.

Deleting a region deletes the boundary definition and nothing else — every
landmark, measurement and cached path survives.

#### Self-intersection: sound, incomplete, and labelled

No projected-polygon test is used. A boundary that wraps a limb
self-intersects in every axis-aligned projection while being perfectly simple
on the surface, so that test would be a heuristic dressed as a result.

**Detected** (every report is a real self-touch): a path used twice; a corner
the loop reaches more than once; two non-adjacent paths that share a point,
shortlisted by a spatial grid and confirmed by an actual distance.
**Not detected:** a crossing strictly between two sampled points of a path —
that needs per-point triangle indices the path cache does not store. The limit
is stated in the code, carried in every validation result, and printed by the
operator.

#### A verdict says what it did *not* check

A `VALID` from a panel redraw and a `VALID` from **Validate Region** are not
the same claim: the redraw restates a region from its properties and its
paths' cache state, while only the explicit press loads every cached polyline
and runs the shared-point test. Printing the same word for both would let the
cheaper check be read as the stronger one.

Every result now carries `touch_checked`; every report ends with a **scope**
line naming the limitation, and saying outright when the shared-point test was
not run; and the panel shows *"Not checked for self-intersection — this
verdict comes from a redraw"* until Validate has been run against the region
**as it now stands**. Nothing anywhere states that a boundary is *simple* or
*non-self-intersecting* — a closed boundary is reported as closed.

That "has been validated" flag is derived rather than remembered: every
refresh recomputes a fingerprint of the ordered path references, their
orientation and the verdict, and any difference clears both the flag and the
stored report. Reordering a boundary withdraws the claim at once instead of
leaving a validated-looking region that no longer matches what was validated.

#### Fixed: the panel could not be drawn in the real UI

The Surface Regions panel called the *storing* `state.refresh_region_status`
from `Panel.draw()`. Blender forbids writing to ID-backed data while the
interface is drawing, so opening the sidebar produced

    AttributeError: Writing to ID classes in this context is not allowed:
    Scene, Scene datablock, error setting BSMT_SurfaceRegion.status

and the panel was replaced by that message. Headless Blender never enters a
real draw callback, so every suite had been drawing the panel happily since
the milestone landed and none of them could see it.

Deriving and recording are now two acts with two callers.
`state.validate_region` derives and writes nothing — that is what the panel
uses. `state.store_region_status` writes. `state.refresh_region_status` is
both together and is reachable only from operators and invalidation paths.
The validation rules, their order, the statuses and the reason codes are all
unchanged; only the question of *who may write the answer down* moved.

Because a draw no longer writes, the panel shows the live verdict while the
stored one is whatever an operator last recorded. When they disagree the panel
shows the live verdict **and says so**, rather than silently correcting the
record — which is the one thing it is not allowed to do.

`tests/test_region_panel_draw_blender.py` (33 checks) guards this without
relying on Blender to raise, since headless Blender will not: it snapshots
every persisted region field across a draw, plants a wrong-but-legal sentinel
verdict that a re-deriving draw would quietly correct, and replaces all three
status writers with tripwires that raise. Putting the original one-line call
back makes five of its checks fail.

#### Each boundary path is named by its endpoints

The list row has space for a path's name and its orientation; which two
landmarks the selected one runs between is what you need while reordering a
loop, so the panel shows it under the list — derived live from the landmarks,
not cached onto the segment where a rename would quietly make it wrong.

#### Deferred, deliberately

Region definitions are **not** written to protocol files or CSV yet. They
belong there and the data model is built for it, but `loads_protocol`'s
signature is consumed by three call sites and a 53-check round-trip suite, and
the Surface Area milestone will want to extend the same record. The protocol
format and **CSV schema v2 are untouched** — nothing was half-implemented. See
`PROJECT_SPEC.md` §11ag.7.

#### Unchanged

No solver, preflight, readiness policy, repair, decimation, topology,
measurement or path behaviour changed. Panel `bl_order` values were renumbered
(Export 80 → 90) to keep the documented ten-apart spacing that reserves room
for a future stage; the visible order is unchanged apart from the new panel.

#### Verified

`tests/test_regions.py` — **116 checks** offline, the rules without Blender.
`tests/test_surface_region_blender.py` — **113 checks** in Blender on a scan
with four genuinely solved geodesic paths, including save/reload, an empty
file, and the thirteen no-solver blocks. Among them: a drawn boundary is a
curve helper and `state.measurement_target` never returns it, even when it is
the active object, so a curve made of measurement results can never become
something the tool measures.


## 0.27.0 — Milestone 3.28, delete the artifact you inspected

Real human scans carry small detached fragments — a shard of floor, a scrap of
turntable, a sliver off a shoulder — and one of them is frequently what holds
the non-manifold edge that blocks exact measurement. The existing
**Show Edges / Focus** pair let a researcher find that defect and look at it,
and then offered nothing to do about it except a weld that correctly refuses
and rolls back. The Connected Components list could delete a component, but
nothing connected the defect you were looking at to the component it lived in.

### What is new

A **focused defect** in the Non-Manifold Edges section: the non-manifold edges
are grouped into defects (edges sharing a vertex are one defect — the same
rule `repair.non_manifold_regions` has always used, now in one shared
function), you step between them, and each one states the connected component
it sits in and whether that component may be deleted.

Then two buttons, beside Show Edges / Focus / Clear Highlight:

- **Preview Artifact** — draws the *whole* component the focused defect
  belongs to, as a solid coloured copy of its own triangles at its own
  position, and states its vertex, edge and triangle counts, its bounding box
  in millimetres, its share of the mesh, whether it is the main component and
  whether it holds the highlighted edge. Read-only.
- **Delete Artifact** — deletes that entire connected component from the
  **measurement mesh only**, after a confirmation dialog that says all of the
  above plus that the source scan will not be modified and that landmarks,
  measurements and cached paths may go stale.

### What it refuses

**The primary body component is never deleted.** "Primary" is the component
holding the most triangles — the same ranking `component_labels` and the
Connected Components list already use, not a second definition, and not object
count, because one Blender object routinely holds fifteen components. When the
focused defect turns out to be on the body, the destructive button is not
drawn at all; the panel says so, and a scripted call is refused with:

> The focused defect belongs to the primary body component. Automatic
> component deletion is not permitted. Use another repair method or inspect
> manually.

A single-component mesh is refused the same way. A **tie** — two components
with the same triangle count — is refused as ambiguous rather than broken:
a coin-flip between two candidate bodies is not a repair.

**BSMT does not decide whether geometry is anatomically irrelevant.** Nothing
on this path infers it, and being small is not evidence of being unwanted:
hair, a garment, a held object and an artefact all look the same to a triangle
count. Deleting is a researcher's decision, made by looking at the thing,
which is why the workflow is Show Edges → Focus → Preview → confirm and why
nothing runs without a press.

### Transactional, on the existing rule

The mesh datablock is backed up before the edit. Afterwards the canonical
diagnostics are rebuilt and `repair.accept_repair` — the existing
authoritative acceptance rule, in strict mode — decides, with four additions
specific to removing a whole component:

- no degenerate triangle may appear that was not there before;
- exactly one component may disappear;
- the mesh must shrink by exactly the triangles that component held;
- the primary component must come through with its triangle count unchanged.

Anything else restores the mesh. **A partial improvement is a success**:
non-manifold 2 → 1 is kept, because requiring every defect to vanish in one
press would make the operation impossible on precisely the scans that need it.

### Other changes on this path

- `_RepairBase._guarded` now re-analyses the edited mesh inside a guard. An
  exception there previously escaped the operator and left the edit in place
  with nothing having vouched for it; it now restores the mesh and says so.
  This applies to every repair that uses the wrapper, and it is the path that
  a deletion emptying the mesh takes.
- After a successful deletion the repair highlights are cleared — they were
  built from indices of a mesh that no longer exists — and
  `state.invalidate_for_geometry_change`, the centralized policy, restates
  every landmark and measurement on that mesh. **Nothing is re-projected.**
- `repair.non_manifold_regions` now calls a shared
  `repair.group_nonmanifold_edges`; behaviour, region ids and ordering are
  unchanged.

### Unchanged

No readiness rule, solver gate, degenerate-triangle policy, decimation or
topology analysis changed. Multiple connected components are still a
*preference*, never a blocker, and nothing here introduces a rule requiring
one component. There is still no global merge-by-distance, loose-geometry
sweep, remesh, hole-filling or smoothing anywhere in BSMT.

### Verified

`tests/test_artifact.py` — **88 checks** offline, the policy without Blender:
the component a defect sits in, the primary/only/tie refusals with their exact
messages, and that acceptance never accepts what `repair.accept_repair`
refused.

`tests/test_artifact_deletion_blender.py` — **149 checks** in Blender, on a
1.7 m body-scale measurement mesh with a real source object, rotated and
translated: the fragment goes and the body's triangle count is unchanged to
the triangle; the source scan is untouched; a defect on the body is refused
with the message the panel shows; 2 → 1 is accepted and 1 → 0 after it;
injected edits that over-reach, that empty the mesh and that manufacture a
degenerate triangle are each rolled back vertex-for-vertex; highlights never
reach the diagnostics or the edit; landmarks and measurements go stale without
being re-projected; and a stored analysis is refused once the geometry hash
has moved.


## 0.26.2 — Milestone 3.27, a defect highlight you can see

Reported: on a measurement mesh whose diagnostics report exactly one
non-manifold edge — repair blocked, Weld Non-Manifold Region correctly
attempting and rolling back at 1 → 1 — pressing **Show Edges** produced no
visible highlight in the viewport.

### What was measured before anything was changed

Audited on a body-scale measurement mesh (1.7 m tall in millimetre
coordinates, rotated and translated like an aligned scan) carrying exactly one
non-manifold edge:

| Checked | Result |
|---|---|
| Show Edges finds the same edge diagnostics counted | **yes**, 1 = 1 |
| Helper geometry created | **yes** |
| Linked to `BSMT_Helpers`, collection in the scene | **yes** |
| Layer collection excluded or hidden | **no**, neither |
| Object hidden, in viewport or render | **no** |
| Placed in world coordinates, with the object's transform | **yes**, within 1.5e-5 mm of the true edge |
| Occluded by the surface it lies on | **no** — `show_in_front` was already set |

Everything the existing suite could have asserted was already true. The
highlight was drawn. It just could not be seen.

### Root cause: a colourless hairline

The highlight was an **edge-only mesh with no material**, displayed as `WIRE`:

- **No colour.** Blender draws a wire object in the theme's wire colour
  (near-black). `obj.color` — the alarming magenta-red this add-on sets — is
  only consulted when the viewport's *wireframe* colour is switched to
  Object, which is not the default and not something a researcher has any
  reason to change. Verified by forcing the viewport's colour mode and
  watching the line stay dark.
- **No thickness.** A wire is one pixel wide at any zoom.
- **No scale awareness.** What it drew was the mesh's own edge: about 5 mm
  long on a 350,000-triangle body scan, 0.3% of the subject's height.

A dark hairline, a few pixels long, over a grey body. The degenerate-triangle
stage had already met and solved this problem — it marks defects with crosses
sized against the scan's bounding box — but the non-manifold and boundary
highlights never got the same treatment.

### The fix — visualization only

- **Highlights are drawn as solid rods**, six-sided, along each defect edge,
  carrying a **material** in the defect's colour. A material is what makes
  Solid shading paint it at all; it is the same mechanism the landmark
  markers have always used.
- **The rod's radius is 0.4% of the scan's own bounding-box diagonal**
  (floored at 0.5 mm) — about 7 mm on a 1.7 m body, roughly three times the
  diameter of a landmark marker. **Only the thickness is exaggerated.** The
  rod's endpoints are the defect's own, so a highlight never misstates where
  a defect is or how far it runs.
- **Sizes now convert out of millimetres properly.** They are decided in
  millimetres against the bounding box, then divided by the unit multiplier
  *and* the object's scale to reach the local coordinates the helper mesh is
  built in. The old marker size was correct only for a scan stored in
  millimetres at scale 1; anything else was off by that factor.
- **A Focus button**, next to Show Edges. The highlight is honest about its
  size, which means a 5 mm defect on a 1.7 m body is still something you have
  to be looking at the right part of the scan to see. Focus frames every
  non-manifold edge in one view — the same explicit control the
  degenerate-triangle stage already has. It is deliberately **not** something
  Show Edges does on its own: BSMT does not move a researcher's view as a
  side effect of being asked to show something.
- The boundary-loop and degenerate-triangle highlights share the same drawing
  code and are fixed with it: blue rods and amber crosses that are now
  actually blue and amber.

### What did not change

No repair policy, no readiness verdict, no solver gate, no topology analysis.
The highlight still touches nothing: the measurement mesh's vertex and face
counts, and its topology verdict, are asserted unchanged across a highlight.
Weld Non-Manifold Region still rolls back when the count does not improve.

### Verified

`tests/test_repair_highlight_blender.py` (new): 38 checks in Blender, on a
rotated, translated, body-scale measurement mesh with exactly one known
non-manifold edge — the highlight marks the same edge diagnostics counted, at
its true world position, reaching both endpoints without overstating the
extent; it has faces, a material and the right colour; its thickness is
millimetres and proportionate to the scan; it is in front, in the helper
collection, unselectable; the mesh and its verdict are untouched; Clear
Highlight removes it; the same holds at a different object scale; Focus never
moves the scan; and a clean mesh gets no highlight and a refused Focus. On the
0.26.1 code the suite fails at the first check of what reaches the screen.

Highlight build cost, measured: 100 edges 2 ms, 2,000 edges 34 ms, 20,000
edges 357 ms. It is a button press, not a redraw.

## 0.26.1 — Milestone 3.26, a panel that cannot render nothing

Reported: on a real source scan (M02, 534,732 vertices / 1,069,448 triangles,
3 components, 7 boundary edges, 3 non-manifold edges, verdict **NOT READY**),
the Scan Preprocessing panel was expanded and its body was completely empty —
no controls, and no sentence saying why. Scan Setup and Mesh Repair were
drawing normally, so the workflow had no route from a source scan to a
measurement mesh.

### What was measured before anything was changed

The reported scan was imported and analysed. BSMT reproduces the report's
numbers exactly — 534,732 / 1,069,448, 3 components, 7 boundary edges, 3
non-manifold edges, 0 degenerate triangles, 0 coincident vertices, `NOT
READY` — and in that state, on Blender 4.5.13, **the panel drew its full
body**: scan facts, topology, Analyze Scan, appearance, the density warning,
the preset, the target, Create Measurement Mesh, the compare row and the
Solver Safety Gate. The same was true of the installed extension, of the
source tree, in and out of Edit Mode, with the object hidden, with a modifier
on it, with a shared mesh, and against every stored preprocessing state. The
exact blank body could not be produced from the reported inputs.

What the audit did establish is how it is reachable, and that it was reachable
by construction rather than by any one input:

- **Everything the panel shows is read from the scene before its first widget
  is emitted.** `describe()`, the stage hint and the creation policy all run
  ahead of the first `label()`.
- **Blender renders what a draw() emitted before it raised** — measured with a
  panel made to fail on purpose: three widgets drawn, then an exception, and
  the three widgets are on screen. So a fault in that leading read phase
  renders as *precisely* the reported symptom: an open disclosure arrow above
  nothing.
- **The panel had no failure path at all.** Any exception in that phase — a
  Blender API change, an unusual datablock, undrawable text — produced a blank
  body and nothing else. The reported scan is a live example of the last of
  those: its `.mtl` names the texture in a legacy Korean codepage, and the
  image path reaches Python holding an unpaired surrogate, which
  `layout.label()` cannot encode.

### The fix

- **An expanded Scan Preprocessing panel can no longer render an unexplained
  blank body.** The body is built inside a guard; if it fails, the panel says
  which panel failed, names the exception, points at the system console for
  the traceback (printed once per distinct failure, not once per redraw), and
  keeps Create Measurement Mesh so a diagnostic fault cannot block the
  workflow.
- **Text that Blender cannot draw is replaced rather than raised.** Object,
  mesh, UV, colour, material and image names now pass through `_safe_text`,
  which is what stops a scan-derived name from being able to blank a panel.
- **`scancopy.creation_block` is now the one place that decides whether a
  measurement mesh may be created**, and the operator's `poll()` asks it. The
  conditions and their order are unchanged. What is *not* on that list is the
  point: non-manifold edges, boundary edges, several components, degenerate
  triangles, coincident vertices and density never block preprocessing and
  never remove its controls — this stage is what produces the mesh that can
  become ready.
- **The panel prints the reason.** Where creation is unavailable it says which
  object and why ("'Camera' is a camera, not a mesh", "'M02_BSMT' is already a
  measurement mesh"), with the remedy, instead of a greyed button.
- **The panel stopped costing 94 ms a redraw.** Auditing the draw path
  measured it: `scancopy.used_material_names` read one Python integer per
  face through `polygons.foreach_get`, which on the reported 1,069,448-face
  scan cost **88 ms every time the panel redrew** — enough to make the whole
  sidebar stutter while the pointer is over it. The same values read from the
  mesh's own `material_index` attribute cost **0.1 ms**, and the panel's
  fact-gathering as a whole went from 94 ms to 6.5 ms. Identical answer; the
  polygon path is kept as the fallback.
- **The generated measurement mesh becomes the active object.** Repair,
  alignment, landmarks and measurement all act on the active object, and
  leaving the source active left the researcher in front of a Mesh Repair
  panel correctly saying the selected mesh is not a measurement mesh, with
  nothing naming the object to select instead. Selection state only.

### What did not change

No solver preflight, no readiness policy, no repair, no decimation, no
topology analysis. Preprocessing still repairs nothing, and
`preprocess.preflight` still refuses exact measurement on a non-manifold mesh
— verified on the reported scan after preprocessing: the 350,000-triangle
measurement mesh keeps all 3 non-manifold edges, and the gate keeps refusing
it.

### Verified

`tests/test_preprocess_panel_blender.py` (new): 71 checks in Blender. A READY
source shows the controls; a NOT READY source with non-manifold edges,
boundary edges and three components shows the *same* controls and polls True,
while the solver gate still refuses that mesh; an empty scene and a camera
each get a named explanation; an existing measurement mesh shows its own
state and why another copy would be refused; and, in the check that would
have caught the report, the panel's fact-gathering is made to raise and the
body must still name the failure and keep the forward action. The material
audit keeps its exact answer on a three-slot mesh with an unused slot, a
single-slot mesh with no explicit indices, and a mesh with no material.

Real-scan acceptance on M02: source → Standard 350k → 350,000-triangle
measurement mesh with its UV map, material and image texture preserved,
source geometry byte-for-byte unchanged, generated mesh active and selected,
topology analysed, still NOT READY for the 3 non-manifold edges, Mesh Repair
now accepting it.

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
