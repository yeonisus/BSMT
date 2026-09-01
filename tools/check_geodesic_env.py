"""BSMT Milestone 2.2 - pygeodesic environment proof of concept.

Standalone diagnostic. NOT part of the add-on; it imports nothing from
body_surface_measurement and changes nothing.

Run it with the same interpreter that Blender uses, which is the only thing
that matters for a compiled wheel:

    /Applications/Blender.app/Contents/MacOS/Blender --background \\
        --python /Users/yeoni/BSMT/tools/check_geodesic_env.py

It can also be run with a plain python3 to check a candidate environment:

    python3 tools/check_geodesic_env.py

What it reports:

  1. interpreter version, architecture and executable, plus Blender's own
     version when running inside Blender;
  2. numpy version, and whether pygeodesic imports against it;
  3. the ACTUAL public API surface of pygeodesic - nothing about the call
     signatures is assumed;
  4. accuracy against the analytic great-circle distance on icospheres of
     increasing resolution, which shows the polyhedral discretisation error
     and its trend;
  5. wall-clock timing on a scan-sized mesh;
  6. the exact pip command for this interpreter if pygeodesic is missing.

No add-on file is modified and nothing is installed by this script.
"""

import math
import platform
import sys
import time

SEPARATOR = "-" * 72


def heading(text):
    print("")
    print(text)
    print(SEPARATOR)


def report_interpreter():
    heading("1. Interpreter")
    print("  python version   : %s" % sys.version.split()[0])
    print("  python full      : %s" % sys.version.replace("\n", " "))
    print("  executable       : %s" % sys.executable)
    print("  platform         : %s %s" % (platform.system(), platform.release()))
    print("  machine          : %s" % platform.machine())
    print("  implementation   : %s" % platform.python_implementation())

    major, minor = sys.version_info[:2]
    tag = "cp%d%d" % (major, minor)
    print("  wheel tag needed : %s" % tag)
    if (major, minor) != (3, 11):
        print("  NOTE: PROJECT_SPEC assumes Blender 4.5 bundles Python 3.11.")
        print("        This interpreter is %d.%d, so the wheel matrix in the" % (major, minor))
        print("        spec must be revisited before relying on it.")

    try:
        import bpy  # noqa: F401
    except Exception:
        print("  blender          : not running inside Blender")
        return None

    print("  blender version  : %s" % bpy.app.version_string)
    print("  blender binary   : %s" % bpy.app.binary_path)
    return bpy.app.version


def report_numpy():
    heading("2. numpy")
    try:
        import numpy
    except Exception as exc:
        print("  FAIL: numpy is unavailable: %s: %s" % (type(exc).__name__, exc))
        return None
    print("  version          : %s" % numpy.__version__)
    print("  location         : %s" % numpy.__file__)
    return numpy


def report_pygeodesic():
    heading("3. pygeodesic")
    try:
        import pygeodesic
    except Exception as exc:
        print("  NOT INSTALLED: %s: %s" % (type(exc).__name__, exc))
        print("")
        print("  Install it into THIS interpreter (not the system python):")
        print("")
        print("    \"%s\" -m pip install --user pygeodesic" % sys.executable)
        print("")
        print("  If pip is missing:")
        print("    \"%s\" -m ensurepip" % sys.executable)
        print("")
        print("  A wheel must exist for tag cp%d%d on %s."
              % (sys.version_info[0], sys.version_info[1], platform.machine()))
        return None

    print("  version          : %s" % getattr(pygeodesic, "__version__", "unknown"))
    print("  location         : %s" % pygeodesic.__file__)

    try:
        import pygeodesic.geodesic as geodesic
    except Exception as exc:
        print("  FAIL: pygeodesic.geodesic did not import: %s: %s"
              % (type(exc).__name__, exc))
        return None

    # Report the real API rather than assuming any of it.
    public = [name for name in dir(geodesic) if not name.startswith("_")]
    print("  module members   : %s" % ", ".join(sorted(public)))

    algorithm = getattr(geodesic, "PyGeodesicAlgorithmExact", None)
    if algorithm is None:
        print("  FAIL: PyGeodesicAlgorithmExact not present")
        return None
    methods = [name for name in dir(algorithm) if not name.startswith("_")]
    print("  exact algorithm  : PyGeodesicAlgorithmExact")
    print("  its methods      : %s" % ", ".join(sorted(methods)))
    return geodesic


# ---------------------------------------------------------------------------
# analytic test geometry
# ---------------------------------------------------------------------------

def icosphere(numpy, radius, subdivisions):
    """Icosphere with vertices exactly on the sphere. Returns (V, F)."""
    phi = (1.0 + math.sqrt(5.0)) / 2.0
    vertices = numpy.array([
        [-1, phi, 0], [1, phi, 0], [-1, -phi, 0], [1, -phi, 0],
        [0, -1, phi], [0, 1, phi], [0, -1, -phi], [0, 1, -phi],
        [phi, 0, -1], [phi, 0, 1], [-phi, 0, -1], [-phi, 0, 1],
    ], dtype=numpy.float64)
    faces = numpy.array([
        [0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
        [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
        [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
        [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1],
    ], dtype=numpy.int64)

    for _ in range(subdivisions):
        midpoint = {}
        new_faces = []
        vertex_list = list(vertices)

        def middle(a, b):
            key = (a, b) if a < b else (b, a)
            if key not in midpoint:
                vertex_list.append((vertex_list[a] + vertex_list[b]) * 0.5)
                midpoint[key] = len(vertex_list) - 1
            return midpoint[key]

        for i0, i1, i2 in faces:
            a = middle(int(i0), int(i1))
            b = middle(int(i1), int(i2))
            c = middle(int(i2), int(i0))
            new_faces += [[i0, a, c], [i1, b, a], [i2, c, b], [a, b, c]]
        vertices = numpy.array(vertex_list, dtype=numpy.float64)
        faces = numpy.array(new_faces, dtype=numpy.int64)

    norms = numpy.linalg.norm(vertices, axis=1, keepdims=True)
    return vertices / norms * radius, faces


def great_circle(numpy, radius, a, b):
    cosine = float(numpy.dot(a, b)) / (radius * radius)
    return radius * math.acos(max(-1.0, min(1.0, cosine)))


def mean_edge_length(numpy, vertices, faces):
    pairs = numpy.concatenate(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
    )
    segments = vertices[pairs[:, 0]] - vertices[pairs[:, 1]]
    return float(numpy.linalg.norm(segments, axis=1).mean())


def test_accuracy(numpy, geodesic):
    heading("5. Accuracy against the analytic great-circle distance")
    radius = 100.0            # mm
    print("  sphere radius %.1f mm; exact polyhedral geodesics UNDERESTIMATE the" % radius)
    print("  smooth surface distance because chords cut corners. The error is")
    print("  discretisation, not algorithmic - it must shrink with edge length.")
    print("")
    print("  %-5s %8s %9s %10s %12s %12s %10s"
          % ("subdiv", "verts", "tris", "h (mm)", "geodesic", "analytic", "rel err"))

    previous = None
    for subdivisions in (2, 3, 4):
        vertices, faces = icosphere(numpy, radius, subdivisions)
        h = mean_edge_length(numpy, vertices, faces)

        algorithm = geodesic.PyGeodesicAlgorithmExact(
            numpy.ascontiguousarray(vertices, dtype=numpy.float64),
            numpy.ascontiguousarray(faces, dtype=numpy.int32),
        )
        # Antipodal-ish pair, far apart so the path crosses many triangles.
        source = int(numpy.argmax(vertices[:, 2]))
        target = int(numpy.argmin(vertices[:, 2]))
        result = algorithm.geodesicDistance(source, target)
        distance = float(result[0] if isinstance(result, tuple) else result)
        path = result[1] if isinstance(result, tuple) and len(result) > 1 else None

        analytic = great_circle(numpy, radius, vertices[source], vertices[target])
        relative = abs(distance - analytic) / analytic
        print("  %-5d %8d %9d %10.4f %12.5f %12.5f %9.3e"
              % (subdivisions, vertices.shape[0], faces.shape[0], h,
                 distance, analytic, relative))

        if previous is not None:
            ratio = previous[1] / relative if relative else float("inf")
            order = math.log(ratio) / math.log(previous[0] / h) if h and ratio > 0 else 0.0
            print("        -> error shrank %.1fx as h halved (observed order ~%.2f)"
                  % (ratio, order))
        previous = (h, relative)

        if subdivisions == 2:
            print("        -> path returned: %s"
                  % ("yes, %d points" % len(path) if path is not None else "no"))

    print("")
    print("  Interpretation: a shrinking error confirms discretisation-limited")
    print("  behaviour. An error that plateaus would indicate an algorithmic")
    print("  bias, which is exactly what edge-graph Dijkstra shows and what an")
    print("  exact polyhedral method must not.")


def test_flat_plane(numpy, geodesic):
    heading("4. Flat plane: geodesic must equal the Euclidean distance exactly")
    size = 20
    spacing = 5.0
    xs, ys = numpy.meshgrid(
        numpy.arange(size + 1) * spacing, numpy.arange(size + 1) * spacing,
        indexing="ij",
    )
    vertices = numpy.stack(
        [xs.ravel(), ys.ravel(), numpy.zeros(xs.size)], axis=1
    ).astype(numpy.float64)
    index = lambda i, j: i * (size + 1) + j
    i, j = numpy.meshgrid(numpy.arange(size), numpy.arange(size), indexing="ij")
    i = i.ravel()
    j = j.ravel()
    faces = numpy.concatenate([
        numpy.stack([index(i, j), index(i + 1, j), index(i + 1, j + 1)], axis=1),
        numpy.stack([index(i, j), index(i + 1, j + 1), index(i, j + 1)], axis=1),
    ]).astype(numpy.int32)

    algorithm = geodesic.PyGeodesicAlgorithmExact(vertices, faces)
    source = index(0, 0)
    target = index(size, size)
    result = algorithm.geodesicDistance(source, target)
    distance = float(result[0] if isinstance(result, tuple) else result)
    analytic = float(numpy.linalg.norm(vertices[target] - vertices[source]))
    relative = abs(distance - analytic) / analytic

    print("  geodesic  : %.12f" % distance)
    print("  Euclidean : %.12f" % analytic)
    print("  rel error : %.3e" % relative)
    print("  verdict   : %s"
          % ("PASS - no algorithmic error on a planar mesh" if relative < 1e-9
             else "FAIL - an exact method must be exact here"))
    return relative < 1e-9


def test_performance(numpy, geodesic):
    heading("6. Timing on a scan-sized mesh")
    radius = 100.0
    vertices, faces = icosphere(numpy, radius, 6)     # ~82k verts, ~164k tris
    print("  mesh             : %d vertices, %d triangles"
          % (vertices.shape[0], faces.shape[0]))

    started = time.perf_counter()
    algorithm = geodesic.PyGeodesicAlgorithmExact(
        numpy.ascontiguousarray(vertices, dtype=numpy.float64),
        numpy.ascontiguousarray(faces, dtype=numpy.int32),
    )
    construction = time.perf_counter() - started

    source = int(numpy.argmax(vertices[:, 2]))
    target = int(numpy.argmin(vertices[:, 2]))
    started = time.perf_counter()
    algorithm.geodesicDistance(source, target)
    query = time.perf_counter() - started

    print("  construction     : %.2f s" % construction)
    print("  single query     : %.2f s" % query)
    reference = 314086.0
    ratio = reference / float(faces.shape[0])
    print("  reference scan   : 21_M_3400E has %d triangles, %.1fx this mesh"
          % (int(reference), ratio))
    print("  extrapolated     : ~%.1f s per query at that size (MMP cost grows"
          % (query * ratio))
    print("                     faster than linearly, so treat this as a floor)")


def main():
    print("BSMT Milestone 2.2 - pygeodesic environment check")
    print(SEPARATOR)

    report_interpreter()
    numpy = report_numpy()
    if numpy is None:
        print("\nSTOP: numpy is required.")
        return 1

    geodesic = report_pygeodesic()
    if geodesic is None:
        print("\nSTOP: pygeodesic is not usable in this interpreter.")
        print("Milestone 2.2 is not satisfied until this section succeeds")
        print("inside Blender's own Python.")
        return 1

    plane_ok = test_flat_plane(numpy, geodesic)
    test_accuracy(numpy, geodesic)
    test_performance(numpy, geodesic)

    heading("Summary")
    print("  pygeodesic imports in this interpreter : yes")
    print("  planar exactness                       : %s"
          % ("yes" if plane_ok else "NO - investigate before Milestone 2.3"))
    print("  ready for Milestone 2.3                : %s"
          % ("yes" if plane_ok else "no"))
    return 0 if plane_ok else 1


if __name__ == "__main__":
    sys.exit(main())
