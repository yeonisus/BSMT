"""Local face repair on the primary body component, end to end (M 3.30).

    /path/to/blender -b --factory-startup --python \\
        tests/test_local_face_repair_blender.py

Milestone 3.28 correctly refuses to delete the connected component holding a
defect when that component is the body - on a real scan the body is 92% of the
mesh, and deleting it is not a repair. That refusal stays, and this suite
re-asserts it. What it adds is the repair that refusal left missing: removing
the handful of FACES that hang off such a defect, when and only when the local
topology yields one unambiguous removable branch.

The fixture is the real case. A 1.7 m body sphere in millimetre coordinates,
rotated, translated and scaled the way an aligned measurement copy is, with a
third face grafted onto one of its own edges - one non-manifold edge, two
vertices, about a millimetre across, on the component holding ~99% of the
mesh. That is the scan that motivated this milestone.

What is asserted, in the order it matters
-----------------------------------------
* an AMBIGUOUS local topology never offers a removal, and the panel does not
  draw the destructive button for one;
* a successful removal takes exactly the approved faces - the rest of the body
  is bit-identical afterwards, which is the locality claim checked rather than
  asserted;
* the SOURCE scan is never touched;
* a removal that over-reaches, empties the mesh, or manufactures a degenerate
  triangle is rolled back by BSMT's own transaction, independently of
  Blender's undo stack;
* a stale candidate - geometry changed since the inspection - is refused
  rather than acted on;
* success invalidates the geometry-dependent state through the existing
  central path, and leaves no dangling reference to the mesh that was;
* and the three existing repairs - local weld, Delete Artifact, Remove
  Duplicate Faces - still behave exactly as they did.

On Blender's undo
-----------------
`bpy.ops.ed.undo` has no valid context in background mode, so the undo STEP
cannot be driven here. Section K asserts what can be: the operator declares
'UNDO', and BSMT's rollback needs no undo stack at all.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_local_face_repair_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_local_face_repair_blender.py")
    raise SystemExit(0)

import numpy as np  # noqa: E402

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
    log = []
    stub = type("Stub", (object,), {
        name: staticmethod(getattr(panel_class, name))
        for name in dir(panel_class) if name.startswith("_draw")
    })()
    stub.layout = FakeLayout(log)
    panel_class.draw(stub, context)
    return log


def drawn_operators(log):
    return [value for kind, value in log if kind == "operator"]


def drawn_texts(log):
    return [value for kind, value in log if kind == "label"]


LAST_ERROR = [""]


def run(operator, **kwargs):
    """Call an operator, turning a reported ERROR into {'CANCELLED'}."""
    LAST_ERROR[0] = ""
    try:
        return operator(**kwargs)
    except RuntimeError as exc:
        LAST_ERROR[0] = str(exc)
        return {'CANCELLED'}


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _graft_flap(bm, edge_index, faces=1, offset=1.0):
    """Hang `faces` triangles off ONE edge of the body. The real defect shape."""
    bm.edges.ensure_lookup_table()
    edge = bm.edges[edge_index]
    first, second = edge.verts
    middle = (first.co + second.co) * 0.5
    normal = middle.normalized()
    tip = bm.verts.new(middle + normal * offset)
    bm.faces.new((first, second, tip))
    previous = tip
    for step in range(1, faces):
        nxt = bm.verts.new(middle + normal * offset
                           + (second.co - first.co).normalized()
                           * offset * (step + 1))
        bm.faces.new((first, previous, nxt))
        previous = nxt
    return first, second


def _graft_twin(bm, edge_index, size=3):
    """Two comparable local patches on one body edge: the AMBIGUOUS case."""
    bm.edges.ensure_lookup_table()
    edge = bm.edges[edge_index]
    first, second = edge.verts
    middle = (first.co + second.co) * 0.5
    normal = middle.normalized()
    for sign in (1.0, -1.0):
        tips = [bm.verts.new(middle + normal * 2.0 * sign
                             + Vector((0.0, 0.0, 3.0)) * sign),
                bm.verts.new(middle + normal * 2.0 * sign
                             - Vector((0.0, 0.0, 3.0)) * sign)]
        bm.faces.new((first, second, tips[0]))
        bm.faces.new((first, second, tips[1]))
        bm.faces.new((tips[0], tips[1], first))
        previous = tips[1]
        for step in range(size - 3):
            nxt = bm.verts.new(middle + normal * (3.0 + step) * sign
                               + Vector((4.0 + step, 0.0, 0.0)))
            bm.faces.new((tips[0], previous, nxt))
            previous = nxt


def build(name="Body", defect='FLAP', flap_faces=1, scale=1.0,
          fragments=0):
    """A measurement mesh with its source scan, body-scale and transformed."""
    wipe()
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    for vert in bm.verts:
        vert.co.x *= 200.0
        vert.co.y *= 120.0
        vert.co.z *= 850.0
    # Triangulated, like every mesh the solver is ever given: a canonical
    # triangle is a loop triangle, and on a quad mesh it is not a polygon at
    # all - which is a different problem from the one under test here.
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    if defect == 'FLAP':
        _graft_flap(bm, 500, faces=flap_faces)
    elif defect == 'TWIN':
        _graft_twin(bm, 500)
    for index in range(fragments):
        base = Vector((600.0 + 120.0 * index, 0.0, 0.0))
        first = bm.verts.new(base)
        second = bm.verts.new(base + Vector((10.0, 0.0, 0.0)))
        for offset in ((5.0, 8.0, 0.0), (5.0, -8.0, 0.0), (5.0, 0.0, 8.0)):
            bm.faces.new((first, second, bm.verts.new(base + Vector(offset))))
    mesh = bpy.data.meshes.new(name + "_BSMT_Mesh")
    bm.to_mesh(mesh)
    bm.free()

    if defect == 'DUPLICATE':
        # bmesh REFUSES to create a face that already exists, so the copy is
        # made at mesh level - which is also how a real exporter produces one.
        corners = [tuple(int(v) for v in polygon.vertices)
                   for polygon in mesh.polygons]
        points = [tuple(vertex.co) for vertex in mesh.vertices]
        corners.append(tuple(reversed(corners[100])))   # reversed duplicate
        mesh.clear_geometry()
        mesh.from_pydata(points, [], corners)
        mesh.update()

    source_mesh = mesh.copy()
    source_mesh.name = name + "_Source_Mesh"
    source = bpy.data.objects.new(name + "_Source", source_mesh)
    bpy.context.scene.collection.objects.link(source)

    obj = bpy.data.objects.new(name + "_BSMT", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = (Matrix.Translation(Vector((300.0, -150.0, 900.0)))
                        @ Matrix.Rotation(0.6, 4, 'Z')
                        @ Matrix.Rotation(0.25, 4, 'X')
                        @ Matrix.Scale(scale, 4))
    obj.bsmt_scan.is_measurement_copy = True
    obj.bsmt_scan.source = source
    obj.bsmt_scan.source_name = source.name
    for other in bpy.context.selected_objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj, source


def counts(obj):
    obj.data.calc_loop_triangles()
    return len(obj.data.vertices), len(obj.data.loop_triangles)


def positions(obj):
    flat = np.empty(len(obj.data.vertices) * 3, dtype=np.float64)
    obj.data.vertices.foreach_get("co", flat)
    return flat


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (artifact, geodesic, localrepair,
                                          meshrepair, operators, panels,
                                          repair, state, visualization)

    context = bpy.context
    props = state.get_props(context)

    def analyze(obj):
        bpy.context.view_layer.objects.active = obj
        return run(bpy.ops.bsmt.analyse_repair)

    def topology(obj):
        canonical = geodesic.meshcache.get(bpy.context, obj, props.unit,
                                           rebuild=True)
        return dict(canonical.topology or {})

    def focus_defect(index=0):
        props.repair_nonmanifold_index = index

    # ------------------------------------------------------------------
    print("\nA. the real case: one non-manifold edge on the body itself")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP')
    analyze(obj)
    report = topology(obj)
    check("A1: the fixture reproduces the real scan's defect count",
          report.get("nonmanifold_edge_count") == 1,
          str(report.get("nonmanifold_edge_count")))
    check("A2: with one defect listed",
          len(props.repair_nonmanifold_defects) == 1,
          str(len(props.repair_nonmanifold_defects)))

    entry = props.repair_nonmanifold_defects[0]
    check("A3: the defect has one edge and two vertices",
          entry.edge_count == 1 and entry.vertex_count == 2,
          "%d / %d" % (entry.edge_count, entry.vertex_count))
    check("A4: it sits on the largest, primary body component",
          entry.component_is_largest is True)
    check("A5: so component deletion is still BLOCKED",
          artifact.PRIMARY_BLOCK_MESSAGE in entry.deletion_block,
          entry.deletion_block)

    check("A6: Inspect Local Topology succeeds",
          run(bpy.ops.bsmt.inspect_local_defect) == {'FINISHED'},
          LAST_ERROR[0])
    check("A7: it classifies the defect as a small dangling flap",
          props.repair_local_classification
          == localrepair.LOCAL_DANGLING_FLAP,
          props.repair_local_classification)
    check("A8: and offers a removal", props.repair_local_removable is True)
    check("A9: the candidate is exactly one face",
          props.repair_local_face_count == 1,
          str(props.repair_local_face_count))
    check("A10: with an area", props.repair_local_area_mm2 > 0.0)
    for fragment in ("Classification:", "Candidate:", "Faces:",
                     "Maximum reach:", "Safety caps"):
        check("A11: the report states %r" % fragment,
              fragment in props.repair_local_report,
              props.repair_local_report)
    check("A12: nothing was modified by inspecting",
          topology(obj).get("nonmanifold_edge_count") == 1)

    # ------------------------------------------------------------------
    print("\nB. preview highlights ONLY the candidate faces")
    # ------------------------------------------------------------------
    before_counts = counts(obj)
    before_positions = positions(obj).copy()
    check("B1: Preview Candidate Faces succeeds",
          run(bpy.ops.bsmt.preview_local_candidate) == {'FINISHED'},
          LAST_ERROR[0])
    helper = bpy.data.objects.get(visualization.REPAIR_LOCAL_CANDIDATE)
    check("B2: a candidate highlight object exists", helper is not None)
    if helper is not None:
        helper.data.calc_loop_triangles()
        check("B3: it holds exactly the candidate faces",
              len(helper.data.polygons) == 1,
              str(len(helper.data.polygons)))
        check("B4: NOT the whole component",
              len(helper.data.polygons) < before_counts[1] / 100.0)
        check("B5: it is a BSMT helper, invisible to diagnostics",
              visualization.is_helper(helper))
        check("B6: and it is not the component preview",
              helper.name != visualization.REPAIR_COMPONENT)
    check("B7: the mesh is untouched by the preview",
          counts(obj) == before_counts, str(counts(obj)))
    check("B8: not one vertex moved",
          np.array_equal(positions(obj), before_positions))
    check("B9: the helper did not reach the diagnostics",
          topology(obj).get("triangle_count") == before_counts[1],
          str(topology(obj).get("triangle_count")))
    check("B10: the preview can be cleared",
          run(bpy.ops.bsmt.clear_repair_highlight) == {'FINISHED'}
          and bpy.data.objects.get(
              visualization.REPAIR_LOCAL_CANDIDATE) is None)

    # No solver is involved anywhere on this path.
    source_text = open(os.path.join(ROOT, "body_surface_measurement",
                                    "localrepair.py")).read()
    check("B11: the policy module imports no solver at all",
          "from .geodesic" not in source_text
          and "import geodesic" not in source_text
          and "pygeodesic." not in source_text)

    # ------------------------------------------------------------------
    print("\nC. the removal, and what it is allowed to touch")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP')
    analyze(obj)
    run(bpy.ops.bsmt.inspect_local_defect)
    before_counts = counts(obj)
    before_positions = positions(obj).copy()
    source_before = positions(source).copy()
    source_faces_before = len(source.data.polygons)
    canonical_before = geodesic.meshcache.get(bpy.context, obj, props.unit,
                                              rebuild=True)
    body_before = np.array(canonical_before.vertices_local, copy=True)
    tris_before = np.array(canonical_before.triangles, copy=True)
    candidate_signature = localrepair.face_position_signature(
        body_before, tris_before)

    check("C1: Remove Local Artifact Faces succeeds",
          run(bpy.ops.bsmt.remove_local_faces) == {'FINISHED'},
          LAST_ERROR[0])
    after_counts = counts(obj)
    check("C2: exactly one triangle went",
          after_counts[1] == before_counts[1] - 1,
          "%s -> %s" % (before_counts, after_counts))
    check("C3: exactly one vertex went with it",
          after_counts[0] == before_counts[0] - 1,
          "%s -> %s" % (before_counts, after_counts))
    after_report = topology(obj)
    check("C4: non-manifold 1 -> 0",
          after_report.get("nonmanifold_edge_count") == 0,
          str(after_report.get("nonmanifold_edge_count")))
    check("C5: no degenerate triangle was created",
          after_report.get("degenerate_triangle_count") == 0)
    check("C6: connected_components is NOT required to be 1",
          after_report.get("component_count") >= 1)

    # sect. 9: the locality claim, MEASURED - and measured against the two
    # surfaces rather than against the operator's own account of what it did.
    canonical_after = geodesic.meshcache.get(bpy.context, obj, props.unit,
                                             rebuild=True)
    gone = _missing_faces(localrepair, body_before, tris_before,
                          canonical_after.vertices_local,
                          canonical_after.triangles)
    check("C7: exactly one triangle's surface is absent afterwards",
          gone.size == 1, str(gone.size))
    reasons = localrepair.locality_reasons(
        body_before, tris_before, gone,
        canonical_after.vertices_local, canonical_after.triangles)
    check("C8: every surviving triangle is bit-identical - the edit was local",
          reasons == [], str(reasons))
    check("C8b: and the signature of the whole mesh did change",
          not np.array_equal(candidate_signature,
                             localrepair.face_position_signature(
                                 canonical_after.vertices_local,
                                 canonical_after.triangles)))
    check("C8c: the surviving body vertices kept their exact coordinates",
          _surviving_vertices_unchanged(before_positions, positions(obj)))

    check("C9: the SOURCE scan still has every face",
          len(source.data.polygons) == source_faces_before)
    check("C10: and not one of its vertices moved",
          np.array_equal(positions(source), source_before))

    check("C11: the repair log records the action",
          "Remove local artifact faces" in props.repair_log,
          props.repair_log[-200:])
    check("C12: and the inspection was cleared, not carried forward",
          props.repair_local_removable is False
          and props.repair_local_hash == "")
    check("C13: no candidate highlight survived the edit",
          bpy.data.objects.get(visualization.REPAIR_LOCAL_CANDIDATE) is None)

    # ------------------------------------------------------------------
    print("\nD. a multi-face flap goes as ONE branch")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP', flap_faces=3)
    analyze(obj)
    run(bpy.ops.bsmt.inspect_local_defect)
    check("D1: the whole branch is the candidate, not one face",
          props.repair_local_face_count == 3,
          "%s (%s)" % (props.repair_local_face_count,
                       props.repair_local_classification))
    before_counts = counts(obj)
    check("D2: the removal succeeds",
          run(bpy.ops.bsmt.remove_local_faces) == {'FINISHED'},
          LAST_ERROR[0])
    check("D3: all three faces went",
          counts(obj)[1] == before_counts[1] - 3,
          "%s -> %s" % (before_counts, counts(obj)))
    check("D4: and the defect is gone",
          topology(obj).get("nonmanifold_edge_count") == 0)

    # ------------------------------------------------------------------
    print("\nE. an AMBIGUOUS local topology is refused")
    # ------------------------------------------------------------------
    obj, source = build(defect='TWIN')
    analyze(obj)
    focus_defect(0)
    check("E1: inspection reports, but does not offer a removal",
          run(bpy.ops.bsmt.inspect_local_defect) in ({'FINISHED'},
                                                     {'CANCELLED'}))
    check("E2: no removal is offered",
          props.repair_local_removable is False,
          props.repair_local_classification)
    check("E3: the classification is not one of the removable ones",
          props.repair_local_classification not in localrepair.REMOVABLE,
          props.repair_local_classification)
    check("E4: and the researcher is told a manual edit is required",
          localrepair.MANUAL_EDIT_MESSAGE in props.repair_local_report
          or "not offered" in props.repair_local_report,
          props.repair_local_report)

    before_counts = counts(obj)
    check("E5: the destructive operator refuses to run",
          run(bpy.ops.bsmt.remove_local_faces) == {'CANCELLED'})
    check("E6: and the mesh is untouched", counts(obj) == before_counts,
          "%s -> %s" % (before_counts, counts(obj)))

    log = draw_panel(panels.BSMT_PT_repair, bpy.context)
    check("E7: the panel does not draw the removal button",
          "bsmt.remove_local_faces" not in drawn_operators(log),
          str(drawn_operators(log)))
    check("E8: it still draws Inspect Local Topology",
          "bsmt.inspect_local_defect" in drawn_operators(log))
    check("E9: and says there is no safe candidate",
          any("No safe candidate" in text for text in drawn_texts(log)),
          str(drawn_texts(log)[-12:]))

    # ------------------------------------------------------------------
    print("\nF. duplicate faces are delegated to the existing repair")
    # ------------------------------------------------------------------
    obj, source = build(defect='DUPLICATE')
    analyze(obj)
    focus_defect(0)
    run(bpy.ops.bsmt.inspect_local_defect)
    check("F1: a duplicated face is classified as one",
          props.repair_local_classification
          == localrepair.LOCAL_DUPLICATE_FACE,
          props.repair_local_classification)
    check("F2: no local removal is offered for it",
          props.repair_local_removable is False)
    log = draw_panel(panels.BSMT_PT_repair, bpy.context)
    check("F3: the panel points at Remove Duplicate Faces",
          any("Remove Duplicate Faces" in text for text in drawn_texts(log)),
          str(drawn_texts(log)[-14:]))
    check("F4: and draws no local removal button",
          "bsmt.remove_local_faces" not in drawn_operators(log))

    before_counts = counts(obj)
    check("F5: the EXISTING duplicate repair still fixes it",
          run(bpy.ops.bsmt.remove_duplicate_faces) == {'FINISHED'},
          LAST_ERROR[0])
    check("F6: one face removed", counts(obj)[1] == before_counts[1] - 1,
          "%s -> %s" % (before_counts, counts(obj)))
    check("F7: and the defect is gone",
          topology(obj).get("nonmanifold_edge_count") == 0)

    # ------------------------------------------------------------------
    print("\nG. a stale candidate is refused, never acted on")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP')
    analyze(obj)
    run(bpy.ops.bsmt.inspect_local_defect)
    check("G1: the inspection recorded the geometry hash it applies to",
          props.repair_local_hash != "")

    # Change the geometry behind its back, the way an edit elsewhere would.
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[3].co.z += 7.0
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    geodesic.meshcache.invalidate(obj.name)
    before_counts = counts(obj)

    check("G2: the removal refuses a stale candidate",
          run(bpy.ops.bsmt.remove_local_faces) == {'CANCELLED'})
    check("G3: and says the mesh changed",
          "changed" in LAST_ERROR[0] or "changed" in props.repair_local_report,
          LAST_ERROR[0] + " | " + props.repair_local_report)
    check("G4: the mesh was not edited", counts(obj) == before_counts)
    check("G5: the preview refuses too",
          run(bpy.ops.bsmt.preview_local_candidate) == {'CANCELLED'})

    # Stepping to another defect must also drop the inspection.
    obj, source = build(defect='FLAP', fragments=2)
    analyze(obj)
    run(bpy.ops.bsmt.inspect_local_defect)
    check("G6: the inspection is live before stepping",
          props.repair_local_hash != "")
    run(bpy.ops.bsmt.step_nonmanifold_defect, direction='NEXT')
    check("G7: stepping to another defect drops the inspection",
          props.repair_local_hash == ""
          and props.repair_local_removable is False)
    check("G8: re-analysing drops it too",
          (run(bpy.ops.bsmt.inspect_local_defect) is not None
           and analyze(obj) is not None
           and props.repair_local_hash == ""))

    # ------------------------------------------------------------------
    print("\nH. rollback: an edit that over-reaches is not kept")
    # ------------------------------------------------------------------
    def with_injected(edit, label, expect_cancel=True):
        obj, source = build(defect='FLAP')
        analyze(obj)
        run(bpy.ops.bsmt.inspect_local_defect)
        before = counts(obj)
        before_positions = positions(obj).copy()
        original = meshrepair.remove_faces_by_vertex_sets

        def patched(target, wanted_counts):
            removed = original(target, wanted_counts)
            edit(target)
            return removed

        meshrepair.remove_faces_by_vertex_sets = patched
        try:
            result = run(bpy.ops.bsmt.remove_local_faces)
        finally:
            meshrepair.remove_faces_by_vertex_sets = original
        if expect_cancel:
            check("H: %s is rolled back" % label, result == {'CANCELLED'},
                  str(result))
            check("H: %s leaves the triangle count as it was" % label,
                  counts(obj) == before,
                  "%s -> %s" % (before, counts(obj)))
            check("H: %s leaves every vertex where it was" % label,
                  np.array_equal(positions(obj), before_positions))
        return obj

    def delete_extra_faces(target):
        bm = bmesh.new()
        bm.from_mesh(target.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[index] for index in range(40)],
                         context='FACES')
        bm.to_mesh(target.data)
        bm.free()
        target.data.update()

    def empty_the_mesh(target):
        bm = bmesh.new()
        bm.from_mesh(target.data)
        bmesh.ops.delete(bm, geom=list(bm.verts), context='VERTS')
        bm.to_mesh(target.data)
        bm.free()
        target.data.update()

    def move_unrelated_geometry(target):
        bm = bmesh.new()
        bm.from_mesh(target.data)
        bm.verts.ensure_lookup_table()
        bm.verts[7].co.z += 12.0
        bm.to_mesh(target.data)
        bm.free()
        target.data.update()

    def make_a_degenerate(target):
        bm = bmesh.new()
        bm.from_mesh(target.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        face = bm.faces[10]
        first, second, third = face.verts[:3]
        second.co = first.co.copy()
        third.co = first.co.copy()
        bm.to_mesh(target.data)
        bm.free()
        target.data.update()

    with_injected(delete_extra_faces, "an over-reaching deletion")
    with_injected(empty_the_mesh, "emptying the mesh")
    with_injected(move_unrelated_geometry, "moving unrelated geometry")
    with_injected(make_a_degenerate, "manufacturing a degenerate triangle")

    # ------------------------------------------------------------------
    print("\nI. invalidation of everything that depended on the geometry")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP')
    analyze(obj)
    canonical = geodesic.meshcache.get(bpy.context, obj, props.unit,
                                       rebuild=True)
    hit = canonical.bvh.ray_cast(
        Vector((0.0, 0.0, 2000.0)), Vector((0.0, 0.0, -1.0)))
    landmark = state.get_landmarks(bpy.context).add()
    landmark.name = "Test"
    landmark.stable_id = 1
    point = landmark.surface_point
    point.valid = True
    point.source_object = obj.name
    point.geometry_hash = canonical.geometry_hash
    point.triangle_index = int(hit[2]) if hit[2] is not None else 0
    point.status = "VALID"

    run(bpy.ops.bsmt.inspect_local_defect)
    check("I1: the removal succeeds with a landmark on the mesh",
          run(bpy.ops.bsmt.remove_local_faces) == {'FINISHED'},
          LAST_ERROR[0])
    check("I2: the landmark is no longer VALID",
          state.get_landmarks(bpy.context)[0].status != "VALID",
          state.get_landmarks(bpy.context)[0].status)
    check("I3: it was RESTATED, never silently re-projected",
          state.get_landmarks(bpy.context)[0].surface_point.geometry_hash
          == canonical.geometry_hash)
    check("I4: and the operator reported what it invalidated",
          "landmark(s) restated" in props.repair_local_report,
          props.repair_local_report)
    check("I5: the central invalidation path is what did it",
          "state.invalidate_for_geometry_change(context, obj.name," in
          open(os.path.join(ROOT, "body_surface_measurement",
                            "operators.py")).read())
    while len(state.get_landmarks(bpy.context)):
        state.get_landmarks(bpy.context).remove(0)

    # ------------------------------------------------------------------
    print("\nJ. index stability: nothing stale is reused after reindexing")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP', fragments=1)
    analyze(obj)
    before_defects = len(props.repair_nonmanifold_defects)
    check("J1: two defects before the repair", before_defects == 2,
          str(before_defects))
    focus_defect(0)
    run(bpy.ops.bsmt.inspect_local_defect)
    if not props.repair_local_removable:
        focus_defect(1)
        run(bpy.ops.bsmt.inspect_local_defect)
    check("J2: one of them is locally repairable",
          props.repair_local_removable is True,
          props.repair_local_classification)
    check("J3: the removal succeeds",
          run(bpy.ops.bsmt.remove_local_faces) == {'FINISHED'},
          LAST_ERROR[0])
    check("J4: the defect list was rebuilt, not patched",
          len(props.repair_nonmanifold_defects) == before_defects - 1,
          str(len(props.repair_nonmanifold_defects)))
    check("J5: the stored geometry hash matches the NEW mesh",
          props.repair_geometry_hash == geodesic.meshcache.get(
              bpy.context, obj, props.unit, rebuild=False).geometry_hash)
    check("J6: and the local inspection is not carried across the edit",
          props.repair_local_hash == "")

    # ------------------------------------------------------------------
    print("\nK. the existing repairs are unchanged")
    # ------------------------------------------------------------------
    check("K1: the removal declares UNDO",
          'UNDO' in operators.BSMT_OT_remove_local_faces.bl_options)
    check("K2: and runs inside the shared transactional wrapper",
          issubclass(operators.BSMT_OT_remove_local_faces,
                     operators._RepairBase))
    check("K3: rollback needs no undo stack - it restores a mesh backup",
          "meshrepair.restore_backup" in open(
              os.path.join(ROOT, "body_surface_measurement",
                           "operators.py")).read())

    # Delete Artifact: still refuses the primary body, still deletes a
    # fragment.
    obj, source = build(defect='FLAP', fragments=1)
    analyze(obj)
    body_defect = next(
        (index for index, entry in enumerate(props.repair_nonmanifold_defects)
         if entry.component_is_largest), None)
    fragment_defect = next(
        (index for index, entry in enumerate(props.repair_nonmanifold_defects)
         if not entry.component_is_largest), None)
    check("K4: the body's defect is found", body_defect is not None)
    check("K5: the fragment's defect is found", fragment_defect is not None)
    if body_defect is not None:
        focus_defect(body_defect)
        before = counts(obj)
        check("K6: Delete Artifact still refuses the primary body",
              run(bpy.ops.bsmt.delete_defect_component) == {'CANCELLED'})
        check("K7: with the message it has always shown",
              artifact.PRIMARY_BLOCK_MESSAGE in LAST_ERROR[0]
              or artifact.PRIMARY_BLOCK_MESSAGE
              in props.repair_artifact_preview,
              LAST_ERROR[0])
        check("K8: and nothing was deleted", counts(obj) == before)
    if fragment_defect is not None:
        focus_defect(fragment_defect)
        before = counts(obj)
        check("K9: Delete Artifact still deletes a non-body fragment",
              run(bpy.ops.bsmt.delete_defect_component) == {'FINISHED'},
              LAST_ERROR[0])
        check("K10: and it took the fragment's triangles with it",
              counts(obj)[1] < before[1], "%s -> %s" % (before, counts(obj)))

    # Weld Non-Manifold Region: unchanged.
    obj, source = build(defect='FLAP')
    analyze(obj)
    before = counts(obj)
    props.repair_weld_distance_mm = 0.001
    result = run(bpy.ops.bsmt.weld_non_manifold)
    check("K11: the local weld still runs and judges its own result",
          result in ({'FINISHED'}, {'CANCELLED'}), str(result))
    check("K12: a weld that changed nothing left the mesh alone",
          result == {'FINISHED'} or counts(obj) == before,
          "%s -> %s" % (before, counts(obj)))

    # Remove Duplicate Faces on a mesh with none: unchanged refusal.
    obj, source = build(defect='FLAP')
    analyze(obj)
    before = counts(obj)
    check("K13: Remove Duplicate Faces still refuses a mesh with none",
          run(bpy.ops.bsmt.remove_duplicate_faces) == {'CANCELLED'})
    check("K14: leaving the mesh untouched", counts(obj) == before)

    # ------------------------------------------------------------------
    print("\nL. the panel, drawn for the real case")
    # ------------------------------------------------------------------
    obj, source = build(defect='FLAP')
    analyze(obj)
    log = draw_panel(panels.BSMT_PT_repair, bpy.context)
    operators_drawn = drawn_operators(log)
    texts = drawn_texts(log)
    check("L1: Local Defect Repair is a section of its own",
          any("Local Defect Repair" in text for text in texts), str(texts[-20:]))
    check("L2: Inspect Local Topology is offered",
          "bsmt.inspect_local_defect" in operators_drawn)
    check("L3: the removal button is NOT drawn before an inspection",
          "bsmt.remove_local_faces" not in operators_drawn)
    check("L4: nor is the preview",
          "bsmt.preview_local_candidate" not in operators_drawn)
    check("L5: the primary-component block is still shown",
          any("Deletion blocked" in text for text in texts), str(texts[-20:]))

    run(bpy.ops.bsmt.inspect_local_defect)
    log = draw_panel(panels.BSMT_PT_repair, bpy.context)
    operators_drawn = drawn_operators(log)
    texts = drawn_texts(log)
    check("L6: after inspection the preview is offered",
          "bsmt.preview_local_candidate" in operators_drawn)
    check("L7: and so is the removal",
          "bsmt.remove_local_faces" in operators_drawn)
    check("L8: the classification is on screen",
          any("Small dangling flap" in text for text in texts),
          str(texts[-24:]))
    check("L9: the safety caps are stated, not hidden",
          any("Safety caps" in text for text in texts), str(texts[-24:]))
    check("L10: and Delete Artifact is still blocked beside it",
          any("Deletion blocked" in text for text in texts))

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


def _missing_faces(localrepair, before_vertices, before_faces,
                   after_vertices, after_faces):
    """Indices of the before-faces whose surface is absent afterwards.

    Derived from the two SURFACES, never from what the operator claims it
    removed - otherwise the locality check would be asserting the operator's
    own account of itself.
    """
    after = {tuple(row) for row in
             localrepair.face_position_signature(after_vertices, after_faces)}
    # The same quantisation the signature uses, applied per face and in the
    # ORIGINAL face order, so the result is usable as an index array.
    tolerance = localrepair.LOCALITY_TOLERANCE
    quantised = np.rint(np.asarray(before_vertices, dtype=np.float64)
                        / tolerance).astype(np.int64)
    corners = quantised[np.asarray(before_faces, dtype=np.int64)]
    order = np.lexsort((corners[:, :, 2], corners[:, :, 1],
                        corners[:, :, 0]), axis=1)
    corners = np.take_along_axis(corners, order[:, :, None], axis=1)
    flat = corners.reshape(before_faces.shape[0], 9)
    return np.asarray([index for index in range(flat.shape[0])
                       if tuple(flat[index]) not in after], dtype=np.int64)


def _surviving_vertices_unchanged(before_flat, after_flat):
    """Every after-vertex position occurs, bit-identically, in the before set."""
    before = before_flat.reshape(-1, 3)
    after = after_flat.reshape(-1, 3)
    known = {tuple(row) for row in before}
    return all(tuple(row) in known for row in after)


if __name__ == "__main__":
    sys.exit(main())
