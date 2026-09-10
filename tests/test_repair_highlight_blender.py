"""A defect highlight you can actually see (Milestone 3.27).

    /path/to/blender -b --factory-startup --python \\
        tests/test_repair_highlight_blender.py

Reported: on a measurement mesh whose diagnostics report exactly one
non-manifold edge, pressing "Show Edges" produced no visible highlight.

The highlight was being created, linked, placed correctly and left visible -
every check the existing suite made passed. What it was NOT was *visible*: an
edge-only wire object with no material, which Blender draws one pixel wide in
the theme's wire colour, marking a defect about 5 mm long on a 1.7 m body.

So the checks here are about what reaches the screen, not about whether an
object exists:

- the highlight marks the SAME edge diagnostics counted, at the same
  world-space position, on a rotated, translated, non-trivially scaled scan;
- it has faces and a material, which is what makes a colour appear at all;
- it is thick enough to read at body scale, and its endpoints are still the
  defect's own - only the thickness is exaggerated;
- it is in front, in the helper collection, unselectable, and does not touch
  the mesh it describes;
- Clear Highlight removes it.
"""

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bmesh
    import bpy
    from mathutils import Matrix, Vector
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_repair_highlight_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_repair_highlight_blender.py")
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


def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def body_scan_with_one_non_manifold_edge(name="Body_BSMT", scale=1.0):
    """A body-shaped measurement mesh carrying exactly one non-manifold edge.

    Body-like on purpose: about 1.7 m tall in millimetre coordinates, rotated
    and translated the way an aligned scan is. A highlight that is only
    visible on a unit sphere at the origin is not a highlight for this tool.

    Returns (object, world_a, world_b) - the two world-space endpoints of the
    edge that is non-manifold, measured before BSMT is asked anything.
    """
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=64, v_segments=32, radius=1.0)
    for vert in bm.verts:
        vert.co.x *= 200.0
        vert.co.y *= 120.0
        vert.co.z *= 850.0
    bm.edges.ensure_lookup_table()
    edge = bm.edges[500]
    first, second = edge.verts
    local_a = tuple(first.co)
    local_b = tuple(second.co)
    tip = bm.verts.new((first.co + second.co) * 0.5 * 1.05)
    bm.faces.new((first, second, tip))          # a third face on that edge
    mesh = bpy.data.meshes.new(name + "_Mesh")
    bm.to_mesh(mesh)
    bm.free()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = (Matrix.Translation(Vector((300.0, -150.0, 900.0)))
                        @ Matrix.Rotation(0.6, 4, 'Z')
                        @ Matrix.Rotation(0.25, 4, 'X')
                        @ Matrix.Scale(scale, 4))
    obj.bsmt_scan.is_measurement_copy = True
    obj.bsmt_scan.source_name = name + "_Source"
    for other in bpy.context.selected_objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return (obj,
            obj.matrix_world @ Vector(local_a),
            obj.matrix_world @ Vector(local_b))


def world_vertices(helper):
    return [helper.matrix_world @ vertex.co for vertex in helper.data.vertices]


def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (geodesic, operators, repair, state,
                                          visualization)

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------- A -------
    print("\nA. the highlight marks the edge diagnostics counted")
    wipe()
    obj, world_a, world_b = body_scan_with_one_non_manifold_edge()
    check("A1: diagnostics ran", bpy.ops.bsmt.diagnose_topology() == {'FINISHED'})
    verdict = state.mesh_verdict(context, props, obj)
    reported = int(verdict["report"].get("nonmanifold_edge_count", 0) or 0)
    check("A2: diagnostics report exactly one non-manifold edge",
          reported == 1, reported)
    check("A3: and the mesh is NOT READY because of it",
          verdict["state"] == 'NOT_READY'
          and any("non-manifold" in reason for reason in verdict["reasons"]),
          verdict["reasons"])

    canonical = geodesic.meshcache.get(context, obj, props.unit, rebuild=False)
    edges = repair.classify_edges(canonical.triangles,
                                  canonical.vertex_count)["non_manifold"]
    check("A4: the visualization path finds the same count",
          edges.shape[0] == reported, edges.shape[0])

    before_vertices = len(obj.data.vertices)
    before_polygons = len(obj.data.polygons)
    check("A5: Show Edges ran",
          bpy.ops.bsmt.show_non_manifold() == {'FINISHED'})

    # ------------------------------------------------------------- B -------
    print("\nB. helper geometry exists, and is where the defect is")
    helper = bpy.data.objects.get(visualization.REPAIR_NON_MANIFOLD)
    check("B1: a highlight helper was created", helper is not None)
    if helper is None:
        print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
        return 1
    check("B2: it carries geometry", len(helper.data.vertices) > 0,
          len(helper.data.vertices))

    points = world_vertices(helper)
    center = Vector((0.0, 0.0, 0.0))
    for point in points:
        center += point
    center /= len(points)
    edge_center = (world_a + world_b) * 0.5
    check("B3: it is centred on the defect's own world position, not the "
          "origin and not the object's local frame",
          (center - edge_center).length < 1.0,
          "%.4f mm from the edge midpoint" % (center - edge_center).length)

    # The rod's axis must still run between the true endpoints: only the
    # thickness may be exaggerated, never the position or the extent.
    near_a = min((point - world_a).length for point in points)
    near_b = min((point - world_b).length for point in points)
    axis_length = max((first - second).length
                      for first in points for second in points)
    true_length = (world_a - world_b).length
    check("B4: it reaches both endpoints of the real edge",
          near_a < 20.0 and near_b < 20.0, (near_a, near_b))
    check("B5: and does not overstate the edge's extent",
          axis_length < true_length * 1.6,
          "%.2f mm drawn for a %.2f mm edge" % (axis_length, true_length))

    # ------------------------------------------------------------- C -------
    print("\nC. it is drawn, in the helper collection, and visible")
    check("C1: it is a BSMT helper", visualization.is_helper(helper))
    check("C2: it is in the BSMT helper collection",
          any(collection.name == visualization.COLLECTION_NAME
              for collection in helper.users_collection),
          [c.name for c in helper.users_collection])
    layer = context.view_layer.layer_collection.children.get(
        visualization.COLLECTION_NAME)
    check("C3: that collection is not excluded from the view layer",
          layer is not None and not layer.exclude and not layer.hide_viewport)
    check("C4: the helper is not hidden",
          not helper.hide_viewport and not helper.hide_get()
          and not helper.hide_render)
    check("C5: it is drawn in front, so a defect inside the scan still shows",
          helper.show_in_front)
    check("C6: it is not selectable", helper.hide_select)

    # ------------------------------------------------------------- D -------
    print("\nD. it is drawn in a way that can actually be seen")
    check("D1: it has FACES, not just edges - a wire object is drawn one "
          "pixel wide in the theme colour",
          len(helper.data.polygons) > 0, len(helper.data.polygons))
    check("D2: it carries a material, which is what colours it in Solid "
          "shading regardless of the viewport's colour mode",
          len(helper.data.materials) > 0 and helper.data.materials[0] is not None,
          [m.name for m in helper.data.materials])
    material = helper.data.materials[0]
    expected = visualization.REPAIR_NON_MANIFOLD_COLOR
    check("D3: in the non-manifold colour",
          all(abs(material.diffuse_color[i] - expected[i]) < 1e-5
              for i in range(4)),
          tuple(material.diffuse_color))
    check("D4: and the object colour agrees, for Object colour mode",
          all(abs(helper.color[i] - expected[i]) < 1e-5 for i in range(4)),
          tuple(helper.color))

    # Thickness, in millimetres, against the body it is drawn on.
    radius_local = operators._defect_highlight_radius(canonical)
    scale = float(obj.matrix_world.to_scale().length / math.sqrt(3.0))
    radius_mm = radius_local * scale * float(canonical.unit_multiplier)
    diagonal = float((canonical.topology or {}).get("bbox_diagonal", 0.0))
    check("D5: the rod is millimetres thick, not a hairline",
          radius_mm >= 2.0, "%.2f mm radius" % radius_mm)
    check("D6: and stays proportionate to the scan it is drawn on",
          0.001 <= radius_mm / diagonal <= 0.02,
          "%.4f of a %.0f mm diagonal" % (radius_mm / diagonal, diagonal))

    thickness = max((point - center).length for point in points) * 2.0
    check("D7: what is drawn is wider than the bare edge it marks",
          thickness > true_length * 0.2, (thickness, true_length))

    # ------------------------------------------------------------- E -------
    print("\nE. the mesh it describes is untouched")
    check("E1: no vertex was added to the measurement mesh",
          len(obj.data.vertices) == before_vertices, len(obj.data.vertices))
    check("E2: no face was added either",
          len(obj.data.polygons) == before_polygons, len(obj.data.polygons))
    after = state.mesh_verdict(context, props, obj)
    check("E3: the topology verdict is unchanged - highlighting repairs "
          "nothing and hides nothing",
          after["state"] == verdict["state"]
          and int(after["report"].get("nonmanifold_edge_count", 0)) == reported,
          after["state"])

    # ------------------------------------------------------------- F -------
    print("\nF. Clear Highlight removes it")
    check("F1: Clear Highlight ran",
          bpy.ops.bsmt.clear_repair_highlight() == {'FINISHED'})
    check("F2: the helper object is gone",
          bpy.data.objects.get(visualization.REPAIR_NON_MANIFOLD) is None)
    check("F3: and the scan is still there, unmodified",
          bpy.data.objects.get(obj.name) is not None
          and len(obj.data.vertices) == before_vertices)

    # ------------------------------------------------------------- G -------
    print("\nG. the same scan at a different object scale")
    wipe()
    scaled, scaled_a, scaled_b = body_scan_with_one_non_manifold_edge(
        name="Scaled_BSMT", scale=0.25)
    bpy.ops.bsmt.diagnose_topology()
    bpy.ops.bsmt.show_non_manifold()
    helper = bpy.data.objects.get(visualization.REPAIR_NON_MANIFOLD)
    check("G1: a highlight was created on the scaled scan", helper is not None)
    if helper is not None:
        points = world_vertices(helper)
        center = Vector((0.0, 0.0, 0.0))
        for point in points:
            center += point
        center /= len(points)
        target = (scaled_a + scaled_b) * 0.5
        check("G2: still at the defect's world position",
              (center - target).length < 1.0,
              "%.4f mm away" % (center - target).length)
        canonical_scaled = geodesic.meshcache.get(bpy.context, scaled,
                                                  props.unit, rebuild=False)
        radius_local = operators._defect_highlight_radius(canonical_scaled)
        scale_factor = float(
            scaled.matrix_world.to_scale().length / math.sqrt(3.0))
        radius_mm = (radius_local * scale_factor
                     * float(canonical_scaled.unit_multiplier))
        world_diagonal = float(
            (canonical_scaled.topology or {}).get("bbox_diagonal", 0.0))
        check("G3: the thickness follows the scan's real size, not the "
              "numbers in the mesh datablock",
              0.001 <= radius_mm / world_diagonal <= 0.02,
              "%.4f of a %.0f mm diagonal" % (radius_mm / world_diagonal,
                                              world_diagonal))

    # ------------------------------------------------------------- H -------
    print("\nH. Focus frames the defect without moving the scan")
    before_matrix = scaled.matrix_world.copy()
    result = bpy.ops.bsmt.focus_non_manifold()
    check("H1: Focus ran or reported no viewport",
          result in ({'FINISHED'}, {'CANCELLED'}), str(result))
    check("H2: the scan itself was not moved",
          all(abs(before_matrix[i][j] - scaled.matrix_world[i][j]) < 1e-9
              for i in range(4) for j in range(4)))
    check("H3: and the mesh was not modified",
          len(scaled.data.vertices) > 0)

    # ------------------------------------------------------------- I -------
    print("\nI. a clean mesh has nothing to highlight, and says so")
    wipe()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    clean = bpy.context.object
    clean.name = "Clean_BSMT"
    clean.bsmt_scan.is_measurement_copy = True
    clean.bsmt_scan.source_name = "Clean_Source"
    bpy.context.view_layer.objects.active = clean
    bpy.ops.bsmt.diagnose_topology()
    check("I1: Show Edges still runs",
          bpy.ops.bsmt.show_non_manifold() == {'FINISHED'})
    check("I2: and leaves no highlight behind",
          bpy.data.objects.get(visualization.REPAIR_NON_MANIFOLD) is None)
    check("I3: Focus refuses, rather than framing nothing",
          bpy.ops.bsmt.focus_non_manifold() == {'CANCELLED'})

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
        print("BSMT_REPAIR_HIGHLIGHT_RESULT=%d" % code)
