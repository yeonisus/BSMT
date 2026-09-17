# Phase-1 bounded exact-path acceleration (DRAFT — nothing here is shipped)

**Status: DRAFT.** This document describes an integration staged in the
working tree for review. It is not released, not tagged, VERSION is
unchanged, and no release ZIP has been rebuilt with it. Nothing in this
file should be read as "BSMT now does X" — every claim below is "BSMT can
optionally do X, when a specific dependency build is present."

## What this is

An optional acceleration of the Region Boundary solve (`solve.surface_path`,
used by Compute Boundary for every segment of a Surface Region). It is
**exact same MMP method** — the same polyhedral geodesic algorithm BSMT has
always used, wrapped by the same pygeodesic library, with the same guarantee
of no approximation, no Dijkstra, no heat method, and no interpolation
(sect. 5.2, sect. 5.3). Nothing about *what* is computed changes; only how
fast a Region's path segments can be produced.

## Why it exists

Today, a Region Boundary path segment is solved with an **unbounded** exact
query (`geodesicDistance`), which sweeps the entire mesh regardless of how
close the two landmarks are — tens of seconds per segment at scan scale
(measured: ~26 s on a 313,880-triangle body-proportioned mesh, *any*
separation). BSMT's production *distance* query already avoids this via an
expanding-bound strategy (`registry.bounded_distance`), but that strategy
has never been able to also return a **path** — a path needs
`geodesicDistance()`, and `geodesicDistance()` cannot be bounded. Region
Boundary needs a path for every segment, so it has always paid the full
unbounded cost.

A locally-patched build of pygeodesic adds one additive method,
`geodesicDistanceAndPathBounded()`, which returns a distance **and** a path
from a single bounded query. Where that method is available, BSMT's
expanding-bound strategy is extended to use it (`registry.
bounded_distance_and_path`), turning Region Boundary's per-segment cost from
"always unbounded" into "usually the same small bound the distance query
already uses."

## Automatic fallback — always present, never assumed away

BSMT does **not** hard-depend on the patched method. A single runtime
capability probe, `registry.bounded_path_capability()` (`exact_mmp.
bounded_path_capability()` underneath — a plain `hasattr()` check on the
installed `pygeodesic.geodesic.PyGeodesicAlgorithmExact`), decides the route
every time `solve.surface_path()` runs:

- **Capability present** → the bounded route (`registry.
  bounded_distance_and_path`) is used, and it returns a path in one bounded
  call instead of one unbounded call.
- **Capability absent** → the original unbounded route
  (`solver.distance_and_path()`) runs, byte-for-byte the same code that has
  always been there. This is the state on every platform except BSMT's own
  patched macOS ARM64 build — in particular, **Windows always takes this
  path today**, because only a macOS ARM64 wheel has been patched.
- **A genuine solver or geometry failure** (the dependency imports fine, the
  method exists, but the query itself fails) is reported as a failure —
  `MeasurementError('BACKEND_ERROR', …)` — exactly as production has always
  reported a solver failure. It is never silently retried as if the
  capability were absent; only an actual capability/dependency gap routes to
  the fallback.

## Authoritative direction — Policy A, unchanged

Every Region Boundary segment is still solved `ordered[i] -> ordered[i+1]`,
in that exact direction — never reversed, never reused across two segments.
This integration does not touch, and does not need, any multi-target or
reverse-query capability (BSMT's "Phase 2" prototype, evaluated and
explicitly **not approved** for production — see the tie-policy decision in
project memory). Both the bounded and unbounded routes answer the *same*
`(point_a, point_b)` pair every time.

## Performance — geometry-dependent, report a range

Speedup depends on how local a segment is relative to the mesh, because the
unbounded route's cost is constant (always sweeps the whole mesh) while the
bounded route's cost scales with the segment's own straight-line distance.
Measured on a 357,600-triangle durable fixture: whole-region speedup in the
tens of times (see the project's prototype benchmark record for exact
numbers and methodology). **Do not quote a single multiplier** as if it
generalizes to every scan and every landmark placement.

## What stays exactly the same

- `solve.surface_path()`'s signature, return type (`SurfacePathResult`), and
  every exception it can raise.
- Ordered landmark definition, segment ordering, `SurfacePoint` semantics,
  Region cache schema, Interior, and Surface Area — none of these modules
  were touched.
- `max_distance` semantics: a **minimum sweep radius**, never a truncation.
  An under-estimate costs time, never correctness — the same exact geodesic
  comes back regardless of the bound, provided the target is reachable at
  all. This is the same guarantee production's existing bounded *distance*
  query has always relied on; the bounded *path* query inherits it, not a
  new one.

## What is known and unresolved

- **A pre-existing pygeodesic numerical fragility exists**, independent of
  this integration: on certain (mesh, query) combinations, the *unmodified,
  upstream* algorithm can hit an internal C++ assertion and abort the whole
  process, rather than raising a catchable Python exception. This has been
  observed on both the stock and the patched build, on specific
  hand-constructed test geometry. This integration does not attempt to fix
  it and does not claim to eliminate it — see the prototype's own notes for
  what was observed and ruled out.
- **A locally-rebuilt `.so` is not proven bit-identical to the official
  upstream-built wheel.** BSMT's own existing Blender test suite
  (`test_surface_region_blender.py`, `test_surface_interior_blender.py`,
  `test_surface_area_blender.py`) passes 100% against both the official
  stock wheel and a correctly-installed patched wheel; however, a
  *separately, locally rebuilt* "stock" `.so` (same source, different
  toolchain) was observed to crash on inputs the official wheel handles
  without issue. This is a build-reproducibility risk to resolve — most
  likely by building the patched wheel with the same toolchain/CI upstream
  uses, or by landing the change upstream so an official release provides
  it — **before** any patched wheel ships to a researcher, not by trusting
  a local rebuild.
- **Windows validation is still pending.** `docs/windows_acceptance.md`
  records that nothing has been run on Windows. This integration changes
  nothing about that status: Windows has no patched wheel and always takes
  the unbounded fallback, unmodified.
- **BSMT's licence is still PROVISIONAL** (`docs/LICENSING.md`,
  `SPDX:GPL-3.0-or-later`, undecided). This blocks distribution of any BSMT
  package, independent of anything in this document.
- **The patched wheel is staged, not shipped.** It lives at
  `wheels/patched_staging/`, deliberately outside `wheels/` so
  `tools/build_release.py` does not pick it up automatically — bundling it
  into a real release is a decision for whoever chooses to run that build,
  not something this integration does silently. Its filename and its
  `pygeodesic.__version__` (`0.1.11+bsmt.phase1`) both mark it as a locally
  patched build, never the official upstream release, per MIT's notice
  requirement.

## Files

- `body_surface_measurement/geodesic/backends/exact_mmp.py` —
  `BOUNDED_PATH_CAPABLE`, `bounded_path_capability()`, `ExactSolver.
  distance_and_path_bounded()`.
- `body_surface_measurement/geodesic/registry.py` —
  `bounded_path_capability()`, `bounded_distance_and_path()`.
- `body_surface_measurement/geodesic/solve.py` — `surface_path()`'s query
  section branches on `registry.bounded_path_capability()`; everything else
  in the function is unchanged.
- `tests/test_phase1_bounded_path.py` — routing/failure-policy tests
  (dependency-injected stub solver, no backend required) and numerical
  equivalence tests (need a real backend; both routes are exercised via
  `forced_capability()` regardless of what is actually installed).
- `wheels/patched_staging/pygeodesic-0.1.11+bsmt.phase1-cp311-cp311-macosx_11_0_arm64.whl`
  — the staged, clearly-marked, macOS ARM64 / CPython 3.11 patched wheel.
  Not referenced by `tools/build_release.py`.
