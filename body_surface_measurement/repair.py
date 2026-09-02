"""Topology repair analysis and readiness rules (Milestone 3.4). Pure numpy.

No bpy, so the analysis and the readiness decision are testable outside
Blender. The mesh editing lives in ``meshrepair.py``.

Everything here is *diagnosis*. It finds and describes defects, it does not
fix them: every repair in BSMT is an explicit, user-chosen action on a named
region, never a global sweep. On a human scan a global cleanup silently fuses
anatomically distinct surfaces that happen to touch - arm to torso, finger to
finger, garment to skin - and a fused surface produces a confidently wrong,
systematically SHORT geodesic.

Edge classification reuses ``geodesic.topology.unique_edges``, so the counts
shown here and the counts in the topology report can never disagree.
"""

import numpy as np

from .geodesic.topology import unique_edges

#: A component holding at most this fraction of the mesh's triangles is
#: *offered* for removal. Never removed automatically: being small is not
#: evidence of being unwanted, and hair, a garment or a held object can be a
#: legitimate small component.
SMALL_COMPONENT_FRACTION = 0.02

READY = 'READY'
NOT_READY = 'NOT_READY'


class RepairError(Exception):
    """A repair request cannot be carried out as asked."""


# ---------------------------------------------------------------------------
# edge classification
# ---------------------------------------------------------------------------

def classify_edges(faces, vertex_count):
    """Split a triangle soup's edges by how many triangles use each.

    Returns a dict of (n, 2) int64 arrays: `boundary` (exactly one incident
    triangle), `non_manifold` (three or more) and `interior` (exactly two).
    """
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        empty = np.zeros((0, 2), dtype=np.int64)
        return {"boundary": empty, "non_manifold": empty, "interior": empty}
    edge_a, edge_b, counts = unique_edges(faces, int(vertex_count))
    edges = np.stack([edge_a, edge_b], axis=1)
    return {
        "boundary": edges[counts == 1],
        "interior": edges[counts == 2],
        "non_manifold": edges[counts >= 3],
        "non_manifold_counts": counts[counts >= 3],
    }


def duplicate_faces(faces):
    """Indices of triangles that repeat another triangle's vertex set.

    A duplicated face is the most common cause of a non-manifold edge and the
    safest thing to remove: it adds no surface, only a second copy of one that
    is already there. The FIRST occurrence of each set is kept.
    """
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return np.zeros(0, dtype=np.int64)
    keys = np.sort(faces, axis=1)
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
    ordered = keys[order]
    same = np.all(ordered[1:] == ordered[:-1], axis=1)
    duplicates = order[1:][same]
    return np.sort(duplicates)


# ---------------------------------------------------------------------------
# boundary loops
# ---------------------------------------------------------------------------

def boundary_loops(vertices, faces, max_loops=200):
    """Group boundary edges into connected loops, largest perimeter first.

    Each loop reports its edge count, perimeter and bounding-box size, which is
    what tells a researcher whether a hole is a scanner dropout worth filling
    or the open bottom of a cropped scan that must be left alone.

    Open chains are reported too, not just closed cycles: a boundary that is
    not a clean cycle is exactly the kind of thing worth seeing before
    deciding to fill anything.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    edges = classify_edges(faces, vertices.shape[0])["boundary"]
    if edges.shape[0] == 0:
        return []

    adjacency = {}
    for index in range(edges.shape[0]):
        a = int(edges[index, 0])
        b = int(edges[index, 1])
        adjacency.setdefault(a, []).append((b, index))
        adjacency.setdefault(b, []).append((a, index))

    seen_edges = set()
    loops = []
    for start_index in range(edges.shape[0]):
        if start_index in seen_edges or len(loops) >= max_loops:
            continue
        chain_edges = []
        chain_vertices = []
        stack = [start_index]
        visited_vertices = set()
        while stack:
            edge_index = stack.pop()
            if edge_index in seen_edges:
                continue
            seen_edges.add(edge_index)
            chain_edges.append(edge_index)
            for endpoint in (int(edges[edge_index, 0]), int(edges[edge_index, 1])):
                if endpoint not in visited_vertices:
                    visited_vertices.add(endpoint)
                    chain_vertices.append(endpoint)
                for _other, neighbour_edge in adjacency.get(endpoint, ()):
                    if neighbour_edge not in seen_edges:
                        stack.append(neighbour_edge)

        chain = np.asarray(chain_edges, dtype=np.int64)
        segment = vertices[edges[chain, 0]] - vertices[edges[chain, 1]]
        perimeter = float(np.linalg.norm(segment, axis=1).sum())
        loop_vertices = np.asarray(sorted(chain_vertices), dtype=np.int64)
        points = vertices[loop_vertices]
        extent = points.max(axis=0) - points.min(axis=0)
        # A clean cycle has one boundary edge per boundary vertex.
        closed = bool(chain.shape[0] == loop_vertices.shape[0])
        loops.append({
            "edge_indices": chain,
            "edges": edges[chain],
            "vertex_indices": loop_vertices,
            "edge_count": int(chain.shape[0]),
            "vertex_count": int(loop_vertices.shape[0]),
            "perimeter_mm": perimeter,
            "bbox_mm": [float(v) for v in extent],
            "bbox_diagonal_mm": float(np.linalg.norm(extent)),
            "closed": closed,
            "center_mm": [float(v) for v in points.mean(axis=0)],
        })

    loops.sort(key=lambda entry: entry["perimeter_mm"], reverse=True)
    for position, loop in enumerate(loops):
        loop["loop_id"] = position + 1
    return loops


def describe_loop(loop):
    """One line for the UI list."""
    return "L%d  %d edges  %.1f mm  %.0fx%.0fx%.0f mm%s" % (
        loop["loop_id"], loop["edge_count"], loop["perimeter_mm"],
        loop["bbox_mm"][0], loop["bbox_mm"][1], loop["bbox_mm"][2],
        "" if loop["closed"] else "  (open chain)",
    )


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def component_rows(triangle_counts, vertex_counts=None,
                   small_fraction=SMALL_COMPONENT_FRACTION):
    """Per-component sizes and relative share, largest first."""
    counts = [int(value) for value in triangle_counts]
    total = sum(counts) or 1
    rows = []
    for index, count in enumerate(counts):
        fraction = count / float(total)
        rows.append({
            "index": index + 1,
            "triangle_count": count,
            "vertex_count": (int(vertex_counts[index])
                             if vertex_counts is not None
                             and index < len(vertex_counts) else 0),
            "fraction": fraction,
            "percent": 100.0 * fraction,
            "is_small": fraction <= small_fraction and len(counts) > 1,
            "is_largest": False,
        })
    rows.sort(key=lambda row: row["triangle_count"], reverse=True)
    if rows:
        rows[0]["is_largest"] = True
        rows[0]["is_small"] = False
    return rows


def describe_component(row):
    return "Component %d: %s triangles (%.2f%%)%s" % (
        row["index"], "{:,}".format(row["triangle_count"]), row["percent"],
        "  [largest]" if row["is_largest"] else
        ("  [small]" if row["is_small"] else ""),
    )


# ---------------------------------------------------------------------------
# measurement readiness (sect. 7)
# ---------------------------------------------------------------------------

def readiness(report):
    """Is this mesh ready for exact geodesic measurement?

    The rule is deliberately narrow. Only non-manifold topology BLOCKS, because
    only that has been shown to break the solver. Multiple components and open
    boundaries are *preferences*: a landmark pair on one good component is
    perfectly measurable on a multi-component scan, and a cropped scan with an
    open bottom is normal. Degenerate triangles and coincident vertices stay
    warnings unless they are shown to break the solver - refusing a whole scan
    over a handful of them would block real work for no demonstrated reason.
    """
    non_manifold = int(report.get("nonmanifold_edge_count", 0) or 0)
    components = int(report.get("component_count", 0) or 0)
    boundary = int(report.get("boundary_edge_count", 0) or 0)
    degenerate = int(report.get("degenerate_triangle_count", 0) or 0)
    duplicates = int(report.get("duplicate_vertex_count", 0) or 0)
    triangles = int(report.get("triangle_count", 0) or 0)

    blockers = []
    if non_manifold > 0:
        blockers.append(
            "%d non-manifold edge(s) - the exact solver is not safe on this "
            "mesh. Try Remove Duplicate Faces, then the local weld; if "
            "neither clears them, manual cleanup is required." % non_manifold
        )
    if triangles <= 0:
        blockers.append("the mesh has no triangles")
    elif components < 1:
        blockers.append("no connected component was found")

    preferences = []
    if components > 1:
        preferences.append(
            "%d connected components - prefer 1. Measurement still works "
            "within a component; a cross-component pair is refused "
            "individually." % components
        )
    if boundary > 0:
        preferences.append(
            "%d boundary edge(s) - prefer 0. A geodesic near a hole can take a "
            "long detour that looks plausible but is an artefact." % boundary
        )

    warnings = []
    if degenerate > 0:
        warnings.append("%d degenerate (zero-area) triangle(s)" % degenerate)
    if duplicates > 0:
        warnings.append("%d exactly coincident vertex/vertices" % duplicates)

    status = NOT_READY if blockers else READY
    if status == READY:
        headline = ("Measurement ready" if not preferences
                    else "Measurement ready, with caveats")
    else:
        headline = "Not measurement ready"

    return {
        "status": status,
        "ready": status == READY,
        "headline": headline,
        "blockers": blockers,
        "preferences": preferences,
        "warnings": warnings,
        "ideal": status == READY and not preferences,
    }


def readiness_lines(result):
    lines = [result["headline"]]
    for entry in result["blockers"]:
        lines.append("  BLOCKED: %s" % entry)
    for entry in result["preferences"]:
        lines.append("  prefer:  %s" % entry)
    for entry in result["warnings"]:
        lines.append("  note:    %s" % entry)
    if result["ideal"]:
        lines.append("  1 component, 0 boundary edges, 0 non-manifold edges")
    return lines


# ---------------------------------------------------------------------------
# repair provenance
# ---------------------------------------------------------------------------

def repair_record(action, before, after, detail=""):
    """What one repair changed, for the provenance log."""
    def delta(key):
        return (int(after.get(key, 0) or 0) - int(before.get(key, 0) or 0))

    return {
        "action": action,
        "detail": detail,
        "triangles_before": int(before.get("triangle_count", 0) or 0),
        "triangles_after": int(after.get("triangle_count", 0) or 0),
        "triangle_delta": delta("triangle_count"),
        "nonmanifold_before": int(before.get("nonmanifold_edge_count", 0) or 0),
        "nonmanifold_after": int(after.get("nonmanifold_edge_count", 0) or 0),
        "boundary_before": int(before.get("boundary_edge_count", 0) or 0),
        "boundary_after": int(after.get("boundary_edge_count", 0) or 0),
        "components_before": int(before.get("component_count", 0) or 0),
        "components_after": int(after.get("component_count", 0) or 0),
    }


def repair_lines(record):
    return [
        "%s%s" % (record["action"],
                  (" - " + record["detail"]) if record["detail"] else ""),
        "  triangles       %s -> %s (%+d)" % (
            "{:,}".format(record["triangles_before"]),
            "{:,}".format(record["triangles_after"]),
            record["triangle_delta"]),
        "  non-manifold    %d -> %d" % (record["nonmanifold_before"],
                                        record["nonmanifold_after"]),
        "  boundary edges  %d -> %d" % (record["boundary_before"],
                                        record["boundary_after"]),
        "  components      %d -> %d" % (record["components_before"],
                                        record["components_after"]),
    ]
