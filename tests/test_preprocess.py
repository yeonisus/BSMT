"""Offline tests for Milestone 3.3: scan preprocessing and the safety gate.

    python3 tests/test_preprocess.py

Covers the pure decisions - ratio arithmetic, texture verification, the
refusal rules - which is where correctness lives. The Blender work
(duplicating, decimating, baking the modifier) is exercised by the in-Blender
acceptance script. Nothing here imports bpy.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preprocess = _load("bsmt_preprocess", os.path.join(PACKAGE, "preprocess.py"))


def raises(label, exception, call):
    try:
        call()
    except exception:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check(label, False, "did not raise")


def report(**kwargs):
    base = {
        "vertex_count": 157045, "triangle_count": 314086,
        "component_count": 1, "boundary_edge_count": 0,
        "nonmanifold_edge_count": 0, "degenerate_triangle_count": 0,
        "duplicate_vertex_count": 0,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# ratio arithmetic
# ---------------------------------------------------------------------------

def test_target_count_to_ratio():
    print("\n[ratio] target triangle count -> collapse ratio")
    # The real scan: 2,783,068 triangles down to 350,000.
    ratio = preprocess.decimation_ratio(350000, 2783068)
    check("real scan ratio", abs(ratio - 350000.0 / 2783068.0) < 1e-12,
          "%.8f" % ratio)
    check("ratio is in (0, 1]", 0.0 < ratio <= 1.0)
    check("half", abs(preprocess.decimation_ratio(500, 1000) - 0.5) < 1e-12)

    check("target == current gives 1.0",
          preprocess.decimation_ratio(1000, 1000) == 1.0)
    check("target above current clamps to 1.0",
          preprocess.decimation_ratio(5000, 1000) == 1.0)
    check("a huge target still clamps",
          preprocess.decimation_ratio(10 ** 9, 1000) == 1.0)

    raises("zero current triangles is refused", preprocess.PreprocessError,
           lambda: preprocess.decimation_ratio(100, 0))
    raises("negative current is refused", preprocess.PreprocessError,
           lambda: preprocess.decimation_ratio(100, -5))
    raises("zero target is refused", preprocess.PreprocessError,
           lambda: preprocess.decimation_ratio(0, 1000))
    raises("negative target is refused", preprocess.PreprocessError,
           lambda: preprocess.decimation_ratio(-1, 1000))
    raises("a non-numeric target is refused", preprocess.PreprocessError,
           lambda: preprocess.decimation_ratio("lots", 1000))


def test_plan_never_reduces_when_not_asked():
    print("\n[ratio] target >= current means no destructive reduction")
    step = preprocess.plan(350000, 2783068)
    check("a real reduction decimates",
          step["method"] == preprocess.METHOD_DECIMATE)
    check("expected count is the target", step["expected_triangles"] == 350000)
    check("reduction percentage is reported",
          abs(step["reduction_percent"] - 87.42) < 0.1,
          "%.2f" % step["reduction_percent"])
    check("the summary names both counts",
          "2783068" in step["summary"] and "350000" in step["summary"],
          step["summary"])

    for target in (314086, 400000, 10 ** 7):
        step = preprocess.plan(target, 314086)
        check("target %d >= current copies without decimating" % target,
              step["method"] == preprocess.METHOD_COPY, step["method"])
        check("  ratio is exactly 1.0", step["ratio"] == 1.0)
        check("  no triangles are expected to be lost",
              step["expected_triangles"] == 314086)
        check("  reduction is zero", step["reduction_percent"] == 0.0)


def test_presets():
    print("\n[ratio] quality presets")
    targets = preprocess.PRESET_TARGETS
    check("High is 500k", targets["HIGH"] == 500000)
    check("Standard is 350k", targets["STANDARD"] == 350000)
    check("Light is 200k", targets["LIGHT"] == 200000)
    check("Custom carries no forced target", targets["CUSTOM"] == 0)
    check("the default target is the Standard preset",
          preprocess.DEFAULT_TARGET_TRIANGLES == 350000)
    check("every preset has an identifier, label and description",
          all(len(entry) == 4 for entry in preprocess.PRESETS))


def test_accuracy_note():
    print("\n[ratio] collapse decimation is approximate, and says so")
    note, within = preprocess.accuracy_note(347912, 350000)
    check("a near miss is within tolerance", within, note)
    check("the note gives both numbers",
          "347912" in note.replace(",", "") and "350000" in note, note)
    _note, within = preprocess.accuracy_note(200000, 350000)
    check("a large miss is flagged", not within)


# ---------------------------------------------------------------------------
# texture / UV preservation
# ---------------------------------------------------------------------------

def facts(uv=("UVMap",), materials=("ScanMaterial",),
          images=("scan.jpg",), paths=("/scans/scan.jpg",)):
    return preprocess.texture_facts(uv, materials, images, paths)


def test_texture_preserved():
    print("\n[texture] a copy that kept everything passes")
    ok, problems, notes = preprocess.compare_texture(facts(), facts())
    check("identical facts pass", ok, str(problems))
    check("no problems", problems == [])
    check("the UV layer is reported as preserved",
          any("UV layer(s) preserved" in note for note in notes), str(notes))
    check("the material is reported as preserved",
          any("material(s) preserved" in note for note in notes))
    check("the image is reported as preserved",
          any("image texture(s) preserved" in note for note in notes))


def test_texture_loss_is_a_failure():
    print("\n[texture] losing UV, material or image FAILS preprocessing")
    ok, problems, _notes = preprocess.compare_texture(
        facts(), facts(uv=()))
    check("a lost UV layer fails", not ok)
    check("and names the layer", "UVMap" in problems[0], problems[0])

    ok, problems, _notes = preprocess.compare_texture(
        facts(), facts(materials=()))
    check("a lost material fails", not ok)
    check("and names the material", "ScanMaterial" in problems[0])

    ok, problems, _notes = preprocess.compare_texture(
        facts(), facts(images=()))
    check("a dropped image reference fails", not ok)
    check("and names the image", "scan.jpg" in problems[0])

    ok, problems, _notes = preprocess.compare_texture(
        facts(), facts(uv=(), materials=(), images=()))
    check("losing everything reports every problem", len(problems) == 3,
          str(problems))

    # An extra UV layer on the copy is not a loss.
    ok, _problems, _notes = preprocess.compare_texture(
        facts(), facts(uv=("UVMap", "Extra")))
    check("an extra UV layer is not a failure", ok)


def test_texture_absent_in_source():
    print("\n[texture] an untextured source is not a failure")
    empty = preprocess.texture_facts((), (), (), ())
    ok, problems, notes = preprocess.compare_texture(empty, empty)
    check("no UV in, no UV expected out", ok, str(problems))
    check("and it says so",
          any("no UV layer" in note for note in notes), str(notes))
    check("and notes there was no image",
          any("no image texture" in note for note in notes))


# ---------------------------------------------------------------------------
# the solver safety gate
# ---------------------------------------------------------------------------

def test_gate_refuses_non_manifold():
    print("\n[gate] non-manifold topology is refused unconditionally")
    clean = preprocess.preflight(report())
    check("a clean mesh is allowed", clean["allowed"], clean["message"])
    check("and raises no warnings", clean["warnings"] == [], str(clean))

    # The real dense OBJ: 7 non-manifold edges. This is the configuration
    # that crashed Blender with SIGSEGV.
    bad = preprocess.preflight(report(nonmanifold_edge_count=7))
    check("non-manifold is refused", not bad["allowed"])
    check("the count is in the message", "7 non-manifold" in bad["refusals"][0],
          bad["refusals"][0])
    check("the message says what to do",
          "Scan Preprocessing" in bad["refusals"][0])
    check("it is a refusal, not a warning", bad["refusals"] and bad["message"])

    # It cannot be overridden by the density switch: that guard is a
    # different question entirely.
    still = preprocess.preflight(report(nonmanifold_edge_count=1),
                                 guard_dense=False)
    check("unguarding density does not permit non-manifold",
          not still["allowed"])


def test_gate_density_threshold():
    print("\n[gate] density is an operational threshold, not a limit")
    dense = report(triangle_count=2783068)
    guarded = preprocess.preflight(dense, dense_threshold=1000000,
                                   guard_dense=True)
    check("a dense mesh is refused while guarded", not guarded["allowed"])
    check("the message gives both figures",
          "2,783,068" in guarded["refusals"][0]
          and "1,000,000" in guarded["refusals"][0], guarded["refusals"][0])
    check("and offers the override",
          "untick" in guarded["refusals"][0].lower(), guarded["refusals"][0])

    unguarded = preprocess.preflight(dense, dense_threshold=1000000,
                                     guard_dense=False)
    check("unguarded it warns instead of refusing", unguarded["allowed"])
    check("the warning is the brief's wording",
          "may be slow or unstable" in unguarded["warnings"][0],
          unguarded["warnings"][0])
    check("and suggests a measurement copy",
          "measurement copy" in unguarded["warnings"][0])

    # The validated 314k scan must pass cleanly at the default threshold.
    ok = preprocess.preflight(report(triangle_count=314086))
    check("the validated 314k scan passes", ok["allowed"] and not ok["warnings"])

    # Exactly at the threshold is not over it.
    edge = preprocess.preflight(report(triangle_count=1000000),
                                dense_threshold=1000000)
    check("exactly at the threshold is allowed", edge["allowed"])
    over = preprocess.preflight(report(triangle_count=1000001),
                                dense_threshold=1000000)
    check("one triangle over is not", not over["allowed"])


def test_gate_warns_without_refusing():
    print("\n[gate] components, boundaries and degeneracy warn only")
    # The real dense OBJ also has 2 components and 15 boundary edges.
    result = preprocess.preflight(
        report(component_count=2, boundary_edge_count=15)
    )
    check("multiple components do not refuse", result["allowed"])
    check("but they warn",
          any("connected components" in w for w in result["warnings"]),
          str(result["warnings"]))
    check("the warning says measurement is still allowed",
          any("still allowed" in w for w in result["warnings"]))
    check("boundary edges warn",
          any("boundary edge" in w for w in result["warnings"]))
    check("the boundary warning explains the risk",
          any("artefact" in w for w in result["warnings"]))

    degenerate = preprocess.preflight(report(degenerate_triangle_count=3))
    check("degenerate triangles warn only", degenerate["allowed"])
    check("and are counted",
          any("3 degenerate" in w for w in degenerate["warnings"]))

    check("a missing key is treated as zero, not as an error",
          preprocess.preflight({})["allowed"])


# ---------------------------------------------------------------------------
# reporting and honesty
# ---------------------------------------------------------------------------

def test_representation_is_honest():
    print("\n[report] the copy is a representation, not the same surface")
    check("the wording is 'decimated measurement representation'",
          preprocess.REPRESENTATION == "decimated measurement representation")
    check("it never claims the same exact surface",
          "same exact surface" not in preprocess.REPRESENTATION)
    record = {
        "source_name": "21_M_3400E", "source_mesh_name": "Mesh",
        "original_triangles": 2783068, "target_triangles": 350000,
        "actual_triangles": 347912, "method": preprocess.METHOD_DECIMATE,
        "ratio": 0.1258, "bsmt_version": "0.11.0",
        "created": "2026-09-02 10:00:00",
    }
    lines = preprocess.provenance_lines(record)
    text = "\n".join(lines)
    for needed in ("21_M_3400E", "2,783,068", "350,000", "347,912",
                   "DECIMATE_COLLAPSE", "0.11.0", "2026-09-02"):
        check("provenance records %r" % needed, needed in text)
    check("provenance states the representation",
          preprocess.REPRESENTATION in text)


def test_comparison_table():
    print("\n[report] before / after comparison")
    before = report(vertex_count=1391542, triangle_count=2783068,
                    component_count=2, boundary_edge_count=15,
                    nonmanifold_edge_count=7)
    after = report(vertex_count=174000, triangle_count=347912)
    lines = preprocess.format_comparison(before, after)
    text = "\n".join(lines)
    check("both labels appear",
          "Original" in text and "Measurement Copy" in text)
    for needed in ("1,391,542", "2,783,068", "347,912", "174,000"):
        check("the table shows %r" % needed, needed in text)
    check("every diagnostic row is present",
          all(label in text for label, _key in preprocess._COMPARISON_ROWS))
    check("non-manifold edges are shown for both", "Non-manifold edges" in text)


def test_no_welding_or_hole_filling_anywhere():
    print("\n[scope] nothing welds or fills holes")
    for name in ("preprocess.py", "scancopy.py"):
        source = open(os.path.join(PACKAGE, name)).read().lower()
        for forbidden in ("remove_doubles", "merge_by_distance",
                          "fill_holes", "bridge_edge_loops", "mesh.fill"):
            check("%s does not call %s" % (name, forbidden),
                  forbidden not in source)
    text = open(os.path.join(PACKAGE, "preprocess.py")).read()
    check("the reason is documented", "fuse anatomically distinct" in text)
    check("and the consequence is named",
          "systematically SHORT" in text or "systematically short" in text)


def main():
    print("BSMT Milestone 3.3 - preprocessing and safety gate tests")
    print("  python : %s" % sys.version.split()[0])
    for test in (
        test_target_count_to_ratio,
        test_plan_never_reduces_when_not_asked,
        test_presets,
        test_accuracy_note,
        test_texture_preserved,
        test_texture_loss_is_a_failure,
        test_texture_absent_in_source,
        test_gate_refuses_non_manifold,
        test_gate_density_threshold,
        test_gate_warns_without_refusing,
        test_representation_is_honest,
        test_comparison_table,
        test_no_welding_or_hole_filling_anywhere,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
