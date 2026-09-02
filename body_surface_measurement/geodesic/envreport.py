"""Runtime detection of the Python environment BSMT is actually running in.

Milestone 2.2, item 1. **Nothing here is assumed** - every value is read from
the live interpreter. The expected values recorded in PROJECT_SPEC.md
(Blender 4.5.13, Python 3.11, numpy 1.26.4, macOS arm64) are reported as a
*comparison* against what is detected, never substituted for it.

Standard library only, plus an optional numpy and an optional bpy: this module
must import and produce a useful report in the degenerate case where numpy is
broken, which is precisely the case a user needs a diagnostic for.

It also derives the correct pip install target for THIS interpreter. That is
not cosmetic. Blender initialises its Python with ``no_user_site = 1``, so
``pip install --user`` installs into a directory Blender will never look at:
the package imports fine in Terminal and is invisible inside Blender. The
target is therefore chosen from the paths Blender itself actually searches,
determined at runtime.
"""

import os
import platform
import sys

EXPECTED = {
    "blender_version": "4.5.13",
    "python_version": "3.11",
    "numpy_version": "1.26.4",
    "machine": "arm64",
    "system": "Darwin",
}


def _try(function, default=""):
    try:
        return function()
    except Exception as exc:  # noqa: BLE001
        return "%s: %s" % (type(exc).__name__, exc) if default == "" else default


def _writable(path):
    """True when a file could be created at `path`, walking up to a parent."""
    if not path:
        return False
    probe = path
    for _ in range(6):
        if os.path.isdir(probe):
            return os.access(probe, os.W_OK | os.X_OK)
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return False


def collect():
    """Everything Milestone 2.2 item 1 asks for, read from the live process."""
    info = {}

    # --- interpreter -------------------------------------------------------
    info["python_version"] = sys.version.split()[0]
    info["python_version_full"] = sys.version.replace("\n", " ")
    info["python_implementation"] = _try(platform.python_implementation)
    info["executable"] = sys.executable
    info["prefix"] = sys.prefix
    info["system"] = _try(platform.system)
    info["release"] = _try(platform.release)
    info["machine"] = _try(platform.machine)
    info["platform"] = _try(platform.platform)
    info["maxsize_64bit"] = sys.maxsize > 2 ** 32
    info["wheel_tag"] = "cp%d%d" % sys.version_info[:2]

    # --- Blender -----------------------------------------------------------
    try:
        import bpy
    except Exception:  # noqa: BLE001
        bpy = None
    info["inside_blender"] = bpy is not None
    if bpy is not None:
        info["blender_version"] = _try(lambda: bpy.app.version_string)
        info["blender_version_tuple"] = _try(lambda: tuple(bpy.app.version), ())
        info["blender_binary"] = _try(lambda: bpy.app.binary_path)
        info["blender_build_platform"] = _try(
            lambda: bpy.app.build_platform.decode()
            if isinstance(bpy.app.build_platform, bytes)
            else str(bpy.app.build_platform)
        )
    else:
        info["blender_version"] = ""
        info["blender_version_tuple"] = ()
        info["blender_binary"] = ""
        info["blender_build_platform"] = ""

    # --- numpy -------------------------------------------------------------
    try:
        import numpy
        info["numpy_version"] = numpy.__version__
        info["numpy_path"] = numpy.__file__
        info["numpy_error"] = ""
    except Exception as exc:  # noqa: BLE001
        info["numpy_version"] = ""
        info["numpy_path"] = ""
        info["numpy_error"] = "%s: %s" % (type(exc).__name__, exc)

    # --- site-packages -----------------------------------------------------
    try:
        import sysconfig
        info["purelib"] = sysconfig.get_paths().get("purelib", "")
        info["platlib"] = sysconfig.get_paths().get("platlib", "")
    except Exception:  # noqa: BLE001
        info["purelib"] = info["platlib"] = ""

    try:
        import site
        info["user_site_enabled"] = bool(site.ENABLE_USER_SITE)
        info["user_site"] = _try(site.getusersitepackages)
        info["site_packages"] = _try(site.getsitepackages, [])
    except Exception:  # noqa: BLE001
        info["user_site_enabled"] = False
        info["user_site"] = ""
        info["site_packages"] = []
    info["no_user_site_flag"] = bool(getattr(sys.flags, "no_user_site", 0))

    info["sys_path"] = list(sys.path)
    info["import_paths"] = [
        path for path in sys.path
        if path and ("site-packages" in path or path.endswith("modules"))
    ]

    # --- where an install must go so BLENDER can see it --------------------
    info["install_targets"] = _install_targets(bpy, info)
    info["recommended_target"] = (
        info["install_targets"][0]["path"] if info["install_targets"] else ""
    )
    info["install_commands"] = _install_commands(info)

    # --- backend -----------------------------------------------------------
    try:
        from .backends import status as backend_status
        info["backend"] = backend_status()
        info["backend_error"] = ""
    except Exception as exc:  # noqa: BLE001
        info["backend"] = {}
        info["backend_error"] = "%s: %s" % (type(exc).__name__, exc)

    info["mismatches"] = _mismatches(info)
    return info


def _install_targets(bpy, info):
    """Candidate install directories, best first.

    Only directories the running interpreter will actually import from are
    offered. ``--user`` is never offered when ``no_user_site`` is set, which
    is exactly Blender's configuration.
    """
    targets = []
    searched = set(os.path.normpath(p) for p in sys.path if p)

    if bpy is not None:
        addons_modules = _try(
            lambda: bpy.utils.user_resource("SCRIPTS", path="addons/modules",
                                            create=False),
            "",
        )
        if addons_modules and not addons_modules.startswith(("OSError", "Attribute",
                                                             "TypeError", "ValueError")):
            targets.append({
                "path": addons_modules,
                "label": "Blender user scripts modules directory",
                "on_sys_path": os.path.normpath(addons_modules) in searched,
                "writable": _writable(addons_modules),
                "exists": os.path.isdir(addons_modules),
                "survives_blender_update": True,
                "note": "outside the app bundle, so a Blender update does not "
                        "wipe it; Blender adds it to sys.path unconditionally",
            })

    bundled = info.get("platlib") or info.get("purelib")
    if bundled:
        inside = bpy is not None
        targets.append({
            "path": bundled,
            "label": ("Blender bundled site-packages" if inside
                      else "this interpreter's site-packages"),
            "on_sys_path": os.path.normpath(bundled) in searched,
            "writable": _writable(bundled),
            "exists": os.path.isdir(bundled),
            "survives_blender_update": not inside,
            "note": ("inside the application bundle; a Blender update or "
                     "reinstall removes anything added here" if inside
                     else "the default install location for this interpreter"),
        })

    if info.get("user_site") and not info.get("no_user_site_flag"):
        targets.append({
            "path": info["user_site"],
            "label": "per-user site-packages",
            "on_sys_path": os.path.normpath(info["user_site"]) in searched,
            "writable": _writable(info["user_site"]),
            "exists": os.path.isdir(info["user_site"]),
            "survives_blender_update": True,
            "note": "shared with every other Python of this version on the "
                    "machine, so it can shadow Blender's own packages",
        })

    targets.sort(key=lambda t: (not t["writable"], not t["on_sys_path"]))
    return targets


def _install_commands(info):
    """Exact, copy-pasteable Terminal commands for THIS interpreter.

    ``--no-deps`` is deliberate and load-bearing. pygeodesic 0.1.11 declares
    ``numpy<3,>=2``, but Blender 4.5.13 bundles numpy 1.26.4 and the compiled
    extension imports cleanly against it (measured 2026-09-02). Without
    ``--no-deps``, pip would install numpy 2.x ahead of Blender's own numpy on
    sys.path and change the numpy every other part of Blender uses. That is a
    far worse outcome than the dependency metadata being conservative.

    ``sudo`` is never used and never suggested.
    """
    executable = sys.executable or "python3"
    target = info.get("recommended_target", "")
    commands = []
    if target:
        commands.append({
            "purpose": "install pygeodesic where this interpreter will find it",
            "command": '"%s" -m pip install --no-deps --target "%s" pygeodesic'
                       % (executable, target),
        })
    else:
        commands.append({
            "purpose": "install pygeodesic into this interpreter",
            "command": '"%s" -m pip install --no-deps pygeodesic' % executable,
        })
    binary = info.get("blender_binary", "")
    if binary:
        commands.append({
            "purpose": "verify it imports inside Blender itself",
            "command": '"%s" --background --python-expr '
                       '"import pygeodesic, pygeodesic.geodesic; '
                       'print(\'pygeodesic\', pygeodesic.__version__)"' % binary,
        })
    commands.append({
        "purpose": "if pip is missing from this interpreter",
        "command": '"%s" -m ensurepip' % executable,
    })
    return commands


def _mismatches(info):
    """Detected values that differ from what PROJECT_SPEC.md recorded."""
    problems = []
    detected_python = ".".join(info["python_version"].split(".")[:2])
    if detected_python != EXPECTED["python_version"]:
        problems.append(
            "Python is %s, not the %s the spec assumes; the cp%s wheel matrix "
            "must be revisited before relying on it."
            % (detected_python, EXPECTED["python_version"],
               detected_python.replace(".", ""))
        )
    if info["inside_blender"] and info["blender_version"]:
        if not info["blender_version"].startswith(EXPECTED["blender_version"]):
            problems.append(
                "Blender is %s, not %s; re-record the validated environment."
                % (info["blender_version"], EXPECTED["blender_version"])
            )
    if info["numpy_version"] and info["numpy_version"] != EXPECTED["numpy_version"]:
        problems.append(
            "numpy is %s, not %s; a compiled wheel's ABI assumption may differ."
            % (info["numpy_version"], EXPECTED["numpy_version"])
        )
    if info["machine"] and info["machine"] != EXPECTED["machine"]:
        problems.append(
            "CPU architecture is %s, not %s; an %s wheel would be the wrong "
            "one to install." % (info["machine"], EXPECTED["machine"],
                                 EXPECTED["machine"])
        )
    if info["numpy_error"]:
        problems.append("numpy did not import: %s" % info["numpy_error"])
    return problems


def format_report(info):
    """Render collect() as plain lines for the panel and the system console."""
    lines = ["BSMT Milestone 2.2 - environment report"]

    lines.append("")
    lines.append("Blender")
    if info["inside_blender"]:
        lines.append("  version:        %s" % info["blender_version"])
        lines.append("  binary:         %s" % info["blender_binary"])
        lines.append("  build platform: %s" % info["blender_build_platform"])
    else:
        lines.append("  not running inside Blender")

    lines.append("")
    lines.append("Python")
    lines.append("  version:        %s" % info["python_version"])
    lines.append("  sys.version:    %s" % info["python_version_full"])
    lines.append("  sys.executable: %s" % info["executable"])
    lines.append("  implementation: %s" % info["python_implementation"])
    lines.append("  wheel tag:      %s" % info["wheel_tag"])
    lines.append("  64-bit:         %s" % info["maxsize_64bit"])

    lines.append("")
    lines.append("Platform")
    lines.append("  system:         %s" % info["system"])
    lines.append("  release:        %s" % info["release"])
    lines.append("  machine:        %s" % info["machine"])
    lines.append("  platform:       %s" % info["platform"])

    lines.append("")
    lines.append("numpy")
    if info["numpy_version"]:
        lines.append("  version:        %s" % info["numpy_version"])
        lines.append("  location:       %s" % info["numpy_path"])
    else:
        lines.append("  UNAVAILABLE:    %s" % info["numpy_error"])

    lines.append("")
    lines.append("Import paths")
    lines.append("  purelib:        %s" % info["purelib"])
    lines.append("  platlib:        %s" % info["platlib"])
    lines.append("  user site:      %s" % info["user_site"])
    lines.append("  user site used: %s%s" % (
        info["user_site_enabled"] and not info["no_user_site_flag"],
        "  (Blender sets no_user_site, so 'pip install --user' is INVISIBLE "
        "here)" if info["no_user_site_flag"] else "",
    ))
    for path in info["import_paths"]:
        lines.append("    %s" % path)

    lines.append("")
    lines.append("Exact Geodesic Backend")
    backend = info.get("backend", {})
    if info.get("backend_error"):
        lines.append("  status could not be read: %s" % info["backend_error"])
    else:
        lines.append("  pygeodesic:     %s"
                     % ("Available" if backend.get("available") else "Unavailable"))
        lines.append("  version:        %s" % (backend.get("version") or "-"))
        lines.append("  import path:    %s" % (backend.get("module_path") or "-"))
        if backend.get("wrapper_error"):
            lines.append("  WRAPPER ERROR:  %s" % backend["wrapper_error"])
        if not backend.get("available") and backend.get("import_error"):
            lines.append("  import error:   %s" % backend["import_error"])
            lines.append("  (complete traceback printed to the system console)")

    lines.append("")
    lines.append("Install targets this interpreter actually imports from")
    if not info["install_targets"]:
        lines.append("  none detected")
    for index, target in enumerate(info["install_targets"]):
        lines.append("  %s%s" % ("-> " if index == 0 else "   ", target["path"]))
        lines.append("       %s" % target["label"])
        lines.append("       on sys.path: %s, writable: %s, exists: %s, "
                     "survives Blender update: %s"
                     % (target["on_sys_path"], target["writable"],
                        target["exists"], target["survives_blender_update"]))
        lines.append("       %s" % target["note"])

    lines.append("")
    lines.append("Install commands (no sudo, this interpreter only)")
    for entry in info["install_commands"]:
        lines.append("  # %s" % entry["purpose"])
        lines.append("  %s" % entry["command"])

    if info["mismatches"]:
        lines.append("")
        lines.append("Differences from the environment PROJECT_SPEC.md records")
        for problem in info["mismatches"]:
            lines.append("  ! %s" % problem)

    return lines
