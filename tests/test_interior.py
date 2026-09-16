"""Offline tests for Surface Interior: which side of a boundary is the region.

    python3 tests/test_interior.py

Everything that DECIDES which triangles and which PARTS of triangles make up
a surface region lives in ``interior.py`` and is pure, so it is tested here
without Blender. The Blender-side cache, the operator, the fill and the
thickness preview are tested in tests/test_surface_interior_blender.py.

The properties this suite exists to protect:

*   THE PIECES TILE THE TRIANGLE EXACTLY. For every triangle the boundary
    crosses, the two clipped pieces sum to the triangle's own area to
    floating-point precision. This is the property a later Surface Area
    milestone rests on entirely, so it is asserted per triangle and in
    aggregate, not eyeballed.
*   THE WHOLE SURFACE IS ACCOUNTED FOR. The two sides together are the whole
    component: no area is created, none is lost, and nothing is left
    unreached.
*   NEITHER SIDE IS "INSIDE". Both are computed, both are returned, and the
    smaller one is a DEFAULT rather than a claim.
*   AMBIGUITY IS REFUSED. A loop that does not cut the surface in two gets a
    reason code, not a guess.

The fixtures matter. A boundary computed by BSMT is an exact geodesic, whose
breakpoints lie ON MESH EDGES - measured at 4e-6 of the bounding-box diagonal
on a real computed boundary. The loops below are built the same way, on mesh
edges with consecutive points sharing a triangle, because a loop of points
floating near the surface would test something BSMT never produces.
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


interior = _load("bsmt_interior", os.path.join(PACKAGE, "interior.py"))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def grid(n=6, size=6.0):
    """A planar triangulated patch: n x n quads, each split into two."""
    values = np.linspace(0.0, size, n + 1)
    vertices = np.array([[x, y, 0.0] for y in values for x in values],
                        dtype=np.float64)
    faces = []
    for row in range(n):
        for column in range(n):
            a = row * (n + 1) + column
            b, c, d = a + 1, a + (n + 1), a + (n + 2)
            faces.append([a, b, d])
            faces.append([a, d, c])
    return vertices, np.asarray(faces, dtype=np.int64)


def square_loop(low=1.5, high=4.5, per_side=12):
    """A square loop in the z=0 plane, densely sampled."""
    corners = [(low, low), (high, low), (high, high), (low, high)]
    points = []
    for index in range(4):
        x0, y0 = corners[index]
        x1, y1 = corners[(index + 1) % 4]
        for step in np.linspace(0.0, 1.0, per_side, endpoint=False):
            points.append([x0 + (x1 - x0) * step, y0 + (y1 - y0) * step, 0.0])
    return np.asarray(points, dtype=np.float64)


def sphere(nu=24, nv=16, radius=100.0):
    """A genuinely CLOSED UV sphere: one vertex per pole, fans at the caps.

    The poles matter. A sphere built with `nu` coincident vertices at each
    pole has degenerate triangles and open edges, and would quietly make the
    "a closed surface has no boundary edges" check a lie.
    """
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
        faces.append([north, index[(1, column)], index[(1, (column + 1) % nu)]])
        faces.append([south, index[(nv - 1, (column + 1) % nu)],
                      index[(nv - 1, column)]])
    return (np.asarray(vertices, dtype=np.float64),
            np.asarray(faces, dtype=np.int64), index, nu)


def sphere_ring(vertices, index, nu, row, t=0.45):
    """A loop round the sphere that lies EXACTLY on mesh edges.

    Built the way a geodesic is: every point sits on an edge, and consecutive
    points share a triangle. It alternates a point on each meridian edge with
    a point on the diagonal between them, which is the only way to get from
    one meridian edge to the next without leaving the surface.
    """
    points = []
    for column in range(nu):
        a = index[(row, column)]
        b = index[(row, (column + 1) % nu)]
        d = index[(row + 1, (column + 1) % nu)]
        c = index[(row + 1, column)]
        # meridian edge a-c, then the diagonal a-d, then meridian b-d
        points.append(vertices[a] + (vertices[c] - vertices[a]) * t)
        points.append(vertices[a] + (vertices[d] - vertices[a]) * t)
        points.append(vertices[b] + (vertices[d] - vertices[b]) * t)
    return np.asarray(points, dtype=np.float64)


def torus(nu=24, nv=12, major=100.0, minor=30.0):
    """A closed torus - the standard surface where a loop need not separate."""
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


# ---------------------------------------------------------------------------
# A. the pieces tile the triangle exactly
# ---------------------------------------------------------------------------

def test_pieces_tile_exactly():
    print("\n[A] every crossed triangle is split into pieces that tile it")
    vertices, faces = grid()
    edges = interior.edge_map(faces)
    areas = interior.triangle_areas(vertices, faces)
    diagonal = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    runs, _located, _aligned = interior.traversals(
        vertices, faces, edges, square_loop(),
        diagonal * interior.ON_EDGE_FRACTION)
    check("A1: the loop crosses triangles", len(runs) > 10, len(runs))

    tolerance = diagonal * interior.ON_EDGE_FRACTION
    worst = 0.0
    failed = []
    all_pieces = {}
    for triangle, run_list in sorted(runs.items()):
        split = interior.split_triangle(vertices, faces, triangle, run_list,
                                        tolerance)
        if split is None:
            failed.append(triangle)
            continue
        all_pieces[triangle] = split
        error = abs(sum(part["area"] for part in split)
                    - float(areas[triangle]))
        worst = max(worst, error)
    check("A2: every crossed triangle split", not failed, failed)
    check("A3: and its pieces sum to the triangle's own area, exactly "
          "(worst error %.3e)" % worst, worst < 1e-12, worst)
    check("A4: no piece has negative or zero area",
          all(part["area"] > 0.0 for split in all_pieces.values()
              for part in split))
    check("A5: a triangle crossed once gives two pieces",
          all(len(split) >= 2 for split in all_pieces.values()),
          {t: len(s) for t, s in list(all_pieces.items())[:3]})


def test_pieces_carry_barycentric():
    print("\n[A] each piece is described in triangle-local terms too")
    vertices, faces = grid()
    result = interior.compute(vertices, faces, square_loop())
    side = result["sides"][interior.SIDE_SMALLER]
    check("A6: the smaller side has partial pieces",
          side["partial_count"] > 0, side["partial_count"])
    entry = side["partial"][0]
    check("A7: a piece names its triangle", entry["triangle"] >= 0)
    check("A8: it carries a polygon in mesh coordinates",
          entry["polygon"].ndim == 2 and entry["polygon"].shape[1] == 3,
          entry["polygon"].shape)
    check("A9: and the same polygon in barycentric coordinates",
          entry["bary"].shape == entry["polygon"].shape, entry["bary"].shape)
    check("A10: the barycentric coordinates sum to one",
          np.allclose(entry["bary"].sum(axis=1), 1.0),
          entry["bary"].sum(axis=1))
    check("A11: and lie inside the triangle",
          float(entry["bary"].min()) > -1e-9, float(entry["bary"].min()))
    rebuilt = (vertices[faces[entry["triangle"]]]
               * entry["bary"][:, :, None]).sum(axis=1)
    check("A12: barycentric and world descriptions agree",
          np.allclose(rebuilt, entry["polygon"], atol=1e-9),
          float(np.abs(rebuilt - entry["polygon"]).max()))


# ---------------------------------------------------------------------------
# B. a planar patch: the interior is exactly the square
# ---------------------------------------------------------------------------

def test_planar_patch():
    print("\n[B] a square loop on a flat patch encloses exactly the square")
    vertices, faces = grid()
    total = float(interior.triangle_areas(vertices, faces).sum())
    result = interior.compute(vertices, faces, square_loop())
    smaller = result["sides"][interior.SIDE_SMALLER]
    complement = result["sides"][interior.SIDE_COMPLEMENT]

    check("B1: the smaller side is the 3x3 square, to the last decimal",
          abs(smaller["area_total"] - 9.0) < 1e-12,
          smaller["area_total"])
    check("B2: full interior triangles are classified",
          smaller["full_count"] == 8, smaller["full_count"])
    check("B3: and the boundary-crossed ones are partial, not whole",
          smaller["partial_count"] == len(result["crossed_triangles"]),
          (smaller["partial_count"], len(result["crossed_triangles"])))
    check("B4: the complement is the rest of the patch",
          abs(complement["area_total"] - (total - 9.0)) < 1e-12,
          complement["area_total"])
    check("B5: THE TWO SIDES ARE THE WHOLE SURFACE - nothing created, "
          "nothing lost",
          abs(smaller["area_total"] + complement["area_total"] - total)
          < 1e-12,
          smaller["area_total"] + complement["area_total"] - total)
    check("B6: every node was reached by one side or the other",
          result["unreached_nodes"] == 0, result["unreached_nodes"])
    check("B7: no triangle is on both sides",
          not (set(smaller["full_triangles"])
               & set(complement["full_triangles"])))
    check("B8: each crossed triangle gives one piece to each side",
          smaller["partial_count"] == complement["partial_count"]
          == len(result["crossed_triangles"]))


def test_boundary_crosses_triangle_interiors():
    print("\n[B] the boundary really does cut through triangle interiors")
    vertices, faces = grid()
    edges = interior.edge_map(faces)
    diagonal = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    _runs, located, _aligned = interior.traversals(
        vertices, faces, edges, square_loop(),
        diagonal * interior.ON_EDGE_FRACTION)
    kinds = {}
    for entry in located:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    check("B9: boundary points sit on mesh edges",
          kinds.get(interior.LOC_EDGE, 0) > 0, kinds)
    check("B10: so partial triangles are a real case, not a corner case",
          len(_runs) > 10, len(_runs))


# ---------------------------------------------------------------------------
# C. a closed surface: two sides, and the choice between them
# ---------------------------------------------------------------------------

def test_closed_sphere_has_two_sides():
    print("\n[C] on a closed sphere one loop bounds TWO real regions")
    vertices, faces, index, nu = sphere()
    total = float(interior.triangle_areas(vertices, faces).sum())
    result = interior.compute(vertices, faces, sphere_ring(vertices, index,
                                                           nu, row=3))
    smaller = result["sides"][interior.SIDE_SMALLER]
    complement = result["sides"][interior.SIDE_COMPLEMENT]

    check("C1: both sides exist and are non-empty",
          smaller["full_count"] > 0 and complement["full_count"] > 0,
          (smaller["full_count"], complement["full_count"]))
    check("C2: the smaller side IS smaller",
          smaller["area_total"] < complement["area_total"],
          (smaller["area_total"], complement["area_total"]))
    check("C3: together they are the whole sphere",
          abs(smaller["area_total"] + complement["area_total"] - total)
          < 1e-9,
          smaller["area_total"] + complement["area_total"] - total)
    check("C4: the smaller side is the polar cap, not the rest of the ball",
          smaller["area_total"] < 0.25 * total, smaller["area_total"])
    check("C5: a closed sphere has no open edges",
          result["open_edge_count"] == 0, result["open_edge_count"])
    check("C6: nothing is unreached", result["unreached_nodes"] == 0)


def test_side_selection_is_a_lookup():
    print("\n[C] choosing the other side is a lookup, not a recomputation")
    vertices, faces, index, nu = sphere()
    result = interior.compute(vertices, faces,
                              sphere_ring(vertices, index, nu, row=3))
    check("C7: ONE analysis returns BOTH sides",
          set(result["sides"]) == {interior.SIDE_SMALLER,
                                   interior.SIDE_COMPLEMENT},
          sorted(result["sides"]))
    check("C8: so switching side needs no boundary and no solver",
          result["sides"][interior.SIDE_COMPLEMENT]["full_count"] > 0)
    check("C9: the two sides are named for what they are, not for anatomy",
          interior.SIDE_SMALLER == 'SMALLER'
          and interior.SIDE_COMPLEMENT == 'COMPLEMENT')
    labels = " ".join(interior.SIDE_LABELS.values()).lower()
    check("C10: and no label claims to be 'inside'", "inside" not in labels,
          labels)
    tip = " ".join(item[2] for item in interior.SIDE_ITEMS).lower()
    check("C11: the smaller side is documented as a DEFAULT, not a claim",
          "not because it is anatomically inside" in tip
          or "not because it is" in tip, tip[:120])


# ---------------------------------------------------------------------------
# D. ambiguity is refused
# ---------------------------------------------------------------------------

def test_non_separating_loop_is_refused():
    print("\n[D] a loop that does not cut the surface in two is refused")
    vertices, faces, index, nu = torus()
    # Round the major circumference: the classic non-separating loop.
    points = []
    for column in range(nu):
        a = index[(0, column)]
        b = index[(0, (column + 1) % nu)]
        d = index[(1, (column + 1) % nu)]
        c = index[(1, column)]
        points.append(vertices[a] + (vertices[c] - vertices[a]) * 0.45)
        points.append(vertices[a] + (vertices[d] - vertices[a]) * 0.45)
        points.append(vertices[b] + (vertices[d] - vertices[b]) * 0.45)
    refused = None
    try:
        interior.compute(vertices, faces,
                         np.asarray(points, dtype=np.float64))
    except interior.InteriorError as exc:
        refused = exc
    check("D1: REFUSED rather than guessed at", refused is not None)
    if refused is not None:
        check("D2: with a reason code that names the problem",
              refused.code in (interior.CODE_NOT_SEPARATED,
                               interior.CODE_OPEN_SURFACE), refused.code)
        check("D3: and says BSMT will not invent an interior",
              "refuses" in refused.message.lower(), refused.message[:100])


def test_boundary_off_the_mesh_is_refused():
    print("\n[D] a boundary that is not on this mesh is refused")
    vertices, faces = grid()
    floating = square_loop().copy()
    floating[:, 2] += 0.5                  # lifted clear of the surface
    refused = None
    try:
        interior.compute(vertices, faces, floating)
    except interior.InteriorError as exc:
        refused = exc
    check("D9: REFUSED", refused is not None)
    if refused is not None:
        check("D10: naming it as geometry that does not match",
              refused.code == interior.CODE_BOUNDARY_OFF_SURFACE,
              refused.code)


def test_self_crossing_inside_a_triangle_is_detected():
    """The gap the boundary's own check documents, closed one level down.

    Region validation's shared-point test cannot see a crossing that happens
    strictly BETWEEN two sampled boundary points, and says so. Inside a
    single triangle the boundary is straight between samples, so there the
    crossing is an ordinary segment intersection - and this is where it gets
    caught. Found on a real four-landmark region of a sphere: one triangle
    out of 128 where the loop crossed itself.
    """
    print("\n[D] a boundary that crosses itself inside a triangle")
    vertices, faces = grid()
    # Two chords through ONE triangle that cross, built on its actual edges.
    # Triangle 0 of the grid is (0,0)-(1,0)-(1,1).
    corners = vertices[faces[0]]

    def on_edge(first, second, t):
        return corners[first] + (corners[second] - corners[first]) * t

    # (0.25,0) -> (1,0.75) and (0.75,0) -> (0.75,0.75): these meet at
    # (0.75,0.5), strictly inside the triangle.
    crossing = [
        {"points": np.asarray([on_edge(0, 1, 0.25), on_edge(1, 2, 0.75)]),
         "enter": None, "exit": None},
        {"points": np.asarray([on_edge(0, 1, 0.75), on_edge(2, 0, 0.25)]),
         "enter": None, "exit": None},
    ]
    found = interior.crossing_chords(vertices, faces, 0, crossing)
    check("D4: two chords that cross inside a triangle are detected",
          found is not None, found)
    check("D5: and the pair is named", found == (0, 1) if found else False,
          found)

    parallel = [
        {"points": np.asarray([on_edge(0, 1, 0.2), on_edge(2, 0, 0.2)]),
         "enter": None, "exit": None},
        {"points": np.asarray([on_edge(0, 1, 0.6), on_edge(1, 2, 0.6)]),
         "enter": None, "exit": None},
    ]
    check("D6: two chords that do NOT cross are left alone",
          interior.crossing_chords(vertices, faces, 0, parallel) is None,
          interior.crossing_chords(vertices, faces, 0, parallel))
    check("D7: one chord can never cross itself",
          interior.crossing_chords(vertices, faces, 0, crossing[:1]) is None)

    # The detector must not fire on chords that merely meet at a corner.
    edges = interior.edge_map(faces)
    diagonal = float(np.linalg.norm(vertices.max(axis=0)
                                    - vertices.min(axis=0)))
    runs, _located, _aligned = interior.traversals(
        vertices, faces, edges, square_loop(),
        diagonal * interior.ON_EDGE_FRACTION)
    spurious = [triangle for triangle, run_list in runs.items()
                if interior.crossing_chords(vertices, faces, triangle,
                                            run_list) is not None]
    check("D8: and it does NOT fire anywhere on a simple loop", not spurious,
          spurious)


def test_limits_are_stated():
    print("\n[D] the limits are documented, not hidden")
    text = interior.LIMITS
    check("D11: it says the surface is walked, not projected",
          "never projects" in text, text[:80])
    check("D12: it says cut triangles are clipped EXACTLY, and names the "
          "precision that claim rests on",
          "EXACTLY" in text and "3.6e-07" in text and "float32" in text)
    check("D13: it says what IS refused, and does not overstate it",
          "What IS refused" in text and "cannot be separated" in text)
    check("D14: and that neither side is called inside",
          "not an anatomical claim" in text)
    # Collapsed, because the source is line-wrapped and a check that depends
    # on where a sentence happens to break tests the formatter, not the code.
    source = open(os.path.join(PACKAGE, "interior.py")).read()
    flat = " ".join(source.lower().replace("#", " ").split())
    check("D15: the module refuses the naive projected test by name",
          "point-in-polygon" in flat and "wrong on a body" in flat)
    check("D16: every reason code a refusal can carry has a label",
          all(code in interior.CODE_LABELS for code in (
              interior.CODE_OPEN_SURFACE, interior.CODE_NOT_SEPARATED,
              interior.CODE_UNSUPPORTED_CROSSING,
              interior.CODE_BOUNDARY_OFF_SURFACE,
              interior.CODE_SELF_INTERSECTION,
              interior.CODE_CROSS_COMPONENT)))


# ---------------------------------------------------------------------------
# E. components, and non-destructiveness
# ---------------------------------------------------------------------------

def test_region_stays_on_its_component():
    print("\n[E] a region is confined to the component it was drawn on")
    left_vertices, left_faces = grid()
    right_vertices, right_faces = grid()
    right_vertices = right_vertices + np.array([100.0, 0.0, 0.0])
    vertices = np.vstack([left_vertices, right_vertices])
    faces = np.vstack([left_faces, right_faces + len(left_vertices)])
    labels = np.concatenate([np.zeros(len(left_faces), dtype=np.int64),
                             np.ones(len(right_faces), dtype=np.int64)])

    result = interior.compute(vertices, faces, square_loop(),
                              component_labels=labels)
    smaller = result["sides"][interior.SIDE_SMALLER]
    complement = result["sides"][interior.SIDE_COMPLEMENT]
    check("E1: the interior is found on the component it was drawn on",
          abs(smaller["area_total"] - 9.0) < 1e-12, smaller["area_total"])
    check("E2: the component is reported", result["component_id"] == 0,
          result["component_id"])
    other = set(range(len(left_faces), len(faces)))
    check("E3: the OTHER component is in neither side - a region does not "
          "silently spread across a gap in the surface",
          not (set(smaller["full_triangles"]) & other)
          and not (set(complement["full_triangles"]) & other))
    check("E4: so the complement is that component only, not the whole file",
          abs(complement["area_total"] - (36.0 - 9.0)) < 1e-12,
          complement["area_total"])


def test_nothing_is_modified():
    print("\n[E] the analysis modifies nothing")
    vertices, faces = grid()
    before_vertices = vertices.copy()
    before_faces = faces.copy()
    loop = square_loop()
    before_loop = loop.copy()
    interior.compute(vertices, faces, loop)
    check("E5: the vertices are untouched",
          np.array_equal(vertices, before_vertices))
    check("E6: the triangles are untouched", np.array_equal(faces,
                                                            before_faces))
    check("E7: and so is the boundary it was given",
          np.array_equal(loop, before_loop))
    source = open(os.path.join(PACKAGE, "interior.py")).read()
    check("E8: the module imports no bpy", "import bpy" not in source)
    check("E9: and reaches no solver",
          "pygeodesic" not in source and "solve." not in source)


def test_no_area_is_claimed():
    print("\n[E] this milestone does not calculate surface area")
    source = open(os.path.join(PACKAGE, "interior.py")).read()
    lowered = source.lower()
    for banned in ("surface_area", "def area(", "coverage", "projected area"):
        check("E10: no %s in the module" % banned, banned not in lowered)
    # Comment markers are dropped as well as line breaks: the claim is in a
    # comment, and "#" appearing mid-sentence is an artefact of wrapping.
    flat = " ".join(lowered.replace("#", " ").split())
    check("E11: the area total it does report is named a TRIANGLE-AREA "
          "total, and says it is not the region's surface area",
          "triangle-area total" in flat
          and "not presented as the region's surface area" in flat)
    result = interior.compute(*grid(), square_loop())
    check("E12: the result carries its own limits",
          result["limits"] == interior.LIMITS)


# ---------------------------------------------------------------------------
# F. structural performance
# ---------------------------------------------------------------------------
#
# Wall-clock is not asserted. It varies with the machine, it makes a suite
# that fails for reasons nobody can act on, and it does not say WHY something
# is slow. What is asserted here are the properties that made the difference
# between four seconds and several hours on a 350,000-triangle scan: the
# expensive work is confined to the boundary, the indexes are built once, and
# the mesh is walked once rather than twice.

def _big_sphere(nu=120, nv=60, radius=100.0):
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


def test_expensive_work_is_confined_to_the_boundary():
    print("\n[F] clipping runs only where the boundary actually cuts")
    vertices, faces, index, nu = _big_sphere()
    loop = sphere_ring(vertices, index, nu, row=15)

    calls = {"split": 0, "edge_map": 0, "intervals": 0}
    original_split = interior.split_triangle
    original_edge_map = interior.edge_map
    original_intervals = interior._edge_intervals

    def counted_split(*args, **kwargs):
        calls["split"] += 1
        return original_split(*args, **kwargs)

    def counted_edge_map(*args, **kwargs):
        calls["edge_map"] += 1
        return original_edge_map(*args, **kwargs)

    def counted_intervals(*args, **kwargs):
        calls["intervals"] += 1
        return original_intervals(*args, **kwargs)

    interior.split_triangle = counted_split
    interior.edge_map = counted_edge_map
    interior._edge_intervals = counted_intervals
    try:
        result = interior.compute(vertices, faces, loop)
    finally:
        interior.split_triangle = original_split
        interior.edge_map = original_edge_map
        interior._edge_intervals = original_intervals

    cut = len(result["crossed_triangles"])
    check("F1: the fixture is big enough to be worth measuring",
          len(faces) > 10000, len(faces))
    check("F2: CLIPPING ran once per cut triangle, not once per triangle "
          "(%d calls, %d cut, %d in the mesh)" % (calls["split"], cut,
                                                  len(faces)),
          calls["split"] == cut, (calls["split"], cut))
    check("F3: which is a tiny fraction of the mesh",
          calls["split"] < len(faces) / 20, (calls["split"], len(faces)))
    check("F4: the edge map was built ONCE", calls["edge_map"] == 1,
          calls["edge_map"])
    # One call per adjacent PIECE of each touching edge, which is a small
    # constant - the claim is that the work scales with the CUT, not with
    # the mesh. Before this was fixed it ran on every edge in the mesh.
    total_edges = len(interior.edge_map(faces))
    check("F5: edge-interval work scales with the cut (%d calls for %d "
          "touching edges), not with the mesh's %d edges"
          % (calls["intervals"], result["cut_edge_count"], total_edges),
          calls["intervals"] <= 6 * result["cut_edge_count"]
          and calls["intervals"] < total_edges / 4,
          (calls["intervals"], result["cut_edge_count"], total_edges))
    check("F6: and the result reports how many edges that was",
          0 < result["cut_edge_count"] < len(faces) / 10,
          result["cut_edge_count"])


def test_both_sides_come_from_one_traversal():
    print("\n[F] the mesh is walked once, not once per side")
    vertices, faces, index, nu = _big_sphere(nu=60, nv=30)
    loop = sphere_ring(vertices, index, nu, row=8)

    floods = {"count": 0}
    original = interior.SurfaceGraph.flood

    def counted(self, start):
        floods["count"] += 1
        return original(self, start)

    interior.SurfaceGraph.flood = counted
    try:
        result = interior.compute(vertices, faces, loop)
    finally:
        interior.SurfaceGraph.flood = original

    check("F7: ONE flood, not one per side", floods["count"] == 1,
          floods["count"])
    smaller = result["sides"][interior.SIDE_SMALLER]
    complement = result["sides"][interior.SIDE_COMPLEMENT]
    total = float(interior.triangle_areas(vertices, faces).sum())
    check("F8: and the complement derived from it is still exact",
          abs(smaller["area_total"] + complement["area_total"] - total)
          / total < 1e-9,
          abs(smaller["area_total"] + complement["area_total"] - total))
    check("F9: with every node accounted for",
          result["unreached_nodes"] == 0, result["unreached_nodes"])


def test_point_location_is_not_a_global_scan():
    print("\n[F] a boundary point is not compared against every mesh edge")
    vertices, faces, index, nu = _big_sphere(nu=60, nv=30)
    edges = interior.edge_map(faces)
    grid = interior.EdgeGrid(vertices, edges)
    loop = sphere_ring(vertices, index, nu, row=8)

    shortlists = [len(grid.candidates(point, radius=1)) for point in loop]
    check("F10: the grid shortlists a handful of edges per point, out of "
          "%d (worst %d, mean %.1f)"
          % (len(edges), max(shortlists), float(np.mean(shortlists))),
          max(shortlists) < len(edges) / 50,
          (max(shortlists), len(edges)))
    check("F11: and it always finds something to test",
          min(shortlists) > 0, min(shortlists))
    # Soundness: the shortlist must contain the edge a full scan would pick.
    tolerance = float(np.linalg.norm(vertices.max(axis=0)
                                     - vertices.min(axis=0))) \
        * interior.ON_EDGE_FRACTION
    agree = 0
    for point in loop[:40]:
        full = interior.locate_point(vertices, faces, edges, point, tolerance)
        quick = interior.locate_point(vertices, faces, edges, point,
                                      tolerance,
                                      candidates=grid.candidates(point))
        if full is None and quick is None:
            agree += 1
        elif full is not None and quick is not None:
            agree += int(full["kind"] == quick["kind"]
                         and full.get("edge") == quick.get("edge"))
    check("F12: THE SHORTLIST GIVES THE SAME ANSWER as scanning every edge",
          agree == 40, agree)


# ---------------------------------------------------------------------------
# G. the real-scan tiling failure
# ---------------------------------------------------------------------------
#
# From an actual 350,000-triangle human scan, where Compute Interior refused:
#
#   the pieces of triangle 14527 do not tile it (3.82042 vs 3.81668)
#
# An EXCESS of +9.8e-04 relative - the pieces OVERLAPPED. The cause was a
# chord end stored a few microns off the triangle border: the cached boundary
# is float32 display geometry, so a point that mathematically lies exactly on
# a shared mesh edge lands slightly to one side of it. The splitter LOCATED
# that end by projecting it onto the border but then inserted the RAW point
# into both pieces, so the two pieces shared a corner that was not on the
# border and their union bulged past the triangle.
#
# These fixtures reproduce that geometry exactly, at the scale it was seen.

def _failing_triangle():
    """A triangle the size of the real one (3.9 mm^2), and a scan-scale
    tolerance (a 1800 mm bounding box, which is a standing human)."""
    vertices = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.6, 2.6, 0.0]])
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    return vertices, faces, 1800.0 * interior.ON_EDGE_FRACTION


def test_drifted_chord_end_still_tiles():
    print("\n[G] a chord end a few microns off the border - the real case")
    vertices, faces, tolerance = _failing_triangle()
    area = float(interior.triangle_areas(vertices, faces)[0])

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    worst = 0.0
    for drift in (0.0, 1e-5, 1e-4, 1e-3, 3e-3, 1e-2):
        start = bary([0.5, 0.5, 0.0]) + np.array([0.0, -drift, 0.0])
        runs = [{"points": np.asarray([start, bary([0.0, 0.5, 0.5])])}]
        split = interior.split_triangle(vertices, faces, 0, runs, tolerance)
        check("G1: a %.0e mm drift still splits" % drift, split is not None)
        if split is None:
            continue
        total = sum(part["area"] for part in split)
        relative = abs(total - area) / area
        worst = max(worst, relative)
        check("G2: and its pieces TILE the triangle (%.0e mm -> %.2e "
              "relative)" % (drift, relative), relative < 1e-12, relative)
    check("G3: worst residual across every drift is float-exact (%.2e)"
          % worst, worst < 1e-12, worst)


def test_the_exact_real_scan_magnitude():
    print("\n[G] the magnitude actually reported on the scan")
    vertices, faces, tolerance = _failing_triangle()
    area = float(interior.triangle_areas(vertices, faces)[0])

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    # 3e-3 mm of drift is what reproduces +9.8e-04 on a 3.8 mm^2 triangle,
    # which is what the scan reported. Without the projection this fixture
    # produces that excess; with it the residual is zero.
    start = bary([0.5, 0.5, 0.0]) + np.array([0.0, -3e-3, 0.0])
    runs = [{"points": np.asarray([start, bary([0.0, 0.5, 0.5])])}]
    split = interior.split_triangle(vertices, faces, 0, runs, tolerance)
    check("G4: the real-scan configuration splits", split is not None)
    if split is not None:
        total = sum(part["area"] for part in split)
        check("G5: with NO excess - the pieces no longer overlap "
              "(%.3e relative)" % ((total - area) / area),
              abs(total - area) / area < 1e-12, (total, area))
        check("G6: and neither piece has zero or negative area",
              all(part["area"] > 0.0 for part in split),
              [part["area"] for part in split])
        # The two pieces must meet ALONG the chord, sharing its ends exactly.
        ends = []
        for part in split:
            polygon = part["polygon"]
            ends.append({tuple(np.round(point, 12)) for point in polygon})
        shared = ends[0] & ends[1]
        check("G7: the two pieces share exactly the chord's two ends",
              len(shared) == 2, len(shared))


def test_a_drift_too_large_is_still_refused():
    print("\n[G] a chord end too far off the border is REFUSED, not nudged")
    vertices, faces, tolerance = _failing_triangle()

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    start = bary([0.5, 0.5, 0.0]) + np.array([0.0, -0.5, 0.0])
    runs = [{"points": np.asarray([start, bary([0.0, 0.5, 0.5])])}]
    split = interior.split_triangle(vertices, faces, 0, runs, tolerance)
    check("G8: half a millimetre off a 3 mm triangle is refused - that is "
          "not float32 storage, it is a real fault", split is None, split)
    check("G9: the tolerance it is judged against is the mesh's own scale, "
          "not a fixed number", tolerance > 0.0 and tolerance < 1.0,
          tolerance)


def test_tiling_report_is_inspectable():
    print("\n[G] a tiling failure says where to look")
    vertices, faces, tolerance = _failing_triangle()

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    runs = [{"points": np.asarray([bary([0.5, 0.5, 0.0]),
                                   bary([0.0, 0.5, 0.5])])}]
    split = interior.split_triangle(vertices, faces, 0, runs, tolerance)
    text = interior.tiling_report(vertices, faces, 0, runs, split, tolerance)
    for wanted, why in (
            ("triangle 0", "names the triangle"),
            ("parent corners", "gives the parent triangle in mm"),
            ("parent area", "and its area"),
            ("visits this triangle 1 time", "says how many times the "
                                            "boundary visited it"),
            ("bary", "gives barycentric coordinates of each end"),
            ("off-border", "and how far each end sits off the border"),
            ("piece 1", "lists each clipped piece"),
            ("piece sum", "and what they summed to"),
            ("OVERLAP", "and says what an excess means")):
        check("G10: the report %s" % why, wanted in text, text[:200])
    check("G11: and it does not dump the mesh",
          len(text.splitlines()) < 30, len(text.splitlines()))


def test_success_reports_the_tiling_residual():
    print("\n[G] a SUCCESSFUL run says how close the invariant came")
    vertices, faces, index, nu = _big_sphere(nu=60, nv=30)
    result = interior.compute(vertices, faces,
                              sphere_ring(vertices, index, nu, row=8))
    tiling = result["tiling"]
    check("G12: it reports how many boundary triangles were checked",
          tiling["checked"] == len(result["crossed_triangles"]),
          (tiling["checked"], len(result["crossed_triangles"])))
    check("G13: the worst relative residual", tiling["worst_relative"] < 1e-12,
          tiling["worst_relative"])
    check("G14: the worst absolute residual",
          tiling["worst_absolute"] < 1e-9, tiling["worst_absolute"])
    check("G15: and which triangle it was",
          tiling["worst_triangle"] in result["crossed_triangles"]
          or tiling["worst_triangle"] == -1, tiling["worst_triangle"])


# ---------------------------------------------------------------------------
# H. boundary segments that run ALONG a mesh edge
# ---------------------------------------------------------------------------
#
# From a real ~350,000-triangle scan: Compute Interior refused with
#
#   the boundary's crossing of triangle 6825 could not be split exactly
#
# A geodesic that runs along a mesh edge does NOT cut the triangle in two.
# The triangle stays whole and it is the EDGE that becomes part of the
# barrier. Asking the clipper to split such a triangle asks for a piece with
# no interior, which is why it used to be refused outright.
#
# The rule is topological, not a distance threshold: a run is edge-aligned
# when every one of its points is classified on one common mesh edge, or at
# one of that edge's two ends. A chord that enters and leaves through the
# SAME edge but bulges into the triangle between has interior points, is not
# edge-aligned, and must still be split - which §H5 asserts.

def _edge_grid(n=6, size=6.0):
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


def _along_grid_lines(low=2, high=4):
    """A closed loop that follows mesh edges exactly, vertex to vertex."""
    points = []
    for x in range(low, high):
        points.append([float(x), float(low), 0.0])
    for y in range(low, high):
        points.append([float(high), float(y), 0.0])
    for x in range(high, low, -1):
        points.append([float(x), float(high), 0.0])
    for y in range(high, low, -1):
        points.append([float(low), float(y), 0.0])
    return np.asarray(points, dtype=np.float64)


def test_boundary_along_mesh_edges_is_supported():
    print("\n[H] a boundary that runs entirely along mesh edges")
    vertices, faces = _edge_grid()
    total = float(interior.triangle_areas(vertices, faces).sum())
    result = interior.compute(vertices, faces, _along_grid_lines())

    smaller = result["sides"][interior.SIDE_SMALLER]
    complement = result["sides"][interior.SIDE_COMPLEMENT]
    check("H1: it computes at all - this used to be refused outright",
          smaller["full_count"] > 0)
    check("H2: NO triangle was cut, because none was",
          result["partial_triangles"] == 0
          and smaller["partial_count"] == 0
          and complement["partial_count"] == 0,
          (result["partial_triangles"], smaller["partial_count"]))
    check("H3: the mesh edges the boundary ran along are recorded",
          result["aligned_edges"] == 8, result["aligned_edges"])
    check("H4: and each one severed an adjacency",
          result["severed_edges"] == result["aligned_edges"],
          (result["severed_edges"], result["aligned_edges"]))
    check("H5: the enclosed side is EXACTLY the 2x2 square it encloses",
          abs(smaller["area_total"] - 4.0) < 1e-12, smaller["area_total"])
    check("H6: the two sides are the whole mesh - every triangle whole, "
          "none counted twice",
          abs(smaller["area_total"] + complement["area_total"] - total)
          < 1e-12, smaller["area_total"] + complement["area_total"])
    check("H7: and the tiling invariant holds trivially, because an uncut "
          "triangle contributes its whole area to one side and nothing to "
          "the other", result["tiling"]["worst_relative"] == 0.0,
          result["tiling"]["worst_relative"])


def test_shared_edge_blocks_adjacency():
    print("\n[H] the severed edge really does block the traversal")
    vertices, faces = _edge_grid()
    edges = interior.edge_map(faces)
    aligned = {key for key in edges
               if len(edges[key]) == 2
               and abs(vertices[key[0]][1] - 2.0) < 1e-12
               and abs(vertices[key[1]][1] - 2.0) < 1e-12}
    check("H8: the fixture has shared edges to sever", len(aligned) > 0,
          len(aligned))

    open_graph = interior.build_graph(vertices, faces, edges, {}, 1e-6)
    shut_graph = interior.build_graph(vertices, faces, edges, {}, 1e-6,
                                      blocked=aligned)
    key = sorted(aligned)[0]
    left, right = (int(value) for value in edges[key])
    check("H9: without the barrier the two triangles are connected",
          open_graph.flood(open_graph.node(left, -1))[
              open_graph.node(right, -1)])
    reachable = shut_graph.flood(shut_graph.node(left, -1))
    check("H10: the severed edge itself no longer links them directly",
          shut_graph.severed == len(aligned), shut_graph.severed)
    check("H11: and the graph records how many it severed",
          shut_graph.severed > 0, shut_graph.severed)


def test_edge_aligned_detection_is_topological():
    print("\n[H] edge-alignment is decided by classification, not distance")
    vertices, faces = _edge_grid()
    edges = interior.edge_map(faces)
    tolerance = float(np.linalg.norm(vertices.max(axis=0)
                                     - vertices.min(axis=0))) \
        * interior.ON_EDGE_FRACTION

    runs, _located, aligned = interior.traversals(
        vertices, faces, edges, _along_grid_lines(), tolerance)
    check("H12: an along-edge loop yields NO chords to clip",
          not runs, sorted(runs)[:4])
    check("H13: and every segment of it is recorded as an aligned edge",
          len(aligned) == 8, len(aligned))

    # A chord that enters and leaves through the SAME edge but bulges into
    # the triangle is NOT edge-aligned: it has an interior point, it really
    # does cut, and it must still be split.
    bulging = {"points": np.zeros((3, 3)),
               "locations": [{"kind": interior.LOC_EDGE, "edge": (0, 1)},
                             {"kind": interior.LOC_FACE, "triangle": 0},
                             {"kind": interior.LOC_EDGE, "edge": (0, 1)}]}
    check("H14: a chord with an INTERIOR point is not edge-aligned, even "
          "with both ends on one edge",
          interior.aligned_edge(bulging) is None,
          interior.aligned_edge(bulging))
    flat = {"points": np.zeros((3, 3)),
            "locations": [{"kind": interior.LOC_EDGE, "edge": (0, 1)},
                          {"kind": interior.LOC_EDGE, "edge": (0, 1)},
                          {"kind": interior.LOC_EDGE, "edge": (0, 1)}]}
    check("H15: a run whose every point is on one edge IS edge-aligned",
          interior.aligned_edge(flat) == (0, 1),
          interior.aligned_edge(flat))
    crossing = {"points": np.zeros((2, 3)),
                "locations": [{"kind": interior.LOC_EDGE, "edge": (0, 1)},
                              {"kind": interior.LOC_EDGE, "edge": (1, 2)}]}
    check("H16: a run between two DIFFERENT edges is an ordinary chord",
          interior.aligned_edge(crossing) is None)


def test_vertex_events_do_not_double_count():
    print("\n[H] a vertex on the boundary is resolved, not counted twice")
    vertices, faces = _edge_grid()
    edges = interior.edge_map(faces)
    tolerance = float(np.linalg.norm(vertices.max(axis=0)
                                     - vertices.min(axis=0))) \
        * interior.ON_EDGE_FRACTION
    # Every point of this loop sits exactly ON a mesh vertex.
    _runs, located, aligned = interior.traversals(
        vertices, faces, edges, _along_grid_lines(), tolerance)
    kinds = {entry["kind"] for entry in located}
    check("H17: every point of the loop resolved to a VERTEX",
          kinds == {interior.LOC_VERTEX}, kinds)
    check("H18: a vertex knows which edges it sits on, so the run can pick "
          "the one it is running along",
          all("vertex_edges" in entry for entry in located))
    check("H19: consecutive vertices yield ONE aligned edge each, not two",
          len(aligned) == 8, len(aligned))
    for edge in aligned:
        check("H20: and each is a real mesh edge", edge in edges, edge)
        break


def test_near_edge_is_not_treated_as_aligned():
    print("\n[H] near an edge is NOT on it")
    vertices, faces = _edge_grid()
    edges = interior.edge_map(faces)
    tolerance = float(np.linalg.norm(vertices.max(axis=0)
                                     - vertices.min(axis=0))) \
        * interior.ON_EDGE_FRACTION
    # The square loop of §B runs through triangle INTERIORS, close to no
    # edge in particular. It must still be clipped, not mistaken for aligned.
    runs, _located, aligned = interior.traversals(
        vertices, faces, edges, square_loop(), tolerance)
    check("H21: an interior-crossing loop produces chords", len(runs) > 10,
          len(runs))
    check("H22: and NO aligned edges", not aligned, aligned)
    result = interior.compute(vertices, faces, square_loop())
    check("H23: so it is still clipped exactly as before",
          abs(result["sides"][interior.SIDE_SMALLER]["area_total"] - 9.0)
          < 1e-12,
          result["sides"][interior.SIDE_SMALLER]["area_total"])
    check("H24: with partial triangles, not aligned edges",
          result["partial_triangles"] > 0 and result["aligned_edges"] == 0,
          (result["partial_triangles"], result["aligned_edges"]))


def test_split_failure_now_says_why():
    print("\n[H] a refused split reports what it refused")
    vertices, faces, tolerance = _failing_triangle()

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    runs = [{"points": np.asarray([bary([0.8, 0.2, 0.0]),
                                   bary([0.2, 0.8, 0.0])]),
             "locations": [{"kind": interior.LOC_EDGE, "edge": (0, 1)},
                           {"kind": interior.LOC_EDGE, "edge": (0, 1)}]}]
    text = interior.tiling_report(vertices, faces, 0, runs, None, tolerance)
    check("H25: it says no pieces were produced",
          "NO pieces" in text, text[:160])
    check("H26: and names the along-edge case explicitly",
          "lies ALONG mesh edge" in text, text)
    check("H27: this is the record that was missing when a real scan "
          "refused triangle 6825", "triangle 0" in text)


def test_large_drift_is_still_refused():
    """Edge-aligned support must not become a way to absorb bad geometry.

    A chord whose end is genuinely far off the triangle border is not an
    along-edge event, it is a boundary that does not belong to this triangle,
    and the clipper must go on refusing it. The point of the new path is to
    recognise a case that was ALWAYS exact, not to widen what counts as
    close enough.
    """
    print("\n[H] drift beyond tolerance is refused, not absorbed")
    vertices, faces, tolerance = _failing_triangle()

    def bary(weights):
        return np.asarray(weights, dtype=np.float64) @ vertices

    scale = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    for drift in (0.05 * scale, 0.2 * scale):
        start = bary([0.5, 0.5, 0.0]) + np.array([0.0, -drift, 0.0])
        runs = [{"points": np.asarray([start, bary([0.0, 0.5, 0.5])])}]
        check("H32: a %.3f mm drift (%.0f%% of the triangle) is REFUSED"
              % (drift, 100.0 * drift / scale),
              interior.split_triangle(vertices, faces, 0, runs, tolerance)
              is None)

    check("H33: and a run carrying no classification at all is never "
          "called edge-aligned",
          interior.aligned_edge({"points": np.zeros((2, 3))}) is None)
    check("H34: nor is an empty one",
          interior.aligned_edge({"points": np.zeros((0, 3)),
                                 "locations": []}) is None)


def test_the_old_implementation_refused_this():
    """The regression this milestone exists for, stated as a test.

    Alignment detection is what changed. Switch it off and you have the
    previous implementation exactly, which must refuse the along-edge loop
    with the message a real ~350,000-triangle scan produced for triangle
    6825. Leave it on and the same loop resolves.
    """
    print("\n[H] the along-edge boundary the previous build refused")
    vertices, faces = _edge_grid()
    loop = _along_grid_lines()

    detect = interior.aligned_edge
    interior.aligned_edge = lambda run: None
    try:
        interior.compute(vertices, faces, loop)
        refusal = None
    except Exception as exc:                      # noqa: BLE001 - it is ours
        refusal = str(exc)
    finally:
        interior.aligned_edge = detect

    check("H28: WITHOUT edge-aligned support it is refused",
          refusal is not None)
    check("H29: with exactly the message the real scan reported",
          refusal is not None
          and "could not be split exactly" in refusal, refusal)

    result = interior.compute(vertices, faces, loop)
    total = float(interior.triangle_areas(vertices, faces).sum())
    smaller = result["sides"][interior.SIDE_SMALLER]["area_total"]
    complement = result["sides"][interior.SIDE_COMPLEMENT]["area_total"]
    check("H30: WITH it, the same boundary resolves", result["cut_edge_count"]
          >= 0 and smaller > 0.0)
    check("H31: to the exact enclosed area, and the two sides still tile "
          "the mesh", abs(smaller - 4.0) < 1e-12
          and abs(smaller + complement - total) < 1e-12,
          (smaller, complement, total))


def main():
    print("BSMT Surface Interior rules - offline\n" + "=" * 62)
    for test in (
        test_pieces_tile_exactly,
        test_pieces_carry_barycentric,
        test_planar_patch,
        test_boundary_crosses_triangle_interiors,
        test_closed_sphere_has_two_sides,
        test_side_selection_is_a_lookup,
        test_non_separating_loop_is_refused,
        test_self_crossing_inside_a_triangle_is_detected,
        test_boundary_off_the_mesh_is_refused,
        test_limits_are_stated,
        test_region_stays_on_its_component,
        test_nothing_is_modified,
        test_no_area_is_claimed,
        test_expensive_work_is_confined_to_the_boundary,
        test_both_sides_come_from_one_traversal,
        test_point_location_is_not_a_global_scan,
        test_drifted_chord_end_still_tiles,
        test_the_exact_real_scan_magnitude,
        test_a_drift_too_large_is_still_refused,
        test_tiling_report_is_inspectable,
        test_success_reports_the_tiling_residual,
        test_boundary_along_mesh_edges_is_supported,
        test_shared_edge_blocks_adjacency,
        test_edge_aligned_detection_is_topological,
        test_vertex_events_do_not_double_count,
        test_near_edge_is_not_treated_as_aligned,
        test_split_failure_now_says_why,
        test_large_drift_is_still_refused,
        test_the_old_implementation_refused_this,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
