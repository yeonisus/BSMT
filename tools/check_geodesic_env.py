"""BSMT Milestone 2.2 - pygeodesic environment proof, standalone runner.

Run it with the interpreter you actually care about. For BSMT that is
Blender's own, which is the only thing a compiled wheel cares about::

    /Applications/Blender.app/Contents/MacOS/Blender --background \\
        --python /Users/yeoni/BSMT/tools/check_geodesic_env.py

It also runs under a plain python3, to check a candidate environment::

    python3 tools/check_geodesic_env.py

What changed in this revision, and why
--------------------------------------
1. **The install advice was wrong.** The previous version suggested
   ``pip install --user``. Blender initialises its Python with
   ``no_user_site = 1`` (verified on Blender 4.5.13, 2026-09-02), so a
   ``--user`` install lands in a directory Blender never searches: it imports
   in Terminal and fails inside Blender. The target is now derived from the
   paths the running interpreter actually imports from.

2. **The numerics are no longer duplicated here.** The plane, cylinder,
   sphere, benchmark and failure suites now come from
   ``body_surface_measurement/geodesic/backends/selftest.py``, and the
   environment detection from ``geodesic/envreport.py`` - the same code the
   in-Blender panel runs. Two implementations of the same numerical claim can
   drift apart; one cannot.

Those modules are loaded by file path. This script still imports no *add-on*
machinery: no bpy, no operators, no state, no registration. It installs
nothing and modifies nothing.
"""

import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "body_surface_measurement")
GEODESIC = os.path.join(PACKAGE, "geodesic")

SEPARATOR = "-" * 72


def load_modules():
    """Load geodesic.envreport and geodesic.backends without importing bpy."""
    root = types.ModuleType("bsmt_tool")
    root.__path__ = [PACKAGE]
    sys.modules["bsmt_tool"] = root

    geodesic = types.ModuleType("bsmt_tool.geodesic")
    geodesic.__path__ = [GEODESIC]
    sys.modules["bsmt_tool.geodesic"] = geodesic

    spec = importlib.util.spec_from_file_location(
        "bsmt_tool.geodesic.backends",
        os.path.join(GEODESIC, "backends", "__init__.py"),
        submodule_search_locations=[os.path.join(GEODESIC, "backends")],
    )
    backends = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_tool.geodesic.backends"] = backends
    spec.loader.exec_module(backends)

    spec = importlib.util.spec_from_file_location(
        "bsmt_tool.geodesic.envreport", os.path.join(GEODESIC, "envreport.py")
    )
    envreport = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_tool.geodesic.envreport"] = envreport
    spec.loader.exec_module(envreport)

    return envreport, backends


def main():
    print("BSMT Milestone 2.2 - exact geodesic backend environment proof")
    print(SEPARATOR)

    if not os.path.isdir(GEODESIC):
        print("FAIL: %s not found. Run this script from the BSMT checkout."
              % GEODESIC)
        return 2

    try:
        envreport, backends = load_modules()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\nFAIL: could not load the BSMT geodesic modules: %s: %s"
              % (type(exc).__name__, exc))
        return 2

    info = envreport.collect()
    print("\n".join(envreport.format_report(info)))

    if not backends.availability():
        print("")
        print(SEPARATOR)
        print("STOP: pygeodesic is not usable in this interpreter.")
        print("      %s" % backends.unavailable_reason())
        original = backends.status().get("import_traceback", "")
        if original and "ModuleNotFoundError" not in original:
            # A plain "not installed" needs no traceback; anything else does,
            # because an ABI or architecture failure is only diagnosable from
            # the original one.
            print("")
            print("Original traceback:")
            print(original)
        print("")
        print("Milestone 2.2 is closed only when this section succeeds inside")
        print("Blender's own Python. Use the install command printed above.")
        return 1

    print("")
    print(SEPARATOR)
    print("Running the synthetic backend suite")
    report = backends.selftest.run_all(
        include_dense=True,
        dense_triangles=backends.selftest.REFERENCE_SCAN_TRIANGLES,
        include_dijkstra=True,
    )
    print("\n".join(backends.selftest.format_report(report)))

    for suite in report.get("suites", []):
        if suite.get("traceback"):
            print("\n%s raised:" % suite.get("name"))
            print(suite["traceback"])

    print("")
    print(SEPARATOR)
    print("Summary")
    print("  interpreter          : %s" % info["executable"])
    print("  inside Blender       : %s %s"
          % (info["inside_blender"], info["blender_version"]))
    print("  python / numpy       : %s / %s"
          % (info["python_version"], info["numpy_version"]))
    print("  architecture         : %s %s" % (info["system"], info["machine"]))
    print("  pygeodesic           : %s" % backends.backend_version())
    print("  synthetic suite      : %s"
          % ("PASS" if report.get("pass") else "FAIL"))
    print("  ready for Milestone 2.3 : %s"
          % ("yes" if report.get("pass") and info["inside_blender"]
             else "not until this passes inside Blender"))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
