"""Offline tests for Milestone 3.28: component-scoped artifact removal policy.

    python3 tests/test_artifact.py

Everything that DECIDES lives in ``artifact.py`` and is pure numpy, so it is
testable without Blender: which component a defect sits in, whether that
component may be deleted at all, and whether a completed deletion is safe to
keep. The bmesh editing and the operator wiring are exercised by
tests/test_artifact_deletion_blender.py.

The two checks this suite exists for above all others:

  * the PRIMARY body component is refused, in every shape that question can
    be asked - it is the largest, it is the only one, or it ties with the
    largest and BSMT will not break the tie;
  * acceptance is the EXISTING rule plus additions, never a softer parallel
    rule - asserted by feeding the same states through both and requiring
    that `accept_deletion` never accepts what `repair.accept_repair` refused.
"""

import importlib.util
import os
import sys
import types

import numpy as np

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


def _load():
    """Import repair + artifact + topology without importing bpy."""
    package = types.ModuleType("bsmt_a")
    package.__path__ = [PACKAGE]
    sys.modules["bsmt_a"] = package
    geo = types.ModuleType("bsmt_a.geodesic")
    geo.__path__ = [os.path.join(PACKAGE, "geodesic")]
    sys.modules["bsmt_a.geodesic"] = geo
    modules = {}
    for name, path in (
        ("bsmt_a.geodesic.topology",
         os.path.join(PACKAGE, "geodesic", "topology.py")),
        ("bsmt_a.repair", os.path.join(PACKAGE, "repair.py")),
        ("bsmt_a.artifact", os.path.join(PACKAGE, "artifact.py")),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return (modules["bsmt_a.artifact"], modules["bsmt_a.repair"],
            modules["bsmt_a.geodesic.topology"])


artifact, repair, topology = _load()


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def grid(n, spacing=1.0, origin=(0.0, 0.0, 0.0)):
    """An n x n vertex grid of triangles. Manifold, one component."""
    xs, ys = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    vertices = np.stack([xs.ravel() * spacing + origin[0],
                         ys.ravel() * spacing + origin[1],
                         np.zeros(n * n) + origin[2]], axis=1)
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            b = a + 1
            c = a + n
            d = c + 1
            faces.append([a, c, b])
            faces.append([b, c, d])
    return vertices.astype(np.float64), np.asarray(faces, dtype=np.int64)


def fragment(origin=(100.0, 0.0, 0.0), size=10.0):
    """Three triangles sharing one edge: a detached lump, one non-manifold edge."""
    ox, oy, oz = origin
    vertices = np.array([
        [ox, oy, oz],
        [ox + size, oy, oz],
        [ox + size * 0.5, oy + size * 0.8, oz],
        [ox + size * 0.5, oy - size * 0.8, oz],
        [ox + size * 0.5, oy, oz + size * 0.8],
    ], dtype=np.float64)
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 1, 4]], dtype=np.int64)
    return vertices, faces


def join(*pieces):
    vertices = []
    faces = []
    offset = 0
    for piece_vertices, piece_faces in pieces:
        vertices.append(piece_vertices)
        faces.append(np.asarray(piece_faces, dtype=np.int64) + offset)
        offset += piece_vertices.shape[0]
    return np.vstack(vertices), np.vstack(faces)


def labelled(vertices, faces):
    triangle_labels, vertex_labels, _tc, _vc = topology.component_labels(
        vertices, faces)
    return triangle_labels, vertex_labels


def body_plus_fragment():
    """A 'body' grid and one detached three-triangle artifact.

    The grid is large enough that three triangles is under the 2% share that
    makes a component "small" - the fixture has to be able to tell the
    difference between "not the body" and "small", because they are different
    facts and only one of them blocks anything.
    """
    return join(grid(15), fragment())


def report(triangles, components, nonmanifold, boundary=0, degenerate=0,
           counts=None):
    return {
        "triangle_count": triangles,
        "component_count": components,
        "nonmanifold_edge_count": nonmanifold,
        "boundary_edge_count": boundary,
        "degenerate_triangle_count": degenerate,
        "component_triangle_counts": counts or [],
    }


# ---------------------------------------------------------------------------
# A. which component holds the defect
# ---------------------------------------------------------------------------

def test_defect_regions():
    print("\n[A] the defect, and the component it sits in")
    vertices, faces = body_plus_fragment()
    _tri_labels, vertex_labels = labelled(vertices, faces)
    regions = artifact.defect_regions(vertices, faces, vertex_labels)

    check("A1: exactly one defect is reported", len(regions) == 1,
          len(regions))
    region = regions[0]
    check("A2: it is the single non-manifold edge", region["edge_count"] == 1,
          region["edge_count"])
    check("A3: it names one connected component, not a set",
          region["component"] >= 0 and len(region["component_labels"]) == 1,
          region["component_labels"])

    # The fragment is the SMALLER component, so component_labels ranks it 1.
    check("A4: and it is the fragment, not the body", region["component"] == 1,
          region["component"])
    check("A5: the defect's centre is in the fragment's half of the world",
          region["center_mm"][0] > 50.0, region["center_mm"])
    check("A6: it has a description a panel can show",
          "non-manifold edge" in artifact.describe_defect(region),
          artifact.describe_defect(region))


def test_grouping_matches_the_full_region_description():
    print("\n[A] the cheap lookup and the full description number defects alike")
    # A defect with several non-manifold edges around one vertex, plus a
    # second, separate one - so the ORDER of the two actually matters.
    vertices, faces = grid(9)
    apex = vertices.shape[0]
    vertices = np.vstack([vertices, [[1.5, 1.5, 1.0], [5.5, 5.5, 1.0]]])
    # a fan: three extra faces on edges meeting at one vertex
    centre = 1 * 9 + 1
    extra = [[centre, centre + 1, apex],
             [centre, centre + 9, apex],
             [centre, centre + 10, apex]]
    other = 5 * 9 + 5
    extra.append([other, other + 1, apex + 1])
    faces = np.vstack([faces, np.asarray(extra, dtype=np.int64)])

    full = repair.non_manifold_regions(vertices, faces)
    _tri, vertex_labels = labelled(vertices, faces)
    cheap = artifact.defect_regions(vertices, faces, vertex_labels)

    check("A7: both find the same number of defects",
          len(full) == len(cheap), (len(full), len(cheap)))
    check("A8: with the same region ids in the same order",
          [entry["region_id"] for entry in full]
          == [entry["region_id"] for entry in cheap])
    check("A9: and the same edge counts per defect",
          [entry["edge_count"] for entry in full]
          == [entry["edge_count"] for entry in cheap],
          ([e["edge_count"] for e in full], [e["edge_count"] for e in cheap]))
    check("A10: the grouping rule has ONE implementation",
          "group_nonmanifold_edges" in open(
              os.path.join(PACKAGE, "artifact.py")).read()
          and "group_nonmanifold_edges" in open(
              os.path.join(PACKAGE, "repair.py")).read())


def test_components_of_vertices():
    print("\n[A] vertex -> component lookup refuses to guess")
    labels = np.array([0, 0, 1, 1, -1], dtype=np.int64)
    check("A11: one component when the vertices agree",
          artifact.components_of_vertices(labels, [2, 3]) == [1])
    check("A12: both when they do not - the caller decides, not this",
          artifact.components_of_vertices(labels, [0, 2]) == [0, 1])
    check("A13: unused vertices contribute no component",
          artifact.components_of_vertices(labels, [4]) == [])
    check("A14: out-of-range indices are ignored, not crashed on",
          artifact.components_of_vertices(labels, [99]) == [])


# ---------------------------------------------------------------------------
# B. what would be removed
# ---------------------------------------------------------------------------

def test_component_facts():
    print("\n[B] the facts the confirmation has to be able to state")
    vertices, faces = body_plus_fragment()
    triangle_labels, vertex_labels = labelled(vertices, faces)
    regions = artifact.defect_regions(vertices, faces, vertex_labels)
    region = regions[0]

    facts = artifact.component_facts(
        vertices, faces, vertex_labels, triangle_labels, region["component"],
        defect_vertex_indices=region["vertex_indices"])

    check("B1: the fragment's triangle count", facts["triangle_count"] == 3,
          facts["triangle_count"])
    check("B2: its vertex count", facts["vertex_count"] == 5,
          facts["vertex_count"])
    check("B3: its edge count - 3 triangles on a shared edge have 7 edges",
          facts["edge_count"] == 7, facts["edge_count"])
    check("B4: it carries the one non-manifold edge",
          facts["nonmanifold_edge_count"] == 1,
          facts["nonmanifold_edge_count"])
    check("B5: a bounding box with three real dimensions",
          all(value > 0.0 for value in facts["bbox_mm"]), facts["bbox_mm"])
    check("B6: it is NOT the largest component", not facts["is_largest"])
    check("B7: it is flagged small", facts["is_small"], facts["percent"])
    check("B8: and it holds the focused defect",
          facts["holds_focused_defect"])

    body = artifact.component_facts(vertices, faces, vertex_labels,
                                    triangle_labels, 0,
                                    defect_vertex_indices=region["vertex_indices"])
    check("B9: the body component is the largest", body["is_largest"])
    check("B10: the body holds every other triangle",
          body["triangle_count"] == int(faces.shape[0]) - 3,
          body["triangle_count"])
    check("B11: and does NOT hold the focused defect",
          not body["holds_focused_defect"])
    check("B12: the two components partition the mesh",
          body["triangle_count"] + facts["triangle_count"]
          == int(faces.shape[0]))


def test_confirmation_text():
    print("\n[B] the confirmation says the three things that matter")
    vertices, faces = body_plus_fragment()
    triangle_labels, vertex_labels = labelled(vertices, faces)
    facts = artifact.component_facts(vertices, faces, vertex_labels,
                                     triangle_labels, 1)
    text = "\n".join(artifact.confirmation_lines(facts, "Body_BSMT", 2, 3))

    check("B13: it says the whole component goes",
          "connected component" in text, text[:60])
    check("B14: it says the source scan is not modified",
          "source scan will not be modified" in text)
    check("B15: it states vertices, edges, triangles and the bounding box",
          all(word in text for word in ("Vertices:", "Edges:", "Triangles:",
                                        "Bounding box:")))
    check("B16: it says whether this is the main component",
          "Largest / main component:" in text)
    check("B17: it says whether the highlighted edge is in it",
          "Holds the highlighted non-manifold edge:" in text)
    check("B18: it warns that results go stale",
          "stale" in text and "landmark" in text.lower())
    check("B19: and it names the affected counts",
          "2 landmark(s) and 3 measurement(s)" in text, text)


def test_a_large_non_primary_component_is_called_out():
    print("\n[B] a big fragment is offered, but not quietly")
    vertices, faces = join(grid(9), grid(8, origin=(100.0, 0.0, 0.0)))
    triangle_labels, vertex_labels = labelled(vertices, faces)
    facts = artifact.component_facts(vertices, faces, vertex_labels,
                                     triangle_labels, 1)
    check("B20: the second grid is not the largest", not facts["is_largest"])
    check("B21: nor is it 'small'", not facts["is_small"], facts["percent"])
    allowed, _code, _reason = artifact.deletion_block(facts)
    check("B22: deletion is still permitted - size is not the rule", allowed)
    text = "\n".join(artifact.confirmation_lines(facts))
    check("B23: but the confirmation says it is not a small fragment",
          "not a small fragment" in text, text)
    check("B24: and tells the researcher to check it is not anatomy",
          "not anatomy" in text)


# ---------------------------------------------------------------------------
# C. the hard rule
# ---------------------------------------------------------------------------

def test_primary_component_is_refused():
    print("\n[C] the primary body component is never deleted")
    vertices, faces = body_plus_fragment()
    triangle_labels, vertex_labels = labelled(vertices, faces)

    body = artifact.component_facts(vertices, faces, vertex_labels,
                                    triangle_labels, 0)
    allowed, code, reason = artifact.deletion_block(body)
    check("C1: the largest component is refused", not allowed)
    check("C2: named as the primary block", code == artifact.BLOCK_PRIMARY,
          code)
    check("C3: with the message the researcher is meant to read",
          reason == artifact.PRIMARY_BLOCK_MESSAGE, reason)
    check("C4: which names the primary body component",
          "primary body component" in reason)
    check("C5: and points somewhere else instead of just saying no",
          "another repair method" in reason)

    fragment_facts = artifact.component_facts(vertices, faces, vertex_labels,
                                              triangle_labels, 1)
    allowed, code, reason = artifact.deletion_block(fragment_facts)
    check("C6: the detached fragment IS offered", allowed, reason)
    check("C7: with no block code and no reason", code == "" and reason == "")


def test_single_component_is_refused():
    print("\n[C] a one-component mesh has nothing that is not the body")
    vertices, faces = grid(9)
    triangle_labels, vertex_labels = labelled(vertices, faces)
    facts = artifact.component_facts(vertices, faces, vertex_labels,
                                     triangle_labels, 0)
    allowed, code, reason = artifact.deletion_block(facts)
    check("C8: refused", not allowed)
    check("C9: as the only component", code == artifact.BLOCK_ONLY_COMPONENT,
          code)
    check("C10: saying there would be nothing left to measure",
          "nothing to measure" in reason, reason)
    check("C11: and repeating the primary-component message",
          artifact.PRIMARY_BLOCK_MESSAGE in reason)


def test_a_tie_is_refused_not_broken():
    print("\n[C] two equal components: which is the body is not guessed")
    vertices, faces = join(grid(9), grid(9, origin=(100.0, 0.0, 0.0)))
    triangle_labels, vertex_labels = labelled(vertices, faces)
    first = artifact.component_facts(vertices, faces, vertex_labels,
                                     triangle_labels, 0)
    second = artifact.component_facts(vertices, faces, vertex_labels,
                                      triangle_labels, 1)
    check("C12: both hold the same number of triangles",
          first["triangle_count"] == second["triangle_count"],
          (first["triangle_count"], second["triangle_count"]))
    allowed_first, code_first, _r = artifact.deletion_block(first)
    allowed_second, code_second, reason = artifact.deletion_block(second)
    check("C13: neither may be deleted",
          not allowed_first and not allowed_second)
    check("C14: the one that is not ranked largest is refused as ambiguous",
          code_second == artifact.BLOCK_AMBIGUOUS, code_second)
    check("C15: and says BSMT will not guess", "will not guess" in reason,
          reason)
    check("C16: the ranked-largest one is refused as the primary",
          code_first == artifact.BLOCK_PRIMARY, code_first)


def test_no_defect_and_empty_component():
    print("\n[C] nothing focused, and a component that no longer exists")
    allowed, code, reason = artifact.deletion_block(None)
    check("C17: no focused defect is refused", not allowed)
    check("C18: named", code == artifact.BLOCK_NO_DEFECT, code)
    check("C19: and tells the researcher what to press",
          "Analyze" in reason and "Show Edges" in reason, reason)

    empty = {"triangle_count": 0, "component_count": 3,
             "largest_triangle_count": 10, "is_largest": False}
    allowed, code, reason = artifact.deletion_block(empty)
    check("C20: an empty component is refused", not allowed)
    check("C21: named", code == artifact.BLOCK_EMPTY, code)
    check("C22: asking for a re-analysis", "re-analyze" in reason, reason)


# ---------------------------------------------------------------------------
# D. was the completed deletion safe to keep?
# ---------------------------------------------------------------------------

def test_acceptance_keeps_a_good_deletion():
    print("\n[D] a deletion that did what it said is kept")
    before = report(131, 2, 1, boundary=6, counts=[128, 3])
    after = report(128, 1, 0, boundary=0, counts=[128])
    ok, reasons = artifact.accept_deletion(
        before, after, True,
        {"triangle_count": 3, "primary_triangle_count": 128})
    check("D1: accepted", ok, reasons)
    check("D2: with no reasons", reasons == [], reasons)


def test_partial_improvement_is_enough():
    print("\n[D] non-manifold 2 -> 1 is an improvement, not a failure")
    before = report(1000, 3, 2, counts=[900, 60, 40])
    after = report(960, 2, 1, counts=[900, 60])
    ok, reasons = artifact.accept_deletion(
        before, after, True,
        {"triangle_count": 40, "primary_triangle_count": 900})
    check("D3: accepted at 2 -> 1", ok, reasons)
    check("D4: the rule is not 'every defect must be gone'",
          int(after["nonmanifold_edge_count"]) > 0 and ok)


def test_acceptance_rejects():
    print("\n[D] everything that must roll a deletion back")
    base_expected = {"triangle_count": 3, "primary_triangle_count": 128}
    before = report(131, 2, 1, boundary=6, counts=[128, 3])

    cases = [
        ("D5: the defect survived - non-manifold did not fall",
         report(128, 1, 1, counts=[128]), base_expected, "did not decrease"),
        ("D6: non-manifold got worse",
         report(128, 1, 4, counts=[128]), base_expected, "did not decrease"),
        ("D7: a degenerate triangle appeared",
         report(128, 1, 0, degenerate=2, counts=[128]), base_expected,
         "degenerate"),
        ("D8: the mesh has no triangles left",
         report(0, 0, 0, counts=[]), base_expected, "no triangles"),
        ("D9: more than one component disappeared",
         report(100, 0, 0, counts=[100]),
         {"triangle_count": 31, "primary_triangle_count": 128},
         "exactly one component"),
        ("D10: it removed something other than that component",
         report(120, 1, 0, counts=[120]), base_expected,
         "not the 3 the component held"),
        ("D11: the primary body changed size",
         report(128, 1, 0, counts=[125, 3]),
         {"triangle_count": 3, "primary_triangle_count": 128},
         "primary body component changed size"),
    ]
    for label, after, expected, wanted in cases:
        ok, reasons = artifact.accept_deletion(before, after, True, expected)
        joined = "; ".join(reasons)
        check(label, (not ok) and wanted in joined, joined)

    ok, reasons = artifact.accept_deletion(
        report(131, 2, 1, counts=[128, 3]), report(128, 1, 0, counts=[128]),
        False, base_expected)
    check("D12: a lost texture rolls it back", not ok
          and any("texture" in reason for reason in reasons), reasons)


def test_acceptance_is_the_existing_rule_plus_additions():
    print("\n[D] acceptance can only be stricter than the shared rule")
    before = report(131, 2, 1, boundary=6, counts=[128, 3])
    expected = {"triangle_count": 3, "primary_triangle_count": 128}
    states = [
        report(128, 1, 0, counts=[128]),
        report(128, 1, 1, counts=[128]),
        report(128, 3, 0, counts=[100, 20, 8]),
        report(0, 0, 0, counts=[]),
        report(128, 1, 0, degenerate=5, counts=[128]),
        report(200, 1, 0, counts=[200]),
    ]
    softer = 0
    for after in states:
        shared, _why = repair.accept_repair(before, after, True,
                                            require_nonmanifold_decrease=True)
        mine, _reasons = artifact.accept_deletion(before, after, True, expected)
        if mine and not shared:
            softer += 1
    check("D13: it never accepts what repair.accept_repair refused",
          softer == 0, softer)
    check("D14: and it calls that rule rather than restating it",
          "repair.accept_repair(" in open(
              os.path.join(PACKAGE, "artifact.py")).read())


def test_deletion_record():
    print("\n[D] the provenance line the repair log gets")
    vertices, faces = body_plus_fragment()
    triangle_labels, vertex_labels = labelled(vertices, faces)
    facts = artifact.component_facts(vertices, faces, vertex_labels,
                                     triangle_labels, 1)
    record = artifact.deletion_record(
        facts, report(131, 2, 1, counts=[128, 3]),
        report(128, 1, 0, counts=[128]))
    check("D15: it names the action", record["action"]
          == "Delete defect component", record["action"])
    check("D16: it records the non-manifold change",
          record["nonmanifold_before"] == 1 and record["nonmanifold_after"] == 0)
    check("D17: and the component change",
          record["components_before"] == 2 and record["components_after"] == 1)
    check("D18: the detail names the component and its size",
          "component 2" in record["detail"] and "3 triangles" in record["detail"],
          record["detail"])


# ---------------------------------------------------------------------------
# E. scope
# ---------------------------------------------------------------------------

def test_scope():
    print("\n[E] what this feature deliberately is not")
    source = open(os.path.join(PACKAGE, "artifact.py")).read()
    check("E1: no bpy - the policy is testable without Blender",
          "import bpy" not in source)
    check("E2: it is not a global cleanup",
          "remove_doubles" not in source and "holes_fill" not in source
          and "remesh" not in source)
    check("E3: it states that BSMT does not judge anatomical relevance",
          "NOT ANSWERED HERE" in source and "anatomically" in source)
    check("E4: and that the judgement is the researcher's",
          "researcher's" in source)
    check("E5: 'primary' means the most triangles, not object count",
          "most triangles" in source)
    check("E6: the deletion is component-scoped, by name",
          "component_facts" in source and "vertex_indices" in source)

    operators_source = open(os.path.join(PACKAGE, "operators.py")).read()
    check("E7: the operator takes a backup before editing",
          "meshrepair.make_backup(obj)" in operators_source)
    check("E8: it uses the centralized geometry-change invalidation",
          "state.invalidate_for_geometry_change(context, obj.name,"
          in operators_source)
    check("E9: and it clears the highlights it invalidated",
          "visualization.clear_repair_highlights()" in operators_source)
    check("E10: readiness still allows several components",
          "connected_components == 1" not in open(
              os.path.join(PACKAGE, "repair.py")).read())


def main():
    for test in (
        test_defect_regions,
        test_grouping_matches_the_full_region_description,
        test_components_of_vertices,
        test_component_facts,
        test_confirmation_text,
        test_a_large_non_primary_component_is_called_out,
        test_primary_component_is_refused,
        test_single_component_is_refused,
        test_a_tie_is_refused_not_broken,
        test_no_defect_and_empty_component,
        test_acceptance_keeps_a_good_deletion,
        test_partial_improvement_is_enough,
        test_acceptance_rejects,
        test_acceptance_is_the_existing_rule_plus_additions,
        test_deletion_record,
        test_scope,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
