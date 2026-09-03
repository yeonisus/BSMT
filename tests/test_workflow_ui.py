"""Acceptance tests for the workflow-ordered sidebar (Milestone 3.16).

    /path/to/blender -b --factory-startup --python tests/test_workflow_ui.py

Panel ORDER can be checked offline from the class attributes, and is - see
tests/test_panel_order.py. What needs real Blender is that every panel's
``draw`` actually runs, in every scene state a researcher can reach: an empty
scene, a raw scan, an analysed scan, a measurement mesh, and each of the three
preprocessing verdicts. Draw code is ordinary Python and breaks like any
other, and Blender cannot build a UILayout in background mode - hence the
recording stand-in below.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_workflow_ui.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_workflow_ui.py")
    raise SystemExit(0)

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


class FakeLayout(object):
    """Records what a panel asks for. Enough to run draw() headless."""

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
        name = args[1] if len(args) > 1 else kwargs.get("property", "")
        self.log.append(("prop", name))

    def operator(self, idname, **kwargs):
        self.log.append(("operator", idname))
        return FakeLayout(self.log)

    def template_list(self, *args, **kwargs):
        self.log.append(("template_list", args[0] if args else ""))

    def separator(self, *args, **kwargs):
        pass

    def menu(self, *args, **kwargs):
        self.log.append(("menu", args[0] if args else ""))


def draw_panel(panel_class, context):
    """Run one panel's draw() unbound, and return what it emitted."""
    log = []
    stub = type("Stub", (object,), {
        name: staticmethod(getattr(panel_class, name))
        for name in dir(panel_class) if name.startswith("_draw")
    })()
    stub.layout = FakeLayout(log)
    panel_class.draw(stub, context)
    return log


def texts(log):
    return [value for kind, value in log if kind == "label"]


def operators(log):
    return [value for kind, value in log if kind == "operator"]


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (panels, preprocess, readiness,
                                          scancopy, state)

    context = bpy.context
    props = context.scene.bsmt

    ordered = panels.workflow_panels()
    top_level = [cls for cls in ordered if not getattr(cls, "bl_parent_id", "")]

    # ----------------------------------------------------------- order ----
    print("\nthe sidebar reads as the workflow, top to bottom")
    expected = [
        "Scan Setup",
        "Scan Preprocessing",
        "Alignment",
        "Landmark Manager",
        "Measurement Manager",
        "Measurement Visualization",
        "Results and Export",
    ]
    got = [cls.bl_label for cls in top_level]
    check("seven top-level stages, in workflow order", got == expected, got)
    check("every top-level panel declares an explicit bl_order",
          all(getattr(cls, "bl_order", None) is not None for cls in top_level),
          [(c.bl_label, getattr(c, "bl_order", None)) for c in top_level])
    check("and their bl_order values strictly increase",
          [c.bl_order for c in top_level]
          == sorted(c.bl_order for c in top_level),
          [c.bl_order for c in top_level])

    print("\n   nesting is one level deep, never more")
    by_id = {cls.bl_idname: cls for cls in ordered}
    for cls in ordered:
        parent_id = getattr(cls, "bl_parent_id", "")
        if not parent_id:
            continue
        parent = by_id.get(parent_id)
        check("%s's parent %s exists" % (cls.bl_label, parent_id),
              parent is not None, sorted(by_id))
        if parent is not None:
            check("  and %s is itself top level" % parent.bl_label,
                  not getattr(parent, "bl_parent_id", ""),
                  getattr(parent, "bl_parent_id", ""))

    print("\n   Blender agrees the classes are registered")
    for cls in ordered:
        check("%s is registered" % cls.bl_idname,
              hasattr(bpy.types, cls.__name__), cls.__name__)

    # ------------------------------------------------------ empty scene ---
    print("\nA. empty scene: every panel draws, nothing crashes")
    wipe()
    for cls in ordered:
        log = draw_panel(cls, context)
        check("%s draws" % cls.bl_label, isinstance(log, list))
    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("Scan Setup says there is no scan",
          any("No scan selected." in text for text in setup), setup)
    check("and offers Analyze Scan anyway (never hidden)",
          "bsmt.diagnose_topology"
          in operators(draw_panel(panels.BSMT_PT_scan_setup, context)))
    landmarks_text = texts(draw_panel(panels.BSMT_PT_landmarks, context))
    check("sect. 11: the Landmark Manager is still inspectable with no scan",
          bool(landmarks_text), landmarks_text)

    # --------------------------------------------------------- raw scan ---
    print("\nB. raw scan selected but not analysed")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    scan = context.object
    scan.name = "RawScan"
    context.view_layer.objects.active = scan

    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("Scan Setup names the target", any("RawScan" in t for t in setup),
          setup)
    check("and asks for an analysis first",
          any("Analyze the scan before preprocessing." in t for t in setup),
          setup)
    pre = texts(draw_panel(panels.BSMT_PT_preprocessing, context))
    check("Preprocessing says the same, in its own words",
          any("Analyze the scan first." in t for t in pre), pre)
    check("and still shows its controls rather than blanking",
          any("Selected: RawScan" in t for t in pre), pre)

    # ---------------------------------------------------- analysed scan ---
    print("\nC. analysed scan")
    state.geodesic.meshcache.get(context, scan, props.unit, rebuild=True)
    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("Scan Setup stops asking for an analysis",
          not any("Analyze the scan" in t for t in setup), setup)
    check("and reports the triangle count",
          any("Triangles:" in t for t in setup), setup)
    landmarks_text = texts(draw_panel(panels.BSMT_PT_landmarks, context))
    check("Landmarks asks for landmarks",
          any("Create or load landmarks" in t for t in landmarks_text),
          landmarks_text)
    measure_text = texts(draw_panel(panels.BSMT_PT_measurements, context))
    check("Measurements asks for landmarks first",
          any("Create landmarks first." in t for t in measure_text),
          measure_text)
    export_text = texts(draw_panel(panels.BSMT_PT_session, context))
    check("Export asks for a calculation first",
          any("Calculate measurements before exporting." in t
              for t in export_text), export_text)

    # -------------------------------------------------- measurement mesh --
    print("\nD. a measurement mesh exists")
    props.preprocess_target_triangles = 300
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing ran", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects[props.preprocess_copy_name]
    context.view_layer.objects.active = copy

    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("Scan Setup names the measurement mesh as the target",
          any(copy.name in t for t in setup), setup)
    check("and names its source", any("Source Mesh:" in t for t in setup),
          setup)
    measure_text = texts(draw_panel(panels.BSMT_PT_measurements, context))
    check("Measurement Manager says which mesh it measures on",
          any(("Measuring on: " + copy.name) in t for t in measure_text),
          measure_text)
    check("and does NOT repeat the full target block (sect. 14)",
          not any(t.startswith("Measurement Mesh: ") for t in measure_text),
          measure_text)

    # ------------------------------------------------------- verdicts -----
    print("\nE. each preprocessing verdict is shown in words")
    props.preprocess_valid = True
    props.preprocess_report = "Status: X"
    for status, shown, hint in (
        (preprocess.MEASUREMENT_READY, "MEASUREMENT READY", False),
        (preprocess.MEASUREMENT_WARNING, "WARNING", False),
        (preprocess.MEASUREMENT_NOT_READY, "NOT READY", True),
    ):
        props.preprocess_status = status
        props.preprocess_status_detail = "the stated reason"
        pre = texts(draw_panel(panels.BSMT_PT_preprocessing, context))
        check("%s is shown" % status,
              any(("Measurement Mesh: " + shown) in t for t in pre), pre)
        check("  with its reason",
              any("the stated reason" in t for t in pre))
        if hint:
            check("  and NOT READY warns about surface measurement",
                  any("Resolve critical mesh issues" in t for t in pre), pre)
        else:
            check("  and %s does not nag" % status,
                  not any("Resolve critical mesh issues" in t for t in pre))
    props.preprocess_valid = False
    props.preprocess_status = ""

    # ------------------------------------------------ landmarks defined ---
    print("\nF. prerequisite messages clear as the workflow advances")
    canonical = state.geodesic.meshcache.get(context, copy, props.unit)
    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    for index in (1, 2):
        item = landmark_collection.add()
        item.stable_id = index
        item.name = "L%d" % index
        item.protocol_id = "P%02d" % index
    text = texts(draw_panel(panels.BSMT_PT_landmarks, context))
    check("with landmarks defined but unpicked, it asks for picking",
          any("Pick each landmark" in t for t in text), text)

    for index, triangle in ((0, 4), (1, 40)):
        point = landmark_collection[index].surface_point
        point.triangle_index = triangle
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = copy.name
        point.geometry_hash = canonical.geometry_hash
        point.status = "VALID"
        point.valid = True
    text = texts(draw_panel(panels.BSMT_PT_landmarks, context))
    check("once picked, the Landmark hint goes quiet",
          not any("Pick each landmark" in t or "Create or load" in t
                  for t in text), text)

    measure_text = texts(draw_panel(panels.BSMT_PT_measurements, context))
    check("Measurements now asks for definitions, not landmarks",
          any("Define landmark pairs before calculation." in t
              for t in measure_text), measure_text)
    viz_text = texts(draw_panel(panels.BSMT_PT_measurement_visualization,
                                context))
    check("Visualization asks for a measurement to draw",
          any("Define a measurement to visualize." in t for t in viz_text),
          viz_text)

    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    definition = measurement_collection.add()
    definition.stable_id = 1
    definition.name = "M1"
    definition.protocol_id = "M01"
    definition.source_stable_id = 1
    definition.target_stable_id = 2
    measure_text = texts(draw_panel(panels.BSMT_PT_measurements, context))
    check("with a definition, the Measurement hint goes quiet",
          not any("Define landmark pairs" in t or "Create landmarks first" in t
                  for t in measure_text), measure_text)

    # --------------------------------------------- safety is not weakened -
    print("\nG. the workflow UI weakens no gate")
    check("the density threshold is untouched",
          props.dense_threshold_triangles
          == preprocess.DEFAULT_DENSE_THRESHOLD)
    check("the density guard still defaults on", props.guard_dense_solve)
    gate = preprocess.preflight({"triangle_count": 100,
                                 "nonmanifold_edge_count": 3},
                                props.dense_threshold_triangles, True)
    check("non-manifold topology is still refused a solve",
          not gate["allowed"], str(gate["refusals"]))
    panels_text = open(os.path.join(ROOT, "body_surface_measurement",
                                    "panels.py")).read()
    check("no panel disables a whole later stage",
          "layout.enabled = False" not in panels_text)
    check("the hint helper only ever draws a label",
          "def _draw_hint" in panels_text
          and "row.label(text=text, icon='INFO')" in panels_text)

    # ------------------------------------------- the 0.22.0 status defect --
    print("\nH. a degenerate-only mesh must never read as Ready")
    # The reported case: manifold, closed, one component, no non-manifold
    # edges - and degenerate triangles from vertices collapsed onto each
    # other. Scan Setup said "Topology: Ready" and the headline said READY,
    # because both carried their own rule that tested non-manifold only.
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    bad = context.object
    bad.name = "DegenerateScan"
    context.view_layer.objects.active = bad
    for index in range(1, 15):
        bad.data.vertices[index].co = bad.data.vertices[0].co
    bad.data.update()

    canonical = state.geodesic.meshcache.get(context, bad, props.unit,
                                             rebuild=True)
    report = canonical.topology
    check("the fixture reproduces the report: 1 component",
          report["component_count"] == 1, report["component_count"])
    check("  0 boundary edges", report["boundary_edge_count"] == 0,
          report["boundary_edge_count"])
    check("  0 non-manifold edges", report["nonmanifold_edge_count"] == 0,
          report["nonmanifold_edge_count"])
    check("  degenerate triangles present",
          report["degenerate_triangle_count"] > 0,
          report["degenerate_triangle_count"])
    check("  and exact coincident vertices",
          report["duplicate_vertex_count"] > 0,
          report["duplicate_vertex_count"])

    verdict = state.mesh_verdict(context, props, bad)
    check("the one authoritative verdict says NOT READY",
          verdict["state"] == preprocess.MEASUREMENT_NOT_READY,
          verdict["state"])

    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("Scan Setup does NOT say 'Topology: Ready'",
          not any(t.strip() == "Topology:         Ready" for t in setup),
          setup)
    check("it says NOT READY",
          any("Topology:" in t and "NOT READY" in t for t in setup), setup)
    check("and names the degenerate triangles",
          any("degenerate" in t for t in setup), setup)
    check("the readiness headline agrees",
          any(t.startswith("NOT READY") and "degenerate" in t for t in setup),
          setup)
    check("no line in Scan Setup claims READY",
          not any(t.startswith("READY") for t in setup), setup)

    pre = texts(draw_panel(panels.BSMT_PT_preprocessing, context))
    check("Scan Preprocessing shows the same non-zero degenerate count",
          any("Degenerate tris:" in t and "0" != t.split()[-1] for t in pre),
          pre)

    print("\n   the same mesh, repaired, reads Ready again")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    good = context.object
    good.name = "CleanScan"
    context.view_layer.objects.active = good
    state.geodesic.meshcache.get(context, good, props.unit, rebuild=True)
    verdict = state.mesh_verdict(context, props, good)
    check("a clean mesh is READY", verdict["state"]
          == preprocess.MEASUREMENT_READY, verdict)
    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("and Scan Setup says so",
          any("Topology:" in t and "MEASUREMENT READY" in t for t in setup),
          setup)

    # ------------------------------------------------------ staleness -----
    print("\nI. a stale diagnostic never leaves an old status behind")
    context.view_layer.objects.active = good
    check("the cached report is current to start with",
          state.geodesic.meshcache.is_current(good))
    for index in range(1, 15):
        good.data.vertices[index].co = good.data.vertices[0].co
    good.data.update()
    context.view_layer.update()
    check("a geometry edit drops the cached report",
          state.geodesic.meshcache.peek(good.name) is None)
    verdict = state.mesh_verdict(context, props, good)
    check("so the verdict reports NOT ANALYSED, not the old READY",
          verdict["state"] == state.MESH_NOT_ANALYSED and not verdict["analysed"],
          verdict["state"])
    setup = texts(draw_panel(panels.BSMT_PT_scan_setup, context))
    check("and Scan Setup says 'not analyzed yet' rather than Ready",
          any("not analyzed yet" in t for t in setup), setup)
    check("no stale Ready survives the edit",
          not any("Ready" in t and "Topology" in t for t in setup), setup)

    print("\n   re-analysing tells the truth about the new geometry")
    state.geodesic.meshcache.get(context, good, props.unit, rebuild=True)
    verdict = state.mesh_verdict(context, props, good)
    check("the re-analysed mesh is NOT READY",
          verdict["state"] == preprocess.MEASUREMENT_NOT_READY, verdict)

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
        print("BSMT_WORKFLOW_UI_RESULT=%d" % code)
