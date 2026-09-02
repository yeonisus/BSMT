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

# --- automatic local repair thresholds (Milestone 3.5) ---------------------
#
# These bound what "small localised artefact" means. They exist as named
# constants rather than magic numbers because they are the entire difference
# between repairing a scanner spike and quietly deleting anatomy. A region
# that exceeds any of them is REFUSED, not attempted: on a human scan the
# safe failure is "we did not touch it", never "we did our best".
#
# Sized against the real Design X measurement copy, whose 7 non-manifold
# edges sit in one star/fan around a single vertex at roughly millimetre
# scale, and whose 14 boundary edges form ~7 open chains of ~1 mm.

#: A non-manifold region involving more faces than this is not a local
#: artefact and is refused.
MAX_REGION_FACES = 64
#: Nor is one whose bounding box is larger than this across. The limit is
#: ADAPTIVE: what makes a defect "local" is spanning a handful of triangles,
#: and how many millimetres that is depends on the mesh's own resolution. A
#: fan around one vertex on a 4 mm-edge body scan spans ~20 mm; the same fan
#: on a coarse 13 mm-edge mesh spans ~60 mm and is no less local. The
#: absolute value below is a floor, raised to REGION_EDGE_FACTOR x the mean
#: edge length when the mesh is coarser than that.
MAX_REGION_DIAGONAL_MM = 20.0
REGION_EDGE_FACTOR = 6.0


def region_diagonal_limit(mean_edge_mm=0.0):
    """The largest a region may be and still count as a local artefact."""
    try:
        mean_edge = float(mean_edge_mm or 0.0)
    except (TypeError, ValueError):
        mean_edge = 0.0
    return max(MAX_REGION_DIAGONAL_MM, REGION_EDGE_FACTOR * mean_edge)
#: Hard cap on faces an automatic repair may remove from one region.
MAX_FACES_REMOVED = 32

#: A hole left by an automatic repair is filled only if it is this small.
MAX_PATCH_EDGES = 32
MAX_PATCH_PERIMETER_MM = 60.0
MAX_PATCH_DIAGONAL_MM = 20.0

#: "Tiny boundary" for the separate automatic boundary pass. Deliberately
#: much tighter than the patch limits: this pass runs over boundaries the user
#: did not point at, so it must never reach a crop plane, a neck cut or any
#: anatomically meaningful opening.
TINY_BOUNDARY_EDGES = 12
TINY_BOUNDARY_PERIMETER_MM = 12.0
TINY_BOUNDARY_DIAGONAL_MM = 5.0

# defect classifications
DEFECT_DUPLICATE = 'DUPLICATE_FACES'
DEFECT_FIN = 'FIN'
DEFECT_FAN = 'FAN'
DEFECT_FLAP = 'LOCAL_FLAP'
DEFECT_AMBIGUOUS = 'AMBIGUOUS'
DEFECT_TOO_LARGE = 'TOO_LARGE'

DEFECT_LABELS = {
    DEFECT_DUPLICATE: "duplicate / reversed faces",
    DEFECT_FIN: "fin - a face hanging off an edge by a dangling vertex",
    DEFECT_FAN: "fan - excess faces around one central vertex",
    DEFECT_FLAP: "local flap - a small redundant patch",
    DEFECT_AMBIGUOUS: "ambiguous - not repaired automatically",
    DEFECT_TOO_LARGE: "too large to treat as a local artefact",
}


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


# ---------------------------------------------------------------------------
# automatic local non-manifold repair (Milestone 3.5)
# ---------------------------------------------------------------------------
#
# Everything below computes a PLAN. It decides which faces an automatic repair
# would remove and predicts the resulting topology, without touching a mesh.
# ``meshrepair.py`` executes the plan transactionally and reverts it if the
# measured outcome does not match.


def _edge_key(a, b):
    return (int(a), int(b)) if a < b else (int(b), int(a))


def edge_incidence(faces):
    """Map every undirected edge to the faces using it. Pure python dict.

    Built once and then decremented as faces are removed, so a repair plan
    never has to re-scan the whole mesh per step.
    """
    incidence = {}
    faces = np.asarray(faces, dtype=np.int64)
    for index in range(faces.shape[0]):
        a, b, c = faces[index]
        for key in (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a)):
            incidence.setdefault(key, []).append(index)
    return incidence


def non_manifold_regions(vertices, faces, max_regions=64):
    """Group non-manifold edges into connected local regions.

    Two non-manifold edges belong to the same region when they share a vertex.
    Reporting them individually would be misleading: the real scan's seven
    edges are one artefact around one vertex, not seven separate problems.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    edges = classify_edges(faces, vertices.shape[0])["non_manifold"]
    if edges.shape[0] == 0:
        return []

    # Union-find over non-manifold edges, joined through shared vertices.
    parent = list(range(edges.shape[0]))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    by_vertex = {}
    for index in range(edges.shape[0]):
        for vertex in (int(edges[index, 0]), int(edges[index, 1])):
            if vertex in by_vertex:
                union(index, by_vertex[vertex])
            else:
                by_vertex[vertex] = index

    groups = {}
    for index in range(edges.shape[0]):
        groups.setdefault(find(index), []).append(index)

    incidence = edge_incidence(faces)
    regions = []
    for members in groups.values():
        member_edges = edges[np.asarray(members, dtype=np.int64)]
        face_set = set()
        for a, b in member_edges:
            face_set.update(incidence.get(_edge_key(a, b), ()))
        face_indices = np.asarray(sorted(face_set), dtype=np.int64)
        vertex_indices = np.unique(faces[face_indices]) if face_indices.size \
            else np.unique(member_edges)
        points = vertices[vertex_indices]
        extent = points.max(axis=0) - points.min(axis=0)
        corners = vertices[faces[face_indices]] if face_indices.size else None
        area = 0.0
        if corners is not None and corners.size:
            area = float(0.5 * np.linalg.norm(
                np.cross(corners[:, 1] - corners[:, 0],
                         corners[:, 2] - corners[:, 0]), axis=1).sum())
        regions.append({
            "edges": member_edges,
            "edge_count": int(member_edges.shape[0]),
            "face_indices": face_indices,
            "face_count": int(face_indices.shape[0]),
            "vertex_indices": vertex_indices,
            "vertex_count": int(vertex_indices.shape[0]),
            "bbox_mm": [float(v) for v in extent],
            "bbox_diagonal_mm": float(np.linalg.norm(extent)),
            "area_mm2": area,
            "center_mm": [float(v) for v in points.mean(axis=0)],
        })

    regions.sort(key=lambda entry: entry["edge_count"], reverse=True)
    for position, region in enumerate(regions[:max_regions]):
        region["region_id"] = position + 1
    return regions[:max_regions]


def describe_region(region):
    return ("R%d  %d non-manifold edge(s), %d face(s), %d vertex/vertices, "
            "%.2f mm across, %.4f mm2"
            % (region["region_id"], region["edge_count"], region["face_count"],
               region["vertex_count"], region["bbox_diagonal_mm"],
               region["area_mm2"]))


def classify_region(vertices, faces, region, mean_edge_mm=0.0):
    """Name the defect, or refuse. Returns (classification, detail).

    Refusing is a real outcome, not a fallback: sect. 3 says do not claim a
    classification if it is ambiguous, and a wrong guess here removes anatomy.
    """
    faces = np.asarray(faces, dtype=np.int64)
    face_indices = region["face_indices"]

    if region["face_count"] > MAX_REGION_FACES:
        return DEFECT_TOO_LARGE, ("%d faces exceeds the %d-face limit for a "
                                  "local artefact"
                                  % (region["face_count"], MAX_REGION_FACES))
    limit = region_diagonal_limit(mean_edge_mm)
    if region["bbox_diagonal_mm"] > limit:
        return DEFECT_TOO_LARGE, ("%.1f mm across exceeds the %.1f mm limit "
                                  "for a local artefact on this mesh"
                                  % (region["bbox_diagonal_mm"], limit))

    # Duplicate or reversed faces inside the region.
    local = faces[face_indices]
    keys = [tuple(sorted(int(v) for v in row)) for row in local]
    if len(set(keys)) < len(keys):
        return DEFECT_DUPLICATE, "the region contains repeated face(s)"

    # A fin: a region face with a vertex no OTHER face uses. Each face
    # contributes a vertex exactly once to the flattened array, so this
    # degree is literally "how many faces use this vertex", and 1 means the
    # vertex hangs off the surface.
    degree = np.bincount(faces.ravel(), minlength=int(faces.max()) + 1)
    for row in local:
        if any(int(degree[int(v)]) == 1 for v in row):
            return DEFECT_FIN, "a face hangs off the surface by a dangling vertex"

    # A fan: every non-manifold edge shares one central vertex.
    shared = set(int(v) for v in region["edges"][0])
    for pair in region["edges"][1:]:
        shared &= set(int(v) for v in pair)
    if shared:
        return DEFECT_FAN, ("%d non-manifold edge(s) all meet at vertex %d"
                            % (region["edge_count"], sorted(shared)[0]))

    if region["face_count"] <= 12:
        return DEFECT_FLAP, "a small redundant patch of %d face(s)" % region["face_count"]

    return DEFECT_AMBIGUOUS, ("the local topology does not match a pattern "
                              "BSMT repairs automatically")


def plan_region_repair(vertices, faces, region,
                       max_removed=MAX_FACES_REMOVED, mean_edge_mm=0.0):
    """Choose the minimal redundant faces to remove. Returns a plan dict.

    Greedy and deterministic: at each step it removes the region face that
    resolves the most non-manifold edges, breaking ties by smallest area then
    lowest index, and it KEEPS a removal only if the non-manifold count
    strictly falls. Candidates are restricted to faces incident to a
    non-manifold edge of this region, so the blast radius cannot spread into
    surrounding anatomy.

    It never touches a mesh; ``remove_faces`` is a list of face indices for
    ``meshrepair`` to execute inside a transaction.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    classification, detail = classify_region(vertices, faces, region,
                                             mean_edge_mm)
    plan = {
        "region_id": region.get("region_id", 0),
        "classification": classification,
        "detail": detail,
        "remove_faces": [],
        "nonmanifold_before": region["edge_count"],
        "nonmanifold_after": region["edge_count"],
        "refused": classification in (DEFECT_AMBIGUOUS, DEFECT_TOO_LARGE),
        "resolved": False,
        "steps": [],
    }
    if plan["refused"]:
        return plan

    # Local edge bookkeeping: incidence counted across the WHOLE mesh, then
    # decremented as faces are removed. Exact, and O(1) per step.
    incidence = edge_incidence(faces)
    counts = {key: len(value) for key, value in incidence.items()}

    candidate_faces = set(int(v) for v in region["face_indices"])
    corners = vertices[faces]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0],
                 corners[:, 2] - corners[:, 0]), axis=1)
    # When a face repeats another, remove the LATER copy. Both choices are
    # topologically identical, but the first occurrence is the one already
    # woven into the mesh's winding and UV layout, so churning it gains
    # nothing and risks the texture.
    redundant = set(int(v) for v in duplicate_faces(faces))

    # A face held on by a vertex no other face uses is provably safe to
    # remove: nothing else references that vertex, so removing the face
    # cannot open a hole in the surrounding shell. Real Design X artefacts
    # are exactly this shape, and preferring these faces stops the greedy
    # from picking a slightly smaller *surface* face instead and tearing a
    # hole that then has to be patched.
    vertex_degree = np.bincount(faces.ravel(), minlength=int(faces.max()) + 1)
    dangling = set()
    for index in candidate_faces:
        if any(int(vertex_degree[int(v)]) == 1 for v in faces[index]):
            dangling.add(int(index))

    def face_edges(index):
        a, b, c = faces[index]
        return (_edge_key(a, b), _edge_key(b, c), _edge_key(c, a))

    def region_non_manifold():
        return sum(1 for key in region_edge_keys if counts.get(key, 0) >= 3)

    region_edge_keys = set()
    for index in candidate_faces:
        region_edge_keys.update(face_edges(index))

    removed = []
    alive = set(candidate_faces)
    current = region_non_manifold()
    for _step in range(max_removed):
        if current == 0:
            break
        best = None
        for index in sorted(alive):
            keys = face_edges(index)
            resolves = sum(1 for key in keys if counts.get(key, 0) >= 3)
            if resolves == 0:
                continue
            # Deterministic ordering: resolve the most edges, then prefer a
            # face whose removal cannot tear the surrounding shell - a
            # redundant copy, or one hanging by a dangling vertex - then the
            # smallest face, then the lowest index.
            score = (-resolves,
                     0 if index in redundant else 1,
                     0 if index in dangling else 1,
                     float(areas[index]), index)
            if best is None or score < best[0]:
                best = (score, index, keys)
        if best is None:
            break

        _score, index, keys = best
        for key in keys:
            counts[key] = counts.get(key, 0) - 1
        after = region_non_manifold()
        if after >= current:
            # Undo the trial: a removal that does not strictly improve is not
            # made. Without this the greedy step could keep deleting faces
            # while achieving nothing.
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
            break
        alive.discard(index)
        removed.append(int(index))
        plan["steps"].append({
            "face": int(index),
            "area_mm2": float(areas[index]),
            "nonmanifold_after": after,
        })
        current = after

    plan["remove_faces"] = removed
    plan["nonmanifold_after"] = current
    plan["resolved"] = current == 0
    if not removed:
        plan["refused"] = True
        plan["detail"] = (detail + "; no face removal reduced the "
                                   "non-manifold count")
    return plan


def predict_patch(vertices, faces, remove_faces):
    """The boundary a removal would leave behind, and whether it is fillable.

    Returned before anything is executed, so the size limits of sect. 5 are
    checked against the actual hole rather than hoped for afterwards.
    """
    faces = np.asarray(faces, dtype=np.int64)
    keep = np.ones(faces.shape[0], dtype=bool)
    keep[np.asarray(remove_faces, dtype=np.int64)] = False
    remaining = faces[keep]
    loops_before = {tuple(sorted(edge))
                    for edge in classify_edges(
                        faces, int(np.asarray(vertices).shape[0]))["boundary"]}
    after = classify_edges(remaining, int(np.asarray(vertices).shape[0]))
    new_boundary = [edge for edge in after["boundary"]
                    if tuple(sorted(edge)) not in loops_before]
    if not new_boundary:
        return {"new_boundary_edges": 0, "fillable": True, "loops": [],
                "reason": "the removal leaves no new boundary"}

    loops = boundary_loops(np.asarray(vertices, dtype=np.float64), remaining)
    new_keys = {tuple(sorted(edge)) for edge in new_boundary}
    touched = [loop for loop in loops
               if any(tuple(sorted(edge)) in new_keys for edge in loop["edges"])]

    fillable = True
    reasons = []
    for loop in touched:
        if loop["edge_count"] > MAX_PATCH_EDGES:
            fillable = False
            reasons.append("a new hole has %d edges (limit %d)"
                           % (loop["edge_count"], MAX_PATCH_EDGES))
        if loop["perimeter_mm"] > MAX_PATCH_PERIMETER_MM:
            fillable = False
            reasons.append("a new hole is %.1f mm around (limit %.1f)"
                           % (loop["perimeter_mm"], MAX_PATCH_PERIMETER_MM))
        if loop["bbox_diagonal_mm"] > MAX_PATCH_DIAGONAL_MM:
            fillable = False
            reasons.append("a new hole is %.1f mm across (limit %.1f)"
                           % (loop["bbox_diagonal_mm"], MAX_PATCH_DIAGONAL_MM))
    return {
        "new_boundary_edges": len(new_boundary),
        "loops": touched,
        "fillable": fillable,
        "reason": "; ".join(reasons) if reasons else "within the patch limits",
    }


def is_tiny_boundary(loop):
    """Whether a boundary loop is small enough for the automatic pass.

    Deliberately strict. This pass runs over boundaries the researcher did not
    point at, so it must never reach a crop plane, a neck cut, or any
    anatomically meaningful opening.
    """
    if loop["edge_count"] > TINY_BOUNDARY_EDGES:
        return False, ("%d edges exceeds the %d-edge limit"
                       % (loop["edge_count"], TINY_BOUNDARY_EDGES))
    if loop["perimeter_mm"] > TINY_BOUNDARY_PERIMETER_MM:
        return False, ("%.2f mm perimeter exceeds the %.1f mm limit"
                       % (loop["perimeter_mm"], TINY_BOUNDARY_PERIMETER_MM))
    if loop["bbox_diagonal_mm"] > TINY_BOUNDARY_DIAGONAL_MM:
        return False, ("%.2f mm across exceeds the %.1f mm limit"
                       % (loop["bbox_diagonal_mm"], TINY_BOUNDARY_DIAGONAL_MM))
    return True, "within the tiny-boundary limits"


# ---------------------------------------------------------------------------
# acceptance (sect. 6)
# ---------------------------------------------------------------------------

def accept_repair(before, after, texture_ok, new_boundary_limit=MAX_PATCH_EDGES):
    """Should a completed repair be kept? Returns (accept, reasons).

    Every criterion must hold. A repair that fails any of them is reverted, so
    a failed attempt can never be left behind in the measurement copy.
    """
    reasons = []
    nm_before = int(before.get("nonmanifold_edge_count", 0) or 0)
    nm_after = int(after.get("nonmanifold_edge_count", 0) or 0)
    if nm_after >= nm_before:
        reasons.append("non-manifold edges did not decrease (%d -> %d)"
                       % (nm_before, nm_after))

    boundary_before = int(before.get("boundary_edge_count", 0) or 0)
    boundary_after = int(after.get("boundary_edge_count", 0) or 0)
    if boundary_after - boundary_before > new_boundary_limit:
        reasons.append("it opened %d new boundary edge(s), more than the %d "
                       "allowed" % (boundary_after - boundary_before,
                                    new_boundary_limit))

    components_before = int(before.get("component_count", 0) or 0)
    components_after = int(after.get("component_count", 0) or 0)
    if components_after > components_before:
        reasons.append("it split the mesh into more components (%d -> %d)"
                       % (components_before, components_after))

    if int(after.get("triangle_count", 0) or 0) <= 0:
        reasons.append("the mesh has no triangles left")

    if not texture_ok:
        reasons.append("the UV map, material or image texture was lost")

    return (not reasons), reasons


def change_summary(before, after, region_bbox_mm=None, area_mm2=None):
    """What a repair changed, in the terms sect. 7 asks for."""
    return {
        "vertices_before": int(before.get("vertex_count", 0) or 0),
        "vertices_after": int(after.get("vertex_count", 0) or 0),
        "vertex_delta": int(after.get("vertex_count", 0) or 0)
                        - int(before.get("vertex_count", 0) or 0),
        "faces_before": int(before.get("triangle_count", 0) or 0),
        "faces_after": int(after.get("triangle_count", 0) or 0),
        "face_delta": int(after.get("triangle_count", 0) or 0)
                      - int(before.get("triangle_count", 0) or 0),
        "region_bbox_mm": region_bbox_mm or [0.0, 0.0, 0.0],
        "max_region_dimension_mm": (max(region_bbox_mm)
                                    if region_bbox_mm else 0.0),
        "affected_area_mm2": float(area_mm2 or 0.0),
    }


def change_lines(summary):
    return [
        "  vertices        %s -> %s (%+d)" % (
            "{:,}".format(summary["vertices_before"]),
            "{:,}".format(summary["vertices_after"]), summary["vertex_delta"]),
        "  faces           %s -> %s (%+d)" % (
            "{:,}".format(summary["faces_before"]),
            "{:,}".format(summary["faces_after"]), summary["face_delta"]),
        "  affected region %.2f mm across, %.4f mm2"
        % (summary["max_region_dimension_mm"], summary["affected_area_mm2"]),
    ]


# ---------------------------------------------------------------------------
# iterative repair support (Milestone 3.5a)
# ---------------------------------------------------------------------------
#
# Real-data lesson. Planning every region against ONE initial analysis and
# executing them as a batch took the Design X copy from 7 non-manifold edges
# to 4, not to 0. Two causes, both structural:
#
#   * after the first local edit the connectivity has changed, so every later
#     plan was computed against topology that no longer existed;
#   * acceptance compared NET counts, so a batch that fixed three defects and
#     created two elsewhere still looked like progress.
#
# The fixes below are the two halves of that: signatures that identify
# non-manifold edges by POSITION (so a step can be judged even though
# removing faces renumbers vertices), and a rule that refuses any step
# introducing a non-manifold edge that was not there before.

#: Positions are rounded to this many millimetres before comparison, so
#: float noise cannot make an unchanged edge look new.
SIGNATURE_TOLERANCE_MM = 1e-4


def _signature_key(point):
    quantum = SIGNATURE_TOLERANCE_MM
    return tuple(int(round(float(value) / quantum)) for value in point)


def nonmanifold_signature(vertices, faces):
    """Identify every non-manifold edge by its MIDPOINT, not by index.

    Face removal renumbers vertices, so an index-based before/after set
    comparison would report the whole mesh as changed. A midpoint is stable
    under any renumbering.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    edges = classify_edges(faces, vertices.shape[0])["non_manifold"]
    if edges.shape[0] == 0:
        return set()
    midpoints = 0.5 * (vertices[edges[:, 0]] + vertices[edges[:, 1]])
    return {_signature_key(point) for point in midpoints}


def degenerate_signature(vertices, faces, relative=1e-12):
    """Zero-area triangles, identified by centroid position."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return set()
    corners = vertices[faces]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0],
                 corners[:, 2] - corners[:, 0]), axis=1)
    scale = float(areas.max()) if areas.size else 0.0
    threshold = max(relative * scale, 0.0)
    bad = np.where(areas <= threshold)[0]
    if bad.size == 0:
        return set()
    return {_signature_key(point) for point in corners[bad].mean(axis=1)}


def step_acceptable(before_signature, after_signature,
                    before_report, after_report, texture_ok,
                    before_degenerate=None, after_degenerate=None):
    """Judge ONE local repair step. Returns (accept, reasons).

    Stricter than a net count in the way the real data demanded: a step is
    refused if it introduces a non-manifold edge ANYWHERE that was not there
    before, even when the total falls. Fixing three defects while creating two
    is not progress, it is churn that moves the problem.
    """
    reasons = []

    introduced = after_signature - before_signature
    if introduced:
        reasons.append("it introduced %d new non-manifold edge(s) elsewhere"
                       % len(introduced))

    if len(after_signature) >= len(before_signature):
        reasons.append("non-manifold edges did not decrease (%d -> %d)"
                       % (len(before_signature), len(after_signature)))

    components_before = int(before_report.get("component_count", 0) or 0)
    components_after = int(after_report.get("component_count", 0) or 0)
    if components_after > components_before:
        reasons.append("it split the mesh into more components (%d -> %d)"
                       % (components_before, components_after))

    if int(after_report.get("triangle_count", 0) or 0) <= 0:
        reasons.append("the mesh has no triangles left")

    if before_degenerate is not None and after_degenerate is not None:
        new_degenerate = after_degenerate - before_degenerate
        if new_degenerate:
            reasons.append("it created %d degenerate triangle(s)"
                           % len(new_degenerate))

    if not texture_ok:
        reasons.append("the UV map, material or image texture was lost")

    return (not reasons), reasons


def region_signature(vertices, region):
    """Midpoint signature of one region's OWN non-manifold edges."""
    vertices = np.asarray(vertices, dtype=np.float64)
    edges = np.asarray(region["edges"], dtype=np.int64)
    if edges.size == 0:
        return set()
    midpoints = 0.5 * (vertices[edges[:, 0]] + vertices[edges[:, 1]])
    return {_signature_key(point) for point in midpoints}


def local_invariants(target_signature, after_signature,
                     vertices, faces, centre_mm, radius_mm):
    """Did THIS region get fixed, and did the patch stay clean?

    Returns (ok, problems).

    Deliberately scoped to the region's own edges. An earlier version asked
    whether any edge within a ball of the repair still had three or more
    incident faces, and on a mesh with several separate artefacts that ball
    swallowed neighbouring defects the repair had never touched - so a
    perfectly good repair was reverted because a different, unrelated fin was
    still there. Whether the repair created a problem ELSEWHERE is a global
    question, and step_acceptable() answers it with the full signature.
    """
    problems = []

    unresolved = target_signature & after_signature
    if unresolved:
        problems.append("%d of this region's %d non-manifold edge(s) survived"
                        % (len(unresolved), len(target_signature)))

    # Degenerate faces created by the patch, checked tightly around it.
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size:
        centre = np.asarray(centre_mm, dtype=np.float64)
        near = np.linalg.norm(vertices - centre, axis=1) <= float(radius_mm)
        if near.any():
            local = faces[near[faces].any(axis=1)]
            if local.size:
                corners = vertices[local]
                areas = 0.5 * np.linalg.norm(
                    np.cross(corners[:, 1] - corners[:, 0],
                             corners[:, 2] - corners[:, 0]), axis=1)
                zero = int(np.count_nonzero(areas <= 0.0))
                if zero:
                    problems.append("%d degenerate face(s) at the repair"
                                    % zero)

    return (not problems), problems


def fillable_boundary_loops(vertices, faces, loops):
    """Loops that are genuinely fillable: closed, and every edge a boundary.

    Filling an edge that already has two incident faces adds a third and
    manufactures the very defect being repaired. The real run reduced
    non-manifold 7 -> 4 while ADDING four faces, which is what an unchecked
    fill looks like.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    edges = classify_edges(faces, vertices.shape[0])
    boundary = {tuple(sorted((int(a), int(b)))) for a, b in edges["boundary"]}
    good = []
    rejected = []
    for loop in loops:
        if not loop["closed"] or loop["edge_count"] < 3:
            rejected.append((loop, "not a closed loop of 3+ edges"))
            continue
        keys = {tuple(sorted((int(a), int(b)))) for a, b in loop["edges"]}
        if not keys <= boundary:
            rejected.append((loop, "some of its edges already carry two faces"))
            continue
        good.append(loop)
    return good, rejected
