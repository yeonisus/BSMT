"""Acceptance tests for Scan Preprocessing v1 (Milestone 3.15).

    /path/to/blender -b --factory-startup --python tests/test_preprocess_blender.py

Cannot run offline. Everything asserted here is about what Blender's decimate
modifier actually does to real datablocks - whether a colour attribute
survives a collapse, whether a material slot outlives the faces that used it,
whether the source is genuinely untouched - and a stub cannot answer any of
it. The pure arithmetic and the classification rules are in
tests/test_preprocess.py.

Scenarios A-D come straight from the milestone brief: a dense clean mesh, a
PLY-like coloured mesh, a textured OBJ-like mesh, and a non-manifold input.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_preprocess_blender.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_preprocess_blender.py")
    raise SystemExit(0)

import bmesh  # noqa: E402

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def wipe():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def activate(obj):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def sphere(segments=64, rings=32, name="Scan"):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings)
    obj = bpy.context.object
    obj.name = name
    obj.data.name = name + "_Mesh"
    return obj


def add_color(obj, name="Col", domain='POINT', kind='BYTE_COLOR'):
    """A per-vertex colour, as a PLY scan carries it."""
    layer = obj.data.color_attributes.new(name=name, type=kind, domain=domain)
    for index, datum in enumerate(layer.data):
        datum.color = ((index % 97) / 97.0, 0.25, 0.75, 1.0)
    return layer


def add_texture(obj, material_name="ScanMat", image_name="scan_diffuse"):
    """A UV + material + image texture, as a textured OBJ carries it."""
    material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    image = bpy.data.images.new(image_name, 16, 16)
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    obj.data.materials.append(material)
    return material, image


def make_non_manifold(obj):
    """Add a third face along one existing edge. Deliberately unmeasurable."""
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    edge = next(e for e in bm.edges if len(e.link_faces) == 2)
    spur = bm.verts.new((10.0, 10.0, 10.0))
    bm.faces.new((edge.verts[0], edge.verts[1], spur))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return obj


def snapshot(obj):
    """Enough of a source's state to prove it was never modified."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    return {
        "object_name": obj.name,
        "mesh_name": mesh.name,
        "mesh_id": mesh.as_pointer(),
        "vertices": len(mesh.vertices),
        "polygons": len(mesh.polygons),
        "triangles": len(mesh.loop_triangles),
        "uv": [layer.name for layer in mesh.uv_layers],
        "colors": [(c.name, c.domain, c.data_type)
                   for c in mesh.color_attributes],
        "materials": [s.material.name for s in obj.material_slots if s.material],
    }


def check_source_unchanged(before, obj, label):
    after = snapshot(obj)
    check("%s: the source object is untouched" % label, before == after,
          "%r -> %r" % (before, after))


# ---------------------------------------------------------------------------

def main():
    import body_surface_measurement as bsmt
    bsmt.register()
    from body_surface_measurement import (preprocess, scancopy, state)

    context = bpy.context
    props = context.scene.bsmt

    # ------------------------------------------------------------------ A --
    print("\nA. dense clean mesh -> target 350k")
    wipe()
    # 1,046,528 triangles: above the operational density threshold on purpose.
    dense = activate(sphere(segments=1024, rings=512, name="DenseScan"))
    before = snapshot(dense)
    check("the fixture really is dense (%s triangles)"
          % "{:,}".format(before["triangles"]),
          before["triangles"] > 1000000, before["triangles"])

    info = scancopy.describe(dense)
    check("the panel reports it as not analysed before Analyze Scan",
          not info["analysed"])

    props.preprocess_target_triangles = 350000
    started = time.perf_counter()
    result = bpy.ops.bsmt.create_measurement_copy()
    elapsed = time.perf_counter() - started
    check("preprocessing finished", result == {'FINISHED'}, str(result))
    print("      (%.1f s)" % elapsed)

    copy = bpy.data.objects.get(props.preprocess_copy_name)
    check("a measurement mesh was created", copy is not None,
          props.preprocess_copy_name)
    check("named from the source", copy.name == "DenseScan_BSMT", copy.name)
    check_source_unchanged(before, dense, "A")

    copy.data.calc_loop_triangles()
    actual = len(copy.data.loop_triangles)
    check("the copy is near the target (%s)" % "{:,}".format(actual),
          abs(actual - 350000) / 350000.0 < 0.05, actual)
    check("the copy is a different mesh datablock",
          copy.data.as_pointer() != dense.data.as_pointer())
    check("the copy's mesh has its own name",
          copy.data.name != dense.data.name,
          "%s vs %s" % (copy.data.name, dense.data.name))

    copy_info = scancopy.describe(copy)
    check("topology diagnostics are complete for the copy",
          copy_info["analysed"]
          and "component_count" in copy_info
          and "boundary_edge_count" in copy_info
          and "nonmanifold_edge_count" in copy_info, str(copy_info.keys()))
    check("and the before/after comparison is in the report",
          "Triangles" in props.preprocess_report
          and "Non-manifold edges" in props.preprocess_report)
    # NOT a check for READY. Measured on Blender 4.5.13: a UV sphere is
    # watertight at 224, 3,968 and 65,024 triangles but reports 40 BOUNDARY
    # EDGES at 1,046,528 - in the SOURCE, before anything is decimated. So
    # WARNING is the correct verdict here and the diagnostics are right; what
    # must be asserted is that the copy is usable and that decimation did not
    # make the topology worse.
    check("the copy is not NOT READY",
          props.preprocess_status != preprocess.MEASUREMENT_NOT_READY,
          "%s / %s" % (props.preprocess_status, props.preprocess_status_detail))
    before_report = state.geodesic.meshcache.peek(dense.name)
    check("any WARNING is explained by something really in the diagnostics",
          props.preprocess_status == preprocess.MEASUREMENT_READY
          or copy_info["boundary_edge_count"] > 0
          or copy_info["component_count"] > 1,
          "%s / %s" % (props.preprocess_status, props.preprocess_status_detail))
    check("decimation introduced no non-manifold edges",
          copy_info["nonmanifold_edge_count"] == 0,
          copy_info["nonmanifold_edge_count"])
    check("and no degenerate triangles",
          copy_info["degenerate_triangle_count"] == 0,
          copy_info["degenerate_triangle_count"])
    check("timings were recorded",
          props.preprocess_seconds > 0.0
          and props.preprocess_diagnostic_seconds > 0.0,
          "%s / %s" % (props.preprocess_seconds,
                       props.preprocess_diagnostic_seconds))

    print("\n   provenance")
    provenance = copy.bsmt_scan
    check("marked as a measurement mesh", provenance.is_measurement_copy)
    check("the source is a real object pointer, not just a name",
          provenance.source is dense)
    check("source name recorded", provenance.source_name == "DenseScan")
    check("source mesh name recorded",
          provenance.source_mesh_name == before["mesh_name"])
    check("original triangle count recorded",
          provenance.original_triangles == before["triangles"])
    check("target triangle count recorded",
          provenance.target_triangles == 350000)
    check("actual triangle count recorded",
          provenance.actual_triangles == actual)
    check("method recorded",
          provenance.method == preprocess.METHOD_DECIMATE, provenance.method)
    check("ratio recorded and plausible",
          0.0 < provenance.ratio < 1.0, provenance.ratio)
    check("BSMT version recorded", bool(provenance.bsmt_version))
    check("creation time recorded", bool(provenance.created))
    check("and the representation is honest about decimation",
          "not" in provenance.representation.lower()
          or "Decimated" in provenance.representation,
          provenance.representation)

    print("\n   the source stays reachable and nothing is hidden away")
    activate(copy)
    check("the pair resolves from the copy",
          scancopy.resolve_source(copy) is dense)
    check("and from the source", scancopy.find_measurement_copy(dense) is copy)
    bpy.ops.bsmt.show_scan(which='SOURCE')
    check("Show Source shows the source",
          not dense.hide_viewport and copy.hide_viewport)
    bpy.ops.bsmt.show_scan(which='COPY')
    check("Show Measurement Mesh shows the copy",
          dense.hide_viewport and not copy.hide_viewport)
    bpy.ops.bsmt.show_scan(which='BOTH')
    check("Show Both shows both",
          not dense.hide_viewport and not copy.hide_viewport)
    check("the source object still exists and is still linked",
          dense.name in bpy.data.objects and dense.users_collection)

    # ------------------------------------------------------------------ B --
    print("\nB. PLY-like coloured mesh -> colour attribute survives")
    wipe()
    ply = activate(sphere(segments=128, rings=64, name="PlyScan"))
    while ply.data.uv_layers:
        ply.data.uv_layers.remove(ply.data.uv_layers[0])
    add_color(ply, "Col")
    before = snapshot(ply)
    check("the fixture is colour-only (no UV, no material)",
          not before["uv"] and not before["materials"] and before["colors"],
          str(before))

    props.preprocess_target_triangles = 4000
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing finished", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects[props.preprocess_copy_name]
    check_source_unchanged(before, ply, "B")

    names = [c.name for c in copy.data.color_attributes]
    check("the colour attribute survived decimation", "Col" in names, names)
    domains = {c.name: (c.domain, c.data_type) for c in copy.data.color_attributes}
    check("with its domain and type intact",
          domains.get("Col") == ("POINT", "BYTE_COLOR"), str(domains))
    values = [tuple(d.color) for d in copy.data.color_attributes["Col"].data[:8]]
    check("and real colour values, not defaults",
          any(abs(v[0] - 1.0) > 1e-6 for v in values), str(values[:2]))
    check("the report says the colour was preserved",
          "color attribute(s) preserved" in props.preprocess_report)
    check("the copy is MEASUREMENT READY",
          props.preprocess_status == preprocess.MEASUREMENT_READY,
          props.preprocess_status_detail)
    check("and the panel would show the colour attribute",
          scancopy.describe(copy)["has_color"])

    # ------------------------------------------------------------------ C --
    print("\nC. textured OBJ-like mesh -> UV, material and image survive")
    wipe()
    obj_like = activate(sphere(segments=128, rings=64, name="ObjScan"))
    material, image = add_texture(obj_like)
    before = snapshot(obj_like)
    check("the fixture has a UV map", before["uv"] == ["UVMap"], str(before["uv"]))
    check("and a material", before["materials"] == ["ScanMat"])

    props.preprocess_target_triangles = 4000
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing finished", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects[props.preprocess_copy_name]
    check_source_unchanged(before, obj_like, "C")

    check("the UV layer survived",
          [l.name for l in copy.data.uv_layers] == ["UVMap"],
          str([l.name for l in copy.data.uv_layers]))
    check("the material slot survived",
          [s.material.name for s in copy.material_slots if s.material]
          == ["ScanMat"])
    check("the material datablock is SHARED, not duplicated",
          copy.material_slots[0].material is material,
          copy.material_slots[0].material.name)
    facts = scancopy.audit_object(copy)
    check("the image texture is still referenced",
          "scan_diffuse" in facts["images"], str(facts["images"]))
    check("and its file path is recorded",
          len(facts["image_paths"]) == len(facts["images"]))
    check("the source material was not modified",
          material.name == "ScanMat" and material.use_nodes)
    check("no new image datablock was created (no baking)",
          len([i for i in bpy.data.images if i.name.startswith("scan_diffuse")])
          == 1, [i.name for i in bpy.data.images])
    check("the copy is MEASUREMENT READY",
          props.preprocess_status == preprocess.MEASUREMENT_READY,
          props.preprocess_status_detail)

    # ------------------------------------------------------------------ D --
    print("\nD. non-manifold input -> copy allowed, verdict NOT READY")
    wipe()
    bad = activate(sphere(segments=64, rings=32, name="BadScan"))
    make_non_manifold(bad)
    before = snapshot(bad)

    props.preprocess_target_triangles = 1500
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing is ALLOWED on non-manifold input",
          result == {'FINISHED'}, str(result))
    copy = bpy.data.objects.get(props.preprocess_copy_name)
    check("a copy was still created", copy is not None)
    check_source_unchanged(before, bad, "D")
    check("the verdict is NOT READY",
          props.preprocess_status == preprocess.MEASUREMENT_NOT_READY,
          "%s / %s" % (props.preprocess_status, props.preprocess_status_detail))
    check("and it names non-manifold topology",
          "non-manifold" in props.preprocess_status_detail.lower(),
          props.preprocess_status_detail)
    check("the report still shows before/after diagnostics",
          "Non-manifold edges" in props.preprocess_report)

    print("\n   and the solver gate is not weakened")
    canonical = state.geodesic.meshcache.get(context, copy, props.unit,
                                             rebuild=True)
    gate = preprocess.preflight(canonical.topology,
                                props.dense_threshold_triangles, True)
    if int(canonical.topology.get("nonmanifold_edge_count", 0)):
        check("a non-manifold copy is still refused by the gate",
              not gate["allowed"], str(gate["refusals"]))
    else:
        check("decimation happened to remove the non-manifold edge; the gate "
              "judges the copy on its own topology", gate["allowed"])

    # ------------------------------------------------------ target >= current
    print("\nE. a target at or above the current count does not add geometry")
    wipe()
    small = activate(sphere(segments=16, rings=8, name="SmallScan"))
    before = snapshot(small)
    props.preprocess_target_triangles = 500000
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing finished", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects[props.preprocess_copy_name]
    copy.data.calc_loop_triangles()
    check_source_unchanged(before, small, "E")
    check("the copy has exactly the source's triangle count",
          len(copy.data.loop_triangles) == before["triangles"],
          "%d vs %d" % (len(copy.data.loop_triangles), before["triangles"]))
    check("and the method says it was copied, not decimated",
          copy.bsmt_scan.method == preprocess.METHOD_COPY,
          copy.bsmt_scan.method)
    check("the report says no reduction was needed",
          "not below the current" in props.preprocess_report)

    # ------------------------------------------------------ deterministic name
    print("\nF. deterministic naming when the name is taken")
    wipe()
    first = sphere(segments=16, rings=8, name="Twin")
    second = sphere(segments=16, rings=8, name="TwinB")
    second.name = "Twin"          # Blender uniquifies to Twin.001
    copies = [scancopy.duplicate(first), scancopy.duplicate(first),
              scancopy.duplicate(first)]
    got = [c.name for c in copies]
    check("the first copy takes the plain name", got[0] == "Twin_BSMT", got)
    check("and later ones get .001, .002 in order",
          got[1] == "Twin_BSMT.001" and got[2] == "Twin_BSMT.002", got)
    check("every copy has its own mesh datablock",
          len({c.data.as_pointer() for c in copies}) == 3)
    check("and none of them is the source's mesh",
          all(c.data.as_pointer() != first.data.as_pointer() for c in copies))

    # ------------------------------------------------- no landmark transfer --
    print("\nG. landmarks are never transferred to the copy")
    wipe()
    scanned = activate(sphere(segments=32, rings=16, name="LandmarkedScan"))
    canonical = state.geodesic.meshcache.get(context, scanned, props.unit)
    landmarks = context.scene.bsmt_landmarks
    landmarks.clear()
    item = landmarks.add()
    item.stable_id = 1
    item.name = "L1"
    item.protocol_id = "P01"
    point = item.surface_point
    point.triangle_index = 10
    point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
    point.source_object = scanned.name
    point.geometry_hash = canonical.geometry_hash
    point.status = "VALID"
    point.valid = True

    props.preprocess_target_triangles = 300
    result = bpy.ops.bsmt.create_measurement_copy()
    check("preprocessing finished", result == {'FINISHED'}, str(result))
    copy = bpy.data.objects[props.preprocess_copy_name]
    check("the landmark still points at the SOURCE",
          landmarks[0].surface_point.source_object == "LandmarkedScan",
          landmarks[0].surface_point.source_object)
    check("no landmark was added, moved or re-projected",
          len(landmarks) == 1
          and landmarks[0].surface_point.triangle_index == 10)
    check("and the researcher is warned to re-pick on the copy",
          "NOT copied" in props.preprocess_report
          and "re-pick" in props.preprocess_report,
          props.preprocess_report[-400:])
    check("the copy carries no landmarks of its own",
          not [l for l in landmarks
               if l.surface_point.source_object == copy.name])

    # -------------------------------------------------- dense-mesh messaging -
    print("\nH. dense-mesh messaging is integrated, and the gate is intact")
    check("the operational threshold is unchanged",
          props.dense_threshold_triangles == preprocess.DEFAULT_DENSE_THRESHOLD,
          props.dense_threshold_triangles)
    check("and the density guard still defaults to on", props.guard_dense_solve)
    dense_gate = preprocess.preflight(
        {"triangle_count": 1200000, "nonmanifold_edge_count": 0},
        props.dense_threshold_triangles, True)
    check("a mesh above it is still refused a solve", not dense_gate["allowed"])
    panels_text = open(os.path.join(ROOT, "body_surface_measurement",
                                    "panels.py")).read()
    check("there is one canonical piece of dense-scan advice",
          "measurement mesh" in preprocess.DENSE_SCAN_ADVICE
          and "surface-path" in preprocess.DENSE_SCAN_ADVICE,
          preprocess.DENSE_SCAN_ADVICE)
    check("and the panel shows it rather than wording its own",
          "DENSE_SCAN_ADVICE" in panels_text)
    check("and calls the threshold operational, not mathematical",
          "not a mathematical limit" in panels_text)

    # ------------------------------------------------------- no repair yet ---
    print("\nI. no repair happened anywhere")
    for name in ("preprocess.py", "scancopy.py"):
        source = open(os.path.join(ROOT, "body_surface_measurement",
                                   name)).read().lower()
        for forbidden in ("remove_doubles", "merge_by_distance", "fill_holes",
                          "bridge_edge_loops", "remesh", "smooth"):
            check("%s never calls %s" % (name, forbidden),
                  forbidden not in source)

    # ---------------------------------------------------- the panel draws --
    print("\nJ. the Scan Preprocessing panel draws in every state")

    class FakeLayout(object):
        """Records what a panel asks for. Enough to run draw() headless.

        Panel draw code is ordinary Python and breaks like any other - a
        renamed helper, a missing argument - and Blender cannot build a real
        UILayout in background mode.
        """

        def __init__(self, log):
            self.log = log
            self.alert = False
            self.enabled = True
            self.scale_y = 1.0

        def _child(self, *args, **kwargs):
            return FakeLayout(self.log)

        box = row = column = split = _child

        def label(self, **kwargs):
            self.log.append(("label", kwargs.get("text", "")))

        def prop(self, *args, **kwargs):
            self.log.append(("prop", args[1] if len(args) > 1 else ""))

        def operator(self, idname, **kwargs):
            self.log.append(("operator", idname))
            return FakeLayout(self.log)

        def separator(self, *args, **kwargs):
            pass

    from body_surface_measurement import panels
    panel_class = panels.BSMT_PT_preprocessing

    def draw_panel():
        log = []
        # draw() is an ordinary function on the class; a bpy Panel cannot be
        # instantiated from Python, so it is called unbound with a stand-in.
        stub = type("Stub", (object,), {
            name: staticmethod(getattr(panel_class, name))
            for name in dir(panel_class) if name.startswith("_draw")
        })()
        stub.layout = FakeLayout(log)
        panel_class.draw(stub, context)
        return ([text for kind, text in log if kind == "label"],
                [name for kind, name in log if kind == "operator"])

    wipe()
    labels, operators = draw_panel()
    check("the panel draws with nothing selected", bool(labels), labels)

    scan = activate(sphere(segments=32, rings=16, name="PanelScan"))
    labels, operators = draw_panel()
    check("an unanalysed scan says so",
          any("not analyzed" in text for text in labels), labels)
    check("and offers the EXISTING diagnostics operator, not a new one",
          "bsmt.diagnose_topology" in operators, operators)
    check("the appearance rows include color attributes",
          any(text.startswith("Color attr") for text in labels), labels)
    check("and the compare buttons are present",
          operators.count("bsmt.show_scan") == 3, operators)

    state.geodesic.meshcache.get(context, scan, props.unit, rebuild=True)
    labels, _operators = draw_panel()
    for row in ("Components:", "Boundary edges:", "Non-manifold edges:",
                "Degenerate tris:", "Coincident verts:"):
        check("an analysed scan shows %s" % row,
              any(text.startswith(row) for text in labels), labels)

    props.preprocess_valid = True
    props.preprocess_report = "Status: X"
    for status, shown in ((preprocess.MEASUREMENT_READY, "MEASUREMENT READY"),
                          (preprocess.MEASUREMENT_WARNING, "WARNING"),
                          (preprocess.MEASUREMENT_NOT_READY, "NOT READY")):
        props.preprocess_status = status
        props.preprocess_status_detail = "the stated reason"
        labels, _operators = draw_panel()
        check("the %s verdict is shown in words" % status,
              any("Measurement Mesh: " + shown in text for text in labels),
              labels)
        check("  with its reason",
              any("the stated reason" in text for text in labels))

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
        print("BSMT_PREPROCESS_RESULT=%d" % code)
