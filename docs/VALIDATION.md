# BSMT — Validation Record

**Status of this document:** living record. It states what has actually been
executed, what exists but has not been run, what has not been written, and
what cannot be settled by code at all. Nothing here is a claim about human
body measurement accuracy.

- **Software version under test:** BSMT 0.29.0 (`body_surface_measurement.VERSION`)
- **Host:** macOS (darwin 25.5.0), Apple Silicon / ARM64
- **Blender:** 4.5.13 LTS (hash `daeeeca98fb0`, built 2026-08-25)
- **Geodesic backend:** pygeodesic (MMP), bundled
- **Runs recorded here:** 2026-09-07, 2026-09-10, 2026-09-11, 2026-09-14 and
  2026-09-15 (0.26.1, then 0.26.2, then 0.27.0, then 0.28.0 as an internal
  build, then 0.29.0), from the working tree (see
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
| C | Decimation sensitivity | Numerical characterisation | **EXECUTED** — 28/28 pass | 2026-09-11 (re-run) |
| D | Repair invariance / locality | Software verification | **EXECUTED** — 40/40 pass + 2 supporting suites; artifact deletion (§D.10) 149/149 + 88/88; local face repair (§D.12) 117/117 + 136/136 | 2026-09-07; §D.10 2026-09-11; §D.12 2026-09-14 |
| E | CSV export | Software verification | **EXECUTED** — 312/312 pass at source; package-level validated in §H | 2026-09-07 |
| F | Protocol round-trip | Software verification | **EXECUTED** — 53/53 pass | 2026-09-07 |
| G | Failure / stale-state | Software verification | **EXECUTED** — 68/68 pass (new suite) + 6 supporting suites | 2026-09-07 |
| H | Clean packaged install | Software verification | **EXECUTED** — 80/80 + 41/41 packaged workflow, from a clean 0.29.0 extraction | 2026-09-15 |
| J | Surface Region definition | Software verification | **EXECUTED** — 102/102 offline + 103/103 + 33/33 (draw purity) in Blender | 2026-09-15 |
| K | Surface Interior, Fill and Thickness Preview | Software verification | **EXECUTED** — 109/109 offline + 62/62 + 69/69 in Blender; performance §K.8, real-scan fix §K.9 | 2026-09-15 |
| L | Surface Area | Software verification | **EXECUTED** — 45/45 offline + 55/55 in Blender | 2026-09-15 |
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

# D.10 — artifact deletion           (~15 s)
python3 tests/test_artifact.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_artifact_deletion_blender.py

# D.12 — local face repair           (~20 s)
python3 tests/test_localrepair.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_local_face_repair_blender.py

# G — failure / stale-state          (~1 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_stale_state_blender.py

# H — clean packaged validation      (~2 s; needs dist/ built first)
python3 tools/build_release.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_packaged_extension.py

# E, F — CSV export and protocol     (~1 s, no Blender needed)
python3 tests/test_export.py

# J — Surface Region definition      (~15 s)
python3 tests/test_regions.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_surface_region_blender.py

# J.4b — Surface Region draw purity  (~3 s)
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_region_panel_draw_blender.py

# L — Surface Area                            (~20 s)
python3 tests/test_surfacearea.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_surface_area_blender.py

# K — Surface Interior and Thickness Preview   (~30 s)
python3 tests/test_interior.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_surface_interior_blender.py
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
  --python tests/test_thickness_preview_blender.py
```

Each Blender suite prints a machine-readable final line
(`BSMT_ANALYTIC_RESULT=`, `BSMT_INVARIANCE_RESULT=`,
`BSMT_DECIMATION_RESULT=`, `BSMT_STALE_RESULT=`,
`BSMT_REPAIR_LOCALITY_RESULT=`, `BSMT_ARTIFACT_DELETION_RESULT=`,
`BSMT_PACKAGED_RESULT=`, `BSMT_SURFACE_REGION_RESULT=`,
`BSMT_REGION_DRAW_RESULT=`, `BSMT_SURFACE_INTERIOR_RESULT=`,
`BSMT_THICKNESS_PREVIEW_RESULT=`, `BSMT_SURFACE_AREA_RESULT=`)
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
> Artifact deletion (§D.10): `tests/test_artifact_deletion_blender.py`
> **131/0** and `tests/test_artifact.py` **88/0**, executed 2026-09-11 from
> the working tree — see the version note in §D.10

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

### D.10 Component-scoped artifact deletion — researcher-supervised

> **Status: EXECUTED** — `tests/test_artifact_deletion_blender.py` **149
> checks, 0 failures**; `tests/test_artifact.py` **88 checks, 0 failures**;
> first executed 2026-09-11 at 0.27.0, re-executed unchanged 2026-09-14 at
> 0.28.0 (an internal build). The 0.27.0, 0.28.0 and 0.29.0 ZIPs all carry
> this code; Section H validates the current one.

A second, separate repair path: deleting the whole connected component that
holds a non-manifold defect the researcher has inspected. It is *not* mesh
healing and *not* scan cleaning, and the claims are correspondingly narrow.

**What is verified**

| Claim | Evidence |
|---|---|
| Only the focused defect's component is removed | body component's triangle count unchanged to the triangle, across every accepted deletion |
| The source scan is never modified | source object's vertex and triangle counts identical after deletion; separate datablock asserted |
| A defect on the **primary body component** is refused | blocked in the list, refused by the operator with the exact panel message, mesh unchanged — including a mesh where one defect is on the body and one on a fragment |
| A single-component mesh is refused | same message, plus *nothing to measure* |
| A tie for largest is refused, not broken | offline §C |
| Partial improvement is accepted | non-manifold 2 → 1 kept; 1 → 0 on the next press |
| An unsafe result is rolled back | injected over-reaching, mesh-emptying and degenerate-introducing edits each restored **vertex-for-vertex**, reason recorded in the log |
| Helper / highlight geometry never reaches diagnostics or the edit | counts identical with highlights present; helpers refused as repair targets |
| Geometry-dependent results go stale | every landmark leaves VALID, the stored measurement number is dropped, and **no landmark's triangle, barycentrics or local position moved** |
| A stored analysis is refused once geometry changed | external edit behind BSMT's back → refused with *press Analyze Mesh again*; re-analyzing recovers |

**What is NOT claimed**

- **BSMT does not determine anatomical relevance.** Nothing on this path
  infers that geometry is irrelevant to measurement; the operation exists to
  record a researcher's decision, not to make one. No test here, and none
  possible here, says a deleted fragment *should* have been deleted.
- **The fixtures are synthetic.** A body-scale sphere with planted
  three-triangle lumps, not a real scan with real scanner artefacts. What is
  established is the scoping, refusal and rollback behaviour of the operation,
  not its adequacy on any particular body scan.
- **Blender's undo step is not exercised.** `bpy.ops.ed.undo` has no valid
  context in background Blender. What is asserted instead is that the operator
  declares `UNDO`, that BSMT's own rollback needs no undo stack, and that
  restoring the pre-deletion mesh leaves BSMT refusing its now-stale analysis
  rather than acting on it.

### D.12 Local face repair inside a component — researcher-supervised

> **Status: EXECUTED** — `tests/test_local_face_repair_blender.py` **117
> checks, 0 failures**; `tests/test_localrepair.py` **136 checks, 0
> failures**; both 2026-09-14, at 0.28.0 - which was an internal build.
> **0.29.0 is the first RELEASED package to carry Milestone 3.30.**

The third repair path, and the one for a defect that sits **on the body
itself**, where §D.10 correctly refuses to delete the component. It removes a
small branch of faces at the focused defect, and only when the local topology
names exactly one.

**What is verified**

| Claim | Evidence |
|---|---|
| An ambiguous local topology is never offered a removal | two comparable branches, three branches, every branch unbounded, over-cap candidate and a no-improvement candidate each refused by name; the panel draws no destructive button; a scripted call is refused with the same text |
| The edit is geometrically local | every surviving triangle bit-identical by a position-keyed face signature, with the removed face derived from the two *surfaces* rather than from the operator's own account; the proof is separately shown to **fail** on a moved vertex, one extra deletion, and one approved face left behind |
| Exactly the approved faces go | triangle count falls by exactly the candidate's face count; vertex count falls by exactly the vertices it stranded |
| The focused defect is actually resolved | non-manifold 1 → 0 on the body-flap fixture, checked by **midpoint position** rather than index |
| No defect appears elsewhere | whole-mesh non-manifold signature diff, before vs after |
| The source scan is never modified | source object's vertex positions bit-identical and face count unchanged |
| An unsafe result is rolled back | injected over-reaching deletion, mesh emptying, moved unrelated geometry and a manufactured degenerate triangle each restored **vertex-for-vertex** |
| A stale candidate is refused | geometry changed behind BSMT's back → refused; stepping to another defect or re-analysing drops the inspection entirely |
| Preview shows only the candidate | the highlight holds exactly the candidate faces, edits nothing, moves no vertex, and does not reach the diagnostics |
| Geometry-dependent state goes stale | landmark leaves VALID through the same central `invalidate_for_geometry_change` path; nothing is re-projected |
| The existing repairs are unchanged | Delete Artifact still blocks the primary body with its exact message and still deletes a fragment; the local weld still judges its own result; Remove Duplicate Faces still refuses a mesh with none |
| Duplicates are delegated, not reimplemented | a reversed duplicate is classified `LOCAL_DUPLICATE_FACE`, no local removal is offered, the panel points at Remove Duplicate Faces, and that operator still fixes it |

**What is NOT claimed**

- **BSMT does not determine anatomical relevance.** As in §D.10, nothing here
  infers that geometry is unwanted. What is established is that a removal is
  offered *only* when the surrounding topology yields one unambiguous
  candidate, and refused otherwise.
- **The safety caps are not validated against a corpus.** 512-face inspection
  limit, 64-face candidate limit and an 8× dominance ratio are conservative
  **software** bounds, stated in the UI and easy to revise. They are not
  derived from a study of real scan defects, and no test here says they are
  the right numbers for any particular scan.
- **The fixtures are synthetic.** A body-scale triangulated sphere with a
  grafted flap, not a real scan. The scoping, refusal, locality and rollback
  behaviour is what is established.
- **Only one candidate branch is ever supported.** A defect separating two
  removable flaps is refused even when both are plainly artefacts.
- **Edge-type defects only.** A bow-tie vertex carrying no non-manifold edge
  does not appear in the defect list at all.

### D.11 Limitations

- **This is not general mesh healing.** The supported *repair* defect is *exactly
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
> `tests/test_packaged_extension.py` — **80 checks, 0 failures**, 2026-09-15
> Plus the packaged Surface Region workflow of §H.1a — **41 checks, 0
> failures**, including a real Compute Boundary solve from the extraction
> Supporting: `tests/test_portability.py` **87/0** (offline, static source audit)

Every other suite in this document imports BSMT from the working tree. That
proves the source is right and proves nothing about what a researcher
installs. This section extracts the released ZIP into a temporary directory
**outside the repository** and validates that.

### H.1 Artefacts under test

Built by `python3 tools/build_release.py` from `VERSION = (0, 29, 0)`:

| Package | Bytes | SHA256 |
|---|---|---|
| `dist/bsmt-0.29.0.zip` (Blender extension) | 2,026,408 | `bf491e610a044a9cded3d69907fefbed57da1a88f7665470e1083c2fac9457fe` |
| `dist/body_surface_measurement-0.29.0.zip` (legacy add-on) | 394,978 | `be94955d1742dbb181aee0dcb061be25545c12020b66376073004df264ddb0ba` |

Rebuilt 2026-09-15 after Milestone 3.36 (§K.10). It supersedes one earlier
0.29.0 build, which must not be distributed:

| Build | Why it is superseded |
|---|---|
| `0dc9256f…` (0.29.0) | carries Milestone 3.35 but not 3.36 — a boundary running **along** a mesh edge is refused, which a real scan does produce (§K.10) |

**0.29.0 is the first RELEASED package of this line.** 0.28.0 was an
internal test build: it was packaged four times while the work was in
progress, nothing was released from any of them, and all four are superseded
and must not be distributed:

| Build | Why it is superseded |
|---|---|
| `c814ab0d…` (0.28.0) | predates §J.4a (verdict scope) |
| `ea12465b…` (0.28.0) | **shipped the panel defect** — its `panels.py` still calls the storing `state.refresh_region_status` from `draw()`, so installing it reproduces `Writing to ID classes in this context is not allowed` in the real UI |
| `feeb0267…` (0.28.0) | carries the superseded measurement-path region model (§11ag), not §11ai |
| `ed1bb4f7…` (0.28.0) | predates Milestones 3.32 and 3.33 — no Surface Interior, Fill, Thickness Preview or Surface Area |
| `bce1ffaf…` (0.29.0) | predates Milestone 3.34 — carries the unoptimised Compute Interior, which is hours on a real scan |
| `cdf97857…` (0.29.0) | predates Milestone 3.35 — clipped pieces overlap where a chord end is stored off the triangle border |

**Rebuilt after Milestone 3.35**, so that a real-scan retest exercises the
triangle-tiling fix. Two earlier 0.29.0 packages are superseded and must not
be distributed - a smoke test against either would reproduce a defect that has
since been fixed and prove nothing about the current code:

| Build | Why it is superseded |
|---|---|
| `bce1ffaf…` | predates Milestone 3.34 - carries the O(points x edges) point location, which takes hours on a 350,000-triangle scan |
| `cdf97857…` | predates Milestone 3.35 - carries the raw-endpoint insertion that made clipped pieces overlap, and would refuse the scan again at triangle 14527 |

§H below was executed against the 0.29.0 artefacts in the table above and no
other.

"The ZIP holds the current tree" is verified rather than inferred from a
timestamp: every packaged `.py` was byte-compared to its working-tree
original — **38 modules, 38 identical**, none missing from the ZIP and none
left out of it — and the packaged `CHANGELOG.md` matches too. The packaged
`README.md` is one revision behind the source tree; see **H.1b**.
The model actually inside the archive was checked both ways: the landmark
model is present (`BSMT_RegionLandmark`, `segment_pairs`, `MIN_LANDMARKS = 3`,
`definition_key`, `bsmt.compute_region_boundary`, the region-owned
`BSMT_RegionPath_` cache name space, `invalidate_regions_for_landmark`,
`LEGACY_DEFINITION`) **and the old one is absent** (no `measurement_stable_id`
in the region rules, no `MIN_SEGMENTS`, no `add_region_segment`, no
`reverse_region_segment`, no `BSMT_UL_region_segments`). The 0.23.0-0.27.0
packages remain in `dist/` unmodified; 34 ZIPs are present.

### H.1a The Surface Region workflow, driven from the package

Byte-identity says the right source is in the archive; it does not say the
archive behaves. So the **whole researcher workflow** was executed against a
clean extraction of the artefacts above — repository removed from `sys.path`,
every `body_surface_measurement` module dropped from `sys.modules` first, the
same isolation §H.2 describes — including **a real Compute Boundary solve**
with the packaged solver instrumented and counted.

| Section | Checks |
|---|---|
| **A** import isolation: BSMT resolves to the extraction, not the tree; VERSION 0.29.0 | 5 |
| **B** define a region from four landmarks, with **no measurements in the file** — DRAFT, no solver | 5 |
| **C** Compute Boundary: really solves; four segments, the last closing P4→P1; VALID; region-owned polylines; **no measurement and no measurement path cache created** | 8 |
| **D** validate / show / four hide-show cycles / rename / reorder→STALE with cache kept / reorder back→VALID — **no solver** | 7 |
| **E** deleting a measurement leaves the region VALID with its cache intact — **the decoupling, from the package** | 3 |
| **F** the packaged panel draws, shows `P1 → P2 → P3 → P4 → P1`, and **writes nothing** (sentinel survives) | 6 |
| **G** deleting a region drops its own cache and keeps every landmark and the scan | 5 |
| **H** the solver was constructed **only** by Compute Boundary | 2 |

**41 checks, 0 failures.** Every non-compute section ran inside a `NoSolver`
block, so "only Compute Boundary solves" is measured in the packaged build and
not merely inherited from the source suites.

### H.1b README-only divergence between the ZIP and the source tree

Since commit `2138085` the packaged `README.md` is **one revision behind** the
committed source tree. The difference is **exactly two sentences of wording**,
both of them documentation corrections:

| Where | The packaged README says | The tree says |
|---|---|---|
| feature table, Surface Interior row | "It does **not** calculate an area — that is a later milestone" | "It does **not** itself report an area — *Compute Area* is a separate explicit press that measures this classification" |
| Limitations, "A Surface Region is a boundary, not an area" | "BSMT does not calculate surface area in this version" | "The area it encloses is not part of the definition: it comes from two separate explicit presses, *Compute Interior* then *Compute Area*" |

Both statements denied Surface Area, which **§L** records as EXECUTED at this
same version. They were wrong in the package and they are corrected in the
tree; the correction removed an inaccuracy rather than adding a claim.

**What is unaffected**, and how that was established rather than assumed:

* **All 42 packaged Python modules are byte-identical** to the committed source
  tree — re-compared after the correction, 42 identical, none missing in either
  direction.
* **The packaged `CHANGELOG.md` is byte-identical** to the committed tree.
* **No executable code differs.** `README.md` is not imported, parsed or
  executed by anything in the package.
* **No numerical result, tolerance, fixture or measured value differs**, and no
  claim in this document rests on README text.
* **§H's 80/80 and the §K.9 packaged tiling checks remain valid**: both were
  executed against `bf491e61…`, whose code is the code in the tree.

**The recorded artefact provenance is therefore unchanged and still valid.**
`dist/bsmt-0.29.0.zip` = `bf491e610a044a9cded3d69907fefbed57da1a88f7665470e1083c2fac9457fe`
is the artefact every §H and §K.9 result was produced from, and it still is.

**A rebuild is not required for code correctness.** It is required only if
exact documentation byte-equality between the ZIP and the tree is wanted. A
rebuild would change the artefact hash, which is cited in §H.1, §K.9 and the
Development note, so it is a release-record decision and not a free one — and
it is deliberately not taken here.

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
| **No packaged submodule resolved to the working tree** | **PASS** — all **38** loaded BSMT modules came from the extraction directory |

### H.3 Package identity

| Check | Result |
|---|---|
| `VERSION == (0, 29, 0)` | **PASS** |
| `export.SCHEMA_VERSION == 2` | **PASS** |
| Extension manifest declares `version = "0.29.0"` | **PASS** |
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


## J. Surface Region definition and closed-boundary validation

> **Kind:** software verification
> **Status: EXECUTED** — `tests/test_regions.py` **102 checks, 0 failures**
> (offline); `tests/test_surface_region_blender.py` **103 checks, 0 failures**;
> `tests/test_region_panel_draw_blender.py` **33 checks, 0 failures**;
> all three re-executed 2026-09-15 against the Milestone 3.31 model.
>
> **Model note.** Milestone 3.31 replaced the measurement-path model these
> suites originally tested. A Surface Region is now defined by ORDERED
> LANDMARKS; the counts above are for the rewritten suites, and the claims
> below are about the current model. See `PROJECT_SPEC.md` §11ai.
>
> **Version note.** 0.28.0 packaged this work as an internal build and was
> never released; **0.29.0 is the first released package to carry it**, and
> Section H validates that ZIP: "Surface Regions" is asserted as the eighth
> workflow stage from the packaged extension.

A **Surface Region** is a researcher-defined **closed boundary** on the
measurement mesh, specified by an **ordered set of anatomical landmarks**.
Consecutive landmarks — including the final-to-first pair — are joined by
cached surface geodesic paths on the triangular mesh. This section is about
the boundary and nothing else.

### J.1 What is explicitly NOT claimed

- **No area is computed.** This milestone establishes the boundary model and
  stops there. There is no face-area summation, no interior flood fill, no
  triangle clipping, no projected area, no coverage percentage and no contact
  area anywhere in the code. An area computed from a boundary nobody proved
  closed is a plausible wrong number, so the boundary comes first.
- **No geometry is modified.** Not the source scan, and not the measurement
  mesh. A region is a definition, a verdict and a drawn curve.
- **Self-intersection checking is SOUND but INCOMPLETE** — see §J.4. This is
  the main limitation of the milestone and is stated in the code, in the
  operator report and here.

### J.2 What is verified

**The definition.**

| Claim | Evidence |
|---|---|
| n ordered landmarks imply exactly n segments, the last closing Ln→L1 | offline §A; Blender §B, §C |
| The closing segment is implicit and mandatory — an open boundary cannot be expressed | offline §A |
| A reordered or rotated definition is a *different* definition | offline §A |
| Fewer than three landmarks cannot bound anything | offline §B |
| The same landmark twice in a row is refused as `DEGENERATE_SEQUENCE`, including the implicit closing pair (A-B-A) | offline §B |
| A landmark the loop returns to is refused as `DUPLICATE_LANDMARK` | offline §B |
| A deleted landmark leaves a **named** reference, not a dropped entry | offline §B; Blender §H |
| An unpicked landmark is refused as `LANDMARK_NOT_PICKED` | offline §B |
| Landmarks on different components are refused as `CROSS_COMPONENT` | offline §B |
| **The definition is judged before the cache** | offline §B |

**The cached boundary.**

| Claim | Evidence |
|---|---|
| A complete definition with no boundary reads `DRAFT / BOUNDARY_NOT_COMPUTED` | offline §C; Blender §B |
| A boundary computed for this exact definition is `VALID` | offline §C; Blender §C |
| Adding, removing or reordering a landmark makes it `STALE`, cache **kept** | offline §C; Blender §F |
| Re-picking a boundary landmark makes it `STALE` | offline §C; Blender §H |
| A geometry change makes it `STALE`, through `invalidate_for_geometry_change` | offline §C; Blender §I |
| A rigid transform does **not** disturb it, matching the geometry-hash policy | Blender §E |
| A partly-cached boundary is never reported `VALID` | offline §C |
| A segment cached for a different landmark pair is `STALE` | offline §C |
| Definition, cache key and polylines survive a save/reload | Blender §K |
| A file with no region data loads safely, with safe defaults | Blender §K |
| An old measurement-path region is refused as `LEGACY_DEFINITION`, never reinterpreted | offline §D; Blender §L |

**The decoupling from measurements.** This is the milestone's reason for
existing, so it is tested bluntly: a computed `VALID` region, then a
measurement edited, deleted, and finally every measurement cleared.

| Claim | Evidence |
|---|---|
| A region is defined and computed in a file with **no measurements at all** | Blender §A, §C |
| Computing a boundary creates no measurement and no measurement path cache | Blender §C |
| Editing a measurement leaves the region byte-identical | Blender §G |
| **Deleting** a measurement leaves the region `VALID`, cache intact | Blender §G |
| Clearing **every** measurement leaves the region `VALID` | Blender §G |
| No region field references a measurement id | Blender §G; offline §F |

**Everything else.**

| Claim | Evidence |
|---|---|
| Deleting a region keeps every landmark and the scan; its own cache goes with it | Blender §J |
| A verdict reports **what it did not check** — see §J.4a | offline §E; Blender (draw suite) |
| A drawn boundary is a curve helper, excluded from mesh diagnostics | draw suite §C |
| The panel draw writes nothing — see §J.4b | draw suite §B, §C |

### J.3 No solver call, asserted by counting

The constraint that makes regions usable on a real scan is that **no region
operation runs the geodesic solver**. A path costs tens of seconds to minutes;
renaming a boundary must cost none of it.

This is measured, not reasoned about. `PyGeodesicAlgorithmExact` — the only
door to the native solver — is wrapped and its constructions counted, the same
technique §G uses. Every one of these blocks completed with the counter
**unchanged**: creating a region, adding, removing and reordering boundary
landmarks, renaming, validating, showing, hiding, refreshing, clearing,
deleting a region, restating after a landmark change, after a measurement
deletion and after a geometry change, saving, reloading, and drawing the
panel.

**Exactly one operator solves**: `bsmt.compute_region_boundary`, which the
researcher presses and which says how many segments it will solve and that
Blender will not redraw. It is the **fourth** entry point past the preflight
gate, and `tests/test_import.py` asserts that count rather than mere
membership, so a fifth route cannot appear without that test being updated
deliberately.

The counter is also asserted to be **non-zero** after a boundary is computed,
so a suite that silently stopped counting would fail rather than pass.

### J.3a Boundary computation is transactional

Every segment is solved into memory first and **nothing is written until all
of them have succeeded**. A region can therefore never hold a boundary that is
part fresh and part stale — a drawing that would look authoritative while
describing two different definitions.

On failure nothing is written, the previous cache is left exactly as it was,
and the failing segment is named. Because a cache carries the definition key
it was computed for, a surviving previous cache reads `STALE` the moment the
definition has moved on; it cannot be mistaken for a result of the compute
that failed. Refused **before** the solver is constructed: fewer than three
landmarks, a repeat, a missing or unpicked landmark, a cross-component pair, a
legacy definition, a blocked preflight, or landmarks spread across two meshes.

### J.4 Self-intersection — the limitation, stated plainly

The boundary lies on a triangulated surface, not in a plane. The obvious
test — project the loop to XY and run a polygon self-intersection check — is
**wrong on a body**: a boundary that wraps a limb self-intersects in every
axis-aligned projection while being perfectly simple on the surface. BSMT does
not do that.

**What is detected (sound — every report is a real self-touch):**

1. the same landmark twice in a row, including the implicit closing pair;
2. a landmark the loop arrives at more than once;
3. two non-adjacent boundary segments that **share a point** in space, within
   a tolerance of 1e-6 of the mesh's bounding-box diagonal.

(1) and (2) are now **definition-level** checks rather than cache-level ones,
which is strictly better: they are caught before anything is solved, so a
pinched loop costs no solver time to discover.

(3) is sound because two polylines that coincide at a point genuinely touch
there — no projection and no inference is involved, only a distance between
two cached points. It is confirmed by an actual distance after a spatial-grid
shortlist, so the grid can only make the test **miss**, never invent.

**What is NOT detected:** a transversal crossing that happens strictly
*between* two sampled points of a path, leaving no shared point behind.
Catching that needs each polyline point's triangle index; the path cache
stores points and normals only, and resolving triangles would cost one BVH
query per point. This is recorded as deferred work, not as solved.

The suite asserts both halves: a shared point **is** found, and four rings
1 mm apart are **not** reported as touching.

### J.4a A verdict states what it did not check

A `VALID` produced by a **panel redraw** and a `VALID` produced by **Validate
Region** are not the same claim. A redraw restates a region from its stored
properties and its paths' cache state; only the explicit press loads every
cached polyline and runs check (3) above. If both printed the same word and
nothing else, the cheaper check would be read as the stronger one.

So every validation result carries `touch_checked`, and:

- `regions.summary_lines` appends a **scope** line to every report — the
  limitation text above when the shared-point test ran, and
  `regions.TOUCH_NOT_RUN` plus the limitation text when it did not;
- the panel shows *"Not checked for self-intersection — this verdict comes
  from a redraw"* until an explicit Validate has been run against **this**
  region as it now stands, and the limitation itself afterwards;
- neither report nor panel ever states that a boundary is *simple* or
  *non-self-intersecting*. A closed boundary is reported as **closed**.

The "has been validated" flag is **derived, not remembered**. Every refresh
recomputes `state.region_fingerprint` — the ordered path references with
their orientation, plus the verdict and geometry hash of the same pass — and
any difference clears both the flag and the stored report. Reordering a
boundary therefore withdraws the claim immediately (Blender §C30) rather than
leaving a validated-looking region that no longer matches what was validated.

### J.4b Panel drawing is read-only — a real-UI defect, and the guard

**The defect.** The Surface Regions panel called the *storing*
`state.refresh_region_status` from `Panel.draw()`. Blender forbids writing to
ID-backed data while the interface is drawing, so opening the sidebar in the
real UI produced

    AttributeError: Writing to ID classes in this context is not allowed:
    Scene, Scene datablock, error setting BSMT_SurfaceRegion.status

and the panel was replaced by that message. A single draw persisted three
fields (`status`, `status_code`, `status_detail`), and would have persisted
the six `boundary_*` fields and `validated`/`report` whenever they differed.

**Why every suite passed anyway.** `blender -b --factory-startup` never enters
a genuine draw callback, so the restriction is not enforced there. A suite
that calls `Panel.draw()` directly — which is the only way to exercise a panel
headlessly — passes over this class of bug indefinitely. The in-Blender
Surface Region suite had been drawing the panel since the milestone landed and
never saw it.

**The fix.** Deriving and recording are now two functions.
`state.validate_region` derives and is pure; `state.store_region_status`
writes; `state.refresh_region_status` is the two together and is reachable
only from operators and invalidation paths. The panel calls the pure half and
displays the verdict without keeping it — the same idiom
`state.readiness_snapshot` and the alignment panel already use. Validation
semantics are unchanged: the same rules, in the same order, producing the same
statuses and reason codes.

Because the panel no longer writes, the displayed verdict is the live one
while `item.status` is the last verdict an operator stored. They agree in
normal use; when they do not, the panel shows the live verdict **and says the
stored one disagrees**, rather than resolving the difference by writing.

**The guard**, `tests/test_region_panel_draw_blender.py` — **33 checks, 0
failures**. It does not rely on Blender raising, since headless Blender will
not. It catches the cause three independent ways: a **snapshot** of every
persisted region field before and after a draw; a deliberately wrong-but-legal
**sentinel** verdict that a re-deriving draw would silently correct; and a
**tripwire** that replaces all three status writers with functions that raise,
plus a check that the tripwire is itself armed. Reintroducing the original
one-line call makes five of its checks fail, which is how the suite was
verified. It also draws **every** BSMT panel and asserts none of them mutates
region state or reaches the solver.

### J.5 Deferred: protocol / export integration

Region definitions are **not** written to protocol files or to CSV, and CSV
**schema v2 is unchanged**. The assessment and the reason are in
`PROJECT_SPEC.md` §11ag.7; Milestone 3.31 does not change the decision, but it
does simplify what such an export would carry — **ordered landmark
identities**, which a protocol already restores by stable id, rather than
measurement path references.

### J.6 Known limitation: a two-landmark lune cannot be expressed

The minimum is three landmarks, because two give A→B and B→A — the same
geodesic walked both ways, enclosing nothing.

The superseded measurement-path model allowed two, because two *different*
paths between one pair (round the front of an arm and round the back) do
bound a lune. A landmark pair cannot express which way round to go. This is
the one respect in which the current model is **less** expressive than the one
it replaced, and it is recorded rather than glossed over. Expressing a lune
again would need a per-segment route hint; that is a real design question and
this milestone does not answer it.

---

## K. Surface Interior, Region Fill and Thickness Preview

> **Kind:** software verification
> **Status: EXECUTED** — `tests/test_interior.py` **109 checks, 0 failures**
> (offline); `tests/test_surface_interior_blender.py` **62 checks, 0
> failures**; `tests/test_thickness_preview_blender.py` **69 checks, 0
> failures**; all three 2026-09-15.
>
> **Every fixture in this section is SYNTHETIC.** See §K.7.

A **Surface Interior** is *the selected mesh-surface side bounded by a
computed Surface Region boundary*. A **Thickness Preview** is *a
visualization generated by offsetting the selected surface-region
representation along mesh-surface normals* — intended for geometric
visualization, and **not a physical simulation or a manufacturing model**.

### K.1 What is explicitly NOT claimed

- **No area is computed.** No face-area summation is presented as a result,
  no coverage percentage, no centroid, no mesh cutting, no remeshing. The
  internal `area_total` is named a TRIANGLE-AREA TOTAL in the source and
  documented there as not being the region's surface area; no panel shows it.
- **No geometry is modified.** Not the source scan, not the measurement mesh,
  not the landmarks, not the boundary definition. The fill and the preview are
  tagged helper objects.
- **The thickness preview is not a panel.** It is a visualization. §K.6.
- **Nothing here is validated on a human scan.** §K.7.

### K.2 The measured property the method rests on

The approach is only defensible because an exact geodesic's breakpoints lie
**on triangle edges**. Measured on a real BSMT-computed boundary *before* the
algorithm was written:

| | |
|---|---|
| interior boundary points tested | 20 |
| found on a mesh edge | 20 |
| worst distance to nearest edge | 3.9e-06 (bbox diagonal 346) |
| relative | ~1e-8 |

So the boundary is an exact sequence of edge crossings, not samples near the
surface. **No per-point triangle identity had to be added** to the boundary
cache; locating points at Compute Interior time suffices, and a point that
cannot be placed on the mesh is refused rather than approximated.

### K.3 Exact clipping — the property everything downstream inherits

| Claim | Evidence |
|---|---|
| Every cut triangle's pieces tile it exactly; worst error **3.6e-07** of its own area, which is the float32 precision the cached boundary is stored at | offline §A |
| Asserted **per triangle inside `compute`**, not only in a test | `interior.py`, `TILING_TOLERANCE` |
| A triangle crossed several times is split by each cut in turn | offline §A; 128 cut triangles on the Blender fixture, several more than once |
| Each piece is carried in mesh coordinates **and** barycentric, and they agree | offline §A |
| A planar square loop encloses exactly **9.000000** of a **36.000000** patch | offline §B |
| The two sides sum to the whole mesh: **4.3e-11** relative on the sphere | offline §C; Blender §D |
| Nothing is left unreached | offline §B, §C |

### K.4 Two sides, and neither called "inside"

A closed loop on a closed surface bounds two regions. Both are computed from
one analysis; the choice is `SMALLER` (default) or `COMPLEMENT`, by
triangle-area total. The default is a **default**, and the enum description
states in so many words that it is not a claim of anatomical inside. Switching
sides runs no solver.

### K.5 What is refused rather than guessed

| Case | Code |
|---|---|
| loop does not cut an **open** component in two | `INTERIOR_UNDETERMINED_OPEN_SURFACE` |
| loop non-separating on a closed surface (torus handle) | `INTERIOR_NOT_SEPARATED` |
| boundary spans components | `CROSS_COMPONENT` |
| boundary not on this mesh | `BOUNDARY_OFF_SURFACE` |
| boundary crosses itself | `SELF_INTERSECTION_DETECTED` |
| a cut that cannot be split exactly | `UNSUPPORTED_TRIANGLE_CROSSING` |

**An additional triangle-local self-intersection check.** §J.4 records that
the boundary's shared-point test cannot see a crossing that happens strictly
*between* two sampled points. Inside one triangle the boundary is straight
between samples, so there it is an ordinary segment intersection and **is**
detected when the interior is computed.

This is an **additional, triangle-local** capability and nothing more. **BSMT
does not claim complete or general on-surface self-intersection detection**,
and §J.4's limitation stands unchanged. The narrower true statement is: a
self-crossing *within a single triangle* is caught at Compute Interior.

### K.6 Thickness Preview — what it is, and what it is not

Built from the fill, which **is** the classified interior — not from a fresh
analysis, which is what makes "changing the thickness recomputes nothing" true
by construction.

| Claim | Evidence |
|---|---|
| 10 mm and 30 mm offsets, to the float32 the helper mesh stores | Blender §B |
| **Outward**, confirmed radially on a sphere — never inward | Blender §C |
| Outward taken from the component's **signed volume**, not assumed from winding | Blender §A, §J |
| On an **open** component the preview is **refused**, not extruded inward | Blender §J |
| The side wall follows the **clipped** edges: the shell is **closed**, every edge used exactly twice, with **128 clipped triangles** on its border | Blender §D |
| Thickness, colour and opacity leave interior and boundary field-for-field unchanged | Blender §E |
| Zero, negative, non-finite or out-of-range thickness is **refused**, never clamped | Blender §F |
| Stale interior invalidates the preview and removes it | Blender §H |

**The limitation, stated in `panelpreview.LIMITS` and printed on every run.**
A normal offset gives a perpendicular gap that is exactly the thickness
requested. The **shape** is not exact: on a curved body the outer surface
stretches over convex areas, compresses over concave ones, and where the
thickness exceeds the local radius of curvature it **folds through itself**.
The material a real panel would need is **not** uniform. Fold-through is
reported where cheap to detect — a **warning, not a guarantee** that an
unreported preview is locally right.

### K.8 Performance on a real-scale mesh

Compute Interior was **effectively hung** on a ~350,000-triangle scan. It was
profiled before anything was changed, and the fix did not change the result: a
fingerprint of the complete output — every full-triangle index, every clipped
polygon and barycentric corner to 12 decimals, every area, across five
fixtures — is byte-identical before and after (`ed3e4185…`).

**The bottleneck:** point location compared each boundary point against every
edge in the mesh, O(points × edges) — 30.6 s at 28,800 triangles, which
extrapolates to **1.1–5.4 hours** at 350,000.

**After** (uniform edge grid, vectorised search, CSR adjacency, one flood):

| triangles | boundary points | seconds |
|---|---|---|
| 14,160 | 360 | 0.21 |
| 192,720 | 1,320 | 2.14 |
| **358,800** | **1,800** | **4.17** |

Linear, ~86,000 triangles/second, with the two sides summing to the mesh's own
area to **2.8e-13** relative at full scale.

The benchmark boundary is built from **true edge crossings** — every point on
a mesh edge, consecutive points sharing a triangle — because that is what a
computed geodesic is; a polyline sampled at arbitrary spacing exercises a path
real data never takes.

Wall-clock is reported here and deliberately **not** asserted in the suite.
`tests/test_interior.py` §F asserts the structural properties instead:
clipping runs once per cut triangle and not once per triangle, the edge map is
built once, edge-interval work scales with the cut rather than the mesh, there
is one flood and not one per side, and the grid shortlist gives the same
answer as scanning every edge.

**Verified in the PACKAGED build**, not only in the working tree. Imported
from a clean extraction of `cdf97857…` outside the repository:

| triangles | boundary points | seconds |
|---|---|---|
| 39,600 | 600 | 0.49 |
| 192,720 | 1,320 | 2.19 |
| **358,800** | **1,800** | **4.15** |

Stage breakdown at 358,800 triangles: edge map 0.89 s, index build 1.35 s,
boundary-to-triangles 0.09 s, clipping 0.25 s, adjacency 0.72 s, side
traversal 0.24 s, side description 0.50 s. No stage dominates, and the stage
that used to — boundary-to-triangles — is now the cheapest of them.

**Still unmeasured on real data.** These are synthetic spheres. A real scan's
triangle-size distribution, boundary length and cut-triangle count may all
differ, so 4.2 s is an indication and not a promise. One hypothesis was tested
and NOT confirmed: a 324x spread in edge length — far wider than a uniform
sphere — moved `EdgeGrid` construction only from 0.09 s to 0.23 s, so
non-uniform triangle sizes are not by themselves a pathological case.

### K.9 A real-scan tiling failure, found and fixed

On a real ~350,000-triangle scan Compute Interior refused with

    the pieces of triangle 14527 do not tile it (3.82042 vs 3.81668)

**+9.8e-04 relative, an EXCESS** — the clipped pieces overlapped. The refusal
was correct and the invariant was kept: `TILING_TOLERANCE` was not raised, the
triangle was not skipped, and no area was renormalised.

**Cause.** A chord end stored a few microns off the triangle border. The
region boundary cache is float32 display geometry, so a point that
mathematically lies exactly where the geodesic crosses a shared mesh edge
lands slightly to one side once stored. The splitter located that end by
projecting it onto the border but inserted the **raw** point into both pieces,
so the pieces shared a corner off the border and their union bulged past the
triangle.

Thirteen triangle-local configurations were tested before the cause was
accepted — near-vertex, on-vertex, edge-coincident, same-edge entry and exit,
two and three chords, nested and reversed order, shared endpoints, interior
bends, duplicate points. **Only the drifted end produced an excess.**

**Fix.** The projected point is inserted into both pieces, so their union is
exactly the polygon. The correction is the drift itself — microns — applied
identically by both triangles sharing the edge. This is numerical handling of
storage precision, **not** a simplification of the boundary. A drift too large
to be float32 is still refused.

**Equivalence.** Every existing fixture is numerically identical: **0 numeric
differences, worst absolute 0.000e+00** across five fixtures, covering
full-triangle sets, partial parent ids, clipped polygon coordinates, piece
areas, side selection and Surface Area.

**Reporting.** A failure now prints an inspectable per-triangle record
(corners, area, visit count, each chord end in mm and barycentric, vertex/edge
coincidence, off-border distance, piece areas, the sum, and whether the
discrepancy is an overlap or a gap). A success reports the invariant:

    tiling: 140 boundary triangles | max abs residual 3.34e-13 mm^2
            | max relative 7.82e-15 | worst triangle 550

**Verified in the PACKAGED build.** Imported from a clean extraction of
`bf491e61…` outside the repository, on a 3.9 mm² triangle at scan tolerance:
drifts of 0, 1e-5, 1e-4, 1e-3, **3e-3** and 1e-2 mm all tile with a residual
of **0.000e+00**, and a 0.5 mm drift — too large to be float32 storage — is
still refused. The 3e-3 mm case is the one that reproduces the +9.8e-04 the
scan reported.

**Known remaining refusal — resolved in K.10.** A chord lying *along* a
triangle edge yields no split and is refused. Such a triangle is geometrically
uncut and belongs wholly to one side; handling it needs the partition logic to
accept a visited but uncut triangle. Recorded rather than special-cased — and
observed on a real scan on the very next re-test.

### K.10 A boundary that runs along a mesh edge

Re-testing the fix above on the same ~350,000-triangle human scan got past
triangle 14527 and refused a different one:

    the boundary's crossing of triangle 6825 could not be split exactly

**How it was classified, rather than guessed.** The split-failure path carried
no diagnostic record — only the tiling path did — so the triangle could not be
identified from what the operator printed. A record was attached to that path
first, and it is that record which named the case:

    visit 1 lies ALONG mesh edge (16, 17) - such a traversal does not cut
    the triangle, and is handled as an edge-aligned boundary event

**The geometry.** An exact geodesic's breakpoints lie on mesh edges. When the
shortest path between two landmarks *is* a chain of mesh edges — common at a
crease, a seam, or a run of near-coplanar strips — the run lies flat along one
edge. Such a triangle is **not cut**; asking for a split asks for a piece with
no interior. The clipper was right to produce nothing and the computation was
right to refuse. Neither the boundary nor the mesh was faulty.

**What was changed.** A boundary can now partition the surface in two disjoint
ways: by clipping a triangle it crosses (as before), or, when it runs along a
mesh edge, by **severing the adjacency** between the two triangles sharing it.
The uncut triangle keeps its whole area and belongs entirely to one side.

**Detection is topological.** Every boundary point is already classified as on
a vertex, on an edge, or inside a face. A run is edge-aligned when every one
of its points lies on one common mesh edge or at one of that edge's ends. A
run with any face-interior point is an ordinary chord and is still clipped.
**No tolerance was introduced or changed**; a boundary merely passing close to
an edge is not edge-aligned, and drift beyond tolerance is still refused.

**Equivalence.** Against the pre-change build: 0 numeric differences, worst
absolute 0.000e+00, across full-triangle sets, partial parent ids, clipped
polygon coordinates, barycentric coordinates, areas, side selection and
Surface Area. None of the existing fixtures runs along an edge, so none of
them reaches the new path.

**What is asserted** (`tests/test_interior.py` §H, 34 checks):

| # | configuration | assertion |
| --- | --- | --- |
| 1 | loop entirely along mesh edges | computes; 0 triangles cut; 8 aligned edges |
| 2 | shared edge used as boundary | adjacency across it is severed |
| 3 | aligned run ending at a vertex | resolves to the one edge it ran along |
| 4 | interior chord, both ends on one edge | **not** edge-aligned; still clipped |
| 5 | boundary near but not on an edge | still clipped; 0 aligned edges |
| 6 | 5% and 20% drift off the border | still **refused**, not absorbed |
| 7 | both sides together | tile every triangle; worst relative 0.0 |

and the decisive one: with alignment detection disabled — which *is* the
previous implementation — the along-edge loop is refused with the scan's own
message; with it enabled the same loop resolves to exactly 4.000000 mm².

**Performance.** Unchanged: 358,800 triangles in 4.17 s.

**What is still not established.** That this is the *last* real-scan refusal.
Two consecutive re-tests have each found one new configuration, and both were
genuine gaps rather than bad data. The honest statement is that the algorithm
now handles both kinds of boundary-mesh contact it is known to encounter, not
that the scan will now complete.

### K.7 What remains unvalidated on real human scans

Every fixture in this section is synthetic: planar grids, a closed UV sphere,
a torus, and a two-component patch. Nothing here has been run on a human body
scan. Specifically **not** established:

- that the smaller side is the side a researcher wants on real anatomy — it is
  a size comparison, and on a torso a "small" patch and its complement can be
  much closer in area than on a sphere;
- how often a real landmark set produces a self-crossing boundary, and
  therefore how often Compute Interior will refuse in practice;
- whether real scan topology — boundary edges at the crop, near-coincident
  vertices, multiple components — leaves the two sides separable often enough
  to be useful;
- the thickness preview's behaviour where a body is most curved, which is
  precisely where fold-through is expected and where a researcher is most
  likely to want a panel;
- any correspondence between a preview and a physical panel. None is claimed;
- that Compute Interior now completes on the reference scan. Two successive
  re-tests each exposed one new real configuration (K.9, K.10); each was
  fixed exactly rather than tolerated, and the next re-test is what would
  establish it.

---

## L. Surface Area

> **Kind:** software verification
> **Status: EXECUTED** — `tests/test_surfacearea.py` **45 checks, 0
> failures** (offline); `tests/test_surface_area_blender.py` **55 checks, 0
> failures**; both 2026-09-15.
>
> **Every fixture is SYNTHETIC. No human scan has been measured.** §L.7.

### L.1 What the number is, precisely

The **mesh surface area of the selected region on the triangular body mesh**:

    A_region = Σ area(whole interior triangles)
             + Σ area(exactly-clipped boundary polygons)

It is **not** the true anatomical surface area, **not** the actual human
surface area and **not** an exact smooth-body area. A triangulation is a
chord approximation of a curved surface, so its area reads systematically a
little under the smooth surface it was sampled from — the same property §0.3
records for distances. On the sphere fixture the mesh measures 125,215.7 mm²
against a smooth 4πr² of 125,663.7 mm², about 0.36% under, which is a
sampling difference and not an error in the sum.

### L.2 It measures what is already drawn

The area is summed from the interior classification — the same representation
the region fill draws and the thickness preview's base is built from. Surface
Area does not decide the side, flood fill, clip, or re-run any interior
analysis; a missing, stale or invalid interior is a refusal.

### L.3 Two-side consistency — the strongest available check

The selected side plus its complement must equal the surface component's own
area. Nothing wrong near the boundary can satisfy it: whatever one side
gains, the other loses.

| Fixture | Agreement |
|---|---|
| planar patch, offline | exact — 9.000000 + 27.000000 = 36.000000 |
| closed sphere, offline float64 | < 1e-09 relative |
| sphere through the real operators | **3.3e-09** relative |
| read back from stored properties | ~1e-07 — float32 property storage |

Per cut triangle, the two sides' pieces tile it to **1e-12** relative
offline. Every triangle is on exactly one side or is cut; the three sets are
the whole mesh.

### L.4 Precision, by source

| Stage | Precision |
|---|---|
| clipped geometry, stored | **float64**, barycentric, in `interiorcache` |
| area accumulation | **float64** |
| area as stored on the region | **float32** — a Blender `FloatProperty` |
| piece-bounds tolerance | `1e-5` relative, matched to `interior.TILING_TOLERANCE` |

float32 storage is ~7 significant digits, roughly 0.004 mm² on a 400 cm²
region — far finer than the mesh's own fidelity to a body, and the reason the
two-side sum reads ~1e-07 from properties and 3.3e-09 in float64.

### L.5 Refusals, and what is never shown

A clipped piece that is negative, or larger than its parent triangle beyond
tolerance, is `AREA_PIECE_OUT_OF_BOUNDS` with the triangle named — that is a
clipping fault which has produced a plausible number. An empty selection is
`AREA_DEGENERATE`, refused rather than reported as 0 mm².

**A stale area is never displayed as a number**, only as a status. This was a
real defect found by the regression: the panel had been echoing the stored
result sentence, which contains the figure, under a "stale" heading.

### L.6 Explicit, and what must not move it

*Compute Area* is its own press; nothing computes an area during Compute
Boundary, Compute Interior, a panel draw, Show Fill, Show Preview, a
thickness change, a side switch, or a save. **No solver is constructed
anywhere in it.**

Verified not to move the area: thickness, fill colour, fill opacity, preview
colour, preview rebuilds, and adding, editing, deleting or clearing
measurements. Verified to make it stale: landmark reorder, landmark re-pick,
and a geometry change through the centralized policy.

**Export:** CSV integration is **deferred** — schema v2 is per-measurement
rows with a fixed column tuple, and region-area rows would mutate those
semantics or need a new export surface. **Schema v2 is untouched**, asserted
by test. **Protocol files contain no area** — a reusable definition is
ordered landmarks; an area belongs to one scan.

### L.7 What real-scan validation would have to establish

Software verification is not human validation. None of the following has been
performed, and none is implied by any number this milestone produces:

- **repeatability** — the same researcher re-picking the same landmarks on
  the same scan, and the spread of the resulting areas;
- **sensitivity to landmark placement** — how much area moves per millimetre
  of landmark displacement, which on a curved region may be strongly
  non-uniform;
- **sensitivity to scan density / decimation** — §C characterises this for
  distances; area is a quadratic quantity and may behave differently;
- **agreement with independent mesh software** on the same region;
- **posture sensitivity**, if regions are compared across scans of a subject;
- **protocol reproducibility** — whether a landmark-defined region transfers
  between subjects such that areas are comparable at all.

---

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

- **Release under test: 0.29.0**, built 2026-09-15 by
  `python3 tools/build_release.py` from `VERSION = (0, 29, 0)`, which is the
  single source of truth the build reads to stamp the extension manifest.
  SHA256 digests are recorded in §H.1. Previous release ZIPs in `dist/` are
  left in place; the build writes only the two ZIPs of the version it is
  building, so this build ADDED the 0.29.0 pair and touched none of the 34
  older ones - including the superseded 0.28.0 pair, which is kept for the
  record and must not be distributed. **36 ZIPs** are now in `dist/`.
- **Release metadata is reconciled.** `VERSION`, the generated
  `blender_manifest.toml`, `CHANGELOG.md` (0.29.0), `PROJECT_SPEC.md`
  (§11ag Milestone 3.29 and §11ah Milestone 3.30), `INSTALL.md`,
  `docs/LICENSING.md` and `docs/windows_acceptance.md` all name 0.29.0.
  `export.SCHEMA_VERSION` remains **2**, as designed — it versions the CSV
  layout, not the release, and neither milestone changed an exported column.
- **What 0.26.1 changed, and why none of the evidence below moves.** It is a
  sidebar release: a guard that stops the Scan Preprocessing panel rendering
  an unexplained blank body, one shared answer to "may a measurement mesh be
  created" (`scancopy.creation_block`, which the operator's `poll()` now
  asks), the generated mesh becoming the active object, and a per-face
  material read taken off the slow RNA path. No solver, no preflight, no
  readiness policy, no topology analysis and no decimation code was touched;
  `preprocess.preflight` still refuses a non-manifold mesh, which §B of
  `tests/test_preprocess_panel_blender.py` now asserts directly.
- **What 0.26.2 changed on top of that.** Repair highlights are drawn as
  coloured solid rods instead of colourless one-pixel wires, sized in
  millimetres against the scan's own bounding box, with a Focus button that
  frames the non-manifold edges. Visualization only: no repair, readiness,
  solver-gate or topology code was touched, and
  `tests/test_repair_highlight_blender.py` asserts that a highlight leaves
  the measurement mesh's vertices, faces and topology verdict unchanged. The
  one non-cosmetic correction is that highlight sizes now convert out of
  solver millimetres through the unit multiplier and the object scale, which
  was wrong for any scan not stored in millimetres at scale 1 — it affects
  where a marker is drawn, never what is measured.
- **What 0.27.0 changed on top of that.** Milestone 3.28: a researcher who
  has inspected a non-manifold defect can delete the connected component it
  sits in, from the measurement mesh only. It is not scan cleaning and not
  automatic — BSMT never judges whether geometry is anatomically relevant,
  and it refuses outright when the defect is on the primary body component.
  The deletion is transactional on the existing `repair.accept_repair` rule
  plus four component-scoped additions (§D.10). No readiness rule, solver
  gate, degenerate-triangle policy, decimation or topology analysis changed,
  and no rule requiring a single connected component was introduced. One
  pre-existing gap was closed on this path: `_RepairBase._guarded` now
  re-analyses the edited mesh inside a guard, so an exception there restores
  the mesh instead of leaving an unvouched-for edit in place.
- **What 0.29.0 changed on top of that.** Five milestones, of which 3.29 and
  3.30 were packaged as the internal 0.28.0 build and never released.
  **3.30, Local Defect Repair**: removal of a small, unambiguously separable
  branch of faces at an inspected non-manifold defect *inside* a component —
  including the primary body, where component deletion is correctly refused
  — offered only when the local topology yields exactly one candidate branch
  and refused otherwise (§D.12). **3.29, Surface Region**: the first region
  model, built from already-solved surface paths — **superseded outright by
  3.31** and not in the released behaviour. **3.31**: a region is an ordered
  set of **landmarks**, Measurement Manager is not involved, and one explicit
  *Compute Boundary* is the only route to the geodesic solver (§J). **3.32**:
  **Surface Interior** — which side of the boundary is the region, with every
  triangle classified as inside, outside or **cut**, the cut ones clipped
  exactly, drawn as **Region Fill**, and offset outward as a **Thickness
  Preview** that is a visualization and not a simulation (§K). **3.33**:
  **Surface Area** on that classification, in mm² and cm², which is mesh area
  and **not** anatomical area (§L).

  No readiness rule, solver gate, degenerate-triangle policy, decimation or
  topology analysis was touched by any of them. The primary-component
  deletion block of 0.27.0, the local weld and duplicate-face removal are
  unchanged. CSV **schema v2 is untouched**, and protocol files carry no
  region, interior or area data.

- **What 0.29.0 does NOT establish.** Everything Milestones 3.31-3.33 add is
  verified on **synthetic geometry only** — planar grids, a closed sphere, a
  torus, two-component patches. No human scan has been measured. The distinct
  work that would make these numbers anthropometrically meaningful is listed
  in §L.7 and none of it is marked complete: landmark repeatability, area
  sensitivity to landmark placement, real-scan decimation sensitivity,
  comparison against independent mesh software, posture sensitivity, protocol
  reproducibility, and thickness-preview behaviour on highly curved human
  surfaces.
- **Every suite in this document was re-executed on 2026-09-15 at 0.29.0**,
  including the decimation suite (§C, ~700 s). Full regression across all
  **43** suite files: **4,970 checks, 0 failures** — 3,239 from the 21
  offline invocations and 1,781 from the 23 Blender invocations, counting
  `tests/test_panel_order.py` (which runs in both modes) once. Milestone 3.32
  added `tests/test_interior.py` (109),
  `tests/test_surface_interior_blender.py` (62) and
  `tests/test_thickness_preview_blender.py` (69); Milestone 3.33 adds
  `tests/test_surfacearea.py` (45) and
  `tests/test_surface_area_blender.py` (55). The increase
  over 0.27.0's 4,130 is the five new suites — `tests/test_regions.py` (102),
  `tests/test_surface_region_blender.py` (103),
  `tests/test_region_panel_draw_blender.py` (33), `tests/test_localrepair.py`
  (136) and `tests/test_local_face_repair_blender.py` (117) — plus one check
  added to §H by the fix below and one to `tests/test_import.py` by the
  fourth solver entry point Milestone 3.31 introduced. The two Surface Region
  suites are smaller than the 116/113 recorded before that milestone because
  they were rewritten for the landmark model, not because coverage was
  dropped: they now assert the measurement decoupling, which the old model
  could not have.
- **§H matches the tree.** Its 80/80 was executed against
  `dist/bsmt-0.29.0.zip` (`bf491e61…`), rebuilt after Milestone 3.36 from the
  working tree as it now stands. The §K.9 packaged tiling checks were measured
  from a clean extraction of that same artefact; the §K.8 performance figures
  were measured from its immediate predecessor, whose `interior.py` differs
  only by the projected-endpoint fix and is unchanged in cost. All 42 packaged Python modules were
  byte-compared to their working-tree originals — 42 identical, none missing
  in either direction — and the packaged `CHANGELOG.md` matches too. The
  packaged `README.md` is one revision behind the committed tree by exactly
  two corrected sentences of wording (§H.1b); no executable code, numerical
  result or package-validation claim is affected, and the artefact hash and
  its provenance stand. No section of this document rests on a stale
  artefact. Every other section above was executed from the current working
  tree.
- **One harness defect was found and fixed during this release build.**
  `tests/test_packaged_extension.py` asserted a hardcoded count of *eight*
  top-level workflow panels beside an `EXPECTED_STAGES` tuple that had grown
  to nine, so the first packaged run of the 0.28.0 ZIP failed. The literal is
  now derived from `len(EXPECTED_STAGES)`, matching the pattern
  `tests/test_panel_order.py` already used. No product code was changed; §H
  is 80 checks rather than 79 as a result.
- **`tests/test_backend_exact.py` must be run with plain `python3`**, not
  inside Blender. Its guard half asserts `inside_blender is False`, which is
  by design and correctly fails if the suite is run under Blender. Run as
  documented it reports **76 checks, 0 failures, 9 skipped groups** (the
  numerical groups skip when pygeodesic is absent from the system
  interpreter, which is not the same as a broken wrapper — the in-Blender
  numerical coverage is §A and §H).
- **The manifest licence is provisional** (`SPDX:GPL-3.0-or-later`). The build
  prints a warning on every run. It is a placeholder pending the project
  owner's decision — see `docs/LICENSING.md`.

Anyone citing a number from this document should cite it together with the
release it was produced from: **BSMT 0.29.0**.
