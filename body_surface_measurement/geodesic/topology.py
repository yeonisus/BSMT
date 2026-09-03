"""Pure-numpy mesh topology analysis (Milestone 2.0).

This module imports **only numpy** - no bpy - so the diagnostic core can be
unit tested outside Blender. All Blender coupling lives in extract.py.

Everything here is read-only analysis. Nothing in this module modifies, welds,
repairs or reorders a mesh; see PROJECT_SPEC.md sect. 7.3 for why welding is a
deliberate, opt-in operation and never automatic.

Input convention: V is (n, 3) float64 in solver space (millimetres, centered),
F is (m, 3) int32 triangle indices. See PROJECT_SPEC.md sect. 6.2.
"""

import numpy as np

# A triangle counts as degenerate when its area falls below
# (DEGENERATE_EDGE_FRACTION * bounding box diagonal) ** 2.
DEGENERATE_EDGE_FRACTION = 1e-9

# Safety caps. Root hooking converges in O(log n) rounds - measured at 7-12 on
# half-million-vertex meshes - so these are far above what any real mesh needs.
# They are backstops, never the thing correctness depends on: the result is
# certified explicitly (see labels_are_consistent).
MAX_LABEL_ITERATIONS = 128
MAX_COMPRESSION_ITERATIONS = 64


class TopologyError(Exception):
    """Raised when a topology result cannot be certified correct."""


def unique_edges(faces, vertex_count):
    """Undirected edges of a triangle soup.

    Returns (edge_a, edge_b, incident_triangle_count) with edge_a < edge_b.
    """
    if faces.size == 0:
        empty_i = np.zeros(0, dtype=np.int64)
        return empty_i, empty_i, np.zeros(0, dtype=np.int64)

    pairs = np.concatenate(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
    ).astype(np.int64)
    low = np.minimum(pairs[:, 0], pairs[:, 1])
    high = np.maximum(pairs[:, 0], pairs[:, 1])

    # Encode each undirected edge as a single int64 so we can use the fast
    # 1-D unique. vertex_count**2 stays far below 2**63 for any real mesh.
    key = low * np.int64(vertex_count) + high
    unique_key, counts = np.unique(key, return_counts=True)
    return unique_key // vertex_count, unique_key % vertex_count, counts


def labels_are_consistent(labels, edge_a, edge_b):
    """Certificate that a labelling is exactly the component partition.

    Labels only ever propagate along edges, so two vertices in different
    components can never share one. If in addition every edge joins two
    equally-labelled vertices, the labelling is exactly the connected
    component partition - necessary and sufficient.

    This check exists because an *unconverged* iterative labelling is not
    merely imprecise: a label class is then a subset of a true component, and
    a subset need not be spatially contiguous. That produces one "component"
    covering, say, a head and a separate forearm. Never return a labelling
    without checking this.
    """
    if edge_a.size == 0:
        return True
    return bool(np.all(labels[edge_a] == labels[edge_b]))


def connected_components(vertex_count, edge_a, edge_b, certify=True):
    """Label connected components (Shiloach-Vishkin hooking plus shortcutting).

    Returns one label per vertex; the label is the smallest vertex index in
    the component. Fully vectorised, so it stays usable on dense scans.

    Roots are hooked to roots, not vertices to neighbours. That distinction is
    the whole ballgame: hooking vertices to neighbours propagates labels one
    edge per round, so it needs O(graph diameter) rounds - thousands on a body
    scan - whereas root hooking needs O(log n). An earlier version of this
    function hooked vertices and silently stopped at an iteration cap, which
    split single components into spatially disconnected pieces.

    Raises TopologyError when the result cannot be certified (certify=True).
    """
    parent = np.arange(vertex_count, dtype=np.int64)
    if edge_a.size == 0:
        return parent

    for _ in range(MAX_LABEL_ITERATIONS):
        previous = parent

        # Hook the root of each endpoint toward the other endpoint's root.
        root_a = parent[edge_a]
        root_b = parent[edge_b]
        parent = parent.copy()
        np.minimum.at(parent, root_a, root_b)
        np.minimum.at(parent, root_b, root_a)

        # Shortcut every pointer to its current root. parent[i] <= i is
        # preserved by the minimum-only updates, so this always terminates.
        for _ in range(MAX_COMPRESSION_ITERATIONS):
            jumped = parent[parent]
            if np.array_equal(jumped, parent):
                break
            parent = jumped

        if np.array_equal(parent, previous):
            break

    if certify and not labels_are_consistent(parent, edge_a, edge_b):
        violating = int(np.count_nonzero(parent[edge_a] != parent[edge_b]))
        raise TopologyError(
            "connected component labelling did not converge after %d "
            "iterations: %d of %d edges still join differently labelled "
            "vertices. Component results would be wrong, so they are refused "
            "rather than displayed."
            % (MAX_LABEL_ITERATIONS, violating, int(edge_a.size))
        )

    return parent


def component_labels(vertices, faces):
    """Per-triangle and per-vertex component labels, ordered by size.

    Additive helper for the diagnostics component preview; analyse() is
    unchanged. Labels are 0..k-1 with 0 the component holding the most
    triangles, matching the "triangles per component" order in the report.

    Returns (triangle_labels, vertex_labels, triangle_counts, vertex_counts).
    A vertex not used by any triangle gets label -1.

    Raises TopologyError if the underlying labelling cannot be certified.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    vertex_count = int(vertices.shape[0])

    if faces.size == 0:
        return (
            np.zeros(0, dtype=np.int64),
            np.full(vertex_count, -1, dtype=np.int64),
            [],
            [],
        )

    edge_a, edge_b, _ = unique_edges(faces, vertex_count)
    raw = connected_components(vertex_count, edge_a, edge_b)

    raw_triangle = raw[faces[:, 0]]
    present, counts = np.unique(raw_triangle, return_counts=True)

    # Order components by descending triangle count, stable for determinism.
    order = np.argsort(-counts, kind="stable")
    ordered_raw = present[order]

    # Map raw label -> ordered index, without allocating a max(label) array.
    sorted_raw = np.sort(ordered_raw)
    new_index = np.empty(sorted_raw.size, dtype=np.int64)
    new_index[np.searchsorted(sorted_raw, ordered_raw)] = np.arange(ordered_raw.size)

    triangle_labels = new_index[np.searchsorted(sorted_raw, raw_triangle)]

    used = np.zeros(vertex_count, dtype=bool)
    used[faces.reshape(-1)] = True

    # Every used vertex must carry a label that appears on some triangle,
    # otherwise searchsorted below would silently map it to a wrong component.
    used_raw = raw[used]
    if used_raw.size:
        position = np.searchsorted(sorted_raw, used_raw)
        if int(position.max()) >= sorted_raw.size or not np.all(
            sorted_raw[np.minimum(position, sorted_raw.size - 1)] == used_raw
        ):
            raise TopologyError(
                "vertex labels contain values absent from the triangle "
                "labelling; the component partition is inconsistent"
            )

    vertex_labels = np.full(vertex_count, -1, dtype=np.int64)
    if used_raw.size:
        vertex_labels[used] = new_index[np.searchsorted(sorted_raw, used_raw)]

    triangle_counts = [int(v) for v in counts[order]]
    vertex_counts = [
        int(np.count_nonzero(vertex_labels == index))
        for index in range(ordered_raw.size)
    ]
    return triangle_labels, vertex_labels, triangle_counts, vertex_counts


def verify_component_labels(vertices, faces, triangle_labels, triangle_counts):
    """Independently re-check a component labelling. Returns (rows, problems).

    For every component this extracts its triangles from the SAME canonical
    triangle array, renumbers them locally, and recomputes connectivity from
    scratch on that submesh. A correctly labelled component must contain
    exactly ONE connected component. It also checks that the label classes
    cover every canonical triangle exactly once and are mutually disjoint.

    Nothing here trusts the labelling that produced the input; that is the
    point. `problems` is empty only when every check passes.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    triangle_labels = np.asarray(triangle_labels, dtype=np.int64)
    triangle_counts = list(triangle_counts)

    rows = []
    problems = []
    total_triangles = int(faces.shape[0])
    component_count = len(triangle_counts)

    if int(triangle_labels.shape[0]) != total_triangles:
        problems.append(
            "label array holds %d entries but the canonical triangle array "
            "holds %d" % (int(triangle_labels.shape[0]), total_triangles)
        )
        return rows, problems

    if total_triangles == 0:
        return rows, problems

    if int(faces.min()) < 0 or int(faces.max()) >= int(vertices.shape[0]):
        problems.append(
            "canonical triangles index outside the canonical vertex array "
            "(range %d..%d, %d vertices)"
            % (int(faces.min()), int(faces.max()), int(vertices.shape[0]))
        )
        return rows, problems

    if int(triangle_labels.min()) < 0 or int(triangle_labels.max()) >= component_count:
        problems.append(
            "labels range %d..%d, outside 0..%d"
            % (int(triangle_labels.min()), int(triangle_labels.max()),
               component_count - 1)
        )
        return rows, problems

    coverage = np.zeros(total_triangles, dtype=np.int64)

    for index in range(component_count):
        mask = triangle_labels == index
        coverage += mask.astype(np.int64)
        subset = faces[mask]
        actual = int(subset.shape[0])
        expected = int(triangle_counts[index])

        used = np.unique(subset)
        vertex_count = int(used.size)

        independent = 0
        note = ""
        if actual:
            remap = np.full(int(vertices.shape[0]), -1, dtype=np.int64)
            remap[used] = np.arange(vertex_count, dtype=np.int64)
            local = remap[subset]
            edge_a, edge_b, _ = unique_edges(local, vertex_count)
            try:
                labels = connected_components(vertex_count, edge_a, edge_b)
                independent = int(np.unique(labels).size)
            except TopologyError as exc:
                note = str(exc)

        rows.append({
            "component": index + 1,
            "expected_triangles": expected,
            "actual_triangles": actual,
            "independent_components": independent,
            "vertex_count": vertex_count,
            "note": note,
        })

        if actual != expected:
            problems.append(
                "component %d holds %d triangles but %d were reported"
                % (index + 1, actual, expected)
            )
        if note:
            problems.append("component %d: %s" % (index + 1, note))
        elif actual and independent != 1:
            problems.append(
                "component %d is not connected: independent analysis finds %d "
                "separate pieces in it" % (index + 1, independent)
            )

    misses = int(np.count_nonzero(coverage == 0))
    doubles = int(np.count_nonzero(coverage > 1))
    if misses or doubles:
        problems.append(
            "component triangle sets do not partition the canonical array "
            "exactly once: %d triangles unassigned, %d assigned more than once"
            % (misses, doubles)
        )

    assigned = int(sum(row["actual_triangles"] for row in rows))
    if assigned != total_triangles:
        problems.append(
            "component triangles sum to %d but the canonical array holds %d"
            % (assigned, total_triangles)
        )

    return rows, problems


def format_verification(rows, problems, header_lines=()):
    """Render the verification table as plain text lines."""
    lines = list(header_lines)
    lines.append("")
    lines.append(
        "  ID  expected   actual  independent   vertices"
    )
    for row in rows:
        lines.append(
            "  %-3d %8d %8d %12d %10d"
            % (
                row["component"],
                row["expected_triangles"],
                row["actual_triangles"],
                row["independent_components"],
                row["vertex_count"],
            )
        )
    lines.append("")
    if problems:
        lines.append("INTEGRITY FAILURES")
        for problem in problems:
            lines.append("  ! " + problem)
    else:
        lines.append("Integrity: all checks passed")
        lines.append("  every component contains exactly 1 connected piece")
        lines.append("  component sets are disjoint and cover every triangle once")
    return lines


def exact_duplicate_positions(vertices):
    """Vertices sharing a bit-identical position. Reported, never merged."""
    if vertices.size == 0:
        return 0, 0
    unique_rows, counts = np.unique(vertices, axis=0, return_counts=True)
    surplus = int(vertices.shape[0] - unique_rows.shape[0])
    groups = int(np.count_nonzero(counts > 1))
    return surplus, groups


def exact_duplicate_groups(vertices):
    """Index groups of bit-identically positioned vertices.

    The same rule `exact_duplicate_positions` counts, returning WHICH
    vertices rather than how many - which is what a local repair needs in
    order to be local. Reporting and repair therefore cannot disagree about
    what "exactly coincident" means.

    Returns a list of index lists, each with two or more entries, sorted.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.size == 0:
        return []
    _unique, inverse, counts = np.unique(
        vertices, axis=0, return_inverse=True, return_counts=True
    )
    inverse = np.asarray(inverse).reshape(-1)
    groups = []
    for slot in np.flatnonzero(counts > 1):
        members = np.flatnonzero(inverse == slot)
        groups.append([int(index) for index in members])
    return groups


def degenerate_area_threshold(vertices):
    """The area at or below which a triangle counts as degenerate.

    Scale free: a fraction of the bounding-box diagonal, squared. Defined
    here once so the count in `analyse`, the indices in
    `degenerate_triangles` and any repair that acts on them are all judging
    the same thing.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.size == 0:
        return 0.0
    dimensions = vertices.max(axis=0) - vertices.min(axis=0)
    diagonal = float(np.linalg.norm(dimensions))
    return float((DEGENERATE_EDGE_FRACTION * diagonal) ** 2)


def degenerate_triangles(vertices, faces, threshold=None):
    """(indices, areas, threshold) for every degenerate triangle.

    `indices` is an int64 array of triangle indices into `faces`, in order.
    The rule is `degenerate_area_threshold`, so this is exactly the set that
    `analyse` counts - never a second opinion about which triangles are bad.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    if faces.size == 0:
        return (np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64),
                0.0)
    if threshold is None:
        threshold = degenerate_area_threshold(vertices)
    areas = triangle_areas(vertices, faces)
    indices = np.flatnonzero(areas <= threshold).astype(np.int64)
    return indices, areas, float(threshold)


def near_coincident_estimate(vertices, tolerance):
    """Lower-bound count of vertices within `tolerance` of another vertex.

    Grid based: vertices are bucketed on a lattice of cell size `tolerance`.
    A pair straddling a cell boundary can be missed, so the result is an
    honest LOWER BOUND, not an exact count. Two half-offset lattices are
    tested and the larger count returned to reduce that miss rate.
    """
    if tolerance <= 0.0 or vertices.size == 0:
        return 0, 0

    best_surplus = 0
    best_groups = 0
    for shift in (0.0, 0.5):
        cells = np.floor(vertices / tolerance + shift)
        unique_cells, counts = np.unique(cells, axis=0, return_counts=True)
        surplus = int(vertices.shape[0] - unique_cells.shape[0])
        if surplus > best_surplus:
            best_surplus = surplus
            best_groups = int(np.count_nonzero(counts > 1))
    return best_surplus, best_groups


def triangle_areas(vertices, faces):
    if faces.size == 0:
        return np.zeros(0, dtype=np.float64)
    p0 = vertices[faces[:, 0]]
    p1 = vertices[faces[:, 1]]
    p2 = vertices[faces[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(p1 - p0, p2 - p0), axis=1)


def analyse(vertices, faces, near_tolerance=0.0):
    """Full topology report for a triangle mesh in solver space (mm).

    Returns a plain dict so the caller can format, store or serialise it.
    """
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces, dtype=np.int64)
    vertex_count = int(vertices.shape[0])
    triangle_count = int(faces.shape[0])

    report = {
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
    }

    if vertex_count == 0 or triangle_count == 0:
        report.update(
            loose_vertex_count=vertex_count,
            component_count=0,
            component_triangle_counts=[],
            edge_count=0,
            boundary_edge_count=0,
            nonmanifold_edge_count=0,
            degenerate_triangle_count=0,
            zero_area_triangle_count=0,
            degenerate_area_threshold=0.0,
            min_triangle_area=0.0,
            duplicate_vertex_count=0,
            duplicate_group_count=0,
            near_coincident_count=0,
            near_coincident_group_count=0,
            near_tolerance=near_tolerance,
            bbox_min=[0.0, 0.0, 0.0],
            bbox_max=[0.0, 0.0, 0.0],
            bbox_dimensions=[0.0, 0.0, 0.0],
            bbox_diagonal=0.0,
            edge_length_min=0.0,
            edge_length_median=0.0,
            edge_length_mean=0.0,
            edge_length_max=0.0,
        )
        return report

    # --- edges -----------------------------------------------------------
    edge_a, edge_b, incidence = unique_edges(faces, vertex_count)
    report["edge_count"] = int(edge_a.size)
    report["boundary_edge_count"] = int(np.count_nonzero(incidence == 1))
    report["nonmanifold_edge_count"] = int(np.count_nonzero(incidence >= 3))

    edge_lengths = np.linalg.norm(vertices[edge_a] - vertices[edge_b], axis=1)
    report["edge_length_min"] = float(edge_lengths.min())
    report["edge_length_median"] = float(np.median(edge_lengths))
    report["edge_length_mean"] = float(edge_lengths.mean())
    report["edge_length_max"] = float(edge_lengths.max())

    # --- components ------------------------------------------------------
    labels = connected_components(vertex_count, edge_a, edge_b)

    used = np.zeros(vertex_count, dtype=bool)
    used[faces.reshape(-1)] = True
    report["loose_vertex_count"] = int(np.count_nonzero(~used))

    # Components are counted over vertices that actually carry surface, so
    # loose vertices never inflate the count.
    triangle_labels = labels[faces[:, 0]]
    _, component_sizes = np.unique(triangle_labels, return_counts=True)
    component_sizes = np.sort(component_sizes)[::-1]
    report["component_count"] = int(component_sizes.size)
    report["component_triangle_counts"] = [int(v) for v in component_sizes]

    # --- geometry quality ------------------------------------------------
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    dimensions = bbox_max - bbox_min
    diagonal = float(np.linalg.norm(dimensions))
    report["bbox_min"] = [float(v) for v in bbox_min]
    report["bbox_max"] = [float(v) for v in bbox_max]
    report["bbox_dimensions"] = [float(v) for v in dimensions]
    report["bbox_diagonal"] = diagonal

    # One rule for the count here and for the indices a repair acts on.
    degenerate_indices, areas, area_threshold = degenerate_triangles(
        vertices, faces, threshold=(DEGENERATE_EDGE_FRACTION * diagonal) ** 2
    )
    report["degenerate_area_threshold"] = float(area_threshold)
    report["degenerate_triangle_count"] = int(degenerate_indices.size)
    report["zero_area_triangle_count"] = int(np.count_nonzero(areas == 0.0))
    report["min_triangle_area"] = float(areas.min())

    # --- duplicates (reported only) --------------------------------------
    duplicates, duplicate_groups = exact_duplicate_positions(vertices)
    report["duplicate_vertex_count"] = duplicates
    report["duplicate_group_count"] = duplicate_groups

    near, near_groups = near_coincident_estimate(vertices, near_tolerance)
    report["near_coincident_count"] = near
    report["near_coincident_group_count"] = near_groups
    report["near_tolerance"] = float(near_tolerance)

    return report


def warnings_for(report):
    """Operator-facing warnings. Advisory only - BSMT never acts on these."""
    messages = []

    if report.get("component_count", 0) > 1:
        messages.append(
            "%d connected components. Diagnostic only - this does NOT "
            "invalidate measurement. A point pair on the same component is "
            "measured normally; a pair on different components reports "
            "DISCONNECTED. Components are never welded."
            % report["component_count"]
        )
    if report.get("boundary_edge_count", 0) > 0:
        messages.append(
            "%d boundary edges: cropping or holes may force a surface path to "
            "detour. Check paths that run near a boundary."
            % report["boundary_edge_count"]
        )
    if report.get("nonmanifold_edge_count", 0) > 0:
        messages.append(
            "%d non-manifold edges: geodesic results near them are not well "
            "defined." % report["nonmanifold_edge_count"]
        )
    if report.get("degenerate_triangle_count", 0) > 0:
        messages.append(
            "%d degenerate (near-zero-area) triangles."
            % report["degenerate_triangle_count"]
        )
    if report.get("duplicate_vertex_count", 0) > 0:
        messages.append(
            "%d exactly coincident vertices in %d groups. Reported only - BSMT "
            "does not weld. Welding could fuse anatomically distinct surfaces "
            "(arm/torso, finger/finger, garment/skin)."
            % (report["duplicate_vertex_count"], report["duplicate_group_count"])
        )
    if report.get("near_coincident_count", 0) > 0:
        messages.append(
            "at least %d vertices within %.4f mm of another vertex. Reported "
            "only - BSMT does not weld."
            % (report["near_coincident_count"], report["near_tolerance"])
        )
    if report.get("loose_vertex_count", 0) > 0:
        messages.append(
            "%d loose vertices carry no triangle and are excluded from the "
            "component count." % report["loose_vertex_count"]
        )
    return messages


def format_report(report, header_lines=()):
    """Render the report as a list of plain text lines."""
    lines = list(header_lines)
    sizes = report.get("component_triangle_counts", [])
    shown = ", ".join(str(v) for v in sizes[:6])
    if len(sizes) > 6:
        shown += ", ... (%d more)" % (len(sizes) - 6)

    lines += [
        "",
        "Geometry",
        "  Vertices:                  %d" % report["vertex_count"],
        "  Triangles:                 %d" % report["triangle_count"],
        "  Loose vertices:            %d" % report.get("loose_vertex_count", 0),
        "  Bounding box (mm):         %.3f x %.3f x %.3f"
        % tuple(report.get("bbox_dimensions", (0.0, 0.0, 0.0))),
        "  Edge length (mm):          min %.4f  median %.4f  mean %.4f  max %.4f"
        % (
            report.get("edge_length_min", 0.0),
            report.get("edge_length_median", 0.0),
            report.get("edge_length_mean", 0.0),
            report.get("edge_length_max", 0.0),
        ),
        "",
        "Topology",
        "  Connected components:      %d" % report.get("component_count", 0),
        "    triangles per component: %s" % (shown or "-"),
        "  Edges:                     %d" % report.get("edge_count", 0),
        "  Boundary edges:            %d" % report.get("boundary_edge_count", 0),
        "  Non-manifold edges:        %d" % report.get("nonmanifold_edge_count", 0),
        "  Degenerate triangles:      %d  (area <= %.3e mm^2)"
        % (
            report.get("degenerate_triangle_count", 0),
            report.get("degenerate_area_threshold", 0.0),
        ),
        "",
        "Duplicates (reported only - BSMT never welds automatically)",
        "  Exact coincident vertices: %d  in %d groups"
        % (
            report.get("duplicate_vertex_count", 0),
            report.get("duplicate_group_count", 0),
        ),
        "  Near-coincident <= %.4f mm: >= %d  [grid estimate, lower bound]"
        % (report.get("near_tolerance", 0.0), report.get("near_coincident_count", 0)),
    ]

    messages = warnings_for(report)
    lines.append("")
    if messages:
        lines.append("Warnings")
        for message in messages:
            lines.append("  ! " + message)
    else:
        lines.append("Warnings: none")

    return lines
