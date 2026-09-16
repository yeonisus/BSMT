"""Offline tests for Surface Region definition and boundary validation.

    python3 tests/test_regions.py

Everything that DECIDES whether an ordered set of landmarks is a usable
closed boundary lives in ``regions.py`` and is pure, so it is tested here
without Blender. The Blender-side adapter, the persistence, the UI, the
boundary computation and the "no solver was called" guarantee are in
tests/test_surface_region_blender.py and
tests/test_region_panel_draw_blender.py.

The properties this suite exists to protect:

* THE LANDMARK ORDER IS THE BOUNDARY. n landmarks mean exactly n segments,
  the last of which closes Ln back to L1. There is no orientation to store,
  nothing to reverse, and no way to express an open boundary.
* the verdict is DETERMINISTIC and ORDERED. The DEFINITION is judged before
  the CACHE, so a degenerate landmark list is told it is degenerate rather
  than told its boundary is stale - recomputing would produce the same
  degenerate loop.
* a cached boundary is only ever compared against THE DEFINITION IT WAS
  COMPUTED FOR. Any edit makes it stale rather than wrong.
* the self-intersection test is SOUND. Everything it reports is a real place
  where the boundary meets itself; it never guesses, and it never projects
  the loop into a plane. It is also INCOMPLETE, and says so.
"""

import importlib.util
import os
import sys

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


regions = _load("bsmt_regions", os.path.join(PACKAGE, "regions.py"))


# ---------------------------------------------------------------------------
# fixtures - facts shaped exactly as state.region_definition_facts builds them
# ---------------------------------------------------------------------------

LABELS = {1: "C08", 2: "B03", 3: "W05", 4: "W08", 5: "C10", 6: "C12"}


def landmark(stable_id, exists=True, picked=True, component=1,
             status=regions.LANDMARK_VALID, triangle=None):
    return {
        "stable_id": stable_id,
        "exists": exists,
        "picked": picked,
        "label": LABELS.get(stable_id, "L%d" % stable_id),
        "protocol_id": "L%02d" % stable_id,
        "component_id": component,
        "status": status,
        "triangle": 100 + stable_id if triangle is None else triangle,
        "source_object": "Scan_BSMT",
        "geometry_hash": "HASH",
    }


def definition(ids, **overrides):
    """A region definition with no computed boundary."""
    facts = {"landmarks": [landmark(value) for value in ids],
             "segments": [], "cached_definition": "",
             "live_geometry_hash": "HASH", "legacy_segment_count": 0}
    facts.update(overrides)
    return facts


def computed(ids, geometry="HASH", live="HASH", key=None, length=100.0,
             points=50, triangles=None):
    """A region definition WITH a boundary computed for it."""
    facts = definition(ids, live_geometry_hash=live)
    pairs = regions.segment_pairs(ids)
    triangles = triangles or {}
    facts["segments"] = [{
        "position": position,
        "from_landmark": from_id,
        "to_landmark": to_id,
        "computed": True,
        "point_count": points,
        "length_mm": length,
        "object_name": "Scan_BSMT",
        "geometry_hash": geometry,
        "from_triangle": triangles.get(from_id, 100 + from_id),
        "to_triangle": triangles.get(to_id, 100 + to_id),
        "component_id": 1,
    } for position, (from_id, to_id) in enumerate(pairs)]
    facts["cached_definition"] = (regions.definition_key(ids) if key is None
                                  else key)
    return facts


def ring(count, radius=10.0, z=0.0, start=0.0, span=2.0 * np.pi):
    angles = np.linspace(start, start + span, count)
    return np.stack([radius * np.cos(angles), radius * np.sin(angles),
                     np.full(count, z)], axis=1)


# ---------------------------------------------------------------------------
# A. the landmark order IS the boundary
# ---------------------------------------------------------------------------

def test_order_defines_the_segments():
    print("\n[A] ordered landmarks imply their segments, closing pair included")
    pairs = regions.segment_pairs([1, 2, 3, 4])
    check("A1: four landmarks give exactly four segments", len(pairs) == 4,
          pairs)
    check("A2: consecutive pairs, in order",
          pairs[:3] == [(1, 2), (2, 3), (3, 4)], pairs)
    check("A3: and the LAST one closes back to the first automatically",
          pairs[3] == (4, 1), pairs[3])
    check("A4: three landmarks give three segments",
          regions.segment_pairs([1, 2, 3]) == [(1, 2), (2, 3), (3, 1)])
    check("A5: there is no way to express an open boundary - the closing "
          "pair is not optional",
          all(pair[1] == regions.segment_pairs([1, 2, 3, 4, 5])[0][0]
              for pair in [regions.segment_pairs([1, 2, 3, 4, 5])[-1]]))
    check("A6: an empty definition implies no segments",
          regions.segment_pairs([]) == [] and regions.segment_pairs([1]) == [])


def test_definition_key_distinguishes_loops():
    print("\n[A] the definition key tells different loops apart")
    check("A7: the same order gives the same key",
          regions.definition_key([1, 2, 3, 4])
          == regions.definition_key([1, 2, 3, 4]))
    check("A8: a REORDERED definition is a different key - A-B-C-D and "
          "A-C-B-D are different boundaries",
          regions.definition_key([1, 2, 3, 4])
          != regions.definition_key([1, 3, 2, 4]))
    check("A9: and so is a rotated one, because segment 3 of one is not "
          "segment 3 of the other",
          regions.definition_key([1, 2, 3, 4])
          != regions.definition_key([2, 3, 4, 1]))


def test_definition_reads_as_a_loop():
    print("\n[A] the boundary reads back the way a researcher wrote it")
    text = regions.describe_definition(["C08", "B03", "W05"])
    check("A10: every landmark, in order", "C08" in text and "B03" in text
          and "W05" in text, text)
    check("A11: and closing back to the first, shown explicitly",
          text.endswith("C08"), text)
    check("A12: one landmark is just itself",
          regions.describe_definition(["C08"]) == "C08")
    check("A13: none is empty", regions.describe_definition([]) == "")


# ---------------------------------------------------------------------------
# B. the definition is judged first
# ---------------------------------------------------------------------------

def test_empty_and_insufficient():
    print("\n[B] a definition that cannot describe a boundary")
    result = regions.validate(definition([]))
    check("B1: no landmarks is a DRAFT, not an error - it is a region being "
          "built", result["status"] == regions.STATUS_DRAFT
          and result["code"] == regions.CODE_EMPTY,
          (result["status"], result["code"]))
    for count in (1, 2):
        result = regions.validate(definition(list(range(1, count + 1))))
        check("B2: %d landmark(s) cannot bound anything" % count,
              result["status"] == regions.STATUS_DRAFT
              and result["code"] == regions.CODE_INSUFFICIENT_LANDMARKS,
              (result["status"], result["code"]))
    check("B3: the minimum is three, and it is stated",
          regions.MIN_LANDMARKS == 3)
    result = regions.validate(definition([1, 2, 3]))
    check("B4: three IS enough to define a boundary",
          result["code"] == regions.CODE_BOUNDARY_NOT_COMPUTED,
          (result["status"], result["code"]))


def test_degenerate_sequence():
    print("\n[B] the same landmark twice in a row")
    result = regions.validate(definition([1, 1, 2]))
    check("B5: A-A-B is refused as degenerate",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_DEGENERATE_SEQUENCE,
          (result["status"], result["code"]))
    # A-B-A is the case worth naming: its CLOSING segment is A->A, which only
    # exists because the closing pair is implicit.
    result = regions.validate(definition([1, 2, 1]))
    check("B6: A-B-A is refused - its implicit closing segment is A to A",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_DEGENERATE_SEQUENCE,
          (result["status"], result["code"]))
    check("B7: and the faulty position is named", result["problems"],
          result["problems"])
    result = regions.validate(definition([1, 2, 3, 3, 4]))
    check("B8: a repeat in the middle is caught too",
          result["code"] == regions.CODE_DEGENERATE_SEQUENCE, result["code"])


def test_duplicate_landmark():
    print("\n[B] a landmark the loop returns to")
    result = regions.validate(definition([1, 2, 3, 2, 4]))
    check("B9: a non-adjacent repeat pinches the loop and is refused",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_DUPLICATE_LANDMARK,
          (result["status"], result["code"]))
    check("B10: naming which position repeats which",
          any("already boundary landmark" in problem["detail"]
              for problem in result["problems"]), result["problems"])


def test_missing_and_unpicked():
    print("\n[B] landmarks that are gone, or were never placed")
    facts = definition([1, 2, 3, 4])
    facts["landmarks"][2]["exists"] = False
    result = regions.validate(facts)
    check("B11: a deleted landmark makes the region INVALID",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_MISSING_LANDMARK,
          (result["status"], result["code"]))
    check("B12: the reference is NAMED, not silently dropped",
          "W05" in " ".join(problem["detail"]
                            for problem in result["problems"]),
          result["problems"])
    check("B13: and BSMT says it never substitutes another",
          "substitut" in result["detail"], result["detail"])

    facts = definition([1, 2, 3, 4])
    facts["landmarks"][1]["picked"] = False
    result = regions.validate(facts)
    check("B14: a landmark with no surface point is refused",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_LANDMARK_NOT_PICKED,
          (result["status"], result["code"]))


def test_cross_component():
    print("\n[B] landmarks on disconnected surface components")
    facts = definition([1, 2, 3, 4])
    facts["landmarks"][2]["component_id"] = 7
    result = regions.validate(facts)
    check("B15: a cross-component definition is refused",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_CROSS_COMPONENT,
          (result["status"], result["code"]))
    check("B16: and says why - there is no surface route between components",
          "no surface route" in result["detail"], result["detail"])


def test_definition_is_judged_before_cache():
    print("\n[B] the DEFINITION is judged before the CACHE")
    # Degenerate AND computed for a different definition. The researcher must
    # be told about the degeneracy: recomputing would solve the same
    # zero-length segment again.
    facts = computed([1, 2, 1], key="9,9,9")
    result = regions.validate(facts)
    check("B17: a degenerate loop is reported as degenerate, not as stale",
          result["code"] == regions.CODE_DEGENERATE_SEQUENCE, result["code"])
    facts = computed([1, 2, 3, 4], key="9,9,9")
    facts["landmarks"][0]["exists"] = False
    result = regions.validate(facts)
    check("B18: a missing landmark outranks a stale cache",
          result["code"] == regions.CODE_MISSING_LANDMARK, result["code"])


# ---------------------------------------------------------------------------
# C. the cached boundary
# ---------------------------------------------------------------------------

def test_not_computed():
    print("\n[C] a complete definition with no boundary yet")
    result = regions.validate(definition([1, 2, 3, 4]))
    check("C1: DRAFT, because nothing has been solved",
          result["status"] == regions.STATUS_DRAFT
          and result["code"] == regions.CODE_BOUNDARY_NOT_COMPUTED,
          (result["status"], result["code"]))
    check("C2: and it says exactly what to press, and how many segments",
          "Compute Boundary" in result["detail"]
          and "4 surface segments" in result["detail"], result["detail"])
    check("C3: the segments are listed even though none is computed",
          len(result["segment_labels"]) == 4, result["segment_labels"])
    check("C4: each marked as not computed",
          all("not computed" in line for line in result["segment_labels"]),
          result["segment_labels"])


def test_valid_boundary():
    print("\n[C] a boundary computed for this exact definition")
    result = regions.validate(computed([1, 2, 3, 4]))
    check("C5: VALID", result["status"] == regions.STATUS_VALID,
          (result["status"], result["detail"]))
    check("C6: with no reason code", result["code"] == regions.CODE_NONE,
          result["code"])
    check("C7: recognised as CLOSED and COMPUTED",
          result["closed"] and result["computed"])
    check("C8: four landmarks, four segments",
          result["landmark_count"] == 4 and result["segment_count"] == 4)
    check("C9: the perimeter is the sum of its segments",
          abs(result["length_mm"] - 400.0) < 1e-9, result["length_mm"])
    check("C10: no per-segment problems", result["problems"] == [])
    check("C11: the boundary reads as a closed loop",
          result["definition"].endswith("C08"), result["definition"])
    check("C12: NO AREA is reported anywhere in the result",
          not any("area" in str(key).lower() for key in result))


def test_definition_change_makes_it_stale():
    print("\n[C] editing the definition makes the cache stale, never wrong")
    for tag, key in (("reordered", regions.definition_key([1, 3, 2, 4])),
                     ("a landmark added", regions.definition_key([1, 2, 3])),
                     ("a landmark removed",
                      regions.definition_key([1, 2, 3, 4, 5]))):
        result = regions.validate(computed([1, 2, 3, 4], key=key))
        check("C13: %s -> STALE" % tag,
              result["status"] == regions.STATUS_STALE
              and result["code"] == regions.CODE_BOUNDARY_STALE,
              (tag, result["status"], result["code"]))
    check("C14: and it says to compute again rather than doing it",
          "Compute Boundary again" in regions.validate(
              computed([1, 2, 3, 4], key="9"))["detail"])


def test_geometry_and_landmark_movement():
    print("\n[C] the boundary stops matching the mesh or the landmarks")
    result = regions.validate(computed([1, 2, 3, 4], geometry="OLD",
                                       live="NEW"))
    check("C15: a geometry change makes it STALE",
          result["status"] == regions.STATUS_STALE
          and result["code"] == regions.CODE_GEOMETRY_MISMATCH,
          (result["status"], result["code"]))
    facts = computed([1, 2, 3, 4])
    facts["landmarks"][1]["triangle"] = 9999
    result = regions.validate(facts)
    check("C16: a re-picked landmark makes it STALE",
          result["status"] == regions.STATUS_STALE
          and result["code"] == regions.CODE_LANDMARK_STALE,
          (result["status"], result["code"]))
    check("C17: naming the two landmarks whose segment moved",
          any("B03" in problem["detail"] for problem in result["problems"]),
          result["problems"])
    facts = computed([1, 2, 3, 4])
    facts["landmarks"][2]["status"] = regions.LANDMARK_STALE
    result = regions.validate(facts)
    check("C18: a landmark that has gone stale makes the region stale too",
          result["status"] == regions.STATUS_STALE
          and result["code"] == regions.CODE_LANDMARK_STALE,
          (result["status"], result["code"]))
    facts = computed([1, 2, 3, 4])
    facts["segments"][2]["geometry_hash"] = "OTHER"
    result = regions.validate(facts)
    check("C19: segments solved on different geometry are refused",
          result["code"] == regions.CODE_GEOMETRY_MISMATCH, result["code"])


def test_partial_cache():
    print("\n[C] a boundary that is not completely computed")
    facts = computed([1, 2, 3, 4])
    facts["segments"][2]["computed"] = False
    result = regions.validate(facts)
    check("C20: one uncomputed segment means the boundary is NOT valid",
          result["status"] != regions.STATUS_VALID, result["status"])
    check("C21: it reads as not computed, so the fix is obvious",
          result["code"] == regions.CODE_BOUNDARY_NOT_COMPUTED,
          result["code"])
    facts = computed([1, 2, 3, 4])
    facts["segments"] = facts["segments"][:3]
    result = regions.validate(facts)
    check("C22: too few cached segments is the same answer, never VALID",
          result["status"] != regions.STATUS_VALID
          and result["code"] == regions.CODE_BOUNDARY_NOT_COMPUTED,
          (result["status"], result["code"]))
    facts = computed([1, 2, 3, 4])
    facts["segments"][1]["from_landmark"] = 99
    result = regions.validate(facts)
    check("C23: a segment cached for a different pair is STALE",
          result["status"] == regions.STATUS_STALE
          and result["code"] == regions.CODE_BOUNDARY_STALE,
          (result["status"], result["code"]))


# ---------------------------------------------------------------------------
# D. the old model is refused by name
# ---------------------------------------------------------------------------

def test_legacy_definition():
    print("\n[D] a region saved under the old measurement-path model")
    facts = definition([])
    facts["legacy_segment_count"] = 4
    result = regions.validate(facts)
    check("D1: refused by name, not read as an empty region",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_LEGACY_DEFINITION,
          (result["status"], result["code"]))
    check("D2: and says what to do about it",
          "landmarks" in result["detail"], result["detail"])
    check("D3: an ordinary empty region is still just a DRAFT",
          regions.validate(definition([]))["code"] == regions.CODE_EMPTY)
    check("D4: it is a definition fault, so Compute refuses before solving",
          regions.CODE_LEGACY_DEFINITION in regions.DEFINITION_FAULTS)


# ---------------------------------------------------------------------------
# E. self-intersection - sound, incomplete, and labelled
# ---------------------------------------------------------------------------

def test_touching_polylines():
    print("\n[E] non-adjacent boundary segments that share a point")
    facts = computed([1, 2, 3, 4])
    arcs = [ring(30, start=0.0, span=0.4), ring(30, start=1.6, span=0.4),
            ring(30, start=0.0, span=0.4), ring(30, start=4.8, span=0.4)]
    result = regions.validate(facts, polylines=arcs, touch_tolerance=1e-6)
    check("E1: two non-adjacent segments through the same points are caught",
          result["status"] == regions.STATUS_INVALID
          and result["code"] == regions.CODE_SELF_INTERSECTION,
          (result["status"], result["code"]))
    check("E2: and the pair is named", result["problems"], result["problems"])


def test_touching_is_sound_not_a_guess():
    print("\n[E] the test never invents a self-touch")
    facts = computed([1, 2, 3, 4])
    arcs = [ring(30, z=value, start=value, span=0.4)
            for value in (0.0, 1.0, 2.0, 3.0)]
    result = regions.validate(facts, polylines=arcs, touch_tolerance=1e-6)
    check("E3: four separated arcs are NOT reported as touching",
          result["status"] == regions.STATUS_VALID, result["detail"])
    check("E4: adjacent segments meet at a corner BY DESIGN and are allowed",
          regions.loop_adjacency(4)
          == {frozenset((0, 1)), frozenset((1, 2)), frozenset((2, 3)),
              frozenset((3, 0))})
    check("E5: with no tolerance the test simply does not run",
          regions.validate(facts, polylines=arcs,
                           touch_tolerance=0.0)["status"]
          == regions.STATUS_VALID)


def test_limits_are_stated():
    print("\n[E] the limits of the test are documented, not hidden")
    text = regions.SELF_INTERSECTION_LIMITS
    check("E6: it says it is incomplete", "INCOMPLETE" in text)
    check("E7: it says what it does detect", "share a point" in text)
    check("E8: it says what it does NOT detect",
          "between two sampled points" in text)
    check("E9: and why no projected test is used",
          "wraps a limb" in text and "flat projection" in text)
    check("E10: every result carries the limits with it",
          regions.validate(computed([1, 2, 3, 4]))["limits"] == text)
    source = open(os.path.join(PACKAGE, "regions.py")).read()
    check("E11: the module refuses the naive projected-polygon test by name",
          "XY" in source and "WRONG on a body" in source)


def test_scope_of_the_verdict_is_reported():
    print("\n[E] a verdict reports what it did NOT check")
    cheap = regions.validate(computed([1, 2, 3, 4]))
    full = regions.validate(computed([1, 2, 3, 4]),
                            polylines=[ring(20, z=value, start=value,
                                            span=0.4)
                                       for value in (0.0, 1.0, 2.0, 3.0)],
                            touch_tolerance=1e-6)
    check("E12: both are VALID",
          cheap["status"] == full["status"] == regions.STATUS_VALID)
    check("E13: but only one ran the shared-point test",
          cheap["touch_checked"] is False and full["touch_checked"] is True,
          (cheap["touch_checked"], full["touch_checked"]))
    cheap_text = "\n".join(regions.summary_lines(cheap))
    full_text = "\n".join(regions.summary_lines(full))
    check("E14: the cheap report says the test was not run",
          regions.TOUCH_NOT_RUN in cheap_text, cheap_text)
    check("E15: the full one still states the limits",
          regions.SELF_INTERSECTION_LIMITS in full_text
          and regions.TOUCH_NOT_RUN not in full_text)
    check("E16: neither claims the boundary is simple",
          "is simple" not in cheap_text.lower()
          and "no self-intersection" not in full_text.lower())
    check("E17: the summary spells the boundary out",
          "C08 → B03" in full_text, full_text[:200])


# ---------------------------------------------------------------------------
# F. scope, vocabulary and naming
# ---------------------------------------------------------------------------

def test_scope():
    print("\n[F] this milestone is the BOUNDARY, and stops there")
    source = open(os.path.join(PACKAGE, "regions.py")).read()
    lowered = source.lower()
    for banned in ("flood fill", "floodfill", "triangle clip", "coverage",
                   "projected area", "centroid", "remesh", "mesh cut"):
        check("F1: no %s anywhere in the module" % banned,
              banned not in lowered)
    check("F2: NO AREA is computed in this milestone",
          "def area" not in lowered and "surface_area" not in lowered)
    check("F3: the module says it is a boundary, not an area",
          "there is no area in" in lowered and "stops at the boundary" in lowered)
    check("F4: and that the order is what defines it",
          "order they typed is the orientation" in lowered
          or "IS the orientation" in source)
    check("F5: it states that it never reaches the solver",
          "no solver" in lowered)
    check("F6: no measurement is referenced by the rules at all",
          "measurement_stable_id" not in source)


def test_status_vocabulary():
    print("\n[F] the status vocabulary is complete and shared")
    for status in (regions.STATUS_DRAFT, regions.STATUS_VALID,
                   regions.STATUS_STALE, regions.STATUS_INVALID):
        check("F7: %s has a label, an icon and a short form" % status,
              any(entry[0] == status for entry in regions.STATUS_ITEMS)
              and status in regions.STATUS_ICONS
              and status in regions.STATUS_SHORT)
    codes = set()
    for ids in ([], [1], [1, 1, 2], [1, 2, 3, 2], [1, 2, 3, 4]):
        codes.add(regions.validate(definition(ids))["code"])
    codes.add(regions.validate(computed([1, 2, 3, 4], key="x"))["code"])
    codes.discard(regions.CODE_NONE)
    check("F8: every code a result can carry has a label",
          all(code in regions.CODE_LABELS for code in codes), sorted(codes))
    check("F9: the landmark vocabulary is mirrored, not re-invented",
          regions.LANDMARK_VALID == "VALID"
          and regions.LANDMARK_STALE == "STALE"
          and regions.LANDMARK_NOT_PICKED == "NOT_PICKED")
    check("F10: every definition fault has a label too",
          all(code in regions.CODE_LABELS
              for code in regions.DEFINITION_FAULTS))


def test_naming():
    print("\n[F] ids and names")
    check("F11: protocol ids continue from the ones in use",
          regions.next_protocol_id(["R01", "R07", "junk"]) == "R08")
    check("F12: an empty scene starts at R01",
          regions.next_protocol_id([]) == "R01")
    check("F13: junk is ignored rather than crashed on",
          regions.next_protocol_id([None, "", "RX", "12"]) == "R01")
    check("F14: default names do not collide",
          regions.default_name(["Region 1", "region 2"]) == "Region 3")


def main():
    print("BSMT Surface Region rules - offline\n" + "=" * 62)
    for test in (
        test_order_defines_the_segments,
        test_definition_key_distinguishes_loops,
        test_definition_reads_as_a_loop,
        test_empty_and_insufficient,
        test_degenerate_sequence,
        test_duplicate_landmark,
        test_missing_and_unpicked,
        test_cross_component,
        test_definition_is_judged_before_cache,
        test_not_computed,
        test_valid_boundary,
        test_definition_change_makes_it_stale,
        test_geometry_and_landmark_movement,
        test_partial_cache,
        test_legacy_definition,
        test_touching_polylines,
        test_touching_is_sound_not_a_guess,
        test_limits_are_stated,
        test_scope_of_the_verdict_is_reported,
        test_scope,
        test_status_vocabulary,
        test_naming,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for label in FAILURES:
        print("  FAILED: %s" % label)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
