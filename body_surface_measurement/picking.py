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


def ray_cast_object(context, obj, origin, direction):
    """Cast a world-space ray at ONE object. Returns (location, normal) or None.

    ``Object.ray_cast`` works in the object's own local space, so the ray is
    transformed in and the hit transformed back out. The normal uses the
    inverse-transpose so it stays perpendicular under a non-uniform scale.
    """
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    matrix = evaluated.matrix_world
    try:
        inverse = matrix.inverted()
    except ValueError:
        return None                      # degenerate transform

    local_origin = inverse @ origin
    local_direction = (inverse.to_3x3() @ direction)
    if local_direction.length == 0.0:
        return None
    local_direction = local_direction.normalized()

    try:
        hit, location, normal, _index = evaluated.ray_cast(
            local_origin, local_direction
        )
    except (RuntimeError, ValueError):
        return None
    if not hit:
        return None

    world_location = matrix @ location
    world_normal = (matrix.to_3x3().inverted().transposed() @ normal)
    if world_normal.length > 0.0:
        world_normal = world_normal.normalized()
    return world_location, world_normal


def ray_cast_surface(context, region, rv3d, coord, target=None):
    """Cast a ray through a viewport pixel onto scene geometry.

    Returns (location, normal, object) with a world-space location, or None if
    nothing but empty space (or our own markers) was under the cursor.

    `target` restricts the cast to ONE object, and the pick operators always
    pass the object the researcher is working on. That is not a refinement, it
    is a correctness requirement: a BSMT measurement copy is created at the
    same transform as its source, so the two are exactly COINCIDENT. A
    scene-wide cast then returns whichever the depsgraph reaches first - in
    practice the original - and the landmark is silently recorded as belonging
    to a mesh the researcher was not working on. Every later stage then
    faithfully measures the wrong object.
    """
    origin, direction = _view_ray(region, rv3d, coord)

    if target is not None and getattr(target, "type", None) == 'MESH' \
            and not visualization.is_helper(target):
        hit = ray_cast_object(context, target, origin, direction)
        if hit is None:
            return None
        location, normal = hit
        return location.copy(), normal.copy(), getattr(target, "original",
                                                       target)

    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()

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
