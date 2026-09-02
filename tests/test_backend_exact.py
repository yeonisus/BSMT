"""Offline tests for the Milestone 2.2 exact geodesic backend wrapper.

Runs OUTSIDE Blender with plain python3 + numpy:

    python3 tests/test_backend_exact.py

Two halves, with different requirements:

* the **guard tests** run everywhere, with or without pygeodesic installed.
  They are the ones that matter most, because the binding requirement of
  Milestone 2.2 is that BSMT loads and behaves correctly when the backend is
  ABSENT. They are never skipped.
* the **numerical tests** need a working pygeodesic. When it is missing they
  report SKIP with the reason and do not fail the run - an offline machine
  without the wheel must not be indistinguishable from a broken wrapper.
  Whether it is present is printed explicitly at the top of the run.

The numerical work itself lives in
``body_surface_measurement/geodesic/backends/selftest.py`` so that the
in-Blender operator and this file exercise one implementation, not two that
can drift apart.
"""

import importlib.util
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILURES = []
SKIPS = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def skip(label, reason):
    SKIPS.append(label)
    print("  SKIP  %s (%s)" % (label, reason))


def _load_package():
    """Import the geodesic backends subpackage without importing bpy.

    ``body_surface_measurement/__init__.py`` imports bpy, so the package is
    registered under a private name with an explicit submodule search path
    instead. That keeps this test free of the bpy stub machinery of
    tests/test_import.py, which is testing a different thing.
    """
    import types

    package = types.ModuleType("bsmt_offline")
    package.__path__ = [os.path.join(ROOT, "body_surface_measurement")]
    sys.modules["bsmt_offline"] = package

    geodesic = types.ModuleType("bsmt_offline.geodesic")
    geodesic.__path__ = [
        os.path.join(ROOT, "body_surface_measurement", "geodesic")
    ]
    sys.modules["bsmt_offline.geodesic"] = geodesic

    spec = importlib.util.spec_from_file_location(
        "bsmt_offline.geodesic.backends",
        os.path.join(ROOT, "body_surface_measurement", "geodesic", "backends",
                     "__init__.py"),
        submodule_search_locations=[
            os.path.join(ROOT, "body_surface_measurement", "geodesic",
                         "backends")
        ],
    )
    backends = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_offline.geodesic.backends"] = backends
    spec.loader.exec_module(backends)

    envspec = importlib.util.spec_from_file_location(
        "bsmt_offline.geodesic.envreport",
        os.path.join(ROOT, "body_surface_measurement", "geodesic",
                     "envreport.py"),
    )
    envreport = importlib.util.module_from_spec(envspec)
    sys.modules["bsmt_offline.geodesic.envreport"] = envreport
    envspec.loader.exec_module(envreport)

    return backends, envreport


backends, envreport = _load_package()
exact = backends.exact_mmp
selftest = backends.selftest
HAVE_BACKEND = backends.availability()


# ---------------------------------------------------------------------------
# guard tests - must pass with or without pygeodesic
# ---------------------------------------------------------------------------

def test_package_imports_without_backend():
    print("\n[guard] the wrapper imports whether or not pygeodesic does")
    check("backends package imported", backends is not None)
    check("exact_mmp wrapper imported", exact is not None,
          "wrapper error: %s" % backends.EXACT_MMP_IMPORT_ERROR)
    check("selftest module imported", selftest is not None,
          "error: %s" % backends.SELFTEST_IMPORT_ERROR)
    check("status() never raises", isinstance(backends.status(), dict))
    check("availability() returns a bool",
          isinstance(backends.availability(), bool))
    check("backend_version() returns a str",
          isinstance(backends.backend_version(), str))

    status = backends.status()
    for key in ("backend_name", "available", "version", "module_path",
                "import_error", "import_traceback", "wrapper_error"):
        check("status() has %r" % key, key in status)
    check("status() reports the same availability as availability()",
          status["available"] == backends.availability())


def test_import_error_is_preserved_not_swallowed():
    print("\n[guard] a failed pygeodesic import keeps its original error")
    if HAVE_BACKEND:
        check("import_error empty while available", exact.IMPORT_ERROR == "")
        check("import_traceback empty while available",
              exact.IMPORT_TRACEBACK == "")
        check("unavailable_reason() empty while available",
              backends.unavailable_reason() == "")
    else:
        check("import_error is populated", bool(exact.IMPORT_ERROR),
              "expected the original exception text")
        check("import_traceback is populated", bool(exact.IMPORT_TRACEBACK),
              "expected the complete original traceback")
        check("traceback mentions Traceback",
              "Traceback" in exact.IMPORT_TRACEBACK)
        check("unavailable_reason() carries the original text",
              exact.IMPORT_ERROR in backends.unavailable_reason())


def test_unavailable_backend_raises_never_substitutes():
    print("\n[guard] an unavailable backend raises; it never returns a number")
    if HAVE_BACKEND:
        skip("BackendUnavailable path", "backend is installed here")
        return
    vertices, faces = selftest.plane_grid(2, 2, 1.0)
    try:
        exact.compute_distance_and_path(vertices, faces, 0, 8)
    except exact.BackendUnavailable as exc:
        check("raises BackendUnavailable", True)
        check("message carries the import error",
              exact.IMPORT_ERROR in str(exc))
    except Exception as exc:  # noqa: BLE001
        check("raises BackendUnavailable", False,
              "raised %s instead" % type(exc).__name__)
    else:
        check("raises BackendUnavailable", False, "returned a value")


def test_mesh_validation_is_backend_independent():
    print("\n[guard] mesh validation rejects bad input before any solver call")
    vertices, faces = selftest.plane_grid(3, 3, 2.0)

    good_v, good_f = exact.prepare_mesh(vertices, faces)
    check("prepare_mesh promotes vertices to float64",
          good_v.dtype == np.float64)
    check("prepare_mesh coerces triangles to int32", good_f.dtype == np.int32)
    check("prepare_mesh returns C-contiguous arrays",
          good_v.flags["C_CONTIGUOUS"] and good_f.flags["C_CONTIGUOUS"])
    check("prepare_mesh preserves the vertex values",
          np.allclose(good_v, vertices))

    def rejects(label, call):
        try:
            call()
        except exact.InvalidMeshError:
            check(label, True)
        except Exception as exc:  # noqa: BLE001
            check(label, False, "raised %s instead" % type(exc).__name__)
        else:
            check(label, False, "accepted the input")

    rejects("rejects an empty vertex array",
            lambda: exact.prepare_mesh(np.zeros((0, 3)), faces))
    rejects("rejects an empty triangle array",
            lambda: exact.prepare_mesh(vertices, np.zeros((0, 3), np.int32)))
    rejects("rejects (n,2) vertices",
            lambda: exact.prepare_mesh(np.zeros((10, 2)), faces))
    rejects("rejects (m,4) triangles",
            lambda: exact.prepare_mesh(vertices, np.zeros((5, 4), np.int32)))
    rejects("rejects NaN coordinates",
            lambda: exact.prepare_mesh(selftest._with_nan(vertices), faces))
    rejects("rejects float triangle indices",
            lambda: exact.prepare_mesh(vertices, faces.astype(np.float64)))
    rejects("rejects an out-of-range triangle index",
            lambda: exact.prepare_mesh(
                vertices, selftest._with_bad_index(faces, 9999)))
    rejects("rejects a negative triangle index",
            lambda: exact.prepare_mesh(
                vertices, selftest._with_bad_index(faces, -1)))
    rejects("rejects a triangle with a repeated index",
            lambda: exact.prepare_mesh(vertices, selftest._with_repeat(faces)))
    rejects("path_length rejects a 1-point path",
            lambda: exact.path_length(np.zeros((1, 3))))


def test_generators_are_valid_meshes():
    print("\n[guard] the synthetic generators produce solvable meshes")
    cases = (
        ("plane forward", selftest.plane_grid(6, 6, 3.0, diagonal="forward")),
        ("plane backward", selftest.plane_grid(6, 6, 3.0, diagonal="backward")),
        ("plane alternating",
         selftest.plane_grid(6, 6, 3.0, diagonal="alternating")),
        ("plane sliver", selftest.plane_grid(6, 6, 3.0, diagonal="sliver")),
        ("cylinder", selftest.cylinder_mesh(50.0, 200.0, 24, 10)),
        ("icosphere", selftest.icosphere(100.0, 2)),
    )
    for label, (vertices, faces) in cases:
        try:
            exact.prepare_mesh(vertices, faces)
            ok, detail = True, ""
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        check("%s passes mesh validation" % label, ok, detail)
        areas = np.linalg.norm(
            np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                     vertices[faces[:, 2]] - vertices[faces[:, 0]]), axis=1
        ) * 0.5
        check("%s has no zero-area triangle" % label, bool((areas > 0).all()),
              "min area %.3e" % float(areas.min()))

    # Every vertex of the generated cylinder and icosphere must lie exactly on
    # the analytic surface, or the "reference" they are compared against is
    # not the surface they sample.
    vertices, _ = selftest.cylinder_mesh(50.0, 200.0, 32, 16)
    radii = np.linalg.norm(vertices[:, :2], axis=1)
    check("cylinder vertices lie on r = 50 mm",
          float(np.abs(radii - 50.0).max()) < 1e-9,
          "max deviation %.3e" % float(np.abs(radii - 50.0).max()))
    vertices, _ = selftest.icosphere(100.0, 3)
    radii = np.linalg.norm(vertices, axis=1)
    check("icosphere vertices lie on R = 100 mm",
          float(np.abs(radii - 100.0).max()) < 1e-9,
          "max deviation %.3e" % float(np.abs(radii - 100.0).max()))


def test_analytic_references():
    print("\n[guard] the closed-form references are themselves correct")
    check("cylinder: pure axial move equals the height difference",
          abs(selftest.cylinder_unrolled(50.0, 0.0, 0.0, 0.0, 30.0) - 30.0) < 1e-12)
    check("cylinder: half turn equals pi*R",
          abs(selftest.cylinder_unrolled(50.0, 0.0, 0.0, np.pi, 0.0)
              - np.pi * 50.0) < 1e-12)
    check("cylinder: takes the SHORTER way around (3pi/2 -> pi/2)",
          abs(selftest.cylinder_unrolled(50.0, 0.0, 0.0, 1.5 * np.pi, 0.0)
              - 0.5 * np.pi * 50.0) < 1e-12)
    check("cylinder: quarter turn plus rise is a right triangle",
          abs(selftest.cylinder_unrolled(50.0, 0.0, 0.0, np.pi / 2, 40.0)
              - np.hypot(np.pi / 2 * 50.0, 40.0)) < 1e-12)
    check("sphere: quarter great circle equals pi*R/2",
          abs(selftest.great_circle(100.0, np.array([100.0, 0, 0]),
                                    np.array([0, 100.0, 0]))
              - np.pi * 50.0) < 1e-9)
    check("sphere: antipodal equals pi*R",
          abs(selftest.great_circle(100.0, np.array([0, 0, 100.0]),
                                    np.array([0, 0, -100.0]))
              - np.pi * 100.0) < 1e-9)
    check("sphere: identical points give 0",
          abs(selftest.great_circle(100.0, np.array([0, 0, 100.0]),
                                    np.array([0, 0, 100.0]))) < 1e-9)


def test_environment_report_never_raises():
    print("\n[guard] the environment report works outside Blender too")
    info = envreport.collect()
    for key in ("python_version", "executable", "system", "machine",
                "wheel_tag", "inside_blender", "install_commands",
                "install_targets", "backend"):
        check("collect() reports %r" % key, key in info)
    check("inside_blender is False here", info["inside_blender"] is False)
    check("wheel tag matches this interpreter",
          info["wheel_tag"] == "cp%d%d" % sys.version_info[:2])
    lines = envreport.format_report(info)
    check("format_report returns lines", isinstance(lines, list) and lines)
    text = "\n".join(lines)
    check("report names the interpreter", info["executable"] in text)
    check("report contains an install command",
          any("pip install" in entry["command"]
              for entry in info["install_commands"]))
    check("no install command uses sudo",
          not any("sudo" in entry["command"]
                  for entry in info["install_commands"]))
    check("install commands use --no-deps",
          all("--no-deps" in entry["command"]
              for entry in info["install_commands"]
              if "pip install" in entry["command"]),
          "numpy must never be upgraded underneath Blender")
    check("report states the backend availability",
          "pygeodesic:" in text)


def test_selftest_reports_unavailable_backend_cleanly():
    print("\n[guard] run_all() reports a missing backend instead of crashing")
    if HAVE_BACKEND:
        skip("run_all() unavailable path", "backend is installed here")
        return
    report = selftest.run_all(include_dense=False)
    check("run_all() returns a report", isinstance(report, dict))
    check("report is not a pass", report.get("pass") is False)
    check("report explains why", bool(report.get("error")))
    lines = selftest.format_report(report)
    check("format_report handles the unavailable report",
          isinstance(lines, list) and any("Unavailable" in line or "NO" in line
                                          for line in lines))


# ---------------------------------------------------------------------------
# numerical tests - need a working pygeodesic
# ---------------------------------------------------------------------------

def test_plane_is_exact():
    print("\n[numeric] plane: exact backend must reproduce Euclidean distance")
    if not HAVE_BACKEND:
        skip("plane exactness", backends.unavailable_reason())
        return
    suite = selftest.run_plane(include_dijkstra=True)
    for row in suite["rows"]:
        check(
            "plane %s %s: rel err %.3e <= %.0e"
            % (row["triangulation"], row["grid"], row["rel_error"],
               selftest.PLANE_REL_TOL),
            row["pass"],
            "exact=%.12f reference=%.12f" % (row["exact_mm"], row["reference_mm"]),
        )
    check("every planar triangulation is exact", suite["pass"])

    # The diagnostic comparison, not a requirement on the exact backend.
    biased = [row for row in suite["rows"] if "dijkstra_rel_error" in row]
    if biased:
        worst = max(row["dijkstra_rel_error"] for row in biased)
        check("edge-Dijkstra shows a positive bias the exact method does not",
              worst > 1e-3,
              "worst Dijkstra relative error %.3e" % worst)
        for row in biased:
            print("        %-12s %-7s exact rel err %.1e, Dijkstra rel err %.3e"
                  % (row["triangulation"], row["grid"], row["rel_error"],
                     row["dijkstra_rel_error"]))


def test_path_matches_distance():
    print("\n[numeric] the returned polyline's length equals the distance")
    if not HAVE_BACKEND:
        skip("path consistency", backends.unavailable_reason())
        return
    suite = selftest.run_path_consistency()
    for row in suite["rows"]:
        check(
            "%s: |path| vs distance rel err %.3e <= %.0e"
            % (row["mesh"], row["rel_error"], selftest.PATH_CONSISTENCY_REL_TOL),
            row["pass"],
            "distance=%.9f path=%.9f" % (row["distance"], row["path_length"]),
        )
        check("%s: path endpoints sit on the requested vertices" % row["mesh"],
              row["endpoint_error_mm"] < 1e-6,
              "endpoint error %.3e mm" % row["endpoint_error_mm"])


def test_path_orientation_is_source_to_target():
    print("\n[numeric] the polyline is ordered source -> target")
    if not HAVE_BACKEND:
        skip("path orientation", backends.unavailable_reason())
        return
    vertices, faces = selftest.plane_grid(10, 10, 4.0)
    source = selftest.plane_index(10, 0, 0)
    target = selftest.plane_index(10, 10, 10)
    _, path = exact.compute_distance_and_path(vertices, faces, source, target)
    check("path[0] is the source",
          float(np.linalg.norm(path[0] - vertices[source])) < 1e-9)
    check("path[-1] is the target",
          float(np.linalg.norm(path[-1] - vertices[target])) < 1e-9)
    _, reverse = exact.compute_distance_and_path(vertices, faces, target, source)
    check("reversing the query reverses the polyline",
          float(np.linalg.norm(reverse[0] - vertices[target])) < 1e-9)


def test_symmetry_and_self_distance():
    print("\n[numeric] A->B == B->A, and A->A == 0 exactly")
    if not HAVE_BACKEND:
        skip("symmetry", backends.unavailable_reason())
        return
    vertices, faces = selftest.icosphere(100.0, 3)
    solver = exact.ExactSolver(vertices, faces)
    source = int(np.argmax(vertices[:, 2]))
    cosines = vertices @ vertices[source] / 1e4
    target = int(np.argmin(np.abs(cosines)))
    forward = solver.distance(source, target)
    backward = solver.distance(target, source)
    check("A->B equals B->A within 1e-9 relative",
          abs(forward - backward) / forward < 1e-9,
          "%.12f vs %.12f" % (forward, backward))
    check("A->A is exactly 0.0", solver.distance(source, source) == 0.0)


def test_cylinder_converges():
    print("\n[numeric] cylinder: convergence to the unrolled reference")
    if not HAVE_BACKEND:
        skip("cylinder convergence", backends.unavailable_reason())
        return
    suite = selftest.run_cylinder()
    for row in suite["rows"]:
        print("        n_theta=%-4d tris=%-7d h=%7.3f mm  poly=%10.5f  "
              "smooth=%10.5f  abs err=%8.5f mm  rel err=%7.4f%%  order=%s"
              % (row["n_theta"], row["triangles"], row["h_mm"],
                 row["polyhedral_mm"], row["smooth_reference_mm"],
                 row["abs_error_mm"], row["rel_error_percent"],
                 "n/a" if row["observed_order"] != row["observed_order"]
                 else "%.2f" % row["observed_order"]))
    errors = [row["rel_error"] for row in suite["rows"]]
    check("cylinder error decreases monotonically under refinement",
          all(b < a for a, b in zip(errors, errors[1:])),
          "errors: %s" % ["%.3e" % e for e in errors])
    check("the inscribed polyhedron underestimates the smooth cylinder",
          all(row["signed_error_mm"] <= 1e-9 for row in suite["rows"]),
          "chords cut corners, so the polyhedral geodesic must be shorter")
    check("finest cylinder is within 0.5%% of the smooth reference",
          errors[-1] < 5e-3, "finest relative error %.3e" % errors[-1])


def test_sphere_converges():
    print("\n[numeric] sphere: convergence to the great-circle reference")
    if not HAVE_BACKEND:
        skip("sphere convergence", backends.unavailable_reason())
        return
    suite = selftest.run_sphere()
    for row in suite["rows"]:
        print("        subdiv=%d tris=%-7d h=%7.3f mm  poly=%10.5f  "
              "smooth=%10.5f  abs err=%8.5f mm  rel err=%7.4f%%  order=%s"
              % (row["subdivisions"], row["triangles"], row["h_mm"],
                 row["polyhedral_mm"], row["smooth_reference_mm"],
                 row["abs_error_mm"], row["rel_error_percent"],
                 "n/a" if row["observed_order"] != row["observed_order"]
                 else "%.2f" % row["observed_order"]))
    errors = [row["rel_error"] for row in suite["rows"]]
    check("sphere error decreases monotonically under refinement",
          all(b < a for a, b in zip(errors, errors[1:])),
          "errors: %s" % ["%.3e" % e for e in errors])
    check("the inscribed icosphere underestimates the smooth sphere",
          all(row["signed_error_mm"] <= 1e-9 for row in suite["rows"]))
    orders = [row["observed_order"] for row in suite["rows"][1:]]
    finite = [o for o in orders if o == o]
    check("observed convergence order is measured, and is above 1",
          bool(finite) and min(finite) > 1.0,
          "orders: %s" % ["%.2f" % o for o in finite])


def test_failure_behaviour_suite():
    print("\n[numeric] every failure mode raises rather than crashing")
    if not HAVE_BACKEND:
        skip("failure behaviour suite", backends.unavailable_reason())
        return
    suite = selftest.run_failure_behaviour()
    for row in suite["rows"]:
        check("%s -> %s" % (row["case"], row["raised"]), row["pass"],
              row["message"][:120])


def test_repeated_queries_are_stable():
    print("\n[numeric] a reused solver gives bit-identical repeat answers")
    if not HAVE_BACKEND:
        skip("solver reuse", backends.unavailable_reason())
        return
    vertices, faces = selftest.icosphere(100.0, 3)
    solver = exact.ExactSolver(vertices, faces)
    source = int(np.argmax(vertices[:, 2]))
    target = int(np.argmin(vertices[:, 2]))
    first = solver.distance(source, target)
    check("repeat queries return the identical value",
          all(solver.distance(source, target) == first for _ in range(3)),
          "first = %.15f" % first)


def test_small_benchmark_runs():
    print("\n[numeric] the benchmark harness runs and reports real timings")
    if not HAVE_BACKEND:
        skip("benchmark harness", backends.unavailable_reason())
        return
    suite = selftest.run_benchmark(target_triangles=20000, repeats=2)
    row = suite["rows"][0]
    print("        tris=%d construction=%.3f s query=%.3f s path_points=%d"
          % (row["triangles"], row["solver_construction_s"],
             row["first_query_s"], row["path_points"]))
    check("benchmark reports a positive construction time",
          row["solver_construction_s"] > 0.0)
    check("benchmark reports a positive query time", row["first_query_s"] > 0.0)
    check("benchmark returns a path", row["path_points"] >= 2)
    check("benchmark chose a mesh near the requested size",
          abs(row["triangles"] - 20000) / 20000.0 < 0.5,
          "got %d triangles" % row["triangles"])


def main():
    print("BSMT Milestone 2.2 - offline backend tests")
    print("  python     : %s" % sys.version.split()[0])
    print("  numpy      : %s" % np.__version__)
    print("  pygeodesic : %s"
          % ("%s at %s" % (backends.backend_version(),
                           backends.status()["module_path"])
             if HAVE_BACKEND else "NOT AVAILABLE - %s"
             % backends.unavailable_reason()))

    for test in (
        test_package_imports_without_backend,
        test_import_error_is_preserved_not_swallowed,
        test_unavailable_backend_raises_never_substitutes,
        test_mesh_validation_is_backend_independent,
        test_generators_are_valid_meshes,
        test_analytic_references,
        test_environment_report_never_raises,
        test_selftest_reports_unavailable_backend_cleanly,
        test_plane_is_exact,
        test_path_matches_distance,
        test_path_orientation_is_source_to_target,
        test_symmetry_and_self_distance,
        test_cylinder_converges,
        test_sphere_converges,
        test_failure_behaviour_suite,
        test_repeated_queries_are_stable,
        test_small_benchmark_runs,
    ):
        test()

    print("\n%d checks, %d failure(s), %d skipped group(s)"
          % (CHECKS[0], len(FAILURES), len(SKIPS)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    if SKIPS and not HAVE_BACKEND:
        print("  NOTE: the numerical suites were skipped because pygeodesic is")
        print("        not installed for this interpreter. That is not a")
        print("        failure of BSMT; it is the condition Milestone 2.2")
        print("        requires BSMT to survive. Run the in-Blender panel or")
        print("        tools/check_geodesic_env.py to close the milestone.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
