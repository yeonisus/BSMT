"""The Scan Preprocessing panel always says something (Milestone 3.26).

    /path/to/blender -b --factory-startup --python \\
        tests/test_preprocess_panel_blender.py

Reported: on a real 1.07M-triangle source scan whose topology reports three
non-manifold edges, the expanded Scan Preprocessing panel rendered a
completely empty body - no controls, and no sentence saying why.

Two properties are checked here, and they are different questions:

1. **The stage is available on a NOT READY source.** Preprocessing is the
   step that PRODUCES a measurement mesh, so a source scan's own defects -
   non-manifold edges, boundary edges, several components, degenerate
   triangles, coincident vertices, density - must never remove its controls.
   The solver gate that refuses to MEASURE on such a mesh is separate and is
   asserted here to be untouched.

2. **An expanded panel is never blank.** Blender renders whatever a draw()
   emitted BEFORE it raised, so a fault in the lines that read the scene -
   all of which run ahead of the first widget - renders as an empty body
   under an open disclosure arrow. Section E makes the panel's fact-gathering
   fail on purpose and requires a stated reason on screen.

Section E is the one that would have caught the reported symptom. The rest of
the file is the policy it was reported against.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bmesh
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_preprocess_panel_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_preprocess_panel_blender.py")
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


def props_drawn(log):
    return [value for kind, value in log if kind == "prop"]


def body(log):
    """Everything on screen, as one lowercase string."""
    return " ".join(str(value) for _kind, value in log).lower()


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def activate(obj):
    for other in bpy.context.selected_objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def clean_scan(name="CleanScan", segments=48, rings=24):
    """A closed, manifold, defect-free source scan."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name + "_Mesh"
    return obj


def defective_scan(name="M02Like"):
    """A source scan with the reported defect profile.

    Three non-manifold edges (a third face on an existing edge, three times),
    boundary edges from the fins, and three connected components. Nothing is
    degenerate and no two vertices coincide, which is what the reported scan
    also showed - so the only blocking defect is the non-manifold one.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    for index in (10, 400, 900):
        bm.edges.ensure_lookup_table()
        edge = bm.edges[index]
        a, b = edge.verts
        tip = bm.verts.new((a.co + b.co) * 0.75)
        bm.faces.new((a, b, tip))
    bm.verts.ensure_lookup_table()
    for offset in ((5.0, 0.0, 0.0), (-5.0, 0.0, 0.0)):
        island = bmesh.new()
        bmesh.ops.create_cube(island, size=0.5)
        bmesh.ops.triangulate(island, faces=island.faces[:])
        for vert in island.verts:
            vert.co.x += offset[0]
            vert.co.y += offset[1]
        scratch = bpy.data.meshes.new("scratch")
        island.to_mesh(scratch)
        island.free()
        bm.from_mesh(scratch)
        bpy.data.meshes.remove(scratch)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (panels, preprocess, scancopy, state)

    context = bpy.context
    props = context.scene.bsmt
    panel = panels.BSMT_PT_preprocessing

    # ------------------------------------------------------------- A -------
    print("\nA. a READY source mesh: the controls are there")
    wipe()
    clean = activate(clean_scan())
    bpy.ops.bsmt.diagnose_topology()
    verdict = state.mesh_verdict(context, props, clean)
    check("A1: the clean scan is analysed", verdict["analysed"])
    check("A2: and its verdict is READY",
          verdict["state"] == preprocess.MEASUREMENT_READY,
          "%s %s" % (verdict["state"], verdict["reasons"]))

    log = draw_panel(panel, context)
    check("A3: the panel drew something at all", len(log) > 0, len(log))
    check("A4: the target preset is offered",
          "preprocess_preset" in props_drawn(log), props_drawn(log))
    check("A5: the target triangle count is offered",
          "preprocess_target_triangles" in props_drawn(log), props_drawn(log))
    check("A6: Create Measurement Mesh is offered",
          "bsmt.create_measurement_copy" in operators(log), operators(log))
    check("A7: the scan is identified by name",
          any("cleanscan" in line.lower() for line in texts(log)), texts(log))
    check("A8: and the operator agrees it can run",
          bpy.ops.bsmt.create_measurement_copy.poll())

    # ------------------------------------------------------------- B -------
    print("\nB. a NOT READY source mesh: the same controls are still there")
    wipe()
    broken = activate(defective_scan())
    bpy.ops.bsmt.diagnose_topology()
    verdict = state.mesh_verdict(context, props, broken)
    report = verdict["report"]
    check("B1: the defective scan is analysed", verdict["analysed"])
    check("B2: it carries non-manifold edges",
          int(report.get("nonmanifold_edge_count", 0)) > 0,
          report.get("nonmanifold_edge_count"))
    check("B3: it carries boundary edges",
          int(report.get("boundary_edge_count", 0)) > 0,
          report.get("boundary_edge_count"))
    check("B4: it has several connected components",
          int(report.get("component_count", 0)) > 1,
          report.get("component_count"))
    check("B5: and the mesh verdict is NOT READY",
          verdict["state"] == preprocess.MEASUREMENT_NOT_READY,
          verdict["state"])

    log = draw_panel(panel, context)
    check("B6: the panel body is not empty", len(log) > 0, len(log))
    check("B7: the target preset is STILL offered",
          "preprocess_preset" in props_drawn(log), props_drawn(log))
    check("B8: the target triangle count is STILL offered",
          "preprocess_target_triangles" in props_drawn(log), props_drawn(log))
    check("B9: Create Measurement Mesh is STILL offered",
          "bsmt.create_measurement_copy" in operators(log), operators(log))
    check("B10: nothing on the source blocks creation",
          scancopy.creation_block(broken, props) is None,
          scancopy.creation_block(broken, props))
    check("B11: and the operator polls True on a NOT READY source",
          bpy.ops.bsmt.create_measurement_copy.poll())
    check("B12: the panel reports the defects rather than hiding them",
          "non-manifold" in body(log), texts(log))

    # The gate that DOES refuse, and must keep refusing.
    gate = preprocess.preflight(report,
                                guard_dense=props.guard_dense_solve,
                                dense_threshold=props.dense_threshold_triangles)
    check("B13: the solver gate still refuses this mesh", not gate["allowed"],
          gate["refusals"])
    check("B14: and refuses it for the non-manifold edges",
          'NON_MANIFOLD' in gate["blocking_codes"], gate["blocking_codes"])

    # ------------------------------------------------------------- C -------
    print("\nC. nothing usable selected: the panel explains, never blanks")
    wipe()
    log = draw_panel(panel, context)
    check("C1: with an empty scene the body is not empty", len(log) > 0)
    check("C2: and it says there is no active object",
          "no active object" in body(log), texts(log))
    check("C3: and says what to do about it",
          "select" in body(log), texts(log))
    check("C4: the operator is unavailable",
          not bpy.ops.bsmt.create_measurement_copy.poll())

    bpy.ops.object.camera_add()
    camera = activate(bpy.context.object)
    log = draw_panel(panel, context)
    check("C5: with a camera active the body is not empty", len(log) > 0)
    check("C6: and it names the camera as the problem",
          camera.name.lower() in body(log), texts(log))
    check("C7: and says it is not a mesh", "not a mesh" in body(log), texts(log))
    check("C8: the operator is unavailable for a camera",
          not bpy.ops.bsmt.create_measurement_copy.poll())

    # ------------------------------------------------------------- D -------
    print("\nD. a measurement mesh already exists: its state, not a blank")
    wipe()
    source = activate(clean_scan(name="SourceScan"))
    props.preprocess_target_triangles = 600
    result = bpy.ops.bsmt.create_measurement_copy()
    check("D1: preprocessing finished", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects.get(props.preprocess_copy_name)
    check("D2: the generated mesh exists", copy is not None,
          props.preprocess_copy_name)
    check("D3: the source is untouched",
          source.bsmt_scan.is_measurement_copy is False
          and len(source.data.polygons) > len(copy.data.polygons),
          "%d vs %d" % (len(source.data.polygons), len(copy.data.polygons)))
    check("D4: the generated mesh is the active object",
          context.view_layer.objects.active is copy,
          getattr(context.view_layer.objects.active, "name", None))
    check("D5: and it is selected", copy.select_get())

    log = draw_panel(panel, context)
    check("D6: the panel body is not empty", len(log) > 0)
    check("D7: it says this IS a measurement mesh",
          "this is a measurement mesh" in body(log), texts(log))
    check("D8: it names the source it came from",
          source.name.lower() in body(log), texts(log))
    check("D9: it says why another copy would be refused",
          "already a measurement mesh" in body(log), texts(log))
    check("D10: the panel still shows the preprocessing verdict",
          "measurement mesh:" in body(log), texts(log))
    check("D11: and the preprocessing report is on screen",
          "preprocessing report" in body(log), texts(log))

    activate(source)
    log = draw_panel(panel, context)
    check("D12: back on the source, creation is offered again",
          "bsmt.create_measurement_copy" in operators(log)
          and scancopy.creation_block(source, props) is None)

    # ------------------------------------------------------------- E -------
    print("\nE. the invariant: an expanded panel is never an unexplained blank")
    wipe()
    scan = activate(defective_scan(name="FaultScan"))
    bpy.ops.bsmt.diagnose_topology()

    original_describe = scancopy.describe

    def exploding_describe(obj):
        raise RuntimeError("deliberate fault reading the scan")

    scancopy.describe = exploding_describe
    try:
        log = draw_panel(panel, context)
    finally:
        scancopy.describe = original_describe

    check("E1: the draw did not propagate the fault", isinstance(log, list))
    check("E2: the body is NOT empty", len(log) > 0, len(log))
    check("E3: it says the panel could not be drawn",
          "could not be drawn" in body(log), texts(log))
    check("E4: it names the failure",
          "deliberate fault reading the scan" in body(log), texts(log))
    check("E5: it points at the system console for the traceback",
          "console" in body(log), texts(log))
    check("E6: and it keeps the action that moves the workflow forward",
          "bsmt.create_measurement_copy" in operators(log), operators(log))

    original_hint = state.stage_hint

    def exploding_hint(*args, **kwargs):
        raise RuntimeError("deliberate fault building the stage hint")

    state.stage_hint = exploding_hint
    try:
        log = draw_panel(panel, context)
    finally:
        state.stage_hint = original_hint
    check("E7: a fault in the FIRST thing the body does is also survivable",
          len(log) > 0 and "could not be drawn" in body(log), texts(log))

    check("E8: with the faults removed the panel draws normally again",
          "preprocess_preset" in props_drawn(draw_panel(panel, context)))

    # ------------------------------------------------------------- F -------
    print("\nF. one policy: what the panel says is what the operator does")
    wipe()
    cases = []
    cases.append(("empty scene", None))
    bpy.ops.object.camera_add()
    cases.append(("camera", bpy.context.object))
    cases.append(("clean mesh", clean_scan(name="PollClean")))
    cases.append(("defective mesh", defective_scan(name="PollBroken")))
    for label, obj in cases:
        if obj is None:
            for other in bpy.context.selected_objects:
                other.select_set(False)
            context.view_layer.objects.active = None
        else:
            activate(obj)
        block = scancopy.creation_block(context.active_object, props)
        expected = block is None or not block["blocks_poll"]
        check("F: poll agrees with the stated reason (%s)" % label,
              bpy.ops.bsmt.create_measurement_copy.poll() == expected,
              "%s vs %s" % (bpy.ops.bsmt.create_measurement_copy.poll(),
                            block))
        log = draw_panel(panel, context)
        check("F: the panel is never blank (%s)" % label, len(log) > 0, label)
        if block is not None:
            check("F: and the panel prints that reason (%s)" % label,
                  block["reason"].lower() in body(log),
                  "%s / %s" % (block["reason"], texts(log)))

    # A helper object is a BSMT artefact, not a scan - and says so.
    helper = clean_scan(name="BSMT_helper_probe")
    helper[getattr(__import__(
        "body_surface_measurement.visualization",
        fromlist=["HELPER_FLAG"]), "HELPER_FLAG")] = True
    activate(helper)
    block = scancopy.creation_block(helper, props)
    check("F: a BSMT helper is refused",
          block is not None and block["code"] == scancopy.BLOCK_HELPER, block)
    check("F: the operator refuses it too",
          not bpy.ops.bsmt.create_measurement_copy.poll())
    check("F: and the panel says which object and why",
          "helper" in body(draw_panel(panel, context)))

    # ------------------------------------------------------------- G -------
    print("\nG. text that cannot be drawn is replaced, never raised")
    # Exactly what the reported scan's .mtl produces: a legacy-codepage name
    # that reaches Python as an unpaired surrogate.
    raw = "tex\udcb1.jpg"
    try:
        raw.encode("utf-8")
        encodable = True
    except UnicodeEncodeError:
        encodable = False
    check("G1: the raw scan text really cannot be encoded", not encodable)
    safe = panels._safe_text(raw)
    check("G2: _safe_text returns something Blender can draw",
          safe.encode("utf-8") == b"tex?.jpg", repr(safe))
    check("G3: ordinary text is untouched",
          panels._safe_text("Materials: scan_mat") == "Materials: scan_mat")

    # ------------------------------------------------------------- H -------
    print("\nH. which materials a mesh actually uses, read the fast way")
    wipe()
    multi = activate(clean_scan(name="MultiMat"))
    for name in ("mat_used_a", "mat_unused", "mat_used_b"):
        multi.data.materials.append(bpy.data.materials.new(name))
    faces = multi.data.polygons
    check("H1: the fixture has three slots and some faces",
          len(multi.material_slots) == 3 and len(faces) > 6, len(faces))
    for index, face in enumerate(faces):
        face.material_index = 0 if index % 2 else 2      # slot 1 goes unused
    used = scancopy.used_material_names(multi)
    check("H2: only the slots faces reference are reported",
          used == ["mat_used_a", "mat_used_b"], used)

    for face in faces:
        face.material_index = 1
    check("H3: it follows the faces, not the slot list",
          scancopy.used_material_names(multi) == ["mat_unused"],
          scancopy.used_material_names(multi))

    single = activate(clean_scan(name="SingleMat"))
    single.data.materials.append(bpy.data.materials.new("only_mat"))
    check("H4: a mesh with one slot and no explicit indices reports it",
          scancopy.used_material_names(single) == ["only_mat"],
          scancopy.used_material_names(single))

    bare = activate(clean_scan(name="NoMat"))
    check("H5: a mesh with no material reports nothing",
          scancopy.used_material_names(bare) == [],
          scancopy.used_material_names(bare))

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
        print("BSMT_PREPROCESS_PANEL_RESULT=%d" % code)
