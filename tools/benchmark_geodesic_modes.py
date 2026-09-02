"""BSMT — focused performance investigation ahead of Milestone 2.3.

ONE question: can pygeodesic's one-to-all / one-to-many API materially reduce
runtime for repeated landmark measurements on a ~314k-triangle body scan,
compared with repeated pairwise queries?

This is an investigation script. It is NOT part of the add-on, it imports no
production measurement state, it touches no SurfacePoint, and it changes
nothing. Mesh generators come from the add-on's own
``geodesic/backends/selftest.py`` so the meshes are the ones already validated
in Milestone 2.2.

    "/Applications/Blender.app/Contents/MacOS/Blender" --background \
        --python /Users/yeoni/BSMT/tools/benchmark_geodesic_modes.py

Modes measured
--------------
A   pairwise      geodesicDistance(s, t) once per target. Returns a path.
B1  one-to-all    geodesicDistances([s]) with target_indices=None.
                  Propagates over the WHOLE mesh, returns a distance for every
                  vertex, no path.
B2  one-to-many   geodesicDistances([s], [t1,t2,t3]).
                  Propagates with the targets as stop points, so it terminates
                  once the last target is settled. No path.

C   bounded       geodesicDistances([s], [t], max_distance=finite).
                  The same call as B2 with a finite propagation radius.

B2 and C are not in the original brief. They matter because of what the
Kirsanov source actually does, which the measurements then confirmed.

    // geodesic_algorithm_exact.h, check_stop_conditions()
    double queue_distance = (*m_queue.begin())->min();
    if (queue_distance < stop_distance()) return false;   // == m_max_propagation_distance
    while (index < m_stop_vertices.size()) { ... }

Both Python entry points pass GEODESIC_INF as max_distance - geodesicDistance
hardcodes it, geodesicDistances defaults to it - so `queue_distance < INF` is
always true and the function returns **before ever examining the stop
vertices**. Stop points are therefore inert on their own, which is why an
unbounded query costs the same whether the target is 170 mm or 1366 mm away.

Supplying any finite max_distance re-enables the stop-vertex loop, and that
loop is what actually guarantees termination is safe: it refuses to stop until
every stop vertex is settled. max_distance behaves as a *minimum* sweep radius,
not as a truncation of the answer.

Why two meshes
--------------
MMP cost is driven by how much surface the wavefront has to sweep before the
target is settled, not by triangle count alone. A sphere and a long thin body
of the same triangle count behave differently, so both are measured: the
icosphere for continuity with the Milestone 2.2 benchmark already recorded in
PROJECT_SPEC.md sect. 5.1a, and a body-proportioned cylinder (R = 150 mm,
H = 1700 mm — the reference scan's height) as the closer proxy for a torso.
"""

import importlib.util
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GEODESIC = os.path.join(ROOT, "body_surface_measurement", "geodesic")

SEPARATOR = "-" * 78


def load_backend():
    """Load geodesic.backends by path, without importing bpy or the add-on."""
    package = types.ModuleType("bsmt_bench")
    package.__path__ = [os.path.join(ROOT, "body_surface_measurement")]
    sys.modules["bsmt_bench"] = package

    geodesic = types.ModuleType("bsmt_bench.geodesic")
    geodesic.__path__ = [GEODESIC]
    sys.modules["bsmt_bench.geodesic"] = geodesic

    spec = importlib.util.spec_from_file_location(
        "bsmt_bench.geodesic.backends",
        os.path.join(GEODESIC, "backends", "__init__.py"),
        submodule_search_locations=[os.path.join(GEODESIC, "backends")],
    )
    backends = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_bench.geodesic.backends"] = backends
    spec.loader.exec_module(backends)
    return backends


def rss_mb():
    """Peak resident set size in MB, or None where it is not observable."""
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # noqa: BLE001
        return None
    return (value if sys.platform == "darwin" else value * 1024) / (1024.0 * 1024.0)


def heading(text):
    print("")
    print(text)
    print(SEPARATOR)


# ---------------------------------------------------------------------------
# test meshes and their landmark-like target sets
# ---------------------------------------------------------------------------

def build_icosphere(selftest, np):
    """~327k triangles. Continuity with the sect. 5.1a benchmark."""
    vertices, faces = selftest.icosphere(100.0, 7)
    source = int(np.argmax(vertices[:, 2]))
    axis = vertices[source] / 100.0
    cosines = np.clip(vertices @ axis / 100.0, -1.0, 1.0)
    angles = np.degrees(np.arccos(cosines))
    targets = []
    for wanted in (20.0, 60.0, 120.0):
        targets.append(int(np.argmin(np.abs(angles - wanted))))
    labels = ["t1 near (~20 deg)", "t2 mid (~60 deg)", "t3 far (~120 deg)"]
    return "icosphere R=100 mm", vertices, faces, source, targets, labels


def build_body_cylinder(selftest, np):
    """~314k triangles, body proportions: R = 150 mm, H = 1710 mm.

    1710 mm is the measured height of the reference scan 21_M_3400E. The scan
    itself is not loaded; only its scale is borrowed, so the wavefront has to
    sweep a long thin surface the way it would on a real torso.
    """
    radius, height = 150.0, 1710.0
    n_theta, n_z = 295, 532                      # 295*532*2 = 313,880 triangles
    vertices, faces = selftest.cylinder_mesh(radius, height, n_theta, n_z)

    def vertex(i_theta, j_z):
        return selftest.cylinder_index(n_theta, i_theta, j_z)

    source = vertex(0, int(n_z * 0.10))
    targets = [
        vertex(0, int(n_z * 0.20)),                        # straight up, close
        vertex(n_theta // 4, int(n_z * 0.45)),             # quarter turn, mid
        vertex(n_theta // 2, int(n_z * 0.85)),             # half turn, far
    ]
    labels = [
        "t1 near (same meridian, +171 mm)",
        "t2 mid (quarter turn, +599 mm)",
        "t3 far (half turn, +1283 mm)",
    ]
    return ("body-proportioned cylinder R=150 mm H=1710 mm",
            vertices, faces, source, targets, labels)


# ---------------------------------------------------------------------------
# the measurement itself
# ---------------------------------------------------------------------------

def run_case(backends, np, name, vertices, faces, source, targets, labels):
    exact = backends.exact_mmp
    heading("MESH: %s" % name)
    print("  vertices  : %d" % vertices.shape[0])
    print("  triangles : %d" % faces.shape[0])
    print("  source    : vertex %d" % source)
    for label, target in zip(labels, targets):
        straight = float(np.linalg.norm(vertices[target] - vertices[source]))
        print("  target    : vertex %-7d  %-34s straight %8.2f mm"
              % (target, label, straight))

    results = {"mesh": name, "vertices": int(vertices.shape[0]),
               "triangles": int(faces.shape[0])}

    # ---- construction ----------------------------------------------------
    rss_start = rss_mb()
    started = time.perf_counter()
    solver = exact.ExactSolver(vertices, faces)
    construction = time.perf_counter() - started
    rss_built = rss_mb()
    print("")
    print("  solver construction        : %8.3f s   (peak RSS %.0f -> %.0f MB)"
          % (construction, rss_start, rss_built))
    results["construction_s"] = construction

    algorithm = solver._algorithm      # one-to-all is not exposed by the wrapper

    # ---- A. pairwise -----------------------------------------------------
    print("")
    print("  A. PAIRWISE  geodesicDistance(source, target)  - returns a path")
    pairwise = []
    for label, target in zip(labels, targets):
        started = time.perf_counter()
        distance, path = solver.distance_and_path(source, target)
        elapsed = time.perf_counter() - started
        pairwise.append({"target": target, "label": label,
                         "distance": distance, "seconds": elapsed,
                         "path_points": int(path.shape[0])})
        print("     %-36s %8.3f s   d = %10.4f mm   path %d pts"
              % (label, elapsed, distance, path.shape[0]))
    pairwise_total = sum(row["seconds"] for row in pairwise)
    rss_pairwise = rss_mb()
    print("     %-36s %8.3f s   <- total for 3 targets"
          % ("PAIRWISE TOTAL", pairwise_total))
    print("     peak RSS after pairwise   : %.0f MB" % rss_pairwise)
    results["pairwise"] = pairwise
    results["pairwise_total_s"] = pairwise_total

    # ---- B1. one-to-all --------------------------------------------------
    print("")
    print("  B1. ONE-TO-ALL  geodesicDistances([source])  - whole mesh, no path")
    started = time.perf_counter()
    field, best_source = algorithm.geodesicDistances(
        np.asarray([source], dtype=np.int32), None
    )
    one_to_all = time.perf_counter() - started
    rss_field = rss_mb()
    finite = np.isfinite(field)
    print("     propagation + readout     : %8.3f s" % one_to_all)
    print("     returned                  : %s, dtype %s, %d finite of %d"
          % (field.shape, field.dtype, int(finite.sum()), field.size))
    print("     field memory              : %.1f MB (float64 x %d vertices)"
          % (field.nbytes / 1024.0 / 1024.0, field.size))
    print("     max finite distance       : %10.4f mm" % float(field[finite].max()))
    print("     peak RSS after one-to-all : %.0f MB" % rss_field)
    results["one_to_all_s"] = one_to_all
    results["field_mb"] = field.nbytes / 1024.0 / 1024.0

    lookups = []
    for label, target, row in zip(labels, targets, pairwise):
        started = time.perf_counter()
        value = float(field[target])
        elapsed = time.perf_counter() - started
        delta = abs(value - row["distance"])
        relative = delta / row["distance"] if row["distance"] else 0.0
        lookups.append({"label": label, "value": value, "seconds": elapsed,
                        "abs_delta_mm": delta, "rel_delta": relative})
        print("     lookup %-30s %.3e s   d = %10.4f mm   |diff| = %.3e mm (rel %.3e)"
              % (label, elapsed, value, delta, relative))
    results["lookups"] = lookups

    # ---- B2. one-to-many with stop points --------------------------------
    print("")
    print("  B2. ONE-TO-MANY  geodesicDistances([source], [t1,t2,t3])")
    print("      propagate() receives the targets as stop points, so it")
    print("      terminates once the last one is settled. No path.")
    started = time.perf_counter()
    subset, subset_source = algorithm.geodesicDistances(
        np.asarray([source], dtype=np.int32),
        np.asarray(targets, dtype=np.int32),
    )
    one_to_many = time.perf_counter() - started
    rss_subset = rss_mb()
    print("     propagation + readout     : %8.3f s" % one_to_many)
    print("     returned                  : %s in target order" % (subset.shape,))
    for label, value, row in zip(labels, subset, pairwise):
        delta = abs(float(value) - row["distance"])
        relative = delta / row["distance"] if row["distance"] else 0.0
        print("     %-36s d = %10.4f mm   |diff| = %.3e mm (rel %.3e)"
              % (label, float(value), delta, relative))
    print("     peak RSS after one-to-many: %.0f MB" % rss_subset)
    results["one_to_many_s"] = one_to_many
    results["one_to_many_values"] = [float(v) for v in subset]

    # ---- path availability after a field solve ---------------------------
    print("")
    print("  PATH AVAILABILITY AFTER ONE-TO-ALL")
    print("     geodesicDistances returns %d values: (distances, best_source)."
          % 2)
    print("     Neither is a path. geodesic.pyx exposes no trace_back entry")
    print("     point, so a polyline for an arbitrary target requires a")
    print("     separate geodesicDistance call. Cost of that call, made")
    print("     immediately after the field solve on the same solver object:")
    target = targets[-1]
    started = time.perf_counter()
    distance, path = solver.distance_and_path(source, target)
    after_field = time.perf_counter() - started
    before = pairwise[-1]["seconds"]
    print("     %-36s %8.3f s   (cold pairwise was %.3f s)"
          % ("path for t3 after the field solve", after_field, before))
    print("     ratio to cold pairwise    : %.2fx  ->  %s"
          % (after_field / before if before else float("nan"),
             "state is NOT reused" if after_field > 0.5 * before
             else "state IS reused"))
    results["path_after_field_s"] = after_field
    results["path_after_field_ratio"] = after_field / before if before else None

    # ---- C. bounded queries ----------------------------------------------
    print("")
    print("  C. BOUNDED  geodesicDistances([source], [t], max_distance=k*d)")
    print("     A finite max_distance re-enables the stop-vertex check that")
    print("     GEODESIC_INF disables. Exactness is verified against A, not")
    print("     assumed: a bound that truncated the answer would show here.")
    print("     %-24s %-6s %10s %9s %14s  %s"
          % ("target", "k", "bound(mm)", "time(s)", "distance(mm)", "verdict"))
    bounded = []
    for label, target, row in zip(labels, targets, pairwise):
        d_true = row["distance"]
        for k in (0.5, 1.05, 1.25):
            started = time.perf_counter()
            values, _ = algorithm.geodesicDistances(
                np.asarray([source], dtype=np.int32),
                np.asarray([target], dtype=np.int32),
                d_true * k,
            )
            elapsed = time.perf_counter() - started
            value = float(values[0])
            if not np.isfinite(value):
                verdict = "inf - target not covered (safe, detectable)"
                exact = None
            elif abs(value - d_true) <= 1e-9 * max(1.0, d_true):
                verdict = "EXACT (%.1fx faster than unbounded)" % (
                    row["seconds"] / elapsed if elapsed else float("inf"))
                exact = True
            else:
                verdict = "WRONG by %.3e mm - DO NOT USE" % (value - d_true)
                exact = False
            bounded.append({"label": label, "k": k, "seconds": elapsed,
                            "value": value, "exact": exact,
                            "speedup": row["seconds"] / elapsed if elapsed else None})
            print("     %-24s %-6.3f %10.2f %9.3f %14.6f  %s"
                  % (label[:24], k, d_true * k, elapsed, value, verdict))
    results["bounded"] = bounded

    # ---- summary ---------------------------------------------------------
    print("")
    print("  SUMMARY for %s" % name)
    print("     construction                        %8.3f s" % construction)
    print("     A  pairwise, 3 targets (with paths) %8.3f s" % pairwise_total)
    print("     B1 one-to-all field (no path)       %8.3f s   %+.1f%% vs A"
          % (one_to_all, 100.0 * (one_to_all - pairwise_total) / pairwise_total))
    print("     B2 one-to-many stop points (no path)%8.3f s   %+.1f%% vs A"
          % (one_to_many, 100.0 * (one_to_many - pairwise_total) / pairwise_total))
    print("     lookup per target from the field    %8.3e s" % lookups[0]["seconds"])
    print("     worst |B1 - A| over the 3 targets   %.3e mm"
          % max(row["abs_delta_mm"] for row in lookups))
    tight = [row for row in bounded if row["k"] == 1.05]
    for row in tight:
        print("     C  bounded 1.05x %-20s %8.3f s   %6.1fx faster than pairwise"
              % (row["label"][:20], row["seconds"], row["speedup"] or float("nan")))
    print("     every bounded result exact vs A     : %s"
          % all(row["exact"] for row in bounded))
    return results


def main():
    print("BSMT - geodesic query-mode benchmark (Milestone 2.3 preparation)")
    print(SEPARATOR)

    try:
        backends = load_backend()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("FAIL: could not load the BSMT backend: %s" % exc)
        return 2

    if not backends.availability():
        print("STOP: %s" % backends.unavailable_reason())
        return 1

    import numpy as np
    try:
        import bpy
        print("  Blender    : %s" % bpy.app.version_string)
    except Exception:  # noqa: BLE001
        print("  Blender    : not running inside Blender")
    print("  python     : %s" % sys.version.split()[0])
    print("  numpy      : %s" % np.__version__)
    print("  pygeodesic : %s" % backends.backend_version())
    print("  baseline RSS: %.0f MB" % rss_mb())

    selftest = backends.selftest
    cases = []
    for builder in (build_icosphere, build_body_cylinder):
        started = time.perf_counter()
        name, vertices, faces, source, targets, labels = builder(selftest, np)
        print("")
        print("  generated %-52s in %.2f s" % (name, time.perf_counter() - started))
        cases.append(run_case(backends, np, name, vertices, faces,
                              source, targets, labels))

    heading("CROSS-MESH COMPARISON")
    print("  %-46s %9s %9s %9s %9s"
          % ("mesh", "constr", "A total", "B1 all", "B2 many"))
    for case in cases:
        print("  %-46s %8.2fs %8.2fs %8.2fs %8.2fs"
              % (case["mesh"][:46], case["construction_s"],
                 case["pairwise_total_s"], case["one_to_all_s"],
                 case["one_to_many_s"]))
    print("")
    print("  peak RSS at end: %.0f MB" % rss_mb())
    return 0


if __name__ == "__main__":
    sys.exit(main())
