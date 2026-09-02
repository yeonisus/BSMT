# BSMT — Body Surface Measurement Tool
## Project Specification

**Document version:** 0.2
**Date:** 2026-09-01
**Target environment:** Blender 4.5.13, macOS (Apple Silicon), bundled Python 3.11
**Status:** Phase 1 complete and validated on a real human-body scan. Phase 2 designed, not implemented.

> Note on this document's history: no `PROJECT_SPEC.md` existed in the project before this
> revision. Phase 1 was specified conversationally and implemented from that specification.
> This file is now the authoritative spec and supersedes the earlier Phase 2 design draft.

---

## 1. Purpose and scope

BSMT is a research-grade Blender add-on for measuring distances between anatomical landmarks
on textured OBJ human-body scans.

| Phase | Content | Status |
|---|---|---|
| 1 | Straight-line (Euclidean) distance between two ray-cast surface points | **Done** (v0.2.0) |
| 2 | Surface (geodesic) distance between the same two points | **This document** |
| 3+ | Preprocessing, landmark templates, automatic landmark detection, export, batch measurement | Not designed |
| Future | Anatomical scan alignment (§13) | Requirement recorded, not designed |

Non-goals for Phase 2, explicitly: automatic landmark detection, mesh repair as a measurement
step, cropping tools, measurement templates, export format changes, and any approximate
production distance backend.

---

## 2. Guiding principles

1. **The scan is read-only.** No BSMT operation modifies the source object's mesh data,
   transform, materials, or files. All computation happens on derived, non-destructive copies.
2. **No silent approximation.** A number displayed as a research measurement must come from the
   designated research backend. If it cannot, BSMT reports a failure state — never a substitute
   number of lower quality.
3. **Every measurement carries its provenance.** Backend, backend version, preprocessing
   settings, mesh hash and BSMT algorithm version travel with the result.
4. **No silent topology changes.** BSMT never automatically connects surfaces that are
   topologically separate, and never crosses gaps or disconnected components.
5. **Diagnose before repairing.** Report what the mesh is; let the operator decide what to change.

---

## 3. Phase 1 — as built (reference)

Package `body_surface_measurement/`, add-on version 0.2.0, legacy `bl_info` add-on format.

| File | Responsibility |
|---|---|
| `__init__.py` | `bl_info`, module registration order, reload support |
| `state.py` | `BSMT_Properties` PropertyGroup on `Scene` (unit, marker/line display, A/B validity + world XYZ, distance) |
| `measurement.py` | Unit table, `unit_multiplier`, `mm_to_units`, Euclidean distance, mm formatting. No bpy dependency. |
| `picking.py` | Viewport ray construction, `scene.ray_cast`, helper-object skipping |
| `visualization.py` | Marker spheres (unit sphere + object scale), bevelled poly-curve line, helper collection, tag-gated deletion |
| `operators.py` | `bsmt.pick_point` (modal), `bsmt.calculate_distance`, `bsmt.clear_points` |
| `panels.py` | View3D sidebar `BSMT > Body Measurement` |

Phase 1 behaviour that Phase 2 must preserve unchanged: modal picking, marker display, the
straight-line measurement and its display, the display controls, and `Clear Points` safety.

Phase 1 limitations Phase 2 addresses: points are stored as world XYZ only (no surface
identity, so they do not follow the object and cannot index a mesh), and the picked polygon
index returned by `scene.ray_cast` is discarded.

---

## 4. Phase 2 — terminology and error model

This section defines the language used in the Methods section of any publication using BSMT.

### 4.1 Error decomposition

For two landmarks A and B:

- `d_true` — geodesic distance on the actual body surface. Unobservable.
- `d_poly(M)` — the exact shortest path length on a given triangulated polyhedral mesh `M`.
- `d_alg(M)` — what a given algorithm returns on `M`.

```
total error  =  [ d_alg(M) − d_poly(M) ]   +   [ d_poly(M) − d_true ]
                 algorithmic error              representation (discretisation) error
```

### 4.2 What the MMP backend does and does not guarantee

**Does:** the MMP (Mitchell–Mount–Papadimitriou) exact polyhedral geodesic algorithm makes the
*algorithmic error term essentially zero* — it returns the true shortest path on the given
triangulated polyhedral surface, up to floating-point precision. In particular it eliminates the
metrication bias of edge-graph (Dijkstra-type) methods, whose algorithmic error is positive,
anisotropic, and **does not vanish under mesh refinement**.

**Does not:** it does **not** make the measurement invariant to triangulation or remeshing.
A different triangulation of the same scan is a *different polyhedral surface* with slightly
different intrinsic geometry, so `d_poly(M1) ≠ d_poly(M2)` in general. The exact algorithm is
exact *with respect to the mesh it is given*, not with respect to the body.

**Therefore:** the representation error term is a genuine, irreducible component of measurement
uncertainty. It must be **measured and reported**, not assumed to be zero.

### 4.3 Required reporting language

Acceptable:

> "Surface distances were computed as exact geodesic paths on the triangulated polyhedral scan
> surface using the MMP algorithm (pygeodesic X.Y.Z), which introduces no algorithmic
> approximation beyond floating-point precision on the given mesh. Sensitivity of the reported
> distances to mesh resolution and triangulation was quantified separately (§9.4) and is
> reported as ±… mm."

Not acceptable: "exact geodesic distance on the body surface"; "triangulation-independent";
"invariant to remeshing"; "zero error".

### 4.4 Expected convergence behaviour (to be measured, not assumed)

For meshes sampled from a smooth surface, `d_poly(M)` is expected to approach `d_true` as edge
length `h` decreases; for well-sampled inscribed meshes an empirical rate near O(h²) is commonly
observed for *distances*. BSMT validation shall **measure** the observed rate rather than assert
an order. Note also that convergence of geodesic *distances* does not imply pointwise
convergence of the geodesic *paths*; path-level claims require separate justification.

The discriminating validation result between method families is therefore not "exact is
invariant" but:

> as `h → 0`, the spread of exact-geodesic results across triangulations shrinks, while the
> edge-graph result converges to a non-zero positive bias.

---

## 5. Phase 2 — backend policy

### 5.1 Primary research backend

**pygeodesic (MMP exact polyhedral geodesic).** The reported BSMT surface distance comes from
this backend whenever it is available. It returns both the distance and the path polyline, which
BSMT uses directly for visualization.

Verified externally on 2026-09-01: `pygeodesic` 0.1.11 publishes cp311 wheels for
macOS arm64 and x86_64, manylinux x86_64 and win_amd64. Blender 4.5's bundled Python version
must be confirmed as 3.11 in-session before relying on this (Milestone 2.2).

**Measured behaviour (2026-09-01, `tools/check_geodesic_env.py`).** Run in a throwaway venv on
macOS arm64 with **Python 3.12 / numpy 2.5.2 — NOT Blender's interpreter**, so it validates the
library and the test procedure, not the target environment. Milestone 2.2 is satisfied only when
the same script passes inside Blender's own Python.

| Test | Result |
|---|---|
| Planar mesh, 800 triangles | relative error **0.000e+00** — exact, as an exact polyhedral method must be |
| Icosphere R=100 mm, h=29.8 mm | 310.632 vs analytic 314.159, rel err 1.12e-02 |
| h=15.0 mm | rel err 2.90e-03, error shrank 3.9x — observed order **1.97** |
| h=7.5 mm | rel err 7.31e-04, error shrank 4.0x — observed order **1.99** |

This is the §4.4 prediction confirmed by measurement rather than assumed: the algorithmic error
term is zero (the plane result), and what remains is discretisation error converging at
**~O(h²)**, underestimating because chords cut corners. The observed order must still be
re-measured on the full analytic suite of §9.4; it is not to be quoted from this table as a
general claim.

**API surface, from runtime introspection (do not assume beyond this):**

```
pygeodesic.geodesic.PyGeodesicAlgorithmExact(points, faces)
    .geodesicDistance(sourceIndex, targetIndex)  -> (float, ndarray path (n,3))
    .geodesicDistances(source_indices[, target_indices]) -> (distances, best_source)
```

- Endpoints are **vertex indices only**. There is no face+barycentric entry point, which is
  precisely why Milestone 2.1's `insert_points()` exists: A and B are inserted as real vertices
  of a scratch mesh, and the exact method is then exact for arbitrary in-face locations.
- `geodesicDistance` returns the **path polyline** directly, which Milestone 2.4 will draw.
- `target_indices` is optional, so one-to-all works — useful later for landmark fields.
- Faces are accepted as int32 or int64 and vertices as float32 or float64. BSMT will pass float64
  vertices and int32 faces explicitly rather than rely on coercion.

**Performance concern to carry into Milestone 2.3.** One query on an 81,920-triangle icosphere
took **1.88 s** (construction only 0.04 s). 21_M_3400E has 314,086 triangles, 3.8x larger, so a
single A-B measurement is likely to take **several seconds at best**, since MMP window
propagation grows faster than linearly. Milestone 2.3 must therefore not appear frozen while it
runs, must measure the real cost on the real scan, and must not assume a batch or all-pairs
workflow is affordable. If it proves too slow the options are early termination, the VTP variant,
or the edge-flip solver — all changes of backend, not of architecture.

### 5.2 No approximate production fallback in Phase 2

If the exact backend is unavailable, fails, or the problem is ill-posed, BSMT returns a
**measurement failure state**. It never substitutes an approximate distance.

| Failure state | Displayed message |
|---|---|
| `BACKEND_MISSING` | Surface distance unavailable: exact geodesic backend not installed |
| `DISCONNECTED` | Surface distance unavailable: disconnected surface (A in component #i, N_i triangles; B in component #j, N_j triangles). Applies to the **point pair** only — see §7.4; a multi-component mesh is not itself a failure |
| `INVALID_TOPOLOGY` | Surface distance unavailable: invalid topology (n non-manifold edges, m degenerate triangles) |
| `BACKEND_ERROR` | Surface distance unavailable: exact geodesic backend failed (<exception summary>) |
| `STALE_POINTS` | Surface distance unavailable: mesh changed since points were picked |
| `DIFFERENT_OBJECTS` | Surface distance unavailable: Point A and Point B are on different objects |
| `POINTS_MISSING` | Surface distance unavailable: pick Point A and Point B first |

Rules:
- The straight distance is still shown; the surface distance field shows the failure message.
- No numeric surface distance is written to state in any failure case.
- `Surface / Straight Ratio` is shown only when a surface distance exists.

### 5.3 Edge-graph Dijkstra

Permitted **only** as an internal diagnostic and validation tool: connectivity testing, an upper
bound for the assertion `d_surface ≤ d_dijkstra`, seeding, and the validation-suite comparison
that demonstrates metrication bias. Its value is never displayed as, exported as, or stored as
the research surface measurement. Any UI exposure must be inside a clearly labelled diagnostics
area, never in the measurement result area.

### 5.4 Provenance record

Stored with every successful surface measurement and displayed on demand:

```
backend_name              e.g. "pygeodesic-MMP"
backend_version           resolved at runtime (importlib.metadata / __version__)
bsmt_algorithm_version    e.g. "bsmt-geodesic/1"
canonical_mesh_hash       hash of the computational mesh actually solved on
preprocessing             { triangulation, centering, weld: off|on+tolerance,
                            merged_vertex_count, source_object }
coordinate_unit           mm | cm | m
```

---

## 6. Phase 2 — canonical geodesic mesh and physical coordinate space

A non-destructive computational representation derived from the source scan. The source object
is never modified and receives no visible preprocessing.

### 6.1 Construction

From `obj.evaluated_get(depsgraph).to_mesh()`, on a temporary copy:

1. Triangulate (via `calc_loop_triangles()`, preserving a triangle → source-polygon map).
2. Extract positions with `foreach_get` and promote to **float64**.
3. Map into physical solver space (§6.2).
4. Optionally weld — **off by default**, only as a deliberate operation (§7).
5. Label connected components.
6. Build a `mathutils.bvhtree.BVHTree` over the same triangle array.

**Contents:** `V (n,3) float64` in solver space, `F (m,3) int32`, component labels per vertex and
per triangle, triangle→source-polygon map, `matrix_world`, centering offset, coordinate unit,
BVH, topology report, `geometry_hash`, `metric_key`.

**Stability requirement:** the triangle indices used by picking and by the geodesic solver are
the *same* indices, because both use this one array. This is why picking migrates from
`scene.ray_cast` to a BVH cast against the canonical mesh.

### 6.2 Physical coordinate space (solver space)

**All geodesic computation operates on the physical geometry the scan represents in Blender, not
on raw mesh-local coordinates.** The canonical solver vertex array is produced by this fixed
pipeline, in this order:

| Step | Operation | Purpose |
|---|---|---|
| 1 | `obj.evaluated_get(depsgraph).to_mesh()`, triangulated | evaluated source geometry, modifiers included |
| 2 | `v_world = matrix_world @ v_local` | object transform, where relevant to the metric |
| 3 | `v_mm = v_world * unit_multiplier(unit)` | BSMT coordinate-unit conversion |
| 4 | — | **solver space is millimetres** |
| 5 | `v_solver = v_mm − bbox_center_mm` | numerical centering |
| 6 | `float64` from step 2 onward | numerical precision |

Consequences, which are binding on all downstream code:

- **Backends return millimetres directly.** No unit multiplication is applied to a geodesic
  result anywhere downstream. Double conversion is a named failure mode in Milestone 2.3.
- Tolerances are physical and interpretable (e.g. 1e-6 mm), not relative to an unknown scale.
- The reported surface distance is the backend output, unmodified.
- Centering is a translation and therefore cannot change any distance; the offset is retained so
  solver coordinates map back to world space exactly for visualization.

Mapping back for display: `v_world = v_solver / unit_multiplier(unit) + bbox_center_mm / unit_multiplier(unit)`.

### 6.3 Required transform behaviour

| Transform applied to the scan object | Geodesic distance | SurfacePoint | Cached geodesic result |
|---|---|---|---|
| Translation | unchanged | **valid — not stale** | remains valid; only cached world XYZ refreshes |
| Rotation | unchanged | **valid — not stale** | remains valid; only cached world XYZ refreshes |
| Uniform scale by *k* | scales by exactly *k* | valid | **invalidated — recompute** |
| Non-uniform scale | distance **and path** change | valid | **invalidated — recompute** |
| Geometry / topology edit | undefined | **STALE** | invalidated |
| Coordinate-unit change | scales with the multiplier | valid | **invalidated — recompute** |

Rationale: geodesic distance is intrinsic to the surface metric. Rigid motions are isometries of
the embedding and cannot change it, so a translated or rotated scan must measure identically and
its points must not be invalidated. Scale changes the induced metric; **non-uniform scale changes
it anisotropically, so the shortest path itself moves to a different route** — such a result can
never be rescaled from a previous answer, it must be recomputed.

### 6.4 Cache identity: geometry hash vs metric key

Two independent keys, so that geometry identity and metric-affecting transform state are
distinguishable:

```
geometry_hash = digest( local vertex positions, triangle array, preprocessing settings )
                → identity of the mesh as data. Excludes matrix_world entirely.
                → a change here invalidates SurfacePoints (triangle indices may no longer
                  denote the same surface location).

metric_key    = digest( shape(T), magnitude(T) )   where  T = (Lᵀ L) · unit_multiplier²
                and L is the 3×3 linear part of matrix_world.
                → a change here does NOT invalidate SurfacePoints, but DOES force
                  geodesic recomputation.
```

`Lᵀ L` is the right Cauchy–Green tensor. For the polar decomposition `L = R·S` it equals `S²`, so
it is **exactly invariant to rotation** and it never contains the translation column at all.
Folding in `unit_multiplier²` makes `T` the physical metric in mm² per squared local unit.
Therefore: translation and rotation change neither key; scale and unit changes change only
`metric_key`; editing the mesh changes only `geometry_hash`.

The key must hash **two** parts:

- `shape` — `T` normalised by its own magnitude and coarsely quantised (relative rounding at
  ~1e-9), which absorbs floating-point noise in `matrix_world`;
- `magnitude` — that magnitude, retained to 12 significant digits.

Both are required. **Hashing only the normalised shape makes the key scale-invariant**, so a
uniform scale would wrongly reuse a cached distance instead of forcing recomputation. This was a
real defect in the first implementation, caught by the transform tests of §9.4; the requirement
is recorded here so it cannot be reintroduced.

Two further implementation obligations: signed zeros must be normalised before hashing
(`-0.0` and `+0.0` are numerically equal but hash differently), and a fully degenerate transform
(zero scale on every axis) must produce a distinguished key rather than divide by zero.

A useful consequence of folding the unit into `T`: a unit change combined with a compensating
object scale yields the *same* key, which is correct — the physical metric, and therefore the
distance in millimetres, really is unchanged.

### 6.5 Invalidation rules

- `geometry_hash` differs from the one stored in a `SurfacePoint` → `STALE_POINTS`. Never
  silently re-project the point onto the new mesh.
- `metric_key` differs from the one stored with a cached distance → discard the distance and the
  path, keep the points, require an explicit recompute. The UI must not display a distance
  computed under a different metric.
- Neither differs → the cached result stands; only cached world XYZ values are refreshed from
  `(triangle_index, barycentric, matrix_world)`.

### 6.6 Coordinate frames in use

| Frame | Units | Used for |
|---|---|---|
| local | coordinate units | source mesh data, `geometry_hash` |
| world | coordinate units | markers, Phase 1 straight distance, display |
| mm | millimetres | intermediate |
| solver | millimetres, centered | **all geodesic computation and all reported distances** |

---

## 7. Phase 2 — topology diagnostics and the welding policy

### 7.1 Correction to the earlier draft

The earlier Phase 2 draft asserted that UV seams in imported OBJ scans split mesh topology and
that welding was therefore mandatory. **This is not correct for Blender's mesh data model:** UVs
are stored per *loop*, not per vertex, and Blender's OBJ importer creates one vertex per unique
position index. A UV seam alone does not disconnect the surface.

Coincident or duplicated vertices can still arise from other sources — third-party scan software,
merged scan fragments, split-by-material or split-by-group import options, exporter behaviour.
BSMT therefore **detects and reports** them and does not assume their existence or their cause.

### 7.2 Diagnostics (Milestone 2.0)

Computed on the canonical mesh, reported in the panel and to the console:

- vertex count, triangle count
- connected component count, and triangle count of the largest components
- boundary edge count (edges with exactly one incident triangle)
- non-manifold edge count (edges with ≥3 incident triangles)
- exactly-coincident duplicate-position vertex count
- near-coincident vertex count at a stated tolerance (reported, not applied)
- degenerate (zero/near-zero area) triangle count
- bounding-box dimensions, in coordinate units and in mm
- estimated mean/median edge length (context for tolerances and for `h` in convergence studies)

### 7.3 Welding policy

Automatic welding is **prohibited**. Any topology repair is a deliberate, explicit, opt-in
operation with an operator-supplied tolerance, and it reports the number of merged vertices and
the resulting change in component count.

Rationale — merge-by-distance on a human scan can fuse surfaces that are anatomically distinct
but geometrically close or touching:
arm↔torso, finger↔finger, thigh↔thigh, garment↔skin, and any self-contact region.
A fused surface creates a shortcut that is invisible in the result and produces a confidently
wrong, systematically short measurement. **For Phase 2, prefer an explicit failure or warning
over unsafe automatic welding.**

Diagnostics that suggest a problem produce a warning and a recommendation; they never act.

---

### 7.4 Multiple connected components are not a measurement blocker

A scan containing more than one connected component is normal and legitimate:
hair, garments, scan fragments, cropped islands, accessories. It must **not**
invalidate measurement.

The binding rule:

| Situation | Behaviour |
|---|---|
| Source mesh has more than one component | **Diagnostic warning only.** Never an error, never a blocked measurement, never automatic welding or repair. |
| Point A and Point B on the **same** component | Exact geodesic calculation proceeds normally. |
| Point A and Point B on **different** components | Explicit `DISCONNECTED` failure (§5.2). No number, no approximate substitute. |

The connectivity test is therefore a property of the **point pair**, not of the
mesh. It is evaluated per measurement from the `component_id` stored on each
`SurfacePoint` (§8.1), before any solver call, and it is cheap.

Components are never merged to make a measurement possible. The welding
prohibition of §7.3 is unconditional and is not relaxed by a `DISCONNECTED`
result: the correct response to a cross-component pair is to re-pick the
landmarks or to accept that no surface path exists, never to fuse the surfaces.

Reference measurement from a real scan (21_M_3400E, 2026-09-01): 157,045
vertices, 314,086 triangles, 0 boundary edges, 0 non-manifold edges, 0
coincident vertices, analysed in 0.85 s. Scan height ≈ 1710.283 mm, confirming
the OBJ coordinate unit is millimetres.

> **The component counts from that session are void.** Two runs reported
> different partitions of the same mesh (267,173 / 227 / 25,030 / 21,656 and
> 217,173 / 50,227 / 25,030 / 21,656), both produced by the defective labelling
> algorithm described in §7.6. The component structure of this scan must be
> re-measured with a certified labelling before any figure from it is cited.
> The rule above is unaffected: whatever the true component count, a
> same-component point pair is measurable.

### 7.5 Component visualization (diagnostics only)

To let an operator see *where* each component is before picking landmarks,
BSMT can draw them in distinct colours.

- One temporary BSMT helper object per component, named `BSMT_Component_<n>`
  with `n` = 1 for the component with the most triangles.
- Helper geometry is a **copy** of that component's triangles, placed in world
  space with an identity transform, so it overlays the scan whatever the scan's
  own transform is.
- Standard helper safety applies: `bsmt_helper` tagged, linked into
  `BSMT_Helpers`, `hide_select`, removed only through the tag-gated deletion
  path. The scan's mesh, transform and object state are never touched.
- Colours come from a fixed distinct palette, extended by golden-ratio hue
  rotation beyond it, so component identity is stable across runs.
- One component can be isolated at a time; isolation toggles `hide_viewport`
  only and creates, moves or deletes nothing.
- The preview has its own Clear button and is **not** removed by
  `Clear Points`, so measurement helpers and diagnostics helpers have
  independent lifetimes.
- Nothing about the preview is welded, merged or repaired. It is a colour key,
  not a modification.

---

### 7.6 Connected components must be certified, not assumed

**Defect found 2026-09-01 (fixed in 0.4.1).** The first component labelling
used min-label hooking: each vertex was hooked to the smallest label among its
neighbours, then paths were compressed. That propagates a label one edge per
round, so it needs O(graph diameter) rounds — thousands on a body scan — and it
stopped silently at a 64-iteration cap.

The consequence is worse than an imprecise count. An unconverged label class is
a *subset* of a true component, and **a subset need not be spatially
contiguous**, so one reported "component" covered a head and an unconnected
forearm patch. The count was wrong *and* the colours were meaningless. It went
unnoticed because the test suite used generated grids, whose spatially ordered
vertex numbering converges in two rounds; a real scan's numbering is arbitrary.
Randomly permuting the vertex numbering of a single connected grid reproduced
it immediately: 8–11 components reported instead of 1.

Binding requirements:

1. **Root hooking.** Hook roots to roots (Shiloach–Vishkin), not vertices to
   neighbours. Measured convergence: 7–12 rounds on half-million-vertex meshes,
   independent of numbering.
2. **Certify every labelling.** A labelling is exactly the component partition
   if and only if every edge joins two equally-labelled vertices (labels
   propagate only along edges, so the converse direction is automatic). This
   O(E) check is mandatory; a labelling that fails it raises `TopologyError`
   and is refused, never returned.
3. **Verify independently before display.** For each component, re-extract its
   triangles from the canonical array, renumber locally, and recompute
   connectivity from scratch: exactly one connected component is required.
   Also check that the label classes are disjoint and cover every canonical
   triangle exactly once.
4. **Never draw unverified colours.** Any failure aborts the preview with an
   explicit diagnostic instead of displaying misleading geometry.
5. **Test with permuted vertex numbering.** Any test of connectivity on a
   generated mesh must also run on a randomly renumbered copy. Ordered
   numbering hides exactly the class of defect described here.

This matters beyond diagnostics: Milestone 2.1 stores `component_id` on every
`SurfacePoint`, and §7.4's DISCONNECTED rule is decided from it. A wrong
component partition would silently reject valid measurements and permit
invalid ones.

### 7.7 The canonical triangle array is the single source of truth

Polygon indices, loop-triangle indices, `from_pydata` face indices and helper
object face indices are **not** interchangeable. All component work indexes the
canonical triangle array produced by one `extract_solver_mesh()` call:

    component n  =  canonical_vertices, canonical_triangles[labels == n]

Nothing is re-indexed through a separately generated ordering, and generated
helper geometry is checked against the labelling (face count per component,
triangles only, total coverage) before it is shown.

---

## 8. Phase 2 — SurfacePoint and picking

### 8.1 Representation

```
SurfacePoint:
    source_object            object name (identity of the scan the point belongs to)
    canonical_mesh_hash      hash of the canonical mesh the indices refer to
    triangle_index           index into canonical F
    barycentric_coordinates  (u, v, w), u+v+w = 1, float64
    component_id             connected component label
    cached_local_xyz         for reconstruction
    cached_world_xyz         for marker display and straight distance
```

**Triangle index + barycentric coordinates are the canonical surface location.** World XYZ is a
cached display value and is never the source of truth. This also fixes the Phase 1 limitation
that points do not follow the object under transformation.

### 8.2 Picking (Milestone 2.1 / 2.3)

Phase 2 picking ray-casts against the canonical mesh's BVH so the recorded triangle index is
unambiguous, then computes barycentric coordinates within that triangle. Phase 1's modal
interaction, feedback, cancel behaviour and marker display are preserved. The Phase 1
straight-distance path continues to work from `cached_world_xyz`.

### 8.3 Degeneracy handling at pick time

With a relative tolerance `eps_bary` (scaled to triangle size):
- one barycentric coordinate < `eps_bary` → snap onto that **edge**
- two coordinates < `eps_bary` → snap to that **vertex**
- classification (`FACE` / `EDGE` / `VERTEX`) is stored with the point

### 8.4 Insertion into the scratch mesh at solve time

Both points are located against the *original* canonical indexing, then a single scratch copy is
built with all required splits applied together — never sequentially, because inserting A
renumbers the triangles B refers to.

| Case | Handling |
|---|---|
| Point inside a face | 1→3 triangle split at the point |
| Point on an interior edge | both incident triangles split 1→2 (4 triangles total) |
| Point on a boundary edge | the single incident triangle split 1→2 |
| Point on a vertex | reuse the existing vertex; no split |
| **A and B in the same triangle** | **Special-cased analytically:** the triangle is planar, so the geodesic is the straight segment inside it and `d_surface == d_straight` exactly. No insertion, no solver call. |
| A and B on the same edge | analytic: distance along the edge |
| A and B in triangles sharing the split edge | both splits applied in the same rebuild; verify resulting triangulation is valid before solving |

Post-insertion assertions: total surface area unchanged within tolerance; no zero-area triangles
created; component labels unchanged; both inserted vertices exist exactly once.

---

## 9. Phase 2 — measurement, visualization and validation

### 9.1 Operators and UI

Phase 1's `Calculate Distance` (straight) is unchanged. A new `Calculate Surface Distance`
operator runs the exact backend. Keeping them separate preserves Phase 1 behaviour exactly and
keeps the expensive computation explicit.

Panel display:

```
Straight Distance:   XXX.XX mm
Surface Distance:    XXX.XX mm     |  or a failure message from §5.2
Surface / Straight:  1.XXX          |  shown only when both exist
```

Plus a collapsed **Diagnostics / Provenance** sub-panel.

### 9.2 Assertions on every successful measurement

- `d_surface ≥ d_straight` within numerical tolerance — violation is a hard error, not a warning
- `d_surface ≤ d_dijkstra_seed` (internal diagnostic bound)
- sum of returned polyline segment lengths == reported distance within tolerance
- both endpoints lie in the same connected component

### 9.3 Surface path visualization

`BSMT_Surface_Path` — a BSMT helper object using the existing tagging, collection and deletion
framework (`bsmt_helper`, `BSMT_Helpers`, tag-gated removal):

- a poly curve through the exact returned polyline (source → edge crossings → target)
- adjustable **physical** thickness in mm via `bevel_depth`, consistent with Phase 1's unit handling
- visually distinct from the straight line (different colour; straight line stays yellow)
- each polyline vertex offset along the interpolated surface normal by ~1× the bevel radius so
  the tube is not half-buried in the scan
- `Show Surface Path` display toggle alongside the existing checkboxes
- never modifies the scan; removed by `Clear Points` like every other helper

The full-resolution polyline is retained for the length assertion even if a decimated version is
used for display.

### 9.4 Validation

Headless: `blender --background --python tests/run_validation.py`, emitting a machine-readable
table. Remeshing tests report **convergence and sensitivity**, never identity.

**Analytic surfaces:**

| Surface | Ground truth | Expectation |
|---|---|---|
| Plane (several triangulations, incl. slivers) | geodesic == Euclidean, exactly | exact backend: relative error at floating-point level, on *every* triangulation. This is the strongest single test. |
| Cylinder R | `√((R·Δθ_wrapped)² + Δz²)` | developable; must take the shorter way around |
| Sphere R | `R·arccos(â·b̂)` | across progressively finer meshes; report the measured convergence trend |
| Cone | analytically unrolled distance, where the unrolling is valid | developable with a singular apex |

**Transform tests (§6.3) — required, explicit:**

| Test | Setup | Required result |
|---|---|---|
| Translation | move the object by an arbitrary **t** | `\|d' − d\| ≤ tol`; SurfacePoints **not** stale; path shifts by **t** |
| Rotation | rotate by an arbitrary **R** (non-axis-aligned) | `\|d' − d\| ≤ tol`; SurfacePoints **not** stale; path rotates by **R** |
| Uniform scale | scale by *k* ∈ {0.5, 2, 10} | `d' = k·d` within tol; cached result invalidated first, then recomputed |
| Non-uniform scale | `S = diag(a,b,c)`, `a≠b≠c` | (i) on a **plane**, matches the analytic scaled-Euclidean value; (ii) equals the result of pre-multiplying the vertex data by **S** and solving with identity transform (metamorphic); (iii) **differs** from the unscaled result, and the returned path differs — a rescaled cached answer must be provably impossible |

Also verify that a coordinate-unit change (mm→cm→m) rescales the reported millimetre distance
consistently, and that `metric_key` changed in exactly the scale and unit cases and in no other.

**Invariance and consistency tests:** A→B symmetry, A→A = 0, triangle inequality via an
intermediate point.

**Robustness tests:** disconnected components must **fail** (never return a number); cropped
boundaries; holes; duplicate/coincident vertices (reported, not silently welded); different mesh
densities; different triangulations of the same analytic surface.

**Sensitivity reporting (replaces the earlier "remesh invariance" claim):** for each analytic
surface, report `d` across densities and triangulations as mean ± spread, plus the trend with
`h`. The comparison figure plots relative error vs `h` for exact vs edge-Dijkstra; the expected
signature is a shrinking exact error against a Dijkstra plateau.

**Real-scan protocol (no analytic truth):** intra-operator repeatability (repeat picks of the
same landmark pair, report SD), sensitivity across decimated copies of the same scan, timing,
and the plausibility of the surface/straight ratio.

---

## 10. Packaging decision

**For now:** develop and validate on the current macOS Blender 4.5.13 environment.
`pygeodesic` is installed into Blender's bundled Python via pip. BSMT stays a legacy `bl_info`
add-on. The add-on must load and Phase 1 must work with the backend absent.

**Later, after Phase 2 is validated:** migrate BSMT to the Blender 4.2+ extension format
(`blender_manifest.toml`) and bundle platform-specific wheels for distribution.

Known risks of the interim approach, to be verified in Milestone 2.2: Blender updates can wipe
the bundled site-packages; the wheel architecture must match the Blender binary (arm64 vs
x86_64 under Rosetta); and the wheel's compiled numpy ABI must be compatible with Blender's
bundled numpy.

---

## 11. Phase 2 implementation plan

Proposed structure (created incrementally by the milestones below):

```
body_surface_measurement/
  geodesic/
    __init__.py
    topology.py        pure-numpy mesh analysis: edges, components, duplicates (2.0)
    spaces.py          pure-numpy solver-space mapping, geometry_hash, metric_key (2.0)
    extract.py         bpy-side evaluated-mesh extraction into solver space (2.0)
    preview.py         bpy-side connected-component visualisation (2.0a)
    meshcache.py       canonical mesh, BVH, geometry_hash / metric_key, cache (2.1)
    surface_point.py   SurfacePoint, barycentrics, insertion (2.1)
    registry.py        backend discovery, availability, provenance (2.3)
    backends/
      __init__.py
      exact_mmp.py     pygeodesic (2.3)
      dijkstra.py      diagnostics/validation only (2.5)
```

Tests live **outside** the shipped package, at the project root, so they are never distributed
with the add-on:

```
tests/
  test_topology.py   pure-numpy unit tests, runnable without Blender (2.0)
  analytic.py        closed-form distances (2.5)
  synthetic.py       mesh generators (2.5)
  run_validation.py  headless Blender entry point (2.5)
```

`topology.py` and `spaces.py` deliberately import **only numpy** — no `bpy` — so the diagnostic
core and the §6.2–6.4 coordinate-space semantics are unit-testable outside Blender. All Blender
coupling lives in `extract.py`.

### Milestone 2.0 — Topology diagnostics

**Create:** `geodesic/__init__.py`, `geodesic/topology.py` (pure numpy),
`geodesic/extract.py` (bpy → solver space per §6.2), `tests/test_topology.py`
**Change:** `operators.py` (`bsmt.diagnose_topology`), `panels.py` (diagnostics sub-panel),
`state.py` (report storage + near-coincident tolerance), `__init__.py` (registration)

**Success criteria**
- Default cube reports 8 vertices, 12 triangles, 1 component, 0 boundary edges, 0 non-manifold
  edges, 0 duplicates, 0 degenerate triangles.
- A cube with one face deleted reports a non-zero boundary edge count.
- Two separated spheres report 2 components with correct triangle counts.
- A deliberately duplicated-vertex mesh reports the duplicate count and does **not** weld.
- Runs on a real scan (>100k triangles) in a few seconds; no modification to the source object
  (verify vertex count, transform and mesh datablock name before/after).

**Failure modes**
- Pure-Python per-element loops → unusable on dense scans; must use `foreach_get` + numpy.
- Confusing evaluated vs original mesh, or forgetting `to_mesh_clear()` → leaks.
- Ngons/quads not triangulated before edge counting → wrong manifold statistics.
- Near-coincident tolerance chosen in absolute units → meaningless across mm/cm/m scans.

### Milestone 2.0a — Component visualization (diagnostics)

**Create:** `geodesic/preview.py`
**Change:** `geodesic/topology.py` (additive `component_labels()`, corrected
multi-component warning text), `geodesic/__init__.py` (guarded preview loader,
`preview_error()`), `visualization.py` (public helper primitives,
`COMPONENT_PREFIX`, component preview excluded from `Clear Points`),
`state.py` (`BSMT_ComponentInfo`, component collection, isolation),
`operators.py`, `panels.py`, `tests/`

**Success criteria**
- Each component is drawn in a distinct colour and can be located spatially.
- Component list shows ID, triangle count and vertex count, ordered by size.
- Isolation shows one component at a time and restores all of them.
- `component_labels()` agrees with `analyse()` on component counts and sizes.
- The multi-component warning states that measurement is still allowed.
- Source scan mesh, transform and object state unchanged; nothing welded.
- Preview and measurement helpers clear independently.

**Failure modes**
- **Component labelling that is not certified converged** (§7.6) — this
  actually happened; it produced spatially incoherent colours and two different
  component counts for one mesh.
- Tests that only use generated meshes with spatially ordered vertex numbering
  will not catch it; permuted numbering is mandatory.
- Building copies of a dense scan is slow or memory heavy; report elapsed time
  and keep the operation explicit rather than automatic.
- Helper geometry coincident with the scan z-fights unless drawn in front.
- Component numbering must be deterministic across runs, or the colour key
  means nothing between sessions.
- Isolation must never be implemented by deleting objects.

### Milestone 2.1 — Canonical geodesic mesh + SurfacePoint

**Create:** `geodesic/meshcache.py`, `geodesic/surface_point.py`
**Change:** `state.py` (SurfacePoint fields for A and B), `picking.py` (BVH cast returning
triangle index + barycentrics), `operators.py` (store SurfacePoint on pick)

**Success criteria**
- Barycentric round-trip: reconstructed world position matches the BVH hit to <1e-6 relative.
- Picking on a translated/rotated/scaled object yields a point that reconstructs correctly.
- **Transform semantics per §6.3:** translation and rotation leave `geometry_hash` and
  `metric_key` unchanged and leave SurfacePoints valid; uniform and non-uniform scale change
  `metric_key` only; a mesh edit changes `geometry_hash` only.
- Solver-space vertices are millimetres, centered, float64, matching §6.2 step by step.
- Cache invalidates on geometry change and yields `STALE_POINTS` rather than a wrong answer.
- Insertion unit tests for all five cases in §8.4 pass the post-insertion assertions.
- Same-triangle case is detected and short-circuits before any solver call.
- Phase 1 straight distance still produces identical values to v0.2.0 on the same picks.

**Failure modes**
- Mismatch between `loop_triangles` ordering and the BVH's polygon indices → silently wrong
  triangle indices; must be asserted, not assumed.
- Local vs world space confusion in the cached XYZ.
- Sequential insertion renumbering triangles (§8.4) → B lands in the wrong triangle.
- float32 leakage from `foreach_get` into the solve path.
- Degenerate barycentrics on sliver triangles → snapping tolerance must be relative.

### Milestone 2.1a — as implemented (2026-09-01)

**Created:** `geodesic/surface_point.py` (pure numpy: barycentrics,
classification, scratch-mesh insertion), `geodesic/meshcache.py` (canonical
mesh, BVH, cache, depsgraph invalidation), `tests/test_surface_point.py`

**Key decisions**

- The BVH is built with `BVHTree.FromPolygons` **directly from
  `canonical_vertices` + `canonical_triangles`**, in order, so the index it
  returns *is* the canonical triangle index. No polygon/loop-triangle/BVH/
  preview index mapping exists anywhere to get wrong (§7.7).
- Picking transforms the viewport world ray into object-local coordinates and
  casts against that local BVH. Barycentric coordinates are affine invariant,
  so the result is equally valid in solver millimetres, and the BVH never needs
  rebuilding for a translation or rotation.
- Topology diagnostics, component labels, the component preview and
  SurfacePoint all read the same cached `CanonicalMesh`.
- Cache invalidation uses a `depsgraph_update_post` handler that clears only on
  `is_updated_geometry`; transform updates deliberately do not invalidate.
  Transform-dependent data (`vertices_solver`, `center_mm`, `metric_key`) is
  refreshed in place by `refresh_transform()`.
- Endpoint insertion classifies **both** points against the original canonical
  topology, then rebuilds once. A point on an edge splits every incident
  triangle, so the mesh stays conforming; a second point landing on an internal
  edge created by the first splits both sub-triangles for the same reason.

**Marker attachment (0.5.1).** Helpers follow object transforms automatically.
`attach.py` handles `depsgraph_update_post` and reconstructs each helper world
position from canonical local corners + barycentric + current `matrix_world`.
It never rebuilds canonical triangles, the BVH, component labels or the
geometry hash: a transform is not a geometry edit.

Handler safety rules, all of which had to hold before this could be shipped:

- Updates from BSMT helper objects are ignored, in *both* handlers. The mesh
  cache previously cleared on any geometry update, so moving the measurement
  curve discarded the canonical mesh on every drag frame.
- A re-entrancy guard plus write-only-when-changed prevents feedback loops.
- The handler does no hashing, topology analysis or mesh extraction — only a
  few 3x3 products — so interactive gizmo dragging stays cheap.
- **When the canonical mesh is no longer cached, helpers are not moved.** The
  cache handler runs first and clears on a scan geometry edit, so a cleared
  cache is exactly the "geometry changed" signal. Nothing is ever reprojected
  onto modified geometry; the point is marked as needing an explicit refresh.

`physical_mm_xyz` is world position x unit multiplier (uncentred), so it stays
meaningful under any transform. The arbitrary solver centering remains an
internal detail of the solver-space array.

### Milestone 2.2 — pygeodesic environment proof-of-concept

**Create:** `tools/check_geodesic_env.py` (a standalone script, not part of the add-on)
**Change:** none in the add-on

**Status 2026-09-01:** the script is written and validated against real pygeodesic 0.1.11
outside Blender (see §5.1). The Blender-side run is outstanding and is what actually closes
this milestone.

**Success criteria**
- `sys.version` inside Blender 4.5.13 confirmed (expected 3.11); architecture confirmed arm64.
- `pygeodesic` imports inside Blender's Python without a numpy ABI error.
- On an icosphere of known radius, vertex-to-vertex distance is within the expected
  discretisation margin of the great-circle value, and a path polyline is returned.
- Wall-clock timing recorded for one query on a scan-sized mesh (>100k triangles).
- The exact install command and the resolved backend version are recorded in this spec.

**Failure modes**
- Python is not 3.11 → no cp311 wheel applies; the whole backend choice must be revisited.
- x86_64 wheel installed under a Rosetta pip for an arm64 Blender → import error or crash.
- numpy ABI mismatch with Blender's bundled numpy → segfault, not a clean exception.
- `pip` writing outside Blender's site-packages (wrong interpreter) → import succeeds in Terminal
  but fails in Blender.
- Permission errors writing into the app bundle.

### Milestone 2.3 — Exact A–B surface distance

**Create:** `geodesic/registry.py`, `geodesic/backends/__init__.py`, `geodesic/backends/exact_mmp.py`
**Change:** `state.py` (surface distance, failure state, provenance), `operators.py`
(`bsmt.calculate_surface_distance`), `panels.py` (result + provenance display),
`measurement.py` (surface distance unit conversion and formatting)

**Success criteria**
- Plane: surface distance equals Euclidean to ~1e-9 relative, on three different triangulations.
- Same-triangle picks: surface distance == straight distance exactly (analytic short-circuit).
- `d_surface ≥ d_straight` holds on every test; a violation raises rather than displays.
- Disconnected components produce `DISCONNECTED` with component sizes and **no number**.
- Backend absent produces `BACKEND_MISSING`; the add-on still loads and Phase 1 still works.
- Provenance (backend, version, mesh hash, preprocessing, algorithm version) is displayed.
- No backend exception can reach the user as a Blender traceback.

**Failure modes**
- Index off-by-one between inserted vertices and the array handed to the backend.
- MMP behaviour on meshes with boundaries or non-manifold edges not validated → wrong or hanging.
- Long-running solve blocking the UI with no feedback.
- Unit conversion applied twice (mesh already scaled to world, then multiplied again).
- Failure state left stale from a previous query and displayed next to a fresh straight distance.

### Milestone 2.4 — Exact path visualization

**Create:** none
**Change:** `visualization.py` (`BSMT_Surface_Path`, normal offset, colour, thickness),
`state.py` (`show_surface_path`, `path_thickness_mm`), `panels.py`

**Success criteria**
- The path visibly follows the surface and is not buried in it.
- Sum of polyline segment lengths equals the reported distance within tolerance.
- `Show Surface Path` toggles visibility without altering stored coordinates or the distance.
- `Clear Points` removes it; the scan is untouched.
- Thickness is physical (mm) and correct under each coordinate unit.
- A path with tens of thousands of points still displays at an acceptable frame rate.

**Failure modes**
- Polyline returned in object space but drawn in world space (or reversed order).
- Normal offset applied along the wrong normal at edge crossings → visible zig-zag.
- Display decimation silently applied to the polyline used for the length assertion.
- Curve datablock not freed on rebuild → orphaned data accumulation.

### Milestone 2.5 — Validation suite

**Create:** `tests/analytic.py`, `tests/synthetic.py`, `tests/run_validation.py`,
`geodesic/backends/dijkstra.py` (diagnostics only)
**Change:** `VALIDATION.md` (Phase 2 section)

**Success criteria**
- `blender --background --python tests/run_validation.py` runs end-to-end and emits a table.
- Plane: relative error at floating-point level on every triangulation tested.
- Cylinder, sphere, cone: errors within the predicted discretisation margin; the measured trend
  with `h` is reported (not assumed).
- The four transform tests of §9.4 pass: translation and rotation invariant, uniform scale
  exactly linear, non-uniform scale reflected in both distance and path (and provably not
  obtainable by rescaling a cached result).
- Invariance tests (symmetry, A→A=0, triangle inequality) pass within stated tolerances.
- Robustness tests behave as specified: disconnected fails, boundaries/holes warn, duplicates are
  reported not welded.
- The exact-vs-Dijkstra error-vs-`h` comparison is produced and shows the expected signature.

**Failure modes**
- Synthetic generators producing degenerate or non-manifold triangles → tests measure the
  generator, not the algorithm.
- Errors in the analytic formulas themselves (cone unrolling and cylinder angle wrapping are the
  easy ones to get wrong) → cross-check each against a densely refined numerical solution.
- Tolerances tuned until tests pass, hiding real error.
- Headless Blender not finding `pygeodesic` (different interpreter path than the GUI).

### Milestone 2.6 — Human scan testing

**Create:** `tests/scan_protocol.md`
**Change:** documentation only

**Success criteria**
- Topology diagnostics recorded for each real scan (components, boundaries, duplicates).
- Intra-operator repeatability: N repeated picks of the same landmark pair, SD reported in mm.
- Sensitivity across decimated copies of the same scan reported as a spread, per §9.4.
- Timing acceptable for interactive research use.
- Surface/straight ratios anatomically plausible; any path crossing near a cropped boundary or
  hole is flagged.
- No scan file or scan object modified at any point in the session.

**Failure modes**
- Scans with many small components (hair, garments, scan noise) → landmarks land on the wrong
  component; diagnostics must make this obvious.
- Cropping or holes forcing long detours that look plausible but are artifacts.
- Self-contact regions: correctly *not* connected without welding, but tempting to "fix" — the
  welding prohibition (§7.3) must hold.
- Landmark pairs that wrap a limb, where the shortest surface path is not the anatomically
  intended one.

---

## 12. Open items requiring decisions

1. Confirmation of Blender 4.5.13's bundled Python version and architecture (Milestone 2.2).
2. Whether landmark pairs may wrap a limb or torso; affects interpretation, not the algorithm.
3. Typical scan triangle count and whether scans arrive pre-cropped.
4. Whether Phase 3 requires all-pairs landmark fields (would justify adding a heat-method backend
   for one-to-many queries, alongside — never replacing — the exact backend).

---

## 13. Future phase — anatomical scan alignment

**Status: requirement recorded only. Not designed, not scheduled, and explicitly
NOT part of Milestone 2.2 or any other Phase 2 milestone.**

Handheld human-body scans arrive in whatever coordinate system the capture
software produced. The imported OBJ axes need not correspond to anatomical
front/back, left/right or vertical directions. BSMT must eventually support
bringing a scan into an anatomical frame.

### 13.1 Position in the pipeline

Alignment is a **preprocessing and display operation**, kept separate from
geodesic measurement:

```
Import → Alignment → Geometry preprocessing (if required) → Landmark picking
       → Straight / surface measurement → Export
```

### 13.2 Required modes

**1. Manual alignment**
- translation and rotation controls;
- convenient front and side alignment;
- reset to the original imported transform;
- save/record the alignment transform.

**2. Landmark-based anatomical alignment**

The researcher defines four reference locations:

| Reference | Role |
|---|---|
| horizontal left | defines the medio-lateral axis with its right counterpart |
| horizontal right | |
| vertical upper | defines the longitudinal axis with its lower counterpart |
| vertical lower | |

An orthonormal anatomical frame is constructed from these references (the two
axes will not be exactly perpendicular in practice, so the construction must
orthonormalise explicitly and record the residual as a quality figure).

**Anatomical landmark names must not be hard-coded.** The researcher chooses
appropriate bilateral and vertical landmarks per protocol; BSMT stores roles,
not names.

### 13.3 Requirements

- Operate through **object transforms** wherever possible.
- **Never alter mesh topology.**
- **Preserve SurfacePoint triangle indices and barycentric coordinates.** This
  follows from §6.3: alignment is a rigid transform, so canonical surface
  locations are untouched by construction.
- Record the alignment matrix as data, not just as a modified object transform.
- Provide **Reset Alignment**.
- Preserve the original imported transform so the operation is reversible.
- Keep alignment transforms **distinguishable from geometry editing**, in both
  the data model and the cache-invalidation rules of §6.4: an alignment changes
  `metric_key` only if it is non-rigid (it should not be), and never changes
  `geometry_hash`.

### 13.4 Relationship to measurement correctness

**Alignment must never be required for straight or surface distance
correctness.** Rigid transforms are isometries and cannot change either
distance (§6.3). Alignment exists for interpretation, display, reproducible
viewing and downstream anatomical conventions — not to make measurements
valid. Any future design in which a measurement depends on alignment having
been performed is wrong and must be rejected.

A useful consequence: alignment can be applied, changed or reset at any point
in the workflow, before or after landmark picking, without invalidating
anything already measured.
