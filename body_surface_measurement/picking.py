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


#: Why an object cannot be picked on. Reported instead of "nothing under the
#: cursor", which is what BSMT used to say for every one of these and which
#: sent the researcher looking for a problem with their aim.
BLOCKED_NOT_MESH = "is not a mesh object"
BLOCKED_HELPER = "is a BSMT helper, not a scan"
BLOCKED_DISABLED = (
    "is hidden in the viewport, so there is nothing on screen to click. Show "
    "it again - Scan Preprocessing > Measurement, or the eye and monitor "
    "icons in the Outliner - and pick on the mesh you can see"
)
BLOCKED_NO_GEOMETRY = "has no evaluated geometry to cast against"


def pick_blocker(context, obj):
    """Why a click on `obj` cannot succeed, or "" when it can.

    This exists because "no mesh surface under the cursor" was BSMT's answer
    to a genuinely different question. An object that is DISABLED in the
    viewport has no evaluated mesh, so ``Object.ray_cast`` finds nothing -
    and BSMT's own Source / Measurement buttons set exactly that flag. A
    measurement mesh sits at the same transform as its source, so the
    researcher sees a body, clicks on it, and is told their cursor is not
    over any surface. It is: just not over the one the pick was aimed at.

    The test is `visible_get()`, which is the researcher's question rather
    than Blender's: can this object be seen and therefore clicked? It covers
    every way an object leaves the viewport - the eye icon, the monitor icon,
    a collection hidden in the viewport, and a collection excluded from the
    view layer.

    Measured on Blender 4.5.13, the underlying states differ in a way no
    single API flag exposes: `hide_viewport`, a hidden collection and an
    excluded collection all make ``Object.ray_cast`` RAISE, while
    `hide_set()` leaves it working - and `evaluated.data` reports a full mesh
    in all four. So evaluability cannot be probed without attempting a cast.
    Visibility can, it is the question that actually matters, and refusing on
    it is the safe direction: a pick on an object nobody can see would record
    a reference against geometry the researcher never inspected.
    """
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return BLOCKED_NOT_MESH
    if visualization.is_helper(obj):
        return BLOCKED_HELPER
    try:
        if not obj.visible_get():
            return BLOCKED_DISABLED
    except Exception:                                 # pragma: no cover
        pass
    return ""


def _canonical_ray_cast(context, obj, origin, direction):
    """Fall back to BSMT's own canonical BVH. Returns (location, normal) or None.

    The canonical mesh is built from the object's geometry in LOCAL space and
    the cast transforms the world ray in and the hit back out, exactly as the
    depsgraph path does. It consults no evaluated object, so it still answers
    when Blender has nothing evaluated to offer - and it is the same BVH the
    SurfacePoint is built against a moment later, so the two cannot disagree
    about where the surface is.
    """
    from . import geodesic
    if not geodesic.MESHCACHE_AVAILABLE or geodesic.meshcache is None:
        return None
    canonical = geodesic.meshcache.peek_current(obj)
    if canonical is None:
        return None
    result = geodesic.meshcache.ray_cast_local(
        canonical, obj.matrix_world, origin, direction
    )
    if result is None:
        return None
    _triangle, _local, world_location, _bary = result
    from mathutils import Vector
    location = Vector((float(world_location[0]), float(world_location[1]),
                       float(world_location[2])))
    # The normal is not needed by any caller that reaches this path, and
    # inventing one from the canonical arrays would be a second definition of
    # a value the depsgraph path gets from Blender. Zero says "not known".
    return location, Vector((0.0, 0.0, 0.0))


def ray_cast_object(context, obj, origin, direction):
    """Cast a world-space ray at ONE object. Returns (location, normal) or None.

    ``Object.ray_cast`` works in the object's own local space, so the ray is
    transformed in and the hit transformed back out. The normal uses the
    inverse-transpose so it stays perpendicular under a non-uniform scale.

    A rigid transform - any translation, any rotation - is handled entirely
    by that inversion and cannot cause a miss; verified on a mesh at
    (-28570, -2692, -176) rotated (90.1, -2.3, -0.6).

    When the object has no evaluated mesh, BSMT's canonical BVH answers
    instead, so a momentary depsgraph gap does not read as "nothing there".
    """
    matrix = obj.matrix_world
    try:
        inverse = matrix.inverted()
    except ValueError:
        return None                      # degenerate transform

    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    matrix = evaluated.matrix_world
    try:
        inverse = matrix.inverted()
    except ValueError:
        return None

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
        return _canonical_ray_cast(context, obj, origin, direction)
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
