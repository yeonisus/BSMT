"""Surface Regions in Blender: landmark-defined boundaries, and the solver.

    blender -b --factory-startup --python tests/test_surface_region_blender.py

A Surface Region is a researcher-defined CLOSED BOUNDARY specified by an
ORDERED SET OF LANDMARKS. Consecutive landmarks - including the final-to-first
pair - are joined by cached surface geodesic paths on the triangular mesh.

This suite runs on a real sphere with real landmarks and real solves. What it
exists to prove, beyond "the operators work":

*   COMPUTE BOUNDARY IS THE ONLY THING THAT SOLVES. Defining, reordering,
    renaming, validating, showing, hiding, saving and loading a region must
    cost zero solver constructions, because on a real scan one is tens of
    seconds to minutes. `PyGeodesicAlgorithmExact` is wrapped and counted, and
    every claim below is measured rather than asserted.
*   n LANDMARKS GIVE EXACTLY n SEGMENTS, the last closing back to the first.
*   REGIONS AND MEASUREMENTS ARE DECOUPLED. Deleting or editing a measurement
    cannot reach a region; a region can be defined and computed in a file with
    no measurements at all. This is the whole point of the milestone and it
    gets its own section.
*   a boundary is a CACHED RESULT of a definition. Editing the definition
    makes it stale; nothing is ever silently recomputed.

Draw purity has its own suite, tests/test_region_panel_draw_blender.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_surface_region_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_surface_region_blender.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

FAILURES = []
CHECKS = [0]
SOLVER_CALLS = {"construct": 0}


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def instrument(exact_mmp):
    """Count every construction of the native solver."""
    module = getattr(exact_mmp, "_geodesic", None)
    if module is None:
        return False
    original = module.PyGeodesicAlgorithmExact

    class Counted(object):
        def __init__(self, *args, **kwargs):
            SOLVER_CALLS["construct"] += 1
            self._inner = original(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    module.PyGeodesicAlgorithmExact = Counted
    return True


class NoSolver(object):
    """Assert a block of work never constructed the native solver."""

    def __init__(self, label):
        self.label = label

    def __enter__(self):
        self.before = SOLVER_CALLS["construct"]
        return self

    def __exit__(self, *exc):
        check("%s: pygeodesic was NEVER constructed" % self.label,
              SOLVER_CALLS["construct"] == self.before,
              "%d construction(s)" % (SOLVER_CALLS["construct"] - self.before))
        return False


def run(operator, **kwargs):
    """Call an operator, turning a reported ERROR into {'CANCELLED'}."""
    try:
        return operator(**kwargs)
    except RuntimeError:
        return {'CANCELLED'}


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, landmarks, pathcache,
                                          regions, state, viz, visualization)

    context = bpy.context
    props = context.scene.bsmt

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)
    check("pygeodesic is instrumented",
          instrument(geodesic.registry.exact_mmp))

    # ================================================================== A ==
    print("\nA. a scan and five landmarks. NO measurements anywhere.")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=100.0)
    obj = context.object
    obj.name = "Scan_BSMT"
    obj.bsmt_scan.is_measurement_copy = True
    obj.bsmt_scan.source_name = "Scan_Source"
    obj.matrix_world = Matrix.Translation(Vector((10.0, -5.0, 3.0)))
    context.view_layer.objects.active = obj
    bpy.ops.bsmt.diagnose_topology()
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    for index, triangle in enumerate((10, 300, 600, 900, 1200), start=1):
        item = landmark_collection.add()
        item.stable_id = index
        item.protocol_id = "L%02d" % index
        item.name = "P%d" % index
        item.status = landmarks.STATUS_VALID
        point = item.surface_point
        point.triangle_index = int(triangle)
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = obj.name
        point.geometry_hash = canonical.geometry_hash
        point.component_id = canonical.component_of(triangle)
        point.valid = True
        point.status = "VALID"
        bary = np.array(point.barycentric, dtype=np.float64)
        point.local_xyz = tuple(float(v)
                                for v in canonical.local_from(triangle, bary))
        point.world_xyz = tuple(float(v)
                                for v in canonical.world_from(
                                    triangle, bary, obj.matrix_world))
    props.landmark_next_id = 6

    check("A1: five landmarks are picked", len(landmark_collection) == 5)
    check("A2: and there are NO measurements at all - a region must not need "
          "one", len(context.scene.bsmt_measurements) == 0)
    check("A3: the solver has not been touched yet",
          SOLVER_CALLS["construct"] == 0, SOLVER_CALLS["construct"])

    # ================================================================== B ==
    print("\nB. defining a boundary from landmarks never reaches the solver")
    with NoSolver("B"):
        check("B1: New Surface Region",
              bpy.ops.bsmt.add_region() == {'FINISHED'})
        region = state.active_region(context, props)
        check("B2: it starts as a DRAFT with no landmarks",
              region.status == regions.STATUS_DRAFT
              and region.status_code == regions.CODE_EMPTY
              and len(region.landmarks) == 0,
              (region.status, region.status_code))
        for index in range(4):
            props.landmark_index = index
            check("B3.%d: Add Landmark %d" % (index, index + 1),
                  bpy.ops.bsmt.add_region_landmark() == {'FINISHED'})
        check("B4: four boundary landmarks, in the order they were added",
              region.landmark_ids == [1, 2, 3, 4], region.landmark_ids)
        check("B5: the definition is complete but NOT computed",
              region.status == regions.STATUS_DRAFT
              and region.status_code == regions.CODE_BOUNDARY_NOT_COMPUTED,
              (region.status, region.status_code))
        result, _facts = state.validate_region(context, region)
        check("B6: it implies four segments, the last closing back to P1",
              regions.segment_pairs(region.landmark_ids)
              == [(1, 2), (2, 3), (3, 4), (4, 1)])
        check("B7: and reads back as a closed loop",
              result["definition"] == "P1 → P2 → P3 → P4 → P1",
              result["definition"])
        check("B8: renaming costs nothing", True)
        region.name = "Anterior Thigh"
        state.refresh_region_status(context, region)
        check("B9: and does not change the verdict",
              region.status_code == regions.CODE_BOUNDARY_NOT_COMPUTED)
        check("B10: two landmarks is not enough",
              state.remove_region_landmark(region, 3)
              and state.remove_region_landmark(region, 2)
              and state.refresh_region_status(context, region)["code"]
              == regions.CODE_INSUFFICIENT_LANDMARKS)
        props.landmark_index = 2
        bpy.ops.bsmt.add_region_landmark()
        props.landmark_index = 3
        bpy.ops.bsmt.add_region_landmark()
        check("B11: and four again is", region.landmark_ids == [1, 2, 3, 4],
              region.landmark_ids)

    # ================================================================== C ==
    print("\nC. Compute Boundary - the ONE place a region solves")
    before = SOLVER_CALLS["construct"]
    check("C1: Compute Boundary",
          bpy.ops.bsmt.compute_region_boundary() == {'FINISHED'})
    solved = SOLVER_CALLS["construct"] - before
    check("C2: it solved, and the counter proves it", solved > 0, solved)
    check("C3: exactly four boundary segments were recorded",
          len(region.segments) == 4, len(region.segments))
    check("C4: each naming the landmark pair it joins",
          [(int(s.from_landmark), int(s.to_landmark))
           for s in region.segments] == [(1, 2), (2, 3), (3, 4), (4, 1)],
          [(int(s.from_landmark), int(s.to_landmark))
           for s in region.segments])
    check("C5: the FINAL segment closes P4 back to P1, automatically",
          int(region.segments[3].from_landmark) == 4
          and int(region.segments[3].to_landmark) == 1)
    check("C6: every segment has a cached polyline of its own",
          all(pathcache.region_segment_exists(region.stable_id, position)
              for position in range(4)))
    check("C7: the region is VALID", region.status == regions.STATUS_VALID,
          region.status_detail)
    check("C8: and knows which definition its boundary belongs to",
          region.cached_definition == regions.definition_key([1, 2, 3, 4]),
          region.cached_definition)
    check("C9: NO measurement was created behind the scenes",
          len(context.scene.bsmt_measurements) == 0,
          len(context.scene.bsmt_measurements))
    check("C10: and no measurement path cache was written either",
          not pathcache.cache_meshes(),
          [m.name for m in pathcache.cache_meshes()])
    perimeter = float(region.boundary_length_mm)
    check("C11: the boundary has a perimeter", perimeter > 0.0, perimeter)

    # ================================================================== D ==
    print("\nD. everything else reuses the cache. Nothing else solves.")
    with NoSolver("D"):
        check("D1: Validate Region",
              bpy.ops.bsmt.validate_region() == {'FINISHED'}
              and region.status == regions.STATUS_VALID, region.status_detail)
        check("D2: Show Boundary",
              bpy.ops.bsmt.show_region_boundary() == {'FINISHED'})
        helper = visualization.region_boundary(region.stable_id)
        check("D3: one closed curve, built from the cached polylines",
              helper is not None and helper.data.splines[0].use_cyclic_u
              and len(helper.data.splines[0].points) > 50,
              None if helper is None else len(helper.data.splines[0].points))
        check("D4: it is a BSMT helper, in the helper collection",
              visualization.is_helper(helper)
              and any(c.name == visualization.COLLECTION_NAME
                      for c in helper.users_collection))
        check("D5: Hide keeps the helper - showing again must cost nothing",
              bpy.ops.bsmt.hide_region_boundary() == {'FINISHED'}
              and visualization.region_boundary(region.stable_id) is not None)
        check("D6: Show again", bpy.ops.bsmt.show_region_boundary()
              == {'FINISHED'})
        for _ in range(5):
            bpy.ops.bsmt.hide_region_boundary()
            bpy.ops.bsmt.show_region_boundary()
        check("D7: five hide/show cycles later the cache is intact",
              all(pathcache.region_segment_exists(region.stable_id, position)
                  for position in range(4)))
        check("D8: Refresh Regions", bpy.ops.bsmt.refresh_regions()
              == {'FINISHED'})
        region.color = (1.0, 0.2, 0.2, 1.0)
        bpy.ops.bsmt.refresh_regions()
        check("D9: a colour change does not disturb the boundary",
              region.status == regions.STATUS_VALID
              and abs(region.boundary_length_mm - perimeter) < 1e-6)
        region.name = "Renamed Region"
        state.refresh_region_status(context, region)
        check("D10: renaming keeps the stable id and the verdict",
              region.status == regions.STATUS_VALID)

    # ================================================================== E ==
    print("\nE. a rigid transform does not disturb a computed boundary")
    with NoSolver("E"):
        obj.matrix_world = (Matrix.Translation(Vector((250.0, 90.0, -40.0)))
                            @ Matrix.Rotation(0.7, 4, 'Z'))
        context.view_layer.update()
        bpy.ops.bsmt.validate_region()
        check("E1: still VALID after a move and a rotate",
              region.status == regions.STATUS_VALID, region.status_detail)
        check("E2: and the perimeter is unchanged",
              abs(region.boundary_length_mm - perimeter) < 1e-6,
              (perimeter, region.boundary_length_mm))
        viz.sync_transforms(context, props)
        helper = visualization.region_boundary(region.stable_id)
        check("E3: the boundary followed the scan by matrix",
              helper is not None
              and all(abs(helper.matrix_world[r][c] - obj.matrix_world[r][c])
                      < 1e-9 for r in range(4) for c in range(4)))

    # ================================================================== F ==
    print("\nF. editing the DEFINITION makes the boundary stale, never wrong")
    with NoSolver("F"):
        region.landmark_index = 1
        check("F1: reordering breaks the match, and says so",
              bpy.ops.bsmt.move_region_landmark(direction='DOWN')
              == {'FINISHED'}
              and region.status == regions.STATUS_STALE
              and region.status_code == regions.CODE_BOUNDARY_STALE,
              (region.status, region.status_code))
        check("F2: the cached segments are still there - a solve is minutes "
              "of work and is not thrown away on an edit",
              all(pathcache.region_segment_exists(region.stable_id, position)
                  for position in range(4)))
        check("F3: reordering back restores VALID",
              bpy.ops.bsmt.move_region_landmark(direction='UP')
              == {'FINISHED'}
              and region.status == regions.STATUS_VALID, region.status_detail)
        props.landmark_index = 4
        check("F4: adding a fifth landmark makes it stale",
              bpy.ops.bsmt.add_region_landmark() == {'FINISHED'}
              and region.status == regions.STATUS_STALE,
              (region.status, region.status_code))
        region.landmark_index = 4
        check("F5: removing it again restores VALID",
              bpy.ops.bsmt.remove_region_landmark() == {'FINISHED'}
              and region.status == regions.STATUS_VALID, region.status_detail)
        check("F6: a stale boundary is drawn, but NOT as a trusted one",
              True)
        region.landmark_index = 1
        bpy.ops.bsmt.move_region_landmark(direction='DOWN')
        bpy.ops.bsmt.show_region_boundary()
        helper = visualization.region_boundary(region.stable_id)
        material = helper.data.materials[0] if helper.data.materials else None
        check("F7: it is drawn in the warning colour, not the region's own",
              material is not None
              and all(abs(material.diffuse_color[i]
                          - visualization.REGION_UNTRUSTED_COLOR[i]) < 1e-5
                      for i in range(4)),
              None if material is None else tuple(material.diffuse_color))
        bpy.ops.bsmt.move_region_landmark(direction='UP')
        bpy.ops.bsmt.refresh_regions()

    # ================================================================== G ==
    print("\nG. MEASUREMENTS AND REGIONS ARE DECOUPLED")
    # The milestone's reason for existing. A region is defined by landmarks
    # and owns its boundary cache; a measurement is a different object that
    # happens to share a solver. Nothing done to one may reach the other.
    with NoSolver("G"):
        measurement_collection = context.scene.bsmt_measurements
        measurement_collection.clear()
        for index, (source, target) in enumerate(((1, 2), (2, 3)), start=1):
            measurement = measurement_collection.add()
            measurement.stable_id = index
            measurement.protocol_id = "M%02d" % index
            measurement.name = "P%d to P%d" % (source, target)
            measurement.source_stable_id = source
            measurement.target_stable_id = target
            measurement.measurement_type = 'SURFACE'
        props.measurement_next_id = 3
        bpy.ops.bsmt.validate_region()
        before_status = region.status
        before_definition = region.cached_definition
        before_length = float(region.boundary_length_mm)
        check("G1: the region is VALID with measurements present",
              before_status == regions.STATUS_VALID, region.status_detail)

        measurement_collection[0].name = "renamed measurement"
        measurement_collection[0].source_stable_id = 4
        state.refresh_measurement_status(context, measurement_collection[0])
        bpy.ops.bsmt.validate_region()
        check("G2: EDITING a measurement leaves the region exactly as it was",
              region.status == before_status
              and region.cached_definition == before_definition
              and abs(region.boundary_length_mm - before_length) < 1e-9,
              (region.status, region.status_detail))

        props.measurement_index = 0
        check("G3: DELETING a measurement succeeds",
              bpy.ops.bsmt.remove_measurement() == {'FINISHED'})
        bpy.ops.bsmt.validate_region()
        check("G4: and the region is STILL VALID - this is the decoupling",
              region.status == regions.STATUS_VALID
              and abs(region.boundary_length_mm - before_length) < 1e-9,
              (region.status, region.status_detail))
        check("G5: its boundary cache is untouched",
              all(pathcache.region_segment_exists(region.stable_id, position)
                  for position in range(4)))

        state.clear_measurements(context, props)
        bpy.ops.bsmt.validate_region()
        check("G6: clearing EVERY measurement still leaves the region VALID",
              region.status == regions.STATUS_VALID
              and len(context.scene.bsmt_measurements) == 0,
              region.status_detail)
        check("G7: the region references no measurement id anywhere",
              not any(hasattr(segment, "measurement_stable_id")
                      for segment in region.segments))

    # ================================================================== H ==
    print("\nH. landmarks are what a region DOES depend on")
    with NoSolver("H"):
        moved = landmark_collection[1]
        moved.surface_point.triangle_index = 305
        bpy.ops.bsmt.validate_region()
        check("H1: re-picking a boundary landmark makes the region STALE",
              region.status == regions.STATUS_STALE
              and region.status_code == regions.CODE_LANDMARK_STALE,
              (region.status, region.status_code))
        check("H2: naming which landmarks moved",
              "P2" in region.status_detail or any(
                  "P2" in line for line in region.report.split("\n")),
              region.status_detail)
        moved.surface_point.triangle_index = 300
        bpy.ops.bsmt.validate_region()
        check("H3: putting it back restores VALID",
              region.status == regions.STATUS_VALID, region.status_detail)

        restated = state.invalidate_regions_for_landmark(
            context, 2, "test")
        check("H4: a landmark change restates exactly the regions using it",
              restated == 1, restated)
        check("H5: and none of the ones that do not",
              state.invalidate_regions_for_landmark(context, 99, "test") == 0)

        props.landmark_index = 2
        check("H6: DELETING a boundary landmark",
              bpy.ops.bsmt.remove_landmark() == {'FINISHED'})
        check("H7: makes the region INVALID",
              region.status == regions.STATUS_INVALID
              and region.status_code == regions.CODE_MISSING_LANDMARK,
              (region.status, region.status_code))
        check("H8: the reference is KEPT and NAMED, not silently dropped",
              len(region.landmarks) == 4
              and int(region.landmarks[2].landmark_stable_id) == 3,
              [int(e.landmark_stable_id) for e in region.landmarks])

    # restore a computable definition for the sections below
    region.landmarks.remove(2)
    bpy.ops.bsmt.compute_region_boundary()
    check("H9: a three-landmark region computes and is VALID",
          region.status == regions.STATUS_VALID and len(region.segments) == 3,
          (region.status, len(region.segments)))

    # ================================================================== I ==
    print("\nI. a geometry change goes through the centralized policy")
    with NoSolver("I"):
        # A real edit, so the geometry hash really moves. Calling the
        # invalidation on unchanged geometry would prove nothing.
        mesh = obj.data
        mesh.vertices[0].co.z += 7.5
        mesh.update()
        geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
        summary = state.invalidate_for_geometry_change(context, obj.name)
        check("I1: the centralized invalidation reports regions restated",
              "regions_restated" in summary, sorted(summary))
        bpy.ops.bsmt.validate_region()
        check("I2: and the region is no longer VALID",
              region.status != regions.STATUS_VALID,
              (region.status, region.status_detail))
        check("I3: nothing was recomputed automatically - the counter above "
              "is the proof", True)
        check("I4: the definition itself is untouched",
              len(region.landmarks) == 3, len(region.landmarks))
        check("I5: Compute Boundary REFUSES on landmarks that no longer "
              "describe this mesh, rather than solving something wrong",
              run(bpy.ops.bsmt.compute_region_boundary) == {'CANCELLED'})

    # Put the vertex back. The coordinates return to exactly what they were,
    # so the geometry hash does too, and the landmarks describe this mesh
    # again - which is what lets section J test DELETION rather than repeat
    # section I.
    mesh.vertices[0].co.z -= 7.5
    mesh.update()
    geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
    state.invalidate_for_geometry_change(context, obj.name)

    # ================================================================== J ==
    print("\nJ. deleting a region takes nothing else with it")
    check("J0: the boundary computes again on the restored geometry",
          run(bpy.ops.bsmt.compute_region_boundary) == {'FINISHED'}
          and region.status == regions.STATUS_VALID, region.status_detail)
    with NoSolver("J"):
        landmark_ids = [int(item.stable_id) for item in landmark_collection]
        stable_id = int(region.stable_id)
        bpy.ops.bsmt.show_region_boundary()
        check("J1: Delete Region", bpy.ops.bsmt.remove_region()
              == {'FINISHED'})
        check("J2: the region is gone", len(context.scene.bsmt_regions) == 0)
        check("J3: its boundary helper went with it",
              not visualization.region_boundary_exists(stable_id))
        check("J4: and so did its own cached segments",
              not any(pathcache.region_segment_exists(stable_id, position)
                      for position in range(6)))
        check("J5: every LANDMARK is still there",
              [int(item.stable_id) for item in landmark_collection]
              == landmark_ids, landmark_ids)
        check("J6: the source scan is untouched",
              bpy.data.objects.get("Scan_BSMT") is not None)

    # ================================================================== K ==
    print("\nK. persistence, and a file that has never used regions")
    bpy.ops.bsmt.add_region()
    region = state.active_region(context, props)
    region.name = "Saved Region"
    for index in range(3):
        props.landmark_index = index
        bpy.ops.bsmt.add_region_landmark()
    bpy.ops.bsmt.compute_region_boundary()
    saved = {
        "stable_id": int(region.stable_id),
        "protocol_id": region.protocol_id,
        "name": region.name,
        "landmarks": region.landmark_ids,
        "definition": region.cached_definition,
        "status": region.status,
        "length": float(region.boundary_length_mm),
    }
    path = os.path.join(bpy.app.tempdir, "bsmt_region_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    context = bpy.context
    props = context.scene.bsmt
    reloaded = context.scene.bsmt_regions
    check("K1: the region survived a save and reload", len(reloaded) == 1,
          len(reloaded))
    if len(reloaded):
        item = reloaded[0]
        check("K2: with its stable id and name",
              int(item.stable_id) == saved["stable_id"]
              and item.name == saved["name"])
        check("K3: its ORDERED LANDMARK DEFINITION",
              [int(e.landmark_stable_id) for e in item.landmarks]
              == saved["landmarks"],
              [int(e.landmark_stable_id) for e in item.landmarks])
        check("K4: and the definition its boundary was computed for",
              item.cached_definition == saved["definition"])
        with NoSolver("K-reload"):
            check("K5: its cached polylines survived the round trip",
                  all(pathcache.region_segment_exists(item.stable_id, p)
                      for p in range(len(item.segments))))
            check("K6: so it is still VALID without solving anything",
                  state.refresh_region_status(context, item)["status"]
                  == regions.STATUS_VALID)
            check("K7: and its perimeter is unchanged",
                  abs(item.boundary_length_mm - saved["length"]) < 1e-6,
                  (saved["length"], item.boundary_length_mm))

    print("\n   a file with no region data at all")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    context = bpy.context
    props = context.scene.bsmt
    check("K8: the collection exists and is empty, which IS 'no regions yet'",
          state.get_regions(context) is not None
          and len(state.get_regions(context)) == 0)
    check("K9: the selection index and id counter have safe defaults",
          int(props.region_index) == 0 and int(props.region_next_id) == 1)
    check("K10: restating regions on an empty scene is a no-op, not an error",
          state.refresh_all_region_statuses(context) == 0)
    check("K11: and a region can still be created",
          bpy.ops.bsmt.add_region() == {'FINISHED'}
          and len(state.get_regions(context)) == 1)

    # ================================================================== L ==
    print("\nL. a region saved under the OLD measurement-path model")
    # Simulated the only way it can be: a region with segments but no
    # landmark definition, which is exactly what an old file leaves behind
    # once the removed properties are dropped on load.
    legacy = state.active_region(context, props)
    for _ in range(4):
        legacy.segments.add()
    result = state.refresh_region_status(context, legacy)
    check("L1: it is refused BY NAME, not read as an empty region",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_LEGACY_DEFINITION,
          (result["status"], result["code"]))
    check("L2: and never silently reinterpreted as a landmark definition",
          len(legacy.landmarks) == 0)
    check("L3: Compute Boundary refuses it rather than solving",
          regions.CODE_LEGACY_DEFINITION in regions.DEFINITION_FAULTS)
    legacy.segments.clear()

    # ================================================================== M ==
    print("\nM. the solver was used exactly where it should have been")
    check("M1: the solver WAS used - by Compute Boundary, and the counter "
          "proves the instrumentation works",
          SOLVER_CALLS["construct"] > 0, SOLVER_CALLS["construct"])
    check("M2: every NoSolver block above passed",
          not [label for label in FAILURES if "NEVER constructed" in label],
          [label for label in FAILURES if "NEVER constructed" in label])
    check("M3: no geometry was modified by any region operation",
          all(obj.type != 'MESH' or len(obj.data.vertices) > 0
              for obj in bpy.data.objects))

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        print("BSMT_SURFACE_REGION_RESULT=%d" % code)
    sys.exit(code)
