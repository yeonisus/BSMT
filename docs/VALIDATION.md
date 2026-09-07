# BSMT — Validation Record

**Status of this document:** living record. It states what has actually been
executed, what exists but has not been run, what has not been written, and
what cannot be settled by code at all. Nothing here is a claim about human
body measurement accuracy.

- **Software version under test:** BSMT 0.26.0 (`body_surface_measurement.VERSION`)
- **Host:** macOS (darwin 25.5.0), Apple Silicon / ARM64
- **Blender:** 4.5.13 LTS (hash `daeeeca98fb0`, built 2026-08-25)
- **Geodesic backend:** pygeodesic (MMP), bundled
- **Runs recorded here:** 2026-09-07, from the working tree (see
  [Development note](#development-note--repository-and-release-state))

---

## 0. How to read this document

### 0.1 Evidence status legend

Every section carries one of these. They are not interchangeable, and an
authored test is never reported as a validated result.

| Status | Meaning |
|---|---|
| **EXECUTED** | A suite was run, on the hardware and version named above, and its numerical output is reproduced here. |
| **AUTHORED — NOT EXECUTED** | Test code exists and is complete, but has not been run in the state recorded here. Produces no evidence. |
| **NOT IMPLEMENTED** | No test exists yet. The gap is described, not estimated away. |
| **REQUIRES HUMAN DATA** | Cannot be settled by code at all. Needs real scans, real operators, or a reference instrument. |

### 0.2 Two different kinds of claim

The sections below are not all the same kind of statement, and conflating them
is the main way a document like this misleads. They separate as:

**Software verification** — *does the program do what it is specified to do?*
These are decidable by code, and a passing suite settles them.

> Sections **B** (rigid invariance), **D** (repair invariance/locality),
> **E** (CSV export), **F** (protocol round-trip), **G** (failure/stale state),
> **H** (clean packaged install).

**Numerical / algorithm validation** — *how does the computed number relate to
a mathematically known answer?* These are decidable by code only where a
ground truth exists in closed form, which is why the fixtures are analytic
surfaces and not bodies.

> Sections **A** (analytic geometry) and **C** (decimation sensitivity).

**Neither** — *does the number correspond to the anatomical quantity a
researcher intends to measure?* No section of this document answers that.
See **Section I**.

### 0.3 Terminology, fixed

BSMT's backend computes an **exact geodesic on the input triangular mesh**.
That is the precise claim and the only one made here.

It is **not** an exact geodesic on the underlying smooth surface, and it is
**not** an exact measurement of a smooth anatomical surface. A triangulated
approximation of a curved surface is a chord approximation: its geodesics are
shorter than the smooth ones. That difference is a property of the mesh, not
an error in the solver, and Section A measures it directly.

Wherever this document says *polyhedral geodesic*, it means the exact value on
the mesh as given. Wherever it says *smooth geodesic* or *great circle*, it
means the closed-form value on the ideal surface the mesh was sampled from.

### 0.4 Summary of current evidence

| § | Area | Kind | Status | Executed |
|---|---|---|---|---|
| A | Analytic geometry | Numerical validation | **EXECUTED** — 50/50 pass | 2026-09-07 |
| B | Rigid-transform invariance | Software verification | **EXECUTED** — 63/63 pass | 2026-09-07 |
| C | Decimation sensitivity | Numerical characterisation | **EXECUTED** — 28/28 pass | 2026-09-07 |
| D | Repair invariance / locality | Software verification | **EXECUTED** — 40/40 pass (new suite) + 2 supporting suites | 2026-09-07 |
| E | CSV export | Software verification | **EXECUTED** — 312/312 pass at source; package-level validated in §H | 2026-09-07 |
| F | Protocol round-trip | Software verification | **EXECUTED** — 53/53 pass | 2026-09-07 |
| G | Failure / stale-state | Software verification | **EXECUTED** — 68/68 pass (new suite) + 6 supporting suites | 2026-09-07 |
| H | Clean packaged install | Software verification | **EXECUTED** — 79/79 pass from a clean 0.26.0 extraction | 2026-09-07 |
| I | Researcher-run validation | Empirical | **REQUIRES HUMAN DATA** — none performed | — |

### 0.5 Reproducing these runs

```sh
# A — analytic geometry              (~2 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_analytic_validation.py

# B — rigid-transform invariance     (~1 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_invariance_blender.py

# C — decimation sensitivity         (~12 min)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_decimation_sensitivity.py

# D — repair locality                (~1 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_repair_locality_blender.py

# G — failure / stale-state          (~1 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_stale_state_blender.py

# H — clean packaged validation      (~2 s; needs dist/ built first)
python3 tools/build_release.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_packaged_extension.py

# E, F — CSV export and protocol     (~1 s, no Blender needed)
python3 tests/test_export.py
```

Each Blender suite prints a machine-readable final line
(`BSMT_ANALYTIC_RESULT=`, `BSMT_INVARIANCE_RESULT=`,
`BSMT_DECIMATION_RESULT=`, `BSMT_STALE_RESULT=`,
`BSMT_REPAIR_LOCALITY_RESULT=`, `BSMT_PACKAGED_RESULT=`)
carrying the exit code, so a harness need not parse the log.

---

## A. Analytic geometry validation

> **Kind:** numerical / algorithm validation
> **Status: EXECUTED** — `tests/test_analytic_validation.py`
> **50 checks, 0 failures, runtime 2.21 s**, 2026-09-07

Three surfaces whose geodesics are known in closed form, measured through the
production pipeline at several mesh densities. The three do **not** test the
same thing, and the difference matters.

### A.1 Plane — a correctness test

Fixture: a flat 100 × 100 mm grid; path along the full diagonal.
Reference: the analytic diagonal, 100·√2 = **141.421356 mm**.

| Grid | Triangles | Mean edge *h* (mm) | Reference (mm) | BSMT straight (mm) | BSMT polyhedral geodesic (mm) | Abs err (mm) | Rel err |
|---|---|---|---|---|---|---|---|
| 5 × 5 | 50 | 28.4518 | 141.421356 | 141.421356 | 141.421356 | 0.00e+00 | 0.00e+00 |
| 11 × 11 | 242 | 11.3807 | 141.421356 | 141.421356 | 141.421356 | 0.00e+00 | 0.00e+00 |
| 21 × 21 | 882 | 5.6904 | 141.421356 | 141.421356 | 141.421356 | 0.00e+00 | 0.00e+00 |
| 41 × 41 | 3,362 | 2.8452 | 141.421356 | 141.421356 | 141.421356 | 0.00e+00 | 0.00e+00 |
| 81 × 81 | 13,122 | 1.4226 | 141.421356 | 141.421356 | 141.421356 | 0.00e+00 | 0.00e+00 |

**Observed:** on all five tested polyhedral fixtures the computed surface
distance agreed with the analytic diagonal to zero absolute error at the
recorded precision, and the surface distance agreed with the straight-line
distance to float error. Worst relative error across all densities:
**0.00e+00**.

**Interpretation.** For these particular fixtures the triangulated surface
coincides with the smooth plane — planar sampling discards nothing — so
polyhedral and smooth geodesics are the same quantity and exact agreement is
the expected outcome rather than a coincidence. This is therefore the one
fixture family in this document where agreement with the *smooth* surface is
observed; it is a property of planar fixtures and does not generalise to
curved surfaces. Any deviation here would have been a defect.

**Additional observations at this fixture:**

- An oblique (non-diagonal) path was also exact to **0.000e+00**.
- Edge-following Dijkstra on the *same* mesh returned **114.4975 mm** where the
  true value is **105.9481 mm** — 8.07% long. This is recorded to document why
  edge-following is retained only as a diagnostic and is never used as a
  measurement backend.

### A.2 Cylinder — discretisation-error measurement

Fixture: cylinder, r = 50 mm; a helical path.
Reference: the smooth-surface geodesic, **127.155428 mm**.

| Mesh | Triangles | Mean edge *h* (mm) | Smooth geodesic (mm) | BSMT polyhedral geodesic (mm) | BSMT straight (mm) | smooth − polyhedral (mm) | Rel gap |
|---|---|---|---|---|---|---|---|
| 24 × 12 | 576 | 16.9629 | 127.155428 | 127.017130 | 122.474487 | +1.38e-01 | 1.088e-03 |
| 48 × 24 | 2,304 | 8.4890 | 127.155428 | 127.120808 | 122.474487 | +3.46e-02 | 2.723e-04 |
| 96 × 48 | 9,216 | 4.2455 | 127.155428 | 127.146770 | 122.474487 | +8.66e-03 | 6.809e-05 |
| 192 × 96 | 36,864 | 2.1228 | 127.155428 | 127.153263 | 122.474487 | +2.16e-03 | 1.702e-05 |

**Observed:**

- At every density the polyhedral geodesic was **shorter** than the smooth
  geodesic — the sign a chord approximation requires — and never shorter than
  the straight-line distance.
- The gap shrank monotonically at every refinement.
- **Observed convergence trend: 4.0×, 4.0×, 4.0× reduction per halving of *h*.**

### A.3 Sphere — the same, with curvature in both directions

Fixture: sphere, r = 50 mm; a quarter great circle.
Reference: the great-circle arc, **157.079633 mm**. Lower bound: the chord,
141.421356 mm.

| Subdivisions | Triangles | Mean edge *h* (mm) | Great circle (mm) | BSMT polyhedral geodesic (mm) | Chord (mm) | gc − polyhedral (mm) | Rel gap |
|---|---|---|---|---|---|---|---|
| 1 | 80 | 58.2284 | 157.079633 | 151.763505 | 141.421356 | +5.32e+00 | 3.384e-02 |
| 2 | 320 | 29.8342 | 157.079633 | 155.665910 | 141.421356 | +1.41e+00 | 9.000e-03 |
| 3 | 1,280 | 15.0084 | 157.079633 | 156.717368 | 141.421356 | +3.62e-01 | 2.306e-03 |
| 4 | 5,120 | 7.5157 | 157.079633 | 156.986814 | 141.421356 | +9.28e-02 | 5.909e-04 |

**Observed:**

- Every value was bracketed correctly — below the great circle, above the
  chord — at every density.
- The gap shrank monotonically at every refinement.
- **Observed convergence trend: 3.8×, 3.9×, 3.9× reduction per halving of *h*.**

### A.4 What the cylinder and sphere results do and do not say

The gap columns above are **discretisation error of the fixture mesh**, not
solver error. The solver was handed a chord approximation lying inside the
smooth surface; a shorter geodesic is the correct answer *on that input*.

Reporting the 24 × 12 cylinder's 0.1% gap as "solver error" would be wrong in
both directions: it would blame the solver for the mesh, and it would imply
the solver carries an error budget that it does not.

On the convergence claim, stated precisely: **across the four densities tested
on each of two fixtures, the measured reduction per halving of *h* was
consistent with second-order (O(h²)) behaviour** — the ratios clustered at
4.0× (cylinder) and 3.8–3.9× (sphere) against the 4.0× that second order
predicts. This is an *observed numerical trend over the tested range*, on two
fixtures, at four densities each. It is reported as such and is not asserted
as a general theorem about the solver, about other surfaces, or about
densities outside the tested range.

### A.5 Metric properties on an arbitrary surface

Fixture: a torus; 5 vertices, all 10 distinct pairs.

| Property | Result |
|---|---|
| `d(a,b) ≥ straight-line(a,b)` | Held for all 10 pairs |
| Symmetry `d(a,b) = d(b,a)` | Held; worst residual **1.99e-13** |
| `d(a,a) = 0` | Held for all 5 vertices |
| Triangle inequality | Held: 101.5169 ≤ 110.6014 + 156.7174 |

---

## B. Rigid-transform invariance

> **Kind:** software verification
> **Status: EXECUTED** — `tests/test_invariance_blender.py`
> **63 checks, 0 failures, runtime 0.80 s**, 2026-09-07

A rigid motion is an isometry of the embedding, so it cannot change a distance
between two points **on** the surface. BSMT relies on that at three levels —
the canonical mesh keeps local coordinates, `geometry_hash` excludes
`matrix_world`, and `metric_key` is built from LᵀL. This suite measures
whether the property survives the whole pipeline, not each layer in isolation.

Fixture: a 2,208-triangle sphere scan, 4 landmarks, 4 measurements.

### B.1 Baseline, at the identity transform

| ID | Straight (mm) | Surface (mm) | Ratio |
|---|---|---|---|
| M01 | 111.065865 | 117.946800 | 1.0620 |
| M02 | 158.057251 | 182.792313 | 1.1565 |
| M03 | 93.717941 | 97.693932 | 1.0424 |
| M04 | 146.675995 | 165.050171 | 1.1253 |

Every measurement produced both distances, and surface ≥ straight held for all
four.

### B.2 Under rigid transforms

Worst difference from baseline across all four measurements, per pose:

| Pose | Coordinate reach (mm) | Worst **straight** diff (mm) | Worst **surface** diff (mm) | Tolerance (mm) | Margin |
|---|---|---|---|---|---|
| translate (1234.5, −6789.0, 42.0) | 6,789 | 6.104e-05 | **0.000e+00** | 6.789e-03 | 111× |
| rotate | 100 | 7.629e-06 | **0.000e+00** | 1.000e-04 | 13× |
| translate + rotate (−28570, −2692, −176) | 2.857e+04 | 5.646e-04 | 1.526e-05 | 2.857e-02 | 51× |
| another rotation | 100 | 1.526e-05 | 1.526e-05 | 1.000e-04 | 7× |

**Maximum observed numerical difference anywhere in this suite:
5.646e-04 mm (straight), 1.526e-05 mm (surface).**

Under pure translation and pure rotation the surface distances were
**bit-identical** to baseline. The largest surface deviation, 1.526e-05 mm,
occurred with the object placed roughly 28 metres from the origin.

### B.3 What else was verified under every pose

| Property | Result |
|---|---|
| **SurfacePoint validity** | Every SurfacePoint remained `VALID` under every pose. |
| **SurfacePoint identity** | Each still named the **same triangle index and the same barycentric coordinates**. |
| **Geometry hash** | `c90f590a6e2b6e5ca85878740ea4122a` — **identical across all four poses**. A transform is correctly not treated as a geometry change. |
| **Measurement IDs** | Unchanged. |
| **Statuses** | Unchanged. |

The metric key itself was not asserted directly by name in this suite; what is
recorded is the observable consequence — recalculation after a transform
reproduced the same distances without a geometry-hash change.

### B.4 Cache reuse — no unnecessary solver calls

pygeodesic was instrumented directly (construct and query counters) for this
suite.

| Action after a rigid transform | Solver calls |
|---|---|
| Moving the object, then `Calculate All` against stored results | **0** |
| Refreshing the measurement visualization | **0** |

Stored results remained marked valid with identical numbers; the cached
surface path survived with the **same generation counter**, and the result it
belongs to stayed valid. An exact surface path computed at the transformed
pose agreed with the stored surface distance to 2.485e-06 mm over 40 points.

### B.5 Save / reload

The `.blend` was saved in the transformed pose of B.2 and reloaded.

| Property | Result |
|---|---|
| Landmarks recovered | 4 / 4 |
| Measurements recovered | 4 / 4 |
| SurfacePoint validity | All `VALID` |
| SurfacePoint identity | Same triangle and barycentric |
| Measurement IDs | Survived |
| Stored results | Survived **unchanged** |
| Recalculation after reload | Succeeded; reproduced every surface distance to **1.526e-05 mm** against a 2.000e-03 mm allowance |

### B.6 The tolerance, and its justification as implemented

The tolerance is `TRANSFORM_RELATIVE = 1e-6`, applied relative to the
coordinate reach of the pose.

The justification implemented in the suite is this:
`geodesic/spaces.py::to_solver_space`
builds the solver mesh by applying `matrix_world`, because the solver works in
physical millimetres and the object transform is what carries the scan into
them. A rigid motion therefore *does* reach the solver's input, as a rotation
that is mathematically an isometry but is stored by Blender in **single
precision**. A distance computed through `matrix_world` consequently carries
roughly 1e-7 of the coordinate magnitudes involved; 1e-6 is that figure with
headroom, and it is the same constant family `alignment.position_tolerance`
uses for the same reason (PROJECT_SPEC §11aa.3).

It scales with the scene because the error does. No other rationale is claimed
here, and the tolerance was not adjusted for this run — measured errors sat
7×–111× inside it.

The suite's own history is recorded in its docstring: an earlier version
asserted bit-identical results on the reasoning that the solver sees only
untouched local vertices. That reasoning was wrong, and the test caught it.

---

## C. Decimation / preprocessing sensitivity

> **Kind:** numerical **characterisation** — see C.4 before quoting any number
> **Status: EXECUTED** — `tests/test_decimation_sensitivity.py`
> **28 checks, 0 failures, runtime 698.82 s (≈ 11 min 39 s)**, 2026-09-07

### C.1 Fixture and run conditions

- Fixture: a torus, **608,000 triangles** as built.
- Decimation targets: 500,000 / 350,000 / 200,000 triangles, produced through
  the production `preprocess` module, method `DECIMATE_COLLAPSE`.
- Every target was met exactly — **0.0% from requested** at all three.
- The **source scan was verified unmodified** at each target; each decimation
  produced a separate measurement copy.

Run conditions observed:

| Condition | Result |
|---|---|
| Warnings | **None** |
| Solver failures | **None** |
| Topology / solver safety gate failures | **None** |
| Non-manifold edges | **0** at 608k, 500k, 350k and 200k |
| Degenerate triangles | **0** at 608k, 500k, 350k and 200k |

### C.2 Comparing the same path across densities

Decimation renumbers every triangle, so a SurfacePoint cannot survive it — a
triangle index means nothing on the decimated mesh. The comparison is
therefore made **by position**: the same 3D locations on the original surface,
each re-attached to the nearest point of each decimated surface through the
canonical BVH. That is what a researcher re-picking a landmark on a decimated
copy would produce, minus their own hand.

That re-attachment is itself a source of difference, and it is reported as the
**anchor drift** column — how far the re-attached point sits from the original
position. **A distance difference smaller than the drift is not evidence about
decimation.**

### C.3 Results

| Path category | Mesh | Triangles | Surface (mm) | Diff (mm) | Diff (%) | Anchor drift (mm) | Solve (s) |
|---|---|---|---|---|---|---|---|
| nearly flat | original | 608,000 | 111.9997 | +0.0000 | +0.0000 | 0.0016 | 1.78 |
| nearly flat | 500k | 500,000 | 111.9997 | −0.0000 | −0.0000 | 0.0016 | 1.61 |
| nearly flat | 350k | 350,000 | 111.9997 | −0.0000 | −0.0000 | 0.0016 | 1.07 |
| nearly flat | 200k | 200,000 | 111.9986 | −0.0010 | −0.0009 | 0.0074 | 0.29 |
| moderate curvature | original | 608,000 | 209.0468 | +0.0000 | +0.0000 | 0.0017 | 4.52 |
| moderate curvature | 500k | 500,000 | 209.0468 | +0.0000 | +0.0000 | 0.0017 | 3.27 |
| moderate curvature | 350k | 350,000 | 209.0464 | −0.0004 | −0.0002 | 0.0039 | 2.31 |
| moderate curvature | 200k | 200,000 | 209.0454 | −0.0014 | −0.0007 | 0.0058 | 0.79 |
| high curvature | original | 608,000 | 134.9352 | +0.0000 | +0.0000 | 0.0020 | 3.56 |
| high curvature | 500k | 500,000 | 134.9352 | +0.0000 | +0.0000 | 0.0020 | 2.87 |
| high curvature | 350k | 350,000 | 134.9369 | +0.0018 | +0.0013 | 0.0013 | 1.47 |
| high curvature | 200k | 200,000 | 134.9383 | +0.0031 | +0.0023 | 0.0069 | 0.71 |
| long body-like | original | 608,000 | 711.3914 | +0.0000 | +0.0000 | 0.0031 | 193.14 |
| long body-like | 500k | 500,000 | 711.3917 | +0.0003 | +0.0000 | 0.0032 | 144.88 |
| long body-like | 350k | 350,000 | 711.3940 | +0.0026 | +0.0004 | 0.0032 | 53.29 |
| long body-like | 200k | 200,000 | 711.3996 | +0.0081 | +0.0011 | 0.0051 | 17.89 |

Path categories, as chosen for geometric variety:

| Category | Description | Baseline surface (mm) | Baseline straight (mm) |
|---|---|---|---|
| nearly flat | a short span across the outer equator, where the surface is almost developable | 111.9997 | 111.6343 |
| moderate curvature | over the shoulder of the tube, crossing about a quarter turn of the minor circle | 209.0468 | 191.8884 |
| high curvature | across the inner (saddle) side, the worst-conditioned region | 134.9352 | 130.2957 |
| long body-like | a long wrapping path of the order a torso girth would be | 711.3914 | 655.4474 |

**Worst absolute deviation across every path and density: 0.0081 mm
(0.0023%),** at the long path on the coarsest mesh. Every decimated mesh still
measured every path; no path moved by more than 1% of its length.

**Solve-time observation.** For the girth-scale path, solve time fell
**193.14 s → 144.88 s → 53.29 s → 17.89 s** across 608k → 500k → 350k → 200k,
a 10.8× reduction at the coarsest mesh. (The same path measured directly in
the suite's baseline section, without re-attachment, took 238.44 s.) This is
the practical quantity being traded, and over the tested range it moved by far
more than the distances did.

### C.4 What these numbers do NOT establish

This section is **numerical sensitivity characterisation only**. Stated
explicitly:

1. **This does not scientifically validate 350,000 triangles as a default
   target for human body measurement.** It cannot: the fixture is an analytic
   torus, not a body; the paths were chosen for geometric variety, not
   anatomy; and no reference measurement from an accepted method was compared
   against. What the table establishes is the *shape* of the sensitivity —
   which way the error goes, roughly how large it is over this range, and
   whether it is ordered by density — so that a researcher choosing a target
   knows what is being traded. The scientific justification of a default
   target remains listed in **Section I** as not yet validated.

2. **Decimation moves the surface, and therefore moves equivalent anchor
   locations.** A landmark cannot be carried across a decimation by index; it
   must be re-attached, and re-attachment is itself a displacement. The anchor
   drift column measures that displacement, and it is of the same order as —
   in most cells larger than — the distance differences being reported.

3. **This fixture does not replace validation on real human scans.** A real
   scan has noise, holes, self-contact, non-uniform sampling and anatomically
   meaningful landmarks. None of those are present here.

### C.5 Two caveats the numbers themselves raise

- **In 10 of the 12 decimated cells the deviation is smaller than the anchor
  drift** for that cell, and is therefore below the re-attachment noise floor —
  by the criterion stated in C.2, those cells are not evidence about
  decimation at all. Only *high curvature @ 350k* (0.0018 mm vs 0.0013 mm
  drift) and *long body-like @ 200k* (0.0081 mm vs 0.0051 mm drift) rise above
  it. The honest summary is "the effect is at or below the noise floor of
  re-picking a landmark over the tested range", not "the effect is 0.0081 mm".

- **The sign of the error is not uniform.** The nearly-flat and
  moderate-curvature paths became *shorter* under decimation; the
  high-curvature and long paths became *longer*. A single signed correction
  factor would be wrong.

---

## D. Repair invariance / locality

> **Kind:** software verification
> **Status: EXECUTED — COMPLETE**
> Primary evidence: `tests/test_repair_locality_blender.py` —
> **40 checks, 0 failures**, 2026-09-07, confirmed identical across **5
> consecutive runs**
> Supporting: `tests/test_mesh_repair_blender.py` **76/0** (0.69 s),
> `tests/test_repair.py` **238/0** (offline), both executed 2026-09-07

BSMT's repair is **conservative and local under a narrow defect policy**. It
is not a general mesh-healing facility, and this section does not claim it is:
it merges *exactly coincident* vertices that produce zero-area triangles, and
refuses everything else.

### D.1 Two different claims, deliberately separated

Confusing these is the trap this section exists to avoid.

1. **Data-state invalidation policy — GLOBAL.** Repair changes the mesh, so
   the geometry hash changes, so `landmarks.classify` marks **every** landmark
   on that object non-VALID — not only those near the repair. Nothing is
   re-projected. This is deliberate: a stored triangle index is meaningless
   once triangles have been renumbered, and silently re-attaching a
   researcher's landmark would move their data.

2. **Geometric locality of the repair — STRICTLY LOCAL.** The surface itself
   changes only inside the defect neighbourhood. Every vertex outside it keeps
   its exact coordinates, and a distance measured between two points away from
   the repair is the same number afterwards.

These are not in tension. (1) says the researcher must re-pick and recompute.
(2) says that when they do, they get the same answer. Because (1) holds,
locality cannot be demonstrated by carrying a SurfacePoint across a repair —
the product refuses to do that, correctly — so (2) is demonstrated
geometrically: the same 3D positions are re-attached to the repaired surface
through the canonical BVH, exactly as a researcher re-picking would, and the
distances are compared.

### D.2 Fixture

A 100 mm sphere source scan (64 × 32 segments) and a real measurement mesh
produced by the production operator. Six exactly-coincident vertex pairs were
planted **confined to the north cap** (z ≥ 0.55 R; lowest defect vertex at
z = 63.44), producing 12 zero-area triangles while leaving the mesh manifold,
closed and single-component.

Two landmark pairs:

| Pair | Region | Distance to nearest defect vertex |
|---|---|---|
| **FAR** | south cap (z = −55.56, −95.69) | **119.4 mm** |
| **NEAR** | north cap, among the defects (z = 99.52, 77.30) | adjacent |

### D.3 Source immutability

Repair runs on the measurement mesh. The source scan the researcher imported
was compared before and after, deterministically:

| Check | Result |
|---|---|
| Vertex count | unchanged |
| Polygon and triangle counts | unchanged |
| **Every vertex coordinate** | **bit-identical** (`np.array_equal` over the full float64 array) |
| **Every triangle index** | **identical** (`np.array_equal` over the full index array) |
| **Geometry hash** | **unchanged** |

Independently corroborated by `tests/test_mesh_repair_blender.py` §J —
*"the source is byte-for-byte as it was"* — with the preprocessing record
appended rather than overwritten.

### D.4 Local geometry change bound

| Quantity | Before | After | Change |
|---|---|---|---|
| Vertices | 1,986 | 1,980 | **−6** |
| Polygons | 2,048 | 2,047 | −1 |
| Triangles | 3,968 | 3,956 | **−12** |
| Degenerate triangles | 12 | **0** | cleared |
| Non-manifold edges | 0 | 0 | none introduced |
| Boundary edges | 0 | 0 | none introduced |
| Components | 1 | 1 | none introduced |
| **Vertices outside the repair region that moved** | — | — | **0** |

Defect vertex indices (pre-repair numbering):
`[0, 1, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]`.

Because repair merges vertices and renumbers indices, the comparison is
positional rather than by index:

- **No new vertex position was invented** — the set of positions after repair
  is a subset of the set before.
- **Every position that vanished was a defect position.**
- **Exactly one vertex per coincident pair was removed** (6 removed for 6
  pairs), and the 12 triangles removed are exactly the 12 zero-area ones.
- Restricting to vertices outside the repair region (z < 0.55 R), the count is
  unchanged and the **worst coordinate delta is 0.000e+00 mm** — not "small",
  but exactly zero.

### D.5 Geometric locality — the same positions give the same number

| Pair | Region | Surface before (mm) | Surface after (mm) | Δ (mm) | Straight before | Straight after | Re-attach drift |
|---|---|---|---|---|---|---|---|
| **FAR** | south cap | 68.693613144 | 68.693613144 | **0.000e+00** | 67.377969356 | 67.377969356 | 0.000e+00 |
| **NEAR** | north cap (repaired) | 58.854336140 | 58.854336140 | **0.000e+00** | 58.056942561 | 58.056942561 | 0.000e+00 |

The FAR pair, 119.4 mm from the nearest defect, re-attached with **zero
drift** and returned a **bit-identical** surface and straight distance.

The NEAR pair — whose endpoints sit among the repaired defects — also returned
a bit-identical distance. That is the expected consequence of the defect
policy rather than a surprise: the repair removes only *zero-area* triangles,
and a triangle of zero area contributes no length to any geodesic crossing it.
A repair that changed distances in its own region would mean it had moved real
surface, which is precisely what the policy forbids.

The repaired mesh passes the solver safety gate, and the tolerance budgeted
for re-attachment (1e-6 mm, float64 barycentric reconstruction noise) was not
consumed at all.

### D.6 Unsafe repair rollback

`tests/test_mesh_repair_blender.py` §K, re-executed 2026-09-07. A fan-collapse
fixture — degenerate and manifold to begin with — is one whose repair would
worsen topology:

| Check | Result |
|---|---|
| The repair is refused | **PASS** |
| It says the topology would get worse | **PASS** |
| The mesh was restored, not left half-repaired | **PASS** |
| Its counts are exactly as they were | **PASS** |

A degenerate *sliver* (§F) is likewise listed as not locally repairable, the
preview refuses it in words, and nothing is changed.

### D.7 Near-coincident safety — no tolerance weld

Anatomically the important negative result: two vertices that are *close* but
not *identical* are never merged. Verified structurally and behaviourally
(§B, §D/E of `tests/test_mesh_repair_blender.py`, re-executed 2026-09-07):

- A fixture with a coincident vertex but **no** degenerate triangle: nothing
  is listed as a defect, the preview **refuses to invent a repair**, **no
  vertex was merged**, and the verdict is not NOT READY for coincidence alone.
- `apply_degenerate_plan` **never calls** `remove_doubles`,
  `dissolve_degenerate`, `fill_holes`, `holes_fill`, `triangle_fill`, or
  `automerge`.
- It **welds an explicit targetmap instead**, and **takes no distance or
  tolerance argument**. The pure planner has no tolerance either.

There is therefore no distance parameter anywhere on this path that could be
widened by accident into a global weld.

### D.8 Post-repair topology gate

After repair the degenerate count is 0, no non-manifold edge, boundary edge or
component was introduced, `classify_ready` no longer blocks, the solver gate
allows the mesh, and `mesh_verdict` agrees — the same single policy used
everywhere else (`tests/test_mesh_repair_blender.py` §A, and Section G of this
document).

### D.9 Limitations

- **This is not general mesh healing.** The supported defect is *exactly
  coincident vertices producing zero-area triangles*. Slivers, fan collapses,
  holes and non-manifold junctions are **refused**, not repaired. Section D
  makes no claim beyond that policy.
- **The fixture is an analytic sphere with synthetic defects**, not a real
  scan with real scanner artefacts. It establishes the locality and
  immutability properties of the algorithm, not its adequacy on any particular
  body scan. Real-scan repair behaviour is listed in Section I.
- **The "repair region" is defined by the test**, as the cap the defects were
  planted in. The product does not expose a region boundary; what is measured
  is that no vertex outside that cap moved.
- The pre-repair FAR/NEAR baselines call the solver directly, **deliberately
  bypassing the readiness gate** — which correctly refuses the whole mesh
  because it is degenerate somewhere. That bypass is legitimate only because
  this is a geometric experiment about one region; it is not a claim that BSMT
  would measure an unrepaired mesh. It does not, as Section G records.


## E. CSV export validation

> **Kind:** software verification
> **Status: EXECUTED at source level** — `tests/test_export.py`
> **312 export checks, 0 failures** (365 total in the file, of which 53 are
> protocol checks reported in Section F), runtime < 1 s, 2026-09-07
> **Package-level CSV export: NOT VALIDATED** — see E.8

### E.1 Schema version

`export.SCHEMA_VERSION = 2`, written as the **first column of every row** in
both files. A reader that has to guess which columns a file has is a reader
that will one day guess wrong; the version lets a script refuse a layout it
does not know instead of silently reading the wrong field.

| Version | Introduced | Change |
|---|---|---|
| 1 | 0.18.0 | The original layout. |
| 2 | *pending release* — see [Development note](#development-note--repository-and-release-state) | Stable ids and protocol names added, so every row can be identified without relying on an optional protocol id. |

Current layout: **33 measurement columns, 27 landmark columns.** Verified: no
column is repeated, `schema_version` leads both files, and the session block
(`subject_id`, `condition`, `scan_id`, `landmark_protocol`,
`measurement_protocol`) follows it in that order.

### E.2 Identifiers

Two identifiers are exported, and they are not interchangeable:

- `measurement_id` / `landmark_id` — the researcher's **protocol code**.
  **Optional**; a scene where nobody filled it in exports rows that cannot be
  told apart by it.
- `measurement_stable_id` / `landmark_stable_id` /
  `from_landmark_stable_id` / `to_landmark_stable_id` — **BSMT's own
  monotonic id**, never reused within a scene, always present.

Both are exported so a row can always be identified, and so a protocol code
can still serve as the join key when there is one. The landmark stable ids on
a measurement row are read from the **definition**, not from the resolved
landmark, so they stay meaningful even when the landmark they name has been
deleted.

Both files also name the protocols that produced them
(`landmark_protocol`, `measurement_protocol`), because an exported measurement
is reproducible only if the reader knows *which* protocol produced it.

### E.3 Units

Verified: **every column carrying a distance ends in `_mm`**, and every
landmark millimetre column is named `physical_mm_*`. A landmark's world
coordinates carry an explicit `coordinate_unit` field. An unpicked landmark
states no unit and no coordinate, but keeps its definition row (name and id).

Numbers are written at `DECIMALS = 6` fixed decimal places.

### E.4 Status, stale, failed and not-computed

This is the failure mode the section exists for: a downstream `mean()` over a
column where "not computed" arrived as `0.0`.

Verified **exhaustively** — every status the measurement manager can produce
(`VALID`, `READY`, `STALE`, `DRAFT`, `FAILED`, `NOT_COMPUTED`, `UNRESOLVED`,
and blank) crossed with both validity flags, 129 checks in total:

- A value that is **valid and genuinely zero** writes as `0.000000`.
- A value that is **not valid** writes as **empty**, never `0`.
- The `surface_to_straight_ratio` is written only when **both** distances are
  present.
- The status string is **always** written, including when blank.
- A `NaN` that reached the record exports as **empty**, not the text `nan`.

STALE and FAILED rows therefore stay visible in the file, carrying their
status and their identity, with no numbers.

### E.5 Deterministic ordering

Verified:

- Rows come out in **the order the collection gave them** — never sorted.
- **Two exports of the same rows are byte-identical**, so a diff between two
  export files is a difference in the data and never in the writer.

### E.6 Encoding, escaping and newlines

- Encoding is **UTF-8 with BOM** (`utf-8-sig`). The BOM is deliberate: without
  it Excel on Windows misreads UTF-8. Python's `csv` handles it with
  `encoding="utf-8-sig"`, R with `fileEncoding="UTF-8-BOM"`.
- Verified: Korean names, commas and embedded quotes survive a round trip;
  filenames containing characters a filesystem refuses are sanitised.
- The writer emits **CRLF**, which RFC 4180 and Excel expect, with **no bare LF
  outside a CRLF pair**.

### E.7 Cross-platform parsing — tested by parsed rows, not raw bytes

Raw text differs by line terminator between platforms and between editors that
"helpfully" normalise a file, so a golden test comparing bytes would fail for
reasons unrelated to the data. What is compared is **what a reader gets back**.

Verified: a written file parsed as CRLF, then with line endings rewritten to
LF, then to CR, produced **identical parsed rows** in all three cases. Against
that, a field-by-field golden expectation was asserted for three rows spanning
`VALID`, `READY` (surface missing) and `STALE` (both missing) — including
`schema_version == "2"` on every row.

### E.8 What is NOT yet validated here

**Package-level CSV export is now validated — see §H.8.** The suites in this
section run against the **working tree**. When it was written, the newest
build in `dist/` was 0.25.2, produced before the schema-v2 change, so a
packaged export would have validated schema v1. That has since been resolved:
0.26.0 was built and `tests/test_packaged_extension.py` writes and parses a
schema-v2 CSV **from a clean extraction of the released ZIP**, including the
blank-not-zero handling of an invalidated row.

---

## F. Protocol round-trip validation

> **Kind:** software verification
> **Status: EXECUTED — COMPLETE for the behaviours listed below**
> `tests/test_export.py`, protocol sections: **53 checks, 0 failures**,
> 2026-09-07

A protocol file is a **reusable definition**, carrying what a study decided
and nothing about the scan it was decided on.

### F.1 Definitions survive the round trip (13 checks)

Verified: the protocol name; every landmark, **in order**; every measurement;
the disabled state; the measurement type; Korean names; notes. Saving to a
file and loading it back yields the same object. The file is human-readable
JSON and holds non-ASCII names unescaped.

### F.2 Stable IDs (verified in F.1 and F.3)

Verified: **stable ids are preserved exactly, not renumbered** — including a
deliberate gap in the id sequence (3→6), which a renumbering implementation
would silently close. Measurement endpoints are **referenced by stable id**,
not by name or by list position.

Refused on load: a duplicate landmark stable id, a duplicate measurement
stable id, a stable id of zero.

### F.3 Nothing scan-, subject- or result-specific gets in (24 checks)

Verified by exhaustive absence — a written protocol file never mentions:

`triangle_index` · `barycentric` · `world_xyz` · `geometry_hash` ·
`component_id` · `source_object` · `physical_mm` · `straight_distance` ·
`surface_distance` · `subject_id` · `condition` · `scan_id` · `A_BSMT` ·
`path` · `backend`

Further verified: only the four expected sections exist; a landmark entry
holds **only identity**; a measurement entry holds **only its definition**.
And on the reading side, a file carrying a triangle index, a file carrying
subject data, and a file carrying a result are each **refused**, not silently
accepted and ignored.

### F.4 Refusals and backward compatibility (16 checks)

Refused: a measurement referencing an undefined landmark; a measurement with
the same landmark at both ends; a protocol with no landmarks; a landmark with
no name; an unknown measurement type; a file that is not JSON; a JSON array
instead of an object; a version from the future. Allowed: landmarks with no
measurements.

The two earlier formats still round-trip and remain distinguishable from the
unified format.

### F.5 Assessment

For the behaviours enumerated above — definitions, pair definitions, stable
ids, names and order, and the exclusion of scan-specific coordinates and
results — the current tests genuinely establish the claim, by round trip in
one direction and by explicit refusal in the other. This section is marked
**COMPLETE at source level**, with the same package-level caveat as E.8.

---

## G. Failure / stale-state validation

> **Kind:** software verification
> **Status: EXECUTED — COMPLETE**
> Primary evidence: `tests/test_stale_state_blender.py` —
> **68 checks, 0 failures, runtime 0.72 s**, 2026-09-07
> Supporting suites re-executed the same day, all passing (§G.2)

The failure mode this section exists for is a single sentence: **a number
computed against one configuration must never be shown, reused or exported
once that configuration has changed.** Every check below is that sentence in
different words.

### G.1 Why solver constructions are counted, not reasoned about

"The gate refuses it before solving" is a claim about control flow, and
control flow is what drifts. `PyGeodesicAlgorithmExact` is the only door to
the native solver, so its constructor is wrapped and constructions are
counted across each block that is supposed to refuse. This turns the claim
into a measurement. The technique is shared with
`tests/test_degenerate_policy.py`, which proves the same property for the
blocking-defect list.

**Every refusal and every invalidation path recorded in this section ran with
zero pygeodesic constructions.** Nine separate blocks assert it.

### G.2 Suites executed for this section

| Suite | Checks | Failures | Runtime |
|---|---|---|---|
| `tests/test_stale_state_blender.py` (new) | 68 | 0 | 0.72 s |
| `tests/test_degenerate_policy.py` | 35 | 0 | 0.55 s |
| `tests/test_picking_transforms.py` | 45 | 0 | 1.33 s |
| `tests/test_mesh_repair_blender.py` | 76 | 0 | 0.71 s |
| `tests/test_preprocess_blender.py` | 110 | 0 | 115.74 s |
| `tests/test_surface_distance.py` (offline) | 90 | 0 (14 skipped groups) | < 1 s |
| `tests/test_export.py` (offline) | 365 | 0 | < 1 s |

**789 checks, 0 failures** across the seven suites.

### G.3 Behaviour-by-behaviour evidence

| Behaviour | Evidence | Resulting state | Solver called | Old value reused | Result |
|---|---|---|---|---|---|
| **Geometry edit** | `test_stale_state_blender.py` §A | every dependent measurement → `STALE`; cached path no longer `PATH_CACHED`; definitions survive | **0 constructions** | No — number dropped, not kept | **PASS** |
| **Geometry edit (sweep)** | §A, `state.invalidate_for_geometry_change` | reports 2 measurements invalidated; none holds a number | **0** | No | **PASS** |
| **Scale change (uniform)** | §B | every measurement → `STALE`, reason names scale/unit | **0** | No — dropped rather than rescaled | **PASS** |
| **Scale change (non-uniform)** | §B | every measurement → `STALE` | **0** | No | **PASS** |
| **Coordinate-unit change** | §B | every stored result dropped | **0** | No | **PASS** |
| **Rigid motion (control)** | §B | every measurement stays `VALID`, numbers identical | n/a | Correctly reused | **PASS** |
| **Landmark repick** | §C | exactly 1 of 2 measurements affected; the other keeps its number byte-for-byte | **0** | No | **PASS** |
| **Landmark repick (path)** | §C | cached path passively detects the moved endpoint | **0** | No | **PASS** |
| **Repair** | `test_mesh_repair_blender.py` §G, §J, §K and `test_repair_locality_blender.py` §F | every landmark on a repaired mesh → non-`VALID`, never re-projected; no measurement keeps a number; no cached path survives; source scan untouched; a worsening repair refused | — | No | **PASS** (see §D) |
| **Hidden / excluded measurement mesh** | `test_picking_transforms.py` §G | pick refused with *"hidden in the viewport"* + how to fix; covers eye flag, monitor flag, hidden collection, and collection excluded from the view layer | — | — | **PASS** |
| **— never falls through to the source scan** | `test_picking_transforms.py` §G, Show Source case | with the copy hidden and the source coincident and identical, picking still **refuses** rather than silently hitting the source | — | — | **PASS** |
| **Cross-component pair** | `test_stale_state_blender.py` §E, through the real operator | no distance produced; status not `VALID`; reason names the disconnection; path also refused | **0 constructions** | No | **PASS** |
| **Cross-component (unit)** | `test_surface_distance.py`, `test_degenerate_policy.py` | refused with code `DISCONNECTED` in `solve.validate_points` | before solver | — | **PASS** |
| **Blocking topology** | `test_degenerate_policy.py` §A | degenerate mesh refused on all four routes (surface distance, surface path, forced path, A/B distance); no distance stored, no path cached; failure names the defect | **0 constructions** ×4 | No | **PASS** |
| **Density guard** | `test_stale_state_blender.py` §D | above threshold → refused, refusal names the threshold and the remedy; unticking downgrades to a warning; guard is operational, not a blocking defect | **0** | — | **PASS** |
| **Density guard — prior state** | §D | results valid before the refusal are **identical** after it | **0** | n/a | **PASS** |
| **Stale cached-path handling** | §A, §C, `test_pathcache.py`, `test_path_visualization.py` | `path_cache_state` reports a non-`CACHED` state with a reason; nothing is deleted and nothing is silently re-solved | **0** | No | **PASS** |
| **Export — stale / failed / not-computed** | §F (in Blender) and `test_export.py` (129 offline checks) | invalidated row exports **blank**, explicitly not `0.000000`; straight distance blank; no ratio invented; identity (`measurement_id`, `measurement_stable_id`) kept; status always written | — | No | **PASS** |

### G.4 Export of a failed state, verified in Blender rather than only offline

Section E.4 proves the export **rules** exhaustively on constructed records.
§F of the new suite proves the **states this section produces actually reach
those rules**: a genuinely refused cross-component measurement and a
genuinely invalidated measurement were exported through
`state.measurement_export_record` → `export.measurement_row`, and neither
produced a number. A `STALE` row keeps `measurement_id`, `measurement_stable_id`
and a non-empty `status`, so it is identifiable and filterable rather than
merely blank.

### G.5 One asymmetry, recorded rather than assumed

A cached **path** stores its own endpoint record — `path_source_triangle`,
`path_source_bary`, and the target equivalents — and
`state._endpoints_match` verifies them passively, so a moved endpoint is
caught with no event to hear.

A stored **result** has no equivalent: it keeps `result_object`,
`result_geometry_hash` and `result_metric_tensor` only. Its invalidation on a
re-pick is therefore **event-driven** — the operator that moves the landmark
calls `state.invalidate_measurements_for_landmark`.

That event is fired by every production path that can move a landmark, and
each was located and checked in the source for this section:

| Path | Call site |
|---|---|
| Re-pick | `operators.py:276` |
| Position cleared | `state.py:1750` (`clear_landmark_position`) |
| Landmark deleted | `state.py:1713` (`remove_landmark`) |
| After a repair | `operators.py:3899` |
| Protocol load | writes no coordinates at all (§F.3), so it cannot move a point |

So there is **no reachable path in the current product** where a landmark
moves and a dependent result stays valid, and §C measures the event working.
The asymmetry is nonetheless a defence-in-depth gap rather than a defect: a
result cannot self-check its endpoints the way a path can, so a future
mutation path that forgot the explicit call would not be caught by a second
line of defence. Recorded here so the next person changing landmark handling
knows the invariant is maintained by callers, not by comparison.

*(This was found by a test that initially asserted the passive behaviour and
failed. The test was corrected to assert what the product actually
guarantees; the policy was not weakened to make it pass.)*

### G.6 Limitations of this section

- The density guard is exercised through `preprocess.preflight` with a
  synthetic topology report rather than by building a >`DEFAULT_DENSE_THRESHOLD`
  mesh, which would add minutes for no additional decision coverage — the
  guard's input is the triangle count and nothing else. The *real* dense path
  is covered separately by `test_preprocess_blender.py`, executed above on a
  1,046,528-triangle fixture.
- Repair and hidden-mesh behaviour are cited from their own suites rather
  than re-proved here; both were re-executed for this document (§G.2).
- Everything in this section runs against the **working tree**. Package-level
  behaviour is Section H.


## H. Clean packaged validation

> **Kind:** software verification
> **Status: EXECUTED — COMPLETE**
> `tests/test_packaged_extension.py` — **79 checks, 0 failures**, 2026-09-07
> Supporting: `tests/test_portability.py` **87/0** (offline, static source audit)

Every other suite in this document imports BSMT from the working tree. That
proves the source is right and proves nothing about what a researcher
installs. This section extracts the released ZIP into a temporary directory
**outside the repository** and validates that.

### H.1 Artefacts under test

Built by `python3 tools/build_release.py` from `VERSION = (0, 26, 0)`:

| Package | Bytes | SHA256 |
|---|---|---|
| `dist/bsmt-0.26.0.zip` (Blender extension) | 1,886,006 | `43d3ee7d1038cd735f1a96b90faddd38e18d8aca33651f98726f7179570de4bb` |
| `dist/body_surface_measurement-0.26.0.zip` (legacy add-on) | 281,909 | `68feb85a1d3ffd694705c96d517682bb7f9ecc52ba4521cbed371b6a738d0274` |

### H.2 Import isolation — asserted, not assumed

If the working tree could satisfy the import, this suite would silently be
testing the working tree again — the one failure it exists to prevent. So the
isolation is verified four ways:

| Check | Result |
|---|---|
| Extraction directory is outside the repository | **PASS** (`/var/folders/…/bsmt-packaged-*`) |
| Repository root removed from `sys.path` | **PASS** |
| No `body_surface_measurement` left in `sys.modules` | **PASS** |
| Imported `__file__` is under the extraction directory | **PASS** |
| Imported `__file__` is *not* under the repository | **PASS** |
| **No packaged submodule resolved to the working tree** | **PASS** — all **35** loaded BSMT modules came from the extraction directory |

### H.3 Package identity

| Check | Result |
|---|---|
| `VERSION == (0, 26, 0)` | **PASS** |
| `export.SCHEMA_VERSION == 2` | **PASS** |
| Extension manifest declares `version = "0.26.0"` | **PASS** |
| Manifest declares `platforms = ["macos-arm64", "windows-x64"]` | **PASS** |

### H.4 Registration and clean unregistration

Registers in a `--factory-startup` Blender with no traceback; the scene
carries BSMT properties afterwards. On `unregister()`: no traceback, **every
workflow panel class removed from `bpy.types`**, and the scene property group
gone. (These are real assertions — an earlier draft of the final check was a
tautology and was replaced.)

### H.5 Eight workflow panels, in order — from the package

Exactly **eight** top-level panels, each asserted by position:

| # | Stage | Result |
|---|---|---|
| 1 | Scan Setup | **PASS** |
| 2 | Scan Preprocessing | **PASS** |
| 3 | Mesh Repair | **PASS** |
| 4 | Alignment | **PASS** |
| 5 | Landmark Manager | **PASS** |
| 6 | Measurement Manager | **PASS** |
| 7 | Measurement Visualization | **PASS** |
| 8 | Results and Export | **PASS** |

### H.6 Bundled pygeodesic — the wheel actually loading

`platforms` in a manifest is a claim. To test it, the macOS ARM64 wheel was
unpacked **out of the extension ZIP**, placed on `sys.path` ahead of
everything, and imported:

| Check | Result |
|---|---|
| Extension ZIP carries a macOS ARM64 wheel | **PASS** |
| …and a Windows x64 wheel, so the release still covers both | **PASS** |
| The packaged extension loads the exact solver | **PASS** |
| **`pygeodesic.__file__` is under the unpacked bundled wheel**, not site-packages | **PASS** |
| The backend reports itself available | **PASS** |

That is the bundled dependency loading on this machine's architecture, not a
developer's site-packages answering in its place.

### H.7 End-to-end smoke flow, run entirely from the package

| Step | Result |
|---|---|
| Measurement mesh created via the real operator | **PASS** |
| Canonical mesh built (2,208 triangles) | **PASS** |
| Analysis: single component, 0 non-manifold edges | **PASS** |
| Four landmarks placed | **PASS** |
| `Calculate All` succeeded | **PASS** |
| M01 VALID — straight **170.2480 mm**, surface **204.1411 mm** | **PASS** |
| M02 VALID — straight **175.4449 mm**, surface **214.5538 mm** | **PASS** |
| surface ≥ straight on both | **PASS** |

The goal here is package integrity, not another numerical campaign; the
numbers are recorded so the flow is reproducible.

### H.8 CSV schema v2, written by the packaged extension

| Check | Result |
|---|---|
| UTF-8 **BOM** present (Excel) | **PASS** |
| **CRLF** line terminators | **PASS** |
| Parses back to the expected row count | **PASS** |
| `schema_version` is the **first** column | **PASS** |
| Every row declares schema version **2** | **PASS** |
| v2 columns present: `measurement_stable_id`, `from_landmark_stable_id`, `to_landmark_stable_id`, `landmark_protocol`, `measurement_protocol` | **PASS** |
| Every stable id non-blank | **PASS** |
| Every distance column names millimetres | **PASS** |
| Session block round-trips (`SUBJ-01`, `SCAN-01`) | **PASS** |
| Row order is collection order, not sorted | **PASS** |
| Landmark file also schema v2, stable id on every row, explicit `coordinate_unit` | **PASS** |

**Stale state, exported from the package:** an invalidated measurement exports
**blank** — specifically **not** `0.000000` — with a blank straight distance
and no invented ratio, while keeping `measurement_id` and a readable `status`.
This closes the gap E.8 recorded: package-level CSV export is now validated at
schema v2.

### H.9 Protocol round-trip from the package

Landmark and measurement-pair definitions round-trip exactly through
`save_protocol` / `load_protocol`; **stable ids preserved including a
deliberate 1→6 gap** that a renumbering implementation would silently close;
pair endpoints referenced by stable id; non-ASCII names survive and are stored
unescaped. The written file never mentions `triangle_index`, `barycentric`,
`world_xyz`, `geometry_hash`, `surface_distance`, `subject_id` or `scan_id`.

### H.10 What remains outside this section

- **Windows.** The release ships a `win_amd64` wheel and the manifest declares
  the platform, and `tests/test_portability.py` audits the source statically
  (87/0). Neither is the same as running on Windows.
  `docs/windows_acceptance.md` remains the checklist for that, and its status
  is unchanged: **nothing in it has been run on Windows.**
- **Blender's own extension install path.** This suite imports the extracted
  package directly and unpacks the wheel itself. It does not exercise
  `Install from Disk…` in the Blender UI, nor Blender's own wheel installer.
- **The licence in the manifest is provisional** (`SPDX:GPL-3.0-or-later`).
  The build prints this on every run. It is a placeholder, not a decision —
  see `docs/LICENSING.md`.


## I. Researcher-run validation still required

> **Kind:** empirical
> **Status: REQUIRES HUMAN DATA — none of the following has been performed.**

Nothing in Sections A–H addresses whether a BSMT measurement corresponds to
the anatomical quantity a researcher intends to measure. That question is not
decidable by code, and this document makes **no claim** about it. The
following are **not validated**:

| # | Not yet validated | What it would require |
|---|---|---|
| 1 | **Real human-body scan repeatability** | Repeated scans of the same participants under controlled conditions; report as measurement error, not as a pass. |
| 2 | **Intra-rater landmark placement repeatability** | One operator placing the same landmarks on the same scans on separate occasions, blinded to their earlier placements. |
| 3 | **Inter-rater landmark placement reproducibility** | Multiple operators, same scans, independent placement; reported with an agreement statistic and its confidence interval. |
| 4 | **Comparison with external / reference software** | The same scans and landmarks measured in an established package, reported as agreement with limits, not as "matches". |
| 5 | **Scientific justification of the default decimation target** | Section C characterises sensitivity on an analytic torus only. A defensible default needs real scans and a stated accuracy requirement. See C.4. |
| 6 | **Real-scan decimation sensitivity** | Section C repeated on human scans with anatomically defined landmarks, where noise, holes and self-contact are present. |
| 7 | **Any criterion-validity study against manual or reference anthropometry** | Tape/caliper or another accepted reference on the same participants, with a pre-registered analysis. |

Until these exist, BSMT's verified claim is bounded to: *it computes an exact
geodesic on the mesh it is given, invariantly under rigid motion, and exports
that number without corrupting it.* Whether that number is the right one for a
given anatomical measurement is outside what has been validated.

---

## Development note — release and provenance

- **Release under test: 0.26.0**, built 2026-09-07 by
  `python3 tools/build_release.py` from `VERSION = (0, 26, 0)`, which is the
  single source of truth the build reads to stamp the extension manifest.
  SHA256 digests are recorded in §H.1.
- **Release metadata is reconciled.** `VERSION`, the generated
  `blender_manifest.toml`, `CHANGELOG.md` (0.26.0), `PROJECT_SPEC.md`
  (§11ac, Milestone 3.25), `INSTALL.md`, `docs/LICENSING.md` and
  `docs/windows_acceptance.md` all name 0.26.0. `export.SCHEMA_VERSION`
  remains **2**, as designed — it versions the CSV layout, not the release.
- **Sections A, B, D, E, F and G were executed from the working tree** at the
  commit this release was prepared from; **Section H was executed from the
  built ZIP**.
- **The decimation suite (§C) was not re-run for this release.** It takes
  ~698 s, and every module it exercises — `preprocess.py`, `geodesic/solve.py`,
  `geodesic/spaces.py`, `geodesic/meshcache.py`, `geodesic/extract.py`,
  `geodesic/topology.py`, `measurement.py` — is **unchanged** since its
  successful execution on 2026-09-07. The only production changes since were
  to `export.py` (row construction), `state.py` (export records) and
  `__init__.py` (version constant and description text), none of which can
  affect a decimation measurement. The previously executed evidence in §C
  therefore stands as recorded, and its provenance is this same working tree.
- **The manifest licence is provisional** (`SPDX:GPL-3.0-or-later`). The build
  prints a warning on every run. It is a placeholder pending the project
  owner's decision — see `docs/LICENSING.md`.

Anyone citing a number from this document should cite it together with the
release it was produced from: **BSMT 0.26.0**.
