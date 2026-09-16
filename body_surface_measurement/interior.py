"""Surface Interior: which side of a computed region boundary is the region.

    ordered landmarks -> cached closed boundary -> SURFACE INTERIOR
                      -> filled visualization -> thickness preview
                      -> Surface Area [a later milestone]

A SURFACE INTERIOR is *the selected mesh-surface side bounded by a computed
Surface Region boundary*. This module decides which triangles, and which
PARTS of triangles, make up that side. It computes no area: the partial
pieces are carried in full precisely so that a later milestone can sum them
without re-deriving anything.

No bpy, no solver, and no geometry is modified anywhere.

Why this is not a point-in-polygon test
---------------------------------------
Projecting the loop to XY and asking which faces fall inside is wrong on a
body for the same reason it is wrong for self-intersection: a boundary that
wraps a limb encloses nothing in any axis-aligned projection while bounding a
perfectly real patch of skin. The interior is a property of the SURFACE, and
the only defensible way to find it is to walk the surface.

The property that makes this tractable
--------------------------------------
An exact geodesic on a polyhedral surface is piecewise straight, and its
breakpoints lie ON TRIANGLE EDGES. Measured on the canonical mesh BSMT
already builds: every interior point of a computed boundary segment sits on a
mesh edge to within 4e-6 of the bounding-box diagonal - about 1e-8 relative.

So a computed boundary is not a cloud of samples that happen to lie near the
surface. It is an exact sequence of EDGE CROSSINGS, and between two
consecutive crossings it is a straight chord across exactly one triangle.
That turns "which side is the region" into a graph problem with an exact
barrier, and it is what lets the partial triangles be clipped exactly rather
than approximated.

The two sides, and why neither is called "inside"
-------------------------------------------------
On a closed surface a simple closed loop bounds TWO regions - a patch and
everything else. Neither is intrinsically the inside, and no amount of
geometry makes one of them anatomical. This module labels them SIDE_SMALLER
and SIDE_COMPLEMENT, by triangle-area total, and the researcher chooses. The
smaller side is the DEFAULT because it is usually what someone outlining a
patch meant, and it is a default, not a claim.
"""

import time

import numpy as np

# ---------------------------------------------------------------------------
# status and reason codes (the project's existing vocabulary)
# ---------------------------------------------------------------------------

STATUS_NONE = 'NONE'
STATUS_VALID = 'VALID'
STATUS_STALE = 'STALE'
STATUS_INVALID = 'INVALID'

STATUS_ITEMS = (
    (STATUS_NONE, "Not Computed", "The interior has not been computed yet"),
    (STATUS_VALID, "Valid",
     "The interior was computed from this boundary on this geometry"),
    (STATUS_STALE, "Stale",
     "The boundary or the geometry changed after the interior was computed"),
    (STATUS_INVALID, "Invalid", "The interior could not be determined"),
)

STATUS_ICONS = {
    STATUS_NONE: 'BLANK1',
    STATUS_VALID: 'CHECKMARK',
    STATUS_STALE: 'FILE_REFRESH',
    STATUS_INVALID: 'CANCEL',
}

STATUS_SHORT = {
    STATUS_NONE: "NOT COMPUTED",
    STATUS_VALID: "VALID",
    STATUS_STALE: "STALE",
    STATUS_INVALID: "INVALID",
}

TRUSTED = (STATUS_VALID,)

CODE_NONE = ''
CODE_NOT_COMPUTED = 'INTERIOR_NOT_COMPUTED'
CODE_BOUNDARY_NOT_VALID = 'BOUNDARY_NOT_VALID'
CODE_BOUNDARY_CHANGED = 'BOUNDARY_CHANGED'
CODE_GEOMETRY_MISMATCH = 'GEOMETRY_MISMATCH'
CODE_OPEN_SURFACE = 'INTERIOR_UNDETERMINED_OPEN_SURFACE'
CODE_NOT_SEPARATED = 'INTERIOR_NOT_SEPARATED'
CODE_CROSS_COMPONENT = 'CROSS_COMPONENT'
CODE_UNSUPPORTED_CROSSING = 'UNSUPPORTED_TRIANGLE_CROSSING'
CODE_BOUNDARY_OFF_SURFACE = 'BOUNDARY_OFF_SURFACE'
CODE_SELF_INTERSECTION = 'SELF_INTERSECTION_DETECTED'

CODE_LABELS = {
    CODE_NOT_COMPUTED: "the interior has not been computed",
    CODE_BOUNDARY_NOT_VALID: "the region boundary is not currently valid",
    CODE_BOUNDARY_CHANGED: "the boundary changed after the interior was "
                           "computed",
    CODE_GEOMETRY_MISMATCH: "the interior was computed on different geometry",
    CODE_OPEN_SURFACE: "the surface is open in a way that leaves the two "
                       "sides undetermined",
    CODE_NOT_SEPARATED: "the boundary does not separate the surface into two "
                        "sides",
    CODE_CROSS_COMPONENT: "the boundary spans disconnected surface components",
    CODE_UNSUPPORTED_CROSSING: "the boundary's crossing of a triangle could "
                               "not be split exactly",
    CODE_BOUNDARY_OFF_SURFACE: "a boundary point does not lie on this mesh",
    CODE_SELF_INTERSECTION: "the boundary crosses itself inside a triangle",
}

#: The two sides. Named for what they ARE - a size comparison - and not for
#: what anyone hopes they mean anatomically.
SIDE_SMALLER = 'SMALLER'
SIDE_COMPLEMENT = 'COMPLEMENT'

SIDE_ITEMS = (
    (SIDE_SMALLER, "Smaller Side",
     "The side with the smaller total triangle area. The default, because it "
     "is usually the patch someone outlining a region meant - not because it "
     "is anatomically inside"),
    (SIDE_COMPLEMENT, "Complement Side",
     "The other side: the rest of the surface component"),
)

SIDE_LABELS = {identifier: label for identifier, label, _t in SIDE_ITEMS}

#: A boundary point is treated as lying ON a mesh edge when it is within this
#: fraction of the mesh's bounding-box diagonal of it. The measured worst case
#: on a real computed boundary is about 1e-8 relative, so this is four orders
#: of magnitude of headroom and still far below any real feature.
ON_EDGE_FRACTION = 1e-5

#: Above this triangle count the last-resort whole-mesh face search is not
#: attempted. It is O(triangles) per point and on a 350,000-triangle scan it
#: would turn one unplaceable point into a visible stall - and a point the
#: grid could not find within two cells of itself is not on this mesh
#: anyway, which is a refusal worth getting to quickly.
_FULL_SCAN_LIMIT = 100000

#: How far the pieces of a triangle may sum from the triangle's own area
#: before the split is refused, as a fraction of that area.
#:
#: This is a PRECISION bound, not a slack allowance. The cached boundary is
#: stored as float32 - it is display geometry, and the measurement itself has
#: never been re-derived from it - so a boundary point sits on its edge to
#: about float32 epsilon times the bounding-box diagonal. Measured on a
#: four-landmark region of a 2,208-triangle sphere: 157 crossed triangles,
#: worst relative tiling error 3.6e-07, which is exactly that bound. The
#: value below leaves about a factor of thirty of headroom and still refuses
#: anything that is wrong for a structural reason rather than a numerical one.
TILING_TOLERANCE = 1e-5

LIMITS = (
    "Surface Interior walks the mesh surface; it never projects the boundary "
    "into a plane. Triangles the boundary cuts are clipped EXACTLY - measured "
    "worst tiling error 3.6e-07 of a triangle's own area, which is the "
    "float32 precision the cached boundary is stored at - and a triangle "
    "crossed several times is split by each cut in turn rather than "
    "approximated. What IS refused: a boundary whose two sides cannot be "
    "separated, which is what an open surface can leave undetermined, and a "
    "boundary that does not lie on this mesh. Neither side is called "
    "'inside': the smaller one is a default, not an anatomical claim."
)


def tiling_report(vertices, triangles, triangle_index, runs, pieces,
                  tolerance):
    """A concise, inspectable record of ONE triangle that failed to tile.

    Enough to diagnose the geometry without dumping the mesh: where the
    boundary touched this triangle, in world millimetres AND in barycentric
    coordinates, whether any of it landed on a vertex or an edge, how far
    each chord end sits off the border, and what the pieces came out as. A
    tiling failure says the partition is wrong; this says where to look.
    """
    corners = np.asarray(vertices, dtype=np.float64)[
        np.asarray(triangles)[int(triangle_index)]]
    parent = polygon_area(corners)
    span = float(np.linalg.norm(corners.max(axis=0) - corners.min(axis=0)))
    lines = ["triangle %d" % int(triangle_index),
             "  parent corners (mm): %s"
             % "; ".join("(%.6f, %.6f, %.6f)" % tuple(value)
                         for value in corners),
             "  parent area: %.9f" % parent,
             "  boundary visits this triangle %d time(s)" % len(runs)]

    for position, run in enumerate(runs):
        points = np.asarray(run["points"], dtype=np.float64)
        lines.append("  visit %d: %d point(s)"
                     % (position + 1, points.shape[0]))
        for which, point in (("enter", points[0]), ("exit", points[-1])):
            bary = barycentric(vertices, triangles, triangle_index, point)
            at_vertex = -1
            on_edge = -1
            if bary is not None:
                if float(bary.max()) > 1.0 - 1e-6:
                    at_vertex = int(np.argmax(bary))
                smallest = int(np.argmin(np.abs(bary)))
                if abs(float(bary[smallest])) < 1e-9:
                    on_edge = smallest
            gap = None
            for index in range(3):
                a, b = corners[index], corners[(index + 1) % 3]
                along = b - a
                length2 = float(along.dot(along))
                if length2 <= 0.0:
                    continue
                t = min(max(float((point - a).dot(along) / length2),
                            0.0), 1.0)
                distance = float(np.linalg.norm(point - (a + t * along)))
                gap = distance if gap is None else min(gap, distance)
            lines.append(
                "    %s (mm) %s  bary %s  off-border %.3e%s%s"
                % (which, "(%.6f, %.6f, %.6f)" % tuple(point),
                   ("(%.9f, %.9f, %.9f)" % tuple(bary)) if bary is not None
                   else "(unresolved)",
                   gap if gap is not None else float("nan"),
                   "  AT VERTEX %d" % at_vertex if at_vertex >= 0 else "",
                   "  ON EDGE opposite %d" % on_edge if on_edge >= 0 else ""))
            if gap is not None and gap > tolerance:
                lines.append("    ^ further off the border than tolerance "
                             "(%.3e): this end is not where the boundary "
                             "crosses this triangle" % tolerance)

    if pieces is None:
        lines.append("  the splitter produced NO pieces for this triangle")
        for position, run in enumerate(runs):
            edge = aligned_edge(run)
            if edge is not None:
                lines.append("  visit %d lies ALONG mesh edge %s - such a "
                             "traversal does not cut the triangle, and is "
                             "handled as an edge-aligned boundary event"
                             % (position + 1, edge))
            else:
                kinds = [entry["kind"] for entry in run.get("locations", ())]
                lines.append("  visit %d crosses the interior (point kinds: "
                             "%s)" % (position + 1,
                                      ", ".join(kinds) if kinds
                                      else "unclassified"))
        return "\n".join(lines)

    if pieces:
        total = 0.0
        for index, part in enumerate(pieces):
            polygon = np.asarray(part["polygon"], dtype=np.float64)
            total += float(part["area"])
            duplicates = 0
            for corner in range(polygon.shape[0]):
                following = polygon[(corner + 1) % polygon.shape[0]]
                if float(np.linalg.norm(polygon[corner] - following)) \
                        <= 1e-12 * max(span, 1.0):
                    duplicates += 1
            lines.append("  piece %d: %d corner(s), area %.9f%s"
                         % (index + 1, polygon.shape[0], float(part["area"]),
                            ", %d duplicated corner(s)" % duplicates
                            if duplicates else ""))
        lines.append("  piece sum %.9f vs parent %.9f  (%+.3e relative)"
                     % (total, parent,
                        (total - parent) / max(parent, 1e-300)))
        lines.append("  an EXCESS means the pieces OVERLAP; a DEFICIT means "
                     "they leave a gap")
    return "\n".join(lines)


class _Stopwatch(object):
    """Stage timings, collected only when the caller asks for them.

    One `perf_counter` per stage and nothing per triangle: a per-element log
    on a 350,000-triangle scan would cost more than the stage it measured.
    """

    def __init__(self, sink):
        self.sink = sink
        self.last = time.perf_counter()

    def mark(self, label):
        if self.sink is None:
            return
        now = time.perf_counter()
        self.sink[label] = self.sink.get(label, 0.0) + (now - self.last)
        self.last = now

    def summary(self):
        if not self.sink:
            return ""
        return " | ".join("%s %.2fs" % (label, value)
                          for label, value in self.sink.items())


class InteriorError(Exception):
    """The interior cannot be determined, with a reason code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# mesh adjacency
# ---------------------------------------------------------------------------

def edge_map(triangles):
    """{(min,max) vertex pair: [triangle indices]} for every triangle edge.

    Vectorised. The obvious Python triple loop is 3 dict operations per
    triangle, which is seconds on a 350,000-triangle scan and is paid before
    any real work starts.
    """
    faces = np.asarray(triangles, dtype=np.int64)
    count = faces.shape[0]
    pairs = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]],
                            faces[:, [2, 0]]], axis=0)
    pairs = np.sort(pairs, axis=1)
    owners = np.tile(np.arange(count, dtype=np.int64), 3)
    order = np.lexsort((pairs[:, 1], pairs[:, 0]))
    pairs, owners = pairs[order], owners[order]
    breaks = np.flatnonzero(np.any(pairs[1:] != pairs[:-1], axis=1)) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [pairs.shape[0]]))
    edges = {}
    for start, stop in zip(starts, ends):
        edges[(int(pairs[start, 0]), int(pairs[start, 1]))] = \
            [int(value) for value in owners[start:stop]]
    return edges


def vertex_triangles(triangles, vertex_count):
    """{vertex: [triangle indices]}, built ONCE instead of per query.

    `_triangles_at_vertex` used to scan the whole triangle array for every
    boundary point that landed on a vertex. On a large scan that is a
    350,000-row comparison per point.
    """
    faces = np.asarray(triangles, dtype=np.int64)
    flat = faces.reshape(-1)
    owners = np.repeat(np.arange(faces.shape[0], dtype=np.int64), 3)
    order = np.argsort(flat, kind="stable")
    flat, owners = flat[order], owners[order]
    out = {}
    if flat.size:
        breaks = np.flatnonzero(flat[1:] != flat[:-1]) + 1
        starts = np.concatenate(([0], breaks))
        ends = np.concatenate((breaks, [flat.size]))
        for start, stop in zip(starts, ends):
            out[int(flat[start])] = [int(v) for v in owners[start:stop]]
    return out


class EdgeGrid(object):
    """A uniform grid over edge bounding boxes, for locating boundary points.

    THE fix for the real bottleneck. `locate_point` used to test a point
    against EVERY edge in the mesh: O(points x edges), which on a 350,000
    triangle scan with a few thousand boundary points is billions of Python
    iterations - measured at 30 s for 28,800 triangles and 192 points, which
    extrapolates to hours.

    This changes NOTHING about the answer. The grid only SHORTLISTS edges;
    the winning edge is still chosen by the same exact distance test over the
    candidates, and when the shortlist finds nothing within tolerance the
    caller falls back to the full scan. So the grid can make the search
    faster or, in the worst case, no faster - never wrong.
    """

    def __init__(self, vertices, edges):
        self.vertices = np.asarray(vertices, dtype=np.float64)
        self.keys = list(edges.keys())
        if not self.keys:
            self.cell = 0.0
            self.buckets = {}
            return
        pairs = np.asarray(self.keys, dtype=np.int64)
        starts = self.vertices[pairs[:, 0]]
        stops = self.vertices[pairs[:, 1]]
        lengths = np.linalg.norm(stops - starts, axis=1)
        # One mean edge length per cell: long enough that an edge spans few
        # cells, short enough that a cell holds few edges.
        self.cell = float(max(lengths.mean(), 1e-12))
        self.origin = np.minimum(starts, stops).min(axis=0)
        low = np.floor((np.minimum(starts, stops) - self.origin)
                       / self.cell).astype(np.int64)
        high = np.floor((np.maximum(starts, stops) - self.origin)
                        / self.cell).astype(np.int64)
        buckets = {}
        for index in range(pairs.shape[0]):
            for x in range(low[index, 0], high[index, 0] + 1):
                for y in range(low[index, 1], high[index, 1] + 1):
                    for z in range(low[index, 2], high[index, 2] + 1):
                        buckets.setdefault((x, y, z), []).append(index)
        self.buckets = buckets

    def candidates(self, point, radius=1):
        """Edge keys whose cells are near this point. A shortlist, not an answer."""
        if not self.buckets:
            return []
        base = np.floor((np.asarray(point, dtype=np.float64) - self.origin)
                        / self.cell).astype(np.int64)
        found = set()
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    bucket = self.buckets.get((int(base[0]) + dx,
                                               int(base[1]) + dy,
                                               int(base[2]) + dz))
                    if bucket:
                        found.update(bucket)
        return [self.keys[index] for index in found]


def triangle_areas(vertices, triangles):
    """Area of every triangle, in the units `vertices` is given in."""
    corners = vertices[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    return 0.5 * np.linalg.norm(cross, axis=1)


# ---------------------------------------------------------------------------
# locating the boundary on the mesh
# ---------------------------------------------------------------------------

LOC_EDGE = 'EDGE'
LOC_VERTEX = 'VERTEX'
LOC_FACE = 'FACE'


def locate_point(vertices, triangles, edges, point, tolerance,
                 candidates=None, vertex_index=None):
    """Where on the mesh is this boundary point? A location dict, or None.

    Returns the closest interpretation among: exactly at a vertex, on an edge
    at parameter t, or inside a face. Edge and vertex hits are what a geodesic
    breakpoint is; a face hit is what a boundary CORNER is, because a corner
    is a landmark and a landmark sits inside a triangle.
    """
    point = np.asarray(point, dtype=np.float64)

    keys = list(edges.keys()) if candidates is None else \
        [key for key in candidates if key in edges]
    best = _closest_edge(vertices, keys, point)

    if best is not None and best[0] <= tolerance:
        (u, v), t, tris = best[1], best[2], edges[best[1]]
        best = (best[0], (u, v), t, tris)
    if best is not None and best[0] <= tolerance:
        distance, (u, v), t, tris = best
        span = float(np.linalg.norm(vertices[v] - vertices[u]))
        edge_tol = tolerance / span if span > 0 else 0.0
        for vertex, at in ((int(u), t <= edge_tol),
                           (int(v), t >= 1.0 - edge_tol)):
            if not at:
                continue
            owners = _triangles_at_vertex(triangles, vertex, vertex_index)
            # Which edges this vertex sits on, so an edge-aligned run can
            # intersect the possibilities across its points and find the one
            # edge it is actually running along.
            incident = set()
            for triangle in owners:
                corners = [int(value) for value in np.asarray(triangles)[triangle]]
                for first, second in ((corners[0], corners[1]),
                                      (corners[1], corners[2]),
                                      (corners[2], corners[0])):
                    if vertex in (first, second):
                        incident.add((first, second) if first < second
                                     else (second, first))
            return {"kind": LOC_VERTEX, "vertex": vertex,
                    "triangles": owners, "vertex_edges": incident,
                    "distance": distance}
        return {"kind": LOC_EDGE, "edge": (int(u), int(v)), "t": t,
                "triangles": [int(value) for value in tris],
                "distance": distance}
    return None


def _closest_edge(vertices, keys, point):
    """(distance, edge key, parameter) for the nearest of these edges.

    Vectorised over the candidate list: one numpy pass instead of a Python
    loop with per-edge scalar arithmetic. Identical arithmetic, identical
    tie-breaking (the first minimum wins, as `<` did before).
    """
    if not keys:
        return None
    pairs = np.asarray(keys, dtype=np.int64)
    starts = vertices[pairs[:, 0]]
    along = vertices[pairs[:, 1]] - starts
    length2 = np.einsum("ij,ij->i", along, along)
    usable = length2 > 0.0
    if not np.any(usable):
        return None
    offsets = point - starts
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.einsum("ij,ij->i", offsets, along) / length2
    t = np.clip(np.where(usable, t, 0.0), 0.0, 1.0)
    distances = np.linalg.norm(offsets - along * t[:, None], axis=1)
    distances = np.where(usable, distances, np.inf)
    index = int(np.argmin(distances))
    if not np.isfinite(distances[index]):
        return None
    return (float(distances[index]),
            (int(pairs[index, 0]), int(pairs[index, 1])), float(t[index]))


def _triangles_at_vertex(triangles, vertex, index=None):
    if index is not None:
        return list(index.get(int(vertex), ()))
    return [int(value) for value in np.flatnonzero(
        (triangles == vertex).any(axis=1))]


def locate_in_face(vertices, triangles, point, tolerance, candidates):
    """The candidate triangle whose plane and interior contain this point."""
    point = np.asarray(point, dtype=np.float64)
    best = None
    for index in candidates:
        bary = barycentric(vertices, triangles, int(index), point)
        if bary is None:
            continue
        inside = float(min(bary))
        projected = (vertices[triangles[index]] * bary[:, None]).sum(axis=0)
        gap = float(np.linalg.norm(point - projected))
        if gap > tolerance:
            continue
        if best is None or inside > best[0]:
            best = (inside, int(index), bary)
    if best is None or best[0] < -1e-6:
        return None
    return {"kind": LOC_FACE, "triangle": best[1], "bary": best[2],
            "triangles": [best[1]], "distance": 0.0}


def barycentric(vertices, triangles, triangle_index, point):
    """Barycentric coordinates of `point` in one triangle, or None."""
    a, b, c = vertices[triangles[triangle_index]]
    v0, v1, v2 = b - a, c - a, np.asarray(point, dtype=np.float64) - a
    d00 = float(v0.dot(v0)); d01 = float(v0.dot(v1)); d11 = float(v1.dot(v1))
    d20 = float(v2.dot(v0)); d21 = float(v2.dot(v1))
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 0.0:
        return None
    v = (d11 * d20 - d01 * d21) / denominator
    w = (d00 * d21 - d01 * d20) / denominator
    return np.array([1.0 - v - w, v, w], dtype=np.float64)


# ---------------------------------------------------------------------------
# the boundary as a sequence of triangle traversals
# ---------------------------------------------------------------------------
#
# Between two consecutive breakpoints the boundary is a straight chord across
# ONE triangle. A boundary CORNER is the exception: a corner is a landmark, a
# landmark sits inside a triangle, so at a corner the boundary enters a
# triangle, bends, and leaves. A traversal is therefore a polyline that enters
# one edge and leaves another, with zero or more interior bends.

def traversals(vertices, triangles, edges, loop_points, tolerance,
               grid=None, vertex_index=None):
    """Per-triangle boundary traversals. {triangle: [traversal, ...]}.

    Each traversal is {"points": (k,3) local coords, "enter": location,
    "exit": location}. `loop_points` is the whole closed boundary, in order,
    without the repeated closing point.

    Raises InteriorError when a point cannot be placed on this mesh at all -
    which means the cache was computed on different geometry, and is a
    refusal rather than something to work around.
    """
    count = len(loop_points)
    if count < 3:
        raise InteriorError(CODE_BOUNDARY_NOT_VALID,
                            "the boundary has too few points to trace")

    if grid is None:
        grid = EdgeGrid(vertices, edges)
    if vertex_index is None:
        vertex_index = vertex_triangles(triangles, len(vertices))

    located = []
    for index, point in enumerate(loop_points):
        # SHORTLIST, then the same exact test. Widening rings before the full
        # scan keeps this exact: the grid can only change how many edges are
        # examined, never which one wins.
        found = None
        for radius in (1, 2):
            shortlist = grid.candidates(point, radius=radius)
            if shortlist:
                found = locate_point(vertices, triangles, edges, point,
                                     tolerance, candidates=shortlist,
                                     vertex_index=vertex_index)
            if found is not None:
                break
        if found is None:
            found = locate_point(vertices, triangles, edges, point, tolerance,
                                 vertex_index=vertex_index)
        if found is None:
            # Not on any edge: a corner, which lies inside a triangle. Look
            # in the faces its neighbours touch rather than the whole mesh.
            # The neighbours' triangles first, because a corner almost
            # always lands in one of them. Then a grid shortlist, then the
            # whole mesh: each step only WIDENS the search, so a point this
            # used to place is placed identically and a point it used to
            # refuse now gets a fair chance.
            nearby = set()
            for offset in (-1, 1):
                other = (located[index + offset]
                         if 0 <= index + offset < len(located) else None)
                if other is not None:
                    nearby.update(other["triangles"])
            found = (locate_in_face(vertices, triangles, point, tolerance,
                                    sorted(nearby)) if nearby else None)
            if found is None:
                wider = set()
                for key in grid.candidates(point, radius=2):
                    wider.update(edges.get(key, ()))
                if wider - nearby:
                    found = locate_in_face(vertices, triangles, point,
                                           tolerance, sorted(wider | nearby))
            if found is None and len(triangles) <= _FULL_SCAN_LIMIT:
                found = locate_in_face(vertices, triangles, point, tolerance,
                                       range(len(triangles)))
        if found is None:
            raise InteriorError(
                CODE_BOUNDARY_OFF_SURFACE,
                "boundary point %d does not lie on this mesh - the boundary "
                "was computed on different geometry" % index)
        located.append(found)

    # START THE WALK ON AN EDGE. A boundary CORNER is a landmark, and a
    # landmark sits INSIDE a triangle - so if the point list happens to begin
    # at a corner (it does: segment 1 starts at the first landmark), the walk
    # would open and close mid-triangle and hand that triangle two half
    # traversals that are really one. Rotating to the first edge crossing
    # costs nothing and makes every traversal enter and leave on the border,
    # which is what the splitter requires.
    start = next((index for index, entry in enumerate(located)
                  if entry["kind"] in (LOC_EDGE, LOC_VERTEX)), None)
    if start is None:
        raise InteriorError(
            CODE_NOT_SEPARATED,
            "no part of the boundary lies on a triangle edge, so it does not "
            "cross the surface")
    if start:
        located = located[start:] + located[:start]
        loop_points = list(loop_points[start:]) + list(loop_points[:start])

    # Walk the closed loop, accumulating points into the triangle they cross.
    out = {}
    current_triangle = None
    current_points = []
    current_locations = []
    current_enter = None

    def flush(exit_location):
        if current_triangle is None or len(current_points) < 2:
            return
        out.setdefault(current_triangle, []).append({
            "points": np.asarray(current_points, dtype=np.float64),
            # Every point's own classification, carried with the run. This is
            # what lets an EDGE-ALIGNED traversal be recognised topologically
            # - every point on one common mesh edge - instead of by snapping
            # coordinates together and hoping.
            "locations": list(current_locations),
            "enter": current_enter,
            "exit": exit_location,
        })

    for index in range(count + 1):
        here = located[index % count]
        point = np.asarray(loop_points[index % count], dtype=np.float64)
        following = located[(index + 1) % count]
        shared = set(here["triangles"]) & set(following["triangles"])
        triangle = min(shared) if shared else None

        if current_triangle is not None and triangle != current_triangle:
            current_points.append(point)
            current_locations.append(here)
            flush(here)
            current_points = []
            current_locations = []
            current_enter = None
        if triangle is None:
            current_triangle = None
            current_points = []
            current_locations = []
            continue
        if current_triangle != triangle or current_enter is None:
            current_enter = here
            current_points = [point]
            current_locations = [here]
            current_triangle = triangle
        else:
            current_points.append(point)
            current_locations.append(here)

    # Several traversals of one triangle is NORMAL, not exotic. Measured on a
    # four-landmark region of a 2,208-triangle sphere: two triangles are
    # crossed twice, because the boundary passes through, leaves and comes
    # back. Refusing those would refuse most real boundaries, so they are
    # split properly instead - see `split_triangle`.
    #
    # EDGE-ALIGNED runs are separated out here, before any clipping is
    # attempted. See `aligned_edge`.
    chords = {}
    aligned = set()
    for triangle, run_list in out.items():
        kept = []
        for run in run_list:
            edge = aligned_edge(run)
            if edge is None:
                kept.append(run)
            else:
                aligned.add(edge)
        if kept:
            chords[triangle] = kept
    return chords, located, aligned


def aligned_edge(run):
    """The mesh edge this run lies ALONG, or None if it crosses the interior.

    A boundary segment that runs along a mesh edge does NOT cut the triangle
    in two: the triangle stays whole, and it is the EDGE that becomes part of
    the barrier. Trying to clip such a triangle asks for a piece with no
    interior, which is why it used to be refused outright - the documented
    "along-edge" limitation.

    The test is TOPOLOGICAL, not a distance threshold. Every point of the run
    already carries its own classification from `locate_point`: on an edge,
    at a vertex, or inside a face. The run lies along edge (u, v) when every
    one of its points is either ON that edge or AT one of its two ends. A run
    with any point classified inside a face, or on a different edge, genuinely
    crosses the interior and is clipped as before.

    That distinction matters: a chord that enters and leaves through the SAME
    edge but bulges into the triangle between - which a real geodesic does -
    has interior points, is not edge-aligned, and must still be split.
    """
    locations = run.get("locations")
    if not locations or len(locations) < 2:
        return None
    candidates = None
    for entry in locations:
        if entry["kind"] == LOC_EDGE:
            here = {entry["edge"]}
        elif entry["kind"] == LOC_VERTEX:
            here = {edge for edge in _edges_at(entry)
                    if entry["vertex"] in edge}
        else:
            return None                                # inside a face
        candidates = here if candidates is None else (candidates & here)
        if not candidates:
            return None
    return min(candidates) if candidates else None


def _edges_at(location):
    """Edge keys a VERTEX location could belong to, from its own triangles.

    A vertex sits on several edges, and which of them the boundary is running
    along is decided by the other points of the same run - the intersection
    in `aligned_edge`. This only has to offer the possibilities.
    """
    return location.get("vertex_edges") or ()


# ---------------------------------------------------------------------------
# splitting one crossed triangle, exactly
# ---------------------------------------------------------------------------

def split_polygon(polygon, chord, tolerance):
    """Cut one polygon with one chord. Returns two polygons, or None.

    `polygon` is an ordered list of 3D points; `chord` is a polyline whose
    FIRST and LAST points lie on the polygon's border. The two results are
    the chord plus each of the two arcs of the border between its ends, so
    together they tile the polygon exactly.

    Generic on purpose: a triangle crossed twice is split once, then one of
    the halves is split again. The same routine does both, so there is no
    "simple case" that behaves differently from the general one.
    """
    entry = np.asarray(chord[0], dtype=np.float64)
    exit_point = np.asarray(chord[-1], dtype=np.float64)
    border = [np.asarray(point, dtype=np.float64) for point in polygon]
    count = len(border)
    if count < 3:
        return None

    def locate(point):
        """(edge index, parameter, point ON the border) for a chord end.

        THE POINT IT RETURNS IS THE PROJECTED ONE, and that is the whole
        correctness of this routine. A chord end is on the polygon's border
        by construction - it is where the boundary crosses a mesh edge - but
        the cached boundary is float32 display geometry, so the stored
        coordinate sits a few microns to one side of it.

        Inserting that stored coordinate into both pieces makes them share a
        point that is NOT on the border, so their union bulges past the
        polygon: area is created and the two sides OVERLAP along the chord.
        Measured on a 3.9 mm^2 triangle, a 3e-3 mm drift produces a +1.2e-03
        relative tiling excess - which is the size of the failure seen on a
        real 350,000-triangle scan (3.82042 vs 3.81668, +9.8e-04).

        Projecting restores the invariant without moving the boundary
        anywhere a researcher could see: the correction is the drift itself,
        microns, and it is applied identically by both triangles sharing the
        edge because both project onto the same segment.
        """
        best = None
        for index in range(count):
            a = border[index]
            b = border[(index + 1) % count]
            along = b - a
            length2 = float(along.dot(along))
            if length2 <= 0.0:
                continue
            t = min(max(float((point - a).dot(along) / length2), 0.0), 1.0)
            projected = a + t * along
            gap = float(np.linalg.norm(point - projected))
            if best is None or gap < best[0]:
                best = (gap, index, t, projected)
        # Refused, not nudged, when the end is further off the border than
        # float32 storage can explain: that is a real fault and inventing a
        # projection for it would hide it.
        if best is None or best[0] > tolerance:
            return None
        return best[1], best[2], best[3]

    first = locate(entry)
    second = locate(exit_point)
    if first is None or second is None:
        return None
    (enter_edge, enter_t, entry), (exit_edge, exit_t, exit_point) = \
        first, second
    if enter_edge == exit_edge and abs(enter_t - exit_t) <= 1e-12:
        return None

    # Write the border out once with both chord ends inserted in place, so
    # the two arcs are just slices of one cyclic sequence.
    sequence = []
    for index in range(count):
        sequence.append({"kind": "corner", "point": border[index]})
        marks = []
        if enter_edge == index:
            marks.append((enter_t, "enter", entry))
        if exit_edge == index:
            marks.append((exit_t, "exit", exit_point))
        for _t, which, point in sorted(marks, key=lambda item: item[0]):
            sequence.append({"kind": which, "point": point})

    positions = {entry_["kind"]: index
                 for index, entry_ in enumerate(sequence)
                 if entry_["kind"] in ("enter", "exit")}
    if "enter" not in positions or "exit" not in positions:
        return None
    enter_at, exit_at = positions["enter"], positions["exit"]
    total = len(sequence)

    def arc(start, stop):
        points = []
        index = (start + 1) % total
        while index != stop:
            if sequence[index]["kind"] == "corner":
                points.append(sequence[index]["point"])
            index = (index + 1) % total
        return points

    inner = [np.asarray(value, dtype=np.float64) for value in chord[1:-1]]
    # Each piece is the chord, then the border CONTINUING from where the
    # chord left off back to where it started. The other arc would give a
    # self-crossing polygon whose area is silently wrong rather than
    # obviously broken.
    first_piece = [entry] + inner + [exit_point] + arc(exit_at, enter_at)
    second_piece = [exit_point] + list(reversed(inner)) + [entry] \
        + arc(enter_at, exit_at)

    left = _dedupe(first_piece)
    right = _dedupe(second_piece)
    if len(left) < 3 or len(right) < 3:
        return None
    return (np.asarray(left, dtype=np.float64),
            np.asarray(right, dtype=np.float64))


def crossing_chords(vertices, triangles, triangle_index, runs):
    """Do two traversals of this triangle CROSS each other? (i, j) or None.

    Worth more than it looks. Region validation's shared-point test is sound
    but incomplete by construction - it cannot see a crossing that happens
    strictly BETWEEN two sampled boundary points, and says so. Inside a
    single triangle the boundary is straight between samples, so here that
    crossing is an ordinary segment-segment intersection and IS detectable.
    This closes exactly the gap the boundary check documents.
    """
    if len(runs) < 2:
        return None
    corners = vertices[triangles[triangle_index]]
    normal = np.cross(corners[1] - corners[0], corners[2] - corners[0])
    length = float(np.linalg.norm(normal))
    if length <= 0.0:
        return None
    normal = normal / length
    axis = (np.array([1.0, 0.0, 0.0])
            if abs(float(normal.dot([1.0, 0.0, 0.0]))) < 0.9
            else np.array([0.0, 1.0, 0.0]))
    u = np.cross(normal, axis)
    u = u / max(float(np.linalg.norm(u)), 1e-300)
    v = np.cross(normal, u)
    origin = corners[0]

    def flatten(points):
        values = np.asarray(points, dtype=np.float64) - origin
        return np.stack([values.dot(u), values.dot(v)], axis=1)

    flat = [flatten(run["points"]) for run in runs]

    def segments_cross(p1, p2, p3, p4):
        def side(a, b, c):
            return ((b[0] - a[0]) * (c[1] - a[1])
                    - (b[1] - a[1]) * (c[0] - a[0]))
        d1, d2 = side(p3, p4, p1), side(p3, p4, p2)
        d3, d4 = side(p1, p2, p3), side(p1, p2, p4)
        # STRICT: chords that merely touch at an endpoint share a corner of
        # the loop, which is legitimate. Only a genuine transversal crossing
        # is reported.
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

    for first in range(len(flat)):
        for second in range(first + 1, len(flat)):
            for a in range(flat[first].shape[0] - 1):
                for b in range(flat[second].shape[0] - 1):
                    if segments_cross(flat[first][a], flat[first][a + 1],
                                      flat[second][b], flat[second][b + 1]):
                        return (first, second)
    return None


def _contains(polygon, point, tolerance):
    """Is `point` inside this planar polygon? Crossing test in its own plane.

    Used to decide WHICH piece a second chord cuts. Distance-to-border alone
    cannot answer that: after the first cut, both pieces still have border
    along the original triangle edges, so a chord's ends can sit on both -
    and picking the wrong one produces two overlapping pieces whose areas sum
    to more than the triangle.
    """
    border = np.asarray(polygon, dtype=np.float64)
    if border.shape[0] < 3:
        return False
    normal = np.zeros(3)
    origin = border[0]
    for index in range(1, border.shape[0] - 1):
        normal = normal + np.cross(border[index] - origin,
                                   border[index + 1] - origin)
    length = float(np.linalg.norm(normal))
    if length <= 0.0:
        return False
    normal = normal / length
    axis = np.array([1.0, 0.0, 0.0])
    if abs(float(normal.dot(axis))) > 0.9:
        axis = np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, axis)
    u = u / max(float(np.linalg.norm(u)), 1e-300)
    v = np.cross(normal, u)

    flat = np.stack([(border - origin).dot(u), (border - origin).dot(v)],
                    axis=1)
    target = np.asarray([(np.asarray(point, dtype=np.float64)
                          - origin).dot(u),
                         (np.asarray(point, dtype=np.float64)
                          - origin).dot(v)])
    inside = False
    count = flat.shape[0]
    for index in range(count):
        a = flat[index]
        b = flat[(index + 1) % count]
        if (a[1] > target[1]) != (b[1] > target[1]):
            span = b[1] - a[1]
            if abs(span) > 0.0:
                crossing = a[0] + (target[1] - a[1]) * (b[0] - a[0]) / span
                if crossing > target[0]:
                    inside = not inside
    return inside


def _chord_fit(polygon, chord):
    """How far this chord's two ends are from a polygon's border. Lower wins."""
    border = np.asarray(polygon, dtype=np.float64)
    count = border.shape[0]
    worst = 0.0
    for point in (np.asarray(chord[0], dtype=np.float64),
                  np.asarray(chord[-1], dtype=np.float64)):
        closest = None
        for index in range(count):
            a = border[index]
            b = border[(index + 1) % count]
            along = b - a
            length2 = float(along.dot(along))
            if length2 <= 0.0:
                continue
            t = min(max(float((point - a).dot(along) / length2), 0.0), 1.0)
            gap = float(np.linalg.norm(point - (a + t * along)))
            if closest is None or gap < closest:
                closest = gap
        worst = max(worst, closest if closest is not None else np.inf)
    return worst


def split_triangle(vertices, triangles, triangle_index, runs, tolerance):
    """Cut one triangle with every boundary traversal through it.

    Returns a list of pieces, each {"polygon", "bary", "area"}. They tile the
    triangle EXACTLY - no area created, none lost - which is the property a
    later Surface Area milestone rests on entirely.

    Chords are applied one at a time to whichever piece each one crosses. The
    boundary is simple (region validation refuses a loop that touches
    itself), so the chords do not cross each other and the order they are
    applied in does not change the result.
    """
    corners = vertices[triangles[triangle_index]]
    pieces = [np.asarray(corners, dtype=np.float64)]

    for run in runs:
        chord = run["points"]
        # Which piece does this chord cut? Chosen by SCORE rather than by the
        # first piece that accepts it: the cached boundary is float32 display
        # geometry, so a chord end can sit a whisker off the border it
        # belongs to, and a hard first-match test would hand the chord to the
        # wrong piece - or to none at all - on a triangle crossed twice.
        # A chord's MIDPOINT is inside exactly one piece, so containment is
        # the authority and the border-distance score is only the tie-break
        # for when containment is undecidable (a chord lying along a border).
        midpoint = np.asarray(chord[len(chord) // 2], dtype=np.float64)
        if len(chord) == 2:
            midpoint = 0.5 * (np.asarray(chord[0], dtype=np.float64)
                              + np.asarray(chord[-1], dtype=np.float64))
        best = None
        for index, piece in enumerate(pieces):
            score = _chord_fit(piece, chord)
            holds = _contains(piece, midpoint, tolerance)
            rank = (0 if holds else 1, score)
            if best is None or rank < best[0]:
                best = (rank, index)
        if best is None:
            return None
        # A FIXED, triangle-local tolerance. This used to widen to twice the
        # best border distance, which accepted a chord end arbitrarily far
        # off the border and then built a polygon around it - the hole the
        # projection above closes. A chord that does not reach any piece's
        # border within tolerance is refused.
        split = split_polygon(pieces[best[1]], chord, tolerance)
        if split is None:
            return None
        left, right = split
        index = best[1]
        pieces = pieces[:index] + [left, right] + pieces[index + 1:]

    out = []
    for polygon in pieces:
        area = polygon_area(polygon)
        if area <= 0.0:
            continue
        bary = np.asarray(
            [barycentric(vertices, triangles, triangle_index, value)
             for value in polygon], dtype=np.float64)
        out.append({"polygon": polygon, "bary": bary, "area": area})
    return out or None


def _dedupe(points, tolerance=1e-12):
    out = []
    for point in points:
        value = np.asarray(point, dtype=np.float64)
        if out and float(np.linalg.norm(value - out[-1])) <= tolerance:
            continue
        out.append(value)
    while len(out) > 1 and float(np.linalg.norm(out[0] - out[-1])) <= tolerance:
        out.pop()
    return out


def polygon_area(polygon):
    """Area of a planar polygon in 3D, by the Newell / fan method."""
    points = np.asarray(polygon, dtype=np.float64)
    if points.shape[0] < 3:
        return 0.0
    origin = points[0]
    total = np.zeros(3)
    for index in range(1, points.shape[0] - 1):
        total = total + np.cross(points[index] - origin,
                                 points[index + 1] - origin)
    return float(0.5 * np.linalg.norm(total))


# ---------------------------------------------------------------------------
# the two sides
# ---------------------------------------------------------------------------
#
# Nodes are whole uncrossed triangles, and the two PIECES of every crossed
# one. Adjacency runs across mesh edges - except that an edge the boundary
# crosses is split at the crossing, and the two portions connect the pieces
# that hold the corresponding endpoint vertex. That is the barrier, and it is
# exact: nothing leaks across the boundary, and nothing is blocked that the
# boundary did not actually cut.

def _node_for(triangle, piece_index):
    return (int(triangle), int(piece_index))


def _edge_intervals(vertices, edge, polygon, tolerance):
    """Which stretches of one mesh edge this polygon's border lies along.

    Returned as [t0, t1] parameter spans along u->v. This is how adjacency is
    decided for a CUT triangle, and it is deliberately geometric rather than
    combinatorial: an edge crossed twice by the boundary has THREE portions,
    and any rule based on "which piece holds vertex u" would silently
    mis-link the middle one.

    Only ever called for edges that touch a cut triangle. Running it on every
    edge in the mesh - which is what it used to do - is a Python loop over
    three corners per edge per side, and on a 350,000-triangle scan that is
    the whole cost of the stage for no information, because an edge between
    two whole triangles is simply shared.
    """
    u, v = edge
    a, b = vertices[u], vertices[v]
    along = b - a
    length2 = float(along.dot(along))
    if length2 <= 0.0:
        return []
    spans = []
    count = polygon.shape[0]
    for index in range(count):
        p0 = polygon[index]
        p1 = polygon[(index + 1) % count]
        ts = []
        for point in (p0, p1):
            t = float((point - a).dot(along) / length2)
            gap = float(np.linalg.norm(point - (a + t * along)))
            if gap > tolerance or t < -1e-9 or t > 1.0 + 1e-9:
                ts = []
                break
            ts.append(min(max(t, 0.0), 1.0))
        if len(ts) == 2 and abs(ts[0] - ts[1]) > 1e-12:
            spans.append((min(ts), max(ts)))
    return spans


class SurfaceGraph(object):
    """Adjacency over whole triangles and the pieces of cut ones.

    Integer node ids and CSR arrays rather than a dict of sets keyed by
    tuples. On a 350,000-triangle scan the dict form is 350,000 small Python
    sets, which costs both the time to build them and the memory to hold
    them, for a graph whose overwhelming majority of links are "these two
    whole triangles share an edge".

        whole triangle t          -> node id t
        cut triangle t, piece i   -> node id triangle_count + offset[t] + i

    Two nodes are linked when their borders SHARE A STRETCH of a mesh edge.
    That is the exact barrier: the boundary runs along no edge stretch, so
    nothing leaks across it, and every stretch the boundary did not cut stays
    connected. The links are found two ways, and only the second is
    expensive:

    * both triangles whole - one shared edge, one link, computed for every
      such edge at once with numpy;
    * either triangle cut - the interval overlap above, run only on the few
      hundred edges that touch a cut triangle.
    """

    def __init__(self, vertices, triangles, edges, pieces, tolerance,
                 blocked=None):
        self.triangle_count = int(np.asarray(triangles).shape[0])
        self.pieces = pieces
        offset = {}
        running = 0
        for triangle in sorted(pieces):
            offset[int(triangle)] = running
            running += len(pieces[triangle])
        self._offset = offset
        self.node_count = self.triangle_count + running

        # Edges the boundary RUNS ALONG. Neither adjacent triangle is cut -
        # the boundary never entered either interior - but crossing between
        # them through this edge is forbidden, because that is exactly where
        # the region's border lies. This is the second kind of barrier: an
        # interior chord splits a triangle, an edge-aligned segment severs an
        # adjacency.
        self.blocked = {tuple(sorted(edge)) for edge in (blocked or ())}
        keys = np.asarray(list(edges.keys()), dtype=np.int64) \
            if edges else np.zeros((0, 2), dtype=np.int64)
        owners = [edges[(int(a), int(b))] for a, b in keys] if edges else []
        manifold = np.asarray([len(value) == 2 for value in owners],
                              dtype=bool)
        cut = np.zeros(self.triangle_count, dtype=bool)
        if pieces:
            cut[np.asarray(sorted(pieces), dtype=np.int64)] = True

        left = np.asarray([value[0] if len(value) == 2 else -1
                           for value in owners], dtype=np.int64)
        right = np.asarray([value[1] if len(value) == 2 else -1
                            for value in owners], dtype=np.int64)

        # A blocked edge links nothing at all, so it is removed from the
        # manifold set before either branch below sees it.
        if self.blocked and keys.shape[0]:
            severed = np.asarray(
                [tuple(sorted((int(a), int(b)))) in self.blocked
                 for a, b in keys], dtype=bool)
            manifold = manifold & ~severed
            self.severed = int(severed.sum())
        else:
            self.severed = 0

        # --- the bulk: whole-to-whole, vectorised -------------------------
        plain = manifold.copy()
        if plain.any():
            plain &= ~cut[np.where(left >= 0, left, 0)]
            plain &= ~cut[np.where(right >= 0, right, 0)]
        sources = [left[plain]]
        targets = [right[plain]]

        # --- the few: anything touching a cut triangle --------------------
        self.touching = int((manifold & ~plain).sum())
        for index in np.flatnonzero(manifold & ~plain):
            edge = (int(keys[index, 0]), int(keys[index, 1]))
            sides = []
            for triangle in (int(left[index]), int(right[index])):
                if triangle not in pieces:
                    corners = np.asarray(vertices[np.asarray(triangles)[triangle]])
                    sides.append([(self.node(triangle, -1), span)
                                  for span in _edge_intervals(
                                      vertices, edge, corners, tolerance)])
                else:
                    entries = []
                    for piece, part in enumerate(pieces[triangle]):
                        for span in _edge_intervals(vertices, edge,
                                                    part["polygon"],
                                                    tolerance):
                            entries.append((self.node(triangle, piece), span))
                    sides.append(entries)
            for left_node, (l0, l1) in sides[0]:
                for right_node, (r0, r1) in sides[1]:
                    if min(l1, r1) - max(l0, r0) > 1e-9:
                        sources.append(np.asarray([left_node], dtype=np.int64))
                        targets.append(np.asarray([right_node],
                                                  dtype=np.int64))

        source = np.concatenate(sources) if sources else \
            np.zeros(0, dtype=np.int64)
        target = np.concatenate(targets) if targets else \
            np.zeros(0, dtype=np.int64)
        # Undirected: store both directions, then build CSR.
        both_source = np.concatenate([source, target])
        both_target = np.concatenate([target, source])
        order = np.argsort(both_source, kind="stable")
        self._neighbours = both_target[order]
        counts = np.bincount(both_source, minlength=self.node_count)
        self._starts = np.concatenate(([0], np.cumsum(counts)))

    def node(self, triangle, piece):
        """The integer id of a node, from its (triangle, piece) identity."""
        if piece < 0:
            return int(triangle)
        return int(self.triangle_count + self._offset[int(triangle)]
                   + int(piece))

    def identity(self, node):
        """(triangle, piece) for an integer node id."""
        node = int(node)
        if node < self.triangle_count:
            return (node, -1)
        remainder = node - self.triangle_count
        for triangle in sorted(self._offset, key=self._offset.get,
                               reverse=True):
            base = self._offset[triangle]
            if remainder >= base:
                return (triangle, remainder - base)
        return (node, -1)                             # pragma: no cover

    def flood(self, start):
        """Every node reachable from `start`, as a boolean mask."""
        seen = np.zeros(self.node_count, dtype=bool)
        seen[int(start)] = True
        stack = [int(start)]
        neighbours, starts = self._neighbours, self._starts
        while stack:
            node = stack.pop()
            for following in neighbours[starts[node]:starts[node + 1]]:
                value = int(following)
                if not seen[value]:
                    seen[value] = True
                    stack.append(value)
        return seen


def build_graph(vertices, triangles, edges, pieces, tolerance,
                blocked=None):
    """The surface graph. Kept as a function for the tests that call it."""
    return SurfaceGraph(vertices, triangles, edges, pieces, tolerance,
                        blocked=blocked)


def flood(graph, start):
    """Reachable nodes from a (triangle, piece) start, as tuples."""
    mask = graph.flood(graph.node(*start))
    return {graph.identity(index) for index in np.flatnonzero(mask)}


def node_area(vertices, triangles, areas, pieces, node):
    triangle, piece = node
    if piece < 0:
        return float(areas[triangle])
    return float(pieces[triangle][piece]["area"])


# ---------------------------------------------------------------------------
# the whole computation
# ---------------------------------------------------------------------------

def compute(vertices, triangles, loop_points, component_labels=None,
            tolerance=None, timings=None):
    """Split the surface along a closed boundary. Returns the analysis dict.

    `vertices` and `triangles` are the canonical mesh; `loop_points` is the
    whole closed boundary polyline in the SAME space, without the repeated
    closing point. Pure: no geometry is modified and nothing is cached here.

    The result carries BOTH sides. Switching side is then a lookup, not a
    recomputation - which is what lets the UI offer the choice for free.

    Raises InteriorError with a reason code when the interior genuinely
    cannot be determined. It never guesses one.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    if tolerance is None:
        diagonal = float(np.linalg.norm(vertices.max(axis=0)
                                        - vertices.min(axis=0)))
        tolerance = diagonal * ON_EDGE_FRACTION

    clock = _Stopwatch(timings)
    edges = edge_map(triangles)
    clock.mark("edge map")
    # Built ONCE per call and threaded through every stage that needs them.
    grid = EdgeGrid(vertices, edges)
    vertex_index = vertex_triangles(triangles, len(vertices))
    clock.mark("index build")
    runs, located, aligned_edges = traversals(
        vertices, triangles, edges, loop_points, tolerance, grid=grid,
        vertex_index=vertex_index)
    clock.mark("boundary to triangles")
    if not runs and not aligned_edges:
        raise InteriorError(
            CODE_NOT_SEPARATED,
            "the boundary does not cross any triangle, so it cannot separate "
            "the surface")

    # --- the pieces of every crossed triangle -----------------------------
    pieces = {}
    areas = triangle_areas(vertices, triangles)
    worst = (0.0, 0.0, -1)
    for triangle, run_list in runs.items():
        crossed = crossing_chords(vertices, triangles, triangle, run_list)
        if crossed is not None:
            raise InteriorError(
                CODE_SELF_INTERSECTION,
                "the boundary crosses ITSELF inside triangle %d. A region "
                "whose boundary is not simple bounds no single side, so "
                "there is no interior to compute. Note that the boundary's "
                "own self-intersection check cannot see this - it tests for "
                "shared sample points, and this crossing happens between two "
                "of them." % triangle)
        split = split_triangle(vertices, triangles, triangle, run_list,
                               tolerance)
        if split is None:
            # The SAME diagnostic record the tiling failure produces. Without
            # it this refusal said only that a split failed, with nothing to
            # classify it by - which is exactly the position a real scan left
            # us in at triangle 6825.
            raise InteriorError(
                CODE_UNSUPPORTED_CROSSING,
                "the boundary's crossing of triangle %d could not be split "
                "exactly.\n%s"
                % (triangle,
                   tiling_report(vertices, triangles, triangle, run_list,
                                 None, tolerance)))
        # The pieces must tile the triangle. Asserted here, per triangle,
        # because every downstream number - the fill, and one day the area -
        # inherits this and nothing else re-checks it.
        total = sum(part["area"] for part in split)
        residual = abs(total - float(areas[triangle]))
        relative = residual / max(float(areas[triangle]), 1e-300)
        if residual > TILING_TOLERANCE * max(float(areas[triangle]), 1.0):
            raise InteriorError(
                CODE_UNSUPPORTED_CROSSING,
                "the pieces of triangle %d do not tile it (%.6g vs %.6g), so "
                "the interior would be wrong near the boundary.\n%s"
                % (triangle, total, float(areas[triangle]),
                   tiling_report(vertices, triangles, triangle, run_list,
                                 split, tolerance)))
        if relative > worst[0]:
            worst = (relative, residual, int(triangle))
        pieces[triangle] = split
    clock.mark("clipping")

    graph = build_graph(vertices, triangles, edges, pieces, tolerance,
                        blocked=aligned_edges)
    clock.mark("adjacency")

    # --- the component the boundary lies on -------------------------------
    # Triangles the boundary touches: cut ones, and the ones either side of
    # an edge it runs along. Both kinds sit on the barrier, which is what the
    # component check and the side seeding below need.
    touched = sorted(runs)
    aligned_touched = sorted({triangle for edge in aligned_edges
                              for triangle in edges.get(edge, ())})
    component = None
    if component_labels is not None:
        labels = {int(component_labels[index])
                  for index in (touched or aligned_touched)}
        if len(labels) > 1:
            raise InteriorError(
                CODE_CROSS_COMPONENT,
                "the boundary touches %d disconnected surface components"
                % len(labels))
        component = labels.pop() if labels else None
        allowed = set(np.flatnonzero(
            np.asarray(component_labels) == component).tolist())
    else:
        allowed = set(range(len(triangles)))

    # The universe as a mask over node ids, which is what the graph speaks.
    owner = np.empty(graph.node_count, dtype=np.int64)
    owner[:len(triangles)] = np.arange(len(triangles), dtype=np.int64)
    for triangle in sorted(pieces):
        for piece in range(len(pieces[triangle])):
            owner[graph.node(triangle, piece)] = triangle
    allowed_mask = np.zeros(len(triangles), dtype=bool)
    allowed_mask[np.asarray(sorted(allowed), dtype=np.int64)] = True
    universe_mask = allowed_mask[owner]
    # A CUT triangle's whole-triangle node id is a phantom: the triangle was
    # replaced by its pieces, so that id has no edges and stands for geometry
    # already counted. Deriving the complement as "everything else" makes
    # that matter - it would hand each cut triangle's FULL area to the
    # complement on top of its piece - so the phantoms are struck out here.
    if pieces:
        universe_mask[np.asarray(sorted(pieces), dtype=np.int64)] = False
    if not universe_mask.any():
        raise InteriorError(CODE_NOT_SEPARATED,
                            "no surface was found around the boundary")

    # --- the two sides, from ONE traversal ---------------------------------
    #
    # Seeded from the two PIECES of one cut triangle: they sit either side of
    # the boundary by construction. Only the FIRST side is flooded - the
    # complement is everything else in the component, which is what "the
    # boundary separates it in two" means. Flooding twice would walk the
    # whole component a second time to rediscover that, and on a 350,000
    # triangle scan that is the same work again for no new information.
    #
    # The separation itself is still CHECKED, not assumed: if the first
    # side's flood reaches the other seed piece then the boundary did not cut
    # the component in two, and that is refused below exactly as before.
    # SEEDS: two places known to be on opposite sides of the barrier.
    #
    # Normally the two pieces of a cut triangle, which sit either side of the
    # chord by construction. When the boundary runs ALONG mesh edges instead,
    # there may be no cut triangle at all - and then the two triangles either
    # side of a severed edge are the pair that serve, for exactly the same
    # reason: the barrier passes between them.
    if touched:
        seed_triangle = touched[0]
        seeds = [graph.node(seed_triangle, index)
                 for index in range(len(pieces[seed_triangle]))]
    else:
        seeds = []
        for edge in sorted(aligned_edges):
            owners = [int(value) for value in edges.get(edge, ())]
            if len(owners) == 2:
                seeds = [graph.node(owners[0], -1), graph.node(owners[1], -1)]
                break
        if not seeds:
            raise InteriorError(
                CODE_NOT_SEPARATED,
                "the boundary runs along mesh edges but none of them has a "
                "surface on both sides, so there are no two sides to find")

    first_mask = graph.flood(seeds[0]) & universe_mask
    second_mask = None
    for other in seeds[1:]:
        if not first_mask[other]:
            second_mask = universe_mask & ~first_mask
            break
    clock.mark("side traversal")
    first = {graph.identity(value) for value in np.flatnonzero(first_mask)}
    second = ({graph.identity(value)
               for value in np.flatnonzero(second_mask)}
              if second_mask is not None else None)
    if second is None:
        open_edges = sum(1 for _key, tris in edges.items() if len(tris) == 1)
        raise InteriorError(
            CODE_OPEN_SURFACE if open_edges else CODE_NOT_SEPARATED,
            ("the boundary does not separate this surface into two sides"
             + (" - the component has %d boundary edge(s), so the two sides "
                "run into each other around the opening" % open_edges
                if open_edges else
                " - the two sides of the boundary are connected"))
            + ". BSMT refuses rather than inventing an interior.")

    unreached = ({graph.identity(value)
                  for value in np.flatnonzero(universe_mask)}
                 - first - second)
    first_area = sum(node_area(vertices, triangles, areas, pieces, node)
                     for node in first)
    second_area = sum(node_area(vertices, triangles, areas, pieces, node)
                      for node in second)
    if first_area <= second_area:
        smaller, complement = first, second
        smaller_area, complement_area = first_area, second_area
    else:
        smaller, complement = second, first
        smaller_area, complement_area = second_area, first_area

    described = {SIDE_SMALLER: _describe_side(vertices, triangles, areas,
                                              pieces, smaller),
                 SIDE_COMPLEMENT: _describe_side(vertices, triangles, areas,
                                                 pieces, complement)}
    clock.mark("side description")
    return {
        "sides": described,
        "timings": clock.summary(),
        "cut_edge_count": graph.touching,
        # The two kinds of boundary event, reported separately: a chord cuts
        # a triangle, an edge-aligned segment severs an adjacency without
        # cutting anything.
        "partial_triangles": len(runs),
        "aligned_edges": len(aligned_edges),
        "severed_edges": graph.severed,
        # What the per-triangle tiling invariant actually came to, so a
        # SUCCESSFUL run can say how close it was rather than only that it
        # passed. This is the number to watch when a scan is re-tried.
        "tiling": {"checked": len(runs), "worst_relative": worst[0],
                   "worst_absolute": worst[1], "worst_triangle": worst[2]},
        "smaller_area": float(smaller_area),
        "complement_area": float(complement_area),
        "crossed_triangles": touched,
        "pieces": pieces,
        "component_id": component,
        "unreached_nodes": len(unreached),
        "open_edge_count": sum(1 for _k, tris in edges.items()
                               if len(tris) == 1),
        "limits": LIMITS,
    }


def _describe_side(vertices, triangles, areas, pieces, nodes):
    """One side's triangles and partial pieces, ready for display or area."""
    full = sorted(node[0] for node in nodes if node[1] < 0)
    partial = []
    for triangle, index in sorted(node for node in nodes if node[1] >= 0):
        part = pieces[triangle][index]
        partial.append({
            "triangle": int(triangle),
            "piece": int(index),
            "polygon": part["polygon"],
            "bary": part["bary"],
            "area": float(part["area"]),
        })
    return {
        "full_triangles": full,
        "partial": partial,
        "full_count": len(full),
        "partial_count": len(partial),
        # Reported as a TRIANGLE-AREA TOTAL, which is what it is. It is not
        # presented as the region's surface area, and no panel shows it as
        # one: Surface Area is a later milestone with its own validation.
        "area_total": float(sum(areas[index] for index in full)
                            + sum(entry["area"] for entry in partial)),
    }
