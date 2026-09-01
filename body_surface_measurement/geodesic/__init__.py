"""BSMT Phase 2 geodesic subpackage.

Milestone 2.0 provides topology diagnostics only. No geodesic computation,
no SurfacePoint and no solver backend is implemented yet.

Module layout, per PROJECT_SPEC.md sect. 11:

    spaces.py    pure numpy - solver space, geometry_hash, metric_key
    topology.py  pure numpy - mesh analysis
    extract.py   requires bpy - evaluated mesh extraction

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

spaces = None
topology = None
surface_point = None
extract = None
meshcache = None
preview = None


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


if NUMPY_AVAILABLE:
    # extract depends on spaces, so it is only attempted once analysis loads.
    if _load_analysis():
        if _load_extract():
            _load_meshcache()
        _load_preview()


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
    for module in (spaces, topology, surface_point, extract, meshcache, preview):
        if module is not None:
            importlib.reload(module)
