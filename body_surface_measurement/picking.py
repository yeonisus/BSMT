"""Viewport ray casting.

The clicked location is the exact ray/mesh intersection point in world space.
No snapping to vertices, edges or faces is performed.
"""

from bpy_extras import view3d_utils

from . import visualization

# How many times we are willing to step past one of our own helper markers
# before giving up on the click.
MAX_HELPER_SKIPS = 8


def _view_ray(region, rv3d, coord):
    """Return (origin, direction) in world space for a region-relative pixel."""
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    if not rv3d.is_perspective:
        # In an orthographic view the returned origin sits on the view plane,
        # so geometry behind that plane would be missed. Pull the ray back.
        pull_back = max(rv3d.view_distance * 10.0, 1000.0)
        origin = origin - direction * pull_back
    return origin, direction


def region_from_area(area):
    """The 3D drawing region of a VIEW_3D area (not the sidebar/header)."""
    if area is None:
        return None
    for region in area.regions:
        if region.type == 'WINDOW':
            return region
    return None


def coord_in_region(region, event):
    """Mouse position relative to region, or None when outside it."""
    if region is None:
        return None
    x = event.mouse_x - region.x
    y = event.mouse_y - region.y
    if 0 <= x < region.width and 0 <= y < region.height:
        return (x, y)
    return None


def _skip_distance(obj):
    """Distance needed to step completely past a helper marker."""
    dimensions = getattr(obj, "dimensions", None)
    if dimensions is None:
        return 1e-4
    largest = max(dimensions[0], dimensions[1], dimensions[2])
    return largest * 1.05 + 1e-6


def world_ray(region, rv3d, coord):
    """Public accessor for the viewport ray in world space."""
    return _view_ray(region, rv3d, coord)


def ray_cast_surface(context, region, rv3d, coord):
    """Cast a ray through a viewport pixel onto the visible scene geometry.

    Returns (location, normal, object) with a world-space location, or None if
    nothing but empty space (or our own markers) was under the cursor.
    """
    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()
    origin, direction = _view_ray(region, rv3d, coord)

    for _ in range(MAX_HELPER_SKIPS):
        hit, location, normal, _index, obj, _matrix = scene.ray_cast(
            depsgraph, origin, direction
        )
        if not hit:
            return None
        if not visualization.is_helper(obj):
            original = getattr(obj, "original", obj)
            return location.copy(), normal.copy(), original
        # The ray landed on one of our own markers: continue behind it.
        origin = location + direction * _skip_distance(obj)

    return None
