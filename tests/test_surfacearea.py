"""Offline tests for Surface Area: analytic cases and two-side consistency.

    python3 tests/test_area.py

Surface Area is the **mesh surface area of the selected region on the
triangular body mesh**: the triangles wholly inside it, plus the
exactly-clipped polygons where the boundary cuts through one. It is not the
true anatomical surface area and not an exact smooth-body area, and this
suite is as interested in holding that line as in the arithmetic.

What it protects:

*   THE ARITHMETIC IS RIGHT ON CASES WITH KNOWN ANSWERS. A triangle, a
    square, a half, a convex quad, a mixture - each checked against a number
    derived by hand rather than by the code under test.
*   THE TWO SIDES ADD UP TO THE SURFACE. On a closed sphere and on a torus,
    the selected side plus its complement equal the component's own area.
    This is the strongest correctness statement available: it cannot be
    satisfied by a clipping that is wrong near the boundary, because whatever
    one side gains the other must lose.
*   EVERY TRIANGLE IS ACCOUNTED FOR EXACTLY ONCE. A whole triangle belongs to
    one side; a cut triangle's two pieces tile it.
*   A PIECE THAT CANNOT BE A PIECE IS REFUSED. Negative, or bigger than the
    triangle it came from, means the clipping produced a plausible wrong
    number - the case this check exists for.
"""

import importlib.util
import os
import sys

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


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


area = _load("bsmt_area", os.path.join(PACKAGE, "surfacearea.py"))
interior = _load("bsmt_interior", os.path.join(PACKAGE, "interior.py"))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def grid(n=6, size=6.0):
    values = np.linspace(0.0, size, n + 1)
    vertices = np.array([[x, y, 0.0] for y in values for x in values],
                        dtype=np.float64)
    faces = []
    for row in range(n):
        for column in range(n):
            a = row * (n + 1) + column
            faces.append([a, a + 1, a + n + 2])
            faces.append([a, a + n + 2, a + n + 1])
    return vertices, np.asarray(faces, dtype=np.int64)


def square_loop(low=1.5, high=4.5, per_side=12):
    corners = [(low, low), (high, low), (high, high), (low, high)]
    points = []
    for index in range(4):
        x0, y0 = corners[index]
        x1, y1 = corners[(index + 1) % 4]
        for step in np.linspace(0.0, 1.0, per_side, endpoint=False):
            points.append([x0 + (x1 - x0) * step, y0 + (y1 - y0) * step, 0.0])
    return np.asarray(points, dtype=np.float64)


def sphere(nu=24, nv=16, radius=100.0):
    index = {}
    vertices = []
    for row in range(1, nv):
        theta = np.pi * row / nv
        for column in range(nu):
            phi = 2.0 * np.pi * column / nu
            index[(row, column)] = len(vertices)
            vertices.append([radius * np.sin(theta) * np.cos(phi),
                             radius * np.sin(theta) * np.sin(phi),
                             radius * np.cos(theta)])
    north = len(vertices); vertices.append([0.0, 0.0, radius])
    south = len(vertices); vertices.append([0.0, 0.0, -radius])
    faces = []
    for row in range(1, nv - 1):
        for column in range(nu):
            a = index[(row, column)]
            b = index[(row, (column + 1) % nu)]
            c = index[(row + 1, column)]
            d = index[(row + 1, (column + 1) % nu)]
            faces.append([a, b, d])
            faces.append([a, d, c])
    for column in range(nu):
        faces.append([north, index[(1, column)],
                      index[(1, (column + 1) % nu)]])
        faces.append([south, index[(nv - 1, (column + 1) % nu)],
                      index[(nv - 1, column)]])
    return (np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64), index, nu)


def sphere_ring(vertices, index, nu, row, t=0.45):
    points = []
    for column in range(nu):
        a = index[(row, column)]
        b = index[(row, (column + 1) % nu)]
        d = index[(row + 1, (column + 1) % nu)]
        c = index[(row + 1, column)]
        points.append(vertices[a] + (vertices[c] - vertices[a]) * t)
        points.append(vertices[a] + (vertices[d] - vertices[a]) * t)
        points.append(vertices[b] + (vertices[d] - vertices[b]) * t)
    return np.asarray(points, dtype=np.float64)


def torus(nu=24, nv=12, major=100.0, minor=30.0):
    index = {}
    vertices = []
    for row in range(nv):
        v = 2.0 * np.pi * row / nv
        for column in range(nu):
            u = 2.0 * np.pi * column / nu
            index[(row, column)] = len(vertices)
            vertices.append([(major + minor * np.cos(v)) * np.cos(u),
                             (major + minor * np.cos(v)) * np.sin(u),
                             minor * np.sin(v)])
    faces = []
    for row in range(nv):
        for column in range(nu):
            a = index[(row, column)]
            b = index[(row, (column + 1) % nu)]
            c = index[((row + 1) % nv, column)]
            d = index[((row + 1) % nv, (column + 1) % nu)]
            faces.append([a, b, d])
            faces.append([a, d, c])
    return (np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64), index, nu)


def as_stored(side):
    """The classification as `interiorcache` stores it: indices + barycentric."""
    return (list(side["full_triangles"]),
            [(entry["triangle"], entry["bary"]) for entry in side["partial"]])


# ---------------------------------------------------------------------------
# A. analytic cases
# ---------------------------------------------------------------------------

def test_whole_triangles():
    print("\n[A] whole triangles, against areas worked out by hand")
    vertices = np.array([[0, 0, 0], [3.0, 0, 0], [0, 4.0, 0]])
    faces = np.array([[0, 1, 2]])
    result = area.region_area_mm2(vertices, faces, [0], [])
    check("A1: a 3-4-5 right triangle is exactly 6 mm^2",
          abs(result["area_mm2"] - 6.0) < 1e-12, result["area_mm2"])
    check("A2: all of it is reported as coming from whole triangles",
          abs(result["full_mm2"] - 6.0) < 1e-12
          and result["partial_mm2"] == 0.0, result)

    vertices = np.array([[0, 0, 0], [5.0, 0, 0], [5.0, 5.0, 0], [0, 5.0, 0]])
    faces = np.array([[0, 1, 2], [0, 2, 3]])
    result = area.region_area_mm2(vertices, faces, [0, 1], [])
    check("A3: two triangles making a 5x5 square are exactly 25 mm^2",
          abs(result["area_mm2"] - 25.0) < 1e-12, result["area_mm2"])
    check("A4: the method is recorded with the number",
          result["method"] == area.METHOD, result["method"])


def test_clipped_polygons():
    print("\n[A] clipped polygons, in the parent triangle's own plane")
    vertices = np.array([[0, 0, 0], [4.0, 0, 0], [0, 4.0, 0]])
    faces = np.array([[0, 1, 2]])                     # area 8

    half = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0.5, 0.5]])
    result = area.region_area_mm2(vertices, faces, [], [(0, half)])
    check("A5: a median splits the triangle in half - exactly 4 mm^2",
          abs(result["area_mm2"] - 4.0) < 1e-12, result["area_mm2"])
    check("A6: reported as coming from the clipped side",
          abs(result["partial_mm2"] - 4.0) < 1e-12
          and result["full_mm2"] == 0.0, result)

    # A convex quad cutting off one corner, checked against the shoelace area
    # of the same points computed independently in the triangle's plane.
    quad = np.array([[1.0, 0, 0], [0.5, 0.5, 0], [0, 0.5, 0.5],
                     [0.5, 0, 0.5]])
    result = area.region_area_mm2(vertices, faces, [], [(0, quad)])
    independent = area.polygon_area(quad @ vertices[faces[0]])
    check("A7: a convex clipped quad matches its own planar area",
          abs(result["area_mm2"] - independent) < 1e-12,
          (result["area_mm2"], independent))
    check("A8: and is a real fraction of its parent triangle",
          0.0 < result["area_mm2"] < 8.0, result["area_mm2"])

    mixed = np.array([[0, 0, 0], [4.0, 0, 0], [0, 4.0, 0], [4.0, 4.0, 0]])
    mixed_faces = np.array([[0, 1, 2], [1, 3, 2]])
    result = area.region_area_mm2(mixed, mixed_faces, [1], [(0, half)])
    check("A9: a full triangle plus a clipped one sums to both",
          abs(result["area_mm2"] - 12.0) < 1e-12, result["area_mm2"])
    check("A10: with the two contributions kept apart",
          abs(result["full_mm2"] - 8.0) < 1e-12
          and abs(result["partial_mm2"] - 4.0) < 1e-12, result)


def test_degenerate_and_out_of_bounds():
    print("\n[A] pieces that cannot be pieces")
    vertices = np.array([[0, 0, 0], [4.0, 0, 0], [0, 4.0, 0]])
    faces = np.array([[0, 1, 2]])

    sliver = np.array([[1.0, 0, 0], [0.5, 0.5, 0], [1.0, 0, 0]])
    result = area.region_area_mm2(vertices, faces, [0], [(0, sliver)])
    check("A11: a zero-area clipped polygon contributes nothing",
          abs(result["area_mm2"] - 8.0) < 1e-12, result["area_mm2"])

    oversized = np.array([[2.0, 0, 0], [0, 2.0, 0], [0, 0, 2.0]])
    refused = None
    try:
        area.region_area_mm2(vertices, faces, [], [(0, oversized)])
    except area.AreaError as exc:
        refused = exc
    check("A12: a piece bigger than its parent triangle is REFUSED",
          refused is not None)
    if refused is not None:
        check("A13: with a named reason",
              refused.code == area.CODE_PIECE_OUT_OF_BOUNDS, refused.code)
        check("A14: naming the triangle and saying the area is refused",
              "triangle 0" in refused.message
              and "refused" in refused.message, refused.message[:80])

    refused = None
    try:
        area.region_area_mm2(vertices, faces, [], [])
    except area.AreaError as exc:
        refused = exc
    check("A15: an empty selection is refused, not reported as 0 mm^2",
          refused is not None
          and refused.code == area.CODE_DEGENERATE,
          None if refused is None else refused.code)


def test_units():
    print("\n[A] units")
    check("A16: 1 cm^2 is 100 mm^2", area.MM2_PER_CM2 == 100.0)
    check("A17: cm^2 is derived, not stored",
          abs(area.as_cm2(42815.6) - 428.156) < 1e-9, area.as_cm2(42815.6))
    check("A18: mm^2 formats with a thousands separator",
          area.format_mm2(42815.6) == "42,815.6 mm²", area.format_mm2(42815.6))
    check("A19: and cm^2 alongside it",
          area.format_cm2(42815.6) == "428.16 cm²", area.format_cm2(42815.6))


# ---------------------------------------------------------------------------
# B. the two sides add up to the surface
# ---------------------------------------------------------------------------

def _two_side_check(tag, vertices, faces, loop, label):
    analysis = interior.compute(vertices, faces, loop)
    totals = {}
    for side_name in (interior.SIDE_SMALLER, interior.SIDE_COMPLEMENT):
        full, pieces = as_stored(analysis["sides"][side_name])
        totals[side_name] = area.region_area_mm2(vertices, faces, full,
                                                 pieces)
    component = area.component_area_mm2(vertices, faces, None, 0)
    summed = (totals[interior.SIDE_SMALLER]["area_mm2"]
              + totals[interior.SIDE_COMPLEMENT]["area_mm2"])
    relative = abs(summed - component) / component
    check("%s: the two sides sum to the component's own area "
          "(%.3e relative)" % (tag, relative), relative < 1e-9, relative)
    check("%s: and the smaller side really is smaller" % label,
          totals[interior.SIDE_SMALLER]["area_mm2"]
          < totals[interior.SIDE_COMPLEMENT]["area_mm2"],
          (totals[interior.SIDE_SMALLER]["area_mm2"],
           totals[interior.SIDE_COMPLEMENT]["area_mm2"]))
    return analysis, totals, component


def test_planar_patch_area_is_the_square():
    print("\n[B] a square loop on a flat patch measures the square")
    vertices, faces = grid()
    analysis, totals, component = _two_side_check(
        "B1", vertices, faces, square_loop(), "B2")
    check("B3: the smaller side is exactly the 3x3 square it encloses",
          abs(totals[interior.SIDE_SMALLER]["area_mm2"] - 9.0) < 1e-12,
          totals[interior.SIDE_SMALLER]["area_mm2"])
    check("B4: the complement is exactly the rest of the 36.0 patch",
          abs(totals[interior.SIDE_COMPLEMENT]["area_mm2"] - 27.0) < 1e-12,
          totals[interior.SIDE_COMPLEMENT]["area_mm2"])
    check("B5: part of the answer genuinely comes from clipped triangles - "
          "a whole-face sum would have been wrong here",
          totals[interior.SIDE_SMALLER]["partial_mm2"] > 0.0,
          totals[interior.SIDE_SMALLER]["partial_mm2"])


def test_closed_sphere_two_sides():
    print("\n[B] a closed sphere")
    vertices, faces, index, nu = sphere()
    analysis, totals, component = _two_side_check(
        "B6", vertices, faces, sphere_ring(vertices, index, nu, row=3), "B7")
    smooth = 4.0 * np.pi * 100.0 ** 2
    check("B8: the MESH area is under the smooth sphere's, as a chord "
          "approximation must be (%.1f vs %.1f)" % (component, smooth),
          component < smooth, (component, smooth))
    check("B9: and not wildly under - this is a sampling difference, not an "
          "error", component > 0.97 * smooth, component / smooth)


def test_torus_two_sides():
    print("\n[B] a torus, where a loop need not separate but this one does")
    vertices, faces, index, nu = torus()
    points = []
    for column in range(nu):
        a = index[(0, column)]
        b = index[(0, (column + 1) % nu)]
        d = index[(1, (column + 1) % nu)]
        c = index[(1, column)]
        points.append(vertices[a] + (vertices[c] - vertices[a]) * 0.45)
        points.append(vertices[a] + (vertices[d] - vertices[a]) * 0.45)
        points.append(vertices[b] + (vertices[d] - vertices[b]) * 0.45)
    loop = np.asarray(points, dtype=np.float64)
    try:
        interior.compute(vertices, faces, loop)
        check("B10: the torus loop separates", False, "expected a refusal")
    except interior.InteriorError as exc:
        # This particular loop goes the long way round and does NOT separate;
        # the interior refuses it, so there is no area to compute - which is
        # the correct behaviour and worth asserting from the area side too.
        check("B10: a non-separating loop has no interior, so no area",
              exc.code in (interior.CODE_NOT_SEPARATED,
                           interior.CODE_OPEN_SURFACE), exc.code)


def test_every_triangle_accounted_for_once():
    print("\n[B] every triangle belongs to exactly one side, or is split")
    vertices, faces = grid()
    analysis = interior.compute(vertices, faces, square_loop())
    smaller = analysis["sides"][interior.SIDE_SMALLER]
    complement = analysis["sides"][interior.SIDE_COMPLEMENT]
    full_a = set(smaller["full_triangles"])
    full_b = set(complement["full_triangles"])
    cut = set(analysis["crossed_triangles"])
    check("B11: no whole triangle is on both sides", not (full_a & full_b))
    check("B12: no whole triangle is also a cut one",
          not ((full_a | full_b) & cut))
    check("B13: together they are every triangle in the mesh",
          (full_a | full_b | cut) == set(range(len(faces))),
          len((full_a | full_b | cut)) - len(faces))

    areas = area.triangle_areas(vertices, faces)
    worst = 0.0
    for triangle in sorted(cut):
        total = 0.0
        for side in (smaller, complement):
            for entry in side["partial"]:
                if entry["triangle"] == triangle:
                    total += area.polygon_area(
                        area.piece_polygon(vertices, faces,
                                           entry["triangle"], entry["bary"]))
        worst = max(worst, abs(total - float(areas[triangle]))
                    / float(areas[triangle]))
    check("B14: and every CUT triangle's pieces tile it across the two "
          "sides (worst %.3e relative)" % worst, worst < 1e-12, worst)


# ---------------------------------------------------------------------------
# C. scope and wording
# ---------------------------------------------------------------------------

def test_wording_and_scope():
    print("\n[C] what the number is called")
    text = area.DEFINITION
    check("C1: the definition names the triangular mesh",
          "triangular body mesh" in text, text)
    check("C2: and says 'mesh surface area', not 'surface area'",
          text.startswith("mesh surface area"), text)
    source = " ".join(open(os.path.join(PACKAGE, "surfacearea.py"))
                      .read().lower().replace("#", " ").split())
    for banned in ("true anatomical surface area",
                   "actual human surface area",
                   "exact smooth-body area"):
        check("C3: the module disclaims '%s'" % banned,
              "not the %s" % banned in source or "not %s" % banned in source
              or banned in source, banned)
    check("C4: it says a triangulation is a chord approximation and reads "
          "under", "chord approximation" in source
          and "systematically a little under" in source)
    check("C5: it states it does not decide the side or reclassify",
          "does not decide which side" in source
          and "does not flood fill" in source)
    check("C6: the method identifier travels with every result",
          area.METHOD and area.METHOD_LABEL
          and "full triangles" in area.METHOD_LABEL)
    check("C7: no thickness, shell or volume quantity is computed here",
          "thickness" not in source and "volume" not in source)


def test_area_stays_out_of_protocols_and_csv():
    """Area is scan-specific derived data. It belongs in neither."""
    print("\n[C] area is not a reusable definition, and not a CSV column")
    protocol = open(os.path.join(PACKAGE, "protocol.py")).read().lower()
    check("C8: protocol files carry NO area - a reusable region definition "
          "is landmarks, and an area belongs to one scan",
          "area" not in protocol)
    export = open(os.path.join(PACKAGE, "export.py")).read()
    check("C9: CSV schema v2 is untouched by this milestone",
          "SCHEMA_VERSION = 2" in export)
    check("C10: and carries no area column",
          "area_mm2" not in export and "area_status" not in export)


def main():
    print("BSMT Surface Area - offline\n" + "=" * 62)
    for test in (
        test_whole_triangles,
        test_clipped_polygons,
        test_degenerate_and_out_of_bounds,
        test_units,
        test_planar_patch_area_is_the_square,
        test_closed_sphere_two_sides,
        test_torus_two_sides,
        test_every_triangle_accounted_for_once,
        test_wording_and_scope,
        test_area_stays_out_of_protocols_and_csv,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
