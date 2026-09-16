"""Researcher-supervised artifact removal, end to end (Milestone 3.28).

    /path/to/blender -b --factory-startup --python \\
        tests/test_artifact_deletion_blender.py

A real human scan carries small detached fragments - a shard of floor, a scrap
of turntable, a sliver off a shoulder - and one of them is often what holds
the non-manifold edge blocking exact measurement. This milestone lets a
researcher inspect ONE defect, see the whole connected component it belongs
to, and delete that component. It does not let BSMT decide that the geometry
was irrelevant, and it does not let anyone delete the body.

So this suite asserts the safety story, not the happy path:

* the component holding the focused defect is the one that goes, and nothing
  else - the body's triangle count is unchanged to the triangle;
* the SOURCE scan is never touched;
* a defect on the primary body component is REFUSED, with the message the
  panel shows;
* a deletion that would leave the mesh unsafe is rolled back by BSMT's own
  transaction, independently of Blender's undo stack - asserted by injecting
  edits that over-reach, that empty the mesh, and that manufacture a
  degenerate triangle;
* a partial improvement (2 non-manifold edges -> 1) is a success, because
  requiring every defect to vanish in one press would make the operation
  impossible on exactly the scans that need it;
* helper and highlight geometry never reaches the diagnostics or the edit;
* and once geometry has changed, the stored analysis is refused rather than
  reused - stored region ids and component numbers describe a mesh that no
  longer exists.

On Blender's undo
-----------------
`bpy.ops.ed.undo` has no valid context in background mode, so the undo STEP
cannot be driven here. Section L asserts what can be: the operator declares
'UNDO', BSMT's rollback needs no undo stack at all, and restoring the
pre-deletion mesh the way an undo would leaves BSMT refusing the stale
analysis instead of acting on it.
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
    print("SKIP  tests/test_artifact_deletion_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_artifact_deletion_blender.py")
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
    """Records what a panel asks for. Enough to run draw() headless.

    Blender cannot build a real UILayout in background mode, and the artifact
    workflow adds a whole sub-panel of new draw code - which is ordinary
    Python and breaks like any other. Same stand-in as
    tests/test_workflow_ui.py, deliberately.
    """

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


def drawn_operators(log):
    return [value for kind, value in log if kind == "operator"]


def drawn_texts(log):
    return [value for kind, value in log if kind == "label"]


LAST_ERROR = [""]


def run(operator, **kwargs):
    """Call an operator, turning a reported ERROR into {'CANCELLED'}.

    `bpy.ops` raises RuntimeError when an operator reports {'ERROR'}, and
    reporting an error is exactly how BSMT refuses a destructive action - so
    a refusal has to be caught in order to be asserted at all. The message is
    kept, because the message is half of what is being tested.
    """
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


def _lump(bm, origin, size=10.0):
    """Three triangles on one shared edge: a detached lump, 1 non-manifold edge."""
    base = Vector(origin)
    first = bm.verts.new(base)
    second = bm.verts.new(base + Vector((size, 0.0, 0.0)))
    for offset in ((size * 0.5, size * 0.8, 0.0),
                   (size * 0.5, -size * 0.8, 0.0),
                   (size * 0.5, 0.0, size * 0.8)):
        bm.faces.new((first, second, bm.verts.new(base + Vector(offset))))
    return first, second


def build(name="Body", fragments=1, defect_on_body=False, scale=1.0):
    """A measurement mesh with its source scan, body-scale and transformed.

    The body is a 1.7 m sphere in millimetre coordinates, rotated and
    translated the way an aligned scan is, so nothing here is only true at
    the origin with an identity matrix.
    """
    wipe()
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    for vert in bm.verts:
        vert.co.x *= 200.0
        vert.co.y *= 120.0
        vert.co.z *= 850.0
    if defect_on_body:
        # A third face on an edge of the BODY itself: the defect a researcher
        # inspects and then finds is not a fragment at all.
        bm.edges.ensure_lookup_table()
        edge = bm.edges[500]
        first, second = edge.verts
        tip = bm.verts.new((first.co + second.co) * 0.5 * 1.05)
        bm.faces.new((first, second, tip))
    for index in range(fragments):
        _lump(bm, (600.0 + 120.0 * index, 0.0, 0.0))
    mesh = bpy.data.meshes.new(name + "_BSMT_Mesh")
    bm.to_mesh(mesh)
    bm.free()

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
    from body_surface_measurement import (artifact, geodesic, landmarks,
                                          measurements, meshrepair, operators,
                                          repair, state, visualization)

    context = bpy.context
    props = context.scene.bsmt

    def canonical_of(obj, rebuild=True):
        return geodesic.meshcache.get(context, obj, props.unit,
                                      rebuild=rebuild)

    def topology(obj):
        return dict(canonical_of(obj).topology or {})

    def body_triangles(obj):
        return int((canonical_of(obj).component_triangle_counts or [0])[0])

    # ================================================================== A ==
    print("\nA. a body with one detached artifact carrying the defect")
    obj, source = build()
    before_vertices, before_triangles = counts(obj)
    source_before = counts(source)

    check("A1: Analyze ran", bpy.ops.bsmt.analyse_repair() == {'FINISHED'})
    report = topology(obj)
    check("A2: exactly one non-manifold edge",
          report.get("nonmanifold_edge_count") == 1,
          report.get("nonmanifold_edge_count"))
    check("A3: two connected components",
          report.get("component_count") == 2, report.get("component_count"))
    check("A4: and the mesh is NOT measurement ready",
          not repair.readiness(report)["ready"])

    check("A5: Analyze filled the focused-defect list",
          len(props.repair_nonmanifold_defects) == 1,
          len(props.repair_nonmanifold_defects))
    entry = state.active_nonmanifold_defect(props)
    check("A6: there is a focused defect", entry is not None)
    check("A7: it records the geometry it was computed from",
          props.repair_geometry_hash
          and props.repair_geometry_hash == canonical_of(obj).geometry_hash)
    check("A8: the defect names a component that is NOT the body",
          entry is not None and entry.component_index != 1
          and not entry.component_is_largest,
          entry.component_index if entry else None)
    check("A9: that component is three triangles",
          entry is not None and entry.component_triangle_count == 3,
          entry.component_triangle_count if entry else None)
    check("A10: it is flagged small", entry is not None
          and entry.component_is_small)
    check("A11: and deletion is NOT blocked",
          entry is not None and entry.deletion_block == "",
          entry.deletion_block if entry else None)

    # ================================================================== B ==
    print("\nB. Preview Artifact shows the whole component, and changes nothing")
    check("B1: Show Edges ran",
          bpy.ops.bsmt.show_non_manifold() == {'FINISHED'})
    check("B2: Preview Artifact ran",
          bpy.ops.bsmt.preview_artifact_component() == {'FINISHED'})
    helper = bpy.data.objects.get(visualization.REPAIR_COMPONENT)
    check("B3: a component highlight was created", helper is not None)
    if helper is not None:
        check("B4: it is the component's own three triangles, not one edge",
              len(helper.data.polygons) == 3, len(helper.data.polygons))
        check("B5: with only that component's five vertices",
              len(helper.data.vertices) == 5, len(helper.data.vertices))
        check("B6: it is a BSMT helper", visualization.is_helper(helper))
        check("B7: in the helper collection",
              any(collection.name == visualization.COLLECTION_NAME
                  for collection in helper.users_collection))
        check("B8: unselectable and drawn in front",
              helper.hide_select and helper.show_in_front)
        check("B9: carrying a material, so Solid shading colours it",
              len(helper.data.materials) > 0)
        world = [helper.matrix_world @ vertex.co
                 for vertex in helper.data.vertices]
        expected = obj.matrix_world @ Vector((605.0, 0.0, 0.0))
        near = min((point - expected).length for point in world)
        check("B10: drawn at the fragment's real world position",
              near < 15.0, "%.2f mm" % near)

    preview = props.repair_artifact_preview
    check("B11: the preview states the triangle count",
          "Triangles:     3" in preview, preview)
    check("B12: and the vertex count", "Vertices:      5" in preview)
    check("B13: and a bounding box in millimetres",
          "Bounding box:" in preview and "mm" in preview)
    check("B14: and that this is not the main component",
          "Largest / main component: no" in preview)
    check("B15: and that it holds the highlighted non-manifold edge",
          "Holds the highlighted non-manifold edge: yes" in preview)
    check("B16: and that the source scan will not be modified",
          "source scan will not be modified" in preview)
    check("B17: previewing modified nothing",
          counts(obj) == (before_vertices, before_triangles), counts(obj))

    # ================================================================== C ==
    print("\nC. Delete Artifact removes that component and nothing else")
    body_before = body_triangles(obj)
    check("C1: Delete Artifact ran",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    after_vertices, after_triangles = counts(obj)
    check("C2: exactly the component's three triangles went",
          after_triangles == before_triangles - 3,
          (before_triangles, after_triangles))
    check("C3: exactly its five vertices went",
          after_vertices == before_vertices - 5,
          (before_vertices, after_vertices))
    after = topology(obj)
    check("C4: non-manifold edges 1 -> 0",
          after.get("nonmanifold_edge_count") == 0,
          after.get("nonmanifold_edge_count"))
    check("C5: components 2 -> 1", after.get("component_count") == 1,
          after.get("component_count"))
    check("C6: the mesh is now measurement ready",
          repair.readiness(after)["ready"], repair.readiness(after)["blockers"])
    check("C7: the BODY component is untouched, to the triangle",
          body_triangles(obj) == body_before,
          (body_before, body_triangles(obj)))
    check("C8: no degenerate triangle was introduced",
          after.get("degenerate_triangle_count") == 0,
          after.get("degenerate_triangle_count"))

    print("\n   the source scan")
    check("C9: the source object still exists",
          bpy.data.objects.get(source.name) is not None)
    check("C10: with exactly the geometry it had",
          counts(source) == source_before, (source_before, counts(source)))
    check("C11: and it is a different datablock from the copy",
          source.data is not obj.data)

    print("\n   what the panel is left showing")
    check("C12: the stale highlights are gone",
          not [helper_obj for helper_obj in bpy.data.objects
               if helper_obj.name.startswith(visualization.REPAIR_PREFIX)],
          [o.name for o in bpy.data.objects
           if o.name.startswith(visualization.REPAIR_PREFIX)])
    check("C13: the focused-defect list is empty - the defect is gone",
          len(props.repair_nonmanifold_defects) == 0,
          len(props.repair_nonmanifold_defects))
    check("C14: the diagnostics text was refreshed",
          "Non-manifold edges   0" in props.repair_report, props.repair_report)
    check("C15: the recorded geometry hash follows the new mesh",
          props.repair_geometry_hash == canonical_of(obj).geometry_hash)
    check("C16: the repair log records what was removed",
          "Delete defect component" in props.repair_log
          and "non-manifold    1 -> 0" in props.repair_log, props.repair_log)
    check("C17: and a summary is shown",
          "Deleted component" in props.repair_artifact_preview,
          props.repair_artifact_preview)
    check("C18: with nothing left to delete, the operator stops polling",
          not operators.BSMT_OT_delete_defect_component.poll(context))

    # ================================================================== D ==
    print("\nD. a defect on the primary body component is refused")
    obj, _source = build(name="Solid", fragments=0, defect_on_body=True)
    before_state = (counts(obj), positions(obj).copy())
    bpy.ops.bsmt.analyse_repair()
    report = topology(obj)
    check("D1: one non-manifold edge", report.get("nonmanifold_edge_count") == 1,
          report.get("nonmanifold_edge_count"))
    check("D2: and it is all one connected component",
          report.get("component_count") == 1, report.get("component_count"))
    entry = state.active_nonmanifold_defect(props)
    check("D3: there is a focused defect", entry is not None)
    check("D4: whose component is the body",
          entry is not None and entry.component_is_largest)
    check("D5: the panel is told deletion is blocked",
          entry is not None and entry.deletion_block != "")
    check("D6: with the message the researcher reads",
          entry is not None
          and artifact.PRIMARY_BLOCK_MESSAGE in entry.deletion_block,
          entry.deletion_block if entry else None)
    check("D7: Delete Artifact refuses",
          run(bpy.ops.bsmt.delete_defect_component) == {'CANCELLED'})
    check("D7b: saying the defect belongs to the primary body component",
          artifact.PRIMARY_BLOCK_MESSAGE in LAST_ERROR[0], LAST_ERROR[0])
    check("D8: and the mesh is untouched",
          counts(obj) == before_state[0]
          and np.array_equal(positions(obj), before_state[1]))
    check("D9: the defect is still there - nothing was quietly repaired",
          topology(obj).get("nonmanifold_edge_count") == 1)
    check("D10: Preview still works, so the researcher can see why",
          bpy.ops.bsmt.preview_artifact_component() == {'FINISHED'})
    check("D11: and the preview says deletion is blocked",
          "DELETION BLOCKED" in props.repair_artifact_preview,
          props.repair_artifact_preview[:120])

    print("\n   a body with a fragment, but the defect is on the body")
    obj, _source = build(name="Mixed", fragments=1, defect_on_body=True)
    bpy.ops.bsmt.analyse_repair()
    report = topology(obj)
    check("D12: two non-manifold edges in two components",
          report.get("nonmanifold_edge_count") == 2
          and report.get("component_count") == 2,
          (report.get("nonmanifold_edge_count"),
           report.get("component_count")))
    blocked = [item for item in props.repair_nonmanifold_defects
               if item.component_is_largest]
    offered = [item for item in props.repair_nonmanifold_defects
               if not item.component_is_largest]
    check("D13: one defect is on the body and one is not",
          len(blocked) == 1 and len(offered) == 1,
          (len(blocked), len(offered)))
    check("D14: the body one is blocked",
          blocked and blocked[0].deletion_block != "")
    check("D15: the fragment one is offered",
          offered and offered[0].deletion_block == "")
    check("D16: having several components is still only a preference",
          "connected components" in "\n".join(
              repair.readiness(report)["preferences"]))

    index = next(position for position, item
                 in enumerate(props.repair_nonmanifold_defects)
                 if item.component_is_largest)
    props.repair_nonmanifold_index = index
    before_counts = counts(obj)
    check("D17: focusing the body defect and pressing Delete is refused",
          run(bpy.ops.bsmt.delete_defect_component) == {'CANCELLED'})
    check("D18: with the primary-component message",
          artifact.PRIMARY_BLOCK_MESSAGE in LAST_ERROR[0], LAST_ERROR[0])
    check("D19: and nothing was deleted", counts(obj) == before_counts)

    props.repair_nonmanifold_index = next(
        position for position, item
        in enumerate(props.repair_nonmanifold_defects)
        if not item.component_is_largest)
    check("D20: focusing the FRAGMENT defect instead, deletion is allowed",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    check("D21: the fragment went and the body's defect remains",
          topology(obj).get("component_count") == 1
          and topology(obj).get("nonmanifold_edge_count") == 1,
          (topology(obj).get("component_count"),
           topology(obj).get("nonmanifold_edge_count")))

    # ================================================================== E ==
    print("\nE. a partial improvement is a success: 2 non-manifold -> 1")
    obj, _source = build(name="Two", fragments=2)
    bpy.ops.bsmt.analyse_repair()
    report = topology(obj)
    check("E1: two defects, three components",
          report.get("nonmanifold_edge_count") == 2
          and report.get("component_count") == 3,
          (report.get("nonmanifold_edge_count"),
           report.get("component_count")))
    check("E2: both are offered", len(props.repair_nonmanifold_defects) == 2
          and all(item.deletion_block == ""
                  for item in props.repair_nonmanifold_defects))
    body_before = body_triangles(obj)
    _vertices_before, triangles_before = counts(obj)
    check("E3: Delete Artifact succeeded",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    after = topology(obj)
    check("E4: non-manifold 2 -> 1 was ACCEPTED, not reverted",
          after.get("nonmanifold_edge_count") == 1,
          after.get("nonmanifold_edge_count"))
    check("E5: components 3 -> 2", after.get("component_count") == 2,
          after.get("component_count"))
    check("E6: only one fragment went",
          counts(obj)[1] == triangles_before - 3, counts(obj)[1])
    check("E7: the body is untouched", body_triangles(obj) == body_before)
    check("E8: the mesh is still NOT ready, and says so",
          not repair.readiness(after)["ready"])
    check("E9: the remaining defect is re-derived, not carried over",
          len(props.repair_nonmanifold_defects) == 1
          and props.repair_nonmanifold_defects[0].region_id == 1,
          [(item.region_id, item.component_index)
           for item in props.repair_nonmanifold_defects])
    check("E10: and the second press clears it",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    check("E11: non-manifold 1 -> 0",
          topology(obj).get("nonmanifold_edge_count") == 0)
    check("E12: with the body still untouched after both",
          body_triangles(obj) == body_before)

    # ================================================================== F ==
    print("\nF. an over-reaching edit is rolled back by BSMT's own transaction")
    obj, _source = build(name="Reach")
    bpy.ops.bsmt.analyse_repair()
    before_counts = counts(obj)
    before_positions = positions(obj).copy()
    before_report = topology(obj)
    original_remove = meshrepair.remove_component

    def over_reaching(target, vertex_indices):
        """Delete the component AND a bite out of the body."""
        wanted = set(int(index) for index in vertex_indices)
        wanted.update(range(0, 40))
        return original_remove(target, sorted(wanted))

    meshrepair.remove_component = over_reaching
    try:
        result = run(bpy.ops.bsmt.delete_defect_component)
    finally:
        meshrepair.remove_component = original_remove
    check("F1: the operation was refused", result == {'CANCELLED'}, str(result))
    check("F2: the mesh was restored, vertex for vertex",
          counts(obj) == before_counts
          and np.array_equal(positions(obj), before_positions),
          (before_counts, counts(obj)))
    restored = topology(obj)
    check("F3: the topology is exactly what it was",
          restored.get("nonmanifold_edge_count")
          == before_report.get("nonmanifold_edge_count")
          and restored.get("triangle_count")
          == before_report.get("triangle_count"))
    check("F4: the log says it was reverted, and why",
          "REVERTED" in props.repair_log, props.repair_log[-200:])
    check("F5: naming the primary body as the reason",
          "primary body component changed size" in props.repair_log
          or "not the 3 the component held" in props.repair_log,
          props.repair_log[-300:])

    # ================================================================== G ==
    print("\nG. an edit that empties the mesh is rolled back")
    obj, _source = build(name="Empty")
    bpy.ops.bsmt.analyse_repair()
    before_counts = counts(obj)
    before_positions = positions(obj).copy()

    def delete_everything(target, _vertex_indices):
        mesh = bmesh.new()
        mesh.from_mesh(target.data)
        bmesh.ops.delete(mesh, geom=list(mesh.faces), context='FACES')
        mesh.to_mesh(target.data)
        mesh.free()
        target.data.update()
        return 0

    meshrepair.remove_component = delete_everything
    try:
        result = run(bpy.ops.bsmt.delete_defect_component)
    finally:
        meshrepair.remove_component = original_remove
    check("G1: the operation was refused", result == {'CANCELLED'}, str(result))
    check("G2: the mesh was restored", counts(obj) == before_counts
          and np.array_equal(positions(obj), before_positions),
          (before_counts, counts(obj)))
    check("G3: it still has triangles to measure", counts(obj)[1] > 0)
    check("G4: and the defect is still reported",
          topology(obj).get("nonmanifold_edge_count") == 1)

    # ================================================================== H ==
    print("\nH. an edit that manufactures a degenerate triangle is rolled back")
    obj, _source = build(name="Degenerate")
    bpy.ops.bsmt.analyse_repair()
    before_counts = counts(obj)
    before_positions = positions(obj).copy()
    check("H1: the mesh starts with no degenerate triangles",
          topology(obj).get("degenerate_triangle_count") == 0)

    def collapse_a_triangle(target, vertex_indices):
        removed = original_remove(target, vertex_indices)
        mesh = bmesh.new()
        mesh.from_mesh(target.data)
        mesh.verts.ensure_lookup_table()
        # Put one vertex exactly on a neighbour: every face they share now
        # has zero area, which is a hard blocker for exact measurement.
        mesh.verts[1].co = mesh.verts[0].co.copy()
        mesh.to_mesh(target.data)
        mesh.free()
        target.data.update()
        return removed

    meshrepair.remove_component = collapse_a_triangle
    try:
        result = run(bpy.ops.bsmt.delete_defect_component)
    finally:
        meshrepair.remove_component = original_remove
    check("H2: the operation was refused", result == {'CANCELLED'}, str(result))
    check("H3: the mesh was restored", counts(obj) == before_counts
          and np.array_equal(positions(obj), before_positions))
    check("H4: with no degenerate triangle left behind",
          topology(obj).get("degenerate_triangle_count") == 0)
    check("H5: the log names the degenerate triangles as the reason",
          "degenerate" in props.repair_log, props.repair_log[-300:])

    # ================================================================== I ==
    print("\nI. helper and highlight geometry is never part of the mesh")
    obj, _source = build(name="Helpers")
    bpy.ops.bsmt.analyse_repair()
    clean_report = topology(obj)
    bpy.ops.bsmt.show_non_manifold()
    bpy.ops.bsmt.preview_artifact_component()
    helpers = [item for item in bpy.data.objects
               if item.name.startswith(visualization.REPAIR_PREFIX)]
    check("I1: highlights exist to be confused with the mesh",
          len(helpers) >= 2, [item.name for item in helpers])
    check("I2: every one is flagged a BSMT helper",
          all(visualization.is_helper(item) for item in helpers))
    bpy.ops.bsmt.analyse_repair()
    with_helpers = topology(obj)
    check("I3: diagnostics are identical with the highlights present",
          all(with_helpers.get(key) == clean_report.get(key)
              for key in ("vertex_count", "triangle_count", "component_count",
                          "nonmanifold_edge_count", "boundary_edge_count",
                          "degenerate_triangle_count")),
          (clean_report.get("triangle_count"),
           with_helpers.get("triangle_count")))
    check("I4: the component count did not gain the highlight objects",
          with_helpers.get("component_count") == 2,
          with_helpers.get("component_count"))
    for item in helpers:
        context.view_layer.objects.active = item
        target, reason = operators._repair_target(context)
        check("I5: '%s' is refused as a repair target" % item.name,
              target is None and "helper" in reason, reason)
    context.view_layer.objects.active = obj
    before_helper_count = len(helpers)
    bpy.ops.bsmt.analyse_repair()
    bpy.ops.bsmt.delete_defect_component()
    check("I6: the deletion removed the fragment, not a helper",
          topology(obj).get("component_count") == 1)
    check("I7: and the helpers were cleared rather than left dangling",
          not [item for item in bpy.data.objects
               if item.name.startswith(visualization.REPAIR_PREFIX)],
          before_helper_count)

    # ================================================================== J ==
    print("\nJ. geometry-dependent results go stale, and are not re-projected")
    obj, _source = build(name="Stale")
    bpy.ops.bsmt.analyse_repair()
    canonical = canonical_of(obj)
    collection = context.scene.bsmt_landmarks
    collection.clear()
    for index, triangle in enumerate((5, 40, 200), start=1):
        item = collection.add()
        item.stable_id = index
        item.name = "L%d" % index
        item.protocol_id = "P%02d" % index
        item.status = landmarks.STATUS_VALID
        point = item.surface_point
        point.triangle_index = int(triangle)
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = obj.name
        point.geometry_hash = canonical.geometry_hash
        point.component_id = canonical.component_of(triangle)
        point.status = "VALID"
        point.valid = True
        point.local_xyz = tuple(float(value) for value in canonical.local_from(
            triangle, np.array(point.barycentric, dtype=np.float64)))
        point.world_xyz = tuple(float(value) for value in canonical.world_from(
            triangle, np.array(point.barycentric, dtype=np.float64),
            obj.matrix_world))
    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    measurement = measurement_collection.add()
    measurement.stable_id = 1
    measurement.name = "M1"
    measurement.protocol_id = "M01"
    measurement.source_stable_id = 1
    measurement.target_stable_id = 2
    measurement.surface_valid = True          # has_result is derived
    measurement.surface_mm = 123.456
    measurement.status = measurements.STATUS_VALID
    measurement.result_object = obj.name
    measurement.result_geometry_hash = canonical.geometry_hash

    stored_anchors = [(item.surface_point.triangle_index,
                       tuple(item.surface_point.barycentric),
                       tuple(item.surface_point.local_xyz))
                      for item in collection]
    check("J1: three landmarks start VALID",
          all(item.status == landmarks.STATUS_VALID for item in collection),
          [item.status for item in collection])
    check("J2: and the measurement holds a number", measurement.has_result)

    check("J3: Delete Artifact ran",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    check("J4: no landmark is still VALID",
          not any(item.status == landmarks.STATUS_VALID
                  for item in collection),
          [item.status for item in collection])
    check("J5: the stored measurement number is gone, not kept",
          not measurement.has_result and measurement.surface_mm == 0.0,
          (measurement.has_result, measurement.surface_mm))
    check("J6: the measurement says it was invalidated",
          "invalidated" in measurement.status_detail,
          measurement.status_detail)
    # "Not re-projected" is a claim about the STORED ANCHOR - the canonical
    # triangle and its barycentric coordinates. A landmark BSMT had silently
    # moved onto the new surface would carry a different triangle index or
    # different barycentrics; these are identical.
    anchors_now = [(item.surface_point.triangle_index,
                    tuple(item.surface_point.barycentric),
                    tuple(item.surface_point.local_xyz))
                   for item in collection]
    check("J7: NO landmark was re-projected - triangle, barycentrics and "
          "local position are exactly as picked",
          anchors_now == stored_anchors,
          (stored_anchors[0], anchors_now[0]))
    check("J7b: every landmark still names the mesh it was picked on",
          all(item.surface_point.source_object == obj.name
              for item in collection))
    check("J8: and the operator reports how many were restated",
          "landmark(s) restated" in props.repair_artifact_preview,
          props.repair_artifact_preview)

    # ================================================================== K ==
    print("\nK. a stored analysis is refused once the mesh has changed")
    obj, _source = build(name="Indices", fragments=2)
    bpy.ops.bsmt.analyse_repair()
    stored_hash = props.repair_geometry_hash
    stored_ids = [(item.region_id, item.component_index)
                  for item in props.repair_nonmanifold_defects]
    check("K1: two defects were recorded", len(stored_ids) == 2, stored_ids)

    # Edit the mesh BEHIND BSMT's back, the way any other tool would.
    edit = bmesh.new()
    edit.from_mesh(obj.data)
    edit.verts.ensure_lookup_table()
    bmesh.ops.delete(edit, geom=[edit.verts[0]], context='VERTS')
    edit.to_mesh(obj.data)
    edit.free()
    obj.data.update()

    live = canonical_of(obj)
    check("K2: the mesh's geometry hash has changed",
          live.geometry_hash != stored_hash)
    check("K3: the stored analysis still holds the OLD numbers",
          [(item.region_id, item.component_index)
           for item in props.repair_nonmanifold_defects] == stored_ids)
    before_counts = counts(obj)
    result = run(bpy.ops.bsmt.delete_defect_component)
    check("K4: Delete Artifact refuses to act on them",
          result == {'CANCELLED'}, str(result))
    check("K5: and deleted nothing", counts(obj) == before_counts)
    check("K6: Preview refuses too",
          run(bpy.ops.bsmt.preview_artifact_component) == {'CANCELLED'})
    check("K7: telling the researcher to re-analyze",
          "Analyze Mesh again" in props.repair_artifact_preview,
          props.repair_artifact_preview)

    check("K8: re-analyzing recovers",
          bpy.ops.bsmt.analyse_repair() == {'FINISHED'})
    check("K9: with a hash that matches the live mesh",
          props.repair_geometry_hash == canonical_of(obj).geometry_hash)
    check("K10: and the deletion works again",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})

    print("\n   an analysis of one mesh is not applied to another")
    obj, _source = build(name="First")
    twin_mesh = obj.data.copy()
    twin = bpy.data.objects.new("Second_BSMT", twin_mesh)
    context.scene.collection.objects.link(twin)
    twin.matrix_world = obj.matrix_world.copy()
    twin.bsmt_scan.is_measurement_copy = True
    twin.bsmt_scan.source_name = obj.bsmt_scan.source_name
    bpy.ops.bsmt.analyse_repair()                     # analyses `obj`
    check("K11: the two meshes have the same geometry hash, by construction",
          canonical_of(obj).geometry_hash == canonical_of(twin).geometry_hash)
    context.view_layer.objects.active = twin
    before_counts = counts(twin)
    check("K12: deleting on the OTHER mesh is refused - a matching hash is "
          "not the same mesh",
          run(bpy.ops.bsmt.delete_defect_component) == {'CANCELLED'},
          LAST_ERROR[0])
    check("K13: naming the mesh the analysis is actually of",
          obj.name in LAST_ERROR[0], LAST_ERROR[0])
    check("K14: and nothing was deleted", counts(twin) == before_counts)
    context.view_layer.objects.active = obj

    # ================================================================== L ==
    print("\nL. undo: the contract, and a rollback that does not need it")
    check("L1: the operator declares UNDO, so Blender pushes a step",
          'UNDO' in operators.BSMT_OT_delete_defect_component.bl_options,
          operators.BSMT_OT_delete_defect_component.bl_options)
    check("L2: the read-only operators do NOT push one",
          'UNDO' not in operators.BSMT_OT_preview_artifact_component.bl_options
          and 'UNDO' not in
          operators.BSMT_OT_step_nonmanifold_defect.bl_options)

    obj, _source = build(name="Undo")
    bpy.ops.bsmt.analyse_repair()
    before_counts = counts(obj)
    before_positions = positions(obj).copy()
    backup = meshrepair.make_backup(obj)
    check("L3: Delete Artifact ran",
          bpy.ops.bsmt.delete_defect_component() == {'FINISHED'})
    check("L4: geometry changed", counts(obj) != before_counts)

    # What an undo does to the mesh, done directly - the undo stack itself
    # has no valid context in background Blender.
    check("L5: the pre-deletion mesh can be put back",
          meshrepair.restore_backup(obj, backup))
    check("L6: and it is the mesh that was there",
          counts(obj) == before_counts
          and np.array_equal(positions(obj), before_positions))
    check("L7: BSMT's stored analysis now describes the wrong mesh",
          props.repair_geometry_hash != canonical_of(obj).geometry_hash)
    check("L8: so the next deletion is refused, not misapplied",
          run(bpy.ops.bsmt.delete_defect_component) == {'CANCELLED'},
          LAST_ERROR[0])
    check("L9: the restored geometry is intact", counts(obj) == before_counts)
    check("L10: and Analyze restores a consistent BSMT state",
          bpy.ops.bsmt.analyse_repair() == {'FINISHED'}
          and topology(obj).get("nonmanifold_edge_count") == 1
          and len(props.repair_nonmanifold_defects) == 1)
    meshrepair.discard_backup(backup)

    # ================================================================== M ==
    print("\nM. the panel draws every state of this workflow")
    from body_surface_measurement import panels

    obj, _source = build(name="Panel")
    bpy.ops.bsmt.analyse_repair()
    log = draw_panel(panels.BSMT_PT_repair, context)
    buttons = drawn_operators(log)
    check("M1: the panel drew something", len(log) > 10, len(log))
    check("M2: Show Edges, Focus and Clear Highlight are all there",
          all(name in buttons for name in ("bsmt.show_non_manifold",
                                           "bsmt.focus_non_manifold",
                                           "bsmt.clear_repair_highlight")),
          buttons)
    check("M3: the defect stepper is drawn",
          "bsmt.step_nonmanifold_defect" in buttons, buttons)
    check("M4: Preview Artifact is offered",
          "bsmt.preview_artifact_component" in buttons)
    check("M5: Delete Artifact is offered on a deletable component",
          "bsmt.delete_defect_component" in buttons)
    text = "\n".join(drawn_texts(log))
    check("M6: the panel names the component and its share",
          "Component 2:" in text and "%" in text, text[-400:])
    check("M7: and says the decision is the researcher's",
          "anatomically" in text and "you do." in text, text[-400:])

    print("\n   with the defect on the body, the button is not drawn at all")
    obj, _source = build(name="PanelBlocked", fragments=0, defect_on_body=True)
    bpy.ops.bsmt.analyse_repair()
    log = draw_panel(panels.BSMT_PT_repair, context)
    buttons = drawn_operators(log)
    text = "\n".join(drawn_texts(log))
    check("M8: Delete Artifact is absent",
          "bsmt.delete_defect_component" not in buttons, buttons)
    check("M9: Preview Artifact is still offered, so the researcher can look",
          "bsmt.preview_artifact_component" in buttons)
    check("M10: and the panel says deletion is blocked",
          "Deletion blocked" in text, text[-400:])
    check("M11: naming the primary body component",
          "primary body component" in text)

    print("\n   after a deletion, and on a mesh with no defect at all")
    obj, _source = build(name="PanelAfter")
    bpy.ops.bsmt.analyse_repair()
    bpy.ops.bsmt.delete_defect_component()
    log = draw_panel(panels.BSMT_PT_repair, context)
    buttons = drawn_operators(log)
    text = "\n".join(drawn_texts(log))
    check("M12: the panel still draws", len(log) > 10, len(log))
    check("M13: no defect controls are left behind",
          "bsmt.step_nonmanifold_defect" not in buttons
          and "bsmt.delete_defect_component" not in buttons
          and "bsmt.preview_artifact_component" not in buttons, buttons)
    check("M14: and the diagnostics now read zero non-manifold edges",
          "Non-manifold edges   0" in text, text[-500:])

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
        print("BSMT_ARTIFACT_DELETION_RESULT=%d" % code)
