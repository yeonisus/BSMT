"""Picking must survive any rigid object transform, and say why when it can't.

    /path/to/blender -b --factory-startup --python tests/test_picking_transforms.py

Motivated by a report against a real preprocessed + repaired PLY measurement
mesh at location (-28570, -2692, -176), rotation (90.1, -2.3, -0.6): Alignment
> Pick LEFT answered "no mesh surface under the cursor of 'scan (1)_BSMT'"
while the body was plainly on screen under the cursor.

The transform turned out to be innocent. What was not is below.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_picking_transforms.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_picking_transforms.py")
    raise SystemExit(0)

from mathutils import Euler, Vector  # noqa: E402

FAILURES = []
CHECKS = [0]

#: The transform from the report.
SCAN_LOCATION = (-28570.0, -2692.0, -176.0)
SCAN_ROTATION = (90.1, -2.3, -0.6)


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)


def sphere(location=(0, 0, 0), rotation=(0, 0, 0), radius=850.0,
           name="scan (1)"):
    """A body-scale mesh at an arbitrary rigid transform. Unit scale."""
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=radius)
    obj = bpy.context.object
    obj.name = name
    obj.location = location
    obj.rotation_euler = Euler([math.radians(a) for a in rotation], 'XYZ')
    obj.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return obj


def ray_at(obj, direction=(0.0, 0.0, -1.0), standoff=5000.0):
    """A world-space ray aimed straight through the object's world centre."""
    centre = obj.matrix_world @ Vector((0.0, 0.0, 0.0))
    heading = Vector(direction).normalized()
    return centre - heading * standoff, heading


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import geodesic, picking

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------- A - E --
    print("\na-e. a rigid transform never causes a miss")
    cases = (
        ("A identity", (0, 0, 0), (0, 0, 0)),
        ("B translation only", SCAN_LOCATION, (0, 0, 0)),
        ("C rotation only", (0, 0, 0), SCAN_ROTATION),
        ("D translation + rotation", SCAN_LOCATION, SCAN_ROTATION),
        ("E the reported measurement mesh", SCAN_LOCATION, SCAN_ROTATION),
    )
    for label, location, rotation in cases:
        obj = sphere(location, rotation)
        origin, heading = ray_at(obj)
        hit = picking.ray_cast_object(context, obj, origin, heading)
        check("%s: the ray hits the surface" % label, hit is not None)
        if hit is None:
            continue
        world_location, _normal = hit
        # The hit must be ON the sphere: 850 from its world centre.
        centre = obj.matrix_world @ Vector((0.0, 0.0, 0.0))
        radius = (world_location - centre).length
        # A UV sphere is a polyhedron: a ray through its centre meets a
        # FACE, which sits slightly inside the ideal radius.
        check("  and lands on the surface, not the origin",
              abs(radius - 850.0) < 5.0, radius)
        # And it must be the NEAR side - the first surface the ray meets.
        check("  on the near side, facing the ray",
              (world_location - origin).length
              < (centre - origin).length, world_location)

    print("\n   Alignment and Landmark picking use one implementation")
    operators_source = open(os.path.join(ROOT, "body_surface_measurement",
                                         "operators.py")).read()
    check("the Alignment pick delegates to bsmt.pick_point",
          "bpy.ops.bsmt.pick_point('INVOKE_DEFAULT', target='ALIGN'"
          in operators_source)
    check("there is exactly one ray_cast_surface call site",
          operators_source.count("picking.ray_cast_surface(") == 1,
          operators_source.count("picking.ray_cast_surface("))
    picking_source = open(os.path.join(ROOT, "body_surface_measurement",
                                       "picking.py")).read()
    check("and one place that inverts the object matrix",
          picking_source.count("matrix.inverted()") <= 2,
          picking_source.count("matrix.inverted()"))

    # ------------------------------------------------------------------ F --
    print("\nf. a ray through empty space still reports no surface")
    obj = sphere(SCAN_LOCATION, SCAN_ROTATION)
    centre = obj.matrix_world @ Vector((0.0, 0.0, 0.0))
    heading = Vector((0.0, 0.0, -1.0))
    # Aimed well to the side of the body, parallel to the first ray.
    origin = centre + Vector((100000.0, 0.0, 5000.0))
    check("empty space is a miss",
          picking.ray_cast_object(context, obj, origin, heading) is None)
    check("and it is not blocked for any other reason",
          picking.pick_blocker(context, obj) == "",
          picking.pick_blocker(context, obj))

    # ------------------------------------------------- the actual defect --
    print("\nG. THE DEFECT: a target disabled in the viewport")
    # Blender's two hide flags are not the same thing, and only one of them
    # removes the object from evaluation.
    obj = sphere(SCAN_LOCATION, SCAN_ROTATION)
    origin, heading = ray_at(obj)

    obj.hide_set(True)                        # the EYE icon
    context.view_layer.update()
    check("hide_set (eye) still leaves the ray able to hit",
          picking.ray_cast_object(context, obj, origin, heading) is not None)
    check("but the pick is refused: it cannot be seen either",
          "hidden in the viewport" in picking.pick_blocker(context, obj),
          picking.pick_blocker(context, obj))
    obj.hide_set(False)
    context.view_layer.update()

    obj.hide_viewport = True                  # the MONITOR icon
    context.view_layer.update()
    blocker = picking.pick_blocker(context, obj)
    check("hide_viewport (monitor) is reported as a blocker", bool(blocker),
          blocker)
    check("and the message names the real cause, not the cursor",
          "hidden in the viewport" in blocker, blocker)
    check("telling the researcher how to fix it",
          "Show it again" in blocker, blocker)
    obj.hide_viewport = False
    context.view_layer.update()
    check("showing it again clears the blocker",
          picking.pick_blocker(context, obj) == "")

    print("\n   a collection disabled in the viewport does it too")
    collection = bpy.data.collections.new("Scans")
    context.scene.collection.children.link(collection)
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    collection.objects.link(obj)
    context.view_layer.update()
    check("linked into the collection, still pickable",
          picking.pick_blocker(context, obj) == "")
    collection.hide_viewport = True
    context.view_layer.update()
    check("a hidden collection is reported too",
          "hidden in the viewport" in picking.pick_blocker(context, obj),
          picking.pick_blocker(context, obj))
    collection.hide_viewport = False
    layer = context.view_layer.layer_collection.children["Scans"]
    layer.exclude = True
    context.view_layer.update()
    check("so is a collection excluded from the view layer",
          "hidden in the viewport" in picking.pick_blocker(context, obj),
          picking.pick_blocker(context, obj))
    layer.exclude = False
    context.view_layer.update()

    print("\n   BSMT's own Show Source button reproduces it")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=850.0)
    source = context.object
    source.name = "scan (1)"
    context.view_layer.objects.active = source
    props.preprocess_target_triangles = 500000
    check("a measurement mesh was made",
          bpy.ops.bsmt.create_measurement_copy() == {'FINISHED'})
    copy = bpy.data.objects[props.preprocess_copy_name]
    for obj_at in (source, copy):
        obj_at.location = SCAN_LOCATION
        obj_at.rotation_euler = Euler(
            [math.radians(a) for a in SCAN_ROTATION], 'XYZ')
    context.view_layer.objects.active = copy
    context.view_layer.update()
    check("the measurement mesh is pickable to begin with",
          picking.pick_blocker(context, copy) == "",
          picking.pick_blocker(context, copy))

    bpy.ops.bsmt.show_scan(which='SOURCE')
    context.view_layer.update()
    check("after Show Source the copy is hidden but still ACTIVE",
          context.view_layer.objects.active is copy and copy.hide_viewport)
    check("the source is still on screen, coincident and identical",
          not source.hide_viewport)
    blocker = picking.pick_blocker(context, copy)
    check("picking now explains itself instead of blaming the cursor",
          "hidden in the viewport" in blocker, blocker)

    bpy.ops.bsmt.show_scan(which='BOTH')
    context.view_layer.update()
    check("Show Both makes it pickable again",
          picking.pick_blocker(context, copy) == "",
          picking.pick_blocker(context, copy))
    origin, heading = ray_at(copy)
    check("and the ray hits the measurement mesh at its transform",
          picking.ray_cast_object(context, copy, origin, heading) is not None)

    # ------------------------------------------- the canonical fallback ---
    print("\nH. the canonical BVH answers when the depsgraph has nothing")
    canonical = geodesic.meshcache.get(context, copy, props.unit, rebuild=True)
    check("a canonical mesh is cached", canonical is not None)
    copy.hide_viewport = True
    context.view_layer.update()
    hit = picking.ray_cast_object(context, copy, origin, heading)
    check("a cast still resolves through the canonical BVH",
          hit is not None, hit)
    if hit is not None:
        centre = copy.matrix_world @ Vector((0.0, 0.0, 0.0))
        check("  and lands on the surface",
              abs((hit[0] - centre).length - 850.0) < 2.0,
              (hit[0] - centre).length)
    check("but the pick is still REFUSED, because it cannot be seen",
          bool(picking.pick_blocker(context, copy)))
    copy.hide_viewport = False
    context.view_layer.update()

    print("\nI. no workaround applies a transform or edits geometry")
    for forbidden in ("transform_apply", "obj.matrix_world =",
                      "obj.location =", "obj.rotation_euler =",
                      "obj.scale ="):
        check("picking.py never writes %s" % forbidden,
              forbidden not in picking_source, forbidden)

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        print("BSMT_PICKING_RESULT=%d" % code)
