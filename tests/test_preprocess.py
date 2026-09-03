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
          "2,783,068" in step["summary"] and "350,000" in step["summary"],
          step["summary"])
    check("and reads as a sentence", step["summary"].endswith("."),
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
          "347912" in note.replace(",", "")
          and "350000" in note.replace(",", ""), note)
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
    check("the message names the panel that fixes it",
          "Mesh Repair" in bad["refusals"][0], bad["refusals"][0])
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
    check("and suggests a measurement mesh",
          "measurement mesh" in unguarded["warnings"][0],
          unguarded["warnings"][0])

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

    # Degenerate triangles used to warn here while classify_ready called them
    # blocking - the sidebar said NOT READY and the solver ran on that very
    # mesh. They REFUSE now, from the same list (Milestone 3.18).
    degenerate = preprocess.preflight(report(degenerate_triangle_count=3))
    check("degenerate triangles now REFUSE the solve",
          not degenerate["allowed"], str(degenerate))
    check("and the refusal counts them",
          any("3 degenerate" in line for line in degenerate["refusals"]),
          str(degenerate["refusals"]))
    check("they are not also listed as a warning",
          not any("degenerate" in line for line in degenerate["warnings"]),
          str(degenerate["warnings"]))

    check("a missing key does not raise",
          isinstance(preprocess.preflight({}), dict))
    check("but an empty report is refused, not waved through",
          not preprocess.preflight({})["allowed"],
          "an unknown mesh must not reach a native solver")


# ---------------------------------------------------------------------------
# reporting and honesty
# ---------------------------------------------------------------------------

def test_representation_is_honest():
    print("\n[report] the copy is a representation, not the same surface")
    check("the wording says it is a decimated copy",
          "Decimated copy" in preprocess.REPRESENTATION,
          preprocess.REPRESENTATION)
    check("and says what it is for",
          "measurement" in preprocess.REPRESENTATION.lower(),
          preprocess.REPRESENTATION)
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
          "Original" in text and "Measurement Mesh" in text, text[:120])
    for needed in ("1,391,542", "2,783,068", "347,912", "174,000"):
        check("the table shows %r" % needed, needed in text)
    check("every diagnostic row is present",
          all(label in text for label, _key in preprocess._COMPARISON_ROWS))
    check("non-manifold edges are shown for both", "Non-manifold edges" in text)


# ---------------------------------------------------------------------------
# colour attributes (Milestone 3.15) - a PLY scan's whole appearance
# ---------------------------------------------------------------------------

def colored(colors=(("Col", "POINT", "BYTE_COLOR"),), active="Col",
            uv=(), materials=(), images=(), paths=(), used=()):
    """A PLY-shaped source: point colours, no UV, no material, no image."""
    return preprocess.texture_facts(uv, materials, images, paths,
                                    color_attributes=colors,
                                    active_color=active, used_materials=used)


def test_color_attribute_preserved():
    print("\n[color] a colour attribute that survives passes")
    ok, problems, notes = preprocess.compare_texture(colored(), colored())
    check("identical colour facts pass", ok, str(problems))
    check("and the colour is reported as preserved",
          any("color attribute(s) preserved" in note for note in notes),
          str(notes))
    check("a PLY-shaped source is not reported as untextured-and-fine only",
          preprocess.has_appearance_data(colored()))
    check("names are readable back out",
          preprocess.color_attribute_names(colored()) == ["Col"])


def test_color_attribute_loss_is_a_failure():
    print("\n[color] losing the colour attribute FAILS preprocessing")
    ok, problems, _notes = preprocess.compare_texture(
        colored(), colored(colors=(), active=""))
    check("a lost colour attribute fails", not ok)
    check("and names it", "Col" in problems[0], problems[0])

    # The case that matters: a PLY scan has nothing BUT colour, so the old
    # UV/material/image comparison would have passed it happily.
    before = colored()
    after = colored(colors=())
    ok, _problems, _notes = preprocess.compare_texture(before, after)
    check("a colour-only source cannot lose its colour silently", not ok)


def test_color_attribute_changes_are_notes_not_failures():
    print("\n[color] a changed domain or active colour is a note, not a loss")
    ok, problems, notes = preprocess.compare_texture(
        colored(), colored(colors=(("Col", "CORNER", "BYTE_COLOR"),)))
    check("a changed domain is not a failure", ok, str(problems))
    check("but it is reported",
          any("changed from POINT" in note for note in notes), str(notes))

    ok, _problems, notes = preprocess.compare_texture(
        colored(colors=(("Col", "POINT", "BYTE_COLOR"),
                        ("Col2", "POINT", "BYTE_COLOR")), active="Col"),
        colored(colors=(("Col", "POINT", "BYTE_COLOR"),
                        ("Col2", "POINT", "BYTE_COLOR")), active="Col2"))
    check("a changed active colour is not a failure", ok)
    check("but it is reported",
          any("active color attribute changed" in note for note in notes),
          str(notes))


def test_unused_material_slot_is_a_note():
    print("\n[color] a slot no face uses any more is reported, not failed")
    before = preprocess.texture_facts(("UVMap",), ("Body", "Patch"), (), (),
                                      used_materials=("Body", "Patch"))
    after = preprocess.texture_facts(("UVMap",), ("Body", "Patch"), (), (),
                                     used_materials=("Body",))
    ok, problems, notes = preprocess.compare_texture(before, after)
    check("the slot surviving unused is not a failure", ok, str(problems))
    check("but it is named",
          any("no longer used by any face" in note and "Patch" in note
              for note in notes), str(notes))


def test_geometry_only_source():
    print("\n[color] a source with no appearance data at all is honest")
    bare = preprocess.texture_facts((), (), (), ())
    check("has_appearance_data is False",
          not preprocess.has_appearance_data(bare))
    ok, _problems, notes = preprocess.compare_texture(bare, bare)
    check("and it is not a failure", ok)
    check("and the copy is described as geometry only",
          any("geometry only" in note for note in notes), str(notes))


# ---------------------------------------------------------------------------
# measurement-ready classification (Milestone 3.15)
# ---------------------------------------------------------------------------

def test_ready_classification():
    print("\n[ready] a clean copy is MEASUREMENT READY")
    state, reasons = preprocess.classify_ready(report())
    check("clean is READY", state == preprocess.MEASUREMENT_READY, state)
    check("with no reasons", reasons == [], str(reasons))
    check("and a human label exists",
          preprocess.READY_LABELS[state] == "MEASUREMENT READY")


def test_ready_not_ready_rules():
    print("\n[ready] the blocking conditions")
    state, reasons = preprocess.classify_ready(report(nonmanifold_edge_count=4))
    check("non-manifold is NOT READY",
          state == preprocess.MEASUREMENT_NOT_READY, state)
    check("and says why", "non-manifold" in reasons[0], reasons[0])

    state, reasons = preprocess.classify_ready(
        report(degenerate_triangle_count=3))
    check("degenerate triangles are NOT READY",
          state == preprocess.MEASUREMENT_NOT_READY, state)

    state, reasons = preprocess.classify_ready(report(), canonical_built=False)
    check("a mesh whose canonical form will not build is NOT READY",
          state == preprocess.MEASUREMENT_NOT_READY, state)
    check("and that is the only reason given", len(reasons) == 1, str(reasons))

    state, reasons = preprocess.classify_ready(
        report(), appearance_ok=False,
        appearance_problems=["UV layer(s) lost: UVMap"])
    check("lost appearance data is NOT READY",
          state == preprocess.MEASUREMENT_NOT_READY, state)
    check("and the appearance problem is carried through",
          "UVMap" in reasons[0], reasons[0])

    state, _reasons = preprocess.classify_ready(report(triangle_count=0))
    check("a mesh with no triangles is NOT READY",
          state == preprocess.MEASUREMENT_NOT_READY, state)


def test_ready_warning_rules():
    print("\n[ready] the non-blocking conditions")
    state, reasons = preprocess.classify_ready(report(component_count=3))
    check("several components is a WARNING, never a refusal",
          state == preprocess.MEASUREMENT_WARNING, state)
    check("and it explains the per-pair rule",
          "refused individually" in reasons[0], reasons[0])

    state, reasons = preprocess.classify_ready(report(boundary_edge_count=120))
    check("boundary edges are a WARNING",
          state == preprocess.MEASUREMENT_WARNING, state)

    state, reasons = preprocess.classify_ready(
        report(triangle_count=2000000), dense_threshold=1000000)
    check("staying above the density threshold is a WARNING",
          state == preprocess.MEASUREMENT_WARNING, state)
    check("and it is named operational, not mathematical",
          "operational" in reasons[0], reasons[0])


def test_components_never_block_readiness():
    print("\n[ready] sect. 6: components == 1 is NOT required")
    for count in (2, 5, 50):
        state, _reasons = preprocess.classify_ready(
            report(component_count=count))
        check("%d components is not NOT_READY" % count,
              state != preprocess.MEASUREMENT_NOT_READY, state)
    text = open(os.path.join(PACKAGE, "preprocess.py")).read()
    check("and the reason is documented",
          "Deliberately NOT a rule" in text)
    check("naming where connectivity IS enforced",
          "property of a landmark PAIR" in text)


def test_ready_lines():
    print("\n[ready] the verdict renders")
    state, reasons = preprocess.classify_ready(
        report(component_count=2, boundary_edge_count=8))
    lines = preprocess.ready_lines(state, reasons)
    check("the first line is the verdict", lines[0] == "Status: WARNING",
          lines[0])
    check("every reason is listed", len(lines) == 1 + len(reasons), str(lines))
    trimmed = preprocess.ready_lines(state, ["a", "b", "c", "d", "e"], limit=2)
    check("and a long list is trimmed with a count",
          trimmed[-1] == "  - and 3 more", str(trimmed))


# ---------------------------------------------------------------------------
# Milestone 3.18 - one policy for readiness and for the solver gate
# ---------------------------------------------------------------------------

def _both(report_dict):
    """(classify_ready state, preflight allowed) for one topology report."""
    verdict, _reasons = preprocess.classify_ready(report_dict)
    return verdict, preprocess.preflight(report_dict)["allowed"]


def test_A_degenerate_blocks_everywhere():
    print("\n[policy A] degenerate triangles block the UI AND the solver")
    defective = report(component_count=1, boundary_edge_count=0,
                       nonmanifold_edge_count=0, degenerate_triangle_count=4)
    verdict, allowed = _both(defective)
    check("classify_ready says NOT READY",
          verdict == preprocess.MEASUREMENT_NOT_READY, verdict)
    check("and the solver gate refuses", not allowed)

    gate = preprocess.preflight(defective)
    check("the refusal names the measurement mesh",
          any("measurement mesh" in line for line in gate["refusals"]),
          gate["refusals"])
    check("it counts the triangles",
          any("4 degenerate" in line for line in gate["refusals"]),
          gate["refusals"])
    check("it points at Mesh Repair",
          any("Mesh Repair" in line for line in gate["refusals"]),
          gate["refusals"])
    check("and it says to re-analyze",
          any("re-analyze" in line for line in gate["refusals"]),
          gate["refusals"])
    check("the project vocabulary is used",
          not any("measurement copy" in line.lower()
                  for line in gate["refusals"]))
    check("the defect is reported by code too",
          preprocess.DEFECT_DEGENERATE in gate["blocking_codes"],
          gate["blocking_codes"])


def test_B_coincident_vertices_stay_warning_only():
    print("\n[policy B] coincident vertices alone refuse nothing")
    for field, count in (("duplicate_vertex_count", 14),
                         ("near_coincident_count", 320),
                         ("duplicate_group_count", 7),
                         ("near_coincident_group_count", 3)):
        coincident = report(degenerate_triangle_count=0, **{field: count})
        verdict, allowed = _both(coincident)
        check("%s=%d does not block the UI" % (field, count),
              verdict == preprocess.MEASUREMENT_READY, verdict)
        check("  nor the solver", allowed)

    both = report(degenerate_triangle_count=0, duplicate_vertex_count=14,
                  near_coincident_count=320)
    gate = preprocess.preflight(both)
    check("no refusal mentions coincidence",
          not any("coincident" in line.lower() for line in gate["refusals"]),
          gate["refusals"])
    check("and neither does any warning",
          not any("coincident" in line.lower() for line in gate["warnings"]),
          gate["warnings"])
    check("the shared defect list ignores them entirely",
          preprocess.blocking_defects(both) == [],
          preprocess.blocking_defects(both))

    # They matter only through the degeneracy they can cause.
    verdict, allowed = _both(report(duplicate_vertex_count=14,
                                    degenerate_triangle_count=2))
    check("coincidence that HAS produced degeneracy blocks",
          verdict == preprocess.MEASUREMENT_NOT_READY and not allowed)


def test_C_boundary_edges_warn_and_allow():
    print("\n[policy C] a hole is a warning, not a refusal")
    holed = report(boundary_edge_count=120, nonmanifold_edge_count=0,
                   degenerate_triangle_count=0)
    verdict, allowed = _both(holed)
    check("classify_ready says WARNING",
          verdict == preprocess.MEASUREMENT_WARNING, verdict)
    check("and the solve is still allowed", allowed)
    gate = preprocess.preflight(holed)
    check("the boundary is warned about",
          any("boundary edge" in line for line in gate["warnings"]),
          gate["warnings"])
    check("nothing is refused", gate["refusals"] == [], gate["refusals"])


def test_D_non_manifold_refusal_unchanged():
    print("\n[policy D] the existing non-manifold refusal is untouched")
    bad = report(nonmanifold_edge_count=9)
    verdict, allowed = _both(bad)
    check("classify_ready says NOT READY",
          verdict == preprocess.MEASUREMENT_NOT_READY, verdict)
    check("the solver refuses", not allowed)
    gate = preprocess.preflight(bad)
    check("with the wording it has always used",
          any(line == preprocess.REFUSE_NON_MANIFOLD % 9
              for line in gate["refusals"]), gate["refusals"])
    check("and the code is reported",
          preprocess.DEFECT_NON_MANIFOLD in gate["blocking_codes"])


def test_E_components_warn_and_allow():
    print("\n[policy E] several components warn; the PAIR rule does the work")
    split = report(component_count=4, degenerate_triangle_count=0)
    verdict, allowed = _both(split)
    check("classify_ready says WARNING",
          verdict == preprocess.MEASUREMENT_WARNING, verdict)
    check("and the mesh may still be solved on", allowed)
    gate = preprocess.preflight(split)
    check("the warning says measurement is still allowed",
          any("still allowed" in line for line in gate["warnings"]),
          gate["warnings"])
    check("and that a pair is refused individually",
          any("individually" in line for line in gate["warnings"]),
          gate["warnings"])
    # The per-pair rule itself lives in the solver and is asserted in
    # tests/test_surface_distance.py; the gate must not pre-empt it.
    check("no refusal is raised for the mesh as a whole",
          gate["refusals"] == [], gate["refusals"])


def test_one_policy_two_presentations():
    print("\n[policy] the two gates cannot disagree, by construction")
    cases = [
        {}, {"degenerate_triangle_count": 1}, {"nonmanifold_edge_count": 1},
        {"triangle_count": 0}, {"boundary_edge_count": 5},
        {"component_count": 9}, {"duplicate_vertex_count": 3},
        {"degenerate_triangle_count": 2, "nonmanifold_edge_count": 3},
    ]
    for extra in cases:
        current = report(**extra)
        verdict, allowed = _both(current)
        blocked = bool(preprocess.blocking_defects(current))
        check("%s: NOT READY iff refused" % (extra or "clean"),
              (verdict == preprocess.MEASUREMENT_NOT_READY) == blocked
              and allowed == (not blocked),
              (verdict, allowed, blocked))

    source = open(os.path.join(PACKAGE, "preprocess.py")).read()
    body = source[source.index("def preflight("):source.index("def _thousands")
                  if "def _thousands" in source[source.index("def preflight("):]
                  else len(source)]
    check("preflight derives its refusals from the shared list",
          "blocking_defects(report" in body, body[:200])


def test_dense_guard_is_separate_and_overridable():
    print("\n[policy] density stays a guarded refusal, not a defect")
    dense = report(triangle_count=2000000)
    check("guarded, it refuses",
          not preprocess.preflight(dense, guard_dense=True)["allowed"])
    check("unguarded, it warns and allows",
          preprocess.preflight(dense, guard_dense=False)["allowed"])
    check("density is NOT on the blocking-defect list",
          preprocess.blocking_defects(dense) == [],
          preprocess.blocking_defects(dense))
    check("so a dense mesh is only a WARNING to the UI",
          preprocess.classify_ready(dense)[0]
          == preprocess.MEASUREMENT_WARNING)


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
    print("BSMT preprocessing tests - Milestones 3.3 and 3.15")
    print("  python : %s" % sys.version.split()[0])
    for test in (
        test_target_count_to_ratio,
        test_plan_never_reduces_when_not_asked,
        test_presets,
        test_accuracy_note,
        test_texture_preserved,
        test_texture_loss_is_a_failure,
        test_texture_absent_in_source,
        test_color_attribute_preserved,
        test_color_attribute_loss_is_a_failure,
        test_color_attribute_changes_are_notes_not_failures,
        test_unused_material_slot_is_a_note,
        test_geometry_only_source,
        test_ready_classification,
        test_ready_not_ready_rules,
        test_ready_warning_rules,
        test_components_never_block_readiness,
        test_ready_lines,
        test_gate_refuses_non_manifold,
        test_gate_density_threshold,
        test_gate_warns_without_refusing,
        test_A_degenerate_blocks_everywhere,
        test_B_coincident_vertices_stay_warning_only,
        test_C_boundary_edges_warn_and_allow,
        test_D_non_manifold_refusal_unchanged,
        test_E_components_warn_and_allow,
        test_one_policy_two_presentations,
        test_dense_guard_is_separate_and_overridable,
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
