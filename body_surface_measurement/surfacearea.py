"""Surface Area: the mesh surface area of a selected Surface Region.

    ordered landmarks -> closed boundary -> Surface Interior -> SURFACE AREA

What the number IS
------------------
The **mesh surface area of the selected region on the triangular body mesh**.
It is the sum of the areas of the triangles wholly inside the region, plus
the areas of the exactly-clipped polygons where the boundary cuts through a
triangle.

What it is NOT, stated once and repeated wherever it is displayed: it is not
the true anatomical surface area, not the actual human surface area, and not
an exact smooth-body area. A triangulated scan is a chord approximation of a
curved surface, so its area is systematically a little under the smooth
surface it was sampled from - the same property `docs/VALIDATION.md` §0.3
records for distances. This module measures the mesh it is given.

Where the geometry comes from
-----------------------------
The classification `interior.compute` already produced, and nothing else.
This module does not decide which side is the region, does not flood fill,
does not clip anything and does not re-run any part of the interior
analysis. If there is no current interior, there is no area - that is a
refusal, not a reason to derive one.

Units, and why the barycentric form matters
-------------------------------------------
The interior is analysed in the scan's OBJECT-LOCAL space, so its own areas
are in local units squared - not millimetres squared, and not convertible by
one scale factor when an object carries a non-uniform scale.

Each clipped piece is stored as BARYCENTRIC coordinates in its parent
triangle, which makes this a non-problem: the polygon is rebuilt against
that triangle's corners in whatever space is wanted. Give it the canonical
mesh's PHYSICAL-MILLIMETRE vertices and the answer is in mm^2, exactly, with
no projection and no rescaling of an already-computed number.

Accumulation is float64 throughout.

No bpy, no solver, and no geometry is modified anywhere.
"""

import numpy as np

#: The method this result was produced by, recorded with every area so a
#: number can always be traced to how it was obtained.
METHOD = "MESH_TRIANGLE_AND_CLIPPED_POLYGON"
METHOD_LABEL = ("Triangular mesh surface area (full triangles + clipped "
                "boundary polygons)")

#: One phrase, used everywhere a result is shown or written.
DEFINITION = ("mesh surface area of the selected region on the triangular "
              "body mesh")

STATUS_NONE = 'NONE'
STATUS_VALID = 'VALID'
STATUS_STALE = 'STALE'
STATUS_INVALID = 'INVALID'

STATUS_ITEMS = (
    (STATUS_NONE, "Not Computed", "The area has not been computed yet"),
    (STATUS_VALID, "Valid",
     "Computed from this interior on this geometry"),
    (STATUS_STALE, "Stale",
     "The interior, the boundary or the geometry changed after the area was "
     "computed"),
    (STATUS_INVALID, "Invalid", "The area could not be computed"),
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
CODE_NOT_COMPUTED = 'AREA_NOT_COMPUTED'
CODE_NO_INTERIOR = 'AREA_NO_INTERIOR'
CODE_INTERIOR_NOT_VALID = 'AREA_INTERIOR_NOT_VALID'
CODE_INTERIOR_CHANGED = 'AREA_INTERIOR_CHANGED'
CODE_GEOMETRY_MISMATCH = 'AREA_GEOMETRY_MISMATCH'
CODE_PIECE_OUT_OF_BOUNDS = 'AREA_PIECE_OUT_OF_BOUNDS'
CODE_DEGENERATE = 'AREA_DEGENERATE'

CODE_LABELS = {
    CODE_NOT_COMPUTED: "the area has not been computed",
    CODE_NO_INTERIOR: "there is no computed interior to measure",
    CODE_INTERIOR_NOT_VALID: "the interior is not currently valid",
    CODE_INTERIOR_CHANGED: "the interior changed after the area was computed",
    CODE_GEOMETRY_MISMATCH: "the area was computed on different geometry",
    CODE_PIECE_OUT_OF_BOUNDS: "a clipped piece is bigger than the triangle "
                              "it came from",
    CODE_DEGENERATE: "the interior has no measurable area",
}

#: A clipped piece may not exceed its parent triangle by more than this
#: fraction of that triangle's area, nor be negative by more than it.
#:
#: Matched to `interior.TILING_TOLERANCE`, which is the precision the
#: clipping itself is guaranteed to, because a bound tighter than the
#: guarantee would fail on correct geometry and a looser one would not catch
#: anything the guarantee does not already permit.
PIECE_TOLERANCE = 1e-5


class AreaError(Exception):
    """An area cannot be computed, with a reason code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# the measurements themselves
# ---------------------------------------------------------------------------

def triangle_areas(vertices, triangles):
    """Area of every triangle: 0.5 * ||(b-a) x (c-a)||, in `vertices` units."""
    corners = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]
    cross = np.cross(corners[:, 1] - corners[:, 0],
                     corners[:, 2] - corners[:, 0])
    return 0.5 * np.linalg.norm(cross, axis=1)


def polygon_area(polygon):
    """Area of a planar polygon in 3D, by the Newell sum.

    Summing the cross products and taking the norm ONCE keeps this in the
    polygon's own plane. Projecting to a global axis pair would be the
    obvious alternative and is wrong here for the same reason it is wrong
    everywhere else in BSMT: the answer would depend on how the scan happens
    to be oriented.
    """
    points = np.asarray(polygon, dtype=np.float64)
    if points.shape[0] < 3:
        return 0.0
    origin = points[0]
    total = np.zeros(3, dtype=np.float64)
    for index in range(1, points.shape[0] - 1):
        total = total + np.cross(points[index] - origin,
                                 points[index + 1] - origin)
    return float(0.5 * np.linalg.norm(total))


def piece_polygon(vertices_mm, triangles, parent, bary):
    """Rebuild one clipped piece in millimetres from its barycentric form.

    THE step that makes the result mm^2 without rescaling anything: the
    piece's shape is stored against its parent triangle, so it is
    reconstructed against that triangle's millimetre corners.
    """
    corners = np.asarray(vertices_mm, dtype=np.float64)[
        np.asarray(triangles)[int(parent)]]
    weights = np.asarray(bary, dtype=np.float64)
    return weights @ corners


def region_area_mm2(vertices_mm, triangles, full_triangles, pieces,
                    tolerance=PIECE_TOLERANCE):
    """The selected region's mesh surface area, in mm^2.

    `vertices_mm` must be the canonical mesh's PHYSICAL-MILLIMETRE vertices;
    `full_triangles` and `pieces` are the stored classification. Returns a
    dict; raises AreaError when a piece cannot be a piece of its triangle.

    Every sum is float64. The two totals are kept apart rather than
    accumulated together, because "how much of this came from triangles the
    boundary cut" is the number that says how much the exact clipping
    actually mattered on a given scan.
    """
    vertices_mm = np.asarray(vertices_mm, dtype=np.float64)
    triangles = np.asarray(triangles)
    areas = triangle_areas(vertices_mm, triangles)

    indices = np.asarray(list(full_triangles), dtype=np.int64)
    full_mm2 = float(areas[indices].sum()) if indices.size else 0.0

    partial_mm2 = 0.0
    problems = []
    for parent, bary in pieces:
        polygon = piece_polygon(vertices_mm, triangles, parent, bary)
        value = polygon_area(polygon)
        limit = float(areas[int(parent)])
        # A piece is PART of a triangle. Anything outside that is a clipping
        # fault that has produced a plausible number, which is exactly the
        # kind this check exists to refuse rather than report.
        if value < -tolerance * max(limit, 1.0):
            problems.append((int(parent), value, limit, "negative"))
        elif value > limit * (1.0 + tolerance) + tolerance:
            problems.append((int(parent), value, limit, "larger than its "
                                                        "parent triangle"))
        partial_mm2 += value

    if problems:
        parent, value, limit, why = problems[0]
        raise AreaError(
            CODE_PIECE_OUT_OF_BOUNDS,
            "a clipped piece of triangle %d has an area of %.6g mm^2, which "
            "is %s (%.6g mm^2)%s. The area is refused rather than reported."
            % (parent, value, why, limit,
               "" if len(problems) == 1 else " - and %d other piece(s) are "
               "out of bounds too" % (len(problems) - 1)))

    total = full_mm2 + partial_mm2
    if not np.isfinite(total) or total <= 0.0:
        raise AreaError(
            CODE_DEGENERATE,
            "the selected interior measures %.6g mm^2, which is not a usable "
            "area" % total)

    return {
        "area_mm2": float(total),
        "full_mm2": float(full_mm2),
        "partial_mm2": float(partial_mm2),
        "full_count": int(indices.size),
        "partial_count": int(len(pieces)),
        "method": METHOD,
        "definition": DEFINITION,
    }


def component_area_mm2(vertices_mm, triangles, component_labels, component):
    """Total mesh area of one connected component, in mm^2.

    For VALIDATION, not for display as a region result: the two sides of a
    boundary must add up to this, and that is the strongest check available
    on a closed component.
    """
    areas = triangle_areas(vertices_mm, triangles)
    if component_labels is None:
        return float(areas.sum())
    labels = np.asarray(component_labels)
    return float(areas[labels == int(component)].sum())


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

#: 1 cm^2 = 100 mm^2. The authoritative value is mm^2 and nothing else is
#: stored; every other unit is derived at display time.
MM2_PER_CM2 = 100.0


def as_cm2(area_mm2):
    return float(area_mm2) / MM2_PER_CM2


def format_mm2(area_mm2):
    return "{:,.1f} mm²".format(float(area_mm2))


def format_cm2(area_mm2):
    return "{:,.2f} cm²".format(as_cm2(area_mm2))
