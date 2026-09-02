"""Offline tests for Milestone 3.8: landmark label content and styling.

    python3 tests/test_labels.py

`labels.py` splits deliberately in two: what to draw (pure, tested here) and
the blf calls that draw it (a viewport callback, exercised in Blender). Every
decision a researcher can see - the text, the color, the size, which
landmarks get a label at all - is on the pure side.
"""

import importlib.util
import os
import sys
import types

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


# ---------------------------------------------------------------------------
# a bpy stub thin enough that labels.py runs unchanged
# ---------------------------------------------------------------------------

class FakeVector(tuple):
    @property
    def translation(self):
        return self


class FakeMatrix(object):
    def __init__(self, translation):
        self.translation = tuple(translation)


class FakeObject(object):
    def __init__(self, name, translation, helper=True):
        self.name = name
        self.matrix_world = FakeMatrix(translation)
        self._flags = {"bsmt_helper": helper}

    def get(self, key, default=None):
        return self._flags.get(key, default)


def install_stubs():
    bpy = types.ModuleType("bpy")
    bpy.data = types.SimpleNamespace(objects={})
    bpy.app = types.SimpleNamespace(driver_namespace={})
    bpy.context = types.SimpleNamespace()
    bpy.types = types.SimpleNamespace(SpaceView3D=object, Mesh=object)
    sys.modules["bpy"] = bpy

    bmesh = types.ModuleType("bmesh")
    bmesh.ops = types.SimpleNamespace()
    sys.modules["bmesh"] = bmesh

    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = FakeVector
    sys.modules["mathutils"] = mathutils
    return bpy


BPY = install_stubs()

package = types.ModuleType("bsmt_pkg")
package.__path__ = [PACKAGE]
sys.modules["bsmt_pkg"] = package


def load(name):
    spec = importlib.util.spec_from_file_location(
        "bsmt_pkg." + name, os.path.join(PACKAGE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["bsmt_pkg." + name] = module
    setattr(package, name, module)
    spec.loader.exec_module(module)
    return module


landmarks = load("landmarks")
measurement = load("measurement")
visualization = load("visualization")
labels = load("labels")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

class FakePoint(object):
    def __init__(self, valid=True, world=(0.0, 0.0, 0.0)):
        self.valid = valid
        self.world_xyz = world


class FakeLandmark(object):
    def __init__(self, stable_id, name, status=landmarks.STATUS_VALID,
                 valid=True, world=(0.0, 0.0, 0.0)):
        self.stable_id = stable_id
        self.name = name
        self.display_name = ""
        self.status = status
        self.surface_point = FakePoint(valid, world)

    @property
    def label(self):
        return self.display_name or self.name or "(unnamed)"


class FakeProps(object):
    def __init__(self, **overrides):
        self.show_landmark_labels = True
        self.landmark_label_color = (1.0, 1.0, 1.0, 1.0)
        self.landmark_label_size = labels.DEFAULT_LABEL_SIZE
        self.landmark_label_offset = labels.DEFAULT_LABEL_OFFSET
        self.landmark_label_shadow = True
        self.landmark_label_scope = 'ALL'
        self.landmark_index = 0
        for key, value in overrides.items():
            setattr(self, key, value)


def three_landmarks():
    return [
        FakeLandmark(1, "P01", world=(10.0, 0.0, 0.0)),
        FakeLandmark(2, "P02", world=(0.0, 20.0, 0.0)),
        FakeLandmark(3, "P03", world=(0.0, 0.0, 30.0)),
    ]


# ---------------------------------------------------------------------------
# text (sect. 1, 9, 10)
# ---------------------------------------------------------------------------

def test_label_text():
    print("\n[text] the label is the landmark's own name")
    check("a valid landmark shows just its name",
          labels.label_text("P01", landmarks.STATUS_VALID) == "P01")
    check("an anatomical name survives unchanged",
          labels.label_text("Acromion_L", landmarks.STATUS_VALID)
          == "Acromion_L")
    check("a non-ascii name survives",
          labels.label_text("목_앞", landmarks.STATUS_VALID)
          == "목_앞")

    # Sect. 9: a landmark that is not VALID must not read as normal data.
    # Colour alone is not enough - it is invisible to a colour-blind reader
    # and it does not survive a screenshot in a paper.
    for status, word in ((landmarks.STATUS_STALE, "STALE"),
                         (landmarks.STATUS_INVALID, "INVALID"),
                         (landmarks.STATUS_NEEDS_REFRESH, "NEEDS REFRESH")):
        text = labels.label_text("P01", status)
        check("%s is said in words" % status, word in text, text)
        check("  and the name is still there", text.startswith("P01"), text)
    check("only VALID is unadorned",
          labels.label_text("P01", landmarks.STATUS_VALID) == "P01")
    check("the status words come from landmarks.STATUS_SHORT",
          all(landmarks.STATUS_SHORT[s] in labels.label_text("X", s)
              for s in (landmarks.STATUS_STALE, landmarks.STATUS_INVALID)))


def test_text_is_never_cached():
    print("\n[text] renaming shows immediately (sect. 10)")
    props = FakeProps()
    items = three_landmarks()
    first = labels.label_entries(props, items, 0)
    check("P03 is labelled P03",
          [e for e in first if e["stable_id"] == 3][0]["text"] == "P03")

    items[2].name = "Acromion_L"
    again = labels.label_entries(props, items, 0)
    check("after renaming, the very next read shows the new name",
          [e for e in again if e["stable_id"] == 3][0]["text"]
          == "Acromion_L")
    check("nothing else changed",
          [e["text"] for e in again[:2]] == ["P01", "P02"])

    # And there is genuinely nothing to invalidate.
    source = open(os.path.join(PACKAGE, "labels.py")).read()
    for forbidden in ("_CACHE", "lru_cache", "cached"):
        check("labels.py holds no %s" % forbidden, forbidden not in source)


# ---------------------------------------------------------------------------
# colour (sect. 9)
# ---------------------------------------------------------------------------

def test_label_color():
    print("\n[color] the researcher's color, unless the status overrides it")
    chosen = (0.2, 0.4, 0.9, 1.0)
    check("a VALID landmark uses the Label Color",
          labels.label_color(landmarks.STATUS_VALID, chosen) == chosen)

    for status in (landmarks.STATUS_STALE, landmarks.STATUS_INVALID,
                   landmarks.STATUS_NEEDS_REFRESH):
        color = labels.label_color(status, chosen)
        check("%s ignores the chosen color" % status, color[:3] != chosen[:3],
              color)
        check("  and takes it from the existing status system",
              color[:3] == tuple(
                  float(v) for v in visualization.landmark_color(status)[:3]),
              color)
        check("  keeping the chosen alpha", color[3] == chosen[3])

    check("no new color vocabulary is invented here",
          "(1.0, 0.45, 0.0" not in open(
              os.path.join(PACKAGE, "labels.py")).read(),
          "labels.py must not hard-code a status color")


def test_marker_color_follows_the_same_rule():
    print("\n[color] markers and labels agree")
    chosen = (0.1, 0.8, 0.3, 1.0)
    check("a VALID marker uses the Marker Color",
          visualization.landmark_color(landmarks.STATUS_VALID, chosen)
          == chosen)
    check("a STALE marker keeps its status color",
          visualization.landmark_color(landmarks.STATUS_STALE, chosen)
          == visualization.LANDMARK_STALE_COLOR)
    check("an INVALID marker keeps its status color",
          visualization.landmark_color(landmarks.STATUS_INVALID, chosen)
          == visualization.LANDMARK_STALE_COLOR)
    check("with no color given, the old behaviour is unchanged",
          visualization.landmark_color(landmarks.STATUS_VALID)
          == visualization.LANDMARK_COLOR)


# ---------------------------------------------------------------------------
# which landmarks get a label (sect. 1, 8)
# ---------------------------------------------------------------------------

def test_which_landmarks_are_labelled():
    print("\n[scope] only positioned landmarks, and only in scope")
    props = FakeProps()
    items = three_landmarks()
    check("all three are labelled", len(labels.label_entries(props, items, 0)) == 3)

    items[1].surface_point.valid = False
    entries = labels.label_entries(props, items, 0)
    check("an unpicked landmark gets no label", len(entries) == 2,
          [e["text"] for e in entries])
    check("and it is the right one missing",
          {e["stable_id"] for e in entries} == {1, 3})
    items[1].surface_point.valid = True

    props.landmark_label_scope = 'SELECTED'
    entries = labels.label_entries(props, items, 2)
    check("SELECTED scope labels exactly one", len(entries) == 1)
    check("and it is the selected row", entries[0]["stable_id"] == 3)
    check("selecting another row moves the label",
          labels.label_entries(props, items, 0)[0]["stable_id"] == 1)
    check("no selection labels nothing",
          labels.label_entries(props, items, -1) == [])

    props.landmark_label_scope = 'ALL'
    props.show_landmark_labels = False
    check("Show Labels off draws nothing",
          labels.label_entries(props, items, 0) == [])
    props.show_landmark_labels = True
    check("and an absent collection is not an error",
          labels.label_entries(props, None, 0) == [])

    many = [FakeLandmark(i, "P%03d" % i) for i in range(1, labels.MAX_LABELS + 60)]
    check("a pathological count is capped",
          len(labels.label_entries(props, many, 0)) == labels.MAX_LABELS)


# ---------------------------------------------------------------------------
# position (sect. 1, 12) and size (sect. 4, 6)
# ---------------------------------------------------------------------------

def test_position_follows_the_marker():
    print("\n[position] the label is anchored to the marker it names")
    props = FakeProps()
    items = three_landmarks()
    BPY.data.objects.clear()

    check("with no marker, the stored world position is used",
          labels.label_entries(props, items, 0)[0]["world"] == (10.0, 0.0, 0.0))

    # A marker exists: the label follows THAT, so whatever moves the marker -
    # a translate, a rotate, Apply Alignment - moves the label with it.
    name = visualization.landmark_object_name(1)
    BPY.data.objects[name] = FakeObject(name, (111.0, 222.0, 333.0))
    check("with a marker, the label follows the marker",
          labels.label_entries(props, items, 0)[0]["world"]
          == (111.0, 222.0, 333.0))

    BPY.data.objects[name].matrix_world = FakeMatrix((1.0, 2.0, 3.0))
    check("moving the marker moves the label",
          labels.label_entries(props, items, 0)[0]["world"] == (1.0, 2.0, 3.0))

    # An object that is not one of ours is never trusted as an anchor.
    BPY.data.objects[name] = FakeObject(name, (9.0, 9.0, 9.0), helper=False)
    check("a non-helper object of the same name is ignored",
          labels.label_entries(props, items, 0)[0]["world"] == (10.0, 0.0, 0.0))
    BPY.data.objects.clear()


def test_size_is_screen_space():
    print("\n[size] label size is pixels, and the selection is emphasised")
    props = FakeProps()
    items = three_landmarks()
    entries = labels.label_entries(props, items, 1)
    sizes = {e["stable_id"]: e["size"] for e in entries}
    check("unselected labels use the chosen size",
          sizes[1] == labels.DEFAULT_LABEL_SIZE and
          sizes[3] == labels.DEFAULT_LABEL_SIZE, sizes)
    check("the selected label is drawn larger",
          sizes[2] == labels.DEFAULT_LABEL_SIZE + labels.SELECTED_SIZE_BONUS,
          sizes)
    check("and is marked selected",
          [e["selected"] for e in entries] == [False, True, False])

    props.landmark_label_size = 28
    bigger = {e["stable_id"]: e["size"]
              for e in labels.label_entries(props, items, 1)}
    check("changing the size changes every label", bigger[1] == 28)
    check("and the selected one keeps its bonus",
          bigger[2] == 28 + labels.SELECTED_SIZE_BONUS)
    check("the world positions are untouched by a size change",
          [e["world"] for e in labels.label_entries(props, items, 1)]
          == [(10.0, 0.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 30.0)])

    source = open(os.path.join(PACKAGE, "labels.py")).read()
    check("the size is never converted from millimetres",
          "mm_to_units" not in source and "_mm" not in source)
    check("and the module says the size is in screen pixels",
          "screen" in source.lower() and "pixel" in source.lower())


# ---------------------------------------------------------------------------
# the overlay never writes to the scene (sect. 1, 11)
# ---------------------------------------------------------------------------

def test_the_overlay_is_read_only():
    print("\n[safety] drawing a label cannot change anything")
    source = open(os.path.join(PACKAGE, "labels.py")).read()
    for forbidden in ("bpy.data.objects.new", "bpy.data.texts",
                      "bpy.data.curves", "bpy.ops", "meshcache",
                      "surface_point.triangle_index =", "bmesh"):
        check("labels.py never uses %s" % forbidden, forbidden not in source)
    check("no Text object is ever created",
          "bpy.data.fonts" not in source and "font_add" not in source
          and "type='FONT'" not in source)
    check("it does not import the solver",
          "geodesic" not in source and "registry" not in source)
    check("the draw callback is POST_PIXEL",
          "'POST_PIXEL'" in source)
    check("the handle survives Reload Scripts",
          "driver_namespace" in source)


def main():
    print("BSMT Milestone 3.8 - landmark label tests")
    for test in (
        test_label_text,
        test_text_is_never_cached,
        test_label_color,
        test_marker_color_follows_the_same_rule,
        test_which_landmarks_are_labelled,
        test_position_follows_the_marker,
        test_size_is_screen_space,
        test_the_overlay_is_read_only,
    ):
        test()
    print("\n%d checks, %d failure(s)" % (CHECKS[0], len(FAILURES)))
    for name in FAILURES:
        print("  FAILED: %s" % name)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
