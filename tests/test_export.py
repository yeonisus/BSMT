"""Offline tests for Milestone 3.11: CSV export and the unified protocol.

    python3 tests/test_export.py

Both modules are pure stdlib, so the decisions that matter most - whether a
number is written at all, and whether a protocol can carry one subject's data
- are testable directly, without Blender and without pandas.
"""

import csv
import importlib.util
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def load(name):
    spec = importlib.util.spec_from_file_location(
        "bsmt_" + name, os.path.join(PACKAGE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_" + name] = module
    spec.loader.exec_module(module)
    return module


export = load("export")
protocol = load("protocol")


def raises(label, call, exception=None):
    exception = exception or protocol.ProtocolError
    try:
        call()
    except exception:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check(label, False, "did not raise")


SESSION = {"subject_id": "S01", "condition": "SV2", "scan_id": "S01_SV2_01"}
EMPTY_SESSION = {"subject_id": "", "condition": "", "scan_id": ""}
MESH = {
    "measurement_mesh": "A_BSMT",
    "source_mesh": "A",
    "representation": "Decimated copy, made for measurement",
    "source_triangles": 2783068,
    "measurement_triangles": 349999,
    "preprocessing_method": "COLLAPSE",
}
BARE_MESH = {"measurement_mesh": "Scan", "source_mesh": "",
             "representation": "", "source_triangles": 0,
             "measurement_triangles": 0, "preprocessing_method": ""}


def measurement(**overrides):
    record = {
        "protocol_id": "M01", "name": "Neck to Waist", "notes": "",
        "from_landmark_id": "L01", "from_landmark_name": "Neck_F",
        "to_landmark_id": "L02", "to_landmark_name": "Waist_F",
        "measurement_type": "BOTH", "enabled": True,
        "straight_valid": True, "straight_mm": 292.5867,
        "surface_valid": True, "surface_mm": 304.282837,
        "ratio": 1.0399,
        "status": "VALID",
        "result_geometry_hash": "abc123", "result_object": "A_BSMT",
        "backend_name": "pygeodesic-MMP", "backend_version": "0.1.11",
    }
    record.update(overrides)
    return record


def landmark(**overrides):
    record = {
        "protocol_id": "L01", "name": "Neck_F", "status": "VALID", "notes": "",
        "valid": True, "triangle_index": 1943,
        "barycentric": (0.25, 0.5, 0.25), "component_id": 1,
        "world_xyz": (0.291826, 0.0, 0.955096),
        "physical_mm_xyz": (291.826, 0.0, 955.096),
        "source_object": "A_BSMT", "geometry_hash": "abc123",
    }
    record.update(overrides)
    return record


def row_of(record, session=None, mesh=None):
    return export.measurement_row(session or SESSION, record, mesh or MESH,
                                  "0.18.0", "2026-09-02T12:00:00Z")


def landmark_row_of(record, session=None, unit="MM"):
    return export.landmark_row(session or SESSION, record, MESH, "0.18.0",
                               "2026-09-02T12:00:00Z", unit=unit)


# ---------------------------------------------------------------------------
# a blank is not a zero (sect. 9)
# ---------------------------------------------------------------------------

def test_uncalculated_values_are_blank():
    print("\n[export] a value that was never calculated is BLANK, never 0")
    row = row_of(measurement(surface_valid=False, surface_mm=0.0,
                             ratio=0.0, status="READY"))
    check("an uncalculated surface distance is blank",
          row["surface_distance_mm"] == "", repr(row["surface_distance_mm"]))
    check("  and NOT a zero", row["surface_distance_mm"] != "0.000000")
    check("the straight distance is still written",
          row["straight_distance_mm"] == "292.586700",
          row["straight_distance_mm"])
    check("the ratio goes blank with its missing half",
          row["surface_to_straight_ratio"] == "")
    check("and the status says why", row["status"] == "READY")

    row = row_of(measurement(straight_valid=False, surface_valid=False,
                             status="NOT_READY"))
    check("nothing calculated leaves all three blank",
          (row["straight_distance_mm"], row["surface_distance_mm"],
           row["surface_to_straight_ratio"]) == ("", "", ""))

    check("number() refuses to invent a value",
          export.number(0.0, valid=False) == "")
    check("  and writes a genuine zero when it is valid",
          export.number(0.0, valid=True) == "0.000000")
    check("None is blank", export.number(None) == "")
    check("NaN is blank", export.number(float("nan")) == "")
    check("nonsense is blank", export.number("banana") == "")
    check("a real number keeps its decimal point",
          export.number(1234.5, True) == "1234.500000")


def test_status_is_preserved():
    print("\n[export] STALE and FAILED stay visible, with no numbers")
    # BSMT clears a result the moment a dependency changes, so a stale row
    # arrives with nothing to write. The export must not paper over that.
    row = row_of(measurement(status="STALE", straight_valid=False,
                             surface_valid=False))
    check("STALE is exported as STALE", row["status"] == "STALE")
    check("  with no distance", row["straight_distance_mm"] == ""
          and row["surface_distance_mm"] == "")

    row = row_of(measurement(status="FAILED", straight_valid=True,
                             straight_mm=292.5867, surface_valid=False))
    check("FAILED is exported as FAILED", row["status"] == "FAILED")
    check("  and keeps the result that IS valid",
          row["straight_distance_mm"] == "292.586700")
    check("  while the one that failed stays blank",
          row["surface_distance_mm"] == "")

    row = row_of(measurement(status="INVALID_REFERENCE", straight_valid=False,
                             surface_valid=False, to_landmark_id="",
                             to_landmark_name="Waist_F"))
    check("a broken reference is exported as such",
          row["status"] == "INVALID_REFERENCE")
    check("  and still names the landmark that went missing",
          row["to_landmark_name"] == "Waist_F")

    row = row_of(measurement(enabled=False))
    check("a disabled measurement records that it was disabled",
          row["enabled"] == "0")
    check("an enabled one records that too", row_of(measurement())["enabled"] == "1")


# ---------------------------------------------------------------------------
# columns and format (sect. 2, 4)
# ---------------------------------------------------------------------------

def test_columns_are_stable():
    print("\n[export] the column order never moves")
    required = ("subject_id", "condition", "scan_id", "measurement_id",
                "measurement_name", "from_landmark_id", "from_landmark_name",
                "to_landmark_id", "to_landmark_name", "measurement_type",
                "straight_distance_mm", "surface_distance_mm",
                "surface_to_straight_ratio", "status", "measurement_mesh",
                "source_mesh", "geometry_hash", "bsmt_version")
    for column in required:
        check("measurements carry %s" % column,
              column in export.MEASUREMENT_COLUMNS)
    check("the first three are the session", export.MEASUREMENT_COLUMNS[:3]
          == ("subject_id", "condition", "scan_id"))
    check("no column is repeated",
          len(set(export.MEASUREMENT_COLUMNS))
          == len(export.MEASUREMENT_COLUMNS))

    required = ("subject_id", "condition", "scan_id", "landmark_id",
                "landmark_name", "status", "triangle_index", "barycentric_u",
                "barycentric_v", "barycentric_w", "component_id",
                "world_x", "world_y", "world_z", "coordinate_unit",
                "measurement_mesh", "geometry_hash", "bsmt_version")
    for column in required:
        check("landmarks carry %s" % column, column in export.LANDMARK_COLUMNS)
    check("no landmark column is repeated",
          len(set(export.LANDMARK_COLUMNS)) == len(export.LANDMARK_COLUMNS))

    row = row_of(measurement())
    check("a row fills exactly the declared columns",
          set(row) == set(export.MEASUREMENT_COLUMNS),
          set(row) ^ set(export.MEASUREMENT_COLUMNS))
    row = landmark_row_of(landmark())
    check("a landmark row does too",
          set(row) == set(export.LANDMARK_COLUMNS),
          set(row) ^ set(export.LANDMARK_COLUMNS))


def test_written_file():
    print("\n[export] what actually lands on disk")
    rows = [row_of(measurement()),
            row_of(measurement(protocol_id="M02", name="Waist girth",
                               surface_valid=False, status="READY"))]
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "out.csv")
        written = export.write_csv(path, export.MEASUREMENT_COLUMNS, rows)
        check("two rows written", written == 2)

        raw = open(path, "rb").read()
        check("the file starts with a UTF-8 BOM, for Excel",
              raw.startswith(b"\xef\xbb\xbf"), raw[:6])

        with open(path, newline="", encoding="utf-8-sig") as handle:
            parsed = list(csv.DictReader(handle))
        check("it reads back as two rows", len(parsed) == 2)
        check("the header is the declared order",
              list(parsed[0].keys()) == list(export.MEASUREMENT_COLUMNS))
        check("values survive the round trip",
              parsed[0]["straight_distance_mm"] == "292.586700")
        check("and a blank stays blank, not '0'",
              parsed[1]["surface_distance_mm"] == "")
        check("the session travels on every row",
              all(row["subject_id"] == "S01" for row in parsed))

        # No locale anywhere: the decimal separator is always a point.
        text = raw.decode("utf-8-sig")
        check("no comma decimal separator appears in a number",
              "292,586" not in text)
        check("the numbers use a decimal point", "292.586700" in text)


def test_unicode_and_punctuation():
    print("\n[export] Korean names, commas and quotes survive")
    tricky = measurement(
        name='Waist, "mid" level',
        from_landmark_name="목_앞",
        to_landmark_name="허리, 뒤",
        notes='he said "roughly here"; then, later, moved it',
    )
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "unicode.csv")
        export.write_csv(path, export.MEASUREMENT_COLUMNS, [row_of(tricky)])
        with open(path, newline="", encoding="utf-8-sig") as handle:
            parsed = list(csv.DictReader(handle))
        check("one row, not split by the commas", len(parsed) == 1)
        check("a Korean name survives byte for byte",
              parsed[0]["from_landmark_name"] == "목_앞",
              parsed[0]["from_landmark_name"])
        check("so does one with a comma in it",
              parsed[0]["to_landmark_name"] == "허리, 뒤")
        check("embedded quotes survive",
              parsed[0]["measurement_name"] == 'Waist, "mid" level',
              parsed[0]["measurement_name"])
        check("and so do quotes and semicolons in a note",
              parsed[0]["notes"] == 'he said "roughly here"; then, later, '
                                    'moved it')
        check("the csv module quoted the fields that needed it",
              '"Waist, ""mid"" level"' in open(path, encoding="utf-8-sig").read())


def test_empty_session_metadata():
    print("\n[export] an empty session is blank, not 'None'")
    row = row_of(measurement(), session=EMPTY_SESSION)
    check("subject_id is blank", row["subject_id"] == "")
    check("condition is blank", row["condition"] == "")
    check("scan_id is blank", row["scan_id"] == "")
    check("nothing says None",
          "None" not in "".join(str(v) for v in row.values()))
    check("and the measurement is still fully exported",
          row["straight_distance_mm"] == "292.586700")


# ---------------------------------------------------------------------------
# landmarks (sect. 3)
# ---------------------------------------------------------------------------

def test_landmark_rows():
    print("\n[export] a landmark row, positioned and not")
    row = landmark_row_of(landmark())
    check("the triangle index is written", row["triangle_index"] == "1943")
    check("barycentric coordinates are written",
          (row["barycentric_u"], row["barycentric_v"], row["barycentric_w"])
          == ("0.250000", "0.500000", "0.250000"))
    check("the component is written", row["component_id"] == "1")
    check("the world position is written",
          row["world_x"] == "0.291826" and row["world_z"] == "0.955096")
    check("the coordinate unit is named", row["coordinate_unit"] == "MM")
    check("and the millimetre position travels too, so the two files agree",
          row["physical_mm_x"] == "291.826000")
    check("the mesh it was picked on is named",
          row["measurement_mesh"] == "A_BSMT")
    check("with the hash it was picked against",
          row["geometry_hash"] == "abc123")

    # Sect. 3: an unpositioned landmark keeps its DEFINITION row.
    row = landmark_row_of(landmark(valid=False, status="NOT_PICKED",
                                   name="Acromion_L", notes="skipped"))
    check("an unpicked landmark still gets a row",
          row["landmark_name"] == "Acromion_L")
    check("  with its status", row["status"] == "NOT_PICKED")
    check("  and its notes", row["notes"] == "skipped")
    for column in ("triangle_index", "barycentric_u", "barycentric_v",
                   "barycentric_w", "component_id", "world_x", "world_y",
                   "world_z", "coordinate_unit", "physical_mm_x"):
        check("  but %s is blank" % column, row[column] == "", row[column])

    for status in ("STALE", "INVALID", "NEEDS_REFRESH"):
        row = landmark_row_of(landmark(status=status))
        check("%s is exported explicitly" % status, row["status"] == status)


# ---------------------------------------------------------------------------
# provenance (sect. 10)
# ---------------------------------------------------------------------------

def test_mesh_provenance():
    print("\n[export] which mesh the numbers came from")
    row = row_of(measurement())
    check("the measurement mesh is named", row["measurement_mesh"] == "A_BSMT")
    check("and its source", row["source_mesh"] == "A")
    check("the representation is stated",
          "Decimated" in row["representation"])
    check("with both triangle counts",
          row["source_triangles"] == "2783068"
          and row["measurement_triangles"] == "349999")
    check("and the preprocessing method", row["preprocessing_method"] == "COLLAPSE")

    row = row_of(measurement(), mesh=BARE_MESH)
    check("measuring on a plain scan names it", row["measurement_mesh"] == "Scan")
    check("  and leaves the source blank rather than repeating it",
          row["source_mesh"] == "")
    check("  with no invented triangle counts",
          row["source_triangles"] == "" and row["measurement_triangles"] == "")
    check("  and no invented method", row["preprocessing_method"] == "")


# ---------------------------------------------------------------------------
# file names (sect. 5)
# ---------------------------------------------------------------------------

def test_filenames():
    print("\n[export] default names, and characters a filesystem refuses")
    check("session fields make the name",
          export.default_filename("measurements", "S01", "SV2")
          == "S01_SV2_measurements.csv")
    check("landmarks get their own",
          export.default_filename("landmarks", "S01", "SV2")
          == "S01_SV2_landmarks.csv")
    check("a missing condition still works",
          export.default_filename("measurements", "S01", "")
          == "S01_measurements.csv")
    check("scan id is the next fallback",
          export.default_filename("measurements", "", "", "S01_SV2_01")
          == "S01_SV2_01_measurements.csv")
    check("then the mesh name",
          export.default_filename("measurements", "", "", "", "A_BSMT")
          == "A_BSMT_measurements.csv")
    check("and never nothing at all",
          export.default_filename("measurements") == "bsmt_measurements.csv")

    check("a slash cannot escape into a path",
          "/" not in export.default_filename("measurements", "S01/../etc"))
    check("nor a backslash",
          "\\" not in export.sanitize("S01\\S02"))
    check("spaces become underscores",
          export.sanitize("Subject 01") == "Subject_01")
    check("a run of junk collapses to one underscore",
          export.sanitize("a???b") == "a_b", export.sanitize("a???b"))
    check("leading and trailing punctuation is trimmed",
          export.sanitize("..S01..") == "S01", export.sanitize("..S01.."))
    check("a name of pure junk sanitises to nothing",
          export.sanitize("???") == "", repr(export.sanitize("???")))
    # A Korean subject id must survive: stripping it to nothing would give
    # every subject in the study the same fallback filename.
    check("a Korean id survives intact",
          export.sanitize("목_앞") == "목_앞", export.sanitize("목_앞"))
    check("and produces a usable file name",
          export.default_filename("measurements", "목_앞", "SV2")
          == "목_앞_SV2_measurements.csv",
          export.default_filename("measurements", "목_앞", "SV2"))
    for reserved in (":", "*", "?", '"', "<", ">", "|", "/", "\\"):
        check("the reserved character %r is removed" % reserved,
              reserved not in export.sanitize("S01%sX" % reserved))
    check("a very long id is truncated", len(export.sanitize("S" * 300)) <= 80)
    check("the result is still a .csv",
          export.default_filename("measurements", "목").endswith(".csv"))
    check("a name of pure punctuation still falls back",
          export.default_filename("measurements", "???", "***")
          == "bsmt_measurements.csv",
          export.default_filename("measurements", "???", "***"))


def test_summary():
    print("\n[export] the report the researcher reads afterwards")
    _counts, lines = export.summarise_measurements(
        ["VALID"] * 17 + ["STALE"])
    check("the count is first", lines[0] == "Measurements exported: 18",
          lines[0])
    check("VALID is reported", "VALID: 17" in lines, lines)
    check("STALE is reported", "STALE: 1" in lines, lines)
    _counts, lines = export.summarise_landmarks(["VALID", "NOT_PICKED"])
    check("landmarks are summarised too",
          lines[0] == "Landmarks exported: 2", lines[0])


# ---------------------------------------------------------------------------
# the protocol (sect. 6, 7, 8)
# ---------------------------------------------------------------------------

LANDMARKS = [
    (1, "L01", "Neck_F", "at the notch"),
    (2, "L02", "Waist_F", ""),
    (7, "L03", "목_앞", "Korean name"),
]
MEASUREMENTS = [
    (1, "M01", "Neck to Waist", 1, 2, "BOTH", True, ""),
    (4, "M02", "Neck to 목_앞", 1, 7, "SURFACE", False, "disabled on purpose"),
]


def test_protocol_round_trip():
    print("\n[protocol] definitions survive the round trip exactly")
    text = protocol.dumps_protocol("Design X posture study", LANDMARKS,
                                   MEASUREMENTS)
    name, landmarks_out, measurements_out = protocol.loads_protocol(text)
    check("the name survives", name == "Design X posture study")
    check("every landmark survives, in order", landmarks_out == LANDMARKS,
          landmarks_out)
    check("every measurement survives", measurements_out == MEASUREMENTS,
          measurements_out)
    check("stable ids are preserved exactly, not renumbered",
          [entry[0] for entry in landmarks_out] == [1, 2, 7])
    check("  including the gap at 3-6",
          [entry[0] for entry in measurements_out] == [1, 4])
    check("references are by stable id",
          measurements_out[1][3] == 1 and measurements_out[1][4] == 7)
    check("the disabled state survives", measurements_out[1][6] is False)
    check("the type survives", measurements_out[1][5] == "SURFACE")
    check("Korean names survive", landmarks_out[2][2] == "목_앞")
    check("notes survive", landmarks_out[0][3] == "at the notch")

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "p.json")
        protocol.save_protocol(path, "Study", LANDMARKS, MEASUREMENTS)
        again = protocol.load_protocol(path)
        check("saving and loading a file gives the same thing",
              again[1] == LANDMARKS and again[2] == MEASUREMENTS)
        raw = open(path, encoding="utf-8").read()
        check("the file is human readable JSON", raw.lstrip().startswith("{"))
        check("and holds the Korean name unescaped", "목_앞" in raw)


def test_protocol_is_portable():
    print("\n[protocol] nothing scan-, subject- or result-specific gets in")
    text = protocol.dumps_protocol("Study", LANDMARKS, MEASUREMENTS)
    for forbidden in ("triangle_index", "barycentric", "world_xyz",
                      "geometry_hash", "component_id", "source_object",
                      "physical_mm", "straight_distance", "surface_distance",
                      "subject_id", "condition", "scan_id", "A_BSMT",
                      "path", "backend"):
        check("the file never mentions %s" % forbidden, forbidden not in text)

    import json as _json
    document = _json.loads(text)
    check("only the four sections exist",
          set(document) == {"format", "version", "protocol_name",
                            "landmarks", "measurements"}, set(document))
    for entry in document["landmarks"]:
        check("a landmark entry holds only identity",
              set(entry) <= protocol.PROTOCOL_LANDMARK_KEYS, set(entry))
    for entry in document["measurements"]:
        check("a measurement entry holds only its definition",
              set(entry) <= protocol.PROTOCOL_MEASUREMENT_KEYS, set(entry))

    # And a file that DOES carry such data is refused, not quietly cleaned.
    document["landmarks"][0]["triangle_index"] = 1943
    raises("a file carrying a triangle index is refused",
           lambda: protocol.loads_protocol(_json.dumps(document)))
    document = _json.loads(text)
    document["subject_id"] = "S01"
    raises("a file carrying subject data is refused",
           lambda: protocol.loads_protocol(_json.dumps(document)))
    document = _json.loads(text)
    document["measurements"][0]["straight_distance_mm"] = 292.5
    raises("a file carrying a result is refused",
           lambda: protocol.loads_protocol(_json.dumps(document)))


def test_protocol_refusals():
    print("\n[protocol] what a protocol will not be asked to represent")
    raises("a measurement referencing an undefined landmark",
           lambda: protocol.build_protocol(
               "S", LANDMARKS, [(1, "M01", "x", 1, 99, "BOTH", True, "")]))
    raises("a measurement with the same landmark at both ends",
           lambda: protocol.build_protocol(
               "S", LANDMARKS, [(1, "M01", "x", 1, 1, "BOTH", True, "")]))
    raises("a protocol with no landmarks",
           lambda: protocol.build_protocol("S", [], []))
    raises("a duplicate landmark stable id",
           lambda: protocol.build_protocol(
               "S", [(1, "L01", "a", ""), (1, "L02", "b", "")], []))
    raises("a duplicate measurement stable id",
           lambda: protocol.build_protocol(
               "S", LANDMARKS,
               [(1, "M01", "a", 1, 2, "BOTH", True, ""),
                (1, "M02", "b", 1, 7, "BOTH", True, "")]))
    raises("a landmark with no name",
           lambda: protocol.build_protocol("S", [(1, "L01", "  ", "")], []))
    raises("a stable id of zero",
           lambda: protocol.build_protocol("S", [(0, "L01", "a", "")], []))
    raises("an unknown measurement type",
           lambda: protocol.build_protocol(
               "S", LANDMARKS, [(1, "M01", "x", 1, 2, "CURVED", True, "")]))
    raises("a file that is not JSON", lambda: protocol.loads_protocol("{["))
    raises("a JSON array instead of an object",
           lambda: protocol.loads_protocol("[]"))
    raises("a landmark protocol from the older format",
           lambda: protocol.loads_protocol(
               protocol.dumps("Old", [("L01", "Neck_F", "")])))
    raises("a version from the future",
           lambda: protocol.loads_protocol(
               '{"format": "bsmt-protocol", "version": 99, "landmarks": []}'))

    # A protocol with landmarks but no measurements is legitimate: a landmark
    # set is a protocol too.
    name, landmarks_out, measurements_out = protocol.loads_protocol(
        protocol.dumps_protocol("Landmarks only", LANDMARKS, []))
    check("landmarks with no measurements is allowed",
          len(landmarks_out) == 3 and measurements_out == [])


def test_the_older_formats_still_work():
    print("\n[protocol] the two earlier formats are untouched")
    text = protocol.dumps("Old protocol", [("L01", "Neck_F", "note")])
    name, entries = protocol.loads(text)
    check("a landmark protocol still round-trips",
          name == "Old protocol" and entries == [("L01", "Neck_F", "note")])
    text = protocol.dumps_measurements(
        "Old template",
        [("M01", "A to B", "L01", "Neck_F", "L02", "Waist_F", "BOTH", True, "")])
    name, entries = protocol.loads_measurements(text)
    check("a measurement template still round-trips",
          name == "Old template" and len(entries) == 1, entries)
    check("and the two formats stay distinguishable",
          protocol.FORMAT != protocol.PROTOCOL_FORMAT
          != protocol.MEASUREMENT_FORMAT)


def main():
    print("BSMT Milestone 3.11 - export and protocol tests")
    for test in (
        test_uncalculated_values_are_blank,
        test_status_is_preserved,
        test_columns_are_stable,
        test_written_file,
        test_unicode_and_punctuation,
        test_empty_session_metadata,
        test_landmark_rows,
        test_mesh_provenance,
        test_filenames,
        test_summary,
        test_protocol_round_trip,
        test_protocol_is_portable,
        test_protocol_refusals,
        test_the_older_formats_still_work,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
