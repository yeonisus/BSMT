"""Backend selection, the bounded query strategy, and provenance (Milestone 2.3).

Pure numpy - no bpy - so the query strategy is unit-testable outside Blender.

Two responsibilities, deliberately kept together because they are the same
decision seen from two sides: *which* exact backend answered, and *how* it was
asked. Both travel with every measurement as provenance (PROJECT_SPEC.md
sect. 5.4).

The expanding-bound strategy
----------------------------
An unbounded pygeodesic query sweeps the whole mesh regardless of how close
the target is (sect. 5.1b; the mechanism is documented at length above
``exact_mmp.bounded_distances``). Production measurement therefore always
passes a finite ``max_distance``.

The bound must be an over-estimate of the geodesic distance, and the only
quantity we have for free is the straight-line distance, which is a *lower*
bound (a geodesic can never be shorter than the chord). So the strategy is:
multiply the straight distance by an increasing sequence of factors, stopping
at the first that actually reaches the target.

    1.25x  covers a nearly-flat landmark pair
    2.0x   covers ordinary body curvature
    4.0x   covers a path wrapping a limb
    8.0x   covers a pathological detour around a hole or crop boundary

A target not reached comes back as ``inf`` - detectable, never a wrong number -
and the next factor is tried. If all four fail, one unbounded query is made as
a last resort, which is still the exact MMP answer, just slow. Each failed
attempt is itself cheap, because a bound that is too small is exactly the case
that terminates early.

Nothing here ever substitutes an approximate method. Dijkstra, the heat method
and interpolation are not fallbacks and are not reachable from this module
(sect. 5.2, sect. 5.3).
"""

import time

import numpy as np

from .backends import exact_mmp

#: Bumped when the numerical meaning of a BSMT surface measurement changes.
ALGORITHM_VERSION = "bsmt-geodesic/1"

#: Multipliers applied to the straight-line distance, in order.
BOUND_FACTORS = (1.25, 2.0, 4.0, 8.0)

#: Floor for the bound, in solver millimetres. Without it a landmark pair a
#: fraction of a millimetre apart would ask for a propagation radius so small
#: that the first attempt is guaranteed to be wasted.
MIN_BOUND_MM = 1.0


class BackendUnavailable(Exception):
    """No exact geodesic backend is usable."""


class QueryFailed(Exception):
    """The backend was available but produced no exact answer."""


def available():
    """True when an exact backend can answer a production query right now."""
    return bool(exact_mmp.availability())


def unavailable_reason():
    """One-line reason no exact backend is usable, or ''."""
    if available():
        return ""
    return (
        "exact geodesic backend not installed or not importable: %s"
        % (exact_mmp.IMPORT_ERROR or "unknown reason")
    )


def backend_name():
    return exact_mmp.BACKEND_NAME


def backend_version():
    return exact_mmp.backend_version()


class QueryReport(object):
    """How a bounded query was answered. Recorded with every measurement."""

    __slots__ = (
        "distance_mm",
        "straight_mm",
        "bound_factor",       # factor that succeeded, or None for the fallback
        "bound_mm",           # the max_distance actually passed
        "attempts",           # how many backend calls were made
        "attempt_log",        # [(factor_or_None, bound_mm, seconds, reached)]
        "unbounded_fallback",
        "seconds",            # total, across every attempt
        "backend_name",
        "backend_version",
        "algorithm_version",
    )

    def summary(self):
        if self.unbounded_fallback:
            bound = "unbounded fallback"
        else:
            bound = "%.2fx" % self.bound_factor
        return "Exact MMP | bound %s | attempts %d | %.3f s" % (
            bound, self.attempts, self.seconds
        )


def bounded_distance(vertices, triangles, source_index, target_index,
                     straight_mm, bound_factors=BOUND_FACTORS,
                     allow_unbounded_fallback=True, solver=None):
    """Exact geodesic distance via the expanding-bound strategy.

    `straight_mm` is the straight-line distance between the two endpoints in
    the same units as `vertices` (solver millimetres), used only to size the
    bound. It never influences the returned value.

    Returns (distance_mm, QueryReport). Raises BackendUnavailable or
    QueryFailed. It never returns an approximate number.
    """
    started_total = time.perf_counter()
    if solver is None:
        # The availability check guards solver CONSTRUCTION. A caller that
        # supplies its own solver has already established availability, and
        # requiring the check again would make the bound strategy itself
        # untestable on a machine without pygeodesic - which is precisely the
        # machine where its logic most needs to be known-good.
        if not available():
            raise BackendUnavailable(unavailable_reason())
        solver = exact_mmp.ExactSolver(vertices, triangles)

    report = QueryReport()
    report.straight_mm = float(straight_mm)
    report.attempt_log = []
    report.unbounded_fallback = False
    report.backend_name = backend_name()
    report.backend_version = backend_version()
    report.algorithm_version = ALGORITHM_VERSION

    base = max(float(straight_mm), MIN_BOUND_MM)

    distance = None
    used_factor = None
    used_bound = None
    for factor in bound_factors:
        bound = base * float(factor)
        started = time.perf_counter()
        value = solver.bounded_distance(source_index, target_index, bound)
        elapsed = time.perf_counter() - started
        report.attempt_log.append((float(factor), bound, elapsed, value is not None))
        if value is not None:
            distance = value
            used_factor = float(factor)
            used_bound = bound
            break

    if distance is None and allow_unbounded_fallback:
        # Last resort. Still exact MMP - only slow, because it sweeps the
        # whole mesh. Reached when the geodesic detours further than the
        # largest factor allows, e.g. around a large hole.
        started = time.perf_counter()
        value = solver.unbounded_distance(source_index, target_index)
        elapsed = time.perf_counter() - started
        report.attempt_log.append((None, float("inf"), elapsed, value is not None))
        if value is not None:
            distance = value
            report.unbounded_fallback = True
            used_bound = float("inf")

    report.attempts = len(report.attempt_log)
    report.seconds = time.perf_counter() - started_total

    if distance is None:
        raise QueryFailed(
            "the exact backend did not reach the target after %d attempt(s) "
            "(largest bound %.3f mm). The endpoints are most likely on "
            "surfaces with no path between them."
            % (report.attempts, base * float(bound_factors[-1]))
        )

    report.distance_mm = float(distance)
    report.bound_factor = used_factor
    report.bound_mm = used_bound
    return report.distance_mm, report


def provenance(report, geometry_hash, metric_key, component_id, unit,
               source_object, preprocessing=None):
    """The sect. 5.4 provenance record for one successful measurement."""
    return {
        "backend_name": report.backend_name,
        "backend_version": report.backend_version,
        "bsmt_algorithm_version": report.algorithm_version,
        "canonical_mesh_hash": geometry_hash,
        "metric_key": metric_key,
        "component_id": int(component_id),
        "coordinate_unit": unit,
        "source_object": source_object,
        "bound_factor": report.bound_factor,
        "bound_mm": report.bound_mm,
        "unbounded_fallback": report.unbounded_fallback,
        "attempts": report.attempts,
        "elapsed_seconds": report.seconds,
        "preprocessing": preprocessing or {
            "triangulation": "canonical loop triangles",
            "centering": "bbox centre, solver space",
            "weld": "off",
            "endpoint_insertion": "scratch mesh, canonical mesh unmodified",
        },
    }
