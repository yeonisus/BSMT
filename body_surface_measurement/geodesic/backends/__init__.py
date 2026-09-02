"""Geodesic solver backends.

Milestone 2.2 ships exactly one: ``exact_mmp`` (pygeodesic / MMP), and it is
wired only to the development diagnostics panel, not to production
measurement. Milestone 2.3 adds ``registry.py`` and the production path;
Milestone 2.5 adds the diagnostics-only ``dijkstra.py``.

Import discipline is the same as the parent package's (see
``geodesic/__init__.py``): submodules are imported with
``importlib.import_module`` and failures are recorded, printed and re-raised
through the status API rather than swallowed. **Importing this package must
never fail because pygeodesic is missing** - ``exact_mmp`` itself guards that
import, so this package imports cleanly on a machine with no backend at all.
"""

import importlib
import traceback

EXACT_MMP_AVAILABLE = False
EXACT_MMP_IMPORT_ERROR = ""
EXACT_MMP_IMPORT_TRACEBACK = ""

exact_mmp = None
selftest = None

SELFTEST_AVAILABLE = False
SELFTEST_IMPORT_ERROR = ""
SELFTEST_IMPORT_TRACEBACK = ""


def _describe(exc):
    return "%s: %s" % (type(exc).__name__, exc)


def _load_exact_mmp():
    global exact_mmp, EXACT_MMP_AVAILABLE
    global EXACT_MMP_IMPORT_ERROR, EXACT_MMP_IMPORT_TRACEBACK
    try:
        exact_mmp = importlib.import_module(".exact_mmp", __name__)
    except Exception as exc:  # noqa: BLE001
        exact_mmp = None
        EXACT_MMP_AVAILABLE = False
        EXACT_MMP_IMPORT_ERROR = _describe(exc)
        EXACT_MMP_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import exact_mmp wrapper: %s" % EXACT_MMP_IMPORT_ERROR)
        print(EXACT_MMP_IMPORT_TRACEBACK)
        return False
    EXACT_MMP_AVAILABLE = True
    EXACT_MMP_IMPORT_ERROR = ""
    EXACT_MMP_IMPORT_TRACEBACK = ""
    return True


def _load_selftest():
    global selftest, SELFTEST_AVAILABLE
    global SELFTEST_IMPORT_ERROR, SELFTEST_IMPORT_TRACEBACK
    try:
        selftest = importlib.import_module(".selftest", __name__)
    except Exception as exc:  # noqa: BLE001
        selftest = None
        SELFTEST_AVAILABLE = False
        SELFTEST_IMPORT_ERROR = _describe(exc)
        SELFTEST_IMPORT_TRACEBACK = traceback.format_exc()
        print("[BSMT] failed to import backend selftest: %s" % SELFTEST_IMPORT_ERROR)
        print(SELFTEST_IMPORT_TRACEBACK)
        return False
    SELFTEST_AVAILABLE = True
    SELFTEST_IMPORT_ERROR = ""
    SELFTEST_IMPORT_TRACEBACK = ""
    return True


_load_exact_mmp()
_load_selftest()


def ensure_loaded():
    """Retry any submodule import that is not currently loaded."""
    if not EXACT_MMP_AVAILABLE or exact_mmp is None:
        _load_exact_mmp()
    if not SELFTEST_AVAILABLE or selftest is None:
        _load_selftest()


def status():
    """Backend availability record for the diagnostics panel.

    Two distinct failures are kept apart on purpose:

    ``wrapper_error``  BSMT's own wrapper module did not import - a BSMT bug.
    ``import_error``   pygeodesic did not import - missing package, wrong
                       wheel architecture, or a numpy ABI problem. The
                       original message is preserved verbatim.
    """
    if exact_mmp is None:
        return {
            "backend_name": "pygeodesic-MMP",
            "wrapper_version": "",
            "available": False,
            "version": "",
            "module_path": "",
            "import_error": "",
            "import_traceback": "",
            "wrapper_error": EXACT_MMP_IMPORT_ERROR or "wrapper module not loaded",
            "wrapper_traceback": EXACT_MMP_IMPORT_TRACEBACK,
        }
    record = dict(exact_mmp.status())
    record["wrapper_error"] = ""
    record["wrapper_traceback"] = ""
    return record


def availability():
    """True when an exact geodesic backend is usable right now."""
    return bool(exact_mmp is not None and exact_mmp.availability())


def backend_version():
    return exact_mmp.backend_version() if exact_mmp is not None else ""


def unavailable_reason():
    """One-line reason the exact backend cannot be used, or ''."""
    record = status()
    if record["available"]:
        return ""
    if record["wrapper_error"]:
        return "BSMT backend wrapper failed to import: %s" % record["wrapper_error"]
    if record["import_error"]:
        return "pygeodesic unavailable: %s" % record["import_error"]
    return "pygeodesic unavailable: reason not reported"


def selftest_error():
    """Why the synthetic backend test suite cannot run, or ''."""
    if selftest is None:
        return (
            "Backend self-test unavailable: failed to import selftest module: %s"
            % (SELFTEST_IMPORT_ERROR or "not loaded")
        )
    return ""


def reload_submodules():
    for module in (exact_mmp, selftest):
        if module is not None:
            importlib.reload(module)
