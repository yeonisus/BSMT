"""SurfacePoint mathematics and scratch-mesh endpoint insertion (pure numpy).

Imports only numpy - no bpy - so the canonical location maths and the
insertion utilities are unit testable outside Blender.

The canonical location of a surface point is

    triangle_index + barycentric coordinates (u, v, w)

referring to the canonical triangle array, NOT a world XYZ. Barycentric
coordinates are affine invariant, so the same pair remains valid under any
object transform and can be evaluated in local, world or solver-millimetre
coordinates as needed.
"""

import numpy as np

# A barycentric coordinate below this counts as lying on the opposite edge,
# and two below this as lying on a vertex. Barycentric coordinates are
# dimensionless, so an absolute tolerance is scale independent. At 1e-7 the
# geometric displacement from snapping is below 1e-7 x triangle size, which is
# far under any scan's resolution.
SNAP_TOLERANCE = 1e-7

# Permitted departure of u+v+w from 1 before a point is rejected.
SUM_TOLERANCE = 1e-6

KIND_FACE = 'FACE'
KIND_EDGE = 'EDGE'
KIND_VERTEX = 'VERTEX'


class InsertionError(Exception):
    """Raised when endpoint insertion cannot be performed safely."""


def barycentric(triangle, point):
    """Barycentric coordinates of `point` on `triangle` ((3,3) corners).

    Returns (u, v, w) with point == u*A + v*B + w*C. The point is projected
    onto the triangle plane, which is what we want: a ray hit is on the plane
    up to floating point noise.
    """
    triangle = np.asarray(triangle, dtype=np.float64)
    point = np.asarray(point, dtype=np.float64)

    edge0 = triangle[1] - triangle[0]
    edge1 = triangle[2] - triangle[0]
    offset = point - triangle[0]

    d00 = float(edge0 @ edge0)
    d01 = float(edge0 @ edge1)
    d11 = float(edge1 @ edge1)
    d20 = float(offset @ edge0)
    d21 = float(offset @ edge1)

    denominator = d00 * d11 - d01 * d01
    if denominator == 0.0:
        raise InsertionError("degenerate triangle: cannot form barycentrics")

    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    u = 1.0 - v - w
    return np.array([u, v, w], dtype=np.float64)


def reconstruct(triangle, bary):
    """Point at barycentric coordinates `bary` on `triangle`."""
    triangle = np.asarray(triangle, dtype=np.float64)
    bary = np.asarray(bary, dtype=np.float64)
    return bary @ triangle


def normalize_barycentric(bary, tolerance=SUM_TOLERANCE):
    """Clean up floating point noise. Returns (bary, ok, deviation).

    `deviation` is how far the input was from a legal barycentric triple:
    the larger of |sum - 1| and the largest negative excursion. `ok` is False
    when that exceeds `tolerance`, meaning the point is not on the triangle
    and must not be silently coerced onto it.
    """
    bary = np.asarray(bary, dtype=np.float64).copy()

    total = float(bary.sum())
    sum_error = abs(total - 1.0)
    below = float(max(0.0, -bary.min()))
    above = float(max(0.0, bary.max() - 1.0))
    deviation = max(sum_error, below, above)

    if not np.isfinite(bary).all():
        return bary, False, float("inf")

    bary = np.clip(bary, 0.0, 1.0)
    total = float(bary.sum())
    if total <= 0.0:
        return bary, False, deviation
    bary = bary / total
    return bary, deviation <= tolerance, deviation


def classify(bary, tolerance=SNAP_TOLERANCE):
    """Classify a barycentric position. Returns (kind, index).

    KIND_VERTEX with the corner index, KIND_EDGE with the index of the corner
    *opposite* the edge, or KIND_FACE with None.
    """
    bary = np.asarray(bary, dtype=np.float64)
    small = bary <= tolerance
    count = int(np.count_nonzero(small))

    if count >= 2:
        # On a vertex: the corner that is not small.
        corner = int(np.argmax(bary))
        return KIND_VERTEX, corner
    if count == 1:
        return KIND_EDGE, int(np.argmax(small))
    return KIND_FACE, None


def snap(bary, tolerance=SNAP_TOLERANCE):
    """Zero out coordinates within `tolerance` and renormalise."""
    bary = np.asarray(bary, dtype=np.float64).copy()
    kind, index = classify(bary, tolerance)
    if kind == KIND_VERTEX:
        snapped = np.zeros(3, dtype=np.float64)
        snapped[index] = 1.0
        return snapped, kind, index
    if kind == KIND_EDGE:
        bary[index] = 0.0
        total = float(bary.sum())
        if total > 0.0:
            bary = bary / total
        return bary, kind, index
    return bary, kind, index


# ---------------------------------------------------------------------------
# scratch mesh endpoint insertion
# ---------------------------------------------------------------------------

class InsertionResult(object):
    """Outcome of inserting endpoints into a scratch copy of the mesh."""

    __slots__ = (
        "vertices",              # (n + k, 3) float64, canonical vertices + inserted
        "triangles",             # (m', 3) int64
        "point_vertex_indices",  # index into `vertices` for each input point
        "point_kinds",           # KIND_* for each input point
        "replaced_triangles",    # canonical triangle indices that were retriangulated
        "added_vertex_count",
    )


def _edge_key(a, b):
    return (a, b) if a < b else (b, a)


def _build_edge_map(faces):
    edge_map = {}
    for index, tri in enumerate(faces):
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        for a, b in ((i0, i1), (i1, i2), (i2, i0)):
            edge_map.setdefault(_edge_key(a, b), []).append(index)
    return edge_map


def _retriangulate(positions, triangle, entries, tolerance):
    """Insert points into one canonical triangle, keeping winding and conformity.

    `entries` is a list of (vertex_index, xyz). Each point is tested against
    every current sub-triangle: a point on an internal edge lands in two of
    them and splits both, which is what keeps the local patch conforming.
    """
    local = [(int(triangle[0]), int(triangle[1]), int(triangle[2]))]

    for vertex_index, xyz in entries:
        updated = []
        for tri in local:
            corners = positions[list(tri)]
            try:
                bary = barycentric(corners, xyz)
            except InsertionError:
                updated.append(tri)
                continue

            if float(bary.min()) < -tolerance:
                updated.append(tri)          # point is not in this sub-triangle
                continue

            kind, index = classify(bary, tolerance)
            if kind == KIND_VERTEX:
                updated.append(tri)          # already a corner here
            elif kind == KIND_EDGE:
                opposite = tri[index]
                first = tri[(index + 1) % 3]
                second = tri[(index + 2) % 3]
                updated.append((opposite, first, vertex_index))
                updated.append((opposite, vertex_index, second))
            else:
                i0, i1, i2 = tri
                updated.append((i0, i1, vertex_index))
                updated.append((i1, i2, vertex_index))
                updated.append((i2, i0, vertex_index))
        local = updated

    return local


def insert_points(vertices, triangles, points, tolerance=SNAP_TOLERANCE):
    """Insert surface points into a scratch copy of the mesh.

    `points` is a sequence of (triangle_index, barycentric). Every point is
    classified against the ORIGINAL canonical topology first, and the scratch
    mesh is then built in a single pass. Inserting one point and reusing the
    other's now-renumbered triangle index would be wrong, so it is never done.

    Cases handled: interior point, point on an edge (both incident triangles
    are split, so the mesh stays conforming), point on an existing vertex
    (reused, nothing split), two points in different triangles, and two points
    in the same triangle (the second is located within the sub-triangles
    produced by the first).

    The canonical arrays are never mutated.
    """
    canonical_vertices = np.asarray(vertices, dtype=np.float64)
    canonical_triangles = np.asarray(triangles, dtype=np.int64)
    triangle_count = int(canonical_triangles.shape[0])
    vertex_count = int(canonical_vertices.shape[0])

    result = InsertionResult()

    if not points:
        result.vertices = canonical_vertices.copy()
        result.triangles = canonical_triangles.copy()
        result.point_vertex_indices = []
        result.point_kinds = []
        result.replaced_triangles = set()
        result.added_vertex_count = 0
        return result

    # --- classify every point against the original topology ---------------
    specs = []
    for triangle_index, bary in points:
        triangle_index = int(triangle_index)
        if not 0 <= triangle_index < triangle_count:
            raise InsertionError(
                "triangle index %d outside the canonical array (%d triangles)"
                % (triangle_index, triangle_count)
            )
        clean, ok, deviation = normalize_barycentric(bary)
        if not ok:
            raise InsertionError(
                "barycentric coordinates %s are not on triangle %d "
                "(deviation %.3e)" % (np.asarray(bary).tolist(), triangle_index, deviation)
            )
        snapped, kind, index = snap(clean, tolerance)
        corners = canonical_triangles[triangle_index]
        xyz = reconstruct(canonical_vertices[corners], snapped)
        specs.append({
            "triangle": triangle_index,
            "bary": snapped,
            "kind": kind,
            "index": index,
            "xyz": xyz,
            "corners": corners,
        })

    # --- allocate vertices, sharing coincident points ---------------------
    extra = []
    scale = float(np.linalg.norm(
        canonical_vertices.max(axis=0) - canonical_vertices.min(axis=0)
    )) or 1.0
    merge_distance = scale * tolerance

    for spec in specs:
        if spec["kind"] == KIND_VERTEX:
            spec["vertex"] = int(spec["corners"][spec["index"]])
            continue
        shared = None
        for other in specs:
            if "vertex" not in other or other is spec:
                continue
            if np.linalg.norm(other["xyz"] - spec["xyz"]) <= merge_distance:
                shared = other["vertex"]
                break
        if shared is not None:
            spec["vertex"] = shared
        else:
            spec["vertex"] = vertex_count + len(extra)
            extra.append(spec["xyz"])

    scratch_vertices = (
        np.vstack([canonical_vertices, np.array(extra, dtype=np.float64)])
        if extra else canonical_vertices.copy()
    )

    # --- which canonical triangles are affected ---------------------------
    edge_map = None
    affected = {}
    for spec in specs:
        if spec["kind"] == KIND_VERTEX:
            continue
        if spec["kind"] == KIND_EDGE:
            if edge_map is None:
                edge_map = _build_edge_map(canonical_triangles)
            corners = spec["corners"]
            first = int(corners[(spec["index"] + 1) % 3])
            second = int(corners[(spec["index"] + 2) % 3])
            incident = edge_map.get(_edge_key(first, second), [spec["triangle"]])
        else:
            incident = [spec["triangle"]]
        for triangle_index in incident:
            affected.setdefault(triangle_index, []).append(
                (spec["vertex"], spec["xyz"])
            )

    # --- single deterministic rebuild -------------------------------------
    out = []
    for index in range(triangle_count):
        entries = affected.get(index)
        if entries is None:
            tri = canonical_triangles[index]
            out.append((int(tri[0]), int(tri[1]), int(tri[2])))
        else:
            out.extend(
                _retriangulate(
                    scratch_vertices, canonical_triangles[index], entries, tolerance
                )
            )

    result.vertices = scratch_vertices
    result.triangles = np.array(out, dtype=np.int64)
    result.point_vertex_indices = [spec["vertex"] for spec in specs]
    result.point_kinds = [spec["kind"] for spec in specs]
    result.replaced_triangles = set(affected)
    result.added_vertex_count = len(extra)
    return result
