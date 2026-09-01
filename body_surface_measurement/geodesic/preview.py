"""Connected-component visualisation (diagnostics only).

Builds one temporary BSMT helper object per connected component, coloured
distinctly, so components can be located spatially on the scan.

Non-destructive by construction:

* the source scan's mesh, transform and object state are never touched;
* helper geometry is a *copy* of each component's triangles, placed in world
  space with an identity transform, tagged with the standard bsmt_helper flag
  and linked into the BSMT_Helpers collection;
* components are never welded, merged or repaired - they are only drawn;
* removal goes through visualization.remove_object(), which refuses to delete
  anything that is not a BSMT helper.

Requires bpy, so it is loaded through the guarded importer in this package's
__init__ exactly like extract.py.
"""

import colorsys

import bpy
import numpy as np

# Submodule-direct imports; see this package's __init__ for why.
from ..visualization import (
    COMPONENT_PREFIX,
    HELPER_FLAG,
    get_material,
    is_helper,
    new_helper_object,
    remove_collection_if_empty,
    remove_object,
)

# Distinct, colour-blind-tolerant starting palette. Beyond it, hues are spread
# by the golden ratio so neighbouring components stay separable.
PALETTE = (
    (0.95, 0.26, 0.21, 1.0),   # red
    (0.13, 0.59, 0.95, 1.0),   # blue
    (0.30, 0.69, 0.31, 1.0),   # green
    (1.00, 0.76, 0.03, 1.0),   # amber
    (0.61, 0.15, 0.69, 1.0),   # purple
    (0.00, 0.74, 0.83, 1.0),   # cyan
    (1.00, 0.44, 0.00, 1.0),   # orange
    (0.55, 0.43, 0.39, 1.0),   # brown
)

GOLDEN_RATIO_CONJUGATE = 0.6180339887498949


class PreviewIntegrityError(Exception):
    """Raised when generated preview geometry does not match the labelling.

    Colours that do not correspond to real components are worse than no
    colours at all, so any mismatch aborts the preview instead of drawing it.
    """


def component_color(index):
    """Stable colour for component `index` (0-based)."""
    if index < len(PALETTE):
        return PALETTE[index]
    hue = (index * GOLDEN_RATIO_CONJUGATE) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.95)
    return (red, green, blue, 1.0)


def component_name(index):
    """Object name for component `index` (0-based); displayed 1-based."""
    return "%s%d" % (COMPONENT_PREFIX, index + 1)


def _component_mesh(name, world_vertices, triangles):
    """Copy one component's triangles into a fresh mesh datablock."""
    used = np.unique(triangles)
    remap = np.full(world_vertices.shape[0], -1, dtype=np.int64)
    remap[used] = np.arange(used.size, dtype=np.int64)

    mesh = bpy.data.meshes.new(name)
    # from_pydata is used deliberately: it is stable across Blender versions,
    # unlike direct loop/polygon buffer filling, whose API changed in 4.x.
    mesh.from_pydata(
        world_vertices[used].tolist(), [], remap[triangles].tolist()
    )
    mesh.validate(verbose=False)
    mesh.update()
    mesh[HELPER_FLAG] = True
    return mesh, int(used.size)


def build(context, world_vertices, triangles, triangle_labels, component_count,
          expected_counts=None):
    """Create one helper object per component, straight from canonical arrays.

    `triangles` is the canonical triangle array and `triangle_labels` is its
    per-triangle labelling; component n is built from

        canonical_vertices, canonical_triangles[triangle_labels == n]

    and nothing is re-indexed through any other triangle ordering. The
    canonical array is the single source of truth.

    Every generated object is checked against the labelling before this
    returns. Raises PreviewIntegrityError on any mismatch, after removing the
    partial preview.

    Returns a list of (index, triangle_count, vertex_count, color).
    """
    world_vertices = np.asarray(world_vertices, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    triangle_labels = np.asarray(triangle_labels, dtype=np.int64)

    if triangle_labels.shape[0] != triangles.shape[0]:
        raise PreviewIntegrityError(
            "label array holds %d entries but the canonical triangle array "
            "holds %d" % (triangle_labels.shape[0], triangles.shape[0])
        )
    if triangles.size and (
        int(triangles.min()) < 0
        or int(triangles.max()) >= int(world_vertices.shape[0])
    ):
        raise PreviewIntegrityError(
            "canonical triangles index outside the canonical vertex array"
        )

    clear(context)

    created = []
    try:
        for index in range(component_count):
            subset = triangles[triangle_labels == index]
            if subset.size == 0:
                continue
            name = component_name(index)
            color = component_color(index)

            mesh, vertex_count = _component_mesh(
                name + "_Mesh", world_vertices, subset
            )
            obj = new_helper_object(context, name, mesh, color)
            # Helper geometry is coincident with the scan surface; draw it on
            # top instead of z-fighting with it.
            obj.show_in_front = True
            obj.data.materials.append(
                get_material("BSMT_Material_Component_%d" % (index + 1), color)
            )

            wanted = int(subset.shape[0])
            built = len(obj.data.polygons)
            if built != wanted:
                raise PreviewIntegrityError(
                    "component %d: %d triangles were selected but the helper "
                    "object holds %d faces (mesh validation may have dropped "
                    "geometry)" % (index + 1, wanted, built)
                )
            if len(obj.data.loops) != 3 * built:
                raise PreviewIntegrityError(
                    "component %d: helper object contains non-triangular faces"
                    % (index + 1)
                )
            if expected_counts is not None:
                expected = int(expected_counts[index])
                if wanted != expected:
                    raise PreviewIntegrityError(
                        "component %d: %d triangles selected but %d reported"
                        % (index + 1, wanted, expected)
                    )

            created.append((index, wanted, vertex_count, color))

        total_drawn = sum(row[1] for row in created)
        if total_drawn != int(triangles.shape[0]):
            raise PreviewIntegrityError(
                "preview covers %d triangles but the canonical array holds %d"
                % (total_drawn, int(triangles.shape[0]))
            )
    except Exception:
        # Never leave misleading colours behind.
        clear(context)
        raise

    return created


def clear(context):
    """Remove component preview objects only. Returns how many were removed."""
    removed = 0
    for obj in list(bpy.data.objects):
        if is_helper(obj) and obj.name.startswith(COMPONENT_PREFIX):
            if remove_object(obj):
                removed += 1
    remove_collection_if_empty()
    return removed


def apply_isolation(context, isolate, component_count):
    """Show every component (isolate == 0) or only component `isolate` (1-based).

    Visibility only: no object is created, moved or deleted here.
    """
    for index in range(component_count):
        obj = bpy.data.objects.get(component_name(index))
        if obj is None or not is_helper(obj):
            continue
        visible = isolate == 0 or isolate == index + 1
        obj.hide_viewport = not visible
        obj.hide_render = not visible
