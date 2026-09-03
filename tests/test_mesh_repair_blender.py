"""Acceptance tests for Mesh Repair v1 (Milestone 3.19).

    /path/to/blender -b --factory-startup --python tests/test_mesh_repair_blender.py

The fixture reproduces the real scan that motivated the milestone: one
connected component, no boundary edges, no non-manifold edges, exactly
coincident vertices, and degenerate triangles caused by those vertices being
collapsed onto each other. BSMT blocks exact measurement on it; the question
is whether a bounded local repair can make it measurable again without
welding anything the researcher did not ask for.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_mesh_repair_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_mesh_repair_blender.py")
    raise SystemExit(0)

import bmesh  # noqa: E402
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


def code_only(source):
    """Source with comments and docstrings removed.

    Assertions about what code DOES must not be satisfied or broken by what a
    comment SAYS - the first version of the checks below fired on their own
    explanatory prose, which names the very operators it promises not to call.
    """
    out = []
    in_doc = False
    for line in source.splitlines():
        stripped = line.strip()
        fences = stripped.count('"' * 3) + stripped.count("'" * 3)
        if in_doc:
            if fences:
                in_doc = False
            continue
        if fences == 1:
            in_doc = True
            continue
        if fences >= 2 or stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def snapshot(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return {
        "name": obj.name, "mesh": mesh.name,
        "vertices": len(mesh.vertices), "polygons": len(mesh.polygons),
        "triangles": len(mesh.loop_triangles),
    }


def collapse_isolated_edges(obj, pairs):
    """Collapse `pairs` well-separated edges by moving one end onto the other.

    This is the shape the real scan had: isolated pairs of exactly coincident
    vertices, each making the two triangles around its edge zero-area, with
    the edge and face topology otherwise untouched - so the mesh stays
    manifold, closed and single-component.

    Deliberately NOT "collapse N adjacent vertices onto one point": that is a
    fan collapse, it genuinely destroys the local topology, and no local
    merge can repair it. It has its own test below.
    """
    mesh = obj.data
    used = set()
    done = 0
    total_edges = len(mesh.edges)
    step = max(1, total_edges // (pairs * 4))
    for index in range(0, total_edges, step):
        first, second = mesh.edges[index].vertices
        if first in used or second in used:
            continue
        mesh.vertices[second].co = mesh.vertices[first].co
        used.update((first, second))
        done += 1
        if done >= pairs:
            break
    mesh.update()
    return done


def make_measurement_mesh(context, name="Scan", collapse=7):
    """A source scan plus a real measurement mesh made by preprocessing.

    Repair only ever runs on a measurement mesh, so the fixture has to be a
    genuine one - produced by the real operator, carrying real provenance.
    """
    from body_surface_measurement import preprocess
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24)
    source = context.object
    source.name = name
    source.data.name = name + "_Mesh"
    context.view_layer.objects.active = source

    props = context.scene.bsmt
    props.preprocess_target_triangles = 500000     # copy, do not decimate
    result = bpy.ops.bsmt.create_measurement_copy()
    copy = bpy.data.objects[props.preprocess_copy_name]

    if collapse:
        collapse_isolated_edges(copy, collapse)
    context.view_layer.objects.active = copy
    return source, copy, result


def topology_of(context, obj):
    from body_surface_measurement import geodesic
    canonical = geodesic.meshcache.get(context, obj, context.scene.bsmt.unit,
                                       rebuild=True)
    return dict(canonical.topology or {})


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, meshrepair, preprocess,
                                          repair, state, visualization)

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------------ A --
    print("\nA. the motivating defect: detect, locate, repair locally")
    source, copy, made = make_measurement_mesh(context)
    check("a real measurement mesh was produced", made == {'FINISHED'},
          str(made))
    source_before = snapshot(source)

    before = topology_of(context, copy)
    check("1 connected component", before["component_count"] == 1,
          before["component_count"])
    check("0 boundary edges", before["boundary_edge_count"] == 0,
          before["boundary_edge_count"])
    check("0 non-manifold edges", before["nonmanifold_edge_count"] == 0,
          before["nonmanifold_edge_count"])
    check("degenerate triangles present",
          before["degenerate_triangle_count"] > 0,
          before["degenerate_triangle_count"])
    check("exact coincident vertices present",
          before["duplicate_vertex_count"] > 0,
          before["duplicate_vertex_count"])

    verdict, reasons = preprocess.classify_ready(before)
    check("BSMT says NOT READY before repair",
          verdict == preprocess.MEASUREMENT_NOT_READY, verdict)
    gate = preprocess.preflight(before, props.dense_threshold_triangles, True)
    check("and the solver refuses", not gate["allowed"], gate["refusals"])

    print("\n   Analyze Repair Issues lists the defects")
    check("analysis ran", bpy.ops.bsmt.analyse_repair() == {'FINISHED'})
    listed = len(props.repair_degenerates)
    check("the degenerate triangles are listed",
          listed == before["degenerate_triangle_count"],
          "%d listed vs %d counted" % (listed,
                                       before["degenerate_triangle_count"]))
    check("each is classified", all(entry.kind for entry in
                                    props.repair_degenerates))
    check("and the collapsed ones are marked repairable",
          any(entry.repairable for entry in props.repair_degenerates))
    check("every defect carries a location",
          all(any(abs(v) > 0 for v in entry.centroid)
              for entry in props.repair_degenerates))

    print("\n   the defects can be located in the viewport")
    check("Show Degenerate Triangles ran",
          bpy.ops.bsmt.show_degenerate_triangles() == {'FINISHED'})
    marker = bpy.data.objects.get(visualization.REPAIR_DEGENERATE)
    check("a highlight helper exists", marker is not None)
    check("it is a BSMT helper", marker is not None
          and visualization.is_helper(marker))
    check("it is in the helper collection", marker is not None
          and any(c.name == visualization.COLLECTION_NAME
                  for c in marker.users_collection),
          [c.name for c in marker.users_collection] if marker else None)
    check("it is not selectable", marker is not None and marker.hide_select)
    check("and it has geometry to see", marker is not None
          and len(marker.data.vertices) > 0)
    check("the mesh itself was not modified by highlighting",
          snapshot(copy)["triangles"] == before["triangle_count"],
          snapshot(copy))

    print("\n   stepping through defects")
    if listed > 1:
        first = props.repair_degenerate_index
        bpy.ops.bsmt.step_degenerate_defect(direction='NEXT')
        check("Next moves the selection",
              props.repair_degenerate_index != first,
              props.repair_degenerate_index)
        bpy.ops.bsmt.step_degenerate_defect(direction='PREV')
        check("Previous moves it back",
              props.repair_degenerate_index == first,
              props.repair_degenerate_index)
    matrix_before = [list(row) for row in copy.matrix_world]
    bpy.ops.bsmt.focus_degenerate_defect()
    check("focusing never moves the scan",
          [list(row) for row in copy.matrix_world] == matrix_before)

    print("\n   preview says what will happen, and changes nothing")
    check("preview ran", bpy.ops.bsmt.preview_degenerate_repair()
          == {'FINISHED'})
    preview = props.repair_degenerate_preview
    check("it describes merges and removals",
          "merge" in preview and "remove" in preview, preview)
    check("preview modified nothing",
          snapshot(copy)["triangles"] == before["triangle_count"])

    print("\n   apply the repair")
    props.repair_degenerate_scope = 'ALL_SAFE'
    applied = bpy.ops.bsmt.repair_degenerate_local()
    check("the repair ran", applied == {'FINISHED'}, str(applied))

    after = topology_of(context, copy)
    check("degenerate triangles are gone",
          after["degenerate_triangle_count"] == 0,
          after["degenerate_triangle_count"])
    check("no new non-manifold edges",
          after["nonmanifold_edge_count"] <= before["nonmanifold_edge_count"],
          (before["nonmanifold_edge_count"], after["nonmanifold_edge_count"]))
    check("no new boundary edges",
          after["boundary_edge_count"] <= before["boundary_edge_count"],
          (before["boundary_edge_count"], after["boundary_edge_count"]))
    check("no new components",
          after["component_count"] <= before["component_count"],
          (before["component_count"], after["component_count"]))
    check("the exact coincident vertices were merged",
          after["duplicate_vertex_count"] < before["duplicate_vertex_count"],
          (before["duplicate_vertex_count"], after["duplicate_vertex_count"]))

    print("\n   the verdict comes from the SAME policy as everywhere else")
    verdict, reasons = preprocess.classify_ready(after)
    check("classify_ready no longer blocks",
          verdict != preprocess.MEASUREMENT_NOT_READY, (verdict, reasons))
    gate = preprocess.preflight(after, props.dense_threshold_triangles, True)
    check("and the solver gate allows it", gate["allowed"], gate["refusals"])
    live = state.mesh_verdict(context, props, copy)
    check("mesh_verdict agrees", live["state"] == verdict,
          (live["state"], verdict))

    # ------------------------------------------------------------------ J --
    print("\nJ. the source scan is untouched throughout")
    check("the source is byte-for-byte as it was",
          snapshot(source) == source_before,
          (source_before, snapshot(source)))

    print("\n   provenance is appended, not overwritten")
    provenance = copy.bsmt_scan
    check("the preprocessing record survives",
          provenance.is_measurement_copy and provenance.source is source
          and provenance.original_triangles > 0)
    check("repair applied is recorded", provenance.repair_applied)
    check("with the repair type", provenance.repair_type
          == repair.DEGENERATE_COINCIDENT, provenance.repair_type)
    check("degenerate before/after recorded",
          provenance.repair_degenerate_before > 0
          and provenance.repair_degenerate_after == 0,
          (provenance.repair_degenerate_before,
           provenance.repair_degenerate_after))
    check("merged vertex count recorded", provenance.repair_merged_vertices > 0,
          provenance.repair_merged_vertices)
    check("removed face count recorded", provenance.repair_removed_faces > 0,
          provenance.repair_removed_faces)
    check("BSMT version recorded", bool(provenance.repair_version))
    check("and a timestamp", bool(provenance.repair_created))

    # ------------------------------------------------------------------ G --
    print("\nG. landmarks on a repaired mesh become STALE, never re-projected")
    source, copy, _made = make_measurement_mesh(context)
    canonical = geodesic.meshcache.get(context, copy, props.unit, rebuild=True)
    landmarks = context.scene.bsmt_landmarks
    landmarks.clear()
    item = landmarks.add()
    item.stable_id = 1
    item.name = "L1"
    item.protocol_id = "P01"
    item.status = "VALID"
    point = item.surface_point
    point.triangle_index = 200
    point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
    point.source_object = copy.name
    point.geometry_hash = canonical.geometry_hash
    point.status = "VALID"
    point.valid = True
    before_triangle = point.triangle_index

    bpy.ops.bsmt.analyse_repair()
    props.repair_degenerate_scope = 'ALL_SAFE'
    check("repair ran with a landmark present",
          bpy.ops.bsmt.repair_degenerate_local() == {'FINISHED'})
    check("the landmark is no longer VALID",
          landmarks[0].status != "VALID", landmarks[0].status)
    check("it was NOT re-projected",
          landmarks[0].surface_point.triangle_index == before_triangle,
          landmarks[0].surface_point.triangle_index)
    check("and it still points at the same object",
          landmarks[0].surface_point.source_object == copy.name)
    panel_source = open(os.path.join(ROOT, "body_surface_measurement",
                                     "panels.py")).read()
    check("the panel warns before repairing",
          "will invalidate existing " in panel_source
          and "landmark positions." in panel_source)

    # ------------------------------------------------------------------ B --
    print("\nB. a coincident vertex not in a degenerate face is left alone")
    source, copy, _made = make_measurement_mesh(context, collapse=0)
    mesh = copy.data
    mesh.vertices.add(1)
    mesh.vertices[-1].co = mesh.vertices[0].co     # loose duplicate, no face
    mesh.update()
    report = topology_of(context, copy)
    check("the fixture has a coincident vertex",
          report["duplicate_vertex_count"] > 0,
          report["duplicate_vertex_count"])
    check("and no degenerate triangle",
          report["degenerate_triangle_count"] == 0,
          report["degenerate_triangle_count"])
    bpy.ops.bsmt.analyse_repair()
    check("nothing is listed as a degenerate defect",
          len(props.repair_degenerates) == 0, len(props.repair_degenerates))
    before_verts = len(copy.data.vertices)
    outcome = bpy.ops.bsmt.preview_degenerate_repair()
    check("preview refuses to invent a repair",
          "no degenerate" in props.repair_degenerate_preview.lower(),
          props.repair_degenerate_preview)
    try:
        bpy.ops.bsmt.repair_degenerate_local()
    except RuntimeError:
        pass
    check("no vertex was merged",
          len(copy.data.vertices) == before_verts,
          (before_verts, len(copy.data.vertices)))
    check("the verdict is not NOT READY for coincidence alone",
          preprocess.classify_ready(report)[0]
          != preprocess.MEASUREMENT_NOT_READY)

    # ------------------------------------------------------------------ F --
    print("\nF. a sliver is refused rather than guessed at")
    source, copy, _made = make_measurement_mesh(context, collapse=0)
    bm = bmesh.new()
    bm.from_mesh(copy.data)
    bm.verts.ensure_lookup_table()
    # Three DISTINCT but exactly collinear positions: a real sliver, not a
    # collapse. The coordinates are chosen to be exact in float32, because
    # BSMT's degenerate threshold is (1e-9 x bbox diagonal)^2 - it catches
    # triangles that are exactly flat, not merely thin. Using a midpoint of
    # two arbitrary sphere vertices gives an area around 7e-09, which is
    # correctly NOT degenerate under that rule.
    v1 = bm.verts.new((10.0, 0.0, 0.0))
    v2 = bm.verts.new((14.0, 0.0, 0.0))
    v3 = bm.verts.new((12.0, 0.0, 0.0))
    bm.faces.new((v1, v2, v3))
    bm.to_mesh(copy.data)
    bm.free()
    copy.data.update()
    report = topology_of(context, copy)
    check("the fixture has a degenerate sliver",
          report["degenerate_triangle_count"] > 0,
          report["degenerate_triangle_count"])
    bpy.ops.bsmt.analyse_repair()
    slivers = [entry for entry in props.repair_degenerates
               if not entry.repairable]
    check("it is listed as not locally repairable", bool(slivers),
          [(e.kind, e.repairable) for e in props.repair_degenerates])
    props.repair_degenerate_scope = 'SELECTED'
    for index, entry in enumerate(props.repair_degenerates):
        if not entry.repairable:
            props.repair_degenerate_index = index
            break
    bpy.ops.bsmt.preview_degenerate_repair()
    check("the preview refuses it in words",
          "not safe" in props.repair_degenerate_preview.lower(),
          props.repair_degenerate_preview)
    before_verts = len(copy.data.vertices)
    try:
        bpy.ops.bsmt.repair_degenerate_local()
    except RuntimeError:
        pass
    check("and nothing was changed",
          len(copy.data.vertices) == before_verts)

    # ---------------------------------------------------------------- D/E --
    print("\nD/E. no global weld, no hole filling in this repair path")
    module_source = open(os.path.join(ROOT, "body_surface_measurement",
                                      "meshrepair.py")).read()
    start = module_source.index("def apply_degenerate_plan")
    body = code_only(module_source[start:])
    for forbidden in ("remove_doubles", "dissolve_degenerate", "fill_holes",
                      "holes_fill", "triangle_fill", "automerge"):
        check("apply_degenerate_plan never calls %s" % forbidden,
              forbidden not in body, body[:120])
    check("it welds an explicit targetmap instead",
          "weld_verts" in body and "targetmap" in body)
    check("and takes no distance or tolerance argument",
          "dist=" not in body and "tolerance" not in body, body[:120])
    check("the pure planner has no tolerance either",
          "tolerance" not in code_only(
              open(os.path.join(ROOT, "body_surface_measurement",
                                "repair.py")).read()
              [open(os.path.join(ROOT, "body_surface_measurement",
                                 "repair.py")).read()
               .index("def plan_degenerate_repair"):]))

    print("\n   highlights are removable")
    source, copy, _made = make_measurement_mesh(context)
    bpy.ops.bsmt.analyse_repair()
    bpy.ops.bsmt.show_degenerate_triangles()
    check("a highlight exists",
          bpy.data.objects.get(visualization.REPAIR_DEGENERATE) is not None)
    bpy.ops.bsmt.clear_repair_highlight()
    check("Clear Repair Highlights removes it",
          bpy.data.objects.get(visualization.REPAIR_DEGENERATE) is None)

    # ------------------------------------------------- the validity guard --
    print("\nK. a repair that would make the topology worse is refused")
    # A FAN collapse - many adjacent vertices onto one point - is genuinely
    # unrepairable by a local merge: welding them makes edges shared by three
    # faces. The guard must catch that and put the mesh back.
    source, copy, _made = make_measurement_mesh(context, collapse=0)
    for index in range(1, 15):
        copy.data.vertices[index].co = copy.data.vertices[0].co
    copy.data.update()
    before = topology_of(context, copy)
    check("the fan fixture is degenerate",
          before["degenerate_triangle_count"] > 0,
          before["degenerate_triangle_count"])
    check("and manifold to start with",
          before["nonmanifold_edge_count"] == 0)
    bpy.ops.bsmt.analyse_repair()
    props.repair_degenerate_scope = 'ALL_SAFE'
    snapshot_before = snapshot(copy)
    try:
        outcome = bpy.ops.bsmt.repair_degenerate_local()
    except RuntimeError:
        outcome = {'CANCELLED'}
    check("the repair is refused", outcome != {'FINISHED'}, str(outcome))
    check("and it says the topology would get worse",
          "worse" in props.repair_degenerate_preview.lower(),
          props.repair_degenerate_preview)
    restored = topology_of(context, copy)
    check("the mesh was restored, not left half-repaired",
          restored["nonmanifold_edge_count"] == 0
          and restored["degenerate_triangle_count"]
          == before["degenerate_triangle_count"],
          (before, restored))
    check("its counts are exactly as they were",
          snapshot(copy)["triangles"] == snapshot_before["triangles"],
          (snapshot_before, snapshot(copy)))

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
        print("BSMT_MESH_REPAIR_RESULT=%d" % code)
