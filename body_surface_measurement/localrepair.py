"""Local face repair inside a component that may not be deleted. Pure numpy.

Milestone 3.30. ``artifact.py`` answers "may this whole connected component
go?" and, for the primary body, correctly answers NO - deleting the component
holding 92% of a scan is not a repair. That refusal is right and stays. It
also leaves a real gap: a single non-manifold edge less than a millimetre
across, sitting on the body itself, currently has no repair at all.

This module answers a much narrower question about that defect:

    when the defect's non-manifold edges are treated as a SEPARATOR, does
    the local surface fall into exactly one continuation that keeps going
    into the body, and exactly one small branch that stops?

If it does, that small branch is a removable candidate and this module says
so, with its face count, vertex count, area, bounding box and reach. If it
does not - two comparable branches, three or more branches, a branch that
also runs away into the body - the answer is AMBIGUOUS and no destructive
operation is offered. Refusing is the designed outcome, not a fallback: a
false refusal costs the researcher a manual edit, a false positive deletes
anatomy from a research measurement.

What this is NOT
----------------
Not component deletion (``artifact.py``), not a weld (``meshrepair.
weld_non_manifold_region``), not a global cleanup, not remeshing, not
duplicate-face removal - when the defect turns out to be a duplicated face
this module CLASSIFIES it and hands the researcher to the existing
``bsmt.remove_duplicate_faces``, rather than growing a second implementation
of the same edit.

Three thresholds, and what they are
-----------------------------------
``LOCAL_EXPLORE_FACES``, ``MAX_CANDIDATE_FACES`` and ``DOMINANCE_RATIO`` are
SOFTWARE SAFETY CAPS. None of them is an anatomical claim - BSMT does not
decide whether geometry is anatomically relevant, here or anywhere else. They
exist so that a bug in the branch walk cannot turn into a large deletion, and
they are reported in the UI so the researcher can see the rule that was
applied. The structural test is the branch separation above; size is evidence
shown alongside it, never the criterion on its own.

No bpy, so every decision here is testable outside Blender, and no
pygeodesic: this is mesh topology, and the solver has nothing to say about it.
"""

import numpy as np

from . import repair

# ---------------------------------------------------------------------------
# software safety caps (sect. 3 of the brief - NOT anatomical criteria)
# ---------------------------------------------------------------------------

#: How far the branch walk is allowed to run before it declares a branch
#: "unbounded" - that is, a surface that keeps going and is therefore the
#: body's own continuation rather than a local artefact. A cap, not a
#: measurement: the walk stops here and reports ">= this many faces", which is
#: all the classifier needs to know to call that branch dominant.
LOCAL_EXPLORE_FACES = 512

#: A candidate branch larger than this is refused outright, whatever the
#: dominance ratio says. The backstop that makes a walk bug bounded.
MAX_CANDIDATE_FACES = 64

#: The dominant continuation must hold at least this many times the
#: candidate's faces. Relative, on purpose: "smaller than the body by a large
#: factor" is a statement about the local topology, where "under N triangles"
#: would be a claim about anatomy that BSMT is not entitled to make.
DOMINANCE_RATIO = 8

#: Positions are quantised to this many millimetres for the locality proof.
#: Matches ``repair.SIGNATURE_TOLERANCE_MM`` in spirit; face removal moves no
#: surviving vertex at all, so this only absorbs float printing noise.
LOCALITY_TOLERANCE = 1e-6

# ---------------------------------------------------------------------------
# classifications
# ---------------------------------------------------------------------------

LOCAL_DUPLICATE_FACE = 'LOCAL_DUPLICATE_FACE'
LOCAL_DANGLING_FLAP = 'SMALL_DANGLING_FLAP'
LOCAL_SMALL_BRANCH = 'SMALL_LOCAL_BRANCH'
LOCAL_AMBIGUOUS = 'AMBIGUOUS'
LOCAL_UNSUPPORTED = 'UNSUPPORTED'

#: The classifications for which a removal is offered at all.
REMOVABLE = (LOCAL_DANGLING_FLAP, LOCAL_SMALL_BRANCH)

CLASSIFICATION_LABELS = {
    LOCAL_DUPLICATE_FACE: "Duplicate / reversed duplicate face",
    LOCAL_DANGLING_FLAP: "Small dangling flap",
    LOCAL_SMALL_BRANCH: "Small local branch",
    LOCAL_AMBIGUOUS: "Ambiguous / unsupported local topology",
    LOCAL_UNSUPPORTED: "Unsupported local topology",
}

#: Shown verbatim whenever no candidate could be identified.
MANUAL_EDIT_MESSAGE = (
    "Automatic local repair is not offered. Manual mesh inspection is "
    "required."
)

#: Shown verbatim when the defect is a duplicated face. The existing repair
#: already handles this case safely and is not reimplemented here.
DUPLICATE_DELEGATE_MESSAGE = (
    "This defect is caused by duplicated face(s). "
    "Use Remove Duplicate Faces, "
    "which already removes exactly the repeated copy."
)


# ---------------------------------------------------------------------------
# local incidence, without a whole-mesh python pass
# ---------------------------------------------------------------------------

def _edge_codes(faces, vertex_count):
    """Every face corner's edge as one int64 code, plus the owning face.

    ``a * vertex_count + b`` with a < b. Pure numpy and O(m): the python dict
    in ``repair.edge_incidence`` costs about three seconds on a 350,000
    triangle body scan, and nothing here needs a dict of the whole mesh -
    only the rows belonging to a handful of defect edges.
    """
    faces = np.asarray(faces, dtype=np.int64)
    pairs = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]],
                            faces[:, [2, 0]]), axis=0)
    pairs = np.sort(pairs, axis=1)
    codes = pairs[:, 0] * np.int64(vertex_count) + pairs[:, 1]
    owners = np.tile(np.arange(faces.shape[0], dtype=np.int64), 3)
    return codes, owners


def _code_of(edges, vertex_count):
    edges = np.asarray(edges, dtype=np.int64)
    if edges.size == 0:
        return np.zeros(0, dtype=np.int64)
    ordered = np.sort(edges.reshape(-1, 2), axis=1)
    return ordered[:, 0] * np.int64(vertex_count) + ordered[:, 1]


def incident_faces(faces, edges, vertex_count):
    """Faces using each given edge. Returns {edge_code: sorted face indices}."""
    codes, owners = _edge_codes(faces, vertex_count)
    wanted = _code_of(edges, vertex_count)
    result = {}
    for code in wanted:
        rows = owners[codes == code]
        result[int(code)] = np.unique(rows)
    return result


# ---------------------------------------------------------------------------
# the branch walk
# ---------------------------------------------------------------------------

def local_branches(faces, defect_edges, vertex_count,
                   explore_cap=LOCAL_EXPLORE_FACES):
    """Walk the surface outward from the defect, with the defect as a wall.

    The defect's non-manifold edges are removed from the adjacency graph, so
    two faces that meet ONLY across a defect edge are not neighbours. What
    comes back is the connected face groups that remain, seeded from the
    defect's own incident faces.

    Each branch reports whether it hit `explore_cap`. A branch that did is
    UNBOUNDED: the walk stopped, the surface did not, and that is the body's
    own continuation rather than a local artefact. A branch that stopped on
    its own is a closed local patch and is a candidate for removal.

    Deliberately bounded. An unbounded flood over the primary component of a
    350,000-triangle scan is the expensive whole-mesh operation sect. 1 of the
    brief rules out, and the answer it would give - "this branch is huge" -
    is already known the moment the cap is reached.
    """
    faces = np.asarray(faces, dtype=np.int64)
    codes, owners = _edge_codes(faces, vertex_count)
    defect_codes = set(int(code) for code in _code_of(defect_edges,
                                                     vertex_count))

    seeds = sorted({int(index) for code in defect_codes
                    for index in owners[codes == code]})
    if not seeds:
        return []

    # Adjacency is built lazily and only around the walk: one numpy pass per
    # edge actually visited, never a dict of the whole mesh.
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    sorted_owners = owners[order]

    def neighbours(face_index):
        a, b, c = faces[face_index]
        found = []
        for first, second in ((a, b), (b, c), (c, a)):
            low, high = (int(first), int(second)) if first < second \
                else (int(second), int(first))
            code = low * vertex_count + high
            if code in defect_codes:
                continue           # the wall: this edge does not conduct
            start = int(np.searchsorted(sorted_codes, code, side="left"))
            stop = int(np.searchsorted(sorted_codes, code, side="right"))
            for owner in sorted_owners[start:stop]:
                if int(owner) != int(face_index):
                    found.append(int(owner))
        return found

    branches = []
    assigned = {}
    for seed in seeds:
        if seed in assigned:
            continue
        members = {seed}
        frontier = [seed]
        unbounded = False
        while frontier:
            if len(members) >= explore_cap:
                unbounded = True
                break
            current = frontier.pop()
            for neighbour in neighbours(current):
                if neighbour in members:
                    continue
                members.add(neighbour)
                frontier.append(neighbour)
                if len(members) >= explore_cap:
                    unbounded = True
                    break
            if unbounded:
                break
        index = len(branches)
        for member in members:
            assigned.setdefault(member, index)
        branches.append({
            "faces": np.asarray(sorted(members), dtype=np.int64),
            "face_count": len(members),
            "unbounded": bool(unbounded),
            "seeds": sorted(seed_index for seed_index in seeds
                            if seed_index in members),
        })

    branches.sort(key=lambda entry: (not entry["unbounded"],
                                     -entry["face_count"]))
    return branches


# ---------------------------------------------------------------------------
# measuring a candidate
# ---------------------------------------------------------------------------

def measure_candidate(vertices, faces, candidate_faces, defect_vertices):
    """Everything the UI has to state about a proposed deletion."""
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    candidate = np.asarray(candidate_faces, dtype=np.int64)

    local = faces[candidate] if candidate.size else np.zeros((0, 3),
                                                             dtype=np.int64)
    used = np.unique(local) if local.size else np.zeros(0, dtype=np.int64)

    area = 0.0
    if local.size:
        corners = vertices[local]
        area = float(0.5 * np.linalg.norm(
            np.cross(corners[:, 1] - corners[:, 0],
                     corners[:, 2] - corners[:, 0]), axis=1).sum())

    if used.size:
        points = vertices[used]
        extent = points.max(axis=0) - points.min(axis=0)
        center = points.mean(axis=0)
    else:
        extent = np.zeros(3, dtype=np.float64)
        center = np.zeros(3, dtype=np.float64)

    # Reach: how far the candidate gets from the defect it hangs off. The
    # geometric figure is what a researcher can check against the scan; the
    # topological one is what says "this is a couple of triangles", which a
    # millimetre figure on an unknown mesh resolution cannot say.
    defect_points = vertices[np.asarray(defect_vertices, dtype=np.int64)] \
        if len(defect_vertices) else np.zeros((0, 3), dtype=np.float64)
    reach_mm = 0.0
    if used.size and defect_points.size:
        distances = np.linalg.norm(
            vertices[used][:, None, :] - defect_points[None, :, :], axis=2)
        reach_mm = float(distances.min(axis=1).max())

    # Faces used ONLY by the candidate strand their vertices when the
    # candidate goes; bmesh deletes exactly those, so they are counted here
    # and asserted afterwards.
    keep = np.ones(faces.shape[0], dtype=bool)
    if candidate.size:
        keep[candidate] = False
    survivors = np.unique(faces[keep]) if keep.any() else \
        np.zeros(0, dtype=np.int64)
    dropped = np.setdiff1d(used, survivors, assume_unique=False)

    return {
        "face_indices": candidate,
        "face_count": int(candidate.size),
        "vertex_indices": used,
        "vertex_count": int(used.size),
        "dropped_vertex_indices": dropped,
        "dropped_vertex_count": int(dropped.size),
        "area_mm2": area,
        "bbox_mm": [float(value) for value in extent],
        "bbox_diagonal_mm": float(np.linalg.norm(extent)),
        "center_mm": [float(value) for value in center],
        "reach_mm": reach_mm,
        "face_keys": sorted(tuple(sorted(int(v) for v in row))
                            for row in local),
    }


def predict_removal(vertices, faces, candidate_faces):
    """What removing the candidate would do to the topology. Nothing is edited.

    Reported before anything is executed, so the researcher sees the claimed
    outcome and the operator can refuse a candidate whose removal would not
    actually help - rather than finding that out after the edit and rolling
    back.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    candidate = np.asarray(candidate_faces, dtype=np.int64)

    keep = np.ones(faces.shape[0], dtype=bool)
    if candidate.size:
        keep[candidate] = False
    remaining = faces[keep]

    vertex_count = int(vertices.shape[0])
    before = repair.classify_edges(faces, vertex_count)
    after = repair.classify_edges(remaining, vertex_count)

    before_signature = repair.nonmanifold_signature(vertices, faces)
    after_signature = repair.nonmanifold_signature(vertices, remaining)

    return {
        "nonmanifold_before": int(before["non_manifold"].shape[0]),
        "nonmanifold_after": int(after["non_manifold"].shape[0]),
        "boundary_before": int(before["boundary"].shape[0]),
        "boundary_after": int(after["boundary"].shape[0]),
        "triangles_after": int(remaining.shape[0]),
        "introduced": after_signature - before_signature,
        "degenerate_before": repair.degenerate_signature(vertices, faces),
        "degenerate_after": repair.degenerate_signature(vertices, remaining),
    }


# ---------------------------------------------------------------------------
# the classifier
# ---------------------------------------------------------------------------

def classify_local_defect(vertices, faces, defect_edges, mean_edge_mm=0.0,
                          explore_cap=LOCAL_EXPLORE_FACES,
                          max_candidate_faces=MAX_CANDIDATE_FACES,
                          dominance_ratio=DOMINANCE_RATIO):
    """Inspect ONE non-manifold defect. Returns a report dict.

    `defect_edges` is the (k, 2) vertex-index array of that defect's own
    non-manifold edges, exactly as ``artifact.defect_regions`` reports them.

    The result always carries `classification`, `reason` and `removable`.
    `candidate` is present only when `removable` is True, and it is the ONLY
    thing a removal may act on.

    Order of questions, and why:

      1. is the defect a duplicated face? -> hand it to the existing repair,
         which already removes exactly the repeated copy (sect. 12);
      2. does the defect separate the local surface into exactly two
         branches, one of which keeps going? -> that is the structural test,
         and it is what decides;
      3. do the software safety caps hold? -> a backstop on (2), reported so
         the rule is visible;
      4. would removing the candidate actually resolve the defect without
         creating a new one anywhere? -> predicted, never assumed.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    edges = np.asarray(defect_edges, dtype=np.int64).reshape(-1, 2)
    vertex_count = int(vertices.shape[0])

    report = {
        "classification": LOCAL_UNSUPPORTED,
        "reason": "",
        "removable": False,
        "candidate": None,
        "defect_edge_count": int(edges.shape[0]),
        "defect_vertex_indices": np.unique(edges) if edges.size
        else np.zeros(0, dtype=np.int64),
        "incident_face_counts": [],
        "incident_face_count": 0,
        "branches": [],
        "duplicate_face_count": 0,
        "caps": {
            "explore_faces": int(explore_cap),
            "max_candidate_faces": int(max_candidate_faces),
            "dominance_ratio": int(dominance_ratio),
            "diagonal_limit_mm": repair.region_diagonal_limit(mean_edge_mm),
        },
        "prediction": None,
    }

    if edges.size == 0:
        report["classification"] = LOCAL_UNSUPPORTED
        report["reason"] = "this defect has no non-manifold edges"
        return report

    incidence = incident_faces(faces, edges, vertex_count)
    counts = [int(value.size) for value in incidence.values()]
    report["incident_face_counts"] = sorted(counts, reverse=True)
    all_incident = np.unique(np.concatenate(
        [value for value in incidence.values() if value.size])) \
        if any(counts) else np.zeros(0, dtype=np.int64)
    report["incident_face_count"] = int(all_incident.size)

    if all_incident.size == 0:
        report["reason"] = ("no face uses this defect's edges any more - "
                            "re-analyze the mesh")
        return report

    # --- 1. a duplicated face is somebody else's job ---------------------
    local_keys = [tuple(sorted(int(v) for v in faces[index]))
                  for index in all_incident]
    repeated = len(local_keys) - len(set(local_keys))
    if repeated:
        report["classification"] = LOCAL_DUPLICATE_FACE
        report["duplicate_face_count"] = int(repeated)
        report["reason"] = DUPLICATE_DELEGATE_MESSAGE
        return report

    # --- 2. the structural test ------------------------------------------
    branches = local_branches(faces, edges, vertex_count,
                              explore_cap=explore_cap)
    report["branches"] = [
        {"face_count": branch["face_count"],
         "unbounded": branch["unbounded"]}
        for branch in branches
    ]

    if len(branches) < 2:
        report["classification"] = LOCAL_AMBIGUOUS
        report["reason"] = (
            "removing the defect edge(s) as a separator leaves the local "
            "surface in one piece, so there is no branch to remove - the "
            "defect is woven into the surface rather than hanging off it"
        )
        return report
    # A branch that ran past the cap is a CONTINUATION: the walk stopped, the
    # surface did not. Two of them are not two candidates - on a real body
    # scan the two sides of a defect edge reconnect only by going right round
    # the torso, thousands of faces away, so a bounded walk from each side
    # necessarily reports two. Treating that as ambiguity would refuse
    # precisely the case this milestone exists for. They are pooled as "the
    # surface keeps going", which is the only thing a local inspection can
    # honestly say about them.
    continuations = [branch for branch in branches if branch["unbounded"]]
    bounded = [branch for branch in branches if not branch["unbounded"]]

    if continuations:
        dominant_size = max(branch["face_count"] for branch in continuations)
        dominant_unbounded = True
        candidates = bounded
    else:
        # Nothing ran away, so the local surface is fully described: the
        # largest branch stands in for the body and everything else is a
        # candidate. Which is why the dominance check below is what carries
        # the decision in this case.
        dominant_size = bounded[0]["face_count"]
        dominant_unbounded = False
        candidates = bounded[1:]

    if not candidates:
        report["classification"] = LOCAL_AMBIGUOUS
        report["reason"] = (
            "every local branch at this defect keeps going past the %d-face "
            "inspection cap, so none of them is a local artefact"
            % explore_cap
        )
        return report
    if len(candidates) > 1:
        report["classification"] = LOCAL_AMBIGUOUS
        report["reason"] = (
            "the non-manifold edge(s) separate the local surface into %d "
            "branches, leaving %d candidate branches (%s faces) against the "
            "body surface; BSMT offers a removal only when exactly one small "
            "branch stands against one continuing body surface"
            % (len(branches), len(candidates),
               " and ".join(str(branch["face_count"])
                            for branch in candidates))
        )
        return report

    candidate_branch = candidates[0]
    if dominant_size < dominance_ratio * candidate_branch["face_count"]:
        report["classification"] = LOCAL_AMBIGUOUS
        report["reason"] = (
            "the non-manifold edge separates two similarly sized local "
            "surface branches (%d and %d faces); no safe automatic deletion "
            "candidate could be identified"
            % (dominant_size, candidate_branch["face_count"])
        )
        return report
    dominant = {"face_count": dominant_size, "unbounded": dominant_unbounded}

    candidate = measure_candidate(vertices, faces, candidate_branch["faces"],
                                  report["defect_vertex_indices"])
    candidate["dominant_face_count"] = int(dominant["face_count"])
    candidate["dominant_unbounded"] = bool(dominant["unbounded"])

    # --- 3. the software safety caps -------------------------------------
    if candidate["face_count"] > max_candidate_faces:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = (
            "the candidate branch holds %d faces, above the %d-face software "
            "safety cap on a local removal" % (candidate["face_count"],
                                               max_candidate_faces)
        )
        return report
    limit = report["caps"]["diagonal_limit_mm"]
    if candidate["bbox_diagonal_mm"] > limit:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = (
            "the candidate branch is %.2f mm across, above the %.1f mm "
            "software safety cap for a local artefact on this mesh"
            % (candidate["bbox_diagonal_mm"], limit)
        )
        return report

    # --- 4. would it actually help? --------------------------------------
    prediction = predict_removal(vertices, faces, candidate_branch["faces"])
    report["prediction"] = prediction
    if prediction["nonmanifold_after"] >= prediction["nonmanifold_before"]:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = (
            "removing the candidate would not reduce the non-manifold edge "
            "count (%d -> %d), so it is not the repair for this defect"
            % (prediction["nonmanifold_before"],
               prediction["nonmanifold_after"])
        )
        return report
    if prediction["introduced"]:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = (
            "removing the candidate would introduce %d new non-manifold "
            "edge(s) elsewhere" % len(prediction["introduced"])
        )
        return report
    new_degenerate = prediction["degenerate_after"] - \
        prediction["degenerate_before"]
    if new_degenerate:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = (
            "removing the candidate would create %d degenerate triangle(s)"
            % len(new_degenerate)
        )
        return report
    if prediction["triangles_after"] <= 0:
        report["classification"] = LOCAL_AMBIGUOUS
        report["candidate"] = candidate
        report["reason"] = "removing the candidate would leave no triangles"
        return report

    # A flap HANGS OFF: at least one of its vertices is used by nothing else,
    # so the candidate has an edge of the surface to itself. A branch that
    # shares every vertex with the surviving surface is a redundant patch
    # lying over it. Both are removable; they are different shapes and are
    # named differently so the researcher sees which one they are looking at.
    report["classification"] = (LOCAL_DANGLING_FLAP
                                if candidate["dropped_vertex_count"]
                                else LOCAL_SMALL_BRANCH)
    report["removable"] = True
    report["candidate"] = candidate
    report["reason"] = (
        "the defect's %d edge(s) separate a %d-face local branch from a "
        "continuing body surface of %s%d faces"
        % (report["defect_edge_count"], candidate["face_count"],
           ">=" if dominant["unbounded"] else "", dominant["face_count"])
    )
    return report


# ---------------------------------------------------------------------------
# what the UI says
# ---------------------------------------------------------------------------

def report_lines(report, defect_id=0):
    """The inspection result, in the order sect. 16 of the brief asks for."""
    lines = []
    if defect_id:
        lines.append("Focused defect: D%d" % int(defect_id))
    else:
        lines.append("Focused defect")
    lines.append("  Non-manifold edges:  %d" % report["defect_edge_count"])
    lines.append("  Incident faces:      %d%s"
                 % (report["incident_face_count"],
                    ("  (per edge: %s)"
                     % ", ".join(str(value)
                                 for value in report["incident_face_counts"]))
                    if report["incident_face_counts"] else ""))
    if report["branches"]:
        lines.append("  Local branches:      %s"
                     % ", ".join("%s%d faces" % (">=" if branch["unbounded"]
                                                 else "", branch["face_count"])
                                 for branch in report["branches"]))
    lines.append("")
    lines.append("Classification:")
    lines.append("  %s" % CLASSIFICATION_LABELS.get(
        report["classification"], report["classification"]))
    lines.append("")

    candidate = report["candidate"]
    if report["removable"] and candidate is not None:
        bbox = candidate["bbox_mm"]
        lines.append("Candidate:")
        lines.append("  Faces:         %d" % candidate["face_count"])
        lines.append("  Vertices:      %d" % candidate["vertex_count"])
        lines.append("  Area:          %.4f mm2" % candidate["area_mm2"])
        lines.append("  Bounding box:  %.2f x %.2f x %.2f mm"
                     % (bbox[0], bbox[1], bbox[2]))
        lines.append("  Maximum reach: %.2f mm from the defect"
                     % candidate["reach_mm"])
        lines.append("  Body branch:   %s%d faces"
                     % (">=" if candidate.get("dominant_unbounded") else "",
                        candidate.get("dominant_face_count", 0)))
        prediction = report["prediction"] or {}
        if prediction:
            lines.append("  Non-manifold:  %d -> %d (predicted)"
                         % (prediction["nonmanifold_before"],
                            prediction["nonmanifold_after"]))
            delta = (prediction["boundary_after"]
                     - prediction["boundary_before"])
            if delta > 0:
                lines.append("  Boundary edges: +%d (predicted) - removing a "
                             "flap exposes the boundary it was hiding" % delta)
        lines.append("")
        lines.append("Reason:")
        lines.extend("  " + line for line in _wrapped(report["reason"]))
    else:
        lines.append("Reason:")
        lines.extend("  " + line for line in _wrapped(report["reason"]))
        lines.append("")
        if report["classification"] == LOCAL_DUPLICATE_FACE:
            lines.append("%d duplicated face(s) at this defect."
                         % report["duplicate_face_count"])
        else:
            lines.append(MANUAL_EDIT_MESSAGE)
    lines.append("")
    lines.append("Safety caps (software, not anatomical):")
    caps = report["caps"]
    lines.append("  inspection cap %d faces, candidate cap %d faces,"
                 % (caps["explore_faces"], caps["max_candidate_faces"]))
    lines.append("  body branch at least %dx the candidate, candidate at "
                 "most %.1f mm across"
                 % (caps["dominance_ratio"], caps["diagonal_limit_mm"]))
    return lines


def _wrapped(text, width=52):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word) if current else word
    if current:
        lines.append(current)
    return lines or [""]


def confirmation_lines(report, defect_id=0, object_name="",
                       landmark_count=0, measurement_count=0):
    """What the confirmation dialog says, in order (sect. 6 of the brief)."""
    candidate = report["candidate"] or {}
    bbox = candidate.get("bbox_mm", [0.0, 0.0, 0.0])
    prediction = report["prediction"] or {}
    lines = [
        "Remove the local candidate faces attached to the focused",
        "non-manifold defect?",
        "",
        "Only the measurement mesh will be modified. The original",
        "source scan will remain unchanged.",
        "",
        "Defect D%d%s" % (int(defect_id),
                          " of '%s'" % object_name if object_name else ""),
        "  Classification:  %s" % CLASSIFICATION_LABELS.get(
            report["classification"], report["classification"]),
        "  Faces to remove: %d" % candidate.get("face_count", 0),
        "  Vertices freed:  %d" % candidate.get("dropped_vertex_count", 0),
        "  Surface area:    %.4f mm2" % candidate.get("area_mm2", 0.0),
        "  Bounding box:    %.2f x %.2f x %.2f mm" % (bbox[0], bbox[1],
                                                      bbox[2]),
        "  Maximum reach:   %.2f mm from the defect"
        % candidate.get("reach_mm", 0.0),
    ]
    if prediction:
        lines.append("  Non-manifold:    %d -> %d (predicted)"
                     % (prediction.get("nonmanifold_before", 0),
                        prediction.get("nonmanifold_after", 0)))
        delta = (prediction.get("boundary_after", 0)
                 - prediction.get("boundary_before", 0))
        if delta > 0:
            lines.append("  Boundary edges:  +%d - the flap was covering an "
                         "existing local boundary" % delta)
    lines.append("")
    lines.append("Existing landmarks, measurements, cached paths and")
    lines.append("surface regions may become stale.")
    if landmark_count or measurement_count:
        lines.append("%d landmark(s) and %d measurement(s) on this mesh will"
                     % (landmark_count, measurement_count))
        lines.append("be restated after the removal.")
    return lines


def describe(report):
    """One line, for a log entry or an operator report."""
    candidate = report["candidate"] or {}
    label = CLASSIFICATION_LABELS.get(report["classification"],
                                      report["classification"])
    if not report["removable"]:
        return "%s - %s" % (label, report["reason"])
    return ("%s: %d face(s), %d vertex/vertices, %.4f mm2, %.2f mm across"
            % (label, candidate.get("face_count", 0),
               candidate.get("vertex_count", 0),
               candidate.get("area_mm2", 0.0),
               candidate.get("bbox_diagonal_mm", 0.0)))


# ---------------------------------------------------------------------------
# geometry locality (sect. 9)
# ---------------------------------------------------------------------------

def face_position_signature(vertices, faces, tolerance=LOCALITY_TOLERANCE):
    """Every triangle keyed by the POSITIONS of its corners, sorted.

    Index-free on purpose. Deleting faces renumbers vertices and polygons, so
    an index-based before/after comparison reports the whole mesh as changed
    and proves nothing. A quantised corner position does not move when its
    neighbours are renumbered, so two signatures compare exactly what the
    locality claim is about: is the SURFACE outside the approved candidate
    the same surface it was?

    Returns an (m, 9) int64 array, lexicographically sorted, so equality is a
    single ``np.array_equal``.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return np.zeros((0, 9), dtype=np.int64)
    quantised = np.rint(vertices / float(tolerance)).astype(np.int64)
    corners = quantised[faces]                        # (m, 3, 3)
    # Corners sorted within a face, so a reversed winding is the same surface.
    keys = corners.reshape(faces.shape[0], 3, 3)
    order = np.lexsort((keys[:, :, 2], keys[:, :, 1], keys[:, :, 0]), axis=1)
    keys = np.take_along_axis(keys, order[:, :, None], axis=1)
    flat = keys.reshape(faces.shape[0], 9)
    rows = np.lexsort([flat[:, column] for column in range(8, -1, -1)])
    return flat[rows]


def locality_reasons(before_vertices, before_faces, candidate_faces,
                     after_vertices, after_faces,
                     tolerance=LOCALITY_TOLERANCE):
    """Did the edit change anything outside the approved candidate? (reasons).

    The whole claim of a LOCAL repair, checked rather than asserted: the
    surface after the edit must be exactly the surface before it, minus the
    approved candidate faces and nothing else. Every returned string is a
    reason to roll back.
    """
    before_faces = np.asarray(before_faces, dtype=np.int64)
    candidate = np.asarray(candidate_faces, dtype=np.int64)
    keep = np.ones(before_faces.shape[0], dtype=bool)
    if candidate.size:
        keep[candidate] = False

    expected = face_position_signature(before_vertices, before_faces[keep],
                                       tolerance)
    actual = face_position_signature(after_vertices, after_faces, tolerance)

    reasons = []
    if expected.shape[0] != actual.shape[0]:
        reasons.append(
            "the edit left %d triangle(s), not the %d that removing the "
            "approved candidate accounts for"
            % (actual.shape[0], expected.shape[0])
        )
        return reasons
    if not np.array_equal(expected, actual):
        differing = int(np.count_nonzero(
            np.any(expected != actual, axis=1)))
        reasons.append(
            "%d triangle(s) outside the approved candidate changed position; "
            "the edit was not local" % differing
        )
    return reasons


# ---------------------------------------------------------------------------
# acceptance (sect. 8)
# ---------------------------------------------------------------------------

def extra_removal_reasons(before, after, expected):
    """The local-removal half of the acceptance rule. (reasons to revert).

    Added to whatever ``repair.accept_repair`` already produced, never
    instead of it - the operator asks the authoritative rule first and in
    full, and these can only ever make acceptance stricter.

    `expected` carries what the approved candidate accounted for:
    `face_count`, `dropped_vertex_count`, and the midpoint `signature` of the
    focused defect's own non-manifold edges.

    Boundary edges are deliberately NOT capped here: removing a flap exposes
    the boundary the flap was covering, and a boundary edge is warning-only in
    the current policy. The increase is reported, not refused.
    """
    reasons = []

    degenerate_before = int(before.get("degenerate_triangle_count", 0) or 0)
    degenerate_after = int(after.get("degenerate_triangle_count", 0) or 0)
    if degenerate_after > degenerate_before:
        reasons.append("it introduced %d degenerate triangle(s) (%d -> %d)"
                       % (degenerate_after - degenerate_before,
                          degenerate_before, degenerate_after))

    nonmanifold_before = int(before.get("nonmanifold_edge_count", 0) or 0)
    nonmanifold_after = int(after.get("nonmanifold_edge_count", 0) or 0)
    if nonmanifold_after > nonmanifold_before:
        reasons.append("the non-manifold edge count rose (%d -> %d)"
                       % (nonmanifold_before, nonmanifold_after))

    wanted_faces = int(expected.get("face_count", 0) or 0)
    triangles_before = int(before.get("triangle_count", 0) or 0)
    triangles_after = int(after.get("triangle_count", 0) or 0)
    if wanted_faces and triangles_after != triangles_before - wanted_faces:
        reasons.append("it removed %d triangle(s), not the %d the approved "
                       "candidate held"
                       % (triangles_before - triangles_after, wanted_faces))

    wanted_vertices = int(expected.get("dropped_vertex_count", -1))
    vertices_before = int(before.get("vertex_count", 0) or 0)
    vertices_after = int(after.get("vertex_count", 0) or 0)
    if wanted_vertices >= 0 and vertices_before:
        if vertices_after != vertices_before - wanted_vertices:
            reasons.append("it removed %d vertex/vertices, not the %d the "
                           "approved candidate stranded"
                           % (vertices_before - vertices_after,
                              wanted_vertices))

    return reasons


def defect_resolved_reasons(defect_signature, after_signature):
    """Did the focused defect actually go? (reasons to revert).

    Positions, not indices - ``repair.nonmanifold_signature`` keys a
    non-manifold edge by its midpoint precisely so it survives the
    renumbering a face deletion causes.
    """
    reasons = []
    survived = set(defect_signature) & set(after_signature)
    if survived:
        reasons.append("%d of the focused defect's %d non-manifold edge(s) "
                       "survived the removal"
                       % (len(survived), len(defect_signature)))
    return reasons


def introduced_reasons(before_signature, after_signature):
    """Did a non-manifold edge appear that was not there before? (reasons).

    Stricter than a net count, in the way the real data demanded of
    ``repair.step_acceptable``: fixing one defect while creating another
    somewhere else is not progress even when the total falls, and on a local
    repair it means the edit reached past the branch it was scoped to.
    """
    introduced = set(after_signature) - set(before_signature)
    if not introduced:
        return []
    return ["it introduced %d non-manifold edge(s) that were not there before"
            % len(introduced)]


__all__ = [
    "LOCAL_EXPLORE_FACES",
    "MAX_CANDIDATE_FACES",
    "DOMINANCE_RATIO",
    "LOCAL_DUPLICATE_FACE",
    "LOCAL_DANGLING_FLAP",
    "LOCAL_SMALL_BRANCH",
    "LOCAL_AMBIGUOUS",
    "LOCAL_UNSUPPORTED",
    "REMOVABLE",
    "CLASSIFICATION_LABELS",
    "MANUAL_EDIT_MESSAGE",
    "DUPLICATE_DELEGATE_MESSAGE",
    "classify_local_defect",
    "confirmation_lines",
    "defect_resolved_reasons",
    "describe",
    "extra_removal_reasons",
    "face_position_signature",
    "introduced_reasons",
    "incident_faces",
    "local_branches",
    "locality_reasons",
    "measure_candidate",
    "predict_removal",
    "report_lines",
]
