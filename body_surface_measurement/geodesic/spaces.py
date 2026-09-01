"""Solver-space mapping and cache identity (pure numpy, no bpy).

Implements PROJECT_SPEC.md sect. 6.2 (physical coordinate space) and sect. 6.4
(geometry_hash vs metric_key). Kept free of bpy so the transform semantics
required by sect. 6.3 can be unit tested outside Blender.
"""

import hashlib

import numpy as np

# Relative rounding applied to the metric tensor before hashing, so that
# floating point noise in matrix_world cannot spuriously invalidate a result.
METRIC_QUANTISATION = 1e-9


def to_solver_space(vertices_local, matrix_world, unit_multiplier):
    """Map mesh-local coordinates into solver space.

    Pipeline (6.2): world transform -> coordinate-unit conversion ->
    millimetres -> numerical centering, in float64 throughout.

    Returns (vertices_solver, center_mm).
    """
    vertices_local = np.asarray(vertices_local, dtype=np.float64)
    matrix_world = np.asarray(matrix_world, dtype=np.float64)

    linear = matrix_world[:3, :3]
    translation = matrix_world[:3, 3]

    vertices_world = vertices_local @ linear.T + translation
    vertices_mm = vertices_world * float(unit_multiplier)
    center_mm = 0.5 * (vertices_mm.min(axis=0) + vertices_mm.max(axis=0))
    return vertices_mm - center_mm, center_mm


def solver_to_world(points, center_mm, unit_multiplier):
    """Inverse of to_solver_space, back to world space in coordinate units."""
    points = np.asarray(points, dtype=np.float64)
    return (points + np.asarray(center_mm, dtype=np.float64)) / float(unit_multiplier)


def geometry_hash(vertices_local, triangles, preprocessing):
    """Identity of the mesh as data. Excludes matrix_world entirely (6.4).

    A change here invalidates SurfacePoints, because triangle indices may no
    longer denote the same surface location.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(np.ascontiguousarray(vertices_local, dtype=np.float64).tobytes())
    digest.update(np.ascontiguousarray(triangles, dtype=np.int32).tobytes())
    digest.update(repr(sorted(preprocessing.items())).encode("utf-8"))
    return digest.hexdigest()


def metric_key(linear, unit_multiplier):
    """Metric-affecting transform state (6.4).

    Built from the right Cauchy-Green tensor T = L^T L, scaled by the squared
    unit multiplier so that T is the physical metric in mm^2 per squared local
    unit. For the polar decomposition L = R * S this equals S**2, so the key is
    exactly invariant to rotation, and the translation column never enters it.

    The key hashes two parts:

      shape     T normalised by its own magnitude, coarsely quantised. Absorbs
                floating point noise; invariant under uniform rescaling.
      magnitude that magnitude, kept to 12 significant digits.

    Both are required. Hashing only the normalised shape would make the key
    scale-invariant, and a uniform scale would then wrongly reuse a cached
    distance instead of forcing recomputation.

    Because the unit multiplier is folded into T, a unit change combined with a
    compensating object scale yields the same key - which is correct: the
    physical surface metric, and therefore the distance in millimetres, is
    genuinely unchanged.

    A change here does NOT invalidate SurfacePoints, but DOES force geodesic
    recomputation (6.5).
    """
    linear = np.asarray(linear, dtype=np.float64)[:3, :3]
    tensor = (linear.T @ linear) * (float(unit_multiplier) ** 2)

    magnitude = float(np.abs(tensor).max())
    if magnitude <= 0.0:
        # Fully degenerate transform (zero scale on every axis).
        return "degenerate"

    shape = np.round(tensor / (magnitude * METRIC_QUANTISATION))
    # -0.0 and +0.0 are numerically equal but hash differently.
    shape[shape == 0.0] = 0.0

    digest = hashlib.blake2b(digest_size=8)
    digest.update(np.ascontiguousarray(shape, dtype=np.float64).tobytes())
    digest.update(("%.12e" % magnitude).encode("utf-8"))
    return digest.hexdigest()
