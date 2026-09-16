"""Surface Region definitions and closed-boundary validation.

A SURFACE REGION is a researcher-defined CLOSED BOUNDARY on the measurement
mesh, specified by an ORDERED SET OF ANATOMICAL LANDMARKS. Consecutive
landmarks - including the final-to-first pair - are joined by cached surface
geodesic paths on the triangular mesh.

    Landmarks  ->  ordered landmark definition  ->  Compute Boundary
               ->  cached boundary segments     ->  Surface Region
               ->  Surface Interior [later]     ->  Surface Area [later]

This module is those rules, and it stops at the boundary. There is no area in
this file and no geometry is summed anywhere: a region is a boundary, and what
is enclosed by it is a separate question this milestone does not answer.

What changed in Milestone 3.31, and why
---------------------------------------
A region used to be defined as an ordered list of MEASUREMENT path references,
each with a stored orientation. That was technically sound and practically
unusable: defining a five-sided region meant creating five measurements,
computing five surface paths, then adding, reordering and reversing five path
references - and getting the direction of each one right by hand.

A measurement and a region answer different questions. A measurement asks
"how far is it from A to B"; a region asks "what closed boundary do these
landmarks describe". Sharing the geodesic backend is right; making one the
authoritative model of the other was not.

So the AUTHORITATIVE definition is now the ordered landmark ids, and the
segments are DERIVED from them: n landmarks always mean exactly n segments,
the last of which closes Ln back to L1. Orientation stops being a thing the
researcher manages, because the order they typed IS the orientation.

Two consequences the rest of this module exists to enforce:

* the boundary is a CACHED RESULT of a definition, so any change to the
  definition makes the cache stale rather than wrong - and it is never
  silently recomputed; and
* a region NEVER solves anything except when the researcher presses Compute
  Boundary. Validating, showing, renaming, reordering or saving must not
  reach the solver, because on a real scan that is minutes of frozen Blender
  (sect. 7 of the brief). Everything here is a pure function of facts the
  caller already holds.

No bpy, and no solver. The Blender-side adapter that gathers the facts is
``state.region_definition_facts``; the drawing is ``visualization`` + ``viz``.
"""

import numpy as np

#: A closed boundary needs at least this many DISTINCT landmarks. Three, not
#: two: two landmarks give A->B and B->A, the same geodesic walked both ways,
#: which encloses nothing at all. (The old measurement-reference model allowed
#: two, because two DIFFERENT paths between one pair - round the front of an
#: arm and round the back - do bound a lune. A landmark pair cannot express
#: that, and pretending otherwise would be the one place this model is less
#: expressive than the old one. It is recorded as a known limitation.)
MIN_LANDMARKS = 3


# ---------------------------------------------------------------------------
# status (the same vocabulary the rest of BSMT uses)
# ---------------------------------------------------------------------------
#
# A region's status is NOT a measurement's status and NOT a landmark's. It
# answers "is this boundary a closed loop I can trust right now", which
# depends on the landmarks but is a separate question.

STATUS_DRAFT = 'DRAFT'
STATUS_VALID = 'VALID'
STATUS_STALE = 'STALE'
STATUS_INVALID = 'INVALID'

STATUS_ITEMS = (
    (STATUS_DRAFT, "Draft",
     "The landmark definition is not finished, or the boundary has not been "
     "computed yet"),
    (STATUS_VALID, "Valid",
     "A closed boundary, computed from this exact landmark definition on "
     "this geometry"),
    (STATUS_STALE, "Stale",
     "The cached boundary no longer matches the landmarks, their positions "
     "or the geometry. Compute Boundary again"),
    (STATUS_INVALID, "Invalid",
     "The landmark definition itself cannot describe a closed boundary"),
)

STATUS_ORDER = (STATUS_VALID, STATUS_DRAFT, STATUS_STALE, STATUS_INVALID)

STATUS_ICONS = {
    STATUS_DRAFT: 'GREASEPENCIL',
    STATUS_VALID: 'CHECKMARK',
    STATUS_STALE: 'FILE_REFRESH',
    STATUS_INVALID: 'CANCEL',
}

STATUS_SHORT = {
    STATUS_DRAFT: "DRAFT",
    STATUS_VALID: "VALID",
    STATUS_STALE: "STALE",
    STATUS_INVALID: "INVALID",
}

#: A region in one of these states must never be presented as a boundary the
#: researcher can rely on, however good the drawing looks.
TRUSTED = (STATUS_VALID,)


# ---------------------------------------------------------------------------
# reason codes
# ---------------------------------------------------------------------------
#
# A status says what to do; a code says WHY, in a token a panel, a log and a
# test can all match on without parsing English.

CODE_NONE = ''
CODE_EMPTY = 'EMPTY'
CODE_INSUFFICIENT_LANDMARKS = 'INSUFFICIENT_LANDMARKS'
CODE_MISSING_LANDMARK = 'MISSING_LANDMARK'
CODE_DUPLICATE_LANDMARK = 'DUPLICATE_LANDMARK'
CODE_DEGENERATE_SEQUENCE = 'DEGENERATE_SEQUENCE'
CODE_LANDMARK_NOT_PICKED = 'LANDMARK_NOT_PICKED'
CODE_LANDMARK_STALE = 'LANDMARK_STALE'
CODE_BOUNDARY_NOT_COMPUTED = 'BOUNDARY_NOT_COMPUTED'
CODE_BOUNDARY_STALE = 'BOUNDARY_STALE'
CODE_CROSS_COMPONENT = 'CROSS_COMPONENT'
CODE_TOPOLOGY_BLOCKED = 'TOPOLOGY_BLOCKED'
CODE_SEGMENT_FAILED = 'SEGMENT_FAILED'
CODE_GEOMETRY_MISMATCH = 'GEOMETRY_MISMATCH'
CODE_SELF_INTERSECTION = 'SELF_INTERSECTION'
CODE_LEGACY_DEFINITION = 'LEGACY_DEFINITION'

CODE_LABELS = {
    CODE_EMPTY: "the region has no boundary landmarks yet",
    CODE_INSUFFICIENT_LANDMARKS: "too few landmarks to close a boundary",
    CODE_MISSING_LANDMARK: "a boundary landmark no longer exists",
    CODE_DUPLICATE_LANDMARK: "a landmark appears more than once",
    CODE_DEGENERATE_SEQUENCE: "the same landmark twice in a row",
    CODE_LANDMARK_NOT_PICKED: "a boundary landmark has no surface point",
    CODE_LANDMARK_STALE: "a boundary landmark has moved or gone stale",
    CODE_BOUNDARY_NOT_COMPUTED: "the boundary has not been computed yet",
    CODE_BOUNDARY_STALE: "the landmark definition changed after the boundary "
                         "was computed",
    CODE_CROSS_COMPONENT: "the boundary spans disconnected surface components",
    CODE_TOPOLOGY_BLOCKED: "the mesh is not safe to solve on",
    CODE_SEGMENT_FAILED: "a boundary segment could not be computed",
    CODE_GEOMETRY_MISMATCH: "the boundary was computed on different geometry",
    CODE_SELF_INTERSECTION: "the boundary touches itself",
    CODE_LEGACY_DEFINITION: "this region was defined in the old "
                            "measurement-path format",
}

#: Landmark statuses this module compares against, mirrored from ``landmarks``
#: so nothing here needs an import that could pull in bpy. Kept in sync by a
#: test.
LANDMARK_VALID = 'VALID'
LANDMARK_STALE = 'STALE'
LANDMARK_INVALID = 'INVALID'
LANDMARK_NOT_PICKED = 'NOT_PICKED'


#: Codes that mean THE DEFINITION cannot describe a closed boundary. Compute
#: Boundary refuses on any of these BEFORE the solver is constructed - there
#: is no boundary to solve, so solving would be minutes spent to arrive at the
#: same refusal. Everything not listed here is about the cache, which is
#: precisely what Compute Boundary exists to replace.
DEFINITION_FAULTS = (
    CODE_EMPTY,
    CODE_INSUFFICIENT_LANDMARKS,
    CODE_MISSING_LANDMARK,
    CODE_DUPLICATE_LANDMARK,
    CODE_DEGENERATE_SEQUENCE,
    CODE_LANDMARK_NOT_PICKED,
    CODE_CROSS_COMPONENT,
    CODE_LEGACY_DEFINITION,
)


class RegionError(Exception):
    """A region cannot be defined or edited as asked."""


def next_protocol_id(existing_ids):
    """The next free "R##" id, preserving ids already in use."""
    highest = 0
    for value in existing_ids:
        text = str(value).strip()
        if len(text) > 1 and text[0] in "Rr" and text[1:].isdigit():
            highest = max(highest, int(text[1:]))
    return "R%02d" % (highest + 1)


def default_name(existing_names):
    """A distinct default region name."""
    taken = {str(value).strip().lower() for value in existing_names}
    index = 1
    while ("region %d" % index) in taken:
        index += 1
    return "Region %d" % index


# ---------------------------------------------------------------------------
# the definition -> segments rule, in one place
# ---------------------------------------------------------------------------

def segment_pairs(landmark_ids):
    """The (from, to) landmark pairs an ordered definition implies.

    THE rule of this milestone, and the reason orientation stopped being the
    researcher's problem: n landmarks mean exactly n segments, and the last
    one closes Ln back to L1. The closing pair is implicit and mandatory - a
    boundary that does not return to where it started is not a boundary, so
    there is no way to express an open one and nothing to validate about it.
    """
    ids = [int(value) for value in (landmark_ids or ())]
    if len(ids) < 2:
        return []
    return [(ids[index], ids[(index + 1) % len(ids)])
            for index in range(len(ids))]


def definition_key(landmark_ids):
    """The ordered definition as one comparable string.

    Stored alongside a computed boundary so that "is this cache still for this
    definition" is a string compare rather than a re-derivation. Rotating or
    reversing the landmarks produces a different key on purpose: the same loop
    traversed the other way is the same curve, but the segments cached for it
    are not the same segments, and claiming otherwise would let position 3 of
    one definition be drawn as position 3 of another.
    """
    return ",".join(str(int(value)) for value in (landmark_ids or ()))


def describe_definition(labels):
    """The boundary as the researcher reads it: "C08 -> B03 -> ... -> C08"."""
    names = [str(value) for value in (labels or ())]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return " → ".join(names + [names[0]])


def describe_segment(facts, position=None):
    """One line for a boundary-segment list row."""
    prefix = "%d. " % position if position else ""
    return "%s%s → %s%s" % (
        prefix,
        facts.get("from_label") or "?",
        facts.get("to_label") or "?",
        "" if facts.get("computed") else "   (not computed)",
    )


# ---------------------------------------------------------------------------
# self-intersection: sound, and deliberately incomplete
# ---------------------------------------------------------------------------
#
# READ THIS BEFORE TRUSTING THE ANSWER.
#
# The boundary lives on a triangulated surface, not in a plane. Projecting it
# to XY (or XZ, or YZ) and running a polygon self-intersection test is the
# obvious thing to do and it is WRONG on a body: a boundary that wraps a limb
# is self-intersecting in every axis-aligned projection while being perfectly
# simple on the surface. BSMT does not do that, and does not claim a
# completeness it has not earned.
#
# What IS implemented here is a SOUND test: everything it reports is a real
# place where the boundary meets itself.
#
#   1. the same path used twice (either orientation);
#   2. a corner landmark that the loop arrives at more than once;
#   3. two NON-ADJACENT boundary paths that share a point in space.
#
# (3) is sound because two polylines that pass within a hair's breadth of one
# another on the surface genuinely touch there - there is no projection and no
# guessing involved, only a distance between two cached points.
#
# What is NOT detected, and is documented as such wherever this is reported:
# a transversal crossing that happens strictly BETWEEN two sampled polyline
# points, and which therefore leaves no shared point behind. Catching that
# needs each polyline point's triangle index, which the path cache does not
# store; resolving them would cost one BVH query per point. See
# `SELF_INTERSECTION_LIMITS`.

SELF_INTERSECTION_LIMITS = (
    "Self-intersection checking is SOUND but INCOMPLETE. It detects a path "
    "used twice, a corner the loop reaches more than once, and non-adjacent "
    "boundary paths that share a point. It does NOT detect a crossing that "
    "happens between two sampled points of a path. No projected-polygon test "
    "is used, because a boundary that wraps a limb self-intersects in every "
    "flat projection while being simple on the surface."
)


def _cell_groups(points, quantum, offset):
    """Indices of points sharing a grid cell. Vectorised per point."""
    cells = np.floor(points / quantum + offset).astype(np.int64)
    order = np.lexsort((cells[:, 2], cells[:, 1], cells[:, 0]))
    ordered = cells[order]
    if ordered.shape[0] < 2:
        return []
    breaks = np.flatnonzero(np.any(ordered[1:] != ordered[:-1], axis=1)) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [ordered.shape[0]]))
    return [order[start:end] for start, end in zip(starts, ends)
            if end - start > 1]


def touching_segments(polylines, tolerance, adjacency=None):
    """Non-adjacent boundary paths that SHARE A POINT. Returns sorted pairs.

    `polylines` is a list of (k, 3) arrays in one common space. `tolerance` is
    a distance in that same space; two points closer than it are treated as
    the same place. `adjacency` is the set of frozenset pairs that are allowed
    to touch because they are consecutive in the loop (including the closing
    pair) - those meet at a corner by design.

    A grid is used so the cost is linear in the number of points rather than
    quadratic: a boundary of four paths on a real scan carries tens of
    thousands of them, and an O(n^2) check would make Validate cost more than
    the solve it is carefully avoiding. Every candidate found by the grid is
    confirmed by an actual distance before it is reported, so the grid can
    only ever make this test miss something - never invent one.
    """
    usable = [(index, np.asarray(points, dtype=np.float64))
              for index, points in enumerate(polylines)
              if points is not None and len(points) > 0]
    if len(usable) < 2:
        return []
    quantum = float(tolerance)
    if not np.isfinite(quantum) or quantum <= 0.0:
        return []
    allowed = set(adjacency or ())

    stacked = np.vstack([points for _index, points in usable])
    owner = np.concatenate([
        np.full(points.shape[0], index, dtype=np.int64)
        for index, points in usable
    ])

    found = set()
    # Two grids, offset by half a cell, so a pair that straddles a cell
    # boundary in the first is interior to the second. This does not make the
    # test complete - nothing here does - it just makes it miss less.
    for offset in (0.0, 0.5):
        for group in _cell_groups(stacked, quantum, offset):
            segments = owner[group]
            if np.unique(segments).size < 2:
                continue
            block = stacked[group]
            for first in range(block.shape[0]):
                for second in range(first + 1, block.shape[0]):
                    left, right = int(segments[first]), int(segments[second])
                    if left == right:
                        continue
                    pair = frozenset((left, right))
                    if pair in allowed or pair in found:
                        continue
                    gap = float(np.linalg.norm(block[first] - block[second]))
                    if gap <= quantum:
                        found.add(pair)
    return sorted(tuple(sorted(pair)) for pair in found)


def loop_adjacency(count):
    """Pairs of segment positions that meet at a corner by construction."""
    if count < 2:
        return set()
    pairs = {frozenset((index, (index + 1) % count)) for index in range(count)}
    return {pair for pair in pairs if len(pair) == 2}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
#
# Deterministic, pure, and ordered on purpose. THE DEFINITION is judged before
# THE CACHE: a region whose landmark list is degenerate should be told that,
# not told its boundary is stale - recomputing would produce the same
# degenerate loop. And a cache is only ever compared against the definition it
# was computed for, never against one it might plausibly fit.

def _blank(status, code, detail, facts, touch_checked=False):
    return {
        "status": status,
        "code": code,
        "detail": detail,
        "closed": False,
        "computed": False,
        "landmark_count": len(facts.get("landmarks", ())),
        "segment_count": 0,
        "segment_labels": [],
        "corner_labels": list(facts.get("labels", ())),
        "definition": describe_definition(facts.get("labels", ())),
        "length_mm": 0.0,
        "point_count": 0,
        "problems": [],
        "object_name": "",
        "geometry_hash": "",
        "component_id": 0,
        "touch_checked": bool(touch_checked),
        "limits": SELF_INTERSECTION_LIMITS,
    }


def _problem(index, code, detail):
    return {"position": index + 1, "code": code, "detail": detail}


def validate(facts, polylines=None, touch_tolerance=0.0):
    """Is this ordered landmark definition a usable closed boundary?

    `facts` is the dict ``state.region_definition_facts`` builds: the ordered
    landmark facts, the cached segment facts, and the identity of the mesh the
    cache was computed on. `polylines`, if given, are the matching cached point
    arrays in ONE common space, used only for the shared-point half of the
    self-intersection test.

    Returns a result dict; `status` is the verdict, `code` is why, `detail` is
    the sentence to show, and `problems` lists every per-segment fault found
    so the panel can point at rows rather than just refusing.

    Reads no geometry, builds no canonical mesh and calls no solver.
    """
    facts = dict(facts or {})
    landmarks = list(facts.get("landmarks", ()))
    labels = [entry.get("label") or "?" for entry in landmarks]
    facts["labels"] = labels
    ids = [int(entry.get("stable_id") or 0) for entry in landmarks]
    problems = []

    # --- 0. a region carried over from the old path-reference model -------
    #
    # Refused by name rather than reinterpreted. An old region's ordered
    # MEASUREMENT references cannot be turned into ordered LANDMARK ids
    # without guessing which end of each path was meant to come first, and a
    # guess here would silently produce a different boundary than the one the
    # researcher defined.
    if not landmarks and int(facts.get("legacy_segment_count") or 0) > 0:
        return _blank(
            STATUS_INVALID, CODE_LEGACY_DEFINITION,
            "This region was defined in the old measurement-path format, "
            "which BSMT no longer interprets. Add its boundary landmarks in "
            "order to define it again.", facts)

    # --- A. is there a definition at all ---------------------------------
    if not landmarks:
        return _blank(STATUS_DRAFT, CODE_EMPTY,
                      "Add boundary landmarks, in the order they run round "
                      "the region.", facts)

    # --- B. the same landmark twice in a row (the closing pair included) --
    #
    # Before the count check: [A, B, A] has three entries but its closing
    # segment is A->A, and calling that "three landmarks" would be counting
    # the degeneracy as structure.
    count = len(ids)
    for index in range(count):
        following = (index + 1) % count
        if count > 1 and ids[index] == ids[following]:
            problems.append(_problem(
                index, CODE_DEGENERATE_SEQUENCE,
                "%s is followed by itself, so that segment has no length"
                % labels[index]))
    if problems:
        result = _blank(STATUS_INVALID, CODE_DEGENERATE_SEQUENCE,
                        "The same landmark appears twice in a row, so the "
                        "boundary has a zero-length segment.", facts)
        result["problems"] = problems
        return result

    # --- C. a landmark the loop returns to ------------------------------
    seen = {}
    for position, stable_id in enumerate(ids):
        if stable_id in seen:
            problems.append(_problem(
                position, CODE_DUPLICATE_LANDMARK,
                "%s is already boundary landmark %d, so the loop is pinched "
                "rather than simple" % (labels[position], seen[stable_id] + 1)))
        else:
            seen[stable_id] = position
    if problems:
        result = _blank(STATUS_INVALID, CODE_DUPLICATE_LANDMARK,
                        "A landmark appears more than once, so the boundary "
                        "passes through the same point twice.", facts)
        result["problems"] = problems
        return result

    # --- D. enough distinct landmarks to bound anything -------------------
    if count < MIN_LANDMARKS:
        return _blank(STATUS_DRAFT, CODE_INSUFFICIENT_LANDMARKS,
                      "A closed boundary needs at least %d landmarks; this "
                      "one has %d." % (MIN_LANDMARKS, count), facts)

    # --- E. every landmark must exist and be usable ----------------------
    missing = [index for index, entry in enumerate(landmarks)
               if not entry.get("exists")]
    for index in missing:
        problems.append(_problem(
            index, CODE_MISSING_LANDMARK,
            "%s is not in this file any more" % labels[index]))
    if missing:
        result = _blank(STATUS_INVALID, CODE_MISSING_LANDMARK,
                        "%d boundary landmark(s) no longer exist. BSMT never "
                        "substitutes another landmark." % len(missing), facts)
        result["problems"] = problems
        return result

    unpicked = [index for index, entry in enumerate(landmarks)
                if not entry.get("picked")]
    for index in unpicked:
        problems.append(_problem(
            index, CODE_LANDMARK_NOT_PICKED,
            "%s has not been picked on the mesh yet" % labels[index]))
    if unpicked:
        result = _blank(STATUS_INVALID, CODE_LANDMARK_NOT_PICKED,
                        "%d boundary landmark(s) have no surface point. Pick "
                        "them in Landmark Manager." % len(unpicked), facts)
        result["problems"] = problems
        return result

    # --- F. one connected component ---------------------------------------
    components = {int(entry.get("component_id") or 0) for entry in landmarks}
    components.discard(0)
    if len(components) > 1:
        for index, entry in enumerate(landmarks):
            problems.append(_problem(
                index, CODE_CROSS_COMPONENT,
                "%s is on component %d"
                % (labels[index], int(entry.get("component_id") or 0))))
        result = _blank(STATUS_INVALID, CODE_CROSS_COMPONENT,
                        "The boundary landmarks lie on %d disconnected "
                        "surface components (%s). A region must lie on one "
                        "connected component - there is no surface route "
                        "between components."
                        % (len(components),
                           ", ".join(str(value)
                                     for value in sorted(components))), facts)
        result["problems"] = problems
        return result

    # ===================================================================== #
    # the DEFINITION is sound. Everything below is about the CACHED BOUNDARY.
    # ===================================================================== #

    pairs = segment_pairs(ids)
    segments = list(facts.get("segments", ()))
    segment_labels = []
    for position, (from_id, to_id) in enumerate(pairs):
        entry = segments[position] if position < len(segments) else {}
        segment_labels.append(describe_segment({
            "from_label": labels[position],
            "to_label": labels[(position + 1) % count],
            "computed": bool(entry.get("computed")),
        }, position + 1))

    def _with_segments(result):
        result["segment_count"] = len(pairs)
        result["segment_labels"] = segment_labels
        result["corner_labels"] = labels
        result["definition"] = describe_definition(labels)
        result["landmark_count"] = count
        return result

    # --- G. has a boundary ever been computed ----------------------------
    stored_key = str(facts.get("cached_definition") or "")
    if not stored_key:
        return _with_segments(_blank(
            STATUS_DRAFT, CODE_BOUNDARY_NOT_COMPUTED,
            "The landmark definition is complete. Press Compute Boundary to "
            "solve its %d surface segments." % len(pairs), facts))

    # --- H. was it computed for THIS definition --------------------------
    #
    # One string compare, against the definition key recorded at compute
    # time, and it is asked BEFORE completeness. Adding a landmark leaves
    # n+1 pairs against n cached segments; reporting that as "not computed"
    # would hide the fact that a perfectly good boundary for the PREVIOUS
    # definition is sitting there, and would tell the researcher they had
    # never computed anything. Stale is the truth: there IS a boundary, and
    # it belongs to a definition this region no longer has.
    if stored_key != definition_key(ids):
        return _with_segments(_blank(
            STATUS_STALE, CODE_BOUNDARY_STALE,
            "The landmark definition changed after the boundary was "
            "computed. Press Compute Boundary again.", facts))

    # --- H.2 is the cache actually all there ------------------------------
    #
    # The key matches, so this cache IS for this definition - but a segment's
    # polyline datablock can still be gone (a purged file, a hand-edited
    # scene). A boundary with a hole in it is not a boundary.
    if len(segments) != len(pairs) \
            or not all(entry.get("computed") for entry in segments):
        return _with_segments(_blank(
            STATUS_DRAFT, CODE_BOUNDARY_NOT_COMPUTED,
            "Part of this boundary is missing its computed path. Press "
            "Compute Boundary to solve its %d surface segments."
            % len(pairs), facts))

    # --- I. do the cached segments still name the right pairs ------------
    for position, (from_id, to_id) in enumerate(pairs):
        entry = segments[position]
        if int(entry.get("from_landmark") or 0) != from_id or \
                int(entry.get("to_landmark") or 0) != to_id:
            problems.append(_problem(
                position, CODE_BOUNDARY_STALE,
                "segment %d was computed for a different landmark pair"
                % (position + 1)))
    if problems:
        result = _with_segments(_blank(
            STATUS_STALE, CODE_BOUNDARY_STALE,
            "The cached boundary does not match the current landmarks. Press "
            "Compute Boundary again.", facts))
        result["problems"] = problems
        return result

    # --- J. same mesh, same geometry -------------------------------------
    objects = {str(entry.get("object_name") or "") for entry in segments}
    objects.discard("")
    hashes = {str(entry.get("geometry_hash") or "") for entry in segments}
    hashes.discard("")
    live_hash = str(facts.get("live_geometry_hash") or "")
    if len(objects) > 1 or len(hashes) > 1:
        return _with_segments(_blank(
            STATUS_STALE, CODE_GEOMETRY_MISMATCH,
            "The boundary segments were computed on different versions of "
            "the mesh. Press Compute Boundary again.", facts))
    if live_hash and hashes and live_hash not in hashes:
        return _with_segments(_blank(
            STATUS_STALE, CODE_GEOMETRY_MISMATCH,
            "The mesh geometry changed after the boundary was computed. "
            "Press Compute Boundary again.", facts))

    # --- K. have the landmarks moved since the solve ----------------------
    #
    # A re-picked landmark is the commonest way a boundary stops being the
    # answer, and it leaves the definition untouched - the ids are the same,
    # the POSITIONS are not. Compared against the endpoint identity recorded
    # at compute time, never against coordinates.
    moved = []
    for position, entry in enumerate(segments):
        landmark = landmarks[position]
        following = landmarks[(position + 1) % count]
        if int(entry.get("from_triangle", -1)) != int(
                landmark.get("triangle", -1)) or \
                int(entry.get("to_triangle", -1)) != int(
                    following.get("triangle", -1)):
            moved.append(position)
            problems.append(_problem(
                position, CODE_LANDMARK_STALE,
                "%s or %s has been re-picked since the boundary was computed"
                % (labels[position], labels[(position + 1) % count])))
    if moved:
        result = _with_segments(_blank(
            STATUS_STALE, CODE_LANDMARK_STALE,
            "%d boundary landmark(s) have been re-picked since the boundary "
            "was computed. Press Compute Boundary again." % len(set(moved)),
            facts))
        result["problems"] = problems
        return result

    stale_landmarks = [index for index, entry in enumerate(landmarks)
                       if entry.get("status") in (LANDMARK_STALE,
                                                  LANDMARK_INVALID)]
    for index in stale_landmarks:
        problems.append(_problem(
            index, CODE_LANDMARK_STALE,
            "%s is %s" % (labels[index],
                          str(landmarks[index].get("status") or "").lower())))
    if stale_landmarks:
        result = _with_segments(_blank(
            STATUS_STALE, CODE_LANDMARK_STALE,
            "%d boundary landmark(s) are stale or invalid. Fix them, then "
            "press Compute Boundary again." % len(stale_landmarks), facts))
        result["problems"] = problems
        return result

    # --- L. the boundary meets itself ------------------------------------
    touch_checked = polylines is not None and touch_tolerance > 0.0
    if touch_checked:
        touches = touching_segments(polylines, touch_tolerance,
                                    loop_adjacency(len(pairs)))
        if touches:
            for left, right in touches:
                problems.append(_problem(
                    right, CODE_SELF_INTERSECTION,
                    "segments %d and %d share a point on the surface"
                    % (left + 1, right + 1)))
            result = _with_segments(_blank(
                STATUS_INVALID, CODE_SELF_INTERSECTION,
                "The boundary touches itself: %s. %s"
                % ("; ".join("segments %d and %d" % (left + 1, right + 1)
                             for left, right in touches),
                   SELF_INTERSECTION_LIMITS), facts, touch_checked=True))
            result["problems"] = problems
            return result

    length = sum(float(entry.get("length_mm") or 0.0) for entry in segments)
    points = sum(int(entry.get("point_count") or 0) for entry in segments)

    result = _with_segments(_blank(
        STATUS_VALID, CODE_NONE,
        "Closed boundary: %d landmarks, %d surface segments, %.1f mm around."
        % (count, len(pairs), length), facts, touch_checked=touch_checked))
    result["closed"] = True
    result["computed"] = True
    result["length_mm"] = float(length)
    result["point_count"] = int(points)
    result["object_name"] = sorted(objects)[0] if objects else ""
    result["geometry_hash"] = sorted(hashes)[0] if hashes else ""
    result["component_id"] = sorted(components)[0] if components else 0
    result["problems"] = problems
    return result


#: Shown beside a verdict that did NOT get the shared-point test. A VALID
#: that has not run it has checked LESS than one that has, and the difference
#: must be visible rather than inferred from which button was last pressed.
TOUCH_NOT_RUN = (
    "the shared-point self-intersection test was NOT run for this verdict "
    "(it needs the cached mesh and an explicit Validate Region)"
)


def summary_lines(result):
    """The validation result as lines for a panel or the console.

    The last lines are the SCOPE of the verdict, not decoration. A closed
    boundary is reported as closed; it is not reported as simple, because
    `SELF_INTERSECTION_LIMITS` says plainly that BSMT cannot prove that. A
    reader who sees only "VALID" would supply the missing claim themselves.
    """
    lines = ["%s - %s" % (STATUS_SHORT.get(result["status"],
                                           result["status"]),
                          result["detail"])]
    for problem in result["problems"]:
        lines.append("  segment %d: %s" % (problem["position"],
                                           problem["detail"]))
    if result["definition"]:
        lines.append("  boundary: %s" % result["definition"])
    if result.get("touch_checked"):
        lines.append("  scope: %s" % SELF_INTERSECTION_LIMITS)
    else:
        lines.append("  scope: %s. %s" % (TOUCH_NOT_RUN,
                                          SELF_INTERSECTION_LIMITS))
    return lines
