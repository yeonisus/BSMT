"""BSMT Phase 2 geodesic subpackage.

Milestone 2.0 provides topology diagnostics only. No geodesic computation,
no SurfacePoint and no solver backend is implemented yet.

Module layout, per PROJECT_SPEC.md sect. 11:

    spaces.py    pure numpy - solver space, geometry_hash, metric_key
    topology.py  pure numpy - mesh analysis
    extract.py   requires bpy - evaluated mesh extraction
    envreport.py stdlib - runtime environment detection (2.2)
    backends/    pure numpy - geodesic solver backends (2.2)
    registry.py  pure numpy - backend selection, bounded query, provenance (2.3)
    solve.py     pure numpy - production A-B surface distance pipeline (2.3)

spaces and topology import outside Blender and are unit tested there.
extract imports bpy and is expected to import only inside Blender.

Import discipline
-----------------
Submodules are imported with importlib.import_module(), never with
`from . import x` written next to an `x = None` placeholder. That combination
silently yields None: the import machinery sees that hasattr(package, "x") is
already True and therefore never imports the submodule, after which
`from . import x` simply rebinds the placeholder to itself. That defect made
geodesic.extract None inside Blender 4.5.13 and produced

    AttributeError: 'NoneType' object has no attribute 'extract_solver_mesh'

It is regression tested in tests/test_import.py.

Import failures are never swallowed: the original message and traceback are
kept in the *_IMPORT_ERROR / *_IMPORT_TRACEBACK globals, printed to the system
console at import time, and surfaced through diagnostics_error().
"""

import importlib
import traceback

NUMPY_AVAILABLE = False
NUMPY_IMPORT_ERROR = ""
NUMPY_IMPORT_TRACEBACK = ""

# spaces + topology: pure numpy, importable outside Blender.
ANALYSIS_AVAILABLE = False
ANALYSIS_IMPORT_ERROR = ""
ANALYSIS_IMPORT_TRACEBACK = ""

# extract: requires bpy.
EXTRACT_AVAILABLE = False
EXTRACT_IMPORT_ERROR = ""
EXTRACT_IMPORT_TRACEBACK = ""

# meshcache: requires bpy. Canonical mesh + BVH.
MESHCACHE_AVAILABLE = False
MESHCACHE_IMPORT_ERROR = ""
MESHCACHE_IMPORT_TRACEBACK = ""

# preview: requires bpy. Diagnostics-only component visualisation.
PREVIEW_AVAILABLE = False
PREVIEW_IMPORT_ERROR = ""
PREVIEW_IMPORT_TRACEBACK = ""

# envreport + backends: Milestone 2.2, development diagnostics only.
# NOTHING here may block topology diagnostics, picking or the Phase 1
# straight distance: the exact geodesic backend is optional by construction
# and BSMT must load normally when pygeodesic is absent.
ENVREPORT_AVAILABLE = False
ENVREPORT_IMPORT_ERROR = ""
ENVREPORT_IMPORT_TRACEBACK = ""

BACKENDS_AVAILABLE = False
BACKENDS_IMPORT_ERROR = ""
BACKENDS_IMPORT_TRACEBACK = ""

# registry + solve: Milestone 2.3 production surface distance. Pure numpy.
# They import the backends package, which guards pygeodesic itself, so they
# load whether or not an exact backend is installed.
MEASURE_AVAILABLE = False
MEASURE_IMPORT_ERROR = ""
MEASURE_IMPORT_TRACEBACK = ""

spaces = None
topology = None
surface_point = None
extract = None
meshcache = None
preview = None
envreport = None
backends = None
registry = None
solve = None


def _describe(exc):
    return "%s: %s" % (type(exc).__name__, exc)


try:
    import numpy  # noqa: F401

    NUMPY_AVAILABLE = True
except Exception as exc:  # pragma: no cover - numpy ships with Blender
    NUMPY_IMPORT_ERROR = _describe(exc)
    NUMPY_IMPORT_TRACEBACK = traceback.format_exc()
    print("[BSMT] numpy could not be imported: %s" % NUMPY_IMPORT_ERROR)
    print(NUMPY_IMPORT_TRACEBACK)


def _load_analysis():
    """Import the pure-numpy modules. Returns True on success."""
    global spaces, topology, surface_point
    global ANALYSIS_AVAILABLE, ANALYSIS_IMPORT_ERROR, ANALYSIS_IMPORT_TRACEBACK

    try:
        spaces = importlib.import_module(".spaces", __name__)
        topology = importlib.import_module(".topology", __name__)
        surface_point = importlib.import_module(".surface_point", __name__)
    except Exception as exc:
        spaces = None
        topology = None
        surface_point = None
        ANALYSIS_AVAILABLE = False
        ANALYSIS_IMPORT_ERROR = _describe(exc)
        ANALYSIS_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import analysis modules: %s" % ANALYSIS_IMPORT_ERROR)
        print(ANALYSIS_IMPORT_TRACEBACK)
        return False

    ANALYSIS_AVAILABLE = True
    ANALYSIS_IMPORT_ERROR = ""
    ANALYSIS_IMPORT_TRACEBACK = ""
    return True


def _load_extract():
    """Import the bpy-dependent module. Returns True on success."""
    global extract
    global EXTRACT_AVAILABLE, EXTRACT_IMPORT_ERROR, EXTRACT_IMPORT_TRACEBACK

    try:
        extract = importlib.import_module(".extract", __name__)
    except Exception as exc:
        extract = None
        EXTRACT_AVAILABLE = False
        EXTRACT_IMPORT_ERROR = _describe(exc)
        EXTRACT_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import extraction module: %s" % EXTRACT_IMPORT_ERROR)
        print(EXTRACT_IMPORT_TRACEBACK)
        return False

    EXTRACT_AVAILABLE = True
    EXTRACT_IMPORT_ERROR = ""
    EXTRACT_IMPORT_TRACEBACK = ""
    return True


def _load_meshcache():
    """Import the canonical mesh cache. Returns True on success."""
    global meshcache
    global MESHCACHE_AVAILABLE, MESHCACHE_IMPORT_ERROR, MESHCACHE_IMPORT_TRACEBACK

    try:
        meshcache = importlib.import_module(".meshcache", __name__)
    except Exception as exc:
        meshcache = None
        MESHCACHE_AVAILABLE = False
        MESHCACHE_IMPORT_ERROR = _describe(exc)
        MESHCACHE_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import meshcache module: %s" % MESHCACHE_IMPORT_ERROR)
        print(MESHCACHE_IMPORT_TRACEBACK)
        return False

    MESHCACHE_AVAILABLE = True
    MESHCACHE_IMPORT_ERROR = ""
    MESHCACHE_IMPORT_TRACEBACK = ""
    return True


def _load_preview():
    """Import the diagnostics preview module. Returns True on success."""
    global preview
    global PREVIEW_AVAILABLE, PREVIEW_IMPORT_ERROR, PREVIEW_IMPORT_TRACEBACK

    try:
        preview = importlib.import_module(".preview", __name__)
    except Exception as exc:
        preview = None
        PREVIEW_AVAILABLE = False
        PREVIEW_IMPORT_ERROR = _describe(exc)
        PREVIEW_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import preview module: %s" % PREVIEW_IMPORT_ERROR)
        print(PREVIEW_IMPORT_TRACEBACK)
        return False

    PREVIEW_AVAILABLE = True
    PREVIEW_IMPORT_ERROR = ""
    PREVIEW_IMPORT_TRACEBACK = ""
    return True


def _load_envreport():
    """Import the runtime environment reporter. Returns True on success."""
    global envreport
    global ENVREPORT_AVAILABLE, ENVREPORT_IMPORT_ERROR, ENVREPORT_IMPORT_TRACEBACK

    try:
        envreport = importlib.import_module(".envreport", __name__)
    except Exception as exc:
        envreport = None
        ENVREPORT_AVAILABLE = False
        ENVREPORT_IMPORT_ERROR = _describe(exc)
        ENVREPORT_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import envreport module: %s" % ENVREPORT_IMPORT_ERROR)
        print(ENVREPORT_IMPORT_TRACEBACK)
        return False

    ENVREPORT_AVAILABLE = True
    ENVREPORT_IMPORT_ERROR = ""
    ENVREPORT_IMPORT_TRACEBACK = ""
    return True


def _load_backends():
    """Import the backends subpackage. Returns True on success.

    This is NOT the same question as "is pygeodesic installed". The
    subpackage guards that import itself and loads successfully either way;
    a failure here means BSMT's own wrapper is broken.
    """
    global backends
    global BACKENDS_AVAILABLE, BACKENDS_IMPORT_ERROR, BACKENDS_IMPORT_TRACEBACK

    try:
        backends = importlib.import_module(".backends", __name__)
    except Exception as exc:
        backends = None
        BACKENDS_AVAILABLE = False
        BACKENDS_IMPORT_ERROR = _describe(exc)
        BACKENDS_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import backends package: %s" % BACKENDS_IMPORT_ERROR)
        print(BACKENDS_IMPORT_TRACEBACK)
        return False

    BACKENDS_AVAILABLE = True
    BACKENDS_IMPORT_ERROR = ""
    BACKENDS_IMPORT_TRACEBACK = ""
    return True


def _load_measure():
    """Import registry + solve (Milestone 2.3). Returns True on success."""
    global registry, solve
    global MEASURE_AVAILABLE, MEASURE_IMPORT_ERROR, MEASURE_IMPORT_TRACEBACK

    try:
        registry = importlib.import_module(".registry", __name__)
        solve = importlib.import_module(".solve", __name__)
    except Exception as exc:
        registry = None
        solve = None
        MEASURE_AVAILABLE = False
        MEASURE_IMPORT_ERROR = _describe(exc)
        MEASURE_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import surface distance modules: %s"
              % MEASURE_IMPORT_ERROR)
        print(MEASURE_IMPORT_TRACEBACK)
        return False

    MEASURE_AVAILABLE = True
    MEASURE_IMPORT_ERROR = ""
    MEASURE_IMPORT_TRACEBACK = ""
    return True


if NUMPY_AVAILABLE:
    # extract depends on spaces, so it is only attempted once analysis loads.
    if _load_analysis():
        if _load_extract():
            _load_meshcache()
        _load_preview()
        if _load_backends():
            _load_measure()

# envreport needs neither numpy nor bpy: it must work precisely when they are
# the thing that is broken.
_load_envreport()


def ensure_loaded():
    """Retry any submodule import that is not currently loaded.

    Blender keeps a package object alive across add-on disable/enable cycles
    and across "Reload Scripts", so a package left in a partially initialised
    state would otherwise stay broken until Blender restarts. Retrying here
    repairs that state exactly the way a manual
    importlib.import_module("...geodesic.extract") does - which is how the
    0.3.0 defect was observed to heal itself in a live session.

    A no-op once everything is loaded. Returns diagnostics_error().
    """
    if NUMPY_AVAILABLE:
        if not ANALYSIS_AVAILABLE or spaces is None or topology is None:
            _load_analysis()
        if ANALYSIS_AVAILABLE and (not EXTRACT_AVAILABLE or extract is None):
            _load_extract()
        if EXTRACT_AVAILABLE and (not MESHCACHE_AVAILABLE or meshcache is None):
            _load_meshcache()
        if ANALYSIS_AVAILABLE and (not PREVIEW_AVAILABLE or preview is None):
            _load_preview()
        if ANALYSIS_AVAILABLE and (not BACKENDS_AVAILABLE or backends is None):
            _load_backends()
        elif backends is not None:
            backends.ensure_loaded()
        if BACKENDS_AVAILABLE and (
            not MEASURE_AVAILABLE or registry is None or solve is None
        ):
            _load_measure()
    if not ENVREPORT_AVAILABLE or envreport is None:
        _load_envreport()
    return diagnostics_error()


def diagnostics_error():
    """Why topology diagnostics are unavailable, or '' when they are usable.

    Returned verbatim to the user, so it names the failing stage and carries
    the original exception text.
    """
    if not NUMPY_AVAILABLE:
        return (
            "Topology diagnostics unavailable: numpy could not be imported: %s"
            % NUMPY_IMPORT_ERROR
        )
    if not ANALYSIS_AVAILABLE:
        return (
            "Topology diagnostics unavailable: failed to import analysis "
            "modules: %s" % ANALYSIS_IMPORT_ERROR
        )
    if not EXTRACT_AVAILABLE:
        return (
            "Topology diagnostics unavailable: failed to import extraction "
            "module: %s" % EXTRACT_IMPORT_ERROR
        )
    if not MESHCACHE_AVAILABLE or meshcache is None:
        return (
            "Topology diagnostics unavailable: failed to import canonical mesh "
            "module: %s" % MESHCACHE_IMPORT_ERROR
        )
    if (
        extract is None
        or topology is None
        or spaces is None
        or surface_point is None
    ):
        # Belt and braces: a flag must never disagree with the module object.
        return (
            "Topology diagnostics unavailable: a geodesic submodule is None "
            "despite reporting success (spaces=%r topology=%r "
            "surface_point=%r extract=%r)"
            % (spaces, topology, surface_point, extract)
        )
    return ""


def preview_error():
    """Why the component preview is unavailable, or '' when it is usable.

    The preview is an optional diagnostic: topology diagnostics stay usable
    even when it fails to load.
    """
    blocking = diagnostics_error()
    if blocking:
        return blocking
    if not PREVIEW_AVAILABLE or preview is None:
        return (
            "Component visualisation unavailable: failed to import preview "
            "module: %s" % PREVIEW_IMPORT_ERROR
        )
    return ""


def import_traceback():
    """Original traceback of the first import failure, or ''."""
    if not NUMPY_AVAILABLE:
        return NUMPY_IMPORT_TRACEBACK
    if not ANALYSIS_AVAILABLE:
        return ANALYSIS_IMPORT_TRACEBACK
    if not EXTRACT_AVAILABLE:
        return EXTRACT_IMPORT_TRACEBACK
    if not MESHCACHE_AVAILABLE:
        return MESHCACHE_IMPORT_TRACEBACK
    if not PREVIEW_AVAILABLE:
        return PREVIEW_IMPORT_TRACEBACK
    return ""


def reload_submodules():
    """Reload the geodesic submodules in place, for Blender's Reload Scripts.

    Safe when a submodule failed to import: reloading the package itself has
    already retried the import.
    """
    for module in (spaces, topology, surface_point, extract, meshcache, preview,
                   envreport):
        if module is not None:
            importlib.reload(module)
    if backends is not None:
        importlib.reload(backends)
        backends.reload_submodules()
    for module in (registry, solve):
        if module is not None:
            importlib.reload(module)


def environment_error():
    """Why the environment report is unavailable, or '' when it is usable."""
    if not ENVREPORT_AVAILABLE or envreport is None:
        return (
            "Environment report unavailable: failed to import envreport "
            "module: %s" % (ENVREPORT_IMPORT_ERROR or "not loaded")
        )
    return ""


def backend_status():
    """Exact-geodesic backend availability record, never raising.

    Returns the same dict shape whether or not anything imported, so the
    diagnostics panel has one code path. A missing pygeodesic is reported,
    not hidden, and its original import error is carried verbatim.
    """
    if backends is None:
        return {
            "backend_name": "pygeodesic-MMP",
            "wrapper_version": "",
            "available": False,
            "version": "",
            "module_path": "",
            "import_error": "",
            "import_traceback": "",
            "wrapper_error": (
                BACKENDS_IMPORT_ERROR
                or "geodesic.backends was not loaded (numpy unavailable?)"
            ),
            "wrapper_traceback": BACKENDS_IMPORT_TRACEBACK,
        }
    return backends.status()


def backend_available():
    """True when an exact geodesic backend can be used right now."""
    return bool(backends is not None and backends.availability())


def backend_error():
    """One-line reason the exact backend is unusable, or ''."""
    if backends is None:
        return (
            "Exact geodesic backend unavailable: %s"
            % (BACKENDS_IMPORT_ERROR
               or "geodesic.backends was not loaded (numpy unavailable?)")
        )
    return backends.unavailable_reason()


def backend_import_traceback():
    """Complete original traceback of the backend import failure, or ''."""
    if BACKENDS_IMPORT_TRACEBACK:
        return BACKENDS_IMPORT_TRACEBACK
    if backends is None:
        return ""
    return backends.status().get("import_traceback", "")


def measure_error():
    """Why surface distance measurement is unavailable, or ''.

    Distinguishes a broken BSMT module from a missing pygeodesic: the first is
    a bug, the second is the expected state on a machine without the backend,
    and only the second leaves Phase 1 fully usable.
    """
    if not MEASURE_AVAILABLE or registry is None or solve is None:
        return (
            "Surface distance unavailable: failed to import the measurement "
            "modules: %s" % (MEASURE_IMPORT_ERROR or "not loaded")
        )
    return ""
