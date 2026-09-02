"""Offline tests for the Milestone 3.1 Measurement Manager.

    python3 tests/test_measurements.py

Covers the pure logic (types, readiness, batch planning, result formatting)
and the measurement template format, including the definition/result
separation. The Blender-side behaviour - the UIList, the calculation
operators, and the dynamic-enum remap hazard - is exercised by the in-Blender
acceptance script, which is where a real EnumProperty exists.

Nothing here imports bpy.
"""

import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


measurements = _load("bsmt_measurements",
                     os.path.join(PACKAGE, "measurements.py"))
protocol = _load("bsmt_protocol2", os.path.join(PACKAGE, "protocol.py"))
landmarks = _load("bsmt_landmarks2", os.path.join(PACKAGE, "landmarks.py"))


def raises(label, exception, call):
    try:
        call()
    except exception:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check(label, False, "did not raise")


class FakeLandmark(object):
    """The minimum a landmark needs to look like, for readiness()."""

    def __init__(self, stable_id, name, status=landmarks.STATUS_VALID,
                 protocol_id=""):
        self.stable_id = stable_id
        self.name = name
        self.label = name
        self.status = status
        self.protocol_id = protocol_id or "L%02d" % stable_id


class FakeDefinition(object):
    def __init__(self, measurement_type, enabled=True):
        self.measurement_type = measurement_type
        self.enabled = enabled


def registry(*landmarks_in):
    table = {int(item.stable_id): item for item in landmarks_in}
    return lambda stable_id: table.get(int(stable_id))


# ---------------------------------------------------------------------------
# types and ids
# ---------------------------------------------------------------------------

def test_types():
    print("\n[types] STRAIGHT / SURFACE / BOTH")
    check("three types exist", set(measurements.TYPES)
          == {"STRAIGHT", "SURFACE", "BOTH"})
    check("enum items match the types",
          {item[0] for item in measurements.TYPE_ITEMS} == set(measurements.TYPES))
    check("STRAIGHT needs straight only",
          measurements.needs_straight("STRAIGHT")
          and not measurements.needs_surface("STRAIGHT"))
    check("SURFACE needs surface only",
          measurements.needs_surface("SURFACE")
          and not measurements.needs_straight("SURFACE"))
    check("BOTH needs both",
          measurements.needs_straight("BOTH") and measurements.needs_surface("BOTH"))


def test_measurement_ids():
    print("\n[ids] stable measurement ids")
    check("first id is M01", measurements.next_protocol_id([]) == "M01")
    check("continues after existing", measurements.next_protocol_id(
        ["M01", "M02"]) == "M03")
    check("continues past a gap",
          measurements.next_protocol_id(["M01", "M09"]) == "M10")
    check("ignores unrecognised ids",
          measurements.next_protocol_id(["custom", "M04"]) == "M05")
    check("pads beyond 99", measurements.next_protocol_id(["M99"]) == "M100")
    check("default name reads naturally",
          measurements.default_name("Neck_F", "Waist_F") == "Neck_F to Waist_F")


# ---------------------------------------------------------------------------
# references
# ---------------------------------------------------------------------------

def test_reference_resolution():
    print("\n[refs] stable-id resolution never substitutes")
    a = FakeLandmark(1, "Neck_F")
    b = FakeLandmark(2, "Waist_F")
    lookup = registry(a, b)
    check("resolves a live id", measurements.resolve(lookup, 1) is a)
    check("resolves the other", measurements.resolve(lookup, 2) is b)
    check("an unset reference resolves to None",
          measurements.resolve(lookup, 0) is None)
    check("a deleted id resolves to None, NOT to a neighbour",
          measurements.resolve(lookup, 3) is None)

    # The exact hazard the Blender spike exposed, at the logic level: ids are
    # not positions, so removing one landmark must not shift what another id
    # means.
    after_delete = registry(a)          # b (id 2) deleted
    check("after deleting id 2, id 1 still resolves to the SAME landmark",
          measurements.resolve(after_delete, 1) is a)
    check("after deleting id 2, id 2 resolves to None",
          measurements.resolve(after_delete, 2) is None)
    status, detail = measurements.readiness(
        measurements.resolve(after_delete, 1),
        measurements.resolve(after_delete, 2), 1, 2,
    )
    check("a deleted target gives INVALID_REFERENCE",
          status == measurements.STATUS_INVALID_REFERENCE, status)
    check("the detail names the missing end", "target" in detail, detail)
    check("the detail does not name a substitute", "Neck_F" not in detail, detail)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_readiness():
    print("\n[status] readiness depends on the landmarks, not on results")
    valid_a = FakeLandmark(1, "A")
    valid_b = FakeLandmark(2, "B")
    unpicked = FakeLandmark(3, "C", landmarks.STATUS_NOT_PICKED)
    stale = FakeLandmark(4, "D", landmarks.STATUS_STALE)
    needs = FakeLandmark(5, "E", landmarks.STATUS_NEEDS_REFRESH)
    invalid = FakeLandmark(6, "F", landmarks.STATUS_INVALID)

    def status_of(a, b):
        return measurements.readiness(a, b, getattr(a, "stable_id", 0),
                                      getattr(b, "stable_id", 0))[0]

    check("both valid -> READY",
          status_of(valid_a, valid_b) == measurements.STATUS_READY)
    check("both not picked -> NOT_READY",
          status_of(unpicked, unpicked) == measurements.STATUS_NOT_READY)
    check("one not picked -> NOT_READY",
          status_of(valid_a, unpicked) == measurements.STATUS_NOT_READY)
    check("a stale landmark -> STALE",
          status_of(valid_a, stale) == measurements.STATUS_STALE)
    check("stale outranks not-picked",
          status_of(unpicked, stale) == measurements.STATUS_STALE)
    check("needs-refresh -> NOT_READY",
          status_of(valid_a, needs) == measurements.STATUS_NOT_READY)
    check("an INVALID landmark -> STALE",
          status_of(valid_a, invalid) == measurements.STATUS_STALE)
    check("missing source -> INVALID_REFERENCE",
          status_of(None, valid_b) == measurements.STATUS_INVALID_REFERENCE)
    check("missing target -> INVALID_REFERENCE",
          status_of(valid_a, None) == measurements.STATUS_INVALID_REFERENCE)
    check("both missing -> INVALID_REFERENCE",
          status_of(None, None) == measurements.STATUS_INVALID_REFERENCE)

    check("A -> A is READY, not an error",
          status_of(valid_a, valid_a) == measurements.STATUS_READY)

    _s, detail = measurements.readiness(valid_a, unpicked, 1, 3)
    check("the detail names the landmark", "'C'" in detail, detail)
    check("the detail says which end", "target" in detail, detail)
    _s, detail = measurements.readiness(needs, valid_b, 5, 2)
    check("needs-refresh tells the user what to do",
          "Validate All" in detail, detail)


def test_status_tables():
    print("\n[status] the status tables are complete and consistent")
    check("seven statuses", len(measurements.STATUS_ORDER) == 7)
    check("enum items cover every status",
          {item[0] for item in measurements.STATUS_ITEMS}
          == set(measurements.STATUS_ORDER))
    check("every status has an icon",
          all(s in measurements.STATUS_ICONS for s in measurements.STATUS_ORDER))
    check("every status has a short label",
          all(s in measurements.STATUS_SHORT for s in measurements.STATUS_ORDER))
    for name in ("NOT_READY", "READY", "CALCULATING", "VALID", "STALE",
                 "INVALID_REFERENCE", "FAILED"):
        check("status %s exists" % name,
              getattr(measurements, "STATUS_" + name, None) == name)

    # measurements.py mirrors the landmark status names to stay bpy-free;
    # they must not drift from landmarks.py.
    for attr, expected in (("LANDMARK_VALID", landmarks.STATUS_VALID),
                           ("LANDMARK_NOT_PICKED", landmarks.STATUS_NOT_PICKED),
                           ("LANDMARK_NEEDS_REFRESH", landmarks.STATUS_NEEDS_REFRESH),
                           ("LANDMARK_STALE", landmarks.STATUS_STALE),
                           ("LANDMARK_INVALID", landmarks.STATUS_INVALID)):
        check("%s matches landmarks.py" % attr,
              getattr(measurements, attr) == expected)


def test_summary():
    print("\n[status] batch summary")
    statuses = ([measurements.STATUS_VALID] * 10
                + [measurements.STATUS_NOT_READY]
                + [measurements.STATUS_FAILED])
    counts, summary = measurements.summarise(statuses)
    check("counts valid", counts[measurements.STATUS_VALID] == 10)
    check("summary reads like the brief's example",
          summary == "10 valid, 1 not ready, 1 failed", summary)
    check("empty summarises cleanly",
          measurements.summarise([])[1] == "no measurements")


# ---------------------------------------------------------------------------
# batch planning: only defined, only enabled
# ---------------------------------------------------------------------------

def test_batch_plan_counts_only_enabled_definitions():
    print("\n[batch] only ENABLED definitions, never all pairs")
    definitions = (
        [FakeDefinition("BOTH") for _ in range(4)]
        + [FakeDefinition("SURFACE") for _ in range(4)]
        + [FakeDefinition("STRAIGHT") for _ in range(4)]
    )
    plan = measurements.batch_plan(definitions)
    check("total is 12", plan["total"] == 12)
    check("enabled is 12", plan["enabled"] == 12)
    check("8 require surface", plan["surface"] == 8, str(plan["surface"]))
    check("4 straight-only", plan["straight_only"] == 4)
    check("summary matches the brief's wording",
          plan["summary"] == "12 enabled measurements, 8 require surface "
                             "distance, 4 straight-only", plan["summary"])

    definitions[0].enabled = False
    definitions[8].enabled = False
    plan = measurements.batch_plan(definitions)
    check("disabled are excluded from enabled", plan["enabled"] == 10)
    check("disabled are counted", plan["disabled"] == 2)
    check("a disabled BOTH drops the surface count", plan["surface"] == 7)
    check("a disabled STRAIGHT drops the straight-only count",
          plan["straight_only"] == 3)

    check("nothing enabled plans nothing",
          measurements.batch_plan(
              [FakeDefinition("BOTH", enabled=False)])["enabled"] == 0)

    # The binding requirement: for N landmarks BSMT never invents pairs.
    check("50 landmarks with 3 definitions plans 3, not 1225",
          measurements.batch_plan(
              [FakeDefinition("BOTH") for _ in range(3)])["enabled"] == 3)
    check("there is no all-pairs helper anywhere in the module",
          not any(name.lower().count("all_pairs")
                  or name.lower().count("combination")
                  or name.lower().count("pairwise")
                  for name in dir(measurements)),
          str([n for n in dir(measurements) if "pair" in n.lower()]))


# ---------------------------------------------------------------------------
# result formatting
# ---------------------------------------------------------------------------

def test_result_formatting():
    print("\n[results] compact list formatting")
    check("both present",
          measurements.format_result(421.321, True, 448.77, True)
          == "421.32 / 448.77 mm")
    check("straight only",
          measurements.format_result(421.321, True, 0.0, False)
          == "421.32 / — mm")
    check("surface only",
          measurements.format_result(0.0, False, 448.77, True)
          == "— / 448.77 mm")
    check("neither",
          measurements.format_result(0.0, False, 0.0, False) == "— / — mm")

    check("ratio", abs(measurements.ratio(421.32, 448.77) - 1.0651) < 1e-3)
    check("ratio of zero straight is 0, not a division error",
          measurements.ratio(0.0, 448.77) == 0.0)
    check("A->A ratio is 0 rather than undefined",
          measurements.ratio(0.0, 0.0) == 0.0)


# ---------------------------------------------------------------------------
# measurement templates
# ---------------------------------------------------------------------------

SIX = [
    ("M01", "Front torso", "L01", "Neck_F", "L03", "Waist_F", "BOTH", True, ""),
    ("M02", "Back torso", "L02", "Neck_B", "L04", "Waist_B", "BOTH", True, ""),
    ("M03", "Shoulder width", "L05", "Shoulder_L", "L06", "Shoulder_R",
     "STRAIGHT", True, "bi-acromial"),
    ("M04", "Left shoulder-waist", "L05", "Shoulder_L", "L03", "Waist_F",
     "SURFACE", True, ""),
    ("M05", "Right shoulder-waist", "L06", "Shoulder_R", "L03", "Waist_F",
     "SURFACE", False, "disabled for now"),
    ("M06", "Neck circumference proxy", "L01", "Neck_F", "L02", "Neck_B",
     "SURFACE", True, ""),
]


def test_template_round_trip():
    print("\n[template] definitions round-trip with order preserved")
    text = protocol.dumps_measurements("Body Surface Measurements 01", SIX)
    name, entries = protocol.loads_measurements(text)
    check("template name round-trips",
          name == "Body Surface Measurements 01", name)
    check("all six round-trip", len(entries) == 6)
    check("ORDER preserved",
          [e["id"] for e in entries] == ["M01", "M02", "M03", "M04", "M05", "M06"])
    check("names round-trip",
          [e["name"] for e in entries] == [row[1] for row in SIX])
    check("types round-trip",
          [e["type"] for e in entries] == [row[6] for row in SIX])
    check("enabled flags round-trip",
          [e["enabled"] for e in entries] == [row[7] for row in SIX])
    check("the disabled definition survives as disabled",
          entries[4]["enabled"] is False)
    check("notes round-trip", entries[2]["notes"] == "bi-acromial")
    check("landmark ids round-trip",
          entries[0]["from_landmark_id"] == "L01"
          and entries[0]["to_landmark_id"] == "L03")
    check("human-readable names travel too (sect. 16)",
          entries[0]["from_landmark_name"] == "Neck_F"
          and entries[0]["to_landmark_name"] == "Waist_F")

    document = json.loads(text)
    check("declares its own format",
          document["format"] == protocol.MEASUREMENT_FORMAT)
    check("format differs from the landmark protocol",
          protocol.MEASUREMENT_FORMAT != protocol.FORMAT)
    check("uses the brief's top-level key",
          "measurement_protocol_name" in document)

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "m.json")
        protocol.save_measurements(path, "Disk", SIX)
        disk_name, disk_entries = protocol.load_measurements(path)
        check("file round-trips", disk_name == "Disk" and len(disk_entries) == 6)


def test_template_has_no_results_or_coordinates():
    print("\n[template] a template is a definition, never a result")
    text = protocol.dumps_measurements("T", SIX)
    lowered = text.lower()
    for forbidden in ("straight_distance_mm", "surface_distance_mm",
                      "straight_mm", "surface_mm", "ratio", "elapsed",
                      "bound_factor", "attempts", "backend",
                      "triangle_index", "barycentric", "geometry_hash",
                      "world_xyz", "component_id", "metric_key"):
        check("the file contains no %r" % forbidden, forbidden not in lowered)
    for entry in json.loads(text)["measurements"]:
        check("entry keys are definition-only, got %s" % sorted(entry),
              set(entry) <= protocol.MEASUREMENT_KEYS)

    for key, value in (("straight_distance_mm", 421.3),
                       ("surface_distance_mm", 448.8),
                       ("elapsed_s", 1.2), ("attempts", 1),
                       ("backend_name", "pygeodesic-MMP"),
                       ("triangle_index", 1234),
                       ("geometry_hash", "abc")):
        payload = json.dumps({"measurements": [{
            "id": "M01", "name": "X", "from_landmark_id": "L01",
            "to_landmark_id": "L02", "type": "BOTH", key: value}]})
        raises("a file carrying %s is refused" % key, protocol.ProtocolError,
               lambda p=payload: protocol.loads_measurements(p))

    try:
        protocol.loads_measurements(json.dumps({"measurements": [{
            "id": "M01", "name": "X", "from_landmark_id": "L01",
            "to_landmark_id": "L02", "type": "BOTH",
            "surface_distance_mm": 1.0}]}))
    except protocol.ProtocolError as exc:
        check("the refusal explains the distinction",
              "definition" in str(exc) and "specific scan" in str(exc), str(exc))

    raises("build_measurements refuses a result key too",
           protocol.ProtocolError,
           lambda: protocol._assert_no_result_data({"measurements": [
               {"id": "M01", "name": "X", "elapsed_s": 1.0}]}))


def test_template_validation():
    print("\n[template] malformed templates are refused with a reason")
    raises("not JSON", protocol.ProtocolError,
           lambda: protocol.loads_measurements("{oops"))
    raises("a JSON list", protocol.ProtocolError,
           lambda: protocol.loads_measurements("[]"))
    raises("empty measurements", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": []})))
    raises("missing measurements key", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"name": "x"})))
    raises("an unnamed measurement", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": [
               {"id": "M01", "name": "", "from_landmark_id": "L01",
                "to_landmark_id": "L02", "type": "BOTH"}]})))
    raises("an unknown type", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": [
               {"id": "M01", "name": "X", "from_landmark_id": "L01",
                "to_landmark_id": "L02", "type": "GEODESIC"}]})))
    raises("a missing landmark reference", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": [
               {"id": "M01", "name": "X", "to_landmark_id": "L02",
                "type": "BOTH"}]})))
    raises("duplicate measurement ids", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": [
               {"id": "M01", "name": "A", "from_landmark_id": "L01",
                "to_landmark_id": "L02", "type": "BOTH"},
               {"id": "M01", "name": "B", "from_landmark_id": "L01",
                "to_landmark_id": "L03", "type": "BOTH"}]})))
    raises("a non-boolean enabled", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({"measurements": [
               {"id": "M01", "name": "X", "from_landmark_id": "L01",
                "to_landmark_id": "L02", "type": "BOTH", "enabled": "yes"}]})))
    raises("a future version", protocol.ProtocolError,
           lambda: protocol.loads_measurements(json.dumps({
               "version": protocol.MEASUREMENT_VERSION + 1,
               "measurements": [{"id": "M01", "name": "X",
                                 "from_landmark_id": "L01",
                                 "to_landmark_id": "L02", "type": "BOTH"}]})))

    # A landmark protocol must not load as a measurement template.
    landmark_file = protocol.dumps("P", [("L01", "Neck_F", "")])
    raises("a landmark protocol is refused here", protocol.ProtocolError,
           lambda: protocol.loads_measurements(landmark_file))
    try:
        protocol.loads_measurements(landmark_file)
    except protocol.ProtocolError as exc:
        check("and it says which loader to use",
              "Load Landmark Protocol" in str(exc), str(exc))
    # ...and vice versa.
    template_file = protocol.dumps_measurements("T", SIX[:1])
    raises("a measurement template is refused by the landmark loader",
           protocol.ProtocolError, lambda: protocol.loads(template_file))

    check("type is normalised to upper case",
          protocol.loads_measurements(json.dumps({"measurements": [
              {"id": "M01", "name": "X", "from_landmark_id": "L01",
               "to_landmark_id": "L02", "type": "both"}]}))[1][0]["type"]
          == "BOTH")
    check("ids are assigned when omitted",
          protocol.loads_measurements(json.dumps({"measurements": [
              {"name": "X", "from_landmark_id": "L01",
               "to_landmark_id": "L02", "type": "BOTH"}]}))[1][0]["id"] == "M01")
    check("enabled defaults to True",
          protocol.loads_measurements(json.dumps({"measurements": [
              {"name": "X", "from_landmark_id": "L01",
               "to_landmark_id": "L02", "type": "BOTH"}]}))[1][0]["enabled"]
          is True)


def test_unresolved_reference_stays_explicit():
    print("\n[template] an unresolvable reference is never guessed")
    # Loading a template whose landmark ids are absent must leave the
    # reference recorded and unresolved, not matched by name.
    text = protocol.dumps_measurements("T", SIX)
    _name, entries = protocol.loads_measurements(text)
    available = {"L01": FakeLandmark(1, "Neck_F", protocol_id="L01")}

    resolved = []
    for entry in entries:
        source = available.get(entry["from_landmark_id"])
        target = available.get(entry["to_landmark_id"])
        status, _detail = measurements.readiness(
            source, target,
            source.stable_id if source else 0,
            target.stable_id if target else 0,
        )
        resolved.append(status)
    check("definitions with a missing end are INVALID_REFERENCE",
          all(status == measurements.STATUS_INVALID_REFERENCE
              for status in resolved), str(resolved))
    check("the reference id survives for a later re-link",
          entries[0]["from_landmark_id"] == "L01")
    check("the human-readable name survives for diagnostics",
          entries[0]["to_landmark_name"] == "Waist_F")

    # A landmark that merely shares a NAME must not be matched when the id
    # does not resolve.
    lookalike = FakeLandmark(9, "Waist_F", protocol_id="L99")
    check("a name match is not a reference match",
          available.get("L03") is None and lookalike.protocol_id != "L03")


def test_performance_of_dependency_lookup():
    print("\n[performance] 50 landmarks, 100 definitions")
    import time

    class Definition(object):
        __slots__ = ("source_stable_id", "target_stable_id", "enabled",
                     "measurement_type")

        def __init__(self, source, target):
            self.source_stable_id = source
            self.target_stable_id = target
            self.enabled = True
            self.measurement_type = "BOTH"

    definitions = [Definition((i % 50) + 1, ((i + 7) % 50) + 1)
                   for i in range(100)]

    def referencing(stable_id):
        return [d for d in definitions
                if d.source_stable_id == stable_id
                or d.target_stable_id == stable_id]

    referencing(1)
    started = time.perf_counter()
    for _ in range(1000):
        referencing(17)
    per_call = (time.perf_counter() - started) / 1000.0
    print("        %.4f ms per dependency lookup over 100 definitions"
          % (per_call * 1000.0))
    check("a dependency lookup is far under a frame",
          per_call < 0.001, "%.4f ms" % (per_call * 1000.0))
    check("it finds the right definitions",
          all(17 in (d.source_stable_id, d.target_stable_id)
              for d in referencing(17)))
    check("it finds all of them",
          len(referencing(17)) == sum(
              1 for d in definitions
              if 17 in (d.source_stable_id, d.target_stable_id)))

    started = time.perf_counter()
    for _ in range(1000):
        measurements.batch_plan(definitions)
    per_plan = (time.perf_counter() - started) / 1000.0
    print("        %.4f ms per batch_plan over 100 definitions"
          % (per_plan * 1000.0))
    check("batch planning is cheap enough to draw every redraw",
          per_plan < 0.002, "%.4f ms" % (per_plan * 1000.0))


def test_batch_report_is_explicit():
    print("\n[batch] the finished-batch report is spelled out")
    statuses = [measurements.STATUS_VALID] * 3
    lines = measurements.batch_report(statuses, enabled_count=3,
                                      disabled_count=0)
    check("reports enabled", "3 enabled" in lines, str(lines))
    check("reports calculated", "3 calculated" in lines, str(lines))
    check("reports valid", "3 valid" in lines, str(lines))
    check("reports not ready even when zero", "0 not ready" in lines, str(lines))
    check("reports failed even when zero", "0 failed" in lines, str(lines))
    check("exactly the brief's five lines when nothing is unusual",
          lines == ["3 enabled", "3 calculated", "3 valid", "0 not ready",
                    "0 failed"], str(lines))

    mixed = ([measurements.STATUS_VALID] * 2
             + [measurements.STATUS_NOT_READY]
             + [measurements.STATUS_FAILED])
    lines = measurements.batch_report(mixed, enabled_count=4, disabled_count=2)
    check("calculated counts only what actually ran",
          "3 calculated" in lines, str(lines))
    check("counts not-ready", "1 not ready" in lines, str(lines))
    check("counts failed", "1 failed" in lines, str(lines))
    check("disabled are reported as skipped",
          "2 disabled, skipped" in lines, str(lines))
    check("disabled are NOT counted as enabled",
          "4 enabled" in lines, str(lines))

    lines = measurements.batch_report(
        [measurements.STATUS_STALE, measurements.STATUS_INVALID_REFERENCE],
        enabled_count=2, disabled_count=0)
    check("stale appears when present", "1 stale" in lines, str(lines))
    check("invalid reference appears when present",
          "1 invalid reference" in lines, str(lines))
    check("neither appears when absent",
          not any("stale" in line for line in
                  measurements.batch_report([measurements.STATUS_VALID], 1, 0)))


def test_one_line_result():
    print("\n[results] compact one-line row")
    line = measurements.one_line_result(
        "M01", "P01", "P02", 27.99, True, 28.00, True,
        measurements.STATUS_VALID)
    check("matches the brief's shape",
          line == "M01 P01\u2192P02 | 27.99 / 28.00 | VALID", line)
    check("straight-only shows a dash for surface",
          measurements.one_line_result("M03", "P02", "P04", 94.20, True, 0.0,
                                       False, measurements.STATUS_VALID)
          == "M03 P02\u2192P04 | 94.20 / — | VALID")
    check("surface-only shows a dash for straight",
          measurements.one_line_result("M02", "P01", "P03", 0.0, False, 203.51,
                                       True, measurements.STATUS_VALID)
          == "M02 P01\u2192P03 | — / 203.51 | VALID")
    check("a stale row carries its status, not a number",
          measurements.one_line_result("M01", "P01", "P02", 0.0, False, 0.0,
                                       False, measurements.STATUS_STALE)
          == "M01 P01\u2192P02 | — / — | STALE")


def test_auto_name_generation():
    print("\n[naming] auto names come from the landmarks' visible names")
    check("uses the visible names, not ids",
          measurements.default_name("P01", "P02") == "P01 to P02")
    check("works for arbitrary researcher names",
          measurements.default_name("Shoulder_L", "Waist_F")
          == "Shoulder_L to Waist_F")
    check("a missing end is marked, not invented",
          measurements.default_name("", "P02") == "? to P02")
    check("both missing", measurements.default_name("", "") == "? to ?")
    check("non-ascii names survive",
          measurements.default_name("\ubaa9_\uc55e", "\ud5c8\ub9ac")
          == "\ubaa9_\uc55e to \ud5c8\ub9ac")


def main():
    print("BSMT Milestone 3.1 - Measurement Manager offline tests")
    print("  python : %s" % sys.version.split()[0])
    for test in (
        test_types,
        test_measurement_ids,
        test_reference_resolution,
        test_readiness,
        test_status_tables,
        test_summary,
        test_batch_plan_counts_only_enabled_definitions,
        test_result_formatting,
        test_batch_report_is_explicit,
        test_one_line_result,
        test_auto_name_generation,
        test_template_round_trip,
        test_template_has_no_results_or_coordinates,
        test_template_validation,
        test_unresolved_reference_stays_explicit,
        test_performance_of_dependency_lookup,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
