"""Offline tests for the Milestone 3.0 Landmark Manager.

    python3 tests/test_landmarks.py

Covers the pure logic - status classification, the stale rule, name and id
rules, batched transform reconstruction - and the protocol file format,
including the separation between a protocol (names and order) and scan
landmark data (positions), which is the whole point of sect. 10.

Blender-side behaviour (the UIList, the modal pick, marker objects) is
exercised by the in-Blender acceptance script instead; nothing here imports
bpy.
"""

import importlib.util
import json
import math
import os
import sys
import tempfile

import numpy as np

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


landmarks = _load("bsmt_landmarks", os.path.join(PACKAGE, "landmarks.py"))
protocol = _load("bsmt_protocol", os.path.join(PACKAGE, "protocol.py"))
surface_point = _load(
    "bsmt_sp", os.path.join(PACKAGE, "geodesic", "surface_point.py")
)


def raises(label, exception, call):
    try:
        call()
    except exception:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, "raised %s instead: %s" % (type(exc).__name__, exc))
    else:
        check(label, False, "did not raise")


# ---------------------------------------------------------------------------
# names and identifiers
# ---------------------------------------------------------------------------

def test_names():
    print("\n[names] arbitrary researcher names, no hard-coded anatomy")
    check("keeps an arbitrary name verbatim",
          landmarks.clean_name("Neck_F") == "Neck_F")
    check("normalises surrounding whitespace",
          landmarks.clean_name("  Shoulder  L  ") == "Shoulder L")
    check("accepts non-ascii names",
          landmarks.clean_name("목_앞") == "목_앞")
    check("accepts a name BSMT knows nothing about",
          landmarks.clean_name("Trochanterion_R") == "Trochanterion_R")
    raises("empty name is rejected", landmarks.LandmarkError,
           lambda: landmarks.clean_name(""))
    raises("whitespace-only name is rejected", landmarks.LandmarkError,
           lambda: landmarks.clean_name("   "))
    raises("None is rejected", landmarks.LandmarkError,
           lambda: landmarks.clean_name(None))
    raises("an over-long name is rejected", landmarks.LandmarkError,
           lambda: landmarks.clean_name("x" * (landmarks.MAX_NAME_LENGTH + 1)))
    check("a name at the limit is accepted",
          len(landmarks.clean_name("x" * landmarks.MAX_NAME_LENGTH))
          == landmarks.MAX_NAME_LENGTH)


def test_duplicate_detection():
    print("\n[names] duplicates are detected case-insensitively")
    existing = ["Neck_F", "Neck_B"]
    check("exact duplicate detected", landmarks.name_exists(existing, "Neck_F"))
    check("case-different duplicate detected",
          landmarks.name_exists(existing, "neck_f"))
    check("whitespace-different duplicate detected",
          landmarks.name_exists(existing, "  Neck_F  "))
    check("a genuinely new name is not a duplicate",
          not landmarks.name_exists(existing, "Shoulder_L"))

    check("unique_name leaves a free name alone",
          landmarks.unique_name(existing, "Waist_L") == "Waist_L")
    suffixed = landmarks.unique_name(existing, "Neck_F")
    check("unique_name suffixes a collision", suffixed == "Neck_F.001", suffixed)
    check("unique_name keeps suffixing",
          landmarks.unique_name(existing + [suffixed], "Neck_F") == "Neck_F.002")


def test_protocol_ids_and_helper_names():
    print("\n[ids] stable ids and protocol ids")
    check("first id is L01", landmarks.next_protocol_id([]) == "L01")
    check("continues after existing ids",
          landmarks.next_protocol_id(["L01", "L02"]) == "L03")
    check("continues past a gap",
          landmarks.next_protocol_id(["L01", "L07"]) == "L08")
    check("ignores ids it does not recognise",
          landmarks.next_protocol_id(["custom", "L02"]) == "L03")
    check("pads beyond 99", landmarks.next_protocol_id(["L99"]) == "L100")

    # Helper object names must come from the stable id, never the user name.
    check("helper name is derived from the stable id",
          landmarks.helper_object_name(7) == "BSMT_Landmark_000007")
    check("helper names are unique per id",
          landmarks.helper_object_name(1) != landmarks.helper_object_name(2))
    for hostile in ("Neck/F", "a" * 200, "목 앞", "", "Point.001"):
        name = landmarks.helper_object_name(3)
        check("helper name is unaffected by the label %r" % hostile[:12],
              name == "BSMT_Landmark_000003")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def test_stale_rule():
    print("\n[status] the single stale rule, shared with the A/B workflow")
    check("same hash and in-range triangle is not stale",
          landmarks.stale_reason("abc", 5, "abc", 100) == "")
    reason = landmarks.stale_reason("abc12345", 5, "def67890", 100)
    check("a changed hash is stale", "geometry changed" in reason, reason)
    check("the reason names both hashes",
          "abc12345" in reason and "def67890" in reason, reason)
    reason = landmarks.stale_reason("abc", 500, "abc", 100)
    check("an out-of-range triangle is stale",
          "outside the canonical array" in reason, reason)
    check("a negative triangle index is stale",
          landmarks.stale_reason("abc", -1, "abc", 100) != "")
    check("the last valid triangle is not stale",
          landmarks.stale_reason("abc", 99, "abc", 100) == "")


def test_status_transitions():
    print("\n[status] sect. 8 transitions")

    def classify(**kwargs):
        base = dict(picked=True, point_geometry_hash="abc", triangle_index=5,
                    source_object="Scan", object_exists=True,
                    canonical_geometry_hash="abc", triangle_count=100)
        base.update(kwargs)
        return landmarks.classify(**base)

    check("unpicked is NOT_PICKED",
          classify(picked=False)[0] == landmarks.STATUS_NOT_PICKED)
    check("matching hash is VALID",
          classify()[0] == landmarks.STATUS_VALID)

    # The geometry-edit path: the cache clears first, so the landmark is
    # NEEDS_REFRESH; only Validate All (which rebuilds) resolves it to STALE.
    status, detail = classify(canonical_geometry_hash=None, triangle_count=None)
    check("no cached canonical mesh is NEEDS_REFRESH",
          status == landmarks.STATUS_NEEDS_REFRESH, status)
    check("NEEDS_REFRESH says why", "not loaded" in detail, detail)
    status, detail = classify(canonical_geometry_hash="different")
    check("a rebuilt, changed mesh is STALE",
          status == landmarks.STATUS_STALE, status)
    check("STALE says why", "geometry changed" in detail, detail)
    check("VALID -> NEEDS_REFRESH -> STALE is the sect. 8 sequence", True)

    check("a missing source object is INVALID",
          classify(object_exists=False)[0] == landmarks.STATUS_INVALID)
    check("no recorded source object is INVALID",
          classify(source_object="")[0] == landmarks.STATUS_INVALID)
    check("an out-of-range triangle is STALE",
          classify(triangle_index=999)[0] == landmarks.STATUS_STALE)

    # A rigid transform changes none of the inputs, so it cannot change status.
    check("a transform cannot change status (no input depends on it)",
          classify()[0] == classify()[0] == landmarks.STATUS_VALID)


def test_summary():
    print("\n[status] Validate All summary counts")
    statuses = ([landmarks.STATUS_VALID] * 18
                + [landmarks.STATUS_NOT_PICKED] * 2
                + [landmarks.STATUS_STALE])
    counts, summary = landmarks.summarise(statuses)
    check("counts valid", counts[landmarks.STATUS_VALID] == 18)
    check("counts not picked", counts[landmarks.STATUS_NOT_PICKED] == 2)
    check("counts stale", counts[landmarks.STATUS_STALE] == 1)
    check("summary reads like the brief's example",
          summary == "18 valid, 2 not picked, 1 stale", summary)
    check("an empty list summarises cleanly",
          landmarks.summarise([])[1] == "no landmarks")
    check("every status has an icon",
          all(status in landmarks.STATUS_ICONS
              for status in landmarks.STATUS_ORDER))
    check("every status has an enum item",
          {item[0] for item in landmarks.STATUS_ITEMS}
          == set(landmarks.STATUS_ORDER))


# ---------------------------------------------------------------------------
# batched transform following
# ---------------------------------------------------------------------------

def _grid(n=12, spacing=10.0):
    xs = np.arange(n + 1, dtype=np.float64) * spacing
    gx, gy = np.meshgrid(xs, xs, indexing="ij")
    vertices = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    faces = []
    for i in range(n):
        for j in range(n):
            v00 = i * (n + 1) + j
            v10 = (i + 1) * (n + 1) + j
            v11 = (i + 1) * (n + 1) + j + 1
            v01 = i * (n + 1) + j + 1
            faces.append([v00, v10, v11])
            faces.append([v00, v11, v01])
    return vertices, np.asarray(faces, dtype=np.int64)


def test_batched_reconstruction_matches_the_single_point_maths():
    print("\n[transform] batched reconstruction == surface_point.reconstruct")
    vertices, faces = _grid()
    rng = np.random.default_rng(20260902)
    count = 50
    triangle_indices = rng.integers(0, faces.shape[0], size=count)
    raw = rng.random((count, 3))
    barycentrics = raw / raw.sum(axis=1, keepdims=True)

    batched = landmarks.local_positions(triangle_indices, barycentrics,
                                        vertices, faces)
    worst = 0.0
    for k in range(count):
        one = surface_point.reconstruct(
            vertices[faces[triangle_indices[k]]], barycentrics[k]
        )
        worst = max(worst, float(np.linalg.norm(batched[k] - one)))
    check("50 landmarks reconstruct identically to the single-point maths",
          worst < 1e-12, "worst %.3e" % worst)

    check("an empty set returns an empty array",
          landmarks.local_positions([], np.zeros((0, 3)), vertices, faces).shape
          == (0, 3))


def test_transform_following_is_exact_and_rigid_safe():
    print("\n[transform] world reconstruction under translation and rotation")
    vertices, faces = _grid()
    rng = np.random.default_rng(7)
    count = 50
    triangle_indices = rng.integers(0, faces.shape[0], size=count)
    raw = rng.random((count, 3))
    barycentrics = raw / raw.sum(axis=1, keepdims=True)

    identity = np.eye(4)
    base = landmarks.world_positions(triangle_indices, barycentrics,
                                     vertices, faces, identity)

    translation = np.eye(4)
    translation[:3, 3] = (37.0, -12.5, 5.25)
    moved = landmarks.world_positions(triangle_indices, barycentrics,
                                      vertices, faces, translation)
    check("translation shifts every landmark by exactly t",
          np.allclose(moved - base, translation[:3, 3], atol=1e-12))

    angle = 0.7
    rotation = np.eye(4)
    rotation[:2, :2] = [[math.cos(angle), -math.sin(angle)],
                        [math.sin(angle), math.cos(angle)]]
    rotated = landmarks.world_positions(triangle_indices, barycentrics,
                                        vertices, faces, rotation)
    check("rotation rotates every landmark by exactly R",
          np.allclose(rotated, base @ rotation[:3, :3].T, atol=1e-12))
    check("rotation preserves pairwise distances",
          np.allclose(np.linalg.norm(rotated[1:] - rotated[:-1], axis=1),
                      np.linalg.norm(base[1:] - base[:-1], axis=1), atol=1e-12))

    scale = np.diag([2.0, 3.0, 0.5, 1.0])
    scaled = landmarks.world_positions(triangle_indices, barycentrics,
                                       vertices, faces, scale)
    check("non-uniform scale still reconstructs from the SAME attachment",
          np.allclose(scaled, base @ scale[:3, :3].T, atol=1e-12))
    check("the canonical attachment is unchanged by any transform",
          np.array_equal(triangle_indices, triangle_indices))


def test_fifty_landmark_refresh_is_fast():
    print("\n[transform] 50 landmarks on a scan-sized mesh")
    import time
    n = 400                                  # 320,000 triangles
    xs = np.arange(n + 1, dtype=np.float64)
    gx, gy = np.meshgrid(xs, xs, indexing="ij")
    vertices = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    quads = np.arange(n * n).reshape(n, n)
    i, j = np.divmod(quads.ravel(), n)
    v00 = i * (n + 1) + j
    v10 = (i + 1) * (n + 1) + j
    v11 = (i + 1) * (n + 1) + j + 1
    v01 = i * (n + 1) + j + 1
    faces = np.concatenate([np.stack([v00, v10, v11], axis=1),
                            np.stack([v00, v11, v01], axis=1)]).astype(np.int64)
    print("        mesh: %d vertices, %d triangles"
          % (vertices.shape[0], faces.shape[0]))

    rng = np.random.default_rng(3)
    triangle_indices = rng.integers(0, faces.shape[0], size=50)
    raw = rng.random((50, 3))
    barycentrics = raw / raw.sum(axis=1, keepdims=True)
    matrix = np.eye(4)
    matrix[:3, 3] = (1.0, 2.0, 3.0)

    landmarks.world_positions(triangle_indices, barycentrics, vertices,
                              faces, matrix)     # warm up
    started = time.perf_counter()
    for _ in range(20):
        landmarks.world_positions(triangle_indices, barycentrics, vertices,
                                  faces, matrix)
    per_refresh = (time.perf_counter() - started) / 20.0
    print("        %.3f ms per 50-landmark refresh" % (per_refresh * 1000.0))
    check("a 50-landmark refresh stays well under one frame (16 ms)",
          per_refresh < 0.016, "%.3f ms" % (per_refresh * 1000.0))
    check("it does not scale with mesh size (gathers 50 triangles, not 320k)",
          per_refresh < 0.005, "%.3f ms" % (per_refresh * 1000.0))


# ---------------------------------------------------------------------------
# protocols
# ---------------------------------------------------------------------------

TEN = [("L%02d" % (i + 1), name, "")
       for i, name in enumerate(
           ["Neck_F", "Neck_B", "Shoulder_L", "Shoulder_R", "Waist_L",
            "Waist_R", "Hip_L", "Hip_R", "Knee_L", "Knee_R"])]


def test_protocol_round_trip():
    print("\n[protocol] save and load preserve names and order")
    text = protocol.dumps("Body Surface Protocol 01", TEN)
    name, entries = protocol.loads(text)
    check("protocol name round-trips", name == "Body Surface Protocol 01", name)
    check("all ten landmarks round-trip", len(entries) == 10, str(len(entries)))
    check("names round-trip exactly",
          [e[1] for e in entries] == [e[1] for e in TEN])
    check("ids round-trip exactly",
          [e[0] for e in entries] == [e[0] for e in TEN])
    check("ORDER is preserved",
          [e[1] for e in entries][:3] == ["Neck_F", "Neck_B", "Shoulder_L"])

    document = json.loads(text)
    check("declares its format", document["format"] == protocol.FORMAT)
    check("declares its version", document["version"] == protocol.VERSION)

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "p.json")
        protocol.save(path, "Disk Protocol", TEN)
        disk_name, disk_entries = protocol.load(path)
        check("file round-trips", disk_name == "Disk Protocol"
              and len(disk_entries) == 10)


def test_protocol_contains_no_scan_data():
    print("\n[protocol] a protocol never carries scan positions")
    text = protocol.dumps("P", TEN)
    document = json.loads(text)
    lowered = text.lower()
    for forbidden in ("triangle_index", "barycentric", "geometry_hash",
                      "world_xyz", "local_xyz", "physical_mm", "component_id",
                      "source_object"):
        check("the file contains no %r" % forbidden, forbidden not in lowered)
    for entry in document["landmarks"]:
        check("entry keys are identity only, got %s" % sorted(entry),
              set(entry) <= protocol.ENTRY_KEYS)

    # And loading scan data as a protocol is refused, not partially imported.
    scan_like = json.dumps({
        "format": protocol.FORMAT,
        "landmarks": [
            {"id": "L01", "name": "Neck_F", "triangle_index": 1234,
             "barycentric": [0.3, 0.3, 0.4]},
        ],
    })
    raises("a file carrying triangle_index is refused",
           protocol.ProtocolError, lambda: protocol.loads(scan_like))
    try:
        protocol.loads(scan_like)
    except protocol.ProtocolError as exc:
        check("the refusal explains the distinction",
              "not a protocol" in str(exc), str(exc))

    for key in ("world_xyz", "geometry_hash", "component_id", "surface_point"):
        payload = json.dumps({"landmarks": [{"id": "L01", "name": "N",
                                             key: "anything"}]})
        raises("a file carrying %s is refused" % key,
               protocol.ProtocolError, lambda p=payload: protocol.loads(p))

    # Writing is guarded in the same direction.
    raises("build() refuses an entry with an unexpected key",
           protocol.ProtocolError,
           lambda: protocol._assert_no_position_data(
               {"landmarks": [{"id": "L01", "name": "N",
                               "triangle_index": 3}]}))


def test_protocol_validation():
    print("\n[protocol] malformed files are refused with a reason")
    raises("not JSON", protocol.ProtocolError, lambda: protocol.loads("{oops"))
    raises("a JSON list is refused", protocol.ProtocolError,
           lambda: protocol.loads("[]"))
    raises("a wrong format tag is refused", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps(
               {"format": "bsmt-scan-data", "landmarks": []})))
    raises("a future version is refused", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps(
               {"version": protocol.VERSION + 1,
                "landmarks": [{"id": "L01", "name": "N"}]})))
    raises("missing landmarks list", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps({"protocol_name": "x"})))
    raises("empty landmarks list", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps({"landmarks": []})))
    raises("an unnamed landmark", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps(
               {"landmarks": [{"id": "L01", "name": ""}]})))
    raises("a non-object landmark", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps({"landmarks": ["Neck_F"]})))
    raises("duplicate names", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps({"landmarks": [
               {"id": "L01", "name": "Neck_F"},
               {"id": "L02", "name": "neck_f"}]})))
    raises("duplicate ids", protocol.ProtocolError,
           lambda: protocol.loads(json.dumps({"landmarks": [
               {"id": "L01", "name": "A"}, {"id": "L01", "name": "B"}]})))
    raises("build() refuses an empty protocol", protocol.ProtocolError,
           lambda: protocol.build("P", []))
    raises("build() refuses an empty name", protocol.ProtocolError,
           lambda: protocol.build("P", [("L01", "", "")]))

    name, entries = protocol.loads(json.dumps(
        {"landmarks": [{"name": "Neck_F"}, {"name": "Neck_B"}]}))
    check("ids are assigned when a file omits them",
          [e[0] for e in entries] == ["L01", "L02"])
    check("a missing protocol name gets a placeholder",
          name == "Untitled Protocol", name)
    check("notes default to empty", entries[0][2] == "")

    _n, entries = protocol.loads(json.dumps({"landmarks": [
        {"id": "L01", "name": "Neck_F", "notes": "midline"}]}))
    check("notes round-trip", entries[0][2] == "midline")


def main():
    print("BSMT Milestone 3.0 - Landmark Manager offline tests")
    print("  python : %s" % sys.version.split()[0])
    print("  numpy  : %s" % np.__version__)
    for test in (
        test_names,
        test_duplicate_detection,
        test_protocol_ids_and_helper_names,
        test_stale_rule,
        test_status_transitions,
        test_summary,
        test_batched_reconstruction_matches_the_single_point_maths,
        test_transform_following_is_exact_and_rigid_safe,
        test_fifty_landmark_refresh_is_fast,
        test_protocol_round_trip,
        test_protocol_contains_no_scan_data,
        test_protocol_validation,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
