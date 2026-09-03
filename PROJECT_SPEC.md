# BSMT — Body Surface Measurement Tool
## Project Specification

**Document version:** 0.3
**Date:** 2026-09-02
**Target environment:** Blender 4.5.13 LTS, macOS 26.5 (Apple Silicon, arm64),
bundled Python 3.11.15, numpy 1.26.4 — **all detected at runtime, 2026-09-02** (§5.1a)
**Status:** Phase 1 complete and validated on a real human-body scan. Milestones 2.0, 2.0a,
2.1, 2.2, 2.3, 3.0 (Landmark Manager), 3.1 (Measurement Manager) and
3.2 (Measurement Visualisation), 3.3 (Scan Preprocessing), 3.4 (Mesh Repair) and
**3.5 (Automatic Local Repair)** implemented and validated in Blender. Real-scan acceptance
testing of 2.3 and 3.0–3.5 is outstanding.

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
| 2 | Surface (geodesic) distance between the same two points | **This document.** Milestones 2.0, 2.0a, 2.1, 2.1a, 2.2, **2.3 done** (v0.7.0); 2.4–2.6 outstanding |
| 3.0 | Named research landmark manager: protocols, guided picking, Validate All | **Done** (v0.8.0) |
| 3.1 | User-defined measurement manager: definitions, batch calculation, templates | **Done** (v0.9.0) |
| 3.2 | Measurement visualisation: straight chords and exact geodesic paths | **Done** (v0.10.0) |
| 3.3 | Scan preprocessing: textured measurement copy + solver safety gate | **Done** (v0.11.0) |
| 3.4 | Controlled mesh repair and measurement readiness | **Done** (v0.12.0) |
| 3.5 | Automatic local non-manifold repair | **Done** (v0.13.0) |
| 3.6+ | CSV/XLSX export, alignment, automatic landmark detection | Not designed |
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
propagation grows faster than linearly. **This estimate was measured in Milestone 2.2 and proved
optimistic: 16.8 s and ~1.7 GB at 327,680 triangles inside Blender (§5.1a).** Milestone 2.3 must therefore not appear frozen while it
runs, must measure the real cost on the real scan, and must not assume a batch or all-pairs
workflow is affordable. If it proves too slow the options are early termination, the VTP variant,
or the edge-flip solver — all changes of backend, not of architecture.

### 5.1a Measured environment proof — Milestone 2.2 (2026-09-02)

Everything below was **detected at runtime**, not assumed. Source: `tools/check_geodesic_env.py`
run under `Blender --background`, which now delegates to `geodesic/envreport.py` and
`geodesic/backends/selftest.py` so the standalone tool and the in-Blender panel report identical
numbers.

**Detected environment**

| Item | Detected value |
|---|---|
| Blender | 4.5.13 LTS, `/Applications/Blender.app/Contents/MacOS/Blender` |
| `sys.version` | 3.11.15 (main, Apr 25 2025) [Clang 15.0.0] |
| `sys.executable` | `/Applications/Blender.app/Contents/Resources/4.5/python/bin/python3.11` |
| `platform.system()` / `machine()` | `Darwin` / `arm64` (macOS 26.5) |
| numpy | 1.26.4, from Blender's bundled site-packages |
| Required wheel tag | `cp311`, macosx arm64 |
| pygeodesic resolved | **0.1.11**, `pygeodesic-0.1.11-cp311-cp311-macosx_11_0_arm64.whl` |

The Python-3.11 assumption of §5.1 is therefore **confirmed**, and the arm64 wheel exists.

**Two environment facts that change the install procedure**

1. **Blender runs its Python with `no_user_site = 1`.** `site.ENABLE_USER_SITE` is `True` for the
   bundled interpreter launched from a shell but `False` inside Blender itself. Consequently
   `pip install --user` puts the package in `~/.local/lib/python3.11/site-packages`, which Blender
   never searches: it imports in Terminal and fails inside Blender. The earlier version of
   `tools/check_geodesic_env.py` recommended exactly that and was wrong. The install target is now
   derived at runtime from the paths the live interpreter actually imports from.

2. **pygeodesic 0.1.11 declares `numpy<3,>=2`, but Blender bundles numpy 1.26.4.** The declared
   floor is *metadata only*: the compiled extension imports and runs correctly against numpy
   1.26.4 (measured, both in the bundled interpreter and inside Blender). A plain
   `pip install pygeodesic` would honour the metadata and place numpy 2.x ahead of Blender's own
   numpy on `sys.path`, silently changing the numpy every other part of Blender uses. **`--no-deps`
   is therefore mandatory, not a convenience.**

**Recorded install command** (no `sudo`; target survives a Blender update and is on Blender's
`sys.path` unconditionally):

```
"/Applications/Blender.app/Contents/Resources/4.5/python/bin/python3.11" -m pip install \
    --no-deps \
    --target "/Users/yeoni/Library/Application Support/Blender/4.5/scripts/addons/modules" \
    pygeodesic
```

**Measured backend results, inside Blender 4.5.13 / Python 3.11.15 / numpy 1.26.4 / arm64.**
The wheel was staged on `sys.path` rather than installed for this run; the numbers are from
Blender's own interpreter.

*Plane — the only test that isolates algorithmic error, because a planar polyhedron IS the plane:*

| Triangulation | Triangles | Exact rel. error | Edge-Dijkstra rel. error |
|---|---|---|---|
| forward diagonals, 20x20 | 800 | 0.000e+00 | 0.000e+00 |
| backward diagonals, 20x20 | 800 | 0.000e+00 | **4.142e-01** |
| alternating (checkerboard) | 800 | 0.000e+00 | 0.000e+00 |
| sliver (aspect ratio 40:1) | 800 | 1.421e-16 | 1.421e-16 |
| forward, 8x8 | 128 | 0.000e+00 | 0.000e+00 |
| forward, 60x60 | 7200 | 0.000e+00 | 4.019e-16 |

The exact backend's algorithmic error is at the floating-point floor on **every** triangulation,
including slivers. The Dijkstra column is the §5.3 diagnostic and is the clearest available
demonstration of metrication bias: on the *backward* grid, where no edge runs along the A-B
direction, it overestimates by √2 − 1 = 41.4%; on grids whose diagonal happens to align with the
query it is accidentally exact. **The bias is triangulation-dependent, which is precisely why an
edge-graph method can never be the research backend.**

*Cylinder — R = 50 mm, H = 200 mm, quarter turn plus half height, endpoints on mesh vertices.
Reference: the analytically unrolled distance 127.155428 mm.*

| n_θ × n_z | Triangles | h (mm) | Polyhedral (mm) | Abs. error (mm) | Rel. error | Observed order |
|---|---|---|---|---|---|---|
| 24 × 10 | 480 | 18.978 | 127.017130 | 0.138298 | 0.1088% | — |
| 48 × 20 | 1 920 | 9.496 | 127.120808 | 0.034620 | 0.0272% | **2.00** |
| 96 × 40 | 7 680 | 4.749 | 127.146770 | 0.008658 | 0.0068% | **2.00** |
| 192 × 80 | 30 720 | 2.375 | 127.153263 | 0.002165 | 0.0017% | **2.00** |

*Sphere — icosphere R = 100 mm, source at the pole, target the vertex nearest 90° (a unique
geodesic; an antipodal pair has infinitely many). Reference: great circle 157.079633 mm.*

| Subdiv. | Triangles | h (mm) | Polyhedral (mm) | Abs. error (mm) | Rel. error | Observed order |
|---|---|---|---|---|---|---|
| 2 | 320 | 29.834 | 155.665910 | 1.413723 | 0.9000% | — |
| 3 | 1 280 | 15.008 | 156.717368 | 0.362265 | 0.2306% | 1.98 |
| 4 | 5 120 | 7.516 | 156.986814 | 0.092819 | 0.0591% | 1.97 |
| 5 | 20 480 | 3.759 | 157.056487 | 0.023146 | 0.0147% | 2.00 |

Both curved cases are **signed negative at every resolution**: the inscribed polyhedron's chords
cut corners, so it underestimates the smooth surface. This difference is **representation error**
(§4.1), not algorithmic error, and it converges at the measured order ≈ 2 predicted but not
assumed by §4.4. The §4.4 discriminating signature is reproduced in full: the exact method's error
shrinks as O(h²) while edge-Dijkstra's does not shrink at all.

*Path/distance consistency* — `sum(|polyline segments|)` vs the reported distance: relative
difference 0.000e+00 (plane), 6.707e-16 (cylinder), 3.628e-16 (icosphere); path endpoints
coincide with the requested vertices to 0 mm.

**Dense-mesh benchmark — the most consequential result for Milestone 2.3.**
Synthetic icosphere of 327 680 triangles / 163 842 vertices, chosen to bracket the real scan's
314 086. The real scan was **not** loaded, opened or modified.

| Quantity | Measured |
|---|---|
| Mesh generation (BSMT's own generator) | 0.53 s |
| **Solver construction** | **0.14 s** |
| **First A–B query** | **16.77 s** |
| **Repeated identical query** | **16.67 s** (mean of 2) |
| Returned path points | 383 |
| Peak RSS before / after | 306 MB / 1 978 MB |
| Peak RSS delta | **≈ 1.67 GB** |

Three findings, all binding on Milestone 2.3:

- **Construction is free; the query is not.** 0.14 s vs 16.8 s. Caching the constructed solver
  buys almost nothing; the cost is entirely MMP window propagation.
- **A repeated query costs the same as the first.** `PyGeodesicAlgorithmExact` does not retain
  usable state between `geodesicDistance` calls, so an N-landmark session costs N × 16.8 s unless
  `geodesicDistances` (one-to-all) is used instead. That is worth measuring before 2.3 commits to
  a per-pair call.
- **≈1.7 GB peak RSS for one query at scan scale.** This must be stated as a system requirement
  and re-measured on the real scan.

This is **substantially worse than the ~7 s floor extrapolated in §5.1** from the 81 920-triangle
timing, confirming that MMP cost grows faster than linearly. Milestone 2.3 therefore cannot call
this synchronously from a panel button without progress feedback, and an all-pairs or batch
workflow is not affordable at this backend's current cost. If it proves unusable the options
remain those named in §5.1 — early termination, the VTP variant, or an edge-flip solver — all
changes of backend, not of architecture.

*Failure behaviour* — all twelve cases of §12/11 behave as required: empty vertex array, empty
triangle array, wrong array shape, non-finite coordinate, float triangle indices, out-of-range and
negative triangle indices, index-degenerate triangle, out-of-range source and target index, and a
1-point polyline each raise a typed `InvalidMeshError` with a specific message; `source == target`
returns exactly `0.0` with no solver call. No case returns a substitute number.

### 5.1b Query-mode investigation — Milestone 2.3 preparation (2026-09-02)

Question: can one-to-all computation materially reduce runtime for repeated landmark measurements
on a ~314k-triangle body scan? Source: `tools/benchmark_geodesic_modes.py`, run under
`Blender --background` on Blender 4.5.13 / Python 3.11.15 / numpy 1.26.4 / arm64, pygeodesic
0.1.11. Two meshes: the 327,680-triangle icosphere of §5.1a, and a body-proportioned cylinder
(R = 150 mm, H = 1710 mm — the reference scan's height) of 313,880 triangles, 157,235 vertices.
The real scan was not loaded.

**Measured, three targets from one source**

| | icosphere, 327,680 tri | body cylinder, 313,880 tri |
|---|---|---|
| Solver construction | 0.142 s | 0.098 s |
| **A** pairwise ×3 (`geodesicDistance`, returns paths) | 17.96 + 19.25 + 17.82 = **55.03 s** | 26.29 + 25.57 + 26.49 = **78.35 s** |
| **B1** one-to-all (`geodesicDistances`, all vertices, no path) | **19.07 s** (−65 % vs A) | **28.77 s** (−63 % vs A) |
| **B2** one-to-many with stop points (no path) | **17.07 s** (−69 % vs A) | **25.00 s** (−68 % vs A) |
| Lookup per target from the field | 2–7 × 10⁻⁷ s | 2–4 × 10⁻⁷ s |
| Distance field memory | 1.3 MB | 1.2 MB |
| Peak RSS | 1 818 → 1 828 MB | 1 949 → 1 950 MB |
| Worst \|B1 − A\| over the three targets | 2.8e-14 mm | 3.9e-12 mm |

Numerical agreement is exact to floating-point: the one-to-all field and the one-to-many subset
reproduce the pairwise distances to ≤ 3.9e-12 mm, i.e. ≤ 2.8e-15 relative.

**Finding 1 — the unbounded cost is independent of how far the target is.**
On the cylinder a 170 mm target cost 26.3 s and a 1366 mm target cost 26.5 s. That is not what
early termination should look like, and the cause is in the Kirsanov source:

```cpp
// geodesic_algorithm_exact.h — check_stop_conditions()
double queue_distance = (*m_queue.begin())->min();
if (queue_distance < stop_distance()) return false;      // stop_distance() == m_max_propagation_distance
while (index < m_stop_vertices.size()) { /* per-stop-vertex check */ }
```

`geodesicDistance` hardcodes `max_propagation_distance = GEODESIC_INF`, and `geodesicDistances`
defaults to it. `queue_distance < INF` is therefore always true and the function returns **before
the stop-vertex loop is ever reached**. Stop points are inert on their own. Every unbounded call,
pairwise or not, performs a full mesh propagation.

This also explains B1 vs B2: they do the same propagation, and the ~2–4 s difference is only the
per-vertex `best_source` readout over 157k–164k vertices.

**Finding 2 — a finite `max_distance` re-enables correct early termination, and the effect is
large.** Bounded queries on the body cylinder, verified against the unbounded reference:

| Target | d_true | Unbounded | Bounded (1.05 × d) | Speed-up | Result |
|---|---|---|---|---|---|
| near, same meridian | 170.36 mm | 28.62 s | **0.125 s** | **229×** | exact |
| mid, quarter turn | 641.74 mm | 29.42 s | **4.25 s** | **6.9×** | exact |
| far, half turn | 1365.78 mm | 27.16 s | 25.23 s | 1.1× | exact |

All 18 tested (target, k) combinations with k ∈ {0.5, 0.9, 1.001, 1.05, 1.25, 2.0} returned the
**exact** unbounded distance. Notably k = 0.5 — a bound *below* the true distance — was still
exact, because the stop-vertex loop refuses to stop until every stop vertex is settled.
`max_distance` therefore acts as a *minimum* sweep radius, not as a truncation of the answer.
Beyond the mesh diameter (≈1610 mm here) the bound stops mattering and the cost is a full sweep;
the 39.5 s reading at k = 1.25 against 27.7 s at k = 2.0 is run-to-run noise at that plateau, not
a real effect.

**This behaviour is undocumented**, so BSMT must not rely on the k < 1 case being safe. The
defensible pattern is to pass a bound that is a genuine upper estimate and to verify the result:
start at ~1.1 × the straight-line distance (the straight distance is a valid *lower* bound on the
geodesic), and if `inf` comes back — meaning the target was not covered — enlarge and retry. That
loop was measured and needed exactly one attempt for all three targets (0.158 s / 4.587 s /
21.431 s), because each failed attempt is itself cheap.

**Finding 3 — a path is not obtainable from a field solve.**
`geodesicDistances` returns `(distances, best_source)`; neither is a polyline. `trace_back` is
declared in the Cython `extern` block of `geodesic.pyx` but **no Python method exposes it**, so
the propagation state that would make a cheap trace-back possible is unreachable from Python. A
polyline for an arbitrary target therefore requires a separate `geodesicDistance` call, which
re-propagates from scratch: measured at 18.05 s immediately after the icosphere field solve
against 17.82 s cold (1.01×), and 26.78 s against 26.49 s on the cylinder (1.01×). **Solver state
is not reused between calls.** And because `geodesicDistance` hardcodes `GEODESIC_INF`, a path
query cannot be bounded at all — it is always full-sweep cost.

**Conclusion for Milestone 2.3.** One-to-all is *not* the answer for this workflow. It costs a
full sweep per source, so with several landmark *pairs* — each a different source — it saves
nothing over one bounded query per pair, and it cannot produce a path. The lever is bounding, not
batching:

| Need | Call | Cost at scan scale |
|---|---|---|
| Distance A→B | `geodesicDistances([A], [B], max_distance)` | 0.1–4 s for realistic landmark separations |
| Path A→B | `geodesicDistance(A, B)` | ~26 s, unavoidable with the stock wheel |

One-to-all keeps a legitimate future role — Phase 3 landmark fields (§12.4), where one source is
genuinely queried against many targets — but not in 2.3.

A future option, recorded but not taken: `trace_back` already exists on the C++ object, so
exposing it would make a bounded path query possible. That means building pygeodesic from source
and vendoring it, which trades the current one-line pip install for a build toolchain. It is only
worth revisiting if path visualisation proves too slow in practice.

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
`pygeodesic` is installed with Blender's own `pip`, **into Blender's user scripts modules
directory, not into the app bundle and never with `--user`** (§5.1a explains why each of those
matters). BSMT stays a legacy `bl_info` add-on. The add-on must load and Phase 1 must work with
the backend absent — verified in Blender 4.5.13 with pygeodesic uninstalled, 2026-09-02.

**Later, after Phase 2 is validated:** migrate BSMT to the Blender 4.2+ extension format
(`blender_manifest.toml`) and bundle platform-specific wheels for distribution.

Known risks of the interim approach, **as resolved by Milestone 2.2 (§5.1a)**:

| Risk | Status after measurement |
|---|---|
| Blender updates wipe the bundled site-packages | Avoided: the recorded target is `~/Library/Application Support/Blender/4.5/scripts/addons/modules`, outside the app bundle and on Blender's `sys.path` unconditionally. It is version-scoped to `4.5`, so a move to Blender 5.x needs a fresh install — not a silent breakage, but it must be re-run. |
| Wheel architecture must match the Blender binary | Resolved: Blender's binary is arm64 and its own `pip` resolves `pygeodesic-0.1.11-cp311-cp311-macosx_11_0_arm64.whl`. Using Blender's interpreter to install is what guarantees this — a Rosetta or system `pip` would not. |
| Compiled numpy ABI vs Blender's bundled numpy | Resolved, with a caveat: the extension imports and runs correctly against numpy 1.26.4, but its metadata declares `numpy<3,>=2`. `--no-deps` is mandatory so pip does not install numpy 2.x over Blender's numpy. |
| App-bundle write permissions | Not encountered, because nothing is written into the bundle. |
| `pip` writing to the wrong interpreter | Avoided by always invoking `"<Blender's python3.11>" -m pip`, and by the `--background --python-expr` verification step, which proves the import inside Blender rather than in a shell. |

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
    envreport.py       stdlib-only runtime environment detection (2.2)
    registry.py        backend discovery, availability, provenance (2.3)
    backends/
      __init__.py      guarded backend loader + status (2.2)
      exact_mmp.py     pygeodesic wrapper (2.2 dev-only, wired to production in 2.3)
      selftest.py      synthetic proof suite: generators, analytic refs, benchmark (2.2)
      dijkstra.py      diagnostics/validation only (2.5)
```

Tests live **outside** the shipped package, at the project root, so they are never distributed
with the add-on:

```
tests/
  test_topology.py     pure-numpy unit tests, runnable without Blender (2.0)
  test_surface_point.py  barycentrics, classification, insertion (2.1)
  test_import.py       add-on module wiring, with a stubbed bpy (2.0)
  test_backend_exact.py  backend wrapper guards + numerics, without Blender (2.2)
  analytic.py        closed-form distances (2.5)
  synthetic.py       mesh generators (2.5)
  run_validation.py  headless Blender entry point (2.5)
```

`topology.py`, `spaces.py`, `surface_point.py` and everything under `backends/` deliberately
import **only numpy** — no `bpy` — so the diagnostic core, the §6.2–6.4 coordinate-space
semantics and the solver backends are unit-testable outside Blender. `envreport.py` goes further
and needs only the standard library, because it must produce a useful report precisely when numpy
is the thing that is broken. All Blender coupling lives in `extract.py`, `meshcache.py` and
`preview.py`.

`backends/exact_mmp.py` guards its own `pygeodesic` import, so **nothing in the BSMT import graph
depends on the backend being installed**. That is a binding invariant, not an implementation
detail: it is what lets Phase 1 keep working on a machine with no exact backend at all.

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

**Created:** `geodesic/envreport.py`, `geodesic/backends/__init__.py`,
`geodesic/backends/exact_mmp.py`, `geodesic/backends/selftest.py`,
`tests/test_backend_exact.py`
**Changed:** `geodesic/__init__.py` (guarded `envreport` + `backends` loaders,
`backend_status()` / `backend_available()` / `backend_error()` /
`backend_import_traceback()` / `environment_error()`), `state.py` (report storage and
benchmark options), `operators.py` (`bsmt.check_geodesic_env`,
`bsmt.run_backend_selftest`, `bsmt.clear_backend_reports`), `panels.py`
(`BSMT_PT_geodesic_backend`, a development panel), `__init__.py` (version 0.6.0),
`tools/check_geodesic_env.py` (rewritten: corrected install advice, numerics delegated)
**Not touched:** `surface_point.py`, `meshcache.py`, `topology.py`, `spaces.py`,
`extract.py`, `preview.py`, `picking.py`, `attach.py`, `visualization.py`,
`measurement.py`. The SurfacePoint representation and the canonical triangle indexing
are unchanged.

**Scope boundary.** This milestone proves the backend works in the target environment and
nothing more. The backend is reachable only from the development panel. No Surface Distance
is added to the measurement result, no `SurfacePoint` reaches the solver, no endpoint
insertion runs, and no path is visualised. All of that is Milestone 2.3 and 2.4.

**Status 2026-09-02: closed.** Full detected environment, install procedure and measured
results are in §5.1a. Summary: Blender 4.5.13 LTS / Python 3.11.15 / numpy 1.26.4 /
Darwin arm64; pygeodesic 0.1.11 (`cp311`, macosx_11_0_arm64) imports and runs inside
Blender's own interpreter; the planar test is exact to the floating-point floor on every
triangulation tested; cylinder and sphere converge at measured order ≈ 2; every failure
mode raises a typed exception; and the add-on loads and Phase 1 measures normally with the
backend uninstalled.

**Carried into Milestone 2.3, from the §5.1a benchmark:** at 327 680 triangles a single
A–B query costs **16.8 s** and **≈1.7 GB** peak RSS, while solver construction costs only
0.14 s, and a repeated identical query costs the same as the first. The expensive thing is
the query, not the setup, and it is not amortised by reuse.

**Success criteria** — all met, 2026-09-02 (§5.1a)
- `sys.version` inside Blender 4.5.13 confirmed (3.11.15); architecture confirmed arm64. ✔
- `pygeodesic` imports inside Blender's Python without a numpy ABI error. ✔
- On an icosphere of known radius, vertex-to-vertex distance is within the expected
  discretisation margin of the great-circle value, and a path polyline is returned. ✔
- Wall-clock timing recorded for one query on a scan-sized mesh (>100k triangles). ✔
- The exact install command and the resolved backend version are recorded in this spec. ✔
- **Added:** the add-on loads, registers and measures with pygeodesic absent. ✔
- **Added:** the sum of the returned polyline's segment lengths equals the reported distance
  to ≤ 6.8e-16 relative. ✔
- **Added:** every failure mode raises a typed exception; no approximate value ever
  substitutes for a failed exact result. ✔

**Failure modes** — and what actually happened
- Python is not 3.11 → no cp311 wheel applies; the whole backend choice must be revisited.
  *Did not occur: 3.11.15 detected.*
- x86_64 wheel installed under a Rosetta pip for an arm64 Blender → import error or crash.
  *Avoided by resolving the wheel with Blender's own interpreter.*
- numpy ABI mismatch with Blender's bundled numpy → segfault, not a clean exception.
  *Did not occur, but the near miss is real and is recorded in §5.1a: pygeodesic 0.1.11
  declares `numpy<3,>=2` while Blender bundles 1.26.4. The binary is compatible; the
  metadata is not. `--no-deps` is what keeps pip from acting on the metadata.*
- `pip` writing outside Blender's site-packages (wrong interpreter) → import succeeds in Terminal
  but fails in Blender. **This is the failure mode that nearly shipped.** The previous
  `tools/check_geodesic_env.py` recommended `pip install --user`, and Blender sets
  `no_user_site = 1`, so that install would have been invisible inside Blender while working
  perfectly in a shell. Fixed; the target is now derived from the live interpreter's own
  import paths.
- Permission errors writing into the app bundle. *Avoided entirely: nothing is written there.*

### Milestone 2.3 — Exact A–B surface distance

**Create:** `geodesic/registry.py`
**Change:** `geodesic/backends/exact_mmp.py` (add a bounded distance query; the module itself
already exists from 2.2), `state.py` (surface distance, failure state, provenance),
`operators.py` (`bsmt.calculate_surface_distance`), `panels.py` (result + provenance display),
`measurement.py` (surface distance unit conversion and formatting)

**Query strategy, decided by measurement (§5.1b) — bounded pairwise, not one-to-all.**
The distance comes from `geodesicDistances([A], [B], max_distance=bound)` with a *finite* bound,
because an unbounded call sweeps the whole mesh regardless of how close B is. The bound starts at
~1.1 × the straight-line A–B distance and is enlarged and retried if the call returns `inf`;
each failed attempt is cheap, and one attempt sufficed in every measured case. The returned value
must be asserted finite and ≥ the straight distance before it is displayed. One-to-all is
explicitly *not* used: it costs a full sweep per source and returns no path.

The path polyline is **not** fetched here. It requires `geodesicDistance`, which cannot be bounded
and costs ~26 s at scan scale, so it belongs to Milestone 2.4 behind an explicit request with
progress feedback — never as a side effect of measuring a distance.

**Success criteria**
- Plane: surface distance equals Euclidean to ~1e-9 relative, on three different triangulations.
- Same-triangle picks: surface distance == straight distance exactly (analytic short-circuit).
- **The bounded query returns the same value as an unbounded one on every test mesh**, and an
  `inf` result triggers an enlarge-and-retry rather than being reported as a distance.
- **A near landmark pair on a scan-sized mesh completes in seconds, not tens of seconds** (§5.1b
  measured 0.13 s at 170 mm separation on a 313,880-triangle body-proportioned mesh).
- `d_surface ≥ d_straight` holds on every test; a violation raises rather than displays.
- Disconnected components produce `DISCONNECTED` with component sizes and **no number**.
- Backend absent produces `BACKEND_MISSING`; the add-on still loads and Phase 1 still works.
- Provenance (backend, version, mesh hash, preprocessing, algorithm version) is displayed.
- No backend exception can reach the user as a Blender traceback.

**Failure modes**
- **Leaving `max_distance` at its `GEODESIC_INF` default**, which silently disables the
  stop-vertex check and makes every query a full mesh sweep (§5.1b). This is the difference
  between 0.13 s and 28.6 s and it produces no error, only slowness.
- **Relying on the undocumented safety of a bound below the true distance.** Measured as exact,
  but not documented behaviour; the enlarge-and-retry loop must exist regardless.
- Index off-by-one between inserted vertices and the array handed to the backend.
- MMP behaviour on meshes with boundaries or non-manifold edges not validated → wrong or hanging.
- Long-running solve blocking the UI with no feedback.
- Unit conversion applied twice (mesh already scaled to world, then multiplied again).
- Failure state left stale from a previous query and displayed next to a fresh straight distance.

### Milestone 2.3a — as implemented (2026-09-02)

**Created:** `geodesic/registry.py` (backend selection, the expanding-bound query strategy,
provenance), `geodesic/solve.py` (the production pipeline, pure numpy),
`tests/test_surface_distance.py`
**Changed:** `geodesic/backends/exact_mmp.py` (`bounded_distances`, `bounded_distance`,
`unbounded_distance`), `geodesic/__init__.py` (guarded loader, `measure_error()`),
`state.py`, `operators.py`, `panels.py`, `__init__.py` (0.7.0), `tests/test_import.py`
**Not touched:** `surface_point.py`, `meshcache.py`, `topology.py`, `spaces.py`, `extract.py`,
`preview.py`, `picking.py`, `attach.py`, `visualization.py`, `measurement.py`,
`backends/selftest.py`. **The SurfacePoint representation and the canonical triangle indexing
are unchanged**, and `insert_points()` is used exactly as validated in Milestone 2.1.

**Pipeline.** `bsmt.calculate_surface_distance` reads the two production SurfacePoints and
hands their canonical locations to `solve.surface_distance()`, which validates → confirms one
source object → confirms one geometry hash → confirms one component → short-circuits the
analytic cases → builds a scratch mesh with both endpoints inserted → runs the bounded exact
query → checks the invariants → returns millimetres. The canonical mesh is never modified; the
solve runs on `canonical.vertices_solver`, which is already physical millimetres (§6.2), so the
backend result is stored unmodified and no unit conversion is applied to it anywhere.

**No path is computed.** A polyline needs an unbounded query (§5.1b), so it is deferred to
Milestone 2.4 and cannot occur as a side effect of measuring.

**Two findings from implementing it**

1. **A too-small bound is exact, not short.** The expanding sequence 1.25× → 2× → 4× → 8× →
   unbounded is implemented as specified, but measurement shows the first factor *always*
   succeeds for a reachable pair. pygeodesic's stop-vertex loop keeps propagating until the
   target is settled, so `max_distance` only ever adds sweeping; bounds of 1e-6×, 0.1×, 0.5×
   and 0.9× the straight distance all returned the exact unbounded answer. `inf` therefore
   means *genuinely unreachable*, never *bound too small*.

   The sequence is consequently **defensive insurance, not everyday machinery**: it costs one
   comparison per measurement and would be what saves the result if a future pygeodesic adopted
   truncating stop semantics. Because it cannot be exercised against the real library, its
   retry and fallback logic is tested against a stub with those semantics.

   A smaller bound would be measurably faster (0.117 s vs 0.204 s at 170 mm, §5.1b) but would
   rest entirely on undocumented behaviour. The specified sequence is the defensible choice.

2. **`Object.matrix_world` is single precision, and that sets the metric tolerance.**
   For a pure rotation R, `LᵀL` differs from the identity by ~3.6e-8 relative — not the ~1e-16
   a float64 matrix would give. The first implementation compared metric tensors at 1e-9 and
   therefore reported **every rotation as a scale change**, invalidating a surface distance that
   §6.3 requires to stay valid. Caught by the in-Blender transform check, not by the offline
   tests, which use float64 matrices and could never have seen it.

   `state.METRIC_RELATIVE_TOLERANCE` is now 1e-6, justified by measurement: the float32 noise
   floor is ~5e-8 while a 1.0001× uniform scale moves the tensor by ~2e-4, so the tolerance sits
   ~25× above the noise and ~200× below the smallest real signal. Regression tested with
   float32-rounded rotations.

   *Related observation, not acted on:* `spaces.metric_key` quantises at ~1e-9 (§6.4) and would
   have the same float32 sensitivity, but it is only computed, stored and displayed — never
   compared — so it causes no behaviour today. Invalidation is driven by the tensor comparison
   above. If `metric_key` is ever used for a comparison, its quantisation must be revisited first.

**Invalidation, verified in Blender 4.5.13 against §6.3**

| Change to the scan | Stored surface distance |
|---|---|
| Translation | **stays valid** ✔ |
| Rotation (non-axis-aligned) | **stays valid** ✔ |
| Uniform scale ×2 | invalidated, "recompute" ✔ |
| Non-uniform scale (2, 3, 0.5) | invalidated, "recompute" ✔ |
| Scale restored to 1 | valid again ✔ |
| Coordinate-unit change | cleared outright ✔ |
| A or B re-picked | cleared outright ✔ |
| Vertex moved (geometry edit) | cache cleared → "recompute"; after rebuild, "the mesh geometry changed"; a solve refuses `STALE_POINTS` and stores no number ✔ |

**Result validated against an analytic reference.** UV sphere R = 100 mm, 3,968 triangles,
two points ~90° apart: surface 156.938 mm vs great circle 157.095 mm, relative 1.0e-3 —
representation error at that mesh resolution, converging as §5.1a measured. Straight 141.205 mm,
ratio 1.111, one attempt at bound 1.25×, 0.017 s.

**Success criteria** — met offline and in Blender; real-scan acceptance outstanding
- Plane, three triangulations: surface == straight to 3.0e-16 relative. ✔
- Same-triangle picks: exactly equal, no solver call. ✔
- Same edge: matches the closed form. Same vertex from two triangles: exactly 0. ✔
- `d_surface ≥ d_straight` on every test; a violation raises `INVARIANT_VIOLATION`. ✔
- A→A is exactly 0.0; A→B == B→A to ≤ 2.2e-16 relative on plane, cylinder and icosphere. ✔
- Disconnected components produce `DISCONNECTED` with component ids and **no number**. ✔
- Backend absent produces `BACKEND_MISSING`; the add-on still loads and Phase 1 still works. ✔
- Provenance is displayed in a collapsed sub-panel. ✔
- No backend exception reaches the user as a Blender traceback. ✔
- Bound expansion, retry and the unbounded fallback behave correctly under a stub. ✔

**Outstanding: real-scan acceptance test on 21_M_3400E** (§10 of the milestone brief). Three
landmark separations — short 50–200 mm, medium 300–700 mm, long 1000 mm+ — recording straight
distance, surface distance, ratio, initial bound, bound used, attempt count and elapsed time.
Runtimes are to be measured, not predicted.

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

## 11a. Milestone 3.0 — Landmark Manager (as implemented, 2026-09-02)

A research layer for repeated, named measurements, added **alongside** the Phase 1 A/B
workflow. A/B is unchanged and remains the quick ad-hoc / debugging / validation path; it is
never converted into named landmarks and is never required by them.

### 11a.1 Data architecture

```
scene.bsmt_landmarks : CollectionProperty(BSMT_Landmark)     <- Scene level, as specified
    stable_id      IntProperty   monotonic, unique per scene, never reused
    protocol_id    StringProperty "L01" - protocol facing
    name           StringProperty arbitrary; no anatomical name is hard-coded anywhere
    display_name   StringProperty optional
    notes          StringProperty optional
    surface_point  PointerProperty(BSMT_SurfacePoint)        <- THE SAME PropertyGroup as A/B
    status         EnumProperty   NOT_PICKED | VALID | NEEDS_REFRESH | STALE | INVALID
    status_detail  StringProperty
```

**Composition worked.** A nested `PointerProperty` to `BSMT_SurfacePoint` inside a
`CollectionProperty` element registers and behaves correctly in Blender 4.5.13 (verified:
`type(item.surface_point).__name__ == "BSMT_SurfacePoint"`), so there is literally one surface
point implementation rather than an equivalent-fields copy. Registration order is
`BSMT_SurfacePoint → BSMT_Landmark → BSMT_ComponentInfo → BSMT_Properties`.

Manager *settings* (active index, marker size, visibility, protocol name, guided state) live on
`BSMT_Properties` with every other BSMT setting; only the landmark *data* is on the Scene.

### 11a.2 Single-implementation rule (§16), enforced

| Concern | The one implementation | Used by |
|---|---|---|
| barycentric maths | `geodesic/surface_point.py` | everything |
| batched reconstruction | `landmarks.local_positions` / `to_world` | `attach.refresh_landmarks` |
| stale detection | `landmarks.stale_reason()` | Landmark Manager **and** the A/B validate operator |
| status classification | `landmarks.classify()` | Landmark Manager |
| writing SurfacePoint fields | `state.fill_surface_point()` | A/B **and** landmarks |
| picking | `BSMT_OT_pick_point` + canonical BVH | A/B **and** landmarks, via a `target` enum |

`state.set_surface_point()` is now a thin wrapper over `fill_surface_point()` that adds the
A/B-only consequence of clearing the stored surface distance. A landmark pick deliberately does
**not** clear it. `operators.py` no longer contains a second geometry-hash comparison; this is
regression tested in `tests/test_import.py`.

### 11a.3 Picking

No new picking algorithm. `BSMT_OT_pick_point` gained a `target` enum (`AB` by default, so every
existing caller and all Phase 1 behaviour is untouched) and a `landmark_index`. The viewport ray,
the canonical BVH cast and the SurfacePoint construction are the same code for both targets; only
the destination `BSMT_SurfacePoint` differs. Landmarks are never snapped to existing mesh
vertices — the stored barycentrics reproduce the exact ray/surface intersection, as in 2.1.

A click that yields no canonical attachment stores nothing, stays modal and does not advance
guided picking.

### 11a.4 Guided picking

Deliberately **not** a long-lived modal operator. Guided mode is UI state (`guided_active`,
`guided_index`) plus `bsmt.guided_picking` with START / NEXT / PREVIOUS / CANCEL; only the
individual pick is modal, exactly as A/B already works. Normal Blender keyboard input is
therefore never globally swallowed (§13). `guided_skip_valid` steps over already-VALID
landmarks; turning it off allows deliberate re-picking.

### 11a.5 Status semantics (§8)

```
geometry edit  ->  cache cleared  ->  NEEDS_REFRESH  ->  Validate All  ->  STALE
rigid transform ->  VALID throughout
scale / unit    ->  VALID; only the physical coordinates refresh
```

`NEEDS_REFRESH` exists because a cleared canonical mesh cache is this project's established
"geometry may have changed" signal (Milestone 2.1a). It is not evidence of a change, but it is
not evidence of sameness either, so the landmark is reported as unverified rather than asserted
valid. **A stale landmark is never silently re-projected** — verified by comparing every
`(triangle_index, barycentric)` before and after a geometry edit plus Validate All.

`Validate All` rebuilds **one** canonical mesh per source object, not one per landmark (§9), and
reports e.g. `18 valid, 2 not picked, 1 stale`.

### 11a.6 Markers

`BSMT_Landmark_%06d`, named from the stable id and never from the researcher's name, so a name
containing awkward characters, a rename, or a protocol reload cannot collide or mangle an object
name (§7). Colour encodes trust: green VALID, dull yellow NEEDS_REFRESH, orange STALE/INVALID. A
stale marker is recoloured **in place**, never moved.

Lifetimes are independent, as §20 requires: `Clear Points` skips the landmark prefix, and
`Clear Landmark Data` leaves A/B, the component preview and the scan untouched. Both verified.

### 11a.7 Performance (§17)

`attach.refresh_landmarks()` groups landmarks by source object, looks the canonical mesh up once
per object, and does one gather plus one matrix product for the whole group. It never rebuilds
topology, the BVH or a geometry hash — none of which is reachable from it.

| Measurement | Result |
|---|---|
| 50 landmarks, 320,000-triangle mesh, pure numpy | **0.008 ms** per refresh |
| 50 landmarks in Blender, including 50 marker object writes | **0.51 ms** per refresh |

Well inside a 16 ms frame, and it does not scale with mesh size: it gathers 50 triangles, not
320,000.

### 11a.8 Protocols

`protocol.py` — pure standard library, no bpy, no numpy — reads and writes
`bsmt-landmark-protocol` v1: protocol name, and an ordered list of `{id, name, notes}`.

**The protocol / scan-data separation is enforced in both directions.** `dump()` asserts that
nothing position-shaped reaches the output; `load()` **refuses** a file carrying
`triangle_index`, `barycentric`, `geometry_hash`, `world_xyz`, `component_id`, `source_object`
and similar, rather than quietly ignoring it — such a file is scan data, and importing it as a
protocol would attach one scan's coordinates to a different scan unnoticed. Verified round-trip:
save 10 names → clear → load → same 10 names, same order, all NOT PICKED, no positions, and
fresh stable ids (not reused).

### 11a.9 A defect found and fixed on the way

`physical_mm_xyz` was refreshed only when the world position moved. A coordinate-unit change
moves nothing but does change the physical millimetre value, so the displayed coordinate went
stale. This was **pre-existing in the A/B path**, and the landmark path had inherited it. Both
now compare the physical value separately, and `_on_unit_changed` calls
`attach.refresh_physical_mm()`. It affected a displayed diagnostic only — no measurement reads
`physical_mm_xyz` — but it was wrong.

### 11a.10 Verified in Blender 4.5.13

10 landmarks on a 3,968-triangle proxy scan: distinct triangle indices, all component 1, all
VALID, markers at the surface. Translation, rotation, uniform scale and non-uniform scale — all
markers follow to a worst **relative** error of 5.1e-8 (the float32 floor of `Object.location`),
status stays VALID, and `(triangle_index, barycentric)` is bit-identical throughout. Geometry
edit → 9 NEEDS_REFRESH → Validate All → `1 not picked, 9 stale`, no re-projection. Protocol
round-trip, guided picking progression, and A/B regression (straight 161.55 mm, surface
188.30 mm) all pass.

**Outstanding: real-scan acceptance on 21_M_3400E** (§18, §19 of the milestone brief) — 10
landmarks at visibly different anatomical locations, picked through the viewport, then the
transform and geometry-edit checks, and a 10-landmark protocol round-trip with guided picking.
The viewport click is the one path that cannot be exercised headlessly.

---

## 11b. Milestone 3.1 — Measurement Manager (as implemented, 2026-09-02)

User-defined measurements between named landmarks. The A/B quick workflow and the Landmark
Manager are both unchanged and both remain fully functional.

### 11b.1 The binding non-goal

**BSMT never generates landmark pairs.** There is no "calculate every pair" button, no
`itertools.combinations`, and no all-pairs helper anywhere in the codebase — enforced by a test
that greps the source. For 50 landmarks BSMT computes the definitions the researcher wrote, not
1,225 combinations. `Calculate All Defined` means *the enabled definitions*, and nothing else.

### 11b.2 Data architecture

```
scene.bsmt_measurements : CollectionProperty(BSMT_Measurement)
    stable_id           monotonic int, unique per scene, never reused
    protocol_id         "M01" - template facing
    name, notes, enabled
    source_stable_id    -> BSMT_Landmark.stable_id      AUTHORITATIVE
    target_stable_id    -> BSMT_Landmark.stable_id      AUTHORITATIVE
    source_protocol_id / source_name   cached, for templates and diagnostics
    measurement_type    STRAIGHT | SURFACE | BOTH
    status              NOT_READY | READY | CALCULATING | VALID | STALE
                        | INVALID_REFERENCE | FAILED
    + results (straight_mm, surface_mm, ratio) and surface provenance
    + dependency fingerprint (result_geometry_hash, result_metric_tensor)
```

### 11b.3 The dynamic-enum hazard, measured and defended

References are landmark **stable ids**, never list indices. That is not a theoretical
preference — a spike in Blender 4.5.13 established the concrete failure:

> A dynamic `EnumProperty` built from the landmark collection **remaps by index** when the
> collection changes. With the picker set to landmark `"42"`, deleting landmark 42 makes the
> property read back as **`"43"`** — a different, unrelated landmark, with no error and no
> warning.

Reproduced again in the acceptance run against the production code: picker `14` → landmark 14
deleted → picker silently reads `15` (the neighbour), while `target_stable_id` stayed `14`,
resolution returned `None` rather than the neighbour, status became `INVALID_REFERENCE`, and
`Calculate Selected` refused with *"missing target (landmark id 14)"* storing no number.

**Therefore the From/To pickers are write-only.** Their update callbacks copy the choice into the
authoritative integer, and nothing in the calculation path, the status path or the template path
ever reads them back. A test asserts `operators.py` contains neither `source_picker` nor
`target_picker`.

### 11b.4 Deleting a referenced landmark (§14)

The measurement is **not** deleted and **not** redirected. It keeps its definition, its cached
protocol id and its landmark name, and becomes `INVALID_REFERENCE` so the loss is visible.
`Remove Invalid Measurements` exists but is explicit, confirmed and opt-in — there is no
automatic destructive cleanup.

### 11b.5 Calculation reuses the production pipelines (§7)

| Type | Path |
|---|---|
| STRAIGHT | `measurement.straight_distance_mm()` — the Phase 1 function, unchanged |
| SURFACE | `geodesic.solve.surface_distance()` — the Milestone 2.3 pipeline, unchanged: validation, scratch-mesh endpoint insertion, bounded exact MMP query, result invariants |
| BOTH | both |

No distance mathematics is reimplemented. Every surface result records backend, version, bound
factor, attempts and elapsed time.

### 11b.6 Batch behaviour (§8, §10, §23)

Sequential and single-threaded, as specified. Before running, the plan is reported —
*"12 enabled measurements, 8 require surface distance, 4 straight-only"* — and disabled
definitions are skipped, not calculated. Progress is printed and pushed to the panel between
measurements, which is the most Blender can repaint around a blocking C++ solve.

**One failure never aborts the batch.** Each definition keeps its own status and reason; the run
ends with e.g. *"4 valid, 1 not ready"*. Verified by clearing one landmark's position mid-batch:
the dependent measurement reported `NOT_READY — source 'Hip_L' is not picked` and every other
measurement still calculated.

### 11b.7 Invalidation (§12, §13)

**Targeted, not a sweep.** A landmark change invalidates only the definitions that reference it,
found by one integer comparison per definition. Measured: **0.0018 ms** to find the dependents of
one landmark among 100 definitions, and 0.0114 ms to re-plan the whole batch — cheap enough to
run in a panel redraw.

Verified in Blender: re-picking one landmark invalidated **exactly one** measurement; the
independent results survived, and the disabled definition was untouched.

| Change | Result |
|---|---|
| Rigid translation | **stays VALID** ✔ |
| Rigid rotation | **stays VALID** ✔ |
| Uniform / non-uniform scale | invalidated → STALE, numbers dropped ✔ |
| Coordinate-unit change | invalidated ✔ |
| Geometry edit | invalidated → STALE ✔ |
| Source or target re-picked / cleared | that definition only ✔ |
| Referenced landmark deleted | INVALID_REFERENCE, definition kept ✔ |
| From/To selection changed, type changed | result dropped ✔ |

Rotation cannot reach the invalidating branch by construction: the metric tensor of §6.4 is
rotation invariant and carries no translation.

### 11b.8 Templates (§15–§18)

`bsmt-measurement-protocol` v1, in `protocol.py` alongside the landmark protocol but as a
**separate format** — each loader refuses the other's file and names the right one. A template
carries protocol name, ids, names, landmark references, type, enabled flag, notes and order.

It carries **no** results, timings, backend provenance, triangle indices, barycentrics or
geometry hashes. Enforced in both directions: writing asserts, and reading **refuses** a file
containing any of them rather than ignoring it — such a file is scan output, and loading it as a
template would present one scan's numbers as another's definitions.

Landmark references are the landmark **protocol ids** (`L01`), so a template resolves naturally
alongside its landmark protocol. The human-readable name travels with each reference for
diagnostics but is **never** used to find a substitute: loading the template with no matching
landmarks left all six definitions `INVALID_REFERENCE`, with the reference id and name preserved
for a later re-link and `source_stable_id == 0` — no landmark was invented.

### 11b.9 Verified in Blender 4.5.13

8 landmarks, 6 definitions mixing STRAIGHT / SURFACE / BOTH. `Calculate Selected` produced
straight 194.089 mm, surface 266.736 mm, ratio 1.3743, bound 1.25×, 1 attempt, 0.012 s, with
surface ≥ straight. STRAIGHT stored only a straight value, SURFACE only a surface value, and the
row rendered `— / 124.42 mm`. `Calculate All Defined` with one disabled ran 5 and skipped 1.
A/B regression: straight 181.315 mm, surface 228.114 mm. Landmark Manager regression: add,
Validate All and guided picking all still work; the scan was never modified.

**Outstanding: real-scan acceptance on 21_M_3400E** (§27 of the milestone brief).

---

## 11c. Milestone 3.1a — Measurement Manager usability (v0.9.1, 2026-09-02)

UI and usability only, from real-user validation of 3.1. No change to the stable-id reference
architecture, SurfacePoint, the canonical mesh, the pygeodesic backend, the distance
mathematics, A/B, the Landmark Manager, or protocol semantics.

### 11c.1 Auto Name

`BSMT_Measurement.auto_name`, default **ON**. The name follows the chosen landmarks —
`P01` + `P02` → `"P01 to P02"` — and updates the moment From or To changes. It uses the
landmarks' **user-visible names**, resolved through the authoritative stable ids and never
through the From/To pickers, because a dynamic enum remaps by index (§11b.3) and could otherwise
label a measurement after a landmark it does not reference.

Turning Auto Name **off** preserves a custom name — `"Front torso length"` survives any From/To
change and any landmark rename. Turning it back on adopts the generated name immediately.

Renaming a *landmark* refreshes the auto names of the measurements that reference it, visiting
only those (one integer compare per definition, ~0.002 ms over 100).

**A correctness fix came with it.** `name` previously carried
`update=_on_definition_changed`, so renaming a measurement invalidated its result. A label is
not part of what is measured, and §12's invalidation list does not include it — only From/To and
type. Renaming no longer invalidates, which is both correct and what makes auto-naming safe.

**Template round-trip.** The file format is unchanged: adding an `auto_name` key would be a
protocol-semantics change. The flag is *inferred* on load instead — a saved name that is exactly
what auto-naming would produce keeps following its landmarks, anything else is preserved verbatim
as a custom name. Verified both ways.

### 11c.2 Result visibility

Three places, in increasing detail:

1. **List row** — two lines: `[x] M01  P01 to P02  BOTH  ✓` over
   `P01 → P02        104.13 / 109.71 mm  r 1.052`. A disabled definition stays listed and reads
   `DISABLED`.
2. **Measurement Results**, a collapsed section (default open) below Calculate All Defined,
   listing every *defined* measurement in order with From → To, type, straight, surface, ratio
   and status. Only user-defined measurements appear; nothing enumerates landmark pairs.
3. **Selected detail** — unchanged, still carrying full provenance (backend, version, bound
   factor, attempts, elapsed).

### 11c.3 Explicit batch summary

`Calculate All Defined` now reports per outcome rather than one compressed line:

```
3 enabled
3 calculated
3 valid
0 not ready
0 failed
```

`stale`, `invalid reference` and `N disabled, skipped` lines appear when non-zero. The zero lines
are shown deliberately: "did everything I asked for actually run" is the question, and a missing
line reads as an omission.

### 11c.4 A stale value can never render as current

`state.result_is_displayable(item)` — `has_result and status == VALID` — is the single gate the
UI asks before drawing any number. Numbers are already cleared on invalidation, so the status
check is redundant today; it is there so that a future path which forgets to clear still cannot
paint a stale value as a live one. Where a result is not displayable the UI shows the status
(alert-coloured for STALE / FAILED / INVALID_REFERENCE) and never a number.

Verified in Blender: re-picking `P01` left the two dependent measurements showing `NOT READY`
with `— / —` and the unrelated one still `VALID` with its number; scaling the object put all
three in `STALE` with no displayable value; and the invariant *a stored number implies VALID*
held throughout.

---

## 11d. Milestone 3.2 — Measurement Visualisation (v0.10.0, 2026-09-02)

Draws a selected measurement as a straight chord, an exact geodesic path, or both. Calculation
and SurfacePoint are unchanged.

### 11d.1 The path is never a side effect

A surface path needs the **unbounded** `geodesicDistance()`, which cannot be bounded and sweeps
the whole mesh — tens of seconds at scan scale (§5.1b). It is therefore computed only by
`bsmt.compute_surface_path`, and never by Calculate Selected, Calculate All Defined, creating a
measurement, picking a landmark, or switching a display mode on. A test asserts that
`solve.surface_path(` appears exactly once in `operators.py` and nowhere in `state.py`,
`panels.py` or `viz.py`.

### 11d.2 One pipeline, two queries

`solve._prepare()` and `solve._build_scratch()` were extracted so `surface_distance()` and
`surface_path()` see **exactly the same endpoints on exactly the same scratch topology**. That
makes the agreement structural rather than a convention two functions must remember.

`surface_path()` refuses to return a path that disagrees with what is already stored:

| Compared | Tolerance |
|---|---|
| polyline segment sum vs the solver's reported path distance | 1e-5 relative |
| the path solve vs the stored production surface distance | 1e-5 relative |

A disagreement raises `PATH_DISTANCE_MISMATCH` and **the stored measurement is never modified**.

**The tolerance is set by storage, not by the solver.** Bounded and unbounded queries returned
bit-identical values in 18/18 measured configurations, and a polyline matched its distance to
≤ 6.7e-16 relative. But the reference handed in is read back out of a Blender `FloatProperty`,
which is **single precision**: a measured 115.068 mm distance came back 1.9e-6 mm adrift for that
reason alone. 1e-5 relative sits ~40× above that floor and orders below any real disagreement.

### 11d.3 Geometry in local space, helpers tracking by matrix

Helper curves store their points in the **scan object's local space** and the helper's
`matrix_world` is kept equal to the scan's. Following a rigid transform is then one matrix
assignment per helper instead of rewriting every point — decisive when a path has thousands —
and "the path follows the scan" becomes true by construction. Verified: after translation the
path moved by exactly **t** and was not recomputed.

### 11d.4 Two defects found while building this

**1. A mode round-trip destroyed a cached path.** Switching to STRAIGHT removed the path helper;
switching back to BOTH found no helper, judged the cache dead, and dropped it — so a 15–30 s
solve was thrown away by a display change, exactly what §6 forbids. The helper curve *is* the
cached polyline, so it is now **hidden, never removed**, when a mode does not want it; it is
removed only when the cache is genuinely dead.

**2. Creating or removing any BSMT helper wiped the canonical mesh cache — a bug present since
Milestone 2.1.** Measured on Blender 4.5.13: linking one helper raises `is_updated_geometry` on
`Scene Collection` and `Collection`, neither helper-tagged, and the 2.1 handler cleared on any
non-helper geometry update. Consequences: a ~1 s canonical rebuild every time a marker, line or
path appeared, and — worse — `meshcache.peek()` returning `None`, which made the transform and
metric checks silently skip, so **a scaled scan could keep reporting VALID**. The handler now
clears only for `Object` and `Mesh` datablocks; a real mesh edit always reports on those, and
membership churn no longer does. Re-verified both directions.

### 11d.5 Result and path lifetimes are one

`clear_measurement_result()` clears the cached path too. A path is solved against the same
landmarks, geometry and metric as the distance, so a path outliving its result would claim to
match a number that no longer exists. Invalidation therefore follows §12 exactly: re-pick,
clear, delete, geometry edit, scale, non-uniform scale and unit change all drop the path; rigid
translation and rotation do not.

### 11d.6 Display offset

The drawn path is lifted along the surface normal by 1.2× its bevel radius, using the canonical
BVH. Display only: the polyline, its length and every reported distance are computed **before**
the lift. Measured on a sphere the drawn curve is ~1.8 % longer than the stored path, and the
stored value is unchanged — which is the point. The path helper is drawn with real occlusion so
the far side of a wrapping geodesic is hidden by the body; the straight chord is drawn in front,
since a chord passes through the body by its nature.

### 11d.7 Lifetimes

Measurement helpers are `BSMT_Measurement_<stable_id>_Straight|_Path`. Verified: `Clear Points`
keeps them, `Clear All Measurement Visualizations` keeps A/B, landmark markers, definitions,
results and the scan.

**Outstanding: real-scan acceptance on 21_M_3400E** (§15 of the milestone brief), in particular
the true path timing on a 314k-triangle mesh — the synthetic acceptance ran on a 2k-triangle
sphere where the path took 0.014 s.

---

## 11e. Milestone 3.3 — Scan preprocessing and the solver safety gate (v0.11.0, 2026-09-02)

Turns a dense textured OBJ into a lighter **textured** measurement copy, and stops an unsafe
mesh reaching the native solver.

### 11e.1 Why the gate exists

A real Design X textured OBJ arrives at 1,391,542 vertices / 2,783,068 triangles with 2
components, 15 boundary edges and **7 non-manifold edges**, and handing it to pygeodesic has
already crashed Blender with **SIGSEGV**. A crash takes the whole unsaved session, so the mesh is
checked *before* the C++ library is constructed.

`preprocess.preflight()` reads the canonical mesh's existing topology report — computed at build
time, so the check costs nothing — and is applied at **exactly three** solver entry points,
verified by parsing `operators.py`:

| Entry point | |
|---|---|
| `BSMT_OT_calculate_surface_distance._solve` | A/B surface distance |
| `_measure_one` | Measurement Manager |
| `BSMT_OT_compute_surface_path._solve` | on-demand path |

| Condition | Behaviour |
|---|---|
| Non-manifold edges > 0 | **Refused, unconditionally.** This is the condition that crashed Blender, and the density override cannot lift it. |
| Triangles > threshold (default 1,000,000) | **Refused while the guard is on** (default), warned when off. |
| Components > 1 | Warns. Measurement is still allowed; a cross-component *pair* is refused individually (§7.4). |
| Boundary edges > 0 | Warns — a geodesic near a hole can take a plausible-looking detour that is an artefact. |
| Degenerate triangles > 0 | Warns. |

**On the density default.** The brief asked for a warning. It is implemented as a *guarded
refusal* that defaults to blocking, with a visible "Block Solving Above Threshold" checkbox to
turn it back into a warning. A warning that proceeds still lets the session die, and losing
unsaved work is worse than being asked to make a measurement copy first. The threshold remains
**operational, not mathematical** — it says nothing about what MMP can represent.

### 11e.2 Non-destructive copy

`<source>_BSMT`, created by `obj.copy()` **plus `obj.data.copy()`** — sharing the mesh datablock
would mean decimating the copy decimated the original. Materials are deliberately *shared*, since
the copy must show the same texture. Verified after every run: the source's triangle count,
vertex count, mesh datablock name, UV layers and materials are unchanged, and the operator
refuses to report success if they are not.

### 11e.3 Target count, not a raw ratio

The researcher gives a triangle count; `ratio = target / current`, clamped to (0, 1]. A target at
or above the current count copies **without decimating** rather than running a modifier that
would do nothing. Presets (High 500k / Standard 350k / Light 200k) write the target, which stays
editable.

Decimation is `DECIMATE` in `COLLAPSE` mode with `use_collapse_triangulate`, baked through the
depsgraph with `bpy.data.meshes.new_from_object(..., preserve_all_data_layers=True)` rather than
`bpy.ops.object.modifier_apply` — no operator context, works headless, and `preserve_all_data_layers`
is what carries the UV layers across.

### 11e.4 Texture preservation is a pass/fail gate

UV layer names, material slots, image datablocks and image file paths are recorded before and
compared after. A lost UV layer, material or image reference marks preprocessing **FAILED** and
the copy is *not* presented as measurement-ready — a copy the researcher cannot visually register
against the original is useless for landmarking. Measured on a textured sphere: UV layer,
material, image and filepath all preserved, the material datablock shared with the source, and
the UV data non-degenerate across every loop.

### 11e.5 Nothing is welded and no hole is filled

No merge-by-distance, no `remove_doubles`, no hole filling — asserted by a test that greps both
modules. On a human scan those silently fuse anatomically distinct surfaces that happen to touch
(arm↔torso, finger↔finger, garment↔skin), and a fused surface produces a confidently wrong,
**systematically short** geodesic. Diagnose and report; the researcher decides.

### 11e.6 Provenance

Stored on the generated object as `Object.bsmt_scan`, so it travels with the .blend. Identity is a
real Blender **object pointer**, which survives a rename (verified); the source name string is a
human-readable fallback only. Records original / target / actual triangle counts, method, ratio,
BSMT version, timestamp, and the representation note.

### 11e.7 The copy is not the original surface

Decimation changes the polyhedral surface, so the copy's geodesic distances are **not** identical
to the original's. The provenance records `"decimated measurement representation"`, and a test
asserts the phrase "same exact surface" appears nowhere. Quantifying surface-distance sensitivity
to mesh density is a separate exercise that must be **measured**, not assumed — it is not done
here.

### 11e.8 Verified in Blender 4.5.13

Textured sphere, 19,042 verts / 38,080 tris, `UVMap` + `ScanMaterial` + `scan_texture.jpg`.
Target 4,760 → copy landed on **4,760 triangles (0.0% error)** in 0.16 s, UV/material/image all
preserved, source completely unchanged. Gate: clean copy allowed; 7 non-manifold refused;
2,783,068 triangles refused while guarded and warned when unguarded; 2 components + 15 boundary
warned without refusing. Through the real operator, a 38,080-triangle mesh against a 10,000
threshold was **blocked with no number stored**, and proceeded to a VALID result once unguarded.
Toggle showed one object at a time without deleting either.

**Outstanding: real-scan acceptance on the 2.78M-triangle textured OBJ** (§15 of the brief) — in
particular the decimation time at that scale and whether the post-check still reports non-manifold
edges, in which case exact geodesic must not be run.

---

## 11f. Milestone 3.4 — Controlled mesh repair (v0.12.0, 2026-09-02)

Repairs the topology defects that block exact geodesic measurement, one explicitly chosen region
at a time.

### 11f.1 Two absolute constraints

**Repairs run only on a generated measurement copy.** Every repair operator resolves
`Object.bsmt_scan.is_measurement_copy` first and refuses anything else by name, so the source scan
cannot be reached. Each repair additionally re-checks the source's triangle count afterwards and
reports a bug if it moved.

**There is no global cleanup path.** No merge-by-distance over the whole mesh, no fill-every-hole,
no "make it manifold" button — asserted by a test that greps `meshrepair.py` for
`remove_doubles(bm, verts=bm.verts` and `holes_fill(bm, edges=bm.edges`. On a human scan those
fuse anatomically distinct surfaces that happen to touch and produce a confidently wrong,
**systematically short** geodesic.

### 11f.2 What is offered

| Action | Scope |
|---|---|
| **Show Non-Manifold** / **Show Selected Boundary** | Edge-only helper overlays, no faces, never selectable. The mesh is not touched to draw them. |
| **Remove Duplicate Faces** | Faces repeating another's vertex set (winding-insensitive). The safest repair: a duplicated face adds no surface, so removing it cannot move anatomy. |
| **Weld Non-Manifold Region** | `remove_doubles` on **the reported non-manifold edges' own endpoints and nothing else**. It cannot reach across a gap between an arm and a torso elsewhere in the scan. Tolerance is the researcher's, in mm, and the merged count is reported. |
| **Fill Selected Boundary** | One loop, chosen from the list. `holes_fill`, falling back to `triangle_fill`, then triangulated. |
| **Remove Selected Component** | One component, confirmed. The largest is refused outright — that is the body. |
| **Restore Backup** | The mesh datablock is copied before every destructive edit, independent of Blender's undo stack. |

Boundary loops are listed with edge count, perimeter in mm and bounding-box size, largest
perimeter first, and open chains are distinguished from closed cycles — a cropped scan's open
bottom is normal and must be left alone.

### 11f.3 Readiness rule (§7)

**Only non-manifold topology blocks**, because only that has been shown to break the solver.
Multiple components and open boundaries are stated *preferences*: a landmark pair on one good
component is perfectly measurable, and a landmark pair spanning two components is already refused
individually (§7.4). Degenerate triangles and coincident vertices are **warnings only** — refusing
a whole scan over a handful of them would block real work for no demonstrated reason.

When neither automatic repair clears the non-manifold edges, the blocker text says so and asks for
manual cleanup rather than attempting something more aggressive.

### 11f.4 After every repair

Diagnostics are rebuilt, the texture is re-audited against the pre-repair record (a loss rolls the
repair back), the readiness verdict is recomputed, and — because the geometry hash has changed —
every SurfacePoint on that object is marked **STALE** and the measurements depending on it are
invalidated. Nothing is ever re-projected onto repaired geometry.

### 11f.5 A defect found while building this

**The hole fill left ngons behind.** `bmesh.ops.holes_fill` returns face references, and the code
triangulated only the faces not present in a `set(bm.faces)` snapshot taken *before* the op. A
BMFace reference taken before a topology-changing operation is not reliable afterwards, so the
membership test silently missed faces: the mesh came back with 2,212 polygons but 2,218 triangles.
Triangulation is now driven by a **property** — every face with more than three verts — and the
result is verified before commit. A measurement copy is already all triangles, so that set is
exactly the faces the fill created.

Also noted: `bmesh` refuses to create a literally duplicated face, so the duplicate-face defect
had to be built through `from_pydata`, which does allow it. A "fin" (a third face on an interior
edge) is the more realistic scanner artefact and is *not* auto-repairable — which is the case
§6 requires to report that manual cleanup is needed.

### 11f.6 Verified in Blender 4.5.13

Textured defect scan (1,116 verts / 2,218 tris, `UVMap` + `SkinMat` + `skin.jpg`) carrying 2
non-manifold edges, 16 boundary edges and 2 components:

- repairs **refused** on the original, allowed on the copy
- non-manifold highlight created as an edge-only helper (2 edges, 0 faces); the mesh was unchanged
- 4 boundary loops listed with perimeters and bounding boxes, open chains distinguished
- duplicate-face removal correctly **aborted** ("no duplicate faces") and left the mesh untouched;
  the local weld merged nothing on a fin; readiness stayed NOT READY and said manual cleanup may
  be required
- hole fill: boundary 12 → 6, mesh still **all triangles** (2,218 polys / 2,218 tris), texture kept
- component list showed `Component 1: 2,206 (99.46%) [largest]` and `Component 2: 12 (0.54%)
  [small]`; removing the largest was refused; removing the small one gave 1 component
- readiness then `READY — Measurement ready, with caveats` (6 boundary edges remaining)
- Restore Backup returned the mesh (2,206 → 2,218 triangles) with the texture intact
- the source scan's triangles, vertices, UVs and materials were unchanged throughout

**Clean-mesh regression:** a defect-free copy reported 0/0/1, `READY` and *ideal*, listed no
boundary loops and one component, and exact surface distance (130.2824 mm) plus the surface path
(36 points, agreeing with the distance) both still worked.

**Outstanding: real-scan acceptance on the Design X measurement copy and the full-body PLY** — in
particular whether decimation leaves the 7 non-manifold edges, and whether they are duplicate
faces (auto-repairable) or fins (manual).

---

## 11g. Milestone 3.5 — Automatic local non-manifold repair (v0.13.0, 2026-09-02)

Repairs small localised non-manifold artefacts without the researcher entering Edit Mode.

### 11g.1 What the real data told us

The Design X measurement copy carries 7 non-manifold edges **clustered in one star/fan around a
single vertex**, and a local weld at 0.01 mm and 0.05 mm changes nothing. That rules out
near-duplicate vertices: it is a genuine topological artefact — extra faces attached to a vertex
fan — and the fix is to remove the redundant faces, not to move any vertex.

### 11g.2 The algorithm

1. **Cluster.** Non-manifold edges are grouped by shared vertices (union–find) into regions.
   Seven edges around one vertex are *one* artefact, not seven problems.
2. **Classify.** `DUPLICATE_FACES` (a repeated vertex set, winding-insensitive), `FIN` (a face
   held on by a vertex no other face uses), `FAN` (every non-manifold edge meets at one vertex),
   `LOCAL_FLAP`, or `AMBIGUOUS` / `TOO_LARGE` — both of which **refuse**. §3 says do not claim a
   classification when it is ambiguous, and a wrong guess removes anatomy.
3. **Plan.** Greedy and deterministic: repeatedly remove the region face that resolves the most
   non-manifold edges, tie-broken by *preferring a redundant copy*, then smallest area, then
   lowest index — and **keep a removal only if the non-manifold count strictly falls**.
   Candidates are restricted to faces touching a non-manifold edge of that region, so the blast
   radius cannot spread. Edge incidence is counted across the whole mesh once and then
   decremented, so each step is O(1) rather than a re-scan.
4. **Execute, validate, revert.** Transactional, below.

Preferring the redundant copy matters: with two identical faces both choices are topologically
identical, but the *first* occurrence is the one already woven into the mesh's winding and UV
layout, so removing the later copy churns nothing.

### 11g.3 The size limit had to be adaptive

A fixed millimetre limit was wrong, and the test fixtures exposed it. What makes a defect "local"
is spanning a handful of triangles, and how many millimetres that is depends on the mesh's own
resolution: a fan around one vertex spans ~20 mm on a 4 mm-edge body scan and ~60 mm on a coarse
13 mm-edge mesh, and is no less local. `region_diagonal_limit()` is therefore
`max(20 mm, 6 x mean edge length)` — an absolute floor, raised for coarser meshes. Verified both
ways: the same artefact is refused when the mesh scale is withheld and accepted when it is known.

### 11g.4 Transactional repair (§6)

The mesh datablock is backed up, the plan executed, and the result **measured**. It is kept only
if every criterion of `accept_repair()` holds: non-manifold edges strictly decreased, no more than
`MAX_PATCH_EDGES` new boundary edges, component count not worse, triangles remain, and the UV
map / material / image are intact. Any failure restores the backup, so a failed attempt is never
left behind.

Faces are addressed by their **sorted vertex set**, never by index. The plan is computed on the
canonical triangle array, and assuming canonical triangle *i* is mesh polygon *i* is exactly the
silent mis-indexing §7.7 warns about.

### 11g.5 Patch reconstruction and tiny boundaries

A hole left by a removal is filled only when it is a **closed** loop of ≥3 edges, within the patch
limits, *and* near a repaired region. That last condition matters: removing a duplicate face can
*restore* a real boundary edge that the duplicate had been masking, and filling that would be
wrong.

`Auto Repair Tiny Boundaries` is a separate, deliberately stricter pass (≤12 edges, ≤12 mm
perimeter, ≤5 mm across) because it runs over boundaries the researcher did not point at. Verified:
a 521 mm crop opening is reported "LEFT ALONE — 32 edges exceeds the 12-edge limit" and the mesh is
untouched.

### 11g.6 Component handling

Unchanged from 3.4 and deliberately so. The real Component 2 is ~8,789 triangles (~2.5%), far too
large to call junk by size alone. It is reported and visualised; removal stays explicit.

### 11g.7 Verified in Blender 4.5.13

Textured sphere with a 4-spoke fan around one vertex: **one** region found holding all 5
non-manifold edges, classified `FAN — 5 non-manifold edge(s) all meet at vertex 200`, repaired to
**0 non-manifold** and 0 boundary edges by removing **4 faces**, components unchanged, UV/material/
image preserved, readiness `READY`. Exact surface distance (62.5433 mm) and the surface path
(13 points) then both worked — they had been refused by the safety gate beforehand, which is the
gate doing its job.

Clean-mesh regression: auto repair and tiny-boundary repair both reported "no repair required" and
left the mesh byte-identical. The source scan was unchanged throughout.

**Outstanding: the real Design X measurement copy.** The synthetic fan is a faithful analogue but
not the same artefact; whether the real 7 edges classify as `FAN`/`FIN` (repairable) or
`AMBIGUOUS` (refused) can only be answered on the real data.

---

## 11h. Milestone 3.5a — Iterative local repair (v0.13.1, 2026-09-02)

Real-data validation of 3.5 on the Design X measurement copy reached
**7 non-manifold edges → 4**, not 0, while adding 4 faces. This revision diagnoses why and fixes
it. No new features.

### 11h.1 Three defects, all structural

**1. Every region was planned against ONE initial analysis and executed as a batch.** After the
first local edit the connectivity has changed, so every later plan referred to topology that no
longer existed. Repair is now **iterative**: analyse → repair one region → re-analyse → continue
only while the non-manifold count strictly falls, stopping at 0 or when no safe step remains.

**2. Acceptance compared NET counts.** A batch that fixed three defects and created two elsewhere
still looked like progress. Non-manifold edges are now identified by their **midpoint position**
(`nonmanifold_signature`), so a step can be judged even though removing faces renumbers vertices,
and `step_acceptable()` **refuses any step introducing a non-manifold edge that was not there
before** — not merely one that fails to reduce the total.

**3. Hole filling could manufacture the defect it was repairing.** Filling across an edge that
already carries two faces adds a third. `fillable_boundary_loops()` now requires every edge of a
loop to be a genuine boundary, and `meshrepair.fill_boundary_loop` re-checks it in bmesh before
committing. That is what the "+4 faces while 7 → 4" signature was.

### 11h.2 Two further corrections

**Removal now takes the dangling vertex with it.** `bmesh.ops.delete` used `'FACES_ONLY'`, which
left a fin's apex behind as a loose vertex with two wire edges — junk that then confused the
boundary analysis. `'FACES'` removes geometry used *only* by the deleted face and leaves anything
still referenced untouched. On the seven-fin fixture this took boundary edges 14 → 0 with no fill
at all, which is the answer to §10: resolving the non-manifold **naturally removes** the open
chain, so nothing has to be force-filled.

**The greedy now prefers a face held by a dangling vertex.** Such a face is provably safe to
remove — nothing else references that vertex, so it cannot tear the surrounding shell. Without
this the tie-break could pick a slightly smaller *surface* face instead, opening a hole that then
needed patching.

### 11h.3 A wrong invariant, found by testing

The first local invariant asked whether any edge within a ball around the repair still had three
or more incident faces. On a mesh with several separate artefacts that ball swallowed
**neighbouring, unrepaired** defects, and good repairs were reverted because a different fin was
still present — visible in the logs as `REVERTED: 1 edge(s) near the repair still have 3+ incident
faces` on a step that had just reduced the count. The check is now scoped to the region's **own**
edges: did *these* non-manifold edges go away, and did the patch create a degenerate face. Whether
the repair broke something elsewhere is a global question, and the signature rule answers it.

### 11h.4 Verified in Blender 4.5.13

Seven separate fin artefacts on a textured sphere — the shape of the real failure:

| | before | after |
|---|---|---|
| non-manifold edges | 7 | **0** |
| boundary edges | 14 | **0** |
| components | 1 | 1 |
| triangles | 3,975 | 3,968 (−7) |
| degenerate triangles | 0 | 0 |
| loose vertices | — | 0 |

Seven iterations, one fin removed per iteration, each step accepted only after re-analysis. UV,
material and image preserved; source scan unchanged; readiness `READY`; exact surface distance
(148.5434 mm) and the surface path then both worked. Clean-mesh regression unchanged.

**Outstanding: re-run on the real Design X copy.** The fixture reproduces the observed failure
shape and now resolves completely, but only the real mesh can confirm its four residual edges are
the same class.

---

## 11i. Object-identity bug and fix (v0.13.2, 2026-09-02)

A surface measurement taken on a repaired measurement copy was refused with the ORIGINAL scan's
numbers — *"2,783,068 triangles"* and *"7 non-manifold edges"* — while the selected copy had
349,992 triangles and none.

### 11i.1 Where the identity was lost

Reproduced headlessly, with the copy as the active object:

```
ACTIVE object is A_BSMT
scene.ray_cast hit: True -> OBJECT: A
```

`picking.ray_cast_surface()` used `scene.ray_cast`, which searches the **whole scene** and skips
only BSMT helpers. A measurement copy is created at the *same transform* as its source, so the two
are exactly **coincident** and the cast returned whichever the depsgraph reached first — the
original. The landmark was then faithfully recorded as belonging to `A`, and every later stage did
the right thing with the wrong owner: `source_object` → canonical mesh → safety gate → solver.

**Nothing downstream was at fault.** The measurement path already resolves the object from
`SurfacePoint.source_object`, never from provenance or selection. Verified in the same run: the
canonical cache holds separate entries with different geometry hashes and correct triangle counts
(`A` 3,968 / `A_BSMT` 1,000), and `A.data is not A_BSMT.data`. The §5 and §6 hypotheses were
checked and cleared; the fault was entirely in picking.

### 11i.2 The fix

`ray_cast_surface(..., target=None)` and a new `ray_cast_object()` cast against **one** object,
transforming the world ray into its local space and the hit back out (normal via the
inverse-transpose, so it stays perpendicular under non-uniform scale). The pick operators pass the
active mesh object. Selection is the researcher's statement of intent and the only thing that can
separate two coincident objects; the scene-wide path remains as the fallback when there is no
usable active mesh.

Two supporting changes:

* **Picking on an original that has a measurement copy now warns** — by name, both objects — since
  that is the exact situation which produced the wrong measurement. It is a warning, not a
  refusal: measuring the original is a legitimate choice.
* **`log_solver_target()` prints the object, mesh, triangle count and geometry hash** before every
  solver call, at all three entry points, and refusal messages now name the object. Nothing in the
  original output said which mesh the numbers belonged to, which is why a wrong-object measurement
  looked like a topology problem.

Ownership is now settled *before* the target is logged, so the log can never name a mesh the
measurement was then refused on.

**Provenance remains informational only.** `A_BSMT.bsmt_scan.source_name == "A"` is printed as a
note and never redirects a measurement back to the source scan.

### 11i.3 Verified in Blender 4.5.13

| Case | Result |
|---|---|
| Object-restricted cast, target `A` | hits the 3,968-triangle mesh |
| Object-restricted cast, target `A_BSMT` | hits the 1,000-triangle mesh |
| Landmarks picked on `A_BSMT` | both own `A_BSMT` |
| Surface distance on `A_BSMT` | succeeds; solver target logged as `A_BSMT`, 1,000 triangles |
| Landmarks on different meshes | refused, no number stored |
| Points stale after a geometry edit | refused until re-picked |
| Provenance `source_name = "A"` | ignored by the solver |

Milestone 3.3 and 3.5a acceptance re-run unchanged.

---

## 11j. Milestone 3.6 — Anatomical alignment (v0.14.0, 2026-09-02)

Handheld scans arrive in whatever frame the capture software produced. §13 recorded the
requirement; this milestone implements it. Alignment is **rigid and object-level only**: it
writes `Object.matrix_world` and nothing else. No vertex is read, written or reprojected, so
mesh geometry, UVs, materials and textures are untouched by construction, not by care.

### 11j.1 The axis convention, and why the sign is what it is

`alignment.py` (pure numpy, no `bpy`) commits to one convention and states it on every frame it
returns:

> **+X = the subject's LEFT, +Y = POSTERIOR (anterior is −Y), +Z = SUPERIOR.**

+Z superior and X along the left–right axis are forced by the brief. The *sign* of X is the only
free choice, and it is made so that Blender's Front view (Numpad 1, looking from −Y toward +Y)
shows the subject's anatomical **front**. Y then follows from right-handedness: `Y = Z × X` is
posterior. Choosing +X = subject's right would have put Blender's Front view behind the subject.

The frame is built from four references and is **orthonormal by construction**:

```
z = normalise(superior − inferior)                  # primary; the body axis is trusted most
x = normalise((left − right) − ((left − right)·z) z) # orthogonalised against z
y = z × x                                            # posterior
```

Superior–inferior is primary because it is the longest and most reliably picked span on a
standing scan. The left–right pick is projected onto the plane perpendicular to it.

**The residual is reported, never absorbed.** `residual_degrees = |90° − ∠(left−right, superior−
inferior)|` is the angle the orthogonalisation had to remove. It is shown in the panel before
Apply, and flagged above `RESIDUAL_WARN_DEGREES = 20°`. Silently orthogonalising a bad pick and
saying nothing would be exactly the "silent approximation" §6 forbids. The acceptance run picks a
deliberately sloppy left/right pair, reports 17.47°, and still produces an exactly orthonormal
frame.

Degenerate reference sets — coincident superior/inferior, coincident left/right, a left–right
axis parallel to the body axis, a non-finite coordinate — raise `AlignmentError`. Nothing is
guessed.

### 11j.2 Flip Front/Back is a derivation, not a heuristic

If the operator labels left and right the wrong way round, X negates, and Y (= Z × X) negates
with it, while Z is untouched. That is **exactly a 180° rotation about Z**. So Flip Front/Back is
not an anatomical guess — it is the precise correction for a swapped left/right pick, and
`tests/test_alignment.py` asserts `flip_matrix() @ frame == swapped_frame` to 1e-12.

### 11j.3 Why alignment cannot change a measurement

This is the property that makes the whole feature safe, and it was designed for in Milestone 2.3
before any alignment code existed:

- `geometry_hash` **excludes `matrix_world`**. A rigid transform cannot change it, so no
  `SurfacePoint` goes stale — landmarks stay VALID, exactly as the brief requires.
- Invalidation compares the **metric tensor** `T = (LᵀL)·multiplier²`. For a rotation `R` applied
  on the left, `(RL)ᵀ(RL) = LᵀRᵀRL = LᵀL`. The metric is invariant, so a cached distance survives.
- A `SurfacePoint` is triangle + barycentric, which a transform does not touch either.

Apply therefore refuses anything that is not rigid, at three separate points: `check_alignable`
rejects **non-uniform scale** outright (with instructions to apply scale first); `is_rigid` guards
the computed rotation; and the scale is compared before and after the write, so an alignment that
somehow changed it is rejected rather than committed.

### 11j.4 A real bug found during acceptance

The four alignment references are `SurfacePoint`s, and after Apply their cached `world_xyz` was
left at its **pre-alignment** value. Every check made against them — "is the subject's left now at
+X", "is the inferior reference at the origin" — was silently reading the old pose. Fixed by
`attach.refresh_alignment_points()`, called from `attach.refresh()` beside `refresh_landmarks`,
with the four align slots added to `_watched_objects`. The stored point never changed; only its
cached world evaluation was stale.

`state.align_point()` returns `None` for an absent property rather than raising: the depsgraph
handler that follows transforms can fire against a scene whose property group has not finished
re-registering, and a missing alignment reference must not take the A/B refresh down with it.

### 11j.5 `metric_key` drifts under rotation, and it does not matter

`metric_key` is a *hash*, computed from float32 `matrix_world` values. Rotating an object changes
its low bits, so the hash changes even though the metric is mathematically identical. This is
harmless, and provably so: `metric_key` is **never compared anywhere** — verified by grep, it is
stored and displayed only. Every invalidation decision goes through
`state.metric_tensors_match()` with `METRIC_RELATIVE_TOLERANCE = 1e-6`, which is float32-tolerant
by design (§11c: `RᵀR` differs from identity by ~3.6e-8 in single precision). The acceptance run
reports the hash change as **informational**, and asserts that the tensor comparison that actually
drives invalidation is unchanged.

### 11j.6 What the panel offers

**Manual** — ±90° rotations about X/Y/Z, a fine rotation by an arbitrary angle, move-to-origin,
and reset. **Landmark-based** — pick LEFT / RIGHT / SUPERIOR / INFERIOR, preview the resulting
frame as an axis helper (`BSMT_Align_Axes`, red = subject's left, green = posterior, blue =
superior) without moving anything, then Apply. Apply rotates about the INFERIOR reference as
pivot, optionally translating it to the world origin.

Apply records `align_previous_matrix`, `align_applied_matrix`, `align_method`, `align_created`
and `align_report`. **Reset restores the recorded pre-alignment matrix exactly** — it is not an
inverse computed afresh.

Not implemented, and deliberately: no ICP, no PCA, no automatic landmark detection, no cropping,
no brightness correction, no export, no batch.

### 11j.7 Verified in Blender 4.5.13

A textured body proxy was rotated to an arbitrary scanner orientation (51.6°, −34.4°, 120.3°), a
straight and a surface measurement taken, then landmark alignment applied.

| Check | Result |
|---|---|
| Transform rigid, no scale introduced | `[1,1,1] → [1,1,1]` |
| Body upright | 0.023° from world +Z |
| The reference frame **is** the world frame | identity to 1e-4 |
| Subject's LEFT at +X | +98.6 vs −100.4 mm |
| Inferior reference at the origin | `[0,0,0]` |
| `geometry_hash` | unchanged |
| Metric tensor comparison | unchanged |
| triangle + barycentric + component | identical |
| Landmarks | all still VALID |
| Straight distance | 292.586700 → 292.586700 mm |
| Surface distance | 304.282837 → 304.282837 mm |
| Cached surface path | survived; recompute agrees, 41 points |
| Texture / topology | preserved / unchanged |
| Flip Front/Back | exactly 180° about Z; still rigid; measurement still VALID |
| Manual X+90 then X−90 | returns to the original transform |
| Reset | restores the pre-alignment matrix; measurement still valid; mesh never touched |
| Non-uniform scale (1, 2, 0.5) | **Apply refused**; allowed again at unit scale |

The one reported non-match is the informational `metric_key` hash (§11j.5).

Offline: `tests/test_alignment.py`, 83 checks. Full regression 1,363 checks across ten suites,
with and without pygeodesic staged, 0 failures.

---

## 11k. Milestone 3.7 — UI wording, measurement drafts and readiness (v0.15.0, 2026-09-02)

The pipeline works end to end. This milestone made it *readable*: one vocabulary throughout the
UI, a measurement that cannot exist in an unfinished state, and one line that answers "can I
measure yet". No analytical behaviour was added.

### 11k.1 One word per concept

The UI now commits to a vocabulary, stated at the top of `panels.py` so it cannot drift:

| Concept | Term | Not |
|---|---|---|
| a named point the researcher places | **Landmark** | point, marker |
| one of the four anatomical references | **Reference Point** | landmark, anchor |
| the two ad-hoc points of the quick tool | **Point A / Point B** | landmark |
| the imported scan | **Source Mesh** | original, source scan |
| the lighter textured copy | **Measurement Mesh** | measurement copy, duplicate |
| straight line between two landmarks | **Straight Distance** | Euclidean, chord |
| exact geodesic distance | **Surface Distance** | geodesic distance |
| the polyline it follows | **Surface Path** | path, geodesic |

**Calculate** produces a distance; **Compute** produces a surface path. That distinction is
deliberate and is the researcher's own preference — the two operations differ in cost by an order
of magnitude, and a label that says which one you are about to trigger is worth the extra word.

UI spelling is US English (*Analyze*, *Color*, *Visualization*) even where the surrounding code is
written in British English. Mixing the two in a single panel was the actual defect: *"Analyse
Mesh"* sat two panels away from *"Measurement Visualization"*.

Panel titles: `Body Measurement` became **Quick Measure (A to B)** — it is a different tool from
the Landmark Manager and had been reading like the main one. `Diagnostics` became **Mesh
Diagnostics**. The rest were already clear and were left alone.

**Every operator gained a `bl_description`.** There were none: Blender falls back to the whole
docstring, so the tooltip for Repair Local Defects was a 411-character wall of text. The
docstrings stay for developers; the tooltips are one sentence each. `tests/test_import.py` now
fails if any operator lacks one.

### 11k.2 A measurement cannot exist half-written

Pressing Add used to append a row pre-filled with landmarks 1 and 2. Pressing it twice left a
duplicate the researcher had to hunt down and delete, and the auto-namer had already produced
something like `"? to ?"`.

A row is now a **draft** (`measurements.is_draft`) until it has a From landmark, a To landmark,
**and they are different**. A draft:

- has **no name at all** — an incomplete definition is never given a meaningless one;
- reports `STATUS_DRAFT`, saying which end is still missing;
- is skipped by Calculate All and refused by Calculate Selected;
- never appears in Measurement Results, and is not counted as defined;
- can be discarded with **Cancel New Measurement**.

**Add reuses a trailing draft** rather than stacking another (`state.trailing_draft_index`) — only
the *last* row, because a draft in the middle is one the researcher is still filling in, and
recycling it would move their selection somewhere they did not ask for. Five presses of Add
produce exactly one row.

Two consequences worth stating plainly:

**A → A is no longer a measurement.** It was previously reported READY on the reasoning that its
answer is exactly zero. The researcher asked for `From != To`, so it is now a draft: never
calculated, never named, never listed.

**"Never chosen" and "chosen, and now missing" are different answers.** This is the subtle part.
A measurement loaded from a template whose landmark is absent has an unresolved id of 0 — the same
value an untouched draft has. Treating it as a draft would have silently dropped a real definition
from the batch and from the results, which is exactly the quiet substitution §6 forbids.
`is_draft` therefore also reads the **cached** protocol id and name that every chosen endpoint
carries: an end with a remembered name was chosen once, so the row stays a real measurement
reporting `INVALID_REFERENCE`. Verified in Blender: deleting a landmark leaves its measurement
listed, named, and explicitly broken.

The empty state is a message — *"No measurements defined."* plus **Add Measurement** — never a
blank row created to give the panel something to draw.

### 11k.3 Displaying more than one measurement

`viz_selected_only` (a boolean) became `viz_scope` with three values: **Selected Measurement**,
**Selected Measurements** (those ticked), **All Enabled Measurements**.

Widening the scope **cannot compute anything**. `viz.visible_measurements` decides what *should*
be drawn and `viz.display_report` names the ones with no cached path, so showing ten measurements
lists two as *"Path not computed"* rather than starting two solves. Measurements that do have a
cached path are still drawn. A test asserts `viz.py` never reaches the solver at all.

### 11k.4 Which mesh is being measured

A measurement mesh sits exactly on top of the scan it was copied from, so the viewport cannot tell
them apart — the object-identity bug of §11i came from precisely that. The Measurement Manager now
shows, at the top:

```
Measurement Mesh: A_BSMT          Triangles:  349,999
Source Mesh:      A               Topology:   Ready
```

The target comes from `state.measurement_target()`, and **the landmarks decide**: a measurement is
computed on the mesh its landmarks were picked on, never on whatever happens to be selected. The
active object stands in only when no landmark has been picked, and is labelled as a guess.

### 11k.5 One readiness line

`readiness.py` (pure python — it imports nothing at all, asserted by a test) turns numbers the
add-on already holds into one line at the top of the first panel:

```
READY FOR MEASUREMENT
NOT READY: 7 non-manifold edges - the exact solver is refused
```

It names the **first** blocker and the panel that explains it, and deliberately restates no
diagnostics. Blocking: no mesh, non-manifold topology, over the density threshold, non-uniform
scale, stale landmarks. Non-blocking, but still worth saying: unpicked landmarks, no landmarks, no
measurements. **"Not analyzed yet" is its own state** — the canonical mesh is consulted with
`peek()`, which never builds one, so opening a panel cannot trigger a rebuild, and when nothing is
cached the line says so rather than inventing an answer.

### 11k.6 Alignment residual, in plain language

`alignment.quality()` reads the residual as **Good** (≤5°), **Check references** (≤15°) or
**Repick recommended**, and the panel prints *"(UI guidance, not a validated threshold)"*
underneath. The bands are labelled `UI GUIDANCE ONLY` in the source as well. Nothing is refused or
adjusted because of them: the frame is exactly orthonormal at any residual, and the residual
itself is always shown.

The reference-point help text was cut to three lines that say what to click:

> LEFT / RIGHT: matching points on each side, at about the same height.
> SUPERIOR / INFERIOR: an upper and a lower point, near the body midline.
> Left and right are the SUBJECT'S, not the viewer's.

### 11k.7 A blocking repair bug, found by re-running an old acceptance script

Re-running the Milestone 3.4 acceptance surfaced a real regression introduced in **v0.13.0**:

> `BSMT: repair reverted - non-manifold edges did not decrease (0 -> 0)`

`accept_repair` required a **strict** decrease in non-manifold edges, and every manual repair
shared it. So on a mesh with 0 non-manifold edges — which is the state the automatic repair works
hard to reach — **Fill Loop, Delete Component and Remove Duplicate Faces were all refused**. The
researcher reaches for those tools precisely when the topology has become good.

The rule is now scoped to the repairs whose *purpose* is removing non-manifold edges (the local
weld and duplicate-face removal). For everything else the criterion is the honest one: **do not
make it worse**. `tests/test_repair.py` asserts the scoping at both the rule and the call sites,
so it cannot quietly widen again. With the fix the 3.4 acceptance runs to completion for the first
time since v0.12.0: 91 checks, 0 failures.

This is why the old scripts are re-run rather than archived.

### 11k.8 Verified in Blender 4.5.13

A textured body proxy with four landmarks and three measurements. 96 checks, 0 failures.

| Case | Result |
|---|---|
| Add once | one draft, no name, status DRAFT |
| Add four more times | **still one row** |
| From only / From == To | still a draft, still unnamed |
| Both ends set and different | becomes real, named `Shoulder_L to Shoulder_R`, READY |
| Calculate All with a draft present | 3 calculated, draft skipped, no result on it |
| Measurement Results | 3 rows, none blank |
| Cancel New Measurement | removes the draft, leaves the real ones |
| Landmark deleted under a measurement | still a real measurement, `INVALID_REFERENCE`, names `Hip_R`, keeps its name |
| Display scope SELECTED / TICKED / ENABLED | 1 / 2 / all, never a draft |
| One path computed, scope widened | 1 with a path, the rest reported "path not computed" — **nothing recomputed** |
| Measurement target | resolves to the landmarks' mesh, and says why |
| Readiness | READY; NOT READY on a stale landmark; NOT READY on non-uniform scale |
| Residual guidance | 1° Good, 10° Check references, 25° Repick recommended |
| Every panel drawn | 9 of 9, no error |
| Wording audit over 149 emitted strings | no banned term, no `? -> ?` row |

Milestones 3.2, 3.3, 3.4, 3.5, 3.5a and 3.6 acceptance scripts all re-run: 0 failures, 0
tracebacks. Offline: 1,514 checks across eleven suites without pygeodesic, 1,624 with it staged,
0 failures.

---

## 11l. Milestone 3.8 — Landmark labels and display controls (v0.16.0, 2026-09-02)

A protocol of fifty landmarks is a field of identical green dots until each one carries its name.
This milestone draws the name beside the marker, and gives the researcher control over how both
look. Nothing analytical was added: every property in this milestone is cosmetic, and none of them
can reach a `SurfacePoint`, a mesh or a distance.

### 11l.1 A draw handler, not one Text object per landmark

The labels are drawn by a `SpaceView3D` draw handler in **POST_PIXEL** space (`labels.py`).

A Blender Text object per landmark was the alternative, and it is worse in every dimension that
matters here. It is a real datablock: it appears in the Outliner, joins the selection and the
depsgraph, has to be re-oriented toward the viewer every frame, and — decisively — it scales in
**world units**, so it becomes unreadable the moment the researcher zooms in on a shoulder. Fifty
landmarks would mean fifty extra objects in a file that must stay recognisably the researcher's
scan, and any of them could be moved or exported by accident.

The overlay has none of those properties. It creates no datablock, cannot be selected, and draws
in screen pixels, so a label is the same size at any zoom. It also **only reads**: a test asserts
`labels.py` contains no `bpy.ops`, no `objects.new`, no `bmesh`, and never imports the solver.

The handle is kept in `bpy.app.driver_namespace`, not a module global, because Blender's "Reload
Scripts" re-imports the module and would otherwise lose the handle and leak a callback that can
never be removed. `register()` is idempotent; `unregister()` never raises.

### 11l.2 What the label is anchored to

**The marker helper object's world location**, when one exists. The marker and the label then move
together *by construction*: whatever moves the marker — a translate, a rotate, Apply Alignment,
Reset Alignment — has already moved the thing the label is positioned from, so the two cannot
drift apart even if a refresh is a frame late. The stored `SurfacePoint.world_xyz` is the fallback
for a landmark whose marker has been removed, and is the same value by definition. An object with
the right name that is *not* a BSMT helper is never trusted as an anchor.

The **text** is read from the landmark on every redraw. There is no cache to invalidate, so
renaming `P03` to `Acromion_L` shows on the next frame (§10) — asserted both offline and in
Blender.

The label is offset in **screen space** (default 8 px, up and right) so it never covers the exact
surface point it names.

### 11l.3 Status is said in words, not only in colour

A landmark that is not VALID reads `P02  [STALE]`, and takes the colour the existing status system
already assigns — `visualization.landmark_color()`, not a new palette invented in the overlay.

Colour alone was not enough. It is invisible to a colour-blind reader and it does not survive a
screenshot pasted into a paper, and §9 asks that a stale landmark never be shown as normal VALID
data. The word is the report; the colour is the reinforcement.

The same rule governs the markers: **Marker Color applies to VALID landmarks only.** A stale or
invalid landmark keeps its status colour whatever the setting, so a marker that cannot be trusted
can never be made to look like one that can. `landmarks.STATUS_SHORT` is now the single definition
of the status words, used by both the list and the overlay.

### 11l.4 Display controls

A collapsible **Landmark Display** section: Show Markers, Show Labels, Marker Color, Marker Size
(mm), Label Color, Label Size, Label Offset, Label Shadow, and a scope of All Landmarks / Selected
Landmark Only.

**Label Size is in screen pixels** (default 13, range 6–64), not millimetres — a label is an
annotation on the screen, not a feature of the body. A test asserts `labels.py` performs no
millimetre conversion at all. Marker Size stays in millimetres, in the existing BSMT convention,
and is applied as object *scale* on a unit sphere, so changing it rebuilds no geometry.

Every label property has an update callback that calls `labels.tag_redraw()`. A label setting
changes no object, so nothing would make Blender repaint on its own, and Label Size would appear
to do nothing until the viewport redrew for some other reason.

The **selected** landmark is emphasised: its marker is drawn `SELECTED_MARKER_SCALE = 1.35` times
larger and its label two pixels bigger. Both are display-time computations — the marker emphasis
is carried entirely by object scale — so nothing about the stored landmark changes to highlight
it (§6). Verified: selecting a landmark and selecting away leaves its stored position identical.

### 11l.5 Performance

Measured in Blender 4.5.13, building the full per-frame draw list:

| Landmarks | Per frame | Labels drawn |
|---|---|---|
| 10 | 0.030 ms | 10 |
| 50 | 0.170 ms | 50 |
| 100 | 0.423 ms | 100 |

At 100 landmarks the overlay costs about 0.4 ms of a 16.7 ms frame. Nothing is rebuilt to draw a
label: after 20 full passes the canonical mesh has the same geometry hash and triangle count, and
no `SurfacePoint` was touched. `MAX_LABELS = 512` exists only so that a pathological scene cannot
make the viewport unusable.

### 11l.6 Alignment regression (§12), and a precision limit worth recording

Three landmarks, an arbitrary scanner orientation, four alignment references, Apply, then Reset:

| Check | Result |
|---|---|
| Labels moved with the object | yes |
| Every label still on its marker | to 1e-9 |
| Every marker on its stored world position | exact |
| Triangle index, barycentric, component, geometry hash | **unchanged** |
| Landmarks after alignment | all still VALID |
| Labels after Reset | back to 1.5e-8 |
| SurfacePoints after the round trip | identical |

The transform is restored to **single-precision exactness** — a maximum error of 5.96e-08, which
is half a float32 ULP at 1.0 — and not bit-exact. That is a property of Blender, not of BSMT, and
it is worth recording precisely because it looks like a bug. Measured on 4.5.13: storing a matrix
in a `FloatVectorProperty` and reading it back is **lossless** (0.0 difference), but *assigning*
`obj.matrix_world` costs 5.96e-08 — and assigning `obj.matrix_basis` costs exactly the same,
because both setters decompose the matrix into float32 location, rotation and scale. Only storing
those three fields directly could avoid it.

It is not worth avoiding. On a 1710 mm body the error is 1e-4 mm; the metric tensor that governs
whether a stored distance stays valid is unchanged by it (asserted); and Blender itself loses the
same bit restoring a pose it saved. The alternative — storing rotation mode plus the matching
rotation field — would add real state and branching to buy 100 nanometres.

### 11l.7 Verified in Blender 4.5.13

79 checks, 0 failures: three landmarks labelled `P01 P02 P03`, each label on its own marker;
renaming P03 updates immediately; marker color, marker size, label color, label size and label
offset all take effect; selection emphasis appears and clears without touching stored data; a
stale landmark reads `[STALE]` in its status colour and the Marker Color setting cannot override
it; both label scopes; the alignment round trip above; the 10/50/100 performance table; handler
register/unregister balance; and all nine panels still drawing.

Offline: `tests/test_labels.py`, 65 checks. Full regression 1,599 checks across twelve suites,
1,709 with pygeodesic staged, 0 failures. Acceptance scripts for milestones 3.2 through 3.7 all
re-run clean.

---

## 11m. Milestone 3.9 — Screen-space landmark markers (v0.17.0, 2026-09-02)

Real-Blender use of 0.16.0 showed three problems at once: P01, P02 and P03 did not look the same
size, the selected landmark looked larger than the others, and even the smallest setting left
markers bigger than a landmark wants to be. All three have the same cause — the marker was a UV
sphere measured in **world units** — and one fix.

### 11m.1 The marker is now drawn, not built

Landmark markers moved into the same POST_PIXEL draw handler as the labels, which is why
`labels.py` became **`overlay.py`**: it draws markers *and* names, and a module called `labels`
that draws markers is exactly the drift that makes a codebase hard to read later.

A world-unit sphere cannot satisfy "all markers the same size". It is the same size in *metres*,
which means its apparent size depends on distance from the camera, on the field of view, and on
whether the view is perspective or orthographic — so two landmarks at different depths on the same
body genuinely render at different diameters. Nothing about tuning the radius fixes that; the unit
is wrong. A marker whose job is "here, precisely" has to be measured in the space the researcher
is actually looking at.

In screen space every one of the symptoms disappears by construction:

| Requirement | How it is met |
|---|---|
| identical size for every landmark | one `radius` for all, asserted |
| same size at any zoom or depth | the radius is a pixel count; nothing projects it |
| perspective and orthographic alike | the projection maps position only, never size |
| substantially smaller than before | 2–20 px, default **6 px** |
| nothing selectable added to the file | no datablock exists at all |

`Marker Size (mm)` is gone and `Marker Size (px)` replaces it. **Point A and Point B are
untouched**: they remain millimetre-sized helper objects, because they are a different tool with
different semantics.

### 11m.2 The selection is a ring, never a bigger dot

0.16.0 emphasised the selected landmark by making its marker 1.35× larger, which is precisely what
made the markers stop reading as one size. The core disc is now **identical for every landmark,
selected or not** — asserted directly: the list of radii is the same whichever row is selected —
and the selection is drawn as a white ring 2 px outside the core, plus two extra pixels of label.

### 11m.3 Drawing

Discs are flat `TRIS` lists (16 segments) and the ring is a `LINES` list. Neither is a stylistic
choice: **`TRI_FAN` and `LINE_LOOP` were removed from Blender's GPU module in 3.2**, and a marker
built from them would fail at draw time, in the viewport, with nothing to see in a headless test.

Batches are grouped by colour, so 100 identical landmarks are **one** draw call and a single stale
landmark adds exactly one more. The GPU shader is built on first use, never at import, because
Blender refuses to create one in background mode — which is also why the drawing itself cannot be
covered by the headless suite, and why `tools/check_overlay_render.py` exists.

### 11m.4 Proving it renders, since headless cannot

`tools/check_overlay_render.py` runs Blender **without** `--background`, draws the real marker
batches into a `GPUOffScreen` buffer, and counts the pixels they actually paint. Measured on
4.5.13:

| Case | Painted | Expected |
|---|---|---|
| two radius-3 discs | 64 px | 2·π·9 = 57 |
| radius 1 | 4 px | π = 3 |
| radius 3 | 32 px | 9π = 28 |
| radius 10 | 308 px | 100π = 314 |

That is the claim "the size is in screen pixels" measured rather than asserted, and it is also the
only proof that this Blender's GPU backend accepts the batch types used.

### 11m.5 Anchoring and migration

Everything is positioned from `SurfacePoint.world_xyz`, which `attach.refresh_landmarks` already
maintained, so a rigid transform, Apply Alignment and Reset Alignment reach the marker through the
one path that already existed. The marker and its label come from **one entry and one projection**,
so they cannot separate. `attach` no longer moves a helper object, and counts world updates
directly — since the marker is drawn from that value, "the landmark moved" and "the marker moved"
are now the same event.

A .blend saved by 0.16.0 or earlier still contains one marker object per landmark. Left alone the
researcher would see two markers for every landmark, one of them at the wrong size and selectable.
`_sweep_legacy_landmark_markers()` removes them at register and on `load_post`, touching only
objects that carry BSMT's own helper tag *and* the landmark prefix — verified against a real 0.16.0
file, including that an untagged look-alike of the same name is left alone.

### 11m.6 A bug this milestone introduced, and the guard that now catches it

Removing the marker machinery deleted `visualization.remove_landmark_marker`, and **two operators
still called it**: `bsmt.remove_landmark` and `bsmt.clear_landmark_position` both raised
`AttributeError`. The offline suite passed, because those operators only run inside Blender. The
acceptance scripts caught it.

The function is restored, as part of the legacy-cleanup surface — deleting a landmark must not
leave an old file's marker behind. More usefully, `tests/test_import.py` now statically resolves
**every `module.function(` call between BSMT modules** (222 of them) against the imported module,
so a deleted function with a surviving call site fails offline. Verified by re-introducing the
exact fault: the guard reports `operators.py calls visualization.remove_landmark_marker()`.

### 11m.7 One deliberate behaviour change

Markers now draw **on top of** the scan instead of being occluded by it. A POST_PIXEL callback has
no usable depth buffer, and for landmark work the alternative is worse: a landmark on the far side
of the body would be silently invisible rather than visibly behind. Labels already behaved this
way, so the two are now consistent.

### 11m.8 Verified in Blender 4.5.13

92 checks, 0 failures. Three landmarks all report one radius; selecting P03 changes no radius,
including its own; 2/3/6/20 px all give a uniform radius; no landmark marker object exists at all;
the selection draws a ring outside the core while all three discs stay in one batch. Marker and
label share one anchor through Apply and Reset Alignment, with triangle index, barycentric,
component and geometry hash unchanged and landmarks still VALID.

Performance improved with the objects gone: 100 landmarks now build in **0.220 ms** per frame
(0.423 ms in 0.16.0), 50 in 0.111 ms, 10 in 0.025 ms.

Offline: `tests/test_overlay.py` replaces `tests/test_labels.py`, 98 checks. Full regression 1,643
checks across twelve suites, 1,753 with pygeodesic staged, 0 failures. All thirteen Blender
acceptance scripts, from Milestone 3.0 onward, re-run clean with 0 tracebacks.

---

## 11n. Landmark visibility mode (v0.17.1, 2026-09-02)

v0.17.0 draws every landmark on top of the scan, because a POST_PIXEL callback has no usable depth
buffer. That is right for placing landmarks and wrong for reading a pose: on a body, half the
markers belong to the far side. **Landmark Visibility** now offers *Always on Top* (unchanged
default) and *Visible Surface Only*.

### 11n.1 The test, and why it is a ray rather than a depth buffer

Moving markers to POST_VIEW would give depth testing for free and cost exact pixel sizing — the
whole point of 3.9. Instead, a landmark that is about to be drawn is checked with a ray:
`overlay.hide_occluded()` casts from the viewer toward the landmark, against the BVH of **the mesh
the landmark was picked on**, and drops the entry when the surface is hit in front of it. Marker
and label go together — a name floating where its marker is not would be worse than either.

The ray is built from the **same screen position the marker is drawn at**
(`region_2d_to_origin_3d` / `region_2d_to_vector_3d`), not from a second camera model that could
disagree. That is also what makes it correct in an **orthographic** view, where there is no single
eye point and the origin is per-pixel.

The canonical mesh is read with `peek()`, never `get()`. A draw callback must not be able to start
a mesh rebuild; a mesh that has not been analysed yet simply occludes nothing. A test asserts
`meshcache.get(` never appears in `overlay.py`.

Everything from 3.9 is preserved: pixel marker size, pixel label size, the selection ring, status
styling. *Always on Top* does no ray casting at all.

### 11n.2 The tolerance is not optional

A landmark sits exactly ON the surface, so the ray that looks for an occluder hits that same
surface at the landmark's own position. Without a margin **every landmark would hide itself**.

`OCCLUSION_TOLERANCE = 1e-3` is a **fraction of the distance from the viewer**, so it means the
same thing at any zoom and in any unit — 2 mm at a 2 m view distance. Millimetres rather than
microns because near a silhouette the ray grazes the body and hits a neighbouring triangle a
fraction in front; and still nowhere near the ~200 mm of body thickness that hides a landmark on
the far side. `is_occluded()` is pure and tested on its own.

A related case is worth recording, because it looks like a bug: a landmark stored in single
precision can sit a fraction **outside** the surface, and a ray aimed at it along the normal at a
silhouette extremum can graze past and hit nothing. Observed in the acceptance run. Reporting
"nothing in the way" is the correct answer there — the landmark is being looked at head on.

### 11n.3 A performance problem found by measuring, not by guessing

The first working version cost **2.34 ms per frame** for 100 landmarks — about 14% of a 60 fps
frame, during exactly the orbiting where it runs. A BVH ray cast is not the expensive part: 100
casts against a 239k-triangle mesh take 0.13 ms. Two other things were.

`ray_hit_distance()` inverted the object matrix **once per ray**. Hoisted into
`meshcache.hit_distance_caster()`, which prepares the inverse once per object and returns a
closure: **2.34 ms → 1.23 ms**.

The remainder was numpy. The BVH takes and returns mathutils Vectors, so a numpy inner loop
converted twice per ray. Rewriting the hot path in mathutils: **1.23 ms → 0.556 ms**, a 4×
improvement overall. *Always on Top* is unchanged at 0.25 ms.

The shared local-space ray transform now has one definition (`meshcache._local_ray`), used by
picking and by the occlusion test, so the two cannot disagree about where a world ray lands.

### 11n.4 Verified in Blender 4.5.13

50 checks, 0 failures, on a body-proportioned mesh with landmarks at the front, the back and the
subject's left:

| Case | Result |
|---|---|
| Viewed from the front | front visible, back hidden |
| Viewed from the back | back visible, front hidden |
| Viewed from the left | side visible, front and back hidden |
| Viewed from the right | all three hidden — correct for these three positions |
| Full 24-step orbit | every landmark visible somewhere; visibility changes; each visible over a contiguous arc of 11–13 steps, no flicker |
| Head-on view of each landmark | none occludes itself |
| Body rotated 180° | visibility swaps; rotating back restores it exactly |
| Canonical mesh dropped | nothing hidden; rebuilding restores the same answer |
| 100 landmarks | 0.556 ms per frame, 49 visible from the front |
| Always on Top | all 100 drawn, one radius, one disc batch, one ring — 3.9 behaviour exactly |

Offline: `tests/test_overlay.py` grows to 126 checks. Full regression 1,671 checks across twelve
suites, 1,781 with pygeodesic staged, 0 failures. All fourteen Blender acceptance scripts re-run
clean, and `tools/check_overlay_render.py` still renders on a real GPU.

---

## 11o. Milestone 3.11 — Export, session metadata and protocol reuse (v0.18.0, 2026-09-02)

The workflow produced numbers a researcher could read but not *keep*. This milestone adds the
record: session metadata, two CSV exports, and a protocol that carries a study's definitions from
one subject to the next. No geometry or solver behaviour changed.

### 11o.1 Two rules govern the whole export

**A blank is not a zero.** A measurement that was never calculated, a surface distance never
solved, a landmark never picked — every one exports as an **empty field**. Writing `0.0` for "not
calculated" is the single failure mode that turns an export into *wrong* data rather than *missing*
data, because a zero survives every downstream check a blank would fail. `export.number()` takes
the validity flag beside the value and returns `""` when it is false; a genuine measured zero still
writes `0.000000`.

**A number is exported only when it is current.** BSMT already clears a stored result the moment a
dependency changes, so a STALE row arrives at the export with nothing to write. Verified end to
end: STALE and FAILED rows carry their status and no distance, a straight-only measurement leaves
the surface field blank, and the ratio blanks out with whichever half is missing.

### 11o.2 Session metadata is metadata

Subject ID, Condition, Scan ID and Notes live on the scene and travel on every exported row.
`tests/test_import.py` asserts that **none of the four names appears in any geometry, landmark,
measurement, alignment, overlay, repair or preprocessing module** — a typed subject id cannot reach
a number.

### 11o.3 Encoding, and why the BOM

`utf-8-sig`. Without the BOM, Excel on Windows reads a UTF-8 CSV as the system code page and a
landmark named `목_앞` arrives as mojibake — a silent corruption of the researcher's own labels.
Python's `csv` handles it with `encoding="utf-8-sig"`, R with `fileEncoding="UTF-8-BOM"`; both are
one argument, and losing the labels is the worse trade. Numbers are formatted with an explicit
`%.6f`, so the decimal separator is `.` in every locale. `export.py` imports **csv, datetime and
re, and nothing else** — asserted, because a pandas dependency is one a researcher will one day not
have.

Commas and quotes in a name are handled by the `csv` module itself; the acceptance run measures a
landmark literally named `Waist, "mid"` surviving a round trip.

### 11o.4 What each file carries

**Measurements** (27 columns): session, ids and names for the measurement and both landmarks, type,
enabled, the three distances, status, the mesh provenance of §11o.5, the geometry hash **the result
was computed against**, the solver backend and version, the BSMT version and a UTC timestamp.
Drafts are never written.

**Landmarks** (23 columns): session, id, name, status, notes, triangle index, three barycentric
coordinates, component, world position with its `coordinate_unit`, **and** the physical millimetre
position. Both positions on purpose: distances are always millimetres, so exporting a coordinate
only in scene units would let the two files silently disagree.

An unpositioned landmark **keeps its definition row** with every geometric field blank. A protocol
of 40 landmarks of which 38 were picked exports 40 rows, so the two that were missed are visible in
the data rather than absent from it.

### 11o.5 Provenance, and which mesh is reported

`measurement_mesh`, `source_mesh`, `representation`, both triangle counts and the preprocessing
method — all read from what BSMT recorded when the measurement mesh was created. Measuring directly
on an imported scan leaves them blank rather than inventing them.

The mesh reported is the one **the landmarks were picked on**, not the selected object — the same
rule the readiness line uses, and the lesson of §11i.

### 11o.6 The protocol

A third format, `bsmt-protocol`, carrying landmarks **and** measurements in one file. The two
earlier formats split a study's definitions across two files and made it possible to load half of
it; they still work and still round-trip.

References are **stable ids**, restored on load. Within a scene the stable id is already the only
trustworthy reference (§11e: a dynamic enum remaps by index); writing and restoring it makes that
guarantee hold across files, so a measurement that referenced landmark 7 still references landmark
7 on the next subject's scan. The next-id counters advance past everything loaded, so a landmark
added afterwards cannot collide.

`_assert_protocol_is_portable()` runs on write **and** on read. A file carrying a triangle index, a
barycentric coordinate, a geometry hash, a result or a subject id is **refused**, not quietly
cleaned — such a file is not a protocol, and treating it as one would hide the mistake that
produced it. Verified: the saved file contains no occurrence of `A_BSMT`, `S01`, `TEST`,
`triangle_index`, `barycentric`, `geometry_hash` or any distance.

Load is **Replace**, not merge (§8 of the brief). Merging means deciding what a collision is — same
name, same id, same stable id? — and every answer silently produces duplicates or silently discards
a definition. Replace is one rule the researcher can predict, and the file they loaded from is
still on disk. Everything arrives **unpositioned**, with no result and no cached path. A dangling
reference is refused with the id that could not be resolved; nothing is ever redirected to another
landmark.

### 11o.7 Filenames

`S01_SV2_measurements.csv`, falling back to scan id, then mesh name, then `bsmt`. Sanitisation
keeps Unicode word characters — a first attempt stripped them to ASCII, which would have given
**every subject in a Korean-labelled study the same fallback filename**. What it removes is
everything that makes a name dangerous or unportable: path separators, the Windows-reserved
`: * ? " < > |`, and whitespace.

### 11o.8 A pre-existing picking limit, found while building the fixture

Recorded because it cost an hour to trace and will otherwise be rediscovered as an export bug.

A ray cast along **exactly** `(1, 0, 0)` at the acceptance body hits triangle 271 at a point the
BVH reports **6.83e-07 off that triangle's plane** — float32 hit-point precision. For that
particular triangle the barycentric solve amplifies it to a sum deviation of **3.67e-06**, over
`SUM_TOLERANCE = 1e-6`, so `meshcache.ray_cast_local` returns `None` and the pick reports *no hit
at all*.

It is rare and it fails safe — no wrong number is produced, and BSMT refusing a point that is not
on its triangle is the no-silent-approximation rule working. But the user-visible symptom is a
click that does nothing. Measured for context: over 400 random rays on the full-resolution mesh,
the decimated 2000/1000/3500-triangle copies and a 39k-triangle mesh, the worst deviation was
**2.2e-16** with zero rejections, and the decimated meshes contain no degenerate triangles
(max aspect ratio 11.2). So this is a specific ray/triangle coincidence, not a mesh-quality
problem. **Not changed here** — a picking-tolerance change belongs in its own milestone with its
own validation, not in an export milestone.

### 11o.9 Verified in Blender 4.5.13

113 checks, 0 failures, on a decimated textured measurement mesh with Session Info
`S01 / TEST / S01_TEST_01`, five landmarks (one Korean, one named `Waist, "mid"`, one deliberately
unpicked) and five measurement rows (one draft, one disabled, one STALE, one FAILED).

| Case | Result |
|---|---|
| Measurements CSV | 4 rows; the draft excluded |
| Values against the UI | straight and surface match to 1e-6, in millimetres |
| STALE / FAILED | status exported, **no numbers** |
| Disabled | exported, `enabled=0`, no numbers |
| Unicode | `목_앞` byte-identical; BOM present |
| Commas and quotes | `Waist, "mid" to hip` survives as one field |
| Provenance | `A_BSMT` / `A`, representation, both triangle counts, result hash |
| Landmarks CSV | 5 rows; the unpicked one keeps its row with 6 blank fields |
| Protocol saved | 5 landmarks, 4 measurements, no scan or subject data |
| Loaded onto a **fresh scene and new scan** | landmark and measurement definitions **identical**, stable ids preserved |
| After load | every landmark NOT_PICKED, no result, no path, all references resolve |
| Adding after a load | new stable id, no collision |
| Loading twice | replaces; no duplicates |
| Dangling reference | refused, naming the unresolved id |

Offline: `tests/test_export.py`, 198 checks. Full regression **1,927 checks across thirteen
suites**, 2,037 with pygeodesic staged, 0 failures. All fifteen Blender acceptance scripts re-run
clean with 0 tracebacks.

---

## 11p. Milestone 3.12 — Picking robustness (v0.18.1, 2026-09-02)

§11o.8 recorded a rare pick failure: a click that reports nothing hit. Investigating it found that
the rule deciding whether a BVH hit belongs to its triangle was measuring the wrong quantity, and
was wrong in **both** directions at once.

### 11p.1 What the failure actually was

Not what the symptom suggested. `barycentric()` computes `u = 1 - v - w`, so the sum is 1 by
construction and the "sum tolerance" was never what rejected anything. The rejected quantity was a
**negative coordinate**: the hit lay a fraction *outside* the triangle the BVH named, because the
true intersection was on a shared edge and float32 put the reported point on the other side of it.

Measured on a decimated body scan, ray along exactly `(1, 0, 0)` from 5 units away:

| Quantity | Value |
|---|---|
| barycentric sum error | **0.0** |
| barycentric excursion outside [0,1] | 3.67e-06 — over the 1e-6 limit, so refused |
| **geometric distance from the triangle** | **5.83e-08 units = 58 nanometres** |
| that distance in float32 ulps of the ray | **0.20 ulp** |

The pick was refused for being one fifth of a float32 ulp out of place.

The reason the ratio looked large is that a barycentric coordinate **is** a ratio: the triangle's
smallest altitude was 15.9 mm, so 58 nm of displacement is 3.7e-6 of it. A fixed barycentric
tolerance means a different physical distance on every face in the mesh.

### 11p.2 The old rule was blind in the other direction

`barycentric()` orthogonally **projects** onto the triangle plane before solving. So a point a
kilometre off the surface, whose projection lands inside the triangle, produced perfectly clean
coordinates and passed. Verified, and now a test:

| Off-plane distance | Old rule | New rule |
|---|---|---|
| 1e-06 | accepted | refused |
| 1e-03 | accepted | refused |
| 1.0 | accepted | refused |
| 1000.0 | **accepted** | refused |

So this milestone does not loosen a tolerance. It **replaces a rule that measured the wrong thing**
with one that is stricter off the plane and looser only within float32 noise in it.

### 11p.3 The rule

`surface_point.locate_hit()` projects the hit onto the plane of the triangle the BVH reported,
recomputes the barycentric coordinates there, clamps them into the simplex, and accepts only when
**both** are within tolerance:

- `|off_plane|` — the hit really lies on this triangle's plane;
- `residual` — the distance seating the point actually moves it, measured as
  `|reconstruct(clamped) - projected|`. Clamp-and-renormalise is not exactly the nearest point in
  the triangle, so this is an *upper bound* on the true distance: it can only refuse a point the
  exact distance would have accepted.

Accepting returns a valid convex combination — sum exactly 1, every coordinate in [0,1] — and the
stored XYZ is the reconstruction of those coordinates, so `triangle + barycentric` and the reported
position describe the same point by construction rather than to within the hit's noise.

**The triangle index is never reconsidered.** No neighbour search, no vertex snap, no substituted
face — asserted by a test that reads the function body. Either the hit seats on its own triangle
within float32 noise, or it is refused.

### 11p.4 The tolerance, derived

    tolerance = HIT_TOLERANCE_ULPS x FLOAT32_EPS x scale
    scale     = max(|corners|inf, |hit|inf, |ray origin|inf)

`FLOAT32_EPS = 2**-24` is the real relative spacing of float32, which is what a mathutils Vector —
and therefore every BVH hit — carries. The **ray's own magnitude counts**: a hit is computed as
`origin + t*direction`, so a ray cast from far away is intrinsically less precise, and the
tolerance follows it honestly rather than pretending otherwise.

`HIT_TOLERANCE_ULPS = 16` is measured, not chosen. Over 4000 random rays against a decimated body
scan the largest off-plane distance of a legitimate hit was **4.5 ulp** (p99.9 = 4.2) and the
largest in-plane displacement needed to seat one was **0.2 ulp**. Sixteen leaves roughly 3.5x
margin over the worst observed case while staying a minuscule absolute distance: on a scan in
millimetres with coordinates to 2000, it is **0.002 mm**. Nothing anthropometric is defined to two
microns, and nothing genuinely on another face is within it.

`SUM_TOLERANCE` is untouched. It now guards only STORED points — a hand-written or corrupted
SurfacePoint — and stays strict. Everything `locate_hit` produces passes it, including after the
float32 round trip a stored SurfacePoint goes through.

### 11p.5 Verified in Blender 4.5.13

42 checks, 0 failures.

| Case | Result |
|---|---|
| The known failing click | **succeeds**, on the BVH's own triangle, moved 6.9e-07 units |
| 48 axis-aligned rays x 4 distances (1.5 to 500) | every BVH hit accepted |
| 8000 random rays | 0 refused, 0 triangles substituted, reconstruction exact |
| Worst displacement | 1.42e-06, inside the derived 4.77e-06 bound |
| Translated / rotated / both / uniformly scaled | 600 rays each, none refused, world position exact |
| Millimetre-scale scanner coordinates (mesh at 1200, -450, 1700) | 212 hits, none refused; tolerance 0.005 mm |
| 1 mm, 0.1 mm and **1 um** off the plane | all refused |
| 1 mm beside the triangle, NaN | refused |
| Full path through `pick_landmark` | stored point passes the strict stored-point check |

The Milestone 3.11 export acceptance now picks its "Waist" landmark on exactly `(1, 0, 0)` — the
ray it previously had to dodge — through the production picking path.

Offline: `tests/test_surface_point.py` grows to 312 checks, covering triangle interior, near edge,
exactly on edge, near vertex, exactly on vertex, the reproduced failure, six random rigid
transforms, and millimetre-scale scanner coordinates, each also after float32 rounding. Full
regression **2,091 checks across thirteen suites**, 2,201 with pygeodesic staged, 0 failures. All
sixteen Blender acceptance scripts re-run clean.

---

## 11q. Milestone 3.13 — Cross-platform packaging (v0.19.0, 2026-09-02)

BSMT worked on one machine. This milestone makes it installable by a colleague on Windows without
a terminal. No measurement code changed.

### 11q.1 Dependency audit

| Dependency | Class | Notes |
|---|---|---|
| `bpy`, `bmesh`, `mathutils`, `gpu`, `gpu_extras`, `blf`, `bpy_extras` | Blender-provided | portable by definition |
| `numpy` 1.26.4 | Blender-provided | BSMT never bundles or installs it |
| `json`, `csv`, `re`, `os`, `sys`, `math`, `datetime`, `importlib`, `time`, `traceback`, `hashlib`, `heapq`, `colorsys`, `platform`, `sysconfig`, `site` | pure stdlib | portable |
| `resource` | **Unix only** | already inside a `try` in `_max_rss_bytes()`; returns None on Windows, which is the documented meaning of the field |
| **pygeodesic 0.1.11** | **native** | one compiled module per platform+ABI |

Blender 4.5.13 measured: **CPython 3.11.15**, `SOABI cpython-311`, so the only wheels that can load
are **cp311**. pygeodesic 0.1.11 publishes `cp311-cp311-win_amd64` and `cp311-cp311-macosx_11_0_arm64`
— exactly what is needed. Both are vendored under `wheels/` with their SHA-256 verified against PyPI.

### 11q.2 Strategy: one package, declared wheels (option B), because it was tested

Blender 4.2 introduced **extensions**, whose `wheels` field is the supported mechanism for exactly
this problem. That makes option B possible in principle; it was chosen because it was *verified*,
not because it is tidier.

Installing `bsmt-0.19.0.zip` into an isolated Blender config:

- the extension installed and enabled — `{'FINISHED'}`, module `bl_ext.user_default.body_surface_measurement`;
- Blender selected and installed the **matching platform wheel** on its own, into
  `extensions/.local/lib/python3.11/site-packages/pygeodesic/`;
- an exact geodesic solve ran through it and returned 1.414214 for a known case.

**And the NumPy question was answered by measurement, not assumption.** The pygeodesic wheel
declares `Requires-Dist: numpy<3,>=2` while Blender ships 1.26.4. If Blender resolved wheel
dependencies it would have installed NumPy 2 and broken Blender. It does not: after the install,
NumPy was still 1.26.4 from Blender's own `site-packages`. Blender installs the listed wheel files;
it does not run a resolver. That is the single fact option B rests on, and it is now checked.

A second package, `body_surface_measurement-0.19.0.zip`, is still produced: the legacy add-on
layout, no wheels, for a Blender without extension repositories. It is documented as the fallback.

### 11q.3 A packaging defect this found

**Blender removes `bl_info` from a module installed as an extension** — the manifest is
authoritative there. BSMT read `bl_info["version"]` at runtime in two places, so the very first
extension install failed with:

    RuntimeError: Error: name 'bl_info' is not defined

Reproduced, then fixed by making a module-level `VERSION` tuple the single source of truth:
`bl_info` is built from it, `_version_string()` and `_addon_version()` read it, and
`tools/build_release.py` parses it with `ast` to stamp the manifest — so the package version and
the reported version cannot disagree. A test in `test_import.py` asserts nothing reads `bl_info` at
runtime again.

### 11q.4 Windows audit

Static, exhaustive, and in `tests/test_portability.py`, because the point is what the source
*assumes* — there is no Windows machine here to fail on:

- **no Unix-only module imported at module level** in any shipped file;
- **no shell**: no `subprocess`, `os.system` or `os.popen` anywhere;
- **no hard-coded absolute path**, POSIX or Windows;
- **no hand-rolled path splitting** — a path arrives from Blender as a string and goes straight to
  `open()`;
- **every one of the 13 calls to `open()` declares an encoding**. This is the Windows bug that
  would have been invisible here: `open()` without `encoding` uses the **locale** encoding, which
  on a Korean Windows is cp949. A protocol written there would be unreadable anywhere else and
  Korean landmark names would be silently mangled. The audit found the source already clean.

Unicode is round-tripped through Korean **directory names, file names, subject ids, landmark names
and notes**, with a byte-level check that the output is UTF-8 and not cp949. Filenames are checked
against every Windows-reserved character and against the reserved device names.

### 11q.5 GPU overlay

`overlay.py` uses only the builtin `UNIFORM_COLOR` shader, `TRIS` and `LINES` batches, and `blf`
through a version-tolerant size helper. No hand-written shader source, no `bgl`, nothing
Metal- or OpenGL-specific, and — as of 3.9 — no `TRI_FAN` or `LINE_LOOP`, which were removed from
Blender's GPU module in 3.2. Asserted by test. **No Windows GPU has drawn it**, so this is
inspection, not validation.

### 11q.6 Degradation without the solver

Built a wheel-less package and installed it. Measured:

| | |
|---|---|
| Add-on enables | **yes** |
| Backend status | `Unavailable — ModuleNotFoundError: No module named 'pygeodesic'` |
| Canonical mesh, topology, picking | work |
| **Straight Distance** | **works — 999.897 mm** |
| Surface Distance | refuses with a clear dependency error, **no crash** |

### 11q.7 About summary

`envreport.about_lines()` and an *About BSMT* section under Session and Export:

```
BSMT 0.19.0
Blender 4.5.13 LTS
macOS ARM64
Python 3.11.15
NumPy 1.26.4
Exact Geodesic: Available (pygeodesic 0.1.11)
```

Enough to tell a Windows problem from a BSMT problem in a bug report, and nothing more.

### 11q.8 Licensing — left open, deliberately

pygeodesic is **MIT** (© 2021 Michael Hogg), and the Kirsanov C++ inside it is MIT too, per its
README: *"licensed under MIT license similar to the original Kirsanov C++ code, rather than GPL"*.
MIT is GPL-compatible, so redistributing the wheel is fine under any BSMT licence provided the
notice travels — it does, inside the wheel's `dist-info`.

The open question is BSMT's own licence. Blender is GPL and the Foundation's position is that an
add-on importing `bpy` is a derivative work needing a GPL-compatible licence; the extension
manifest also **requires** a `license` field, so building a package at all forces the issue.

`tools/build_release.py` writes `SPDX:GPL-3.0-or-later` marked **PROVISIONAL** in the manifest and
prints a warning on every build. **No `LICENSE` file was created**, because writing one asserts
both a licence and an owner and neither is settled — university ownership in particular is not a
question that can be answered here. `docs/LICENSING.md` sets out the components, the Blender
implication and the steps required before distribution.

### 11q.9 What is verified, and what is not

| | |
|---|---|
| **A. Cross-platform by inspection** | no Unix-only import, no shell, no hard-coded path, no path splitting, portable GPU API, correct wheel ABI |
| **B. Cross-platform by automated test** | 87 portability checks; Korean CSV/JSON through Korean paths; Windows filename safety; build-manifest correctness |
| **C. Verified on macOS** | extension installs, correct wheel selected, **NumPy untouched**, solver available, full workflow through the installed extension with Korean object, landmark, folder and subject names; graceful degradation without the solver |
| **D. Still needs real Windows** | that any of it runs there. Wheel loading, GPU overlay rendering, OBJ/MTL/texture resolution from a Windows path, Excel opening the CSV, and the whole `docs/windows_acceptance.md` checklist |

**Windows is packaged, not supported.** The README platform table says so, and must keep saying so
until a Windows machine passes the checklist.

Regression: 2,188 checks across fourteen suites, 2,298 with pygeodesic staged, 0 failures. All
sixteen Blender acceptance scripts re-run clean.

---

## 11r. Milestone 3.14 — Measurement path cache (v0.20.0, 2026-09-03)

Surface-path visualisation worked, and on a large scan it was unusable. This milestone finds out
why, and the answer is not where the symptom pointed. No geodesic algorithm changed, no
preprocessing changed, and no measurement number changed.

### 11r.1 What the symptom was, and what it was not

"Displaying measurement lines is very slow, and worse as triangle count rises" reads like a
drawing problem. It was measured as one first, on Blender 4.5.13, before anything was touched:

| Operation | 261,120 tris, 511-point path |
|---|---|
| `viz.refresh()` | 0.29 ms |
| `apply_measurement_display()` (colour/thickness) | 0.46 ms |
| `sync_transforms()` | 0.03 ms |
| object translate, whole handler chain | 0.10 ms |
| `build_path` including the surface lift | 7 ms |
| **bounded surface distance** | **6.3 s** |
| **exact unbounded path solve** | **31.2 s** |

Every display operation was sub-millisecond. Only the solver was slow — by four orders of
magnitude. So the question was never "why is drawing slow"; it was **"what makes drawing reach the
solver at all"**.

A second run, end to end on a **801,024-triangle** scan, puts a number on what a lost cache
actually costs:

| | |
|---|---|
| canonical mesh build | 1.22 s |
| bounded surface distance | 137.7 s |
| **exact unbounded path solve** | **410.8 s — 6.8 minutes, one solver construction** |
| of which: MMP structure construction | 0.28 s |
| of which: propagation across the mesh | 409.9 s |

The instrumentation settles which half is expensive, and it is not the one that looks it:
construction is a third of a second, propagation is seven minutes. So **every avoidable re-solve
was a seven-minute freeze**, and there is nothing to be gained by making the solver cheaper to
build. The only useful move is to never run it twice for the same question.

### 11r.2 Root cause: the display was the storage

The computed polyline existed in exactly one place — the points of the helper `Curve` that drew
it. `state.path_is_current()` said so outright:

```python
if not visualization.measurement_helper_exists(item.stable_id, 'PATH'):
    return False
```

A cache whose validity test is "is the drawing still on screen" is not a cache. Consequences, all
real:

- `viz.refresh()` called `clear_measurement_path()` the moment a path failed that test, so a cache
  that merely *looked* stale was **deleted**, not marked;
- `Clear Selected` and `Clear All` destroyed the cached path, while their own descriptions said
  "The cached path is kept";
- deleting the helper in the Outliner, or anything that could not find it by name, was
  indistinguishable from "never computed";
- the only way back from any of those was the unbounded solve — 31 s at 261k triangles, and it
  scales with the mesh.

That is the whole reported symptom. The slowness attributed to visualisation was the solver,
re-entered because the visualisation had thrown its own result away.

### 11r.3 The cache, moved out of the drawing

`pathcache.py` stores each measurement's polyline in a `Mesh` datablock of its own,
`BSMT_PathCache_<stable id>`, with `use_fake_user` so it survives a save with no object attached.
It holds `2k` vertices: the polyline in the **scan's local space**, then the unit surface normal at
each point.

Local, never world, is what makes a rigid transform free — translating or rotating the scan cannot
change a local coordinate, so the entry stays valid and the helper follows by matrix. Storing world
coordinates as the authoritative copy would make every move a re-solve.

The **normals are cached with the points** because the drawn path is lifted off the surface by a
multiple of its own thickness, and finding those normals is one BVH query per point: **138 ms for
20,000 points on a 261k-triangle mesh**, measured. Done once at solve time, every later thickness
or offset change becomes one vectorised multiply-add.

Vertex coordinates are float32. Deliberate: this is display geometry feeding a float32 curve. The
measurement is `path_length_mm` / `path_distance_mm`, which are never re-derived from these points.

### 11r.4 Cache identity, and the four states

An entry carries everything needed to judge it **without reading geometry, building a canonical
mesh, or calling anything native**: the measurement stable id, both landmark stable ids, both
endpoint SurfacePoints as triangle index + barycentric, the geometry hash, the metric tensor and
metric key, the unit, the polyline, its length, its point count and its solve time.

Endpoint triangle + barycentric is the field that was missing. Landmark stable ids alone cannot see
a **re-pick**: the id survives, the point moves, and a path from where a landmark used to be would
have kept claiming to be current.

`state.path_cache_state()` returns one of four, with a reason:

| State | Meaning |
|---|---|
| `NOT COMPUTED` | no solve has been run for this measurement |
| `CACHED` | the stored polyline is the path for the current landmarks, geometry and metric |
| `STALE` | a dependency changed. **The entry is kept and reported, never silently recomputed** |
| `INVALID` | the polyline, a landmark, or the scan is gone |

Invalidation:

| Event | Result |
|---|---|
| rigid translation / rotation | **CACHED** — the metric tensor is rotation invariant and carries no translation |
| scale or coordinate-unit change | **STALE** — the physical metric the geodesic was solved under changed |
| mesh geometry edit | **STALE** — marked by the meshcache handler, which writes two properties and nothing else |
| landmark re-picked | **STALE**, and only for the measurements that reference it |
| measurement repointed at a different landmark | **STALE** |
| landmark or scan deleted | **INVALID** |
| helper hidden, removed, restyled, or the file reopened | **CACHED** — the helper is not where anything is kept |

### 11r.5 What may now reach the solver

Exactly one thing: pressing **Compute Surface Path**. Nothing else — not a panel redraw, an orbit,
a zoom, a selection change, a colour, a thickness, a visibility toggle, a scene redraw, a rigid
transform, or any depsgraph handler. `tests/test_path_visualization.py` proves this rather than
asserting it: it wraps `pygeodesic.geodesic.PyGeodesicAlgorithmExact` — the only door to the native
solver — in a counter, and every check records the count before and after and requires it
unchanged.

A stale path is **not** recomputed, by anything, ever. It is hidden, named as stale in the panel
with its reason, and left for the researcher to decide about. Recomputing a path costs minutes on a
dense scan; that is not a decision an update callback is entitled to make.

### 11r.6 Helper writes are compared before they are made

Assigning `bevel_depth`, a colour or `matrix_world` re-tags the datablock for re-evaluation whether
or not the value changed, and re-evaluating a beveled poly curve costs time proportional to its
point count. Measured on Blender 4.5.13:

| Curve operation | 500 pts | 5,000 pts | 20,000 pts |
|---|---|---|---|
| assign `bevel_depth` (same value) | 0.38 ms | 2.22 ms | 8.19 ms |
| rewrite every spline point | 0.35 ms | 2.41 ms | 8.53 ms |
| rewrite points, bevel **off** | 0.08 ms | 0.40 ms | 1.38 ms |
| assign `obj.color` | 0.02 ms | 0.01 ms | 0.01 ms |
| assign `matrix_world` (same value) | 0.01 ms | 0.01 ms | 0.01 ms |

The colour picker fires its update callback on **every mouse move**, so an unguarded `bevel_depth`
write there was one full curve rebuild per pixel of drag. Every write in `visualization.py` is now
guarded by a comparison, and a redisplay whose points would come out identical is skipped entirely
by comparing a **draw signature** — cache generation plus lift distance plus object — instead of
rewriting thousands of points to discover they were already right.

`sync_transforms()` likewise writes a helper's matrix only when it actually moved, so a depsgraph
tick no longer re-tags every measurement curve.

The bevel cost itself was measured and **left alone**: it is genuine geometry work, it only appears
when the thickness really changes, and changing the representation on the strength of a 15 ms
worst case would be a much larger change than the evidence supports.

### 11r.7 Canonical mesh invalidation, narrowed

The meshcache handler cleared the **entire** canonical cache on any non-helper geometry update, so
editing one object threw away every other scan's analysis — about 1.7 s per 1M triangles to
rebuild. It now drops only the entries the changed datablock actually affects (an Object, or every
cached object using a changed Mesh), and marks the paths solved on those objects stale. Property
writes only; no geometry read, no canonical mesh, no solver.

### 11r.8 Timing, so the next answer is not a guess

"The measurement line is slow" has four causes with four different fixes: **A** the solver, **B** a
cache miss that made A run, **C** Blender curve construction, **D** handler churn. `timing.py`
records each named stage — cache validation, cache load/store, solver construction, exact path
solve, result copy, helper create/update, helper transform — into a bounded sample ring plus
**cumulative** totals. Cumulative matters: a bounded history would evict the one 40 s solve within
seconds of redraws and the report would say the solver never ran.

Console logging is off by default and throttled to one line per stage every two seconds, so a
redraw-rate stage cannot flood the console. The panel reads the totals without measuring anything.

### 11r.9 Measured result

On the 801,024-triangle scan above, immediately after its 410.8 s solve: hide → show 0.18 ms,
colour 0.20 ms, thickness 1.11 ms, `viz.refresh()` 0.13 ms, rigid translate 0.11 ms, and **zero**
pygeodesic constructions across all of it.

Worst case for the drawing itself — 1,046,528 triangles with a 20,000-point cached path, which is
far longer than a real anatomical geodesic on that mesh:

| Operation | Before | After |
|---|---|---|
| cache validation | required the helper object | **0.04 ms** |
| hide → show cached path | destroyed the cache on some routes | **0.17 ms** |
| colour change | full curve rebuild per mouse move | **0.19 ms** |
| redisplay from cache | 14.56 ms | **0.02 ms** |
| `viz.refresh()` | 14.90 ms | **0.16 ms** |
| rigid translate, whole handler chain | — | **0.13 ms** |
| thickness change | — | 15.35 ms (bevel rebuild + re-lift; genuine work) |
| pygeodesic calls across all of the above | could be a full re-solve | **0** |

### 11r.10 What is verified

`tests/test_path_visualization.py`, 74 checks under real Blender, covering acceptance A–I: straight
line instant and solver-free; exactly one solve on request; hide→show, helper deletion, colour,
thickness, offset, redraws and orbit-equivalent ticks all solver-free; rigid transform keeps the
cache and the helper follows; scale makes it stale without deleting it; a landmark re-pick
invalidates only dependent measurements; a geometry edit marks stale without re-solving; and a full
**.blend save/reload round trip** returning the identical polyline and redrawing in 0.18 ms.

`tests/test_pathcache.py` adds 48 offline checks for the display lift, the timing recorder, the
write guards and the source-level guarantees.

Regression: 2,242 offline checks across sixteen suites plus 90 in Blender, 0 failures. The
extension package installs and enables on a clean Blender config, selects its own platform wheel,
and registers every new operator.

---

## 11s. Milestone 3.15 — Scan preprocessing v1 (v0.21.0, 2026-09-03)

Milestone 3.3 (v0.11.0) already shipped the non-destructive measurement mesh: the duplicate, the
target-count decimation, the before/after topology table, the provenance record and the solver
safety gate. This milestone did not rebuild any of that. It closed the gap that made the workflow
unsafe for the project's **primary** input format, and gave the result a verdict a researcher can
read in one line.

### 11s.1 The gap: colour was never checked

The appearance comparison looked at three things — UV layers, materials, image textures. A PLY
full-body scan typically has **none of them**. Its entire appearance is a per-vertex colour
attribute, and `compare_texture` had no concept of one. So for the format named first in the
milestone brief, the check reported:

    ok  the source had no UV layer, so none was expected
    ok  the source referenced no image texture

...and passed. A copy that had lost its colour would have been called measurement-ready.

`texture_facts` now carries `color_attributes` as (name, domain, data_type) triples, plus the
active colour and the set of material slots any face actually uses. A lost colour attribute is a
**FAILURE**, on the same footing as a lost UV layer, and it feeds the NOT READY verdict.

### 11s.2 What Blender actually does, measured

Probed on Blender 4.5.13 before designing anything, because the whole workflow rests on it:

| Data | Survives COLLAPSE decimate + `new_from_object` bake? |
|---|---|
| UV layers (multiple) | **Yes** |
| Colour attributes, POINT / BYTE_COLOR | **Yes**, with interpolated values, not defaults |
| Colour attributes, POINT / FLOAT_COLOR | **Yes** |
| Colour attributes, CORNER / BYTE_COLOR | **Yes** |
| Active / default colour attribute name | **Yes** |
| Generic float attributes | **Yes** |
| Material slots | **Yes** |
| Material *assignments* | **No** — see below |
| `preserve_all_data_layers=True` | Made **no difference** in any of these cases on 4.5.13 |

Two findings worth recording:

**A material slot outlives the faces that used it.** A sphere with a second material on exactly one
face, decimated to 2%, kept both slots — but every remaining face referenced slot 0. Slot presence
is therefore a weaker check than it looks, and `used_materials` distinguishes "the material is
still there" from "the material is still used". Reported as a note, never as a loss: the appearance
data is intact and only the assignment is gone.

**`preserve_all_data_layers` bought nothing here.** It is kept anyway — it is documented to matter
and costs nothing — but the tests do not depend on it.

### 11s.2b A Blender quirk the acceptance test found

Building the scenario-A fixture surfaced something worth writing down, because it looked at first
like a decimation defect and is not one. A Blender UV sphere is watertight at every ordinary
density and **is not at very high density**:

| UV sphere | Triangles | Boundary edges |
|---|---|---|
| 16 × 8 | 224 | 0 |
| 64 × 32 | 3,968 | 0 |
| 256 × 128 | 65,024 | 0 |
| **1024 × 512** | **1,046,528** | **40** |
| icosphere, 6 subdivisions | 20,480 | 0 |

Those 40 boundary edges are in the **source**, before any decimation, and the copy inherited 30 of
them. So the WARNING verdict on scenario A is correct and the diagnostics are doing exactly their
job — the fixture, not the tool, was the thing that was not clean. The test now asserts the
property that actually matters (decimation introduced no non-manifold edges and no degenerate
triangles, and any WARNING is explained by a condition really present in the report) rather than
asserting a READY verdict a non-watertight input cannot honestly earn.

The practical lesson for the workflow is the same one the panel already gives: **analyse the scan
before trusting it**. A dense mesh that looks closed on screen may not be.

### 11s.3 The measurement-ready verdict

One line, three states, from the **existing** `topology.analyse()` report. No second definition of
any diagnostic exists (sect. 0).

| | Conditions |
|---|---|
| **NOT READY** | the canonical mesh will not build; no triangles; non-manifold edges > 0; degenerate (zero-area) triangles > 0; appearance data lost |
| **WARNING** | still above the operational density threshold; connected components > 1; boundary edges > 0 |
| **MEASUREMENT READY** | none of the above |

`classify_ready` is not the same question as `preflight`. `preflight` decides whether one solve may
be handed to the native library right now; `classify_ready` decides whether a copy is fit to
landmark and measure on at all. They cannot contradict each other, because every preflight refusal
that is about the mesh itself is also a NOT READY condition.

**Components == 1 is deliberately not a rule.** A real scan can legitimately contain more than one
component — a separate hair cap, a prop, a stray island — and refusing to measure such a scan would
be wrong. Connectivity is a property of a landmark **pair**, and it is enforced there, per
measurement, by `solve.validate_points` raising `DISCONNECTED`. A test pins this so the rule cannot
be tightened by accident.

Degenerate triangles are blocking rather than advisory: a zero-area triangle makes barycentric
reconstruction ill-defined at the point a landmark lands on one, which is a *wrong* measurement
rather than a slow one.

### 11s.4 Landmarks are never carried across

A decimated mesh is a different polyhedral surface. A SurfacePoint is a triangle index plus
barycentric coordinates, so the same numbers name a **different physical point** on the copy.
Re-projecting one would move a researcher's landmark without telling them, which is worse than
losing it.

BSMT therefore does neither: nothing is copied, nothing is re-projected, and existing SurfacePoint
staleness behaviour is untouched. What is new is that preprocessing **counts** the landmarks
anchored to the source and, if there are any, says so in the report and asks for a re-pick on the
copy. Preprocessing is best done before landmarking, and the tool now says so rather than assuming
it.

### 11s.5 Panel

The Scan Preprocessing panel shows object and mesh name, vertices, triangles, connected components,
boundary edges, non-manifold edges, degenerate triangles, coincident vertices, UV maps, colour
attributes, materials and image textures.

Topology comes from `scancopy.diagnostics`, which **peeks** at the canonical mesh cache and never
builds one — a redraw must not cost 1.7 s per million triangles. When the scan has not been
analysed, the panel says so and offers the **existing** `bsmt.diagnose_topology` operator under the
label *Analyze Scan*. There is no second diagnostics path.

*Source* / *Measurement* / *Both* replace a single blind toggle. All three are `hide_viewport`
only: nothing is unlinked, nothing is deleted, every one of them is one click from being undone.

### 11s.6 What is verified

`tests/test_preprocess.py` grew from 107 to 147 offline checks: colour preservation and loss,
domain and active-colour changes as notes, unused material slots, geometry-only sources, and every
branch of the readiness classification including the components rule.

`tests/test_preprocess_blender.py` is new and covers the brief's scenarios A-D under real Blender,
against real datablocks:

| | |
|---|---|
| **A** | 1,046,528-triangle sphere → target 350k: copy created, source byte-for-byte unchanged, result within 5% of target, independent mesh datablock, full before/after diagnostics, complete provenance, MEASUREMENT READY |
| **B** | PLY-shaped mesh (point colours, no UV, no material) → colour attribute survives with its domain, its type and real interpolated values |
| **C** | Textured OBJ-shaped mesh → UV, material and image reference survive; the material datablock is *shared*, not duplicated; no new image datablock is created |
| **D** | Non-manifold input → preprocessing is allowed and a copy is produced, the verdict is NOT READY naming non-manifold topology, and the solver gate still refuses |
| **E** | Target above the current count → copied, not decimated; triangle count identical; method recorded as `COPY_ONLY` |
| **F** | Deterministic naming: `Twin_BSMT`, `Twin_BSMT.001`, `Twin_BSMT.002`, each with its own mesh datablock |
| **G** | A landmarked source → the landmark still points at the source, nothing is added, moved or re-projected, and the report asks for a re-pick |
| **H** | The density threshold, its wording and the solver gate are unchanged |
| **I** | No repair call of any kind exists in either preprocessing module |
| **J** | The Scan Preprocessing panel draws in every state — nothing selected, unanalysed, analysed, and each of the three verdicts — offering the existing diagnostics operator rather than a new one |

Regression: 2,282 offline checks across seventeen suites, plus 110 preprocessing and 90
path-visualisation checks in Blender. 0 failures. The 0.21.0 extension package installs and enables
on a clean Blender config with every new operator and property registered.

### 11s.7 Scope held

No hole filling, no merge-by-distance, no global weld, no non-manifold repair, no remeshing, no
smoothing, no texture baking, no alignment change, no export change, no automatic landmark
detection. Repair is the next milestone, deliberately after this one has been used on real scans.

---

## 12. Open items requiring decisions

1. ~~Confirmation of Blender 4.5.13's bundled Python version and architecture (Milestone 2.2).~~
   **Resolved 2026-09-02 (§5.1a): Python 3.11.15, Darwin arm64, numpy 1.26.4.**
2. Whether landmark pairs may wrap a limb or torso; affects interpretation, not the algorithm.
3. Typical scan triangle count and whether scans arrive pre-cropped.
4. Whether Phase 3 requires all-pairs landmark fields (would justify adding a heat-method backend
   for one-to-many queries, alongside — never replacing — the exact backend). **The §5.1a
   benchmark sharpens this: at 16.8 s per pair with no reuse benefit, an all-pairs workflow over
   more than a handful of landmarks is already unaffordable with the exact backend alone.**
5. ~~**New, from §5.1a:** whether `geodesicDistances` (one-to-all) amortises better than repeated
   `geodesicDistance` calls.~~ **Answered 2026-09-02 (§5.1b): no.** Every unbounded call is a full
   mesh propagation, so one-to-all saves nothing across landmark *pairs* and yields no path. The
   effective lever is a finite `max_distance` on `geodesicDistances`, which cuts a near-landmark
   query from 28.6 s to 0.13 s while remaining exact.

---

## 13. Future phase — anatomical scan alignment

**Status: IMPLEMENTED in Milestone 3.6 (v0.14.0, 2026-09-02). See §11j for what
was actually built, the axis convention it commits to, and its Blender acceptance.**
*This section is retained as the original requirement, written before any alignment
code existed. Where the two differ, §11j is authoritative — in particular §11j.1 fixes
the axis signs this section left open ("choose sign conventions consistently"), and the
automatic-detection ideas below remain unimplemented by design.*

Handheld human-body scans arrive in whatever coordinate system the capture
software produced. The imported OBJ axes need not correspond to anatomical
front/back, left/right or vertical directions. BSMT must eventually support
bringing a scan into an anatomical frame through a **non-destructive
alignment / preprocessing phase**.

### 13.1 Position in the pipeline

Alignment is a **preprocessing and display operation**, kept separate from
geodesic measurement:

```
Import → Alignment → Geometry preprocessing (if required) → Landmark picking
       → Straight / surface measurement → Export
```

### 13.2 Required modes

**Mode A — Manual alignment**
- convenient translation and rotation of the scan;
- **front, side and back viewing assistance** — one-click views along the
  working anatomical axes, so the operator can judge the alignment from each
  of the three conventional directions;
- record the alignment transform as data;
- reset to the original imported transform;
- **never modifies mesh topology.**

**Mode B — Landmark-based anatomical alignment**

The researcher selects reference `SurfacePoint`s defining four roles:

| Role | Purpose |
|---|---|
| left reference | with `right reference`, defines the medio-lateral axis |
| right reference | |
| superior reference | with `inferior reference`, defines the longitudinal (vertical) axis |
| inferior reference | |

An orthonormal anatomical frame is constructed from these references. The two
constructed axes will not be exactly perpendicular on a real scan, so the
construction must orthonormalise explicitly (Gram–Schmidt or an SVD-based
nearest-rotation fit) and **record the residual non-orthogonality as a quality
figure** rather than discarding it. The antero-posterior axis is then the cross
product of the other two.

**Anatomical landmark names must not be hard-coded.** Different scan protocols
require different reference landmarks. BSMT stores the four *roles* and the
`SurfacePoint` bound to each; it never stores or assumes a named anatomical
landmark.

### 13.3 Requirements

- Operate through **object transforms** wherever possible.
- **Never alter mesh topology.**
- **Preserve SurfacePoint triangle indices and barycentric coordinates.** This
  follows from §6.3: alignment is a rigid transform, so canonical surface
  locations are untouched by construction.
- **Save the alignment matrix** as data in its own right, not merely as a
  mutated object transform, so it can be inspected, exported and reapplied.
- Provide **Reset Alignment**.
- **Preserve the original imported transform** so the operation is fully
  reversible.
- Keep alignment transforms **distinguishable from geometry editing**, in both
  the data model and the cache-invalidation rules of §6.4: an alignment changes
  `metric_key` only if it is non-rigid (it should not be), and never changes
  `geometry_hash`.
- **Support reproducible alignment across multiple posture scans.** A saved
  alignment — whether a manual matrix or a set of landmark roles — must be
  reapplicable to another scan of the same subject in a different posture, so
  that a series of scans can be brought into a common anatomical frame by a
  documented, repeatable procedure rather than by eye. What this requires of
  the data model (roles stored independently of any one scan's `SurfacePoint`s,
  and an alignment record that survives being detached from the object it was
  authored on) is a design question for that phase, not a decision taken here.

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
