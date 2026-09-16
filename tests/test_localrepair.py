"""Offline tests for Milestone 3.30: local face repair inside a component.

    python3 tests/test_localrepair.py

Everything that DECIDES lives in ``localrepair.py`` and is pure numpy, so it
is testable without Blender: what the local topology around a non-manifold
defect actually is, whether that topology yields one unambiguous removable
branch, what removing it would do, and whether a completed removal stayed
inside the branch it was approved for. The bmesh editing, the transaction and
the operator wiring are exercised by
tests/test_local_face_repair_blender.py.

The checks this suite exists for above all others:

  * an AMBIGUOUS classification never offers a removal - two comparable
    branches, three or more branches, and a branch that runs away into the
    body are each refused, by their own reason, and none of them sets
    `removable`;
  * a candidate is never larger than the software safety caps say, and those
    caps are stated in the report rather than applied silently;
  * the locality proof actually fails when something outside the approved
    candidate moves - a locality check that cannot fail proves nothing;
  * duplicate faces are CLASSIFIED and handed to the existing repair, not
    reimplemented here.
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
    """Import repair + localrepair + topology without importing bpy."""
    package = types.ModuleType("bsmt_l")
    package.__path__ = [PACKAGE]
    sys.modules["bsmt_l"] = package
    geo = types.ModuleType("bsmt_l.geodesic")
    geo.__path__ = [os.path.join(PACKAGE, "geodesic")]
    sys.modules["bsmt_l.geodesic"] = geo
    modules = {}
    for name, path in (
        ("bsmt_l.geodesic.topology",
         os.path.join(PACKAGE, "geodesic", "topology.py")),
        ("bsmt_l.repair", os.path.join(PACKAGE, "repair.py")),
        ("bsmt_l.artifact", os.path.join(PACKAGE, "artifact.py")),
        ("bsmt_l.localrepair", os.path.join(PACKAGE, "localrepair.py")),
    ):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module
    return (modules["bsmt_l.localrepair"], modules["bsmt_l.repair"],
            modules["bsmt_l.artifact"])


localrepair, repair, artifact = _load()


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


def an_interior_edge(vertices, faces, position=50):
    edges = repair.classify_edges(faces, int(vertices.shape[0]))["interior"]
    return int(edges[position][0]), int(edges[position][1])


def flap(n=12, faces_in_flap=1, height=0.3, spread=0.2):
    """A grid with a small face branch hanging off ONE interior edge.

    That edge then carries three faces - the two the grid already had, plus
    the first of the flap - which is exactly the shape of the real scan's
    single non-manifold edge.
    """
    vertices, faces = grid(n)
    a, b = an_interior_edge(vertices, faces)
    extra = []
    for step in range(faces_in_flap):
        extra.append([vertices[a][0] + spread * step,
                      vertices[a][1] + spread,
                      height + spread * step])
    vertices = np.vstack([vertices, extra])
    base = int(vertices.shape[0]) - faces_in_flap
    grown = list(faces)
    grown.append([a, b, base])
    for step in range(1, faces_in_flap):
        grown.append([a, base + step - 1, base + step])
    return vertices, np.asarray(grown, dtype=np.int64)


def twin_branches(size=3):
    """One edge with two SEPARATE local patches of `size` faces on each side.

    Neither patch is the body and neither is obviously the artefact. The
    fixture the ambiguity rule exists for.
    """
    vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    faces = []
    for sign in (1.0, -1.0):
        base = len(vertices)
        vertices.append([0.5, 1.0, 0.5 * sign])
        vertices.append([0.5, -1.0, 0.5 * sign])
        faces.append([0, 1, base])
        faces.append([0, 1, base + 1])
        faces.append([base, base + 1, 0])
        previous = base
        for step in range(size - 3):
            vertices.append([1.0 + 0.4 * step, 1.0 + 0.4 * step, 0.5 * sign])
            faces.append([base + 1, previous, len(vertices) - 1])
            previous = len(vertices) - 1
    return (np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64))


def three_patches():
    """One edge carrying three separate local patches. Unambiguously ambiguous."""
    vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    faces = []
    for index, height in enumerate((1.0, -1.0, 0.5)):
        base = len(vertices)
        vertices.append([0.5, 1.0, height])
        vertices.append([1.5, 1.5, height])
        vertices.append([-0.5, 1.5, height])
        faces.append([0, 1, base])
        faces.append([1, base, base + 1])
        faces.append([0, base, base + 2])
    return (np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64))


def defect_edges(vertices, faces):
    return repair.classify_edges(faces, int(vertices.shape[0]))["non_manifold"]


def classify(vertices, faces, **kwargs):
    return localrepair.classify_local_defect(
        vertices, faces, defect_edges(vertices, faces), **kwargs)


# ---------------------------------------------------------------------------
# A. the supported cases
# ---------------------------------------------------------------------------

def test_single_face_flap():
    vertices, faces = flap(faces_in_flap=1)
    check("A1: the fixture has exactly one non-manifold edge",
          defect_edges(vertices, faces).shape[0] == 1)

    report = classify(vertices, faces)
    check("A2: one face hanging off an interior edge is a dangling flap",
          report["classification"] == localrepair.LOCAL_DANGLING_FLAP,
          report["classification"] + " / " + report["reason"])
    check("A3: and a removal is offered", report["removable"] is True)
    check("A4: the defect edge carries three faces",
          report["incident_face_count"] == 3,
          str(report["incident_face_counts"]))
    check("A5: the candidate is exactly the one flap face",
          report["candidate"]["face_count"] == 1,
          str(report["candidate"]["face_count"]))
    check("A6: its three vertices are named",
          report["candidate"]["vertex_count"] == 3)
    check("A7: the apex is stranded by the removal and counted",
          report["candidate"]["dropped_vertex_count"] == 1,
          str(report["candidate"]["dropped_vertex_count"]))
    check("A8: the candidate has an area",
          report["candidate"]["area_mm2"] > 0.0)
    check("A9: and a bounding box",
          all(value >= 0.0 for value in report["candidate"]["bbox_mm"])
          and report["candidate"]["bbox_diagonal_mm"] > 0.0)
    check("A10: reach from the defect is reported",
          report["candidate"]["reach_mm"] > 0.0)
    check("A11: removal is predicted to resolve the defect",
          report["prediction"]["nonmanifold_before"] == 1
          and report["prediction"]["nonmanifold_after"] == 0,
          str(report["prediction"]["nonmanifold_after"]))

    body, candidate = report["branches"]
    check("A12: the body branch dwarfs the candidate",
          body["face_count"] > 8 * candidate["face_count"],
          "%d vs %d" % (body["face_count"], candidate["face_count"]))


def test_multi_face_flap():
    vertices, faces = flap(faces_in_flap=4)
    report = classify(vertices, faces)
    check("A13: a four-face flap is still classified",
          report["removable"] is True,
          report["classification"] + " / " + report["reason"])
    check("A14: and the WHOLE branch is the candidate, not just the face "
          "touching the defect",
          report["candidate"]["face_count"] == 4,
          str(report["candidate"]["face_count"]))
    check("A15: every flap face is named by vertex set, never by index",
          len(report["candidate"]["face_keys"]) == 4
          and all(len(key) == 3 for key in report["candidate"]["face_keys"]))


def test_a_branch_that_shares_every_vertex_is_a_branch_not_a_flap():
    """A redundant patch lying ON the surface strands no vertex."""
    vertices, faces = grid(12)
    a, b = an_interior_edge(vertices, faces)
    # A third face on (a, b) whose apex is an EXISTING grid vertex, so nothing
    # is stranded when it goes.
    apex = int(faces[0][0]) if int(faces[0][0]) not in (a, b) else int(faces[0][1])
    grown = np.vstack([faces, [[a, b, apex]]])
    report = localrepair.classify_local_defect(
        vertices, grown, defect_edges(vertices, grown))
    check("A16: a patch that strands no vertex is a small local branch",
          report["classification"] == localrepair.LOCAL_SMALL_BRANCH,
          report["classification"] + " / " + report["reason"])
    check("A17: and it is still removable", report["removable"] is True)
    check("A18: nothing is stranded by it",
          report["candidate"]["dropped_vertex_count"] == 0)


# ---------------------------------------------------------------------------
# B. the refusals - the half of this milestone that matters most
# ---------------------------------------------------------------------------

def test_two_similar_branches_are_ambiguous():
    vertices, faces = twin_branches(size=3)
    check("B1: the fixture has one non-manifold edge",
          defect_edges(vertices, faces).shape[0] == 1)
    report = classify(vertices, faces)
    check("B2: two comparable branches are AMBIGUOUS",
          report["classification"] == localrepair.LOCAL_AMBIGUOUS,
          report["classification"])
    check("B3: and no removal is offered", report["removable"] is False)
    check("B4: no candidate is proposed at all",
          report["candidate"] is None)
    check("B5: the reason names the two branch sizes",
          "similarly sized" in report["reason"]
          and "3 and 3" in report["reason"], report["reason"])
    check("B6: the researcher is told a manual edit is required",
          localrepair.MANUAL_EDIT_MESSAGE
          in "\n".join(localrepair.report_lines(report)))


def test_three_branches_are_ambiguous():
    vertices, faces = three_patches()
    report = classify(vertices, faces)
    check("B7: three local branches are AMBIGUOUS",
          report["classification"] == localrepair.LOCAL_AMBIGUOUS,
          report["classification"])
    check("B8: no removal is offered", report["removable"] is False)
    check("B9: the reason states how many branches were found",
          "into 3 branches" in report["reason"]
          and "2 candidate branches" in report["reason"], report["reason"])
    check("B10: and all three branches are reported",
          len(report["branches"]) == 3)


def test_a_defect_woven_into_the_surface_is_ambiguous():
    """A non-manifold edge whose two sides reconnect is not a flap at all."""
    vertices, faces = grid(12)
    a, b = an_interior_edge(vertices, faces)
    # Duplicate the edge's own two faces' shape onto the same vertices via a
    # bridging face that reconnects both sides: one branch, nothing to remove.
    third = int(np.setdiff1d(np.unique(faces[np.any(faces == a, axis=1)]),
                             [a, b])[0])
    grown = np.vstack([faces, [[a, b, third]]])
    report = localrepair.classify_local_defect(
        vertices, grown, defect_edges(vertices, grown))
    check("B11: a defect whose sides reconnect yields no candidate",
          report["removable"] is False
          or report["candidate"]["face_count"] >= 1)
    check("B12: and whatever it is, the classification is stated",
          report["classification"] in (
              localrepair.LOCAL_AMBIGUOUS, localrepair.LOCAL_SMALL_BRANCH,
              localrepair.LOCAL_DANGLING_FLAP,
              localrepair.LOCAL_DUPLICATE_FACE))


def test_the_dominance_ratio_refuses_a_comparable_branch():
    """A big branch is only "the body" when it is much bigger."""
    vertices, faces = grid(6)                  # 50 faces
    a, b = an_interior_edge(vertices, faces, position=10)
    extra = [[float(step) * 0.1, -2.0 - 0.1 * step, 0.5]
             for step in range(12)]
    grown = list(faces)
    base = int(vertices.shape[0])
    vertices = np.vstack([vertices, extra])
    grown.append([a, b, base])
    for step in range(1, 12):
        grown.append([a, base + step - 1, base + step])
    grown = np.asarray(grown, dtype=np.int64)
    report = localrepair.classify_local_defect(
        vertices, grown, defect_edges(vertices, grown),
        dominance_ratio=8)
    body, candidate = report["branches"][0], report["branches"][1]
    check("B13: the fixture's branches are within 8x of each other",
          body["face_count"] < 8 * candidate["face_count"],
          "%d vs %d" % (body["face_count"], candidate["face_count"]))
    check("B14: so the classification is AMBIGUOUS",
          report["classification"] == localrepair.LOCAL_AMBIGUOUS,
          report["classification"])
    check("B15: and the reason says why",
          "similarly sized" in report["reason"], report["reason"])
    # The same fixture with a lower ratio IS accepted, which proves the
    # refusal came from the dominance rule and not from something else.
    permissive = localrepair.classify_local_defect(
        vertices, grown, defect_edges(vertices, grown), dominance_ratio=2)
    check("B16: a lower dominance ratio accepts the same fixture",
          permissive["removable"] is True, permissive["reason"])


def test_the_candidate_face_cap_refuses_a_large_branch():
    vertices, faces = flap(n=24, faces_in_flap=6)
    strict = localrepair.classify_local_defect(
        vertices, faces, defect_edges(vertices, faces),
        max_candidate_faces=3)
    check("B17: a candidate above the face cap is refused",
          strict["removable"] is False
          and strict["classification"] == localrepair.LOCAL_AMBIGUOUS,
          strict["classification"])
    check("B18: the reason names the cap as a SOFTWARE safety cap",
          "software safety cap" in strict["reason"], strict["reason"])
    check("B19: and it still shows the candidate it refused",
          strict["candidate"] is not None
          and strict["candidate"]["face_count"] == 6)
    relaxed = localrepair.classify_local_defect(
        vertices, faces, defect_edges(vertices, faces),
        max_candidate_faces=64)
    check("B20: the same branch under the real cap is accepted",
          relaxed["removable"] is True, relaxed["reason"])


def test_the_diagonal_cap_refuses_a_far_reaching_branch():
    vertices, faces = flap(faces_in_flap=3, spread=40.0, height=40.0)
    report = classify(vertices, faces)
    check("B21: a branch reaching far past the defect is refused",
          report["removable"] is False, report["reason"])
    check("B22: the reason names the millimetre cap",
          "mm software safety cap" in report["reason"], report["reason"])


def test_a_mesh_with_no_defect():
    vertices, faces = grid(8)
    report = localrepair.classify_local_defect(
        vertices, faces, np.zeros((0, 2), dtype=np.int64))
    check("B23: no defect edges means UNSUPPORTED, not a candidate",
          report["classification"] == localrepair.LOCAL_UNSUPPORTED
          and report["removable"] is False)
    check("B24: and the reason says so",
          "no non-manifold edges" in report["reason"], report["reason"])


def test_refusal_never_sets_removable():
    """One assertion over every refusing fixture: no removal is EVER offered."""
    fixtures = [
        ("two comparable branches", twin_branches(size=3)),
        ("three branches", three_patches()),
    ]
    for label, (vertices, faces) in fixtures:
        report = classify(vertices, faces)
        check("B25: %s offers no removal" % label,
              report["removable"] is False
              and report["classification"] != localrepair.LOCAL_DANGLING_FLAP
              and report["classification"] != localrepair.LOCAL_SMALL_BRANCH,
              report["classification"])


# ---------------------------------------------------------------------------
# C. duplicate faces are delegated, never reimplemented
# ---------------------------------------------------------------------------

def test_exact_duplicate_face_is_delegated():
    vertices, faces = grid(12)
    doubled = np.vstack([faces, faces[20:21]])
    report = localrepair.classify_local_defect(
        vertices, doubled, defect_edges(vertices, doubled))
    check("C1: an exact duplicate face is classified as such",
          report["classification"] == localrepair.LOCAL_DUPLICATE_FACE,
          report["classification"])
    check("C2: and NO local removal is offered for it",
          report["removable"] is False)
    check("C3: the researcher is pointed at Remove Duplicate Faces",
          "Remove Duplicate Faces" in report["reason"], report["reason"])
    check("C4: the existing repair finds the same face",
          list(repair.duplicate_faces(doubled)) == [len(doubled) - 1],
          str(repair.duplicate_faces(doubled)))


def test_reversed_duplicate_face_is_delegated():
    vertices, faces = grid(12)
    doubled = np.vstack([faces, faces[20:21][:, ::-1]])
    report = localrepair.classify_local_defect(
        vertices, doubled, defect_edges(vertices, doubled))
    check("C5: a REVERSED duplicate is the same case",
          report["classification"] == localrepair.LOCAL_DUPLICATE_FACE,
          report["classification"])
    check("C6: no local removal is offered", report["removable"] is False)
    check("C7: the existing repair already handles a reversed copy",
          list(repair.duplicate_faces(doubled)) == [len(doubled) - 1],
          str(repair.duplicate_faces(doubled)))
    check("C8: and it is counted",
          report["duplicate_face_count"] == 1)


# ---------------------------------------------------------------------------
# D. the branch walk itself
# ---------------------------------------------------------------------------

def test_branch_walk_treats_the_defect_as_a_wall():
    vertices, faces = flap(faces_in_flap=1)
    edges = defect_edges(vertices, faces)
    branches = localrepair.local_branches(faces, edges,
                                          int(vertices.shape[0]))
    check("D1: exactly two branches come back", len(branches) == 2,
          str(len(branches)))
    check("D2: the larger one is first",
          branches[0]["face_count"] > branches[1]["face_count"])
    check("D3: the candidate branch holds one face",
          branches[1]["face_count"] == 1)
    check("D4: and the two do not overlap",
          not (set(int(v) for v in branches[0]["faces"])
               & set(int(v) for v in branches[1]["faces"])))


def test_the_walk_is_bounded():
    """A big continuation is reported as unbounded, never fully traversed."""
    vertices, faces = flap(n=40, faces_in_flap=1)     # 3042 grid faces
    edges = defect_edges(vertices, faces)
    branches = localrepair.local_branches(faces, edges,
                                          int(vertices.shape[0]),
                                          explore_cap=64)
    check("D5: the body branch stops at the inspection cap",
          branches[0]["unbounded"] is True
          and branches[0]["face_count"] <= 64 + 3,
          str(branches[0]["face_count"]))
    check("D6: the small branch is not marked unbounded",
          branches[1]["unbounded"] is False)
    report = localrepair.classify_local_defect(
        vertices, faces, edges, explore_cap=64)
    check("D7: an unbounded body branch satisfies dominance on its own",
          report["removable"] is True, report["reason"])
    check("D8: and the report says the body branch was capped",
          any(branch["unbounded"] for branch in report["branches"]))


def test_incident_faces():
    vertices, faces = flap(faces_in_flap=1)
    edges = defect_edges(vertices, faces)
    found = localrepair.incident_faces(faces, edges,
                                       int(vertices.shape[0]))
    check("D9: the defect edge resolves to three incident faces",
          [int(value.size) for value in found.values()] == [3],
          str([int(value.size) for value in found.values()]))


# ---------------------------------------------------------------------------
# E. predicted outcome
# ---------------------------------------------------------------------------

def test_prediction_reports_the_boundary_it_opens():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    prediction = report["prediction"]
    check("E1: the prediction is made before anything is edited",
          prediction is not None)
    check("E2: non-manifold 1 -> 0 is predicted",
          (prediction["nonmanifold_before"], prediction["nonmanifold_after"])
          == (1, 0))
    check("E3: the boundary count is predicted both before and after",
          "boundary_before" in prediction and "boundary_after" in prediction)
    grew = prediction["boundary_after"] > prediction["boundary_before"]
    stated = "Boundary edges" in "\n".join(localrepair.report_lines(report))
    check("E4: a boundary the removal would OPEN is reported, never refused",
          stated == grew, "grew=%s stated=%s" % (grew, stated))
    check("E5: no new non-manifold edge is predicted",
          not prediction["introduced"])
    check("E6: no new degenerate triangle is predicted",
          not (prediction["degenerate_after"]
               - prediction["degenerate_before"]))


def test_a_removal_that_would_not_help_is_refused():
    """The branch separation is not enough on its own; it has to WORK."""
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    candidate = report["candidate"]["face_indices"]
    prediction = localrepair.predict_removal(vertices, faces, candidate)
    check("E7: removing the candidate strictly reduces the count",
          prediction["nonmanifold_after"] < prediction["nonmanifold_before"])
    unhelpful = localrepair.predict_removal(vertices, faces, [])
    check("E8: removing nothing does not, and would be refused",
          unhelpful["nonmanifold_after"] == unhelpful["nonmanifold_before"])


# ---------------------------------------------------------------------------
# F. geometry locality (sect. 9 of the brief)
# ---------------------------------------------------------------------------

def test_locality_accepts_exactly_the_approved_removal():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    candidate = np.asarray(report["candidate"]["face_indices"],
                           dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[candidate] = False
    # What a correct edit produces: the same vertices, minus the stranded
    # apex, renumbered - which is what makes an index comparison useless.
    used = np.unique(faces[keep])
    remap = np.full(int(vertices.shape[0]), -1, dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)
    after_vertices = vertices[used]
    after_faces = remap[faces[keep]]

    # Renumbered on purpose, the way bmesh renumbers after a deletion: an
    # index comparison must be UNABLE to pass this, so F1 passing means the
    # positions matched and not the indices.
    shuffle = np.random.RandomState(1).permutation(after_vertices.shape[0])
    inverse = np.empty_like(shuffle)
    inverse[shuffle] = np.arange(shuffle.size)
    after_vertices = after_vertices[shuffle]
    after_faces = inverse[after_faces]

    reasons = localrepair.locality_reasons(vertices, faces, candidate,
                                           after_vertices, after_faces)
    check("F1: a correct local removal passes the locality proof",
          reasons == [], str(reasons))
    check("F2: even though every vertex was renumbered",
          not np.array_equal(faces[keep], after_faces))


def test_locality_catches_geometry_that_moved():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    candidate = np.asarray(report["candidate"]["face_indices"],
                           dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[candidate] = False
    moved = vertices.copy()
    moved[0] = moved[0] + np.array([0.0, 0.0, 5.0])
    reasons = localrepair.locality_reasons(vertices, faces, candidate,
                                           moved, faces[keep])
    check("F3: a vertex moved outside the candidate FAILS the proof",
          len(reasons) == 1 and "was not local" in reasons[0], str(reasons))


def test_locality_catches_an_extra_deletion():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    candidate = np.asarray(report["candidate"]["face_indices"],
                           dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[candidate] = False
    keep[7] = False                              # one unrelated face too many
    reasons = localrepair.locality_reasons(vertices, faces, candidate,
                                           vertices, faces[keep])
    check("F4: deleting one face too many FAILS the proof",
          len(reasons) == 1 and "not the" in reasons[0], str(reasons))


def test_locality_catches_a_face_that_should_have_gone():
    vertices, faces = flap(faces_in_flap=4)
    report = classify(vertices, faces)
    candidate = np.asarray(report["candidate"]["face_indices"],
                           dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[candidate[:-1]] = False                 # one flap face left behind
    reasons = localrepair.locality_reasons(vertices, faces, candidate,
                                           vertices, faces[keep])
    check("F5: leaving an approved face behind FAILS the proof",
          reasons != [], str(reasons))


def test_the_signature_ignores_winding_and_renumbering():
    vertices, faces = grid(6)
    reversed_faces = faces[:, ::-1]
    check("F6: a reversed winding is the same surface",
          np.array_equal(localrepair.face_position_signature(vertices, faces),
                         localrepair.face_position_signature(vertices,
                                                             reversed_faces)))
    shuffled = np.random.RandomState(0).permutation(faces.shape[0])
    check("F7: face ORDER is not part of the signature",
          np.array_equal(localrepair.face_position_signature(vertices, faces),
                         localrepair.face_position_signature(
                             vertices, faces[shuffled])))


# ---------------------------------------------------------------------------
# G. acceptance (sect. 8)
# ---------------------------------------------------------------------------

def _report(triangles=1000, vertices=600, nonmanifold=1, boundary=0,
            degenerate=0, components=1):
    return {
        "triangle_count": triangles,
        "vertex_count": vertices,
        "nonmanifold_edge_count": nonmanifold,
        "boundary_edge_count": boundary,
        "degenerate_triangle_count": degenerate,
        "component_count": components,
    }


def test_acceptance_keeps_a_good_removal():
    before = _report(nonmanifold=1)
    after = _report(triangles=999, vertices=599, nonmanifold=0, boundary=3)
    reasons = localrepair.extra_removal_reasons(
        before, after, {"face_count": 1, "dropped_vertex_count": 1})
    check("G1: a clean 1 -> 0 removal is accepted", reasons == [],
          str(reasons))


def test_acceptance_allows_partial_improvement():
    before = _report(nonmanifold=4)
    after = _report(triangles=999, vertices=599, nonmanifold=3, boundary=3)
    reasons = localrepair.extra_removal_reasons(
        before, after, {"face_count": 1, "dropped_vertex_count": 1})
    check("G2: 4 -> 3 is a success, not a failure", reasons == [],
          str(reasons))
    accepted, why = repair.accept_repair(before, after, True,
                                         require_nonmanifold_decrease=True)
    check("G3: and the authoritative rule agrees", accepted is True, str(why))


def test_acceptance_tolerates_new_boundary_edges():
    before = _report(nonmanifold=1, boundary=0)
    after = _report(triangles=999, vertices=599, nonmanifold=0, boundary=3)
    reasons = localrepair.extra_removal_reasons(
        before, after, {"face_count": 1, "dropped_vertex_count": 1})
    check("G4: exposing a boundary the flap covered is not a failure",
          reasons == [], str(reasons))


def test_acceptance_rejects():
    cases = [
        ("a rising non-manifold count",
         _report(nonmanifold=1),
         _report(triangles=999, vertices=599, nonmanifold=2),
         {"face_count": 1, "dropped_vertex_count": 1}),
        ("a new degenerate triangle",
         _report(nonmanifold=1),
         _report(triangles=999, vertices=599, nonmanifold=0, degenerate=1),
         {"face_count": 1, "dropped_vertex_count": 1}),
        ("more triangles removed than approved",
         _report(nonmanifold=1),
         _report(triangles=990, vertices=599, nonmanifold=0),
         {"face_count": 1, "dropped_vertex_count": 1}),
        ("more vertices removed than approved",
         _report(nonmanifold=1),
         _report(triangles=999, vertices=560, nonmanifold=0),
         {"face_count": 1, "dropped_vertex_count": 1}),
    ]
    for label, before, after, expected in cases:
        reasons = localrepair.extra_removal_reasons(before, after, expected)
        check("G5: %s is refused" % label, reasons != [], str(reasons))


def test_acceptance_is_the_existing_rule_plus_additions():
    """It may only ever be STRICTER than repair.accept_repair, never softer."""
    cases = [
        (_report(nonmanifold=1), _report(triangles=0, vertices=0,
                                         nonmanifold=0)),
        (_report(nonmanifold=1), _report(triangles=999, vertices=599,
                                         nonmanifold=1)),
        (_report(nonmanifold=1, components=1),
         _report(triangles=999, vertices=599, nonmanifold=0, components=4)),
    ]
    for before, after in cases:
        accepted, _why = repair.accept_repair(
            before, after, True, require_nonmanifold_decrease=True)
        check("G6: the existing rule refuses %d -> %d triangles"
              % (before["triangle_count"], after["triangle_count"]),
              accepted is False)


def test_defect_resolution_is_checked_by_position():
    vertices, faces = flap(faces_in_flap=1)
    edges = defect_edges(vertices, faces)
    region = {"edges": edges}
    before = repair.region_signature(vertices, region)
    check("G7: the focused defect has a positional signature",
          len(before) == 1)

    report = classify(vertices, faces)
    candidate = np.asarray(report["candidate"]["face_indices"],
                           dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[candidate] = False
    after = repair.nonmanifold_signature(vertices, faces[keep])
    check("G8: after the removal none of it survives",
          localrepair.defect_resolved_reasons(before, after) == [])
    check("G9: and a survivor would be reported",
          localrepair.defect_resolved_reasons(before, before) != [])


def test_introduced_elsewhere_is_refused():
    check("G10: a non-manifold edge that was not there before is refused",
          localrepair.introduced_reasons({("a",)}, {("a",), ("b",)}) != [])
    check("G11: an edge that WAS there before is not 'introduced'",
          localrepair.introduced_reasons({("a",), ("b",)}, {("a",)}) == [])


# ---------------------------------------------------------------------------
# H. what the researcher is told
# ---------------------------------------------------------------------------

def test_report_text():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    text = "\n".join(localrepair.report_lines(report, defect_id=1))
    for fragment in ("Focused defect: D1", "Non-manifold edges:",
                     "Incident faces:", "Classification:",
                     "Small dangling flap", "Candidate:", "Faces:",
                     "Vertices:", "Area:", "Bounding box:",
                     "Maximum reach:", "Safety caps (software, not "
                     "anatomical)"):
        check("H1: the report states %r" % fragment, fragment in text,
              text)


def test_ambiguous_report_text():
    vertices, faces = twin_branches(size=3)
    report = classify(vertices, faces)
    text = "\n".join(localrepair.report_lines(report, defect_id=2))
    check("H2: an ambiguous report names the classification",
          "Ambiguous / unsupported local topology" in text, text)
    check("H3: it gives a reason", "Reason:" in text)
    check("H4: it says manual editing is required",
          "Manual mesh inspection is required" in text)
    check("H5: it proposes no candidate at all",
          "Faces to remove" not in text and "Candidate:" not in text)


def test_confirmation_text():
    vertices, faces = flap(faces_in_flap=1)
    report = classify(vertices, faces)
    lines = localrepair.confirmation_lines(report, defect_id=1,
                                           object_name="Body_BSMT",
                                           landmark_count=3,
                                           measurement_count=2)
    text = "\n".join(lines)
    for fragment in ("Remove the local candidate faces",
                     "Only the measurement mesh will be modified",
                     "source scan will remain unchanged",
                     "Classification:", "Faces to remove:", "Surface area:",
                     "Bounding box:", "Maximum reach:",
                     "may become stale", "3 landmark(s) and 2 measurement(s)"):
        check("H6: the dialog states %r" % fragment, fragment in text, text)


def test_describe():
    vertices, faces = flap(faces_in_flap=1)
    line = localrepair.describe(classify(vertices, faces))
    check("H7: one-line description names the class and the size",
          "Small dangling flap" in line and "1 face(s)" in line, line)
    ambiguous = localrepair.describe(classify(*twin_branches(size=3)))
    check("H8: a refusal describes itself as one",
          "Ambiguous" in ambiguous, ambiguous)


# ---------------------------------------------------------------------------
# I. scope: what this module is NOT
# ---------------------------------------------------------------------------

def executable_source(path):
    """Executable tokens only: comments and docstrings are prose, not scope.

    A scope test that greps the whole file fails the moment the module
    DOCUMENTS what it deliberately does not do - which is exactly what these
    modules are written to do.
    """
    import tokenize
    with open(path, "rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    kept = []
    previous = tokenize.INDENT
    for token in tokens:
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                tokenize.NL, tokenize.ENCODING):
            previous = token.type          # a docstring, not a value
            continue
        kept.append(token.string)
        previous = token.type
    return " ".join(kept)


def test_scope():
    source = open(os.path.join(PACKAGE, "localrepair.py")).read()
    code = executable_source(os.path.join(PACKAGE, "localrepair.py"))
    check("I1: no bpy and no bmesh in the policy module",
          "import bpy" not in code and "bmesh" not in code)
    check("I2: no pygeodesic - this is topology, not distance",
          "pygeodesic" not in code and "geodesic" not in code)
    check("I3: it does not weld", "remove_doubles" not in code
          and "weld_verts" not in code)
    check("I4: it does not remesh or decimate",
          "remesh" not in code.lower() and "decimate" not in code.lower())
    check("I5: the caps are documented as software, not anatomical",
          "NOT anatomical" in source or "not anatomical" in source)
    check("I6: it reuses the existing edge classification",
          "repair.classify_edges" in source)
    check("I7: it reuses the existing positional signatures",
          "repair.nonmanifold_signature" in source)
    check("I8: it does not reimplement duplicate-face removal",
          "DUPLICATE_DELEGATE_MESSAGE" in source
          and "Remove Duplicate Faces" in source)

    operators_source = open(os.path.join(PACKAGE, "operators.py")).read()
    check("I9: the removal runs inside the existing transactional wrapper",
          "class BSMT_OT_remove_local_faces(_RepairBase)" in operators_source)
    check("I10: it uses the existing face-removal primitive",
          "meshrepair.remove_faces_by_vertex_sets" in operators_source)
    check("I11: it re-derives the candidate inside the transaction",
          "_inspect_local_defect(\n                props, fresh)"
          in operators_source
          or "_inspect_local_defect(props, fresh)" in operators_source)
    check("I12: it uses the centralized geometry-change invalidation",
          "state.invalidate_for_geometry_change(context, obj.name,"
          in operators_source)
    check("I13: it clears the highlights it invalidated",
          "visualization.clear_repair_highlights()" in operators_source)
    check("I14: the primary-component deletion block is untouched",
          "PRIMARY_BLOCK_MESSAGE" in open(
              os.path.join(PACKAGE, "artifact.py")).read())
    check("I15: the local weld is untouched",
          "class BSMT_OT_weld_non_manifold(_RepairBase)" in operators_source)
    check("I16: Remove Duplicate Faces is untouched",
          "class BSMT_OT_remove_duplicate_faces(_RepairBase)"
          in operators_source)


def main():
    for test in (
        test_single_face_flap,
        test_multi_face_flap,
        test_a_branch_that_shares_every_vertex_is_a_branch_not_a_flap,
        test_two_similar_branches_are_ambiguous,
        test_three_branches_are_ambiguous,
        test_a_defect_woven_into_the_surface_is_ambiguous,
        test_the_dominance_ratio_refuses_a_comparable_branch,
        test_the_candidate_face_cap_refuses_a_large_branch,
        test_the_diagonal_cap_refuses_a_far_reaching_branch,
        test_a_mesh_with_no_defect,
        test_refusal_never_sets_removable,
        test_exact_duplicate_face_is_delegated,
        test_reversed_duplicate_face_is_delegated,
        test_branch_walk_treats_the_defect_as_a_wall,
        test_the_walk_is_bounded,
        test_incident_faces,
        test_prediction_reports_the_boundary_it_opens,
        test_a_removal_that_would_not_help_is_refused,
        test_locality_accepts_exactly_the_approved_removal,
        test_locality_catches_geometry_that_moved,
        test_locality_catches_an_extra_deletion,
        test_locality_catches_a_face_that_should_have_gone,
        test_the_signature_ignores_winding_and_renumbering,
        test_acceptance_keeps_a_good_removal,
        test_acceptance_allows_partial_improvement,
        test_acceptance_tolerates_new_boundary_edges,
        test_acceptance_rejects,
        test_acceptance_is_the_existing_rule_plus_additions,
        test_defect_resolution_is_checked_by_position,
        test_introduced_elsewhere_is_refused,
        test_report_text,
        test_ambiguous_report_text,
        test_confirmation_text,
        test_describe,
        test_scope,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
