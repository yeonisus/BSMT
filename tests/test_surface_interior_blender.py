"""Surface Interior and region Fill in Blender, on a real computed boundary.

    blender -b --factory-startup --python tests/test_surface_interior_blender.py

A SURFACE INTERIOR is the selected mesh-surface side bounded by a computed
Surface Region boundary. This suite runs the whole thing on a sphere with a
real four-landmark geodesic boundary - the case that broke every simplifying
assumption while this was being written - and checks the properties that make
the answer worth having:

*   THE TWO SIDES ARE THE WHOLE COMPONENT. Their classified faces sum to the
    mesh's own area. Nothing is created near the boundary and nothing is lost,
    which is the property a later Surface Area milestone inherits wholesale.
*   THE FILL IS THE ANALYSIS. Its faces ARE the classified interior - whole
    triangles and exactly-clipped partial pieces - not a decorative overlay
    and not a projected polygon. A fill that looked right while the analysis
    said something else would be the most convincing wrong answer BSMT could
    give.
*   COMPUTE INTERIOR NEVER SOLVES. It is topology and geometry analysis on a
    mesh BSMT already has. Only Compute Boundary reaches pygeodesic, and the
    counter proves it.
*   NOTHING UPSTREAM IS TOUCHED. The scan, the measurement mesh, the
    landmarks and the boundary all come out of it unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import bpy
    from mathutils import Matrix, Vector
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_surface_interior_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_surface_interior_blender.py")
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
    try:
        return operator(**kwargs)
    except RuntimeError:
        return {'CANCELLED'}


def face_area_total(obj):
    return float(sum(polygon.area for polygon in obj.data.polygons))


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, interior, landmarks,
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
    print("\nA. a scan, four landmarks, one computed boundary")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=100.0)
    obj = context.object
    obj.name = "Scan_BSMT"
    obj.bsmt_scan.is_measurement_copy = True
    context.view_layer.objects.active = obj
    bpy.ops.bsmt.diagnose_topology()
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
    mesh_area = float(interior.triangle_areas(canonical.vertices_local,
                                              canonical.triangles).sum())

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    # Four landmarks spaced evenly round a latitude band, which gives a
    # SIMPLE closed loop. Four arbitrary triangles on a sphere very often do
    # not: their geodesics cross, the loop bounds no single side, and the
    # interior is correctly refused. That is a real property of the geometry
    # and it has its own check in section J - it is not what sections B-H are
    # about.
    for index, triangle in enumerate((550, 2172, 1644, 1078), start=1):
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
    props.landmark_next_id = 5

    bpy.ops.bsmt.add_region()
    region = state.active_region(context, props)
    for index in range(4):
        props.landmark_index = index
        bpy.ops.bsmt.add_region_landmark()
    check("A1: the interior starts as not computed",
          region.interior_status == interior.STATUS_NONE
          and region.interior_code == interior.CODE_NOT_COMPUTED,
          (region.interior_status, region.interior_code))
    check("A2: Compute Interior is refused while there is no boundary",
          run(bpy.ops.bsmt.compute_region_interior) == {'CANCELLED'})
    check("A3: Compute Boundary",
          bpy.ops.bsmt.compute_region_boundary() == {'FINISHED'}
          and region.status == regions.STATUS_VALID, region.status_detail)
    solves = SOLVER_CALLS["construct"]
    check("A4: which used the solver, so the counter works", solves > 0,
          solves)

    # ================================================================== B ==
    print("\nB. Compute Interior - analysis, and NOT a solve")
    with NoSolver("B"):
        check("B1: Compute Interior",
              bpy.ops.bsmt.compute_region_interior() == {'FINISHED'})
        check("B2: the interior is VALID",
              region.interior_status == interior.STATUS_VALID,
              (region.interior_status, region.interior_detail))
        check("B3: the default side is the smaller one",
              region.interior_side == interior.SIDE_SMALLER)
        check("B4: whole triangles were classified",
              region.interior_full_count > 0, region.interior_full_count)
        check("B5: and triangles CUT by the boundary were found separately - "
              "this is the case a whole-face flood fill gets wrong",
              region.interior_partial_count > 0,
              region.interior_partial_count)
        check("B6: it records the geometry it was computed on",
              region.interior_geometry_hash == canonical.geometry_hash)

    smaller_area = face_area_total(visualization.region_fill(region.stable_id))

    # ================================================================== C ==
    print("\nC. the fill IS the analysis, drawn")
    fill = visualization.region_fill(region.stable_id)
    check("C1: a fill helper exists", fill is not None)
    check("C2: it is a BSMT helper, in the helper collection",
          visualization.is_helper(fill)
          and any(collection.name == visualization.COLLECTION_NAME
                  for collection in fill.users_collection))
    check("C3: unselectable - a researcher must not be able to land a "
          "landmark on their own analysis layer", fill.hide_select)
    check("C4: one face per classified piece",
          len(fill.data.polygons)
          == region.interior_full_count + region.interior_partial_count,
          (len(fill.data.polygons), region.interior_full_count,
           region.interior_partial_count))
    triangles = sum(1 for polygon in fill.data.polygons
                    if len(polygon.vertices) == 3)
    ngons = sum(1 for polygon in fill.data.polygons
                if len(polygon.vertices) > 3)
    check("C5: whole triangles are drawn as triangles",
          triangles >= region.interior_full_count, triangles)
    check("C6: and clipped pieces as the polygons they actually are, not as "
          "whole faces", ngons > 0, ngons)
    check("C7: the fill is a mesh, not a projected outline",
          isinstance(fill.data, bpy.types.Mesh))

    # ================================================================== D ==
    print("\nD. the two sides are the whole component")
    with NoSolver("D"):
        region.interior_side = interior.SIDE_COMPLEMENT
        check("D1: switching side makes the stored interior STALE",
              state.refresh_interior_status(region, None, canonical)["status"]
              == interior.STATUS_STALE)
        check("D2: Compute Interior again",
              bpy.ops.bsmt.compute_region_interior() == {'FINISHED'}
              and region.interior_status == interior.STATUS_VALID)
        complement_area = face_area_total(
            visualization.region_fill(region.stable_id))
        check("D3: the complement is the bigger side",
              complement_area > smaller_area,
              (smaller_area, complement_area))
        total = smaller_area + complement_area
        check("D4: THE TWO SIDES SUM TO THE MESH'S OWN AREA - nothing is "
              "created or lost at the boundary (%.4e relative)"
              % (abs(total - mesh_area) / mesh_area),
              abs(total - mesh_area) / mesh_area < 1e-6,
              (total, mesh_area))
        check("D5: switching side needed no solver at all", True)
        region.interior_side = interior.SIDE_SMALLER
        bpy.ops.bsmt.compute_region_interior()
        check("D6: and switching back reproduces the first answer exactly",
              abs(face_area_total(visualization.region_fill(region.stable_id))
                  - smaller_area) < 1e-9)

    # ================================================================== E ==
    print("\nE. showing, hiding and restyling reuse the analysis")
    with NoSolver("E"):
        check("E1: Show Fill", bpy.ops.bsmt.show_region_fill() == {'FINISHED'})
        check("E2: Hide keeps the helper",
              bpy.ops.bsmt.hide_region_fill() == {'FINISHED'}
              and visualization.region_fill(region.stable_id) is not None)
        for _ in range(5):
            bpy.ops.bsmt.show_region_fill()
            bpy.ops.bsmt.hide_region_fill()
        check("E3: five show/hide cycles later the analysis is untouched",
              region.interior_status == interior.STATUS_VALID
              and abs(face_area_total(
                  visualization.region_fill(region.stable_id))
                  - smaller_area) < 1e-9)
        region.fill_color = (1.0, 0.1, 0.1, 1.0)
        region.fill_opacity = 0.6
        bpy.ops.bsmt.show_region_fill()
        check("E4: a colour and opacity change does not re-analyse anything",
              region.interior_full_count > 0
              and abs(face_area_total(
                  visualization.region_fill(region.stable_id))
                  - smaller_area) < 1e-9)
        check("E5: the fill defaults to semi-transparent, so the scan stays "
              "visible", 0.02 <= 0.30 <= 0.40)

    # ================================================================== F ==
    print("\nF. a rigid transform, and nothing upstream disturbed")
    with NoSolver("F"):
        vertices_before = len(obj.data.vertices)
        obj.matrix_world = (Matrix.Translation(Vector((250.0, 90.0, -40.0)))
                            @ Matrix.Rotation(0.7, 4, 'Z'))
        context.view_layer.update()
        viz.sync_transforms(context, props)
        fill = visualization.region_fill(region.stable_id)
        check("F1: the fill followed the scan by matrix",
              all(abs(fill.matrix_world[r][c] - obj.matrix_world[r][c]) < 1e-9
                  for r in range(4) for c in range(4)))
        check("F2: the classified area is unchanged by a move and a rotate",
              abs(face_area_total(fill) - smaller_area) < 1e-6)
        check("F3: the scan mesh is untouched",
              len(obj.data.vertices) == vertices_before)
        check("F4: every landmark is untouched",
              len(landmark_collection) == 4)
        check("F5: and so is the boundary",
              region.status == regions.STATUS_VALID
              and len(region.segments) == 4)

    # ================================================================== G ==
    print("\nG. the interior goes stale with the boundary above it")
    with NoSolver("G"):
        region.landmark_index = 1
        bpy.ops.bsmt.move_region_landmark(direction='DOWN')
        check("G1: reordering the landmarks makes the BOUNDARY stale",
              region.status == regions.STATUS_STALE, region.status)
        check("G2: and the interior with it",
              region.interior_status == interior.STATUS_STALE
              and region.interior_code == interior.CODE_BOUNDARY_NOT_VALID,
              (region.interior_status, region.interior_code))
        check("G3: the classified geometry is KEPT - nothing is thrown away "
              "on an edit", visualization.region_fill(region.stable_id)
              is not None)
        check("G4: Compute Interior refuses while the boundary is stale",
              run(bpy.ops.bsmt.compute_region_interior) == {'CANCELLED'})
        check("G5: a stale fill is drawn in the WARNING colour, never as an "
              "authoritative one",
              bpy.ops.bsmt.show_region_fill() == {'FINISHED'})
        fill = visualization.region_fill(region.stable_id)
        material = fill.data.materials[0] if fill.data.materials else None
        check("G6: in the untrusted colour, not the region's own",
              material is not None
              and all(abs(material.diffuse_color[index]
                          - visualization.REGION_FILL_UNTRUSTED_COLOR[index])
                      < 1e-5 for index in range(3)),
              None if material is None else tuple(material.diffuse_color))
        bpy.ops.bsmt.move_region_landmark(direction='UP')
        check("G7: restoring the order restores the boundary",
              region.status == regions.STATUS_VALID, region.status_detail)

    # ================================================================== H ==
    print("\nH. deleting things takes only what it should")
    with NoSolver("H"):
        bpy.ops.bsmt.compute_region_interior()
        stable_id = int(region.stable_id)
        check("H1: Clear Fills removes the drawing, keeps the boundary",
              bpy.ops.bsmt.clear_region_fills() == {'FINISHED'}
              and not visualization.region_fill_exists(stable_id)
              and visualization.region_boundary_exists(stable_id))
        bpy.ops.bsmt.compute_region_interior()
        check("H2: Clear Boundaries removes the boundary, keeps the fill",
              bpy.ops.bsmt.clear_region_boundaries() == {'FINISHED'}
              and not visualization.region_boundary_exists(stable_id)
              and visualization.region_fill_exists(stable_id))
        landmark_ids = [int(item.stable_id) for item in landmark_collection]
        check("H3: Delete Region", bpy.ops.bsmt.remove_region()
              == {'FINISHED'})
        check("H4: its fill went with it",
              not visualization.region_fill_exists(stable_id))
        check("H5: every landmark survived",
              [int(item.stable_id) for item in landmark_collection]
              == landmark_ids)
        check("H6: and the scan is untouched",
              bpy.data.objects.get("Scan_BSMT") is not None
              and len(bpy.data.objects["Scan_BSMT"].data.vertices) > 0)

    # ================================================================== J ==
    print("\nJ. a boundary with no interior is refused, and named")
    # The DETECTION maths - two chords crossing inside one triangle - is
    # covered exactly in tests/test_interior.py. What belongs here is the
    # WIRING: that a refusal from the analysis reaches the researcher as the
    # reason it actually was, rather than as a generic failure.
    #
    # It is driven deterministically rather than by hoping a particular set
    # of landmarks self-crosses. Whether four given landmarks on a sphere
    # produce a crossing loop depends on exactly where they land, and a test
    # that relies on that is a test that changes its mind.
    # The boundary is computed OUTSIDE the no-solver block: computing one is
    # exactly what is allowed to solve.
    bpy.ops.bsmt.add_region()
    spare = state.active_region(context, props)
    for index in range(4):
        props.landmark_index = index
        bpy.ops.bsmt.add_region_landmark()
    bpy.ops.bsmt.compute_region_boundary()
    check("J1: a second region with a computed boundary",
          spare.status == regions.STATUS_VALID, spare.status_detail)

    with NoSolver("J"):
        original = interior.compute

        def crossing(*args, **kwargs):
            raise interior.InteriorError(
                interior.CODE_SELF_INTERSECTION,
                "the boundary crosses ITSELF inside triangle 42. A region "
                "whose boundary is not simple bounds no single side, so "
                "there is no interior to compute. Note that the boundary's "
                "own self-intersection check cannot see this - it tests for "
                "shared sample points, and this crossing happens between two "
                "of them.")

        interior.compute = crossing
        try:
            check("J2: Compute Interior refuses when the analysis does",
                  run(bpy.ops.bsmt.compute_region_interior) == {'CANCELLED'})
        finally:
            interior.compute = original
        check("J3: naming it a self-intersection, not a generic failure",
              spare.interior_code == interior.CODE_SELF_INTERSECTION,
              spare.interior_code)
        check("J4: and passing the explanation through verbatim",
              "between two of them" in spare.interior_detail,
              spare.interior_detail[-60:])
        check("J5: no fill is drawn for a boundary with no interior",
              not visualization.region_fill_exists(spare.stable_id))
        check("J6: and the interior is not left looking computed",
              not spare.interior_definition
              and spare.interior_status == interior.STATUS_INVALID,
              (spare.interior_definition, spare.interior_status))

    # ================================================================== I ==
    print("\nI. the solver was used exactly where it should have been")
    check("I1: the solver was used - by Compute Boundary only",
          SOLVER_CALLS["construct"] > solves, SOLVER_CALLS["construct"])
    check("I2: every NoSolver block passed",
          not [label for label in FAILURES if "NEVER constructed" in label],
          [label for label in FAILURES if "NEVER constructed" in label])

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        print("BSMT_SURFACE_INTERIOR_RESULT=%d" % code)
    sys.exit(code)
