"""Build BSMT release packages. Run with any Python 3.8+; no dependencies.

    python3 tools/build_release.py

Produces, in dist/:

  bsmt-<version>.zip                  the Blender EXTENSION package
  body_surface_measurement-<v>.zip    the legacy add-on package

Why two, and which one matters
------------------------------
The extension package is the one to hand a lab member. Blender 4.2 introduced
extensions, and their `wheels` field is the supported mechanism for shipping a
native dependency: Blender installs the wheel that matches the running
platform, so the same ZIP serves Windows and macOS and nobody runs pip.

The legacy add-on package is the same source with no wheels. It exists as a
fallback for a Blender configured without extension repositories, and it is
what every BSMT version up to 0.18.1 shipped as. It requires pygeodesic to be
installed separately, and says so.

The LICENSE placeholder
-----------------------
A Blender extension manifest REQUIRES a license field, so building one forces
a licensing decision. This script does not make that decision: it writes
PROVISIONAL_LICENSE and prints a warning every time. See docs/LICENSING.md.
"""

import argparse
import ast
import hashlib
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(ROOT, "body_surface_measurement")
WHEELS = os.path.join(ROOT, "wheels")
DIST = os.path.join(ROOT, "dist")

#: PROVISIONAL. The Blender Foundation's position is that an add-on importing
#: bpy is a derivative work of Blender and must be GPL compatible, which is
#: why this is the conventional value for a Blender extension. It is NOT a
#: decision this script is entitled to make on the project's behalf - see
#: docs/LICENSING.md - and it must be confirmed by the project owner before
#: BSMT is distributed to anyone.
PROVISIONAL_LICENSE = "SPDX:GPL-3.0-or-later"

#: Files and directories that are development-only and never shipped.
EXCLUDE_NAMES = {"__pycache__", ".DS_Store", ".git", ".gitignore"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej")

#: Blender 4.5 platform identifiers, and the wheel tag each one needs.
PLATFORM_WHEEL_TAGS = {
    "windows-x64": "win_amd64",
    "macos-arm64": "macosx_11_0_arm64",
}

#: Blender 4.5.13 runs CPython 3.11, so only cp311 wheels can load.
PYTHON_TAG = "cp311"


def addon_version():
    """The single source of truth: VERSION in the package's __init__.

    Read with ast, not by importing, because importing the package needs bpy.
    VERSION rather than bl_info because Blender removes bl_info from a module
    installed as an extension.
    """
    source = open(os.path.join(PACKAGE, "__init__.py"), encoding="utf-8").read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", "") == "VERSION":
                    parts = ast.literal_eval(node.value)
                    return ".".join(str(part) for part in parts), parts
    raise SystemExit("could not read VERSION from the package")


def wheel_files():
    """The vendored wheels, checked for the ABI Blender actually runs."""
    if not os.path.isdir(WHEELS):
        return []
    found = []
    for name in sorted(os.listdir(WHEELS)):
        if not name.endswith(".whl"):
            continue
        if PYTHON_TAG not in name:
            raise SystemExit(
                "wheels/%s is not a %s wheel; Blender 4.5 runs CPython 3.11 "
                "and cannot load it" % (name, PYTHON_TAG))
        found.append(name)
    return found


def platforms_for(wheels):
    """Which Blender platforms the vendored wheels actually cover."""
    covered = []
    for platform, tag in sorted(PLATFORM_WHEEL_TAGS.items()):
        if any(tag in name for name in wheels):
            covered.append(platform)
    return covered


def should_skip(path):
    name = os.path.basename(path)
    return name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIXES)


def package_files():
    """Every source file that belongs in a release, as (absolute, relative)."""
    collected = []
    for folder, folders, names in os.walk(PACKAGE):
        folders[:] = sorted(f for f in folders if f not in EXCLUDE_NAMES)
        for name in sorted(names):
            path = os.path.join(folder, name)
            if should_skip(path):
                continue
            collected.append((path, os.path.relpath(path, PACKAGE)))
    return collected


def manifest_text(version, info, wheels, license_id):
    """blender_manifest.toml. Written by hand: no toml writer in the stdlib."""
    platforms = platforms_for(wheels)
    if not platforms:
        raise SystemExit("no vendored wheel matches a known Blender platform")
    lines = [
        'schema_version = "1.0.0"',
        '',
        'id = "body_surface_measurement"',
        'version = "%s"' % version,
        'name = "Body Surface Measurement Tool (BSMT)"',
        'tagline = "Exact geodesic surface measurement on human body scans"',
        'maintainer = "BSMT project"',
        'type = "add-on"',
        '',
        '# Blender 4.2 is the first release with extensions and wheel support.',
        '# BSMT is validated on 4.5 LTS.',
        'blender_version_min = "4.2.0"',
        '',
        '# PROVISIONAL - see docs/LICENSING.md. The project owner must confirm',
        '# this before BSMT is distributed.',
        'license = ["%s"]' % license_id,
        '',
        'tags = ["3D View", "Mesh"]',
        '',
        '# Only the platforms a vendored wheel actually covers are declared, so',
        '# Blender never offers an install it cannot complete.',
        'platforms = [%s]' % ", ".join('"%s"' % p for p in platforms),
        '',
        '# Blender installs the wheel matching the running platform. The user',
        '# never runs pip.',
        'wheels = [',
    ]
    for name in wheels:
        lines.append('  "./wheels/%s",' % name)
    lines.append(']')
    lines.append('')
    return "\n".join(lines)


def write_zip(path, entries):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, source in entries:
            if isinstance(source, bytes):
                archive.writestr(arcname, source)
            else:
                archive.write(source, arcname)
    return path


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()


def build_extension(version, info, wheels, license_id):
    """The extension package: manifest and sources at the ZIP root."""
    entries = [("blender_manifest.toml",
                manifest_text(version, info, wheels, license_id).encode("utf-8"))]
    for path, relative in package_files():
        entries.append((relative.replace(os.sep, "/"), path))
    for name in wheels:
        entries.append(("wheels/" + name, os.path.join(WHEELS, name)))
    for name in ("README.md", "CHANGELOG.md"):
        source = os.path.join(ROOT, name)
        if os.path.exists(source):
            entries.append((name, source))
    return write_zip(os.path.join(DIST, "bsmt-%s.zip" % version), entries)


def build_legacy(version):
    """The legacy add-on package: the source folder, exactly as before."""
    entries = []
    for path, relative in package_files():
        arcname = "body_surface_measurement/" + relative.replace(os.sep, "/")
        entries.append((arcname, path))
    return write_zip(
        os.path.join(DIST, "body_surface_measurement-%s.zip" % version), entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--license", default=PROVISIONAL_LICENSE,
                        help="SPDX identifier for the extension manifest")
    arguments = parser.parse_args()

    version, info = addon_version()
    wheels = wheel_files()
    platforms = platforms_for(wheels)

    print("BSMT release build")
    print("  version   : %s" % version)
    print("  python    : %s (Blender 4.5 runs CPython 3.11)" % PYTHON_TAG)
    print("  wheels    : %s" % (", ".join(wheels) or "none"))
    print("  platforms : %s" % ", ".join(platforms))
    print("")

    extension = build_extension(version, info, wheels, arguments.license)
    legacy = build_legacy(version)

    print("  %-46s %8d bytes" % (os.path.basename(extension),
                                 os.path.getsize(extension)))
    print("    sha256 %s" % digest(extension))
    print("  %-46s %8d bytes" % (os.path.basename(legacy),
                                 os.path.getsize(legacy)))
    print("    sha256 %s" % digest(legacy))
    print("")
    print("  LICENSE IN THE MANIFEST IS PROVISIONAL: %s" % arguments.license)
    print("  It is a placeholder, not a decision. See docs/LICENSING.md and")
    print("  confirm it with the project owner before distributing BSMT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
