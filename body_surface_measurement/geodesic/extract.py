"""Blender-side extraction of a source scan into solver space.

Implements the fixed pipeline of PROJECT_SPEC.md sect. 6.2:

    evaluated geometry -> world transform -> coordinate-unit conversion
    -> millimetres -> numerical centering -> float64

Solver space is millimetres. Distances computed on these coordinates are
already in millimetres and must never be multiplied by a unit factor again.

The source object is never modified: geometry is read from an evaluated
temporary mesh which is released again with to_mesh_clear().
"""

import bpy
import numpy as np

# Submodule-direct imports (`from .x import y`), never `from . import x`:
# the latter resolves through an attribute on the package, which a None
# placeholder there would silently satisfy. See this package's __init__.
from ..measurement import unit_multiplier
from .spaces import geometry_hash, metric_key, solver_to_world, to_solver_space


class ExtractionError(Exception):
    """Raised when a source object cannot be turned into a solver mesh."""


class SolverMesh(object):
    """Triangulated scan geometry in solver space (mm, centered, float64)."""

    __slots__ = (
        "object_name",
        "vertices",            # (n, 3) float64, solver space
        "vertices_local",      # (n, 3) float64, untransformed mesh space
        "triangles",           # (m, 3) int32
        "triangle_to_polygon",  # (m,) int32
        "source_polygon_count",
        "matrix_world",        # (4, 4) float64
        "unit",
        "unit_multiplier",
        "center_mm",           # (3,) float64 offset removed in step 5
        "geometry_hash",
        "metric_key",
    )

    def solver_to_world(self, points):
        """Map solver-space points back to world space, in coordinate units."""
        return solver_to_world(points, self.center_mm, self.unit_multiplier)


def extract_solver_mesh(obj, depsgraph, unit, preprocessing=None):
    """Build a SolverMesh from a scan object. Does not modify the object."""
    if obj is None:
        raise ExtractionError("no object selected")
    if obj.type != 'MESH':
        raise ExtractionError("'%s' is not a mesh object" % obj.name)

    preprocessing = dict(preprocessing or {})
    preprocessing.setdefault("triangulation", "loop_triangles")
    preprocessing.setdefault("weld", "off")
    preprocessing.setdefault("centering", "bbox_center")

    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    if mesh is None:
        raise ExtractionError("'%s' produced no evaluated mesh" % obj.name)

    try:
        # Present in 4.x; guarded so a future auto-computed API still works.
        if hasattr(mesh, "calc_loop_triangles"):
            mesh.calc_loop_triangles()

        vertex_count = len(mesh.vertices)
        if vertex_count == 0:
            raise ExtractionError("'%s' has no vertices" % obj.name)

        flat = np.empty(vertex_count * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", flat)
        vertices_local = flat.reshape(vertex_count, 3).astype(np.float64)

        triangle_count = len(mesh.loop_triangles)
        if triangle_count == 0:
            raise ExtractionError("'%s' has no faces" % obj.name)

        flat_tris = np.empty(triangle_count * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", flat_tris)
        triangles = flat_tris.reshape(triangle_count, 3)

        polygon_index = np.empty(triangle_count, dtype=np.int32)
        mesh.loop_triangles.foreach_get("polygon_index", polygon_index)

        source_polygon_count = len(mesh.polygons)
    finally:
        # Always release the temporary mesh; the source object is untouched.
        evaluated.to_mesh_clear()

    matrix_world = np.array(obj.matrix_world, dtype=np.float64)
    multiplier = unit_multiplier(unit)
    vertices_solver, center_mm = to_solver_space(
        vertices_local, matrix_world, multiplier
    )

    result = SolverMesh()
    result.object_name = obj.name
    result.vertices = vertices_solver
    result.vertices_local = vertices_local
    result.triangles = triangles
    result.triangle_to_polygon = polygon_index
    result.source_polygon_count = source_polygon_count
    result.matrix_world = matrix_world
    result.unit = unit
    result.unit_multiplier = multiplier
    result.center_mm = center_mm
    result.geometry_hash = geometry_hash(vertices_local, triangles, preprocessing)
    result.metric_key = metric_key(matrix_world[:3, :3], multiplier)
    return result
