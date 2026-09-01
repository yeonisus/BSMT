"""Pure measurement maths for BSMT.

This module deliberately contains no Blender operator / UI code so the
distance logic stays testable and independent of the interface.
"""

from mathutils import Vector

# Enum items shared by the property definition and the UI.
# The identifier is stable; only the label is user facing.
UNIT_ITEMS = (
    ('MM', "Millimeters (mm)", "One coordinate unit in the file equals 1 mm", 0),
    ('CM', "Centimeters (cm)", "One coordinate unit in the file equals 1 cm", 1),
    ('M', "Meters (m)", "One coordinate unit in the file equals 1 m", 2),
)

# Multiplier that converts one coordinate unit into millimetres.
UNIT_TO_MM = {
    'MM': 1.0,
    'CM': 10.0,
    'M': 1000.0,
}


def unit_multiplier(unit):
    """Millimetres represented by a single coordinate unit.

    Raises KeyError for an unknown identifier on purpose: we never silently
    assume a unit scale that the user did not choose.
    """
    return UNIT_TO_MM[unit]


def mm_to_units(value_mm, unit):
    """Convert a physical millimetre size into coordinate units of the file.

    Used for helper geometry (marker diameter, line thickness) so that on-screen
    sizes are physical, not dependent on how the scan happens to be scaled.
    """
    return value_mm / unit_multiplier(unit)


def straight_distance(point_a, point_b):
    """Euclidean distance between two world-space points, in coordinate units."""
    return (Vector(point_b) - Vector(point_a)).length


def straight_distance_mm(point_a, point_b, unit):
    """Euclidean distance between two world-space points, in millimetres."""
    return straight_distance(point_a, point_b) * unit_multiplier(unit)


def format_mm(value_mm):
    """Human readable millimetre string used in the sidebar."""
    return "{:.2f} mm".format(value_mm)
