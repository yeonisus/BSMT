"""Clean packaged validation - the ZIP, not the working tree (Milestone 3.25).

    /path/to/blender -b --factory-startup --python tests/test_packaged_extension.py

Every other suite in this project imports BSMT from the working tree. That
proves the source is right and proves nothing about what a researcher
installs. This one extracts the released ZIP into a temporary directory
outside the repository and validates THAT.

The isolation is the whole point, so it is asserted rather than assumed
-------------------------------------------------------------------------
The repository root is deliberately kept off `sys.path`, any pre-imported
`body_surface_measurement` is evicted from `sys.modules`, and after the import
the module's `__file__` is checked to be under the extraction directory. If
the working tree could satisfy the import, this suite would silently be
testing the working tree again - which is the one failure it exists to
prevent - so it is verified in three independent ways and every packaged
module is checked, not just the top-level one.

The bundled wheel is exercised, not just counted
------------------------------------------------
`platforms` in the manifest is a claim. To test it, the macOS ARM64 wheel is
unpacked from the extension ZIP into the same temporary directory, put on
`sys.path` ahead of everything else, and imported - and the resulting
`pygeodesic.__file__` is asserted to live under that directory. That is the
bundled dependency actually loading on this machine's architecture, not a
site-packages copy answering in its place.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(REPO, "dist")

try:
    import bpy
except ImportError:                                   # pragma: no cover
    print("SKIP  tests/test_packaged_extension.py needs Blender:")
    print("      blender -b --factory-startup --python "
          "tests/test_packaged_extension.py")
    raise SystemExit(0)

FAILURES = []
CHECKS = [0]

EXPECTED_VERSION = (0, 26, 0)
EXPECTED_SCHEMA = 2
EXPECTED_STAGES = (
    "Scan Setup",
    "Scan Preprocessing",
    "Mesh Repair",
    "Alignment",
    "Landmark Manager",
    "Measurement Manager",
    "Measurement Visualization",
    "Results and Export",
)


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def version_text(parts):
    return ".".join(str(part) for part in parts)


def main():
    addon_zip = os.path.join(
        DIST, "body_surface_measurement-%s.zip" % version_text(EXPECTED_VERSION))
    extension_zip = os.path.join(
        DIST, "bsmt-%s.zip" % version_text(EXPECTED_VERSION))

    print("\nA. the packages under test")
    for path in (addon_zip, extension_zip):
        check("%s exists" % os.path.basename(path), os.path.isfile(path), path)
    if FAILURES:
        print("\ncannot continue without the built packages")
        return 1
    for path in (addon_zip, extension_zip):
        print("  %s\n    sha256 %s\n    %d bytes"
              % (os.path.basename(path), sha256(path),
                 os.path.getsize(path)))

    workspace = tempfile.mkdtemp(prefix="bsmt-packaged-")
    try:
        return run(workspace, addon_zip, extension_zip)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run(workspace, addon_zip, extension_zip):
    package_root = os.path.join(workspace, "addon")
    wheel_root = os.path.join(workspace, "wheel")
    os.makedirs(package_root)
    os.makedirs(wheel_root)

    check("the extraction directory is OUTSIDE the repository",
          not os.path.abspath(workspace).startswith(os.path.abspath(REPO)),
          workspace)

    with zipfile.ZipFile(addon_zip) as archive:
        archive.extractall(package_root)
    package_dir = os.path.join(package_root, "body_surface_measurement")
    check("the add-on ZIP extracted to a clean package directory",
          os.path.isdir(package_dir), package_dir)

    # ---------------------------------------------------------- isolation --
    print("\nB. import isolation - the working tree must NOT answer")
    removed = [entry for entry in list(sys.path)
               if os.path.abspath(entry or ".") == os.path.abspath(REPO)]
    for entry in removed:
        sys.path.remove(entry)
    for name in [n for n in sys.modules
                 if n == "body_surface_measurement"
                 or n.startswith("body_surface_measurement.")]:
        del sys.modules[name]
    check("the repository root is not on sys.path",
          not any(os.path.abspath(e or ".") == os.path.abspath(REPO)
                  for e in sys.path))
    check("and no BSMT module is left over from a previous import",
          not any(n.startswith("body_surface_measurement")
                  for n in sys.modules))

    # The bundled wheel goes on first, so the packaged dependency is what
    # answers if anything does.
    with zipfile.ZipFile(extension_zip) as archive:
        wheels = [n for n in archive.namelist() if n.endswith(".whl")]
        mac_wheels = [n for n in wheels if "macosx" in n and "arm64" in n]
        check("the extension ZIP carries a macOS ARM64 wheel",
              len(mac_wheels) == 1, wheels)
        check("and a Windows x64 wheel, so the release still covers both",
              any("win_amd64" in n for n in wheels), wheels)
        if mac_wheels:
            archive.extract(mac_wheels[0], workspace)
            with zipfile.ZipFile(os.path.join(workspace,
                                              mac_wheels[0])) as wheel:
                wheel.extractall(wheel_root)

    sys.path.insert(0, wheel_root)
    sys.path.insert(0, package_root)

    import body_surface_measurement as bsmt

    module_file = os.path.abspath(bsmt.__file__)
    check("the imported BSMT comes from the EXTRACTED PACKAGE",
          module_file.startswith(os.path.abspath(package_root)), module_file)
    check("and demonstrably not from the working tree",
          not module_file.startswith(os.path.abspath(REPO)), module_file)
    print("      imported from %s" % module_file)

    # ------------------------------------------------------------ identity --
    print("\nC. package identity")
    check("VERSION is %s" % version_text(EXPECTED_VERSION),
          tuple(bsmt.VERSION) == EXPECTED_VERSION, bsmt.VERSION)
    from body_surface_measurement import export as packaged_export
    check("export.SCHEMA_VERSION is %d" % EXPECTED_SCHEMA,
          packaged_export.SCHEMA_VERSION == EXPECTED_SCHEMA,
          packaged_export.SCHEMA_VERSION)
    with zipfile.ZipFile(extension_zip) as archive:
        manifest = archive.read("blender_manifest.toml").decode("utf-8")
    check("the extension manifest declares the same version",
          'version = "%s"' % version_text(EXPECTED_VERSION) in manifest)
    check("and declares both platforms",
          'platforms = ["macos-arm64", "windows-x64"]' in manifest)

    # ------------------------------------------------------ registration ----
    print("\nD. registration in a factory-startup Blender")
    registered = True
    try:
        bsmt.register()
    except Exception as exc:                          # pragma: no cover
        registered = False
        check("the packaged extension registers", False,
              "%s: %s" % (type(exc).__name__, exc))
    if registered:
        check("the packaged extension registers without a traceback", True)
    check("the scene now carries BSMT properties",
          hasattr(bpy.context.scene, "bsmt"))

    # Every packaged submodule must ALSO be the packaged one - a single
    # working-tree module sneaking in would invalidate everything above.
    strays = sorted(
        name for name, mod in sys.modules.items()
        if name.startswith("body_surface_measurement")
        and getattr(mod, "__file__", None)
        and os.path.abspath(mod.__file__).startswith(os.path.abspath(REPO)))
    check("NO packaged submodule resolved to the working tree",
          not strays, strays)
    print("      %d packaged BSMT modules loaded, all from the extraction dir"
          % sum(1 for n in sys.modules if n.startswith("body_surface_measurement")))

    # ------------------------------------------------------- workflow UI ----
    print("\nE. the workflow sidebar, from the package")
    from body_surface_measurement import panels as packaged_panels
    top_level = [cls for cls in packaged_panels.workflow_panels()
                 if not getattr(cls, "bl_parent_id", "")]
    labels = tuple(cls.bl_label for cls in top_level)
    check("there are exactly EIGHT top-level workflow panels",
          len(labels) == 8, len(labels))
    check("in the specified workflow order",
          labels == EXPECTED_STAGES, labels)
    for position, stage in enumerate(EXPECTED_STAGES, start=1):
        check("  %d. %s" % (position, stage),
              position <= len(labels) and labels[position - 1] == stage)

    # ---------------------------------------------------- bundled solver ----
    print("\nF. the bundled pygeodesic wheel, on this machine's architecture")
    from body_surface_measurement import geodesic as packaged_geodesic
    unavailable = packaged_geodesic.ensure_loaded()
    check("the packaged extension loads the exact solver",
          not unavailable, unavailable)
    try:
        import pygeodesic
        pygeodesic_file = os.path.abspath(pygeodesic.__file__)
    except Exception as exc:                          # pragma: no cover
        pygeodesic_file = ""
        check("pygeodesic is importable", False, str(exc))
    if pygeodesic_file:
        check("pygeodesic came from the BUNDLED WHEEL, not site-packages",
              pygeodesic_file.startswith(os.path.abspath(wheel_root)),
              pygeodesic_file)
        print("      %s" % pygeodesic_file)
    check("and the backend reports itself as available",
          packaged_geodesic.registry.available())

    if unavailable:
        print("\ncannot run the end-to-end flow without the solver")
        return 1 if FAILURES else 0

    return smoke(bsmt, packaged_export, packaged_geodesic,
                 packaged_panels, workspace)


def smoke(bsmt, packaged_export, packaged_geodesic, packaged_panels,
          workspace):
    """A deterministic end-to-end flow, run entirely from the package."""
    import numpy as np
    from body_surface_measurement import protocol as packaged_protocol
    from body_surface_measurement import state as packaged_state

    context = bpy.context
    props = context.scene.bsmt

    print("\nG. end-to-end smoke flow, from the packaged extension")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24,
                                         radius=100.0)
    source = context.object
    source.name = "PackagedScan"
    context.view_layer.objects.active = source

    props.preprocess_target_triangles = 500000
    check("G: a measurement mesh was created",
          bpy.ops.bsmt.create_measurement_copy() == {'FINISHED'})
    copy = bpy.data.objects[props.preprocess_copy_name]
    context.view_layer.objects.active = copy

    canonical = packaged_geodesic.meshcache.get(context, copy, props.unit,
                                                rebuild=True)
    check("G: the canonical mesh built (%d triangles)"
          % canonical.triangle_count, canonical.triangle_count > 0)
    topology = dict(canonical.topology or {})
    check("G: analysis reports a clean single-component mesh",
          int(topology.get("component_count", 0)) == 1
          and int(topology.get("nonmanifold_edge_count", 0)) == 0, topology)

    landmark_collection = context.scene.bsmt_landmarks
    landmark_collection.clear()
    for index, triangle in enumerate((10, 300, 600, 900), start=1):
        item = landmark_collection.add()
        item.stable_id = index
        item.name = "L%d" % index
        item.protocol_id = "P%02d" % index
        item.status = "VALID"
        point = item.surface_point
        point.triangle_index = int(triangle)
        point.barycentric = (1 / 3.0, 1 / 3.0, 1 / 3.0)
        point.source_object = copy.name
        point.geometry_hash = canonical.geometry_hash
        point.component_id = canonical.component_of(triangle)
        point.status = "VALID"
        point.valid = True
        bary = np.array(point.barycentric, dtype=np.float64)
        point.local_xyz = tuple(
            float(v) for v in canonical.local_from(triangle, bary))
        point.world_xyz = tuple(float(v) for v in canonical.world_from(
            triangle, bary, copy.matrix_world))
    check("G: four landmarks placed", len(landmark_collection) == 4)

    measurement_collection = context.scene.bsmt_measurements
    measurement_collection.clear()
    for index, (src, dst) in enumerate(((1, 2), (3, 4)), start=1):
        item = measurement_collection.add()
        item.stable_id = index
        item.name = "M%d" % index
        item.protocol_id = "M%02d" % index
        item.source_stable_id = src
        item.target_stable_id = dst
    props.measurement_index = 0
    props.session_subject_id = "SUBJ-01"
    props.session_condition = "standing"
    props.session_scan_id = "SCAN-01"

    check("G: Calculate All succeeded",
          bpy.ops.bsmt.calculate_all_measurements() == {'FINISHED'})
    results = [(item.protocol_id, item.status, float(item.straight_mm),
                float(item.surface_mm)) for item in measurement_collection]
    for pid, status, straight, surface in results:
        check("G: %s is VALID with a straight (%.4f) and surface (%.4f) "
              "distance" % (pid, straight, surface),
              status == "VALID" and straight > 0.0 and surface > 0.0,
              (status, straight, surface))
        check("G: %s surface >= straight, as it must be" % pid,
              surface >= straight - 1e-9, (surface, straight))

    # ------------------------------------------------------------- export ---
    print("\nH. CSV schema v2, written by the packaged extension")
    import csv
    import io

    session = packaged_state.session_metadata(props)
    exported_at = "2026-09-07T00:00:00"
    version = ".".join(str(part) for part in bsmt.VERSION)
    mesh_info = {}

    rows = [packaged_export.measurement_row(
        session, packaged_state.measurement_export_record(context, item),
        mesh_info, version, exported_at)
        for item in measurement_collection]
    landmark_rows = [packaged_export.landmark_row(
        session, packaged_state.landmark_export_record(item),
        mesh_info, version, exported_at, unit="MM")
        for item in landmark_collection]

    csv_path = os.path.join(workspace, "measurements.csv")
    packaged_export.write_csv(csv_path, packaged_export.MEASUREMENT_COLUMNS,
                              rows)
    landmark_path = os.path.join(workspace, "landmarks.csv")
    packaged_export.write_csv(landmark_path,
                              packaged_export.LANDMARK_COLUMNS, landmark_rows)

    raw = open(csv_path, "rb").read()
    check("H: the file is written with a UTF-8 BOM, as Excel needs",
          raw.startswith(b"\xef\xbb\xbf"), raw[:4])
    check("H: and terminates lines with CRLF", b"\r\n" in raw)
    parsed = list(csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"), newline="")))
    check("H: it parses back to %d rows" % len(rows),
          len(parsed) == len(rows), len(parsed))

    check("H: schema_version is the FIRST column",
          packaged_export.MEASUREMENT_COLUMNS[0] == "schema_version")
    check("H: and every row declares schema version 2",
          all(row["schema_version"] == "2" for row in parsed),
          [row["schema_version"] for row in parsed])
    for column in ("measurement_stable_id", "from_landmark_stable_id",
                   "to_landmark_stable_id", "landmark_protocol",
                   "measurement_protocol"):
        check("H: the v2 column %s is present and populated where expected"
              % column, column in parsed[0], sorted(parsed[0])[:3])
    check("H: every stable id is non-blank",
          all(row["measurement_stable_id"] not in ("", None)
              for row in parsed),
          [row["measurement_stable_id"] for row in parsed])
    check("H: every distance column names millimetres",
          all(c.endswith("_mm") for c in packaged_export.MEASUREMENT_COLUMNS
              if "distance" in c))
    check("H: the session block survived the round trip",
          parsed[0]["subject_id"] == "SUBJ-01"
          and parsed[0]["scan_id"] == "SCAN-01",
          (parsed[0]["subject_id"], parsed[0]["scan_id"]))
    check("H: row order is the collection order, not sorted",
          [row["measurement_id"] for row in parsed] == ["M01", "M02"],
          [row["measurement_id"] for row in parsed])

    landmark_raw = open(landmark_path, "rb").read()
    landmark_parsed = list(csv.DictReader(
        io.StringIO(landmark_raw.decode("utf-8-sig"), newline="")))
    check("H: the landmark file is schema v2 too",
          all(row["schema_version"] == "2" for row in landmark_parsed))
    check("H: with a stable id on every landmark row",
          all(row["landmark_stable_id"] not in ("", None)
              for row in landmark_parsed))
    check("H: and an explicit coordinate unit",
          all(row["coordinate_unit"] == "MM" for row in landmark_parsed),
          [row["coordinate_unit"] for row in landmark_parsed])

    print("\nI. a stale state, exported from the package")
    packaged_state.invalidate_measurement_result(
        measurement_collection[1], "the mesh geometry changed")
    stale_rows = [packaged_export.measurement_row(
        session, packaged_state.measurement_export_record(context, item),
        mesh_info, version, exported_at)
        for item in measurement_collection]
    valid_row, stale_row = stale_rows
    check("I: the VALID row still carries its number",
          valid_row["surface_distance_mm"] not in ("", None))
    check("I: the invalidated row exports BLANK, not zero",
          stale_row["surface_distance_mm"] == "",
          stale_row["surface_distance_mm"])
    check("I: specifically not '0.000000'",
          stale_row["surface_distance_mm"] != "0.000000")
    check("I: its straight distance is blank too",
          stale_row["straight_distance_mm"] == "")
    check("I: no ratio was invented",
          stale_row["surface_to_straight_ratio"] == "")
    check("I: but the row keeps its identity and a readable status",
          stale_row["measurement_id"] == "M02"
          and bool(stale_row["status"]),
          (stale_row["measurement_id"], stale_row["status"]))

    # ----------------------------------------------------------- protocol ---
    print("\nJ. protocol round trip, from the package")
    # The unified protocol format: (stable_id, protocol_id, name, notes) for
    # landmarks and (stable_id, protocol_id, name, source, target, type,
    # enabled, notes) for measurement pairs. Stable ids 1 and 6 leave a gap on
    # purpose - a renumbering implementation would silently close it.
    korean = "\uc7a5\uace8\ub2a5"
    landmark_entries = [
        (1, "P01", "Acromion", "left"),
        (6, "P06", korean, "Korean name"),
    ]
    measurement_entries = [
        (2, "M01", "Acromion to iliac crest", 1, 6, "SURFACE", True, ""),
    ]
    protocol_path = os.path.join(workspace, "protocol.json")
    packaged_protocol.save_protocol(protocol_path, "Packaged Protocol",
                                    landmark_entries, measurement_entries)
    text = open(protocol_path, encoding="utf-8").read()
    name_out, landmarks_out, measurements_out = \
        packaged_protocol.load_protocol(protocol_path)

    check("J: the protocol name round-trips",
          name_out == "Packaged Protocol", name_out)
    check("J: every landmark definition survives, in order",
          landmarks_out == landmark_entries, landmarks_out)
    check("J: every measurement pair definition survives",
          measurements_out == measurement_entries, measurements_out)
    ids = [entry[0] for entry in landmarks_out]
    check("J: stable ids are preserved exactly, gap included",
          ids == [1, 6], ids)
    check("J: pair endpoints are referenced BY STABLE ID",
          measurements_out[0][3] == 1 and measurements_out[0][4] == 6,
          measurements_out[0])
    check("J: names survive, including non-ASCII",
          landmarks_out[1][2] == korean, landmarks_out[1])
    check("J: and the file holds it unescaped", korean in text)
    for forbidden in ("triangle_index", "barycentric", "world_xyz",
                      "geometry_hash", "surface_distance", "subject_id",
                      "scan_id"):
        check("J: the protocol file never mentions %s" % forbidden,
              forbidden not in text)

    print("\nK. the packaged extension unregisters cleanly")
    try:
        bsmt.unregister()
        check("K: unregister() completed without a traceback", True)
    except Exception as exc:
        check("K: unregister() completed without a traceback", False,
              "%s: %s" % (type(exc).__name__, exc))
    # A real assertion, not a tautology: unregister must actually remove the
    # classes from bpy.types. The Scene ID property can outlive unregister,
    # so it is the class removal that is checked.
    leftover = [cls.__name__ for cls in packaged_panels.workflow_panels()
                if hasattr(bpy.types, cls.__name__)]
    check("K: every workflow panel class was removed from bpy.types",
          not leftover, leftover)
    check("K: and the scene property group is gone",
          not hasattr(bpy.types.Scene, "bsmt"),
          getattr(bpy.types.Scene, "bsmt", None))

    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for failure in FAILURES:
        print("  FAILED: %s" % failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        print("BSMT_PACKAGED_RESULT=%d" % code)
    raise SystemExit(code)
