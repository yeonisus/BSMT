"""Offline tests for Milestone 3.4: repair analysis and readiness rules.

    python3 tests/test_repair.py

Covers the pure analysis - edge classification, boundary loop grouping,
component sizing, the readiness rule - which is where the decisions live. The
bmesh editing is exercised by the in-Blender acceptance script.
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
    package = types.ModuleType("bsmt_r")
    package.__path__ = [PACKAGE]
    sys.modules["bsmt_r"] = package
    geo = types.ModuleType("bsmt_r.geodesic")
    geo.__path__ = [os.path.join(PACKAGE, "geodesic")]
    sys.modules["bsmt_r.geodesic"] = geo
    spec = importlib.util.spec_from_file_location(
        "bsmt_r.geodesic.topology", os.path.join(PACKAGE, "geodesic",
                                                 "topology.py"))
    topology = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_r.geodesic.topology"] = topology
    spec.loader.exec_module(topology)
    spec = importlib.util.spec_from_file_location(
        "bsmt_r.repair", os.path.join(PACKAGE, "repair.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_r.repair"] = module
    spec.loader.exec_module(module)
    return module, topology


repair, topology = _load()


def grid(n=4, spacing=10.0):
    """A flat n x n triangulated patch: one component, one boundary loop."""
    xs = np.arange(n + 1, dtype=np.float64) * spacing
    gx, gy = np.meshgrid(xs, xs, indexing="ij")
    vertices = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    faces = []
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            b = (i + 1) * (n + 1) + j
            c = (i + 1) * (n + 1) + j + 1
            d = i * (n + 1) + j + 1
            faces.append([a, b, c])
            faces.append([a, c, d])
    return vertices, np.asarray(faces, dtype=np.int64)


def report(**kwargs):
    base = {"vertex_count": 157045, "triangle_count": 314086,
            "component_count": 1, "boundary_edge_count": 0,
            "nonmanifold_edge_count": 0, "degenerate_triangle_count": 0,
            "duplicate_vertex_count": 0}
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# edge classification
# ---------------------------------------------------------------------------

def test_edge_classification():
    print("\n[edges] boundary / interior / non-manifold")
    V, F = grid(4)
    edges = repair.classify_edges(F, V.shape[0])
    check("a flat patch has boundary edges",
          edges["boundary"].shape[0] == 16, str(edges["boundary"].shape))
    check("it has no non-manifold edges",
          edges["non_manifold"].shape[0] == 0)
    check("interior edges are the rest",
          edges["interior"].shape[0] > 0)
    total = (edges["boundary"].shape[0] + edges["interior"].shape[0]
             + edges["non_manifold"].shape[0])
    a, b, _c = topology.unique_edges(F, V.shape[0])
    check("every edge is classified exactly once", total == a.shape[0],
          "%d vs %d" % (total, a.shape[0]))

    # A fin: a third face on an INTERIOR edge (one that already has two
    # faces). Building it on a boundary edge would only make that edge
    # manifold, which is why the interior edge is chosen from the
    # classification rather than assumed.
    interior_edge = edges["interior"][0]
    fin_v = np.vstack([V, [[5.0, 5.0, 20.0]]])
    fin_f = np.vstack([F, [[interior_edge[0], interior_edge[1], V.shape[0]]]])
    fin_edges = repair.classify_edges(fin_f, fin_v.shape[0])
    check("a fin on an interior edge creates one non-manifold edge",
          fin_edges["non_manifold"].shape[0] == 1,
          str(fin_edges["non_manifold"]))
    check("the incident count is 3",
          int(fin_edges["non_manifold_counts"][0]) == 3)
    check("the non-manifold edge is the one the fin was built on",
          set(fin_edges["non_manifold"][0]) == set(interior_edge),
          "%s vs %s" % (fin_edges["non_manifold"][0], interior_edge))
    check("a fin on a BOUNDARY edge is not non-manifold",
          repair.classify_edges(
              np.vstack([F, [[edges["boundary"][0][0], edges["boundary"][0][1],
                              V.shape[0]]]]),
              fin_v.shape[0])["non_manifold"].shape[0] == 0)

    check("an empty mesh classifies cleanly",
          repair.classify_edges(np.zeros((0, 3), dtype=np.int64),
                                0)["boundary"].shape == (0, 2))


def test_duplicate_faces():
    print("\n[edges] duplicate faces are the safe non-manifold cause")
    V, F = grid(3)
    check("a clean mesh has no duplicates",
          repair.duplicate_faces(F).size == 0)

    doubled = np.vstack([F, F[2:3]])
    duplicates = repair.duplicate_faces(doubled)
    check("one repeated face is found", duplicates.size == 1, str(duplicates))
    check("the FIRST occurrence is kept",
          int(duplicates[0]) == doubled.shape[0] - 1, str(duplicates))

    # A different winding of the same vertex set is still the same face.
    rewound = np.vstack([F, F[2:3][:, [1, 0, 2]]])
    check("a reversed winding counts as a duplicate",
          repair.duplicate_faces(rewound).size == 1)

    tripled = np.vstack([F, F[1:2], F[1:2]])
    check("two extra copies give two duplicates",
          repair.duplicate_faces(tripled).size == 2)
    check("removing both extra copies restores the original face count",
          tripled.shape[0] - repair.duplicate_faces(tripled).size
          == F.shape[0],
          "%d - %d vs %d" % (tripled.shape[0],
                             repair.duplicate_faces(tripled).size,
                             F.shape[0]))

    # And the duplicate really does create non-manifold edges.
    edges = repair.classify_edges(doubled, V.shape[0])
    check("a duplicated face makes non-manifold edges",
          edges["non_manifold"].shape[0] > 0)


# ---------------------------------------------------------------------------
# boundary loops
# ---------------------------------------------------------------------------

def test_boundary_loops():
    print("\n[loops] boundary loops are grouped and measured")
    V, F = grid(4, spacing=10.0)
    loops = repair.boundary_loops(V, F)
    check("one patch gives one loop", len(loops) == 1, str(len(loops)))
    loop = loops[0]
    check("all 16 boundary edges are in it", loop["edge_count"] == 16)
    check("it is closed", loop["closed"])
    check("the perimeter is 4 x 40 mm",
          abs(loop["perimeter_mm"] - 160.0) < 1e-9,
          "%.4f" % loop["perimeter_mm"])
    check("the bbox is 40 x 40 x 0",
          abs(loop["bbox_mm"][0] - 40.0) < 1e-9
          and abs(loop["bbox_mm"][2]) < 1e-9, str(loop["bbox_mm"]))
    check("it has an id", loop["loop_id"] == 1)
    check("the label is readable",
          "16 edges" in repair.describe_loop(loop), repair.describe_loop(loop))

    # Two separate patches -> two separate loops, largest perimeter first.
    V2 = V + np.array([1000.0, 0.0, 0.0])
    both_v = np.vstack([V, V2])
    both_f = np.vstack([F, F + V.shape[0]])
    small_v, small_f = grid(1, spacing=5.0)
    both_v = np.vstack([both_v, small_v + np.array([0, 2000.0, 0])])
    both_f = np.vstack([both_f, small_f + (2 * V.shape[0])])
    loops = repair.boundary_loops(both_v, both_f)
    check("three patches give three loops", len(loops) == 3, str(len(loops)))
    check("they are sorted by perimeter, largest first",
          loops[0]["perimeter_mm"] >= loops[1]["perimeter_mm"]
          >= loops[2]["perimeter_mm"],
          str([round(l["perimeter_mm"], 1) for l in loops]))
    check("ids are assigned in that order",
          [l["loop_id"] for l in loops] == [1, 2, 3])
    check("the smallest is the 5 mm patch",
          abs(loops[2]["perimeter_mm"] - 20.0) < 1e-9,
          "%.3f" % loops[2]["perimeter_mm"])

    check("a closed surface has no loops",
          repair.boundary_loops(*closed_tetrahedron()) == [])


def closed_tetrahedron():
    V = np.array([[0., 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]])
    F = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64)
    return V, F


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def test_component_rows():
    print("\n[components] sizes, shares, and what counts as small")
    rows = repair.component_rows([349812, 412])
    check("sorted largest first", rows[0]["triangle_count"] == 349812)
    check("the largest is marked", rows[0]["is_largest"])
    check("the largest is never 'small'", not rows[0]["is_small"])
    check("the tiny one is offered", rows[1]["is_small"])
    check("shares are computed",
          abs(rows[0]["percent"] - 99.88) < 0.05,
          "%.2f" % rows[0]["percent"])
    check("the label matches the brief's example",
          "Component 1: 349,812 triangles" in repair.describe_component(rows[0]),
          repair.describe_component(rows[0]))

    # Being smaller is NOT enough to be offered for removal.
    rows = repair.component_rows([200000, 150000])
    check("a large second component is NOT flagged small",
          not rows[1]["is_small"], str(rows[1]))
    check("but it is still listed", len(rows) == 2)

    rows = repair.component_rows([1000])
    check("a single component is never small", not rows[0]["is_small"])
    check("and is the largest", rows[0]["is_largest"])


# ---------------------------------------------------------------------------
# readiness (sect. 7)
# ---------------------------------------------------------------------------

def test_readiness_rule():
    print("\n[readiness] only non-manifold BLOCKS")
    ideal = repair.readiness(report())
    check("a clean mesh is READY", ideal["ready"])
    check("and is ideal", ideal["ideal"])
    check("with no blockers or preferences",
          not ideal["blockers"] and not ideal["preferences"])

    blocked = repair.readiness(report(nonmanifold_edge_count=7))
    check("non-manifold blocks", not blocked["ready"])
    check("the count is named", "7 non-manifold" in blocked["blockers"][0])
    check("and manual cleanup is mentioned",
          "manual cleanup" in blocked["blockers"][0])

    empty = repair.readiness(report(triangle_count=0))
    check("an empty mesh blocks", not empty["ready"])

    # Preferences must NOT block.
    multi = repair.readiness(report(component_count=2))
    check("multiple components do NOT block", multi["ready"])
    check("but are a stated preference", len(multi["preferences"]) == 1)
    check("and it says measurement still works",
          "still works" in multi["preferences"][0])
    check("it is not 'ideal'", not multi["ideal"])
    check("the headline says so", "caveats" in multi["headline"])

    open_mesh = repair.readiness(report(boundary_edge_count=15))
    check("boundary edges do NOT block", open_mesh["ready"])
    check("but are a stated preference", len(open_mesh["preferences"]) == 1)

    # sect. 7: a few degenerate or coincident findings must not reject a scan.
    messy = repair.readiness(report(degenerate_triangle_count=4,
                                    duplicate_vertex_count=9))
    check("degenerate triangles do NOT block", messy["ready"])
    check("coincident vertices do NOT block", messy["ready"])
    check("they are warnings only", len(messy["warnings"]) == 2)
    check("and are not preferences either", messy["preferences"] == [])

    real = repair.readiness(report(triangle_count=2783068, component_count=2,
                                   boundary_edge_count=15,
                                   nonmanifold_edge_count=7))
    check("the real dense OBJ is NOT ready", not real["ready"])
    lines = repair.readiness_lines(real)
    check("its report names the blocker",
          any("BLOCKED" in line for line in lines), str(lines))
    check("and both preferences",
          sum(1 for line in lines if "prefer:" in line) == 2)


def test_repair_record():
    print("\n[provenance] what a repair changed")
    before = report(triangle_count=2218, nonmanifold_edge_count=2,
                    boundary_edge_count=16, component_count=2)
    after = report(triangle_count=2206, nonmanifold_edge_count=0,
                   boundary_edge_count=6, component_count=1)
    record = repair.repair_record("Fill boundary loop", before, after,
                                  "loop 1, 4 faces")
    check("the action is recorded", record["action"] == "Fill boundary loop")
    check("the detail is recorded", "loop 1" in record["detail"])
    check("the triangle delta is signed", record["triangle_delta"] == -12)
    check("non-manifold before/after", record["nonmanifold_before"] == 2
          and record["nonmanifold_after"] == 0)
    lines = repair.repair_lines(record)
    text = "\n".join(lines)
    check("the log shows the transition", "2 -> 0" in text, text)
    check("and the triangle change", "-12" in text)
    check("and the component change", "2 -> 1" in text)


def test_no_global_cleanup_anywhere():
    print("\n[scope] no global cleanup path exists")
    source = open(os.path.join(PACKAGE, "meshrepair.py")).read()
    check("no fill-everything helper",
          "holes_fill(bm, edges=bm.edges" not in source)
    check("remove_doubles is never called on every vertex",
          "remove_doubles(bm, verts=bm.verts" not in source)
    check("the weld is scoped to the reported edges",
          "wanted = set(int(value) for value in pairs.ravel())" in source)
    check("and says so",
          "NOT a global merge-by-distance" in source)
    check("the reason is documented in repair.py",
          "anatomically distinct" in open(
              os.path.join(PACKAGE, "repair.py")).read())
    check("and the consequence is named",
          "systematically SHORT" in open(
              os.path.join(PACKAGE, "repair.py")).read())

    operators = open(os.path.join(PACKAGE, "operators.py")).read()
    check("repairs refuse anything but a measurement copy",
          "is not a measurement copy" in operators)
    check("and every repair backs up first",
          "meshrepair.make_backup(obj)" in operators)


# ---------------------------------------------------------------------------
# automatic local repair (Milestone 3.5)
# ---------------------------------------------------------------------------

def mean_edge(vertices, faces):
    """Mean edge length, as the operator passes it to the size limit."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    pairs = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]],
                            faces[:, [2, 0]]), axis=0)
    return float(np.linalg.norm(vertices[pairs[:, 0]] - vertices[pairs[:, 1]],
                                axis=1).mean())


def fin_fixture(scale=1.0):
    """A grid with one extra face on an INTERIOR edge."""
    V, F = grid(6, spacing=scale)
    interior = repair.classify_edges(F, V.shape[0])["interior"][0]
    apex = np.array([[0.5 * scale, 0.5 * scale, 0.4 * scale]])
    return (np.vstack([V, apex]),
            np.vstack([F, [[interior[0], interior[1], V.shape[0]]]]))


def fan_fixture(spokes=3, scale=1.0):
    """Several extra faces meeting at one INTERIOR central vertex.

    The spoke edges are chosen from edges that currently have exactly two
    incident faces. Building a fan on a boundary edge would only make that
    edge manifold, giving a fixture with no defect at all - which is how an
    earlier version of this fixture silently tested nothing.
    """
    V, F = grid(6, spacing=scale)
    centre = 3 * 7 + 3                      # a vertex in the grid interior
    interior = repair.classify_edges(F, V.shape[0])["interior"]
    spoke_edges = [edge for edge in interior if centre in (int(edge[0]),
                                                           int(edge[1]))]
    assert len(spoke_edges) >= spokes, "fixture needs more interior spokes"
    apex = np.array([[3.3 * scale, 3.3 * scale, 0.3 * scale]])
    extra = [[int(edge[0]), int(edge[1]), V.shape[0]]
             for edge in spoke_edges[:spokes]]
    return np.vstack([V, apex]), np.vstack([F, np.asarray(extra, np.int64)])


def test_no_regions_on_a_clean_mesh():
    print("\n[auto] a clean mesh yields nothing to repair")
    V, F = grid(6)
    check("no non-manifold regions", repair.non_manifold_regions(V, F) == [])
    check("a closed surface has none either",
          repair.non_manifold_regions(*closed_tetrahedron()) == [])


def test_regions_are_clustered_not_counted_individually():
    print("\n[auto] non-manifold edges are grouped into local regions")
    V, F = fan_fixture(spokes=3)
    regions = repair.non_manifold_regions(V, F)
    check("one artefact is ONE region, not several", len(regions) == 1,
          str(len(regions)))
    region = regions[0]
    check("it holds every non-manifold edge",
          region["edge_count"]
          == repair.classify_edges(F, V.shape[0])["non_manifold"].shape[0])
    for key in ("region_id", "edge_count", "face_count", "vertex_count",
                "bbox_diagonal_mm", "area_mm2", "center_mm"):
        check("the region reports %r" % key, key in region)
    check("it has a readable description",
          "non-manifold edge(s)" in repair.describe_region(region))

    # Two artefacts far apart must stay two regions.
    V2, F2 = fan_fixture(spokes=2)
    check("the two-spoke fixture really is defective",
          repair.classify_edges(F2, V2.shape[0])["non_manifold"].shape[0] == 2)
    both_v = np.vstack([V2, V2 + np.array([500.0, 0.0, 0.0])])
    both_f = np.vstack([F2, F2 + V2.shape[0]])
    check("two separate artefacts give two regions",
          len(repair.non_manifold_regions(both_v, both_f)) == 2,
          str(len(repair.non_manifold_regions(both_v, both_f))))


def test_classification():
    print("\n[auto] defect classification, and refusing when unclear")
    V, F = fin_fixture()
    region = repair.non_manifold_regions(V, F)[0]
    check("a fin is classified FIN",
          repair.classify_region(V, F, region,
                                 mean_edge(V, F))[0] == repair.DEFECT_FIN)

    V, F = grid(6)
    doubled = np.vstack([F, F[3:4][:, [1, 0, 2]]])
    region = repair.non_manifold_regions(V, doubled)[0]
    check("a reversed duplicate is classified DUPLICATE",
          repair.classify_region(V, doubled, region,
                                 mean_edge(V, doubled))[0]
          == repair.DEFECT_DUPLICATE)

    V, F = fan_fixture(spokes=3)
    region = repair.non_manifold_regions(V, F)[0]
    kind, detail = repair.classify_region(V, F, region, mean_edge(V, F))
    check("a fan is classified", kind in (repair.DEFECT_FAN, repair.DEFECT_FIN),
          "%s: %s" % (kind, detail))

    oversized = dict(region)
    oversized["face_count"] = repair.MAX_REGION_FACES + 1
    check("too many faces is refused",
          repair.classify_region(V, F, oversized,
                                 mean_edge(V, F))[0] == repair.DEFECT_TOO_LARGE)
    huge = dict(region)
    huge["bbox_diagonal_mm"] = 10000.0
    check("too large is refused",
          repair.classify_region(V, F, huge,
                                 mean_edge(V, F))[0] == repair.DEFECT_TOO_LARGE)
    check("the refusal explains itself",
          "exceeds" in repair.classify_region(V, F, huge, mean_edge(V, F))[1])
    check("every classification has a label",
          all(key in repair.DEFECT_LABELS for key in
              (repair.DEFECT_FIN, repair.DEFECT_FAN, repair.DEFECT_DUPLICATE,
               repair.DEFECT_FLAP, repair.DEFECT_AMBIGUOUS,
               repair.DEFECT_TOO_LARGE)))


def test_adaptive_size_limit():
    print("\n[auto] the local-artefact size limit scales with the mesh")
    check("a fine mesh uses the absolute floor",
          repair.region_diagonal_limit(1.0) == repair.MAX_REGION_DIAGONAL_MM)
    check("a coarse mesh raises it",
          repair.region_diagonal_limit(12.58) > repair.MAX_REGION_DIAGONAL_MM)
    check("it is a multiple of the mean edge",
          abs(repair.region_diagonal_limit(10.0)
              - repair.REGION_EDGE_FACTOR * 10.0) < 1e-9)
    check("a zero mean edge falls back to the floor",
          repair.region_diagonal_limit(0.0) == repair.MAX_REGION_DIAGONAL_MM)
    check("a nonsense mean edge does not raise",
          repair.region_diagonal_limit("?") == repair.MAX_REGION_DIAGONAL_MM)

    # The same artefact on a coarse mesh must not be refused for being wide.
    V, F = fan_fixture(spokes=3, scale=30.0)
    region = repair.non_manifold_regions(V, F)[0]
    check("a coarse-mesh fan is refused at the absolute limit",
          repair.classify_region(V, F, region, 0.0)[0]
          == repair.DEFECT_TOO_LARGE)
    check("but accepted once the mesh's own scale is known",
          repair.classify_region(V, F, region, 30.0)[0]
          != repair.DEFECT_TOO_LARGE)


def test_repair_plan_is_minimal_and_deterministic():
    print("\n[auto] the plan removes the minimal redundant faces")
    V, F = fin_fixture()
    region = repair.non_manifold_regions(V, F)[0]
    plan = repair.plan_region_repair(V, F, region,
                                     mean_edge_mm=mean_edge(V, F))
    check("one face removed", len(plan["remove_faces"]) == 1,
          str(plan["remove_faces"]))
    check("it is the fin, not the surface",
          plan["remove_faces"][0] == F.shape[0] - 1)
    check("the region is resolved", plan["resolved"])
    check("the plan is deterministic",
          repair.plan_region_repair(
              V, F, region, mean_edge_mm=mean_edge(V, F))["remove_faces"]
          == plan["remove_faces"])
    check("nothing was mutated",
          repair.non_manifold_regions(V, F)[0]["edge_count"]
          == region["edge_count"])

    V, F = grid(6)
    doubled = np.vstack([F, F[3:4][:, [1, 0, 2]]])
    region = repair.non_manifold_regions(V, doubled)[0]
    plan = repair.plan_region_repair(V, doubled, region,
                                     mean_edge_mm=mean_edge(V, doubled))
    check("a duplicate is resolved by removing one face",
          len(plan["remove_faces"]) == 1)
    check("the LATER copy is removed, keeping the original",
          plan["remove_faces"][0] == doubled.shape[0] - 1,
          str(plan["remove_faces"]))

    V, F = fan_fixture(spokes=3)
    region = repair.non_manifold_regions(V, F)[0]
    plan = repair.plan_region_repair(V, F, region,
                                     mean_edge_mm=mean_edge(V, F))
    check("a fan is resolved", plan["resolved"], str(plan["nonmanifold_after"]))
    check("it removes no more faces than the artefact has",
          len(plan["remove_faces"]) <= region["face_count"])
    check("well under the hard cap",
          len(plan["remove_faces"]) <= repair.MAX_FACES_REMOVED)
    check("the original surface survives",
          F.shape[0] - len(plan["remove_faces"]) >= grid(6)[1].shape[0] - 1)

    refused = repair.plan_region_repair(V, F,
                                        dict(region, bbox_diagonal_mm=1e9),
                                        mean_edge_mm=mean_edge(V, F))
    check("a refused plan removes nothing", refused["remove_faces"] == [])
    check("and is flagged refused", refused["refused"])
    check("and still has a consistent shape",
          "resolved" in refused and "steps" in refused)


def test_patch_prediction():
    print("\n[auto] the hole a removal would leave is predicted first")
    V, F = fin_fixture()
    region = repair.non_manifold_regions(V, F)[0]
    plan = repair.plan_region_repair(V, F, region,
                                     mean_edge_mm=mean_edge(V, F))
    patch = repair.predict_patch(V, F, plan["remove_faces"])
    check("removing a fin leaves no new boundary",
          patch["new_boundary_edges"] == 0, str(patch))
    check("which is trivially fillable", patch["fillable"])

    # Removing a big chunk must be reported as unfillable.
    V, F = grid(8)
    patch = repair.predict_patch(V, F, list(range(0, 40)))
    check("a large removal is reported unfillable", not patch["fillable"],
          patch["reason"])
    check("and says why", "limit" in patch["reason"])


def test_tiny_boundary_limits():
    print("\n[auto] only genuinely tiny boundaries qualify")
    ok, why = repair.is_tiny_boundary(
        {"edge_count": 3, "perimeter_mm": 2.0, "bbox_diagonal_mm": 1.0})
    check("a 3-edge 2 mm hole is tiny", ok, why)

    for loop, expect in (
        ({"edge_count": 40, "perimeter_mm": 2.0, "bbox_diagonal_mm": 1.0},
         "edges"),
        ({"edge_count": 3, "perimeter_mm": 500.0, "bbox_diagonal_mm": 1.0},
         "perimeter"),
        ({"edge_count": 3, "perimeter_mm": 2.0, "bbox_diagonal_mm": 300.0},
         "across"),
    ):
        ok, why = repair.is_tiny_boundary(loop)
        check("a loop too big by %s is refused" % expect, not ok, why)
        check("  and says which limit", expect in why, why)

    # A crop plane must never qualify.
    crop = {"edge_count": 120, "perimeter_mm": 800.0, "bbox_diagonal_mm": 260.0}
    check("a body crop opening is never tiny",
          not repair.is_tiny_boundary(crop)[0])


def test_acceptance_criteria():
    print("\n[auto] a repair is kept only if every criterion holds")
    before = report(triangle_count=349999, nonmanifold_edge_count=7,
                    boundary_edge_count=14, component_count=2)
    good = report(triangle_count=349995, nonmanifold_edge_count=0,
                  boundary_edge_count=16, component_count=2)
    ok, why = repair.accept_repair(before, good, True)
    check("a good repair is accepted", ok, str(why))

    ok, why = repair.accept_repair(before, dict(good,
                                                nonmanifold_edge_count=7), True)
    check("no improvement is rejected", not ok)
    check("  and says so", "did not decrease" in why[0])

    ok, why = repair.accept_repair(before, dict(good,
                                                nonmanifold_edge_count=9), True)
    check("getting worse is rejected", not ok)

    ok, why = repair.accept_repair(before, dict(good,
                                                boundary_edge_count=400), True)
    check("a large new boundary is rejected", not ok)
    check("  and counts it", "new boundary" in why[0])

    ok, why = repair.accept_repair(before, dict(good, component_count=9), True)
    check("splitting the mesh is rejected", not ok)

    ok, why = repair.accept_repair(before, dict(good, triangle_count=0), True)
    check("an emptied mesh is rejected", not ok)

    ok, why = repair.accept_repair(before, good, False)
    check("losing the texture is rejected", not ok)
    check("  and names it", "UV map" in why[0])

    ok, why = repair.accept_repair(before, dict(good, component_count=1), True)
    check("FEWER components is fine", ok, str(why))


def test_change_summary():
    print("\n[auto] the geometric change is quantified")
    before = report(vertex_count=175007, triangle_count=349999)
    after = report(vertex_count=175007, triangle_count=349995)
    summary = repair.change_summary(before, after, [1.2, 0.8, 1.1], 0.94)
    check("face delta is signed", summary["face_delta"] == -4)
    check("vertex delta is reported", summary["vertex_delta"] == 0)
    check("the affected dimension is the largest bbox side",
          abs(summary["max_region_dimension_mm"] - 1.2) < 1e-9)
    check("the affected area is carried", abs(summary["affected_area_mm2"]
                                              - 0.94) < 1e-9)
    text = "\n".join(repair.change_lines(summary))
    check("the report shows the face change", "-4" in text, text)
    check("and the affected region size", "1.20 mm across" in text, text)


def main():
    print("BSMT Milestone 3.4 - mesh repair tests")
    print("  python : %s" % sys.version.split()[0])
    print("  numpy  : %s" % np.__version__)
    for test in (
        test_edge_classification,
        test_duplicate_faces,
        test_boundary_loops,
        test_component_rows,
        test_readiness_rule,
        test_repair_record,
        test_no_global_cleanup_anywhere,
        test_no_regions_on_a_clean_mesh,
        test_regions_are_clustered_not_counted_individually,
        test_classification,
        test_adaptive_size_limit,
        test_repair_plan_is_minimal_and_deterministic,
        test_patch_prediction,
        test_tiny_boundary_limits,
        test_acceptance_criteria,
        test_change_summary,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
