"""Cross-platform checks for Milestone 3.13 (Windows x64 + macOS ARM64).

    python3 tests/test_portability.py

These run on any platform and are about what the SOURCE assumes, not about
what the current machine happens to tolerate. Several of them would only fail
on Windows, which is exactly why they are static: nobody here has a Windows
machine to fail on.

What this file cannot do is prove BSMT works on Windows. It proves the source
contains no assumption that would stop it. See docs/windows_acceptance.md for
the part that needs a real Windows Blender.
"""

import ast
import csv
import importlib.util
import io
import json
import ntpath
import os
import posixpath
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "body_surface_measurement")

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if bool(condition):
        print("  PASS  %s" % label)
    else:
        FAILURES.append(label)
        print("  FAIL  %s %s" % (label, detail))


def load(name, relative=""):
    path = os.path.join(PACKAGE, relative, name + ".py")
    spec = importlib.util.spec_from_file_location("bsmt_" + name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_" + name] = module
    spec.loader.exec_module(module)
    return module


export = load("export")
protocol = load("protocol")


def sources(skip=()):
    """(relative path, text) for every shipped source file."""
    for folder, folders, names in os.walk(PACKAGE):
        folders[:] = [f for f in folders if f != "__pycache__"]
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, ROOT)
            if relative in skip:
                continue
            yield relative, open(path, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# platform-specific modules and calls (sect. 6)
# ---------------------------------------------------------------------------

#: Standard-library modules that do not exist, or behave differently, on
#: Windows. Importing one at module level would stop BSMT from loading at all.
UNIX_ONLY = {"resource", "fcntl", "pwd", "grp", "termios", "posix", "pty",
             "tty", "crypt", "syslog"}


def test_no_unix_only_import_at_module_level():
    print("\n[platform] nothing Unix-only is imported when BSMT loads")
    offenders = []
    scanned = 0
    for relative, text in sources():
        scanned += 1
        tree = ast.parse(text)
        for node in tree.body:                        # module level only
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            for name in sorted(set(names) & UNIX_ONLY):
                offenders.append("%s imports %s" % (relative, name))
    check("no module-level Unix-only import in %d files" % scanned,
          not offenders, offenders[:4])


def test_unix_only_use_is_guarded():
    print("\n[platform] the one Unix-only call is guarded and optional")
    text = open(os.path.join(PACKAGE, "geodesic", "backends", "selftest.py"),
                encoding="utf-8").read()
    check("resource is imported inside a function", "\n        import resource" in text
          or "\n    import resource" in text)
    body = text.split("def _max_rss_bytes")[1].split("\ndef ")[0]
    check("  inside a try", "try:" in body.split("import resource")[0])
    check("  and returns None when it is not there",
          "return None" in body)
    check("  which is the documented meaning of the field",
          "not observable" in body or "or None" in body)


def test_no_shell_or_platform_paths():
    print("\n[platform] no shell, no hard-coded platform paths")
    shell = []
    absolute = []
    scanned = 0
    for relative, text in sources():
        scanned += 1
        for forbidden in ("subprocess", "os.system(", "os.popen(",
                          "commands.getoutput"):
            if forbidden in text:
                shell.append("%s uses %s" % (relative, forbidden))
        # A literal that looks like an absolute POSIX or Windows path.
        found = re.findall(r'"(/(?:Users|home|tmp|Applications|var)/[^"]*)"',
                           text)
        found += re.findall(r'"([A-Za-z]:\\\\[^"]*)"', text)
        absolute.extend("%s: %s" % (relative, f) for f in found)
    check("no shell or subprocess anywhere in %d files" % scanned,
          not shell, shell[:4])
    check("no hard-coded absolute path in %d files" % scanned,
          not absolute, absolute[:4])


def test_paths_are_never_parsed_by_hand():
    print("\n[platform] BSMT never splits a path itself")
    # A path arrives from Blender as a string and is handed straight to
    # open(). Splitting on "/" or "\\" is what breaks on the other OS.
    offenders = []
    scanned = 0
    for relative, text in sources():
        scanned += 1
        for forbidden in ('.split("/")', ".split('/')", '.split("\\\\")',
                          'rsplit("/"', "os.sep.join", 'strip("/")'):
            if forbidden in text:
                offenders.append("%s uses %s" % (relative, forbidden))
    check("no hand-rolled path splitting in %d files" % scanned,
          not offenders, offenders[:4])
    text = open(os.path.join(PACKAGE, "geodesic", "envreport.py"),
                encoding="utf-8").read()
    check("envreport uses os.path, which is correct on both",
          "os.path.normpath" in text and "os.path.isdir" in text)


# ---------------------------------------------------------------------------
# encodings (sect. 7)
# ---------------------------------------------------------------------------

def test_every_file_open_declares_its_encoding():
    print("\n[unicode] no open() inherits the Windows locale encoding")
    # This is the Windows bug that would be invisible here: open() without
    # `encoding` uses the LOCALE encoding, which on a Korean Windows is cp949.
    # A protocol written there would be unreadable anywhere else, and a
    # Korean landmark name would be silently mangled.
    offenders = []
    opens = 0
    for relative, text in sources():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                continue                              # not the builtin open
            if getattr(node.func, "id", "") != "open":
                continue
            opens += 1
            keywords = {k.arg for k in node.keywords}
            binary = any(
                isinstance(a, ast.Constant) and isinstance(a.value, str)
                and "b" in a.value for a in node.args[1:])
            if not (binary or "encoding" in keywords):
                offenders.append("%s:%d" % (relative, node.lineno))
    check("all %d calls to open() declare an encoding or are binary" % opens,
          not offenders, offenders[:4])
    check("and there is at least one to check", opens >= 6, opens)


def test_unicode_survives_a_round_trip():
    print("\n[unicode] Korean text survives CSV and JSON, byte for byte")
    korean = {
        "subject": "피험자01",
        "landmark": "목_앞",
        "awkward": '허리, "중간"',
        "notes": "서 있는 자세; 팔 벌림",
    }
    session = {"subject_id": korean["subject"], "condition": "정면",
               "scan_id": "S01_정면_01"}
    mesh = {"measurement_mesh": "A_BSMT", "source_mesh": "A",
            "representation": "", "source_triangles": 0,
            "measurement_triangles": 0, "preprocessing_method": ""}
    record = {
        "protocol_id": "M01", "name": korean["awkward"], "notes": korean["notes"],
        "from_landmark_id": "L01", "from_landmark_name": korean["landmark"],
        "to_landmark_id": "L02", "to_landmark_name": "허리",
        "measurement_type": "BOTH", "enabled": True,
        "straight_valid": True, "straight_mm": 292.5867,
        "surface_valid": False, "surface_mm": 0.0, "ratio": 0.0,
        "status": "VALID", "result_geometry_hash": "abc",
        "backend_name": "", "backend_version": "",
    }
    row = export.measurement_row(session, record, mesh, "0.19.0", "T")

    # Written and read back the way the module itself specifies. A Korean
    # DIRECTORY too, because that is what a Korean Windows user has.
    base = tempfile.mkdtemp()
    try:
        folder = os.path.join(base, "피험자 데이터", "스캔 01")
        os.makedirs(folder)
        path = os.path.join(folder, "측정_measurements.csv")
        export.write_csv(path, export.MEASUREMENT_COLUMNS, [row])
        check("a CSV writes into a Korean directory", os.path.exists(path))
        with open(path, newline="", encoding=export.ENCODING) as handle:
            parsed = list(csv.DictReader(handle))
        check("the Korean subject id survives",
              parsed[0]["subject_id"] == korean["subject"],
              parsed[0]["subject_id"])
        check("the Korean landmark name survives",
              parsed[0]["from_landmark_name"] == korean["landmark"])
        check("a Korean name with a comma and quotes survives",
              parsed[0]["measurement_name"] == korean["awkward"],
              parsed[0]["measurement_name"])
        check("Korean notes survive",
              parsed[0]["notes"] == korean["notes"])
        check("nothing was transliterated",
              all(ord(c) < 128 or c in korean["landmark"]
                  for c in parsed[0]["from_landmark_name"]))

        # The BOM must be the ONLY thing before the header.
        raw = open(path, "rb").read()
        check("the file starts with exactly one UTF-8 BOM",
              raw.startswith(b"\xef\xbb\xbf")
              and not raw[3:].startswith(b"\xef\xbb\xbf"))
        check("and the Korean bytes are UTF-8, not cp949",
              korean["landmark"].encode("utf-8") in raw
              and korean["landmark"].encode("cp949") not in raw)

        # Protocol JSON through the same directory.
        json_path = os.path.join(folder, "프로토콜.json")
        protocol.save_protocol(
            json_path, "정면 자세 연구",
            [(1, "L01", korean["landmark"], korean["notes"]),
             (2, "L02", "허리", "")],
            [(1, "M01", korean["awkward"], 1, 2, "BOTH", True, "")])
        name, landmarks_out, measurements_out = protocol.load_protocol(json_path)
        check("a protocol round-trips through a Korean path",
              name == "정면 자세 연구")
        check("  with its Korean landmark names intact",
              landmarks_out[0][2] == korean["landmark"], landmarks_out[0])
        check("  and its Korean notes", landmarks_out[0][3] == korean["notes"])
        check("  and a Korean measurement name",
              measurements_out[0][2] == korean["awkward"])
        raw = open(json_path, "rb").read()
        check("the JSON is UTF-8 without escapes",
              korean["landmark"].encode("utf-8") in raw)
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# file names on Windows (sect. 5, 6)
# ---------------------------------------------------------------------------

def test_filenames_are_safe_on_windows():
    print("\n[filenames] nothing Windows refuses gets into a name")
    # Every character Windows reserves, plus both separators.
    for character in ':*?"<>|/\\':
        result = export.sanitize("S01%sX" % character)
        check("%r is removed" % character, character not in result, result)

    windows_path = r"C:\Users\Researcher\Documents\BSMT Data\scan.obj"
    stem = export.sanitize(ntpath.splitext(ntpath.basename(windows_path))[0])
    check("a Windows basename sanitises cleanly", stem == "scan", stem)
    name = export.default_filename("measurements", fallback=windows_path)
    check("even a whole Windows path cannot produce a separator",
          "\\" not in name and "/" not in name and ":" not in name, name)
    check("  and it is still a csv", name.endswith(".csv"), name)

    posix_path = "/Users/Researcher/BSMT Data/scan.obj"
    name = export.default_filename("landmarks", fallback=posix_path)
    check("nor can a POSIX path",
          "/" not in name and name.endswith("_landmarks.csv"), name)

    # A Korean id must survive - see test_export for why - but must still be
    # free of separators.
    name = export.default_filename("measurements", "피험자01", "정면")
    check("a Korean session makes a usable Korean file name",
          name == "피험자01_정면_measurements.csv", name)
    for bad in ("\\", "/", ":", "*", "?", '"', "<", ">", "|"):
        check("  containing no %r" % bad, bad not in name)

    # Windows reserved device names are only reserved as the WHOLE stem.
    for device in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT1"):
        name = export.default_filename("measurements", device)
        stem = name[:-len(".csv")]
        check("%s is not left as a bare device name" % device,
              stem.upper() != device, stem)

    check("a name is never empty",
          export.default_filename("measurements", "", "", "", "") != ".csv")
    check("and never absurdly long",
          len(export.default_filename("measurements", "S" * 500,
                                      "C" * 500)) < 200)


# ---------------------------------------------------------------------------
# GPU overlay (sect. 9)
# ---------------------------------------------------------------------------

def test_overlay_uses_portable_gpu_api_only():
    print("\n[gpu] the overlay uses only backend-independent Blender APIs")
    text = open(os.path.join(PACKAGE, "overlay.py"), encoding="utf-8").read()

    check("only the builtin shader is used",
          "gpu.shader.from_builtin('UNIFORM_COLOR')" in text)
    check("no shader source is compiled by hand",
          "GPUShaderCreateInfo" not in text and "shader.create_from_info" not in text
          and "vertexcode" not in text)
    for removed in ("'TRI_FAN'", "'LINE_LOOP'"):
        check("%s is not used - removed from the GPU module in 3.2" % removed,
              removed not in text)
    for allowed in ("'TRIS'", "'LINES'"):
        check("%s is used, which every backend supports" % allowed,
              allowed in text)
    for metal in ("metal", "Metal", "MTL", "opengl", "OpenGL", "glGet",
                  "bgl"):
        check("nothing %s-specific appears" % metal, metal not in text)
    check("the shader is built on first use, never at import",
          "def _shader" in text
          and "from_builtin" not in text.split("def _shader")[0])
    check("a drawing failure cannot take the viewport down",
          "traceback.print_exc()" in text)
    check("blf.size is called through a compatibility helper",
          "_set_font_size" in text)


# ---------------------------------------------------------------------------
# the packaging itself
# ---------------------------------------------------------------------------

def test_release_build_is_reproducible_and_correct():
    print("\n[package] what the build script promises")
    build_path = os.path.join(ROOT, "tools", "build_release.py")
    spec = importlib.util.spec_from_file_location("bsmt_build", build_path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    version, parts = build.addon_version()
    check("the version comes from VERSION, not bl_info",
          "VERSION" in open(build_path, encoding="utf-8").read())
    check("and it parses", re.match(r"^\d+\.\d+\.\d+$", version), version)

    wheels = build.wheel_files()
    check("both platform wheels are vendored", len(wheels) == 2, wheels)
    check("every wheel is cp311, the ABI Blender 4.5 runs",
          all("cp311" in name for name in wheels), wheels)
    platforms = build.platforms_for(wheels)
    check("they cover macOS arm64 and Windows x64",
          set(platforms) == {"macos-arm64", "windows-x64"}, platforms)

    manifest = build.manifest_text(version, parts, wheels, "SPDX:GPL-3.0-or-later")
    check("the manifest declares both platforms",
          '"windows-x64"' in manifest and '"macos-arm64"' in manifest)
    check("the manifest lists both wheels",
          all(name in manifest for name in wheels))
    check("the manifest version matches the package", version in manifest)
    check("blender_version_min allows 4.2, the first with extensions",
          'blender_version_min = "4.2.0"' in manifest)
    check("the licence is marked PROVISIONAL in the manifest",
          "PROVISIONAL" in manifest)

    # A wheel for the wrong Python would load on nobody's Blender.
    saved = build.PYTHON_TAG
    try:
        build.PYTHON_TAG = "cp312"
        raised = False
        try:
            build.wheel_files()
        except SystemExit:
            raised = True
        check("a wheel for the wrong Python is refused at build time", raised)
    finally:
        build.PYTHON_TAG = saved


def test_the_package_carries_no_development_clutter():
    print("\n[package] a lab user gets no development files")
    build_path = os.path.join(ROOT, "tools", "build_release.py")
    text = open(build_path, encoding="utf-8").read()
    for excluded in ("__pycache__", ".pyc", ".DS_Store"):
        check("the build excludes %s" % excluded, excluded in text)
    check("tests are not part of the package",
          "tests" not in text.split("def package_files")[1].split("\ndef ")[0])
    check("nor tools",
          "tools" not in text.split("def package_files")[1].split("\ndef ")[0])


def main():
    print("BSMT Milestone 3.13 - cross-platform portability tests")
    print("  running on : %s" % sys.platform)
    for test in (
        test_no_unix_only_import_at_module_level,
        test_unix_only_use_is_guarded,
        test_no_shell_or_platform_paths,
        test_paths_are_never_parsed_by_hand,
        test_every_file_open_declares_its_encoding,
        test_unicode_survives_a_round_trip,
        test_filenames_are_safe_on_windows,
        test_overlay_uses_portable_gpu_api_only,
        test_release_build_is_reproducible_and_correct,
        test_the_package_carries_no_development_clutter,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
