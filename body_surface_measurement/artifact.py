"""Component-scoped artifact removal: the policy, in pure numpy.

Milestone 3.28. A human scan routinely carries small detached fragments -
a dropped shard of floor, a duplicated sliver off a shoulder, a scrap of the
scanner's turntable - and one of them is often the thing carrying the
non-manifold edge that blocks exact measurement. Deleting it is the right
repair, and BSMT cannot be the one to decide that.

So this module answers two questions and refuses to answer a third:

  * which connected component holds the defect the researcher is inspecting?
  * is deleting that whole component permitted, and what exactly goes with it?
  * is that geometry anatomically irrelevant?  -  NOT ANSWERED HERE, and not
    anywhere else in BSMT. That judgement is the researcher's, made by looking
    at the thing, which is why the workflow is Show Edges -> Focus -> Preview
    -> confirm, and why nothing on this path runs without a button press.

The hard rule lives in `deletion_block`: the PRIMARY body component is never
offered for deletion. "Primary" is the component holding the most triangles,
which is the same ordering `geodesic.topology.component_labels` already uses
and the same one the Connected Components list already shows - one definition
of "the body", not a second one invented here. A tie is refused rather than
broken, because a coin-flip between two candidate bodies is not a repair.

No bpy: the policy is testable without Blender. `meshrepair.remove_component`
does the editing, and `operators` runs it inside the existing transactional
wrapper that re-diagnoses and rolls back.
"""

import numpy as np

from . import repair

#: Shown, verbatim, when the inspected defect turns out to live on the body.
PRIMARY_BLOCK_MESSAGE = (
    "The focused defect belongs to the primary body component. Automatic "
    "component deletion is not permitted. Use another repair method or "
    "inspect manually."
)

BLOCK_PRIMARY = 'PRIMARY'
BLOCK_ONLY_COMPONENT = 'ONLY_COMPONENT'
BLOCK_AMBIGUOUS = 'AMBIGUOUS_LARGEST'
BLOCK_EMPTY = 'EMPTY'
BLOCK_NO_DEFECT = 'NO_DEFECT'


# ---------------------------------------------------------------------------
# which component holds the defect
# ---------------------------------------------------------------------------

def components_of_vertices(vertex_components, vertex_indices):
    """The 0-based component labels the given vertices belong to.

    A defect's vertices can in principle straddle labels only if the labelling
    is wrong, so returning the SET rather than the first label is deliberate:
    the caller refuses an ambiguous answer instead of silently taking one.
    """
    labels = np.asarray(vertex_components, dtype=np.int64)
    wanted = np.asarray(list(vertex_indices), dtype=np.int64)
    if wanted.size == 0 or labels.size == 0:
        return []
    inside = wanted[(wanted >= 0) & (wanted < labels.size)]
    if inside.size == 0:
        return []
    found = np.unique(labels[inside])
    return [int(value) for value in found if int(value) >= 0]


def defect_regions(vertices, faces, vertex_components, max_regions=64):
    """The non-manifold defects, each tagged with the component it sits in.

    Deliberately NOT ``repair.non_manifold_regions``: that one also resolves
    every incident FACE of every defect, which costs a full python pass over
    the triangle array - about three seconds on a 350,000-triangle body scan,
    paid on every Analyze. Nothing on the artifact path needs the incident
    faces; it needs where the defect is and which component holds it, and
    both come from the defect's own edges, which number in the single digits.

    The GROUPING RULE is not duplicated. ``repair.group_nonmanifold_edges``
    is the single definition of "these edges are one defect", and both this
    and the full region description call it, so a defect has the same id in
    both no matter which produced the number the panel is showing.

    `component` is the 0-based component label, or -1 when the defect's own
    endpoints do not agree on one - which would mean the labelling is wrong,
    and is reported rather than resolved by picking one.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    classified = repair.classify_edges(faces, int(vertices.shape[0]))
    edges = classified["non_manifold"]
    if edges.shape[0] == 0:
        return []

    regions = []
    groups = repair.group_nonmanifold_edges(edges)
    for position, members in enumerate(groups[:max_regions]):
        member_edges = edges[np.asarray(members, dtype=np.int64)]
        vertex_indices = np.unique(member_edges)
        points = vertices[vertex_indices]
        extent = points.max(axis=0) - points.min(axis=0)
        labels = components_of_vertices(vertex_components, vertex_indices)
        regions.append({
            "region_id": position + 1,
            "edges": member_edges,
            "edge_count": int(member_edges.shape[0]),
            "vertex_indices": vertex_indices,
            "vertex_count": int(vertex_indices.size),
            "bbox_mm": [float(value) for value in extent],
            "bbox_diagonal_mm": float(np.linalg.norm(extent)),
            "center_mm": [float(value) for value in points.mean(axis=0)],
            "component": int(labels[0]) if len(labels) == 1 else -1,
            "component_labels": labels,
        })
    return regions


def describe_defect(region):
    return ("D%d  %d non-manifold edge(s) on %d vertex/vertices, %.2f mm "
            "across" % (region["region_id"], region["edge_count"],
                        region["vertex_count"],
                        region["bbox_diagonal_mm"]))


# ---------------------------------------------------------------------------
# what would be removed
# ---------------------------------------------------------------------------

def component_facts(vertices, faces, vertex_components, triangle_components,
                    label, defect_vertex_indices=(),
                    small_fraction=repair.SMALL_COMPONENT_FRACTION):
    """Everything the confirmation dialog has to be able to state.

    `label` is 0-based, as `component_labels` produces it; `component_index`
    in the result is the 1-based number the Connected Components list shows,
    so the researcher can match the two by eye.

    Every count is measured on the canonical triangle array the diagnostics
    were computed from, never on Blender polygon indices - see sect. 7.7.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    vertex_labels = np.asarray(vertex_components, dtype=np.int64)
    triangle_labels = np.asarray(triangle_components, dtype=np.int64)
    label = int(label)

    triangle_indices = np.where(triangle_labels == label)[0]
    vertex_indices = np.where(vertex_labels == label)[0]
    total_triangles = int(faces.shape[0])

    local_faces = faces[triangle_indices] if triangle_indices.size else \
        np.zeros((0, 3), dtype=np.int64)
    if local_faces.size:
        pairs = np.concatenate((local_faces[:, [0, 1]], local_faces[:, [1, 2]],
                                local_faces[:, [2, 0]]), axis=0)
        pairs = np.sort(pairs, axis=1)
        edge_count = int(np.unique(pairs, axis=0).shape[0])
        classified = repair.classify_edges(local_faces, int(vertices.shape[0]))
        boundary_edges = int(classified["boundary"].shape[0])
        nonmanifold_edges = int(classified["non_manifold"].shape[0])
    else:
        edge_count = 0
        boundary_edges = 0
        nonmanifold_edges = 0

    if vertex_indices.size:
        points = vertices[vertex_indices]
        extent = points.max(axis=0) - points.min(axis=0)
        center = points.mean(axis=0)
    else:
        extent = np.zeros(3, dtype=np.float64)
        center = np.zeros(3, dtype=np.float64)

    counts = np.bincount(triangle_labels[triangle_labels >= 0]) \
        if triangle_labels.size else np.zeros(0, dtype=np.int64)
    largest = int(counts.max()) if counts.size else 0
    fraction = (triangle_indices.size / float(total_triangles)
                if total_triangles else 0.0)

    defect_inside = bool(set(int(v) for v in defect_vertex_indices)
                         & set(int(v) for v in vertex_indices))

    return {
        "label": label,
        "component_index": label + 1,
        "vertex_count": int(vertex_indices.size),
        "edge_count": edge_count,
        "triangle_count": int(triangle_indices.size),
        "boundary_edge_count": boundary_edges,
        "nonmanifold_edge_count": nonmanifold_edges,
        "bbox_mm": [float(value) for value in extent],
        "bbox_diagonal_mm": float(np.linalg.norm(extent)),
        "center_mm": [float(value) for value in center],
        "fraction": float(fraction),
        "percent": 100.0 * float(fraction),
        "component_count": int(counts.size),
        "largest_triangle_count": largest,
        # "The primary body" is the component RANKED first, which
        # `component_labels` defines as the one holding the most triangles.
        # Deliberately not "holds as many triangles as the largest": on a tie
        # that would mark two components as the body and this would refuse
        # both under the wrong reason. A tie is caught by `deletion_block`,
        # which refuses it as ambiguous instead of breaking it.
        "is_largest": bool(triangle_indices.size and label == 0),
        "is_small": bool(counts.size > 1 and fraction <= small_fraction
                         and label != 0),
        "holds_focused_defect": defect_inside,
        "vertex_indices": vertex_indices,
        "triangle_indices": triangle_indices,
    }


def describe_component(facts):
    """One line, for a list row or a log entry."""
    return ("Component %d: %s triangles (%.2f%%), %s vertices, %.1f mm "
            "across%s"
            % (facts["component_index"],
               "{:,}".format(facts["triangle_count"]), facts["percent"],
               "{:,}".format(facts["vertex_count"]),
               facts["bbox_diagonal_mm"],
               "  [largest]" if facts["is_largest"]
               else ("  [small]" if facts["is_small"] else "")))


def confirmation_lines(facts, object_name="", landmark_count=0,
                       measurement_count=0):
    """What the confirmation dialog says, in order.

    Deliberately states the three things a researcher can only regret not
    being told BEFORE pressing: the whole component goes, the source scan does
    not, and geometry-dependent results stop being trustworthy.
    """
    bbox = facts["bbox_mm"]
    lines = [
        "Delete the connected component containing the focused defect?",
        "",
        "This operation permanently removes geometry from the measurement",
        "mesh only. The original source scan will not be modified.",
        "",
        "Component %d%s" % (facts["component_index"],
                            " of '%s'" % object_name if object_name else ""),
        "  Vertices:      %s" % "{:,}".format(facts["vertex_count"]),
        "  Edges:         %s" % "{:,}".format(facts["edge_count"]),
        "  Triangles:     %s" % "{:,}".format(facts["triangle_count"]),
        "  Share of mesh: %.2f%%%s" % (facts["percent"],
                                       "  (small)" if facts["is_small"]
                                       else ""),
        "  Bounding box:  %.1f x %.1f x %.1f mm" % (bbox[0], bbox[1], bbox[2]),
        "  Largest / main component: %s" % ("YES" if facts["is_largest"]
                                            else "no"),
        "  Holds the highlighted non-manifold edge: %s"
        % ("yes" if facts["holds_focused_defect"] else "NO"),
        "  Non-manifold edges in it: %d" % facts["nonmanifold_edge_count"],
        "",
    ]
    if not facts["is_small"] and not facts["is_largest"]:
        lines.append("NOTE: this is not a small fragment - it holds %.2f%% of"
                     % facts["percent"])
        lines.append("the mesh. Check that it is not anatomy before deleting.")
        lines.append("")
    lines.append("Existing landmarks, measurements and cached paths may")
    lines.append("become stale.")
    if landmark_count or measurement_count:
        lines.append("%d landmark(s) and %d measurement(s) on this mesh will"
                     % (landmark_count, measurement_count))
        lines.append("be restated after the deletion.")
    return lines


# ---------------------------------------------------------------------------
# may it be deleted at all?
# ---------------------------------------------------------------------------

def deletion_block(facts):
    """(allowed, code, reason). The one hard rule of this milestone.

    Refusing is a real answer. A researcher who has established that the
    defect is on the body itself needs to be told exactly that, not handed a
    destructive button that happens to be a bad idea.
    """
    if facts is None:
        return False, BLOCK_NO_DEFECT, (
            "No focused defect. Press Analyze, then Show Edges, and select a "
            "defect before deleting anything."
        )
    if facts["triangle_count"] <= 0:
        return False, BLOCK_EMPTY, (
            "that component holds no triangles - re-analyze the mesh"
        )
    if facts["component_count"] <= 1:
        return False, BLOCK_ONLY_COMPONENT, (
            "this mesh is a single connected component; deleting it would "
            "leave nothing to measure. " + PRIMARY_BLOCK_MESSAGE
        )
    if facts["is_largest"]:
        return False, BLOCK_PRIMARY, PRIMARY_BLOCK_MESSAGE
    if facts["triangle_count"] >= facts["largest_triangle_count"]:
        return False, BLOCK_AMBIGUOUS, (
            "this component holds as many triangles as the largest one, so "
            "which of them is the body is ambiguous. BSMT will not guess. "
            + PRIMARY_BLOCK_MESSAGE
        )
    return True, "", ""


# ---------------------------------------------------------------------------
# was the completed deletion safe to keep?
# ---------------------------------------------------------------------------

def accept_deletion(before, after, texture_ok, expected):
    """Should a completed component deletion be kept? (accept, reasons).

    The existing authoritative acceptance rule is asked FIRST and in full -
    `repair.accept_repair` - so this cannot drift into a second, softer policy.
    What is added here is only what is specific to removing a whole component:

      * the defect had to actually go, so the non-manifold count must strictly
        fall - that is `repair.accept_repair`'s own strict mode, not a new
        rule;
      * no degenerate triangle may appear that was not there before, because a
        degenerate triangle is a hard blocker for exact measurement;
      * exactly one component may disappear, and the mesh must shrink by
        exactly the triangles that component held - anything else means the
        edit reached past the component it was scoped to;
      * the PRIMARY component must come through with its triangle count
        untouched. That is the check that says "the body is still the body".

    `expected` carries what was measured before the edit: `triangle_count`
    of the doomed component and `primary_triangle_count`.
    """
    _accepted, reasons = repair.accept_repair(
        before, after, texture_ok, require_nonmanifold_decrease=True)
    reasons = list(reasons) + extra_deletion_reasons(before, after, expected)
    return (not reasons), reasons


def extra_deletion_reasons(before, after, expected):
    """The component-deletion-specific half of `accept_deletion`.

    Split out so the operator can add exactly these to the reasons
    `repair.accept_repair` already produced, instead of running the shared
    rule twice and reporting every failure twice.
    """
    reasons = []
    degenerate_before = int(before.get("degenerate_triangle_count", 0) or 0)
    degenerate_after = int(after.get("degenerate_triangle_count", 0) or 0)
    if degenerate_after > degenerate_before:
        reasons.append("it introduced %d degenerate triangle(s) (%d -> %d)"
                       % (degenerate_after - degenerate_before,
                          degenerate_before, degenerate_after))

    components_before = int(before.get("component_count", 0) or 0)
    components_after = int(after.get("component_count", 0) or 0)
    if components_before and components_after != components_before - 1:
        reasons.append("it should have removed exactly one component, but the "
                       "count went %d -> %d"
                       % (components_before, components_after))

    wanted = int(expected.get("triangle_count", 0) or 0)
    triangles_before = int(before.get("triangle_count", 0) or 0)
    triangles_after = int(after.get("triangle_count", 0) or 0)
    if wanted and triangles_after != triangles_before - wanted:
        reasons.append("it removed %d triangle(s), not the %d the component "
                       "held" % (triangles_before - triangles_after, wanted))

    primary_before = int(expected.get("primary_triangle_count", 0) or 0)
    primary_after = _largest_count(after)
    if primary_before and primary_after != primary_before:
        reasons.append("the primary body component changed size (%s -> %s "
                       "triangles); the deletion reached past the component "
                       "it was scoped to"
                       % ("{:,}".format(primary_before),
                          "{:,}".format(primary_after)))

    return reasons


def _largest_count(report):
    counts = report.get("component_triangle_counts") or []
    return int(max(counts)) if len(counts) else 0


def deletion_record(facts, before, after):
    """Provenance for the repair log. Same shape as repair.repair_lines."""
    record = repair.repair_record(
        "Delete defect component",
        before, after,
        "component %d, %s triangles, %s vertices, %.1f mm across"
        % (facts["component_index"], "{:,}".format(facts["triangle_count"]),
           "{:,}".format(facts["vertex_count"]), facts["bbox_diagonal_mm"]),
    )
    return record


__all__ = [
    "PRIMARY_BLOCK_MESSAGE",
    "defect_regions",
    "describe_defect",
    "accept_deletion",
    "extra_deletion_reasons",
    "component_facts",
    "components_of_vertices",
    "confirmation_lines",
    "deletion_block",
    "deletion_record",
    "describe_component",
]
