"""Repair locality, source immutability and distance preservation (M 3.25).

    /path/to/blender -b --factory-startup --python tests/test_repair_locality_blender.py

tests/test_mesh_repair_blender.py already proves that repair detects the right
defects, refuses the ones it cannot fix locally, rolls back a repair that
would worsen topology, never welds by tolerance, and leaves the source scan
byte-for-byte identical. None of that is re-proved here.

What was missing, and is what this file measures, is LOCALITY expressed as a
distance: that a repair in one region of the scan does not change a
measurement taken in another.

Two different claims, deliberately separated
--------------------------------------------
1. **Data-state invalidation policy.** BSMT invalidates GLOBALLY. Repair
   changes the mesh, so the geometry hash changes, so `landmarks.classify`
   marks EVERY landmark on that object non-VALID - not only the ones near the
   repair. Nothing is re-projected. This is deliberate and conservative: a
   stored triangle index is meaningless once triangles have been renumbered,
   and silently re-attaching a researcher's landmark would move their data.

2. **Actual geometric locality of the repair.** The surface itself changes
   only inside the defect neighbourhood. Every vertex outside it keeps its
   exact coordinates, and a distance measured between two points far from the
   repair is the same number afterwards.

These are not in tension, and confusing them is the trap this file exists to
avoid. (1) says a researcher must re-pick and recompute. (2) says that when
they do, they get the same answer. A test that only looked at (1) would
conclude repair destroys everything; a test that only looked at (2) would
conclude nothing needs recomputing. Both are measured below, separately.

Because (1) holds, locality in (2) cannot be shown by carrying a SurfacePoint
across the repair - the product refuses to do that, correctly. It is shown
geometrically instead: the same 3D positions are re-attached to the repaired
surface through the canonical BVH, exactly as a researcher re-picking would,
and the distance is compared.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_repair_locality_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_repair_locality_blender.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]

#: The repair is confined to the north cap; the unrelated pair lives in the
#: south cap. Expressed as a fraction of the sphere radius so the two regions
#: are separated by more than half the object.
NORTH_CAP = 0.55
SOUTH_CAP = -0.55

#: A re-attached point lands on the nearest triangle of the repaired surface.
#: Away from the repair that surface is bit-identical, so the only difference
#: is float64 barycentric reconstruction: nanometres on a 100 mm sphere.
#: Stated as an absolute millimetre figure because it does not scale with the
#: measurement - it is reconstruction noise, not a relative error.
RECONSTRUCTION_TOLERANCE_MM = 1e-6


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def table(header, rows):
    widths = [max(len(str(header[i])), max(len(str(r[i])) for r in rows))
              for i in range(len(header))]
    line = "  | " + " | ".join(str(header[i]).ljust(widths[i])
                               for i in range(len(header))) + " |"
    print("\n" + line)
    print("  |" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        print("  | " + " | ".join(str(row[i]).ljust(widths[i])
                                  for i in range(len(row))) + " |")


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def vertex_array(obj):
    """Every vertex coordinate as float64, in index order."""
    mesh = obj.data
    flat = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", flat)
    return flat.reshape((-1, 3))


def triangle_array(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return np.array([tuple(t.vertices) for t in mesh.loop_triangles],
                    dtype=np.int64)


def counts(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return (len(mesh.vertices), len(mesh.polygons), len(mesh.loop_triangles))


def collapse_in_north_cap(obj, pairs, radius):
    """Make exactly-coincident vertex pairs, all inside the north cap.

    The same defect shape tests/test_mesh_repair_blender.py uses - one vertex
    moved exactly onto a neighbour, so the two triangles round that edge
    become zero-area while the edge and face topology are untouched - but
    confined to one region so that "outside the region" is a testable thing.
    """
    mesh = obj.data
    limit = NORTH_CAP * radius
    used = set()
    collapsed = []
    for edge in mesh.edges:
        first, second = edge.vertices
        if first in used or second in used:
            continue
        if mesh.vertices[first].co.z < limit or \
                mesh.vertices[second].co.z < limit:
            continue
        mesh.vertices[second].co = mesh.vertices[first].co
        used.update((first, second))
        collapsed.append((int(first), int(second)))
        if len(collapsed) >= pairs:
            break
    mesh.update()
    return collapsed


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, landmarks, preprocess,
                                          state)
    from mathutils import Vector

    context = bpy.context
    props = context.scene.bsmt

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)

    solve = geodesic.solve
    RADIUS = 100.0

    def canonical_of(obj):
        return geodesic.meshcache.get(context, obj, props.unit, rebuild=True)

    def attach(canonical, target_local):
        """Re-attach a 3D position to the nearest point of a surface.

        Test helper, not product code. It has to be more careful than the
        equivalent in tests/test_decimation_sensitivity.py because THIS
        fixture deliberately contains zero-area triangles: when the query
        point is exactly a vertex shared by several triangles, the BVH may
        return any one of them, and which one is not stable between runs.
        Land on the zero-area one and there is no barycentric frame to build.

        So the nearest candidates are tried in distance order and the first
        one that actually has a frame is used. That is what a researcher
        re-picking by eye would get too - you cannot pick a triangle with no
        area - and it makes the measurement deterministic.

        This is a limitation of the test's re-attachment, not of BSMT:
        production never builds barycentrics from `bvh.find_nearest`. Its one
        use of that call is viz.surface_normals, which reads only the normal,
        is wrapped in try/except, and discards a zero-length result.
        """
        query = Vector(tuple(float(v) for v in target_local))
        candidates = []
        hit = canonical.bvh.find_nearest(query)
        if hit is not None and hit[0] is not None:
            candidates.append(hit)
        try:
            candidates.extend(canonical.bvh.find_nearest_range(query, 1e-3))
        except Exception:                                 # pragma: no cover
            pass
        candidates.sort(key=lambda entry: entry[3])

        for location, _normal, index, _distance in candidates:
            triangle = int(index)
            found = np.array(location, dtype=np.float64)
            try:
                bary = canonical.barycentric_local(triangle, found)
            except Exception:
                continue                # zero-area: no frame, try the next
            drift = float(np.linalg.norm(found - np.asarray(target_local)))
            return triangle, bary, drift
        return None, None, float("inf")

    def measure(canonical, a_local, b_local):
        """(surface_mm, straight_mm, worst drift) between two positions."""
        triangle_a, bary_a, drift_a = attach(canonical, a_local)
        triangle_b, bary_b, drift_b = attach(canonical, b_local)
        if triangle_a is None or triangle_b is None:
            return None, None, float("inf")
        spec_a = solve.PointSpec(
            triangle_a, bary_a,
            component_id=canonical.component_of(triangle_a),
            source_object=canonical.source_object,
            geometry_hash=canonical.geometry_hash, status="VALID", valid=True)
        spec_b = solve.PointSpec(
            triangle_b, bary_b,
            component_id=canonical.component_of(triangle_b),
            source_object=canonical.source_object,
            geometry_hash=canonical.geometry_hash, status="VALID", valid=True)
        result = solve.surface_distance(
            canonical.vertices_solver, canonical.triangles, spec_a, spec_b)
        position_a = canonical.local_from(triangle_a, bary_a)
        position_b = canonical.local_from(triangle_b, bary_b)
        straight = float(np.linalg.norm(position_b - position_a))
        return (float(result.distance_mm), straight, max(drift_a, drift_b))

    # ================================================================== A ==
    print("\nA. the fixture: a defect in the north cap, a measurement in "
          "the south")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32,
                                         radius=RADIUS)
    source = context.object
    source.name = "Scan"
    source.data.name = "Scan_Mesh"
    context.view_layer.objects.active = source

    props.preprocess_target_triangles = 500000        # copy, do not decimate
    check("a measurement mesh was produced",
          bpy.ops.bsmt.create_measurement_copy() == {'FINISHED'})
    copy = bpy.data.objects[props.preprocess_copy_name]

    collapsed = collapse_in_north_cap(copy, pairs=6, radius=RADIUS)
    check("the defect was planted: %d coincident pairs" % len(collapsed),
          len(collapsed) == 6, len(collapsed))
    context.view_layer.objects.active = copy

    before_vertices = vertex_array(copy)
    before_triangles = triangle_array(copy)
    before_counts = counts(copy)
    defect_vertices = sorted({v for pair in collapsed for v in pair})
    lowest_defect_z = float(min(before_vertices[v][2] for v in defect_vertices))
    check("every defect vertex is in the north cap (lowest z = %.2f)"
          % lowest_defect_z, lowest_defect_z >= NORTH_CAP * RADIUS - 1e-9,
          lowest_defect_z)

    canonical_before = canonical_of(copy)
    topo_before = dict(canonical_before.topology or {})
    check("the fixture really is degenerate before repair",
          int(topo_before.get("degenerate_triangle_count", 0)) > 0,
          topo_before.get("degenerate_triangle_count"))
    check("and manifold, closed, single-component to start with",
          int(topo_before.get("nonmanifold_edge_count", 0)) == 0
          and int(topo_before.get("boundary_edge_count", 0)) == 0
          and int(topo_before.get("component_count", 0)) == 1, topo_before)

    # The source scan, recorded for the immutability comparison.
    source_vertices_before = vertex_array(source)
    source_triangles_before = triangle_array(source)
    source_counts_before = counts(source)
    source_hash_before = canonical_of(source).geometry_hash
    context.view_layer.objects.active = copy

    # ================================================================== B ==
    print("\nB. two measurements, taken before the repair")
    # FAR: both endpoints deep in the south cap, on the opposite side of the
    # sphere from every defect. NEAR: both endpoints inside the north cap,
    # among the collapsed vertices.
    def pick_in_band(vertices, low, high, count):
        chosen = []
        for index in range(len(vertices)):
            z = vertices[index][2]
            if low <= z <= high:
                if all(np.linalg.norm(vertices[index] - vertices[c]) > 40.0
                       for c in chosen):
                    chosen.append(index)
            if len(chosen) >= count:
                break
        return chosen

    far_a, far_b = pick_in_band(before_vertices, -RADIUS,
                                SOUTH_CAP * RADIUS, 2)
    near_a, near_b = pick_in_band(before_vertices, NORTH_CAP * RADIUS,
                                  RADIUS, 2)
    far_a_pos = before_vertices[far_a].copy()
    far_b_pos = before_vertices[far_b].copy()
    near_a_pos = before_vertices[near_a].copy()
    near_b_pos = before_vertices[near_b].copy()

    check("the FAR pair is in the south cap (z = %.2f, %.2f)"
          % (far_a_pos[2], far_b_pos[2]),
          far_a_pos[2] <= SOUTH_CAP * RADIUS
          and far_b_pos[2] <= SOUTH_CAP * RADIUS)
    check("the NEAR pair is in the north cap (z = %.2f, %.2f)"
          % (near_a_pos[2], near_b_pos[2]),
          near_a_pos[2] >= NORTH_CAP * RADIUS
          and near_b_pos[2] >= NORTH_CAP * RADIUS)
    separation = float(min(
        np.linalg.norm(far_a_pos - before_vertices[v]) for v in defect_vertices
    ))
    check("the FAR pair is %.1f mm from the nearest defect vertex"
          % separation, separation > RADIUS, separation)

    # The FAR baseline is taken by calling the solver directly, which
    # deliberately bypasses the readiness gate: the gate refuses this whole
    # mesh because it is degenerate SOMEWHERE, which is correct product
    # behaviour and is exactly what tests/test_degenerate_policy.py proves.
    # Bypassing it is legitimate here and only here, because this is a
    # geometric experiment about one region, and the FAR path lies entirely
    # in the half of the sphere that carries no degenerate triangle. It is a
    # test-internal baseline, not a claim that BSMT would measure this mesh.
    far_before = measure(canonical_before, far_a_pos, far_b_pos)
    check("FAR measured before repair: %.9f mm surface, %.9f straight"
          % (far_before[0], far_before[1]), far_before[0] is not None)

    # The NEAR pair sits among the zero-area triangles. Its endpoints attach
    # to the non-degenerate triangles beside the defect, so it does measure -
    # and the value is worth having, because the interesting question is
    # whether repairing the region it sits in changes it.
    near_before = measure(canonical_before, near_a_pos, near_b_pos)
    check("NEAR measured before repair: %.9f mm surface, %.9f straight"
          % (near_before[0], near_before[1]), near_before[0] is not None)
    check("NEAR attached beside the defect, not onto a zero-area triangle",
          near_before[2] < 1e-6, near_before[2])

    # Real landmarks and a real measurement, so the DATA-STATE policy is
    # exercised through the product rather than simulated.
    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    for index, (name, position) in enumerate(
            (("FarA", far_a_pos), ("FarB", far_b_pos),
             ("NearA", near_a_pos), ("NearB", near_b_pos)), start=1):
        triangle, bary, _drift = attach(canonical_before, position)
        item = landmark_collection.add()
        item.stable_id = index
        item.name = name
        item.protocol_id = "P%02d" % index
        item.status = "VALID"
        point = item.surface_point
        point.triangle_index = int(triangle)
        point.barycentric = tuple(float(v) for v in bary)
        point.source_object = copy.name
        point.geometry_hash = canonical_before.geometry_hash
        point.component_id = canonical_before.component_of(triangle)
        point.status = "VALID"
        point.valid = True
        point.local_xyz = tuple(
            float(v) for v in canonical_before.local_from(triangle, bary))
        point.world_xyz = tuple(float(v) for v in canonical_before.world_from(
            triangle, bary, copy.matrix_world))

    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    for index, (src, dst) in enumerate(((1, 2), (3, 4)), start=1):
        item = measurement_collection.add()
        item.stable_id = index
        item.name = "FAR" if index == 1 else "NEAR"
        item.protocol_id = "M%02d" % index
        item.source_stable_id = src
        item.target_stable_id = dst
    props.measurement_index = 0

    # ================================================================== C ==
    print("\nC. apply the repair")
    check("Analyze Repair Issues ran",
          bpy.ops.bsmt.analyse_repair() == {'FINISHED'})
    repaired = bpy.ops.bsmt.repair_degenerate_local()
    check("the repair ran", repaired == {'FINISHED'}, repaired)

    after_vertices = vertex_array(copy)
    after_triangles = triangle_array(copy)
    after_counts = counts(copy)
    canonical_after = canonical_of(copy)
    topo_after = dict(canonical_after.topology or {})

    check("the degenerate triangles are gone",
          int(topo_after.get("degenerate_triangle_count", 0)) == 0,
          topo_after.get("degenerate_triangle_count"))
    check("no new non-manifold edges",
          int(topo_after.get("nonmanifold_edge_count", 0))
          <= int(topo_before.get("nonmanifold_edge_count", 0)), topo_after)
    check("no new boundary edges",
          int(topo_after.get("boundary_edge_count", 0))
          <= int(topo_before.get("boundary_edge_count", 0)), topo_after)
    check("no new components",
          int(topo_after.get("component_count", 0))
          <= int(topo_before.get("component_count", 0)), topo_after)
    check("and the geometry hash changed, as an edit must",
          canonical_after.geometry_hash != canonical_before.geometry_hash)

    # ================================================================== D ==
    print("\nD. SOURCE IMMUTABILITY - the scan the researcher imported")
    source_vertices_after = vertex_array(source)
    source_triangles_after = triangle_array(source)
    check("D: the source vertex COUNT is unchanged",
          counts(source)[0] == source_counts_before[0],
          (counts(source)[0], source_counts_before[0]))
    check("D: the source polygon and triangle counts are unchanged",
          counts(source) == source_counts_before,
          (counts(source), source_counts_before))
    check("D: every source vertex coordinate is bit-identical",
          source_vertices_after.shape == source_vertices_before.shape
          and np.array_equal(source_vertices_after, source_vertices_before))
    check("D: every source triangle index is identical",
          source_triangles_after.shape == source_triangles_before.shape
          and np.array_equal(source_triangles_after, source_triangles_before))
    check("D: and its geometry hash is unchanged",
          canonical_of(source).geometry_hash == source_hash_before,
          source_hash_before)
    context.view_layer.objects.active = copy

    # ================================================================== E ==
    print("\nE. LOCAL GEOMETRY CHANGE BOUND - what actually moved")
    removed_vertices = before_counts[0] - after_counts[0]
    removed_triangles = before_counts[2] - after_counts[2]

    # The repair merges vertices, so indices are renumbered and a positional
    # comparison is the only honest one: for every vertex position that
    # existed before, is it still present afterwards?
    before_set = {tuple(np.round(v, 9)) for v in before_vertices}
    after_set = {tuple(np.round(v, 9)) for v in after_vertices}
    vanished = before_set - after_set
    appeared = after_set - before_set

    check("E: NO new vertex position was invented",
          len(appeared) == 0, sorted(appeared)[:3])
    check("E: every position that vanished was a defect position",
          all(any(np.allclose(np.asarray(position),
                              before_vertices[v], atol=1e-9)
                  for v in defect_vertices) for position in vanished),
          len(vanished))

    # The decisive locality claim: outside the north cap, nothing moved.
    outside_before = np.array([v for v in before_vertices
                               if v[2] < NORTH_CAP * RADIUS])
    outside_after = np.array([v for v in after_vertices
                              if v[2] < NORTH_CAP * RADIUS])
    check("E: the number of vertices OUTSIDE the repair region is unchanged",
          outside_before.shape == outside_after.shape,
          (outside_before.shape, outside_after.shape))
    if outside_before.shape == outside_after.shape:
        order_before = np.lexsort(outside_before.T)
        order_after = np.lexsort(outside_after.T)
        worst_outside = float(np.max(np.abs(
            outside_before[order_before] - outside_after[order_after])))
        check("E: and NO vertex outside the repair region moved at all "
              "(worst delta %.3e mm)" % worst_outside,
              worst_outside == 0.0, worst_outside)

    table(("quantity", "before", "after", "change"),
          [("vertices", before_counts[0], after_counts[0],
            "-%d" % removed_vertices),
           ("polygons", before_counts[1], after_counts[1],
            "-%d" % (before_counts[1] - after_counts[1])),
           ("triangles", before_counts[2], after_counts[2],
            "-%d" % removed_triangles),
           ("degenerate", topo_before.get("degenerate_triangle_count", 0),
            topo_after.get("degenerate_triangle_count", 0), "cleared"),
           ("vertices outside region moved", 0, 0, "0")])
    print("  defect vertex indices (pre-repair numbering): %s"
          % (defect_vertices,))
    print("  vertices removed: %d   triangles removed: %d"
          % (removed_vertices, removed_triangles))

    check("E: exactly one vertex per coincident pair was removed",
          removed_vertices == len(collapsed), removed_vertices)
    check("E: and the triangles removed are the zero-area ones",
          removed_triangles == int(
              topo_before.get("degenerate_triangle_count", 0)),
          (removed_triangles,
           topo_before.get("degenerate_triangle_count")))

    # ================================================================== F ==
    print("\nF. DATA-STATE POLICY - invalidation is global, by design")
    canonical_now = geodesic.meshcache.peek_current(copy)
    for item in landmark_collection:
        state.refresh_landmark_status(item, canonical_now)
    summary = state.invalidate_for_geometry_change(context, copy.name, props)

    check("F: EVERY landmark is now non-VALID, near and far alike",
          all(item.status != landmarks.STATUS_VALID
              for item in landmark_collection),
          [item.status for item in landmark_collection])
    check("F: including the two nowhere near the repair",
          landmark_collection[0].status != landmarks.STATUS_VALID
          and landmark_collection[1].status != landmarks.STATUS_VALID)
    check("F: nothing was re-projected - each still names its own object",
          all(item.surface_point.source_object == copy.name
              for item in landmark_collection))
    check("F: and no measurement kept a number",
          all(not item.surface_valid for item in measurement_collection),
          summary)
    check("F: no cached path survived either",
          all(not state.path_is_current(item, canonical_now,
                                        copy.matrix_world, context)
              for item in measurement_collection))

    # ================================================================== G ==
    print("\nG. GEOMETRIC LOCALITY - the same positions give the same number")
    far_after = measure(canonical_after, far_a_pos, far_b_pos)
    near_after = measure(canonical_after, near_a_pos, near_b_pos)

    far_surface_delta = abs(far_after[0] - far_before[0])
    far_straight_delta = abs(far_after[1] - far_before[1])
    near_surface_delta = (abs(near_after[0] - near_before[0])
                          if near_before[0] is not None else None)

    table(("pair", "region", "surface before", "surface after", "delta mm",
           "straight before", "straight after", "re-attach drift"),
          [("FAR", "south cap",
            "%.9f" % far_before[0], "%.9f" % far_after[0],
            "%.3e" % far_surface_delta,
            "%.9f" % far_before[1], "%.9f" % far_after[1],
            "%.3e" % far_after[2]),
           ("NEAR", "north cap (repaired)",
            "refused" if near_before[0] is None else "%.9f" % near_before[0],
            "%.9f" % near_after[0],
            "n/a" if near_surface_delta is None
            else "%.3e" % near_surface_delta,
            "refused" if near_before[1] is None else "%.9f" % near_before[1],
            "%.9f" % near_after[1],
            "%.3e" % near_after[2])])

    check("G: the FAR pair re-attached with zero drift",
          far_after[2] == 0.0, far_after[2])
    check("G: the FAR straight distance is unchanged (%.3e mm)"
          % far_straight_delta,
          far_straight_delta <= RECONSTRUCTION_TOLERANCE_MM,
          far_straight_delta)
    check("G: the FAR SURFACE distance is unchanged (%.3e mm)"
          % far_surface_delta,
          far_surface_delta <= RECONSTRUCTION_TOLERANCE_MM,
          far_surface_delta)
    check("G: which is far below any scan's own accuracy",
          far_surface_delta < 1e-3, far_surface_delta)
    check("G: the NEAR pair still measures - repair did not break it",
          near_after[0] is not None and near_after[0] > 0.0, near_after[0])
    if near_surface_delta is None:
        print("  the NEAR pair had NO pre-repair value to compare against: "
              "it became measurable only because the repair ran")
    else:
        print("  the NEAR pair moved by %.3e mm, which is the repair doing "
              "its job in that region" % near_surface_delta)

    check("G: and the repaired mesh passes the solver gate",
          preprocess.preflight(topo_after)["allowed"],
          preprocess.preflight(topo_after))

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for failure in FAILURES:
        print("  FAILED: %s" % failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        print("BSMT_REPAIR_LOCALITY_RESULT=%d" % code)
    raise SystemExit(code)
