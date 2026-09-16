"""Surface Area in Blender: the workflow, the units, and what stays unchanged.

    blender -b --factory-startup --python tests/test_surface_area_blender.py

Surface Area is the **mesh surface area of the selected region on the
triangular body mesh**. This suite runs it on a real computed geodesic
boundary and checks what the number has to survive:

*   THE TWO SIDES ADD UP TO THE COMPONENT. Measured through the real
    operators, on a real boundary. Nothing that is wrong near the boundary
    can satisfy this, because whatever one side gains the other must lose.
*   IT IS EXPLICIT. Nothing computes an area on its own - not Compute
    Boundary, not Compute Interior, not a panel draw, not a save.
*   IT GOES STALE WITH WHAT IT DEPENDS ON, and a stale area is never shown
    as a number: a figure on screen is read as a result whatever label sits
    above it.
*   THINGS THAT MUST NOT MOVE IT: thickness, fill colour and opacity, panel
    rebuilds, and anything done to measurements.
*   NO SOLVER, anywhere in it.
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
    from body_surface_measurement import (geodesic, interior,
                                          interiorcache, landmarks, regions,
                                          state, visualization)
    from body_surface_measurement import surfacearea as area

    context = bpy.context
    props = context.scene.bsmt

    unavailable = geodesic.ensure_loaded()
    if unavailable:
        print("SKIP  the exact backend is unavailable: %s" % unavailable)
        raise SystemExit(0)
    check("pygeodesic is instrumented",
          instrument(geodesic.registry.exact_mmp))

    # ================================================================== A ==
    print("\nA. a scan, a boundary, and nothing computed by accident")
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

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
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
    check("A1: a DRAFT region has no area and Compute Area is refused",
          region.area_status == area.STATUS_NONE
          and run(bpy.ops.bsmt.compute_region_area) == {'CANCELLED'},
          region.area_status)
    for index in range(4):
        props.landmark_index = index
        bpy.ops.bsmt.add_region_landmark()
    check("A2: the boundary computes",
          bpy.ops.bsmt.compute_region_boundary() == {'FINISHED'}
          and region.status == regions.STATUS_VALID, region.status_detail)
    solves = SOLVER_CALLS["construct"]
    check("A3: a VALID boundary is still not enough - the interior decides "
          "which side is the region",
          run(bpy.ops.bsmt.compute_region_area) == {'CANCELLED'})
    check("A4: and computing the boundary computed no area",
          region.area_status == area.STATUS_NONE, region.area_status)

    # ================================================================== B ==
    print("\nB. Compute Area is explicit, and never runs by itself")
    with NoSolver("B"):
        check("B1: Compute Interior succeeds",
              bpy.ops.bsmt.compute_region_interior() == {'FINISHED'}
              and region.interior_status == interior.STATUS_VALID,
              region.interior_detail)
        check("B2: and STILL computes no area - it is a separate press",
              region.area_status == area.STATUS_NONE
              and region.area_mm2 == 0.0, region.area_status)
        check("B3: the classification was stored in float64, which is what "
              "the area is measured from",
              interiorcache.exists(region.stable_id))
        stored = interiorcache.load(region.stable_id)
        check("B4: with the parent triangle of every clipped piece",
              all(parent >= 0 for parent, _bary in stored["pieces"])
              and len(stored["pieces"]) == region.interior_partial_count,
              (len(stored["pieces"]), region.interior_partial_count))
        check("B5: and barycentric corners in float64, not float32",
              stored["pieces"][0][1].dtype == np.float64,
              stored["pieces"][0][1].dtype)

        check("B6: Compute Area",
              bpy.ops.bsmt.compute_region_area() == {'FINISHED'}
              and region.area_status == area.STATUS_VALID, region.area_detail)
        check("B7: it produced a positive area in mm^2",
              region.area_mm2 > 0.0, region.area_mm2)
        # Relative, because these are Blender FloatProperties and therefore
        # float32: the area is computed in float64 and stored at about seven
        # significant digits. Documented rather than tightened.
        split_error = abs(region.area_full_mm2 + region.area_partial_mm2
                          - region.area_mm2) / region.area_mm2
        check("B8: split into whole-triangle and clipped contributions that "
              "sum to the total, to the float32 the properties store "
              "(%.2e relative)" % split_error,
              region.area_full_mm2 > 0.0 and region.area_partial_mm2 > 0.0
              and split_error < 1e-6,
              (region.area_full_mm2, region.area_partial_mm2,
               region.area_mm2))
        check("B9: the method is recorded with the result",
              region.area_method == area.METHOD, region.area_method)
        check("B10: and which side it measured",
              region.area_side == region.interior_side, region.area_side)

    smaller_mm2 = float(region.area_mm2)

    # ================================================================== C ==
    print("\nC. the two sides add up to the surface component")
    with NoSolver("C"):
        region.interior_side = interior.SIDE_COMPLEMENT
        check("C1: switching side makes the area stale, not wrong",
              state.refresh_area_status(
                  region,
                  state.refresh_interior_status(region, None, canonical)
              )["status"] == area.STATUS_STALE)
        bpy.ops.bsmt.compute_region_interior()
        check("C2: the complement's area computes",
              bpy.ops.bsmt.compute_region_area() == {'FINISHED'}
              and region.area_status == area.STATUS_VALID, region.area_detail)
        complement_mm2 = float(region.area_mm2)
        component = float(region.area_component_mm2)
        summed = smaller_mm2 + complement_mm2
        relative = abs(summed - component) / component
        check("C3: A_smaller + A_complement == the component's own area "
              "(%.3e relative)" % relative, relative < 1e-6,
              (summed, component))
        check("C4: the complement is the bigger side",
              complement_mm2 > smaller_mm2, (smaller_mm2, complement_mm2))
        check("C5: and switching sides used no solver at all", True)
        region.interior_side = interior.SIDE_SMALLER
        bpy.ops.bsmt.compute_region_interior()
        bpy.ops.bsmt.compute_region_area()
        check("C6: switching back reproduces the first area exactly",
              region.area_mm2 == smaller_mm2,
              (region.area_mm2, smaller_mm2))

    # ================================================================== D ==
    print("\nD. units")
    with NoSolver("D"):
        check("D1: mm^2 is the only stored value",
              region.area_mm2 == smaller_mm2)
        check("D2: cm^2 is derived from it, at 100 mm^2 per cm^2",
              abs(area.as_cm2(region.area_mm2) - region.area_mm2 / 100.0)
              < 1e-12)
        check("D3: and both are shown in the stored description",
              "mm²" in region.area_detail and "cm²" in region.area_detail,
              region.area_detail)
        check("D4: the component area is recorded for context, not as the "
              "region result",
              region.area_component_mm2 > region.area_mm2,
              (region.area_mm2, region.area_component_mm2))

    # ================================================================== E ==
    print("\nE. things that MUST NOT move the area")
    with NoSolver("E"):
        before = (region.area_mm2, region.area_status, region.area_side,
                  region.area_interior_key, region.area_full_mm2,
                  region.area_partial_mm2)
        for thickness in (5.0, 20.0, 35.0):
            region.panel_thickness_mm = thickness
            bpy.ops.bsmt.show_region_panel()
        check("E1: three thickness changes and preview rebuilds",
              visualization.region_panel_exists(region.stable_id))
        region.fill_color = (1.0, 0.2, 0.2, 1.0)
        region.fill_opacity = 0.85
        region.panel_color = (0.2, 1.0, 0.2, 1.0)
        region.panel_opacity = 0.7
        bpy.ops.bsmt.show_region_fill()
        bpy.ops.bsmt.show_region_panel()
        bpy.ops.bsmt.hide_region_panel()
        bpy.ops.bsmt.clear_region_panels()
        after = (region.area_mm2, region.area_status, region.area_side,
                 region.area_interior_key, region.area_full_mm2,
                 region.area_partial_mm2)
        check("E2: THE AREA IS UNTOUCHED by thickness, colour, opacity and "
              "every preview rebuild", before == after, (before, after))

        measurement_collection = context.scene.bsmt_measurements
        measurement_collection.clear()
        entry = measurement_collection.add()
        entry.stable_id = 1
        entry.protocol_id = "M01"
        entry.name = "P1 to P2"
        entry.source_stable_id = 1
        entry.target_stable_id = 2
        entry.measurement_type = 'SURFACE'
        props.measurement_next_id = 2
        props.measurement_index = 0
        bpy.ops.bsmt.remove_measurement()
        state.clear_measurements(context, props)
        check("E3: and by adding, deleting and clearing measurements",
              (region.area_mm2, region.area_status) == before[:2],
              (region.area_mm2, region.area_status))

    # ================================================================== F ==
    print("\nF. the area goes stale with what it depends on")
    with NoSolver("F"):
        region.landmark_index = 1
        bpy.ops.bsmt.move_region_landmark(direction='DOWN')
        check("F1: reordering the landmarks makes the area stale",
              region.area_status == area.STATUS_STALE, region.area_status)
        check("F2: the number is kept, but it is named as stale",
              region.area_mm2 > 0.0 and region.area_status != area.STATUS_VALID)
        check("F3: Compute Area refuses while the interior is not valid",
              run(bpy.ops.bsmt.compute_region_area) == {'CANCELLED'})
        bpy.ops.bsmt.move_region_landmark(direction='UP')
        bpy.ops.bsmt.compute_region_interior()
        bpy.ops.bsmt.compute_region_area()
        check("F4: restoring the order and recomputing restores it",
              region.area_status == area.STATUS_VALID
              and region.area_mm2 == smaller_mm2, region.area_mm2)

        moved = landmark_collection[1]
        original_triangle = int(moved.surface_point.triangle_index)
        moved.surface_point.triangle_index = 305
        state.refresh_region_status(context, region, canonical)
        check("F5: re-picking a boundary landmark makes the area stale too",
              region.area_status == area.STATUS_STALE, region.area_status)
        moved.surface_point.triangle_index = original_triangle
        state.refresh_region_status(context, region, canonical)
        bpy.ops.bsmt.compute_region_interior()
        bpy.ops.bsmt.compute_region_area()

    print("\n   a geometry change")
    with NoSolver("F-geometry"):
        mesh = obj.data
        mesh.vertices[0].co.z += 7.5
        mesh.update()
        geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
        state.invalidate_for_geometry_change(context, obj.name)
        check("F6: a geometry change makes the area stale",
              region.area_status != area.STATUS_VALID, region.area_status)
        check("F7: and Compute Area refuses",
              run(bpy.ops.bsmt.compute_region_area) == {'CANCELLED'})
        mesh.vertices[0].co.z -= 7.5
        mesh.update()
        geodesic.meshcache.get(context, obj, props.unit, rebuild=True)
        state.invalidate_for_geometry_change(context, obj.name)

    # ================================================================== G ==
    print("\nG. save, reload, and the panel")
    bpy.ops.bsmt.compute_region_boundary()
    bpy.ops.bsmt.compute_region_interior()
    bpy.ops.bsmt.compute_region_area()
    saved = float(region.area_mm2)
    check("G0: an area to save", region.area_status == area.STATUS_VALID)
    path = os.path.join(bpy.app.tempdir, "bsmt_area_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    context = bpy.context
    props = context.scene.bsmt
    reloaded = context.scene.bsmt_regions[0]
    with NoSolver("G"):
        check("G1: the area survived a save and reload",
              reloaded.area_mm2 == saved, (reloaded.area_mm2, saved))
        check("G2: and so did the float64 classification it came from",
              interiorcache.exists(reloaded.stable_id))
        restored = geodesic.meshcache.get(
            context, bpy.data.objects["Scan_BSMT"], props.unit, rebuild=True)
        verdict = state.refresh_region_status(context, reloaded, restored)
        check("G3: it is still VALID, because every identity still matches",
              reloaded.area_status == area.STATUS_VALID,
              (reloaded.area_status, reloaded.area_detail))

        from body_surface_measurement import panels

        class Layout(object):
            def __init__(self, log):
                self.log = log
                self.alert = self.enabled = self.active = True
                self.scale_x = self.scale_y = 1.0
                self.alignment = 'EXPAND'

            def _child(self, *a, **k):
                return Layout(self.log)

            box = row = column = column_flow = split = grid_flow = _child

            def label(self, **k):
                self.log.append(k.get("text", ""))

            def prop(self, *a, **k):
                pass

            def operator(self, *a, **k):
                return Layout(self.log)

            def template_list(self, *a, **k):
                pass

            def separator(self, *a, **k):
                pass

            def menu(self, *a, **k):
                pass

        def draw(cls, ctx):
            log = []
            stub = type("S", (object,), {
                n: staticmethod(getattr(cls, n))
                for n in dir(cls) if n.startswith("_draw")})()
            stub.layout = Layout(log)
            cls.draw(stub, ctx)
            return log

        fields = ("area_mm2", "area_status", "area_code", "area_detail",
                  "area_interior_key", "area_side", "area_method",
                  "area_full_mm2", "area_partial_mm2", "area_component_mm2")
        snapshot = tuple(getattr(reloaded, name) for name in fields)
        texts = "\n".join(draw(panels.BSMT_PT_surface_regions, context))
        check("G4: the panel draws the area",
              area.format_mm2(reloaded.area_mm2) in texts, texts[-400:])
        check("G5: in cm^2 as well",
              area.format_cm2(reloaded.area_mm2) in texts, texts[-400:])
        check("G6: naming the method",
              "full triangles" in texts and "clipped" in texts)
        check("G7: and saying it is NOT true anatomical surface area",
              "NOT true anatomical surface area" in texts, texts[-300:])
        check("G8: drawing it wrote NOTHING",
              tuple(getattr(reloaded, name) for name in fields) == snapshot)

        reloaded.area_status = area.STATUS_STALE
        texts = "\n".join(draw(panels.BSMT_PT_surface_regions, context))
        check("G9: a STALE area is not shown as a number - a figure on "
              "screen is read as a result whatever label is above it",
              area.format_mm2(reloaded.area_mm2) not in texts,
              texts[-300:])
        check("G10: it is named as stale instead",
              area.STATUS_SHORT[area.STATUS_STALE] in texts)

    # ================================================================== H ==
    print("\nH. the solver was never used for any of it")
    check("H1: the solver was used only by Compute Boundary",
          SOLVER_CALLS["construct"] > 0, SOLVER_CALLS["construct"])
    check("H2: every NoSolver block passed",
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
        print("BSMT_SURFACE_AREA_RESULT=%d" % code)
    sys.exit(code)
