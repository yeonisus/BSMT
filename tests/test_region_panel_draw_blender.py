"""Surface Region UI drawing must not write to Blender ID data.

    blender -b --factory-startup --python tests/test_region_panel_draw_blender.py

THE BUG THIS EXISTS FOR
-----------------------
Blender forbids writing to ID-backed data (a Scene, and anything hanging off
one) while the interface is drawing. A panel that assigns to a PropertyGroup
field from `draw()` raises, in the real UI and only in the real UI:

    AttributeError: Writing to ID classes in this context is not allowed:
    Scene, Scene datablock, error setting BSMT_SurfaceRegion.status

and the whole panel is replaced by that message. `-b --factory-startup` never
enters a genuine draw callback, so the restriction is NOT enforced here and a
suite that merely calls `Panel.draw()` will pass over the bug forever. That is
exactly what happened: the Surface Regions panel called the *storing*
`state.refresh_region_status` from `draw()`, every offline and in-Blender
suite passed, and the failure appeared the first time a researcher opened the
sidebar.

So this suite does not rely on Blender raising. It catches the bug by its
CAUSE instead, three independent ways:

1.  SNAPSHOT - every ID-backed field of every region, plus the scene-level
    selection and counters, is recorded before the draw and compared after.
    Any persisted change, by any route, shows up as a diff.
2.  SENTINEL - a deliberately wrong-but-legal stored verdict is planted first.
    A draw that re-derives and persists silently corrects it; a pure draw
    leaves it alone.
3.  TRIPWIRE - the only two functions that write a region's verdict are
    replaced with ones that raise. A draw that reaches a persisting write
    fails loudly and names itself, rather than being caught by a diff.

The rest of the suite is the other half of the contract: state must still
change in the LEGAL places. A read-only draw is worthless if it was bought by
breaking Validate, the edit operators or invalidation propagation.

No solver is needed or wanted here - the paths are left uncomputed on purpose,
which keeps the suite fast and still exercises every structural verdict.
"""

import os
import sys

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHECKS = [0]
FAILURES = []


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s%s" % (label, ("  [%r]" % (detail,)) if detail else ""))


# ---------------------------------------------------------------------------
# a layout that records what a panel asked to draw, and nothing else
# ---------------------------------------------------------------------------

class FakeLayout(object):
    def __init__(self, log):
        self.log = log
        self.alert = False
        self.enabled = True
        self.active = True
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.alignment = 'EXPAND'

    def _child(self, *args, **kwargs):
        return FakeLayout(self.log)

    box = row = column = column_flow = split = grid_flow = _child

    def label(self, **kwargs):
        self.log.append(("label", kwargs.get("text", "")))

    def prop(self, *args, **kwargs):
        self.log.append(("prop", args[1] if len(args) > 1 else ""))

    def operator(self, idname, **kwargs):
        self.log.append(("operator", idname))
        return FakeLayout(self.log)

    def template_list(self, *args, **kwargs):
        self.log.append(("template_list", args[0] if args else ""))

    def separator(self, *args, **kwargs):
        pass

    def menu(self, *args, **kwargs):
        pass


def draw_panel(panel_class, ctx):
    """Call one panel's draw, plus the UILists it templates, as the UI would."""
    log = []
    stub = type("Stub", (object,), {
        name: staticmethod(getattr(panel_class, name))
        for name in dir(panel_class) if name.startswith("_draw")
    })()
    stub.layout = FakeLayout(log)
    panel_class.draw(stub, ctx)
    return log


def draw_uilist(uilist_class, ctx, data, items, active_property):
    """A UIList draws once per row, and is as much 'the UI' as a panel is."""
    log = []
    instance = type("Stub", (object,), {"layout_type": 'DEFAULT'})()
    for index, item in enumerate(items):
        uilist_class.draw_item(instance, ctx, FakeLayout(log), data, item,
                               'NONE', data, active_property, index, 0)
    return log


# ---------------------------------------------------------------------------
# the snapshot: every persisted field a draw could plausibly touch
# ---------------------------------------------------------------------------

REGION_FIELDS = (
    "stable_id", "protocol_id", "name", "notes",
    "status", "status_code", "status_detail", "report",
    "validated", "validated_fingerprint",
    "cached_definition", "computed_elapsed_s",
    # Milestone 3.32: the interior hangs off the same panel, so a draw-time
    # write to any of ITS fields is the same defect one level down.
    "interior_status", "interior_code", "interior_detail",
    "interior_definition", "interior_geometry_hash", "interior_side",
    "interior_full_count", "interior_partial_count", "interior_component_id",
    "show_fill", "fill_opacity",
    "boundary_closed", "boundary_length_mm", "boundary_point_count",
    "boundary_object", "boundary_geometry_hash", "boundary_component_id",
    "show_boundary", "landmark_index",
)
LANDMARK_FIELDS = ("landmark_stable_id", "landmark_protocol_id",
                   "landmark_name")
SEGMENT_FIELDS = ("from_landmark", "to_landmark", "computed", "point_count",
                  "length_mm", "object_name", "geometry_hash")


def snapshot(ctx, props):
    """Everything persistent about regions, as one comparable dict."""
    out = {}
    collection = ctx.scene.bsmt_regions
    out[("scene", "region_count")] = len(collection)
    for index, region in enumerate(collection):
        for field in REGION_FIELDS:
            out[("region", index, field)] = getattr(region, field)
        # The DEFINITION is the thing a draw must never touch, so it is
        # snapshotted entry by entry rather than only counted.
        out[("region", index, "landmark_count")] = len(region.landmarks)
        for position, entry in enumerate(region.landmarks):
            for field in LANDMARK_FIELDS:
                out[("boundary_landmark", index, position, field)] = getattr(
                    entry, field)
        out[("region", index, "segment_count")] = len(region.segments)
        for position, segment in enumerate(region.segments):
            for field in SEGMENT_FIELDS:
                out[("segment", index, position, field)] = getattr(segment,
                                                                   field)
        out[("region", index, "color")] = tuple(region.color)
    for field in ("region_index", "region_next_id",
                  "region_boundary_thickness_mm", "show_regions",
                  "landmark_index", "measurement_index"):
        out[("props", field)] = getattr(props, field)
    # Visualization is persisted state too: a draw that created or removed a
    # boundary helper would be just as illegal as one that set a status.
    out[("bpy", "objects")] = tuple(sorted(o.name for o in bpy.data.objects))
    out[("bpy", "meshes")] = len(bpy.data.meshes)
    out[("bpy", "curves")] = len(bpy.data.curves)
    return out


def diff(before, after):
    keys = set(before) | set(after)
    return sorted((key, before.get(key, "<absent>"), after.get(key, "<absent>"))
                  for key in keys if before.get(key) != after.get(key))


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, landmarks, panels, regions,
                                          state)

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------------ A --
    print("\nA. a scan, four landmarks, and one landmark-defined region")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16,
                                         radius=100.0)
    obj = context.object
    obj.name = "Scan_BSMT"
    obj.bsmt_scan.is_measurement_copy = True
    context.view_layer.objects.active = obj
    bpy.ops.bsmt.diagnose_topology()
    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=True)

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    for index, triangle in enumerate((10, 200, 400, 600), start=1):
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
    props.landmark_next_id = 5

    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    for index, (source, target) in enumerate(((1, 2), (2, 3), (3, 4), (4, 1)),
                                             start=1):
        item = measurement_collection.add()
        item.stable_id = index
        item.protocol_id = "M%02d" % index
        item.name = "P%d to P%d" % (source, target)
        item.source_stable_id = source
        item.target_stable_id = target
        item.source_name = "P%d" % source
        item.target_name = "P%d" % target
        item.measurement_type = 'SURFACE'
    props.measurement_next_id = 5

    bpy.ops.bsmt.add_region()
    region = state.active_region(context, props)
    for index in range(4):
        props.landmark_index = index
        bpy.ops.bsmt.add_region_landmark()
    check("A1: a region with four boundary LANDMARKS exists",
          region is not None and region.landmark_ids == [1, 2, 3, 4],
          None if region is None else region.landmark_ids)
    check("A2: the definition is complete; the only thing missing is the "
          "solve", region.status == regions.STATUS_DRAFT
          and region.status_code == regions.CODE_BOUNDARY_NOT_COMPUTED,
          (region.status, region.status_detail))
    check("A3: nothing was computed - this suite needs no solver",
          not region.cached_definition and len(region.segments) == 0)
    check("A4: the measurements in this scene are irrelevant to it",
          len(measurement_collection) == 4)

    # ------------------------------------------------------------------ B --
    print("\nB. SNAPSHOT + SENTINEL: a draw changes no persisted field")
    # A deliberately wrong-but-legal stored verdict. A draw that re-derives
    # and persists will quietly correct it; a pure draw leaves it exactly so.
    region.status = regions.STATUS_INVALID
    region.status_code = regions.CODE_CROSS_COMPONENT
    region.status_detail = "SENTINEL - a draw must not overwrite this"
    region.report = "SENTINEL REPORT"
    sentinel = (region.status, region.status_code, region.status_detail,
                region.report)

    before = snapshot(context, props)
    log = draw_panel(panels.BSMT_PT_surface_regions, context)
    log += draw_uilist(panels.BSMT_UL_regions, context, context.scene,
                       context.scene.bsmt_regions, "region_index")
    log += draw_uilist(panels.BSMT_UL_region_landmarks, context, region,
                       region.landmarks, "landmark_index")
    after = snapshot(context, props)
    changes = diff(before, after)

    check("B1: the panel actually drew something", len(log) > 5, len(log))
    check("B2: NOT ONE persisted field changed", not changes, changes[:6])
    check("B3: the sentinel verdict survived the draw untouched",
          (region.status, region.status_code, region.status_detail,
           region.report) == sentinel,
          (region.status, region.status_detail))
    check("B4: no helper object was created or destroyed by drawing",
          before[("bpy", "objects")] == after[("bpy", "objects")])
    check("B5: and drawing ten more times is still inert",
          all(not diff(snapshot(context, props),
                       (draw_panel(panels.BSMT_PT_surface_regions, context),
                        snapshot(context, props))[1])
              for _ in range(10)))

    # ------------------------------------------------------------------ C --
    print("\nC. TRIPWIRE: the draw path cannot even reach a persisting write")
    # The snapshot above proves nothing was written. This proves nothing TRIED
    # to be - which is the property Blender actually enforces, and it names
    # the offender instead of leaving a diff to interpret.
    tripped = []

    def forbidden(name):
        def raiser(*args, **kwargs):
            tripped.append(name)
            raise AssertionError(
                "%s was called from a panel draw. Storing a verdict is a "
                "write to ID data, which Blender refuses while the UI is "
                "drawing - use state.validate_region, which is pure." % name)
        return raiser

    originals = {
        "store_region_status": state.store_region_status,
        "refresh_region_status": state.refresh_region_status,
        "refresh_all_region_statuses": state.refresh_all_region_statuses,
        "refresh_interior_status": state.refresh_interior_status,
    }
    for name in originals:
        setattr(state, name, forbidden("state.%s" % name))
    # panels.py resolves these through the module, so patching `state` is
    # enough - asserted by the fact that B's unpatched draw took the same path.
    armed = ""
    try:
        raised = ""
        try:
            draw_panel(panels.BSMT_PT_surface_regions, context)
            draw_uilist(panels.BSMT_UL_regions, context, context.scene,
                        context.scene.bsmt_regions, "region_index")
            draw_uilist(panels.BSMT_UL_region_landmarks, context, region,
                        region.landmarks, "landmark_index")
        except Exception as exc:                      # noqa: BLE001
            raised = "%s: %s" % (type(exc).__name__, exc)
        # Whatever the draw tripped is the evidence; freeze it BEFORE the
        # arming call below adds a deliberate trip of its own.
        tripped_by_draw = list(tripped)
        # Prove the tripwire is LIVE. Without this, C1 passing would be
        # equally consistent with the patch having silently not taken, which
        # is the failure mode that makes a guard test worthless.
        try:
            state.refresh_region_status(context, region)
        except AssertionError as exc:
            armed = str(exc)
    finally:
        for name, function in originals.items():
            setattr(state, name, function)

    check("C1: no region-status writer was called during draw",
          not tripped_by_draw, tripped_by_draw)
    check("C2: and the draw completed without raising", raised == "", raised)
    check("C3: the tripwire is armed - the old draw-time call DOES trip it",
          "panel draw" in armed, armed or "the patch did not take")

    # The panel must swallow nothing: if validation itself fails, the panel
    # says so rather than silently showing a stale verdict.
    check("C4: the pure verdict function is what draw uses, and it writes "
          "nothing",
          not diff(snapshot(context, props),
                   (state.validate_region(context, region),
                    snapshot(context, props))[1]))

    # ------------------------------------------------------------------ D --
    print("\nD. the panel shows the LIVE verdict, not the stale stored one")
    texts = "\n".join(value for kind, value
                      in draw_panel(panels.BSMT_PT_surface_regions, context)
                      if kind == "label")
    # The stored REPORT is shown on purpose - it is the record of the last
    # explicit Validate, and displaying it is a read. What must not appear is
    # the stale stored VERDICT being passed off as the current one.
    check("D1: the stale stored DETAIL is not presented as the verdict",
          sentinel[2] not in texts, texts[-300:])
    check("D2: it reports what the landmarks actually say now",
          regions.STATUS_SHORT[regions.STATUS_DRAFT] in texts,
          texts[-300:])
    check("D3: and it says the stored status disagrees rather than "
          "silently fixing it",
          "Stored status is" in texts, texts[-400:])
    check("D4: saying that changed nothing either",
          region.status == regions.STATUS_INVALID, region.status)

    # ------------------------------------------------------------------ E --
    print("\nE. state still changes where it is LEGAL to change it")
    check("E1: Validate Region records the verdict",
          bpy.ops.bsmt.validate_region() == {'FINISHED'}
          and region.status == regions.STATUS_DRAFT
          and region.status_detail != sentinel[2],
          (region.status, region.status_detail))
    check("E2: and the panel no longer reports a divergence",
          "Stored status is" not in "\n".join(
              value for kind, value
              in draw_panel(panels.BSMT_PT_surface_regions, context)
              if kind == "label"))

    region.landmark_index = 1
    check("E3: reorder updates the stored definition",
          bpy.ops.bsmt.move_region_landmark(direction='DOWN') == {'FINISHED'}
          and region.landmark_ids == [1, 3, 2, 4], region.landmark_ids)
    check("E4: reorder back restores it",
          bpy.ops.bsmt.move_region_landmark(direction='UP') == {'FINISHED'}
          and region.landmark_ids == [1, 2, 3, 4], region.landmark_ids)
    region.landmark_index = 3
    check("E5: remove updates the stored verdict",
          bpy.ops.bsmt.remove_region_landmark() == {'FINISHED'}
          and region.landmark_ids == [1, 2, 3], region.landmark_ids)
    props.landmark_index = 3
    check("E6: add updates it, still without solving",
          bpy.ops.bsmt.add_region_landmark() == {'FINISHED'}
          and region.landmark_ids == [1, 2, 3, 4], region.landmark_ids)
    check("E7: and the verdict is still derived, not stuck",
          region.status == regions.STATUS_DRAFT
          and region.status_code == regions.CODE_BOUNDARY_NOT_COMPUTED,
          (region.status, region.status_code))

    # ------------------------------------------------------------------ F --
    print("\nF. invalidation propagation, and the measurement decoupling")
    props.measurement_index = 2
    before = (region.status, region.status_code, list(region.landmark_ids))
    bpy.ops.bsmt.remove_measurement()
    check("F1: DELETING A MEASUREMENT does not touch the region at all",
          (region.status, region.status_code, list(region.landmark_ids))
          == before, (region.status, region.status_code))
    check("F2: a draw still reports it without writing",
          not diff(snapshot(context, props),
                   (draw_panel(panels.BSMT_PT_surface_regions, context),
                    snapshot(context, props))[1]))

    landmark_collection = context.scene.bsmt_landmarks
    props.landmark_index = 0
    bpy.ops.bsmt.remove_landmark()
    check("F3: deleting a BOUNDARY LANDMARK does reach the region",
          region.status == regions.STATUS_INVALID
          and region.status_code == regions.CODE_MISSING_LANDMARK,
          (region.status, region.status_code))
    check("F4: and it happened outside any draw - the panel was not the "
          "thing that noticed", len(region.landmarks) == 4,
          len(region.landmarks))
    check("F5: the lost reference is kept and named",
          int(region.landmarks[0].landmark_stable_id) == 1
          and region.landmarks[0].landmark_name,
          region.landmarks[0].landmark_name)

    # ------------------------------------------------------------------ G --
    print("\nG. no solver, and no other BSMT panel writes region data")
    constructed = [0]
    exact = geodesic.registry.exact_mmp
    original = getattr(exact, "PyGeodesicAlgorithmExact", None)
    if original is not None:
        class Counting(original):
            def __init__(self, *args, **kwargs):
                constructed[0] += 1
                super().__init__(*args, **kwargs)
        exact.PyGeodesicAlgorithmExact = Counting

    before = snapshot(context, props)
    drawn = 0
    failures = []
    for panel_class in panels.classes:
        if not (isinstance(panel_class, type)
                and issubclass(panel_class, bpy.types.Panel)):
            continue
        try:
            draw_panel(panel_class, context)
            drawn += 1
        except Exception as exc:                      # noqa: BLE001
            failures.append((panel_class.__name__,
                             "%s: %s" % (type(exc).__name__, exc)))
    after = snapshot(context, props)
    if original is not None:
        exact.PyGeodesicAlgorithmExact = original

    check("G1: every BSMT panel drew", drawn > 8, drawn)
    check("G2: none of them raised", not failures, failures[:3])
    check("G3: and NOT ONE of them changed region state",
          not diff(before, after), diff(before, after)[:6])
    check("G4: the geodesic solver was never constructed by a draw",
          constructed[0] == 0, constructed[0])

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        print("BSMT_REGION_DRAW_RESULT=%d" % code)
    sys.exit(code)
