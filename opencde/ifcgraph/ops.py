# This file was generated with the assistance of an AI coding tool.
"""Dataspatium's write tools on an IFC model and its topology of the walls, with Dataspatium's own code (ADR 0021).

A copy of Dataspatium's class ``_ChangeSet`` and of its topology (``_ifc_topology``), with the module-level helpers
they call (``python/dataspatium.py``, main, commit ffd1f6e of 2026-11-22), kept in step by hand, so that one
list of operations gives the same model in the app and on the server, and the server finds the rooms the app finds.
A change set that carries the operations as ``ops`` (``documents/changes.py``) is tried by the apply worker
(``apply.py``), which runs them with :func:`run` on the base version's file before its changes; the topology worker
(``topology.py``) finds the rooms of a file (the owner's decision F36 a: the server makes the client's room check).
The operations are the AI's write tools in the app: ``create_storeys_and_grid``, ``define_type``, ``create_wall``,
``create_slab``, ``create_roof``, ``create_opening``, ``create_element``, ``create_spaces``, ``set_properties``,
``set_classification``, ``assign``, ``edit_element``, ``transform_elements``, ``delete_elements``, ``set_site`` and
``set_responsible_party``,
each a JSON object with its tool's name as ``tool`` and its arguments beside it; ``_ChangeSet``'s docstring lists the
fields each release of the form added. Lengths are metres, scaled to the file's unit. A wall, a slab, a roof or an
opening (or the door or window that fills it), a type of ``define_type`` and an element of ``create_element`` get the
ref ``new-1``, ``new-2`` … in the order of the operations that create them (``_ChangeSet.CREATES``), and so does each
room that ``create_spaces`` lists, in its order; the spaces of ``rooms`` null get none. An operation names an element
or a type by its GlobalId, by its STEP id only where no GlobalId reaches it, or by such a ref (the owner's decision
F34 a, elements named by GlobalId, which the app's copy has too), and a storey by its name. ``create_wall`` gives a
wall its centre line as an ``Axis`` representation, which the topology reads, and ``create_spaces`` makes the rooms
of the topology spaces (F37 a: rooms become IfcSpace through a proposal), each with its quantities and its boundaries
to the walls, doors and windows along its edge; a door or window made later in a wall that bounds a space bounds it
too. ``edit_element`` changes an element in place, so that it keeps its GlobalId. After the operations the walls the
change set made are joined, what it made or changed gets its base quantities, and LoadBearing, Status and IsExternal
(the last from the topology) land in the common sets. Every class and enumeration value an operation sets is checked
against the file's schema (the app's decision B-57.2, the right IFC class), a set named ``Pset_`` or ``Qto_`` against
IfcOpenShell's templates of the schema, and a value the release deprecates is never written (bSI rule IFC102): a wall
is ``SOLIDWALL``, or ``PARTITIONING`` with ``partition``; in IFC4X3 a storey has its height in its placement alone and
a room is ``SPACE`` with ``Pset_SpaceCommon.IsExternal`` false. The change set's geometry is raw entities but for the
door and window types, whose bodies IfcOpenShell's API draws (``ifcopenshell.api.geometry``); only the topology builds
a body, with ``ifcopenshell.geom``, for a wall without an ``Axis``. Nothing depends on Pyodide.

Against the original only this differs, so that the next copy is a diff away:

- the imports: ``math`` and ``sys``, which the original imports at the top of its module, are imported here;
- the error class: ``DataspatiumError`` keeps its name and its base, with a docstring of its own;
- ``_ChangeSet.MAX_PROPOSAL`` and ``_ChangeSet.proposal`` are left out: they lower the operations to attribute-level
  changes with the app's ``_Proposal``, which is not copied, since the server applies the operations themselves;
- of the original's part on the right IFC class only what ``_ChangeSet`` calls is copied (``DEPRECATED_VALUES``,
  ``_ifc_schema``, ``_ifc_declaration``, ``_ifc_enum_items``, ``_ifc_concrete`` and ``IFC_NEW_SCHEMAS``, which the
  original keeps further down): not its read tools ``_ifc_class_search`` and ``_ifc_class_report`` with their
  helpers;
- the owner settings: :class:`ServerChangeSet`, below the copy, overrides ``owner_settings``, so that the owner
  histories IfcOpenShell's API makes name the proposer and this server instead of the file's own owner;
- ``_ifc_topology`` also gives each storey the GlobalIds of the walls it could not read (``unread_walls``), and
  ``_topo_plan`` those of the walls with an open end (``dangling``), for the report's rooms, which name elements by
  GlobalId; the lines that do so are marked ``# Dataregium``;
- ruff formats the copy in lines of 120 characters and lets its idioms be (``pyproject.toml``), and forty-two
  comments quiet bandit (``# nosec B110`` and ``B112``, seventeen) and ty (``# ty: ignore``, twenty-five); no other
  statement changed. ``unbound`` and ``host_of`` are defined once each, as in the original since its 2cbe038: the
  later ``unbound`` and the earlier ``host_of``, which also finds the wall of an opening that nothing fills.
"""

from __future__ import annotations

import math
import re
import sys
from typing import Any

from opencde import __version__


class DataspatiumError(Exception):
    """An operation that cannot be applied, in the words of Dataspatium's code (in the app, the app's language)."""


def _is_na(v):
    if v is None:
        return True
    try:
        if v is getattr(sys.modules.get("pandas"), "NA", object()) or v is getattr(
            sys.modules.get("pandas"), "NaT", object()
        ):
            return True
    except Exception:  # nosec B110
        pass
    return isinstance(v, float) and math.isnan(v)


def _ifc_text(v):
    return None if v is None else str(v)


def _ifc_value(v):
    if _is_na(v):
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except Exception:  # nosec B110
            pass
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    return str(v)


# ---- the change set of "Ändra med AI" ---------------------------------------------------------
# The AI's write tools never touch the model themselves: they fill a change set (a JSON list of operations) that the person
# previews and approves in the app; only then does _ifc_changeset() apply it, as a recipe step with undo. Lengths are metres and
# are scaled to the file's unit. A created element gets a ref ("new-1", "new-2", ...) in the order of the operations
# create_wall, create_slab, create_roof and create_opening, so that a later operation can name it before the file has given it an id;
# so does the type of define_type, which create_wall, create_slab and create_roof name as their type, and the element of create_element,
# which create_element names as part_of or host and whose type is a define_type ref as well.


def _cs_num(op, key, default=None, positive=False, unit="metres"):
    v = op.get(key)
    if v is None:
        if default is None:
            raise DataspatiumError("%s is required (%s)" % (key, unit))
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise DataspatiumError("%s must be a number (%s)" % (key, unit))
    if positive and v <= 0:
        raise DataspatiumError("%s must be greater than zero" % key)
    return v


def _cs_xy(value, key):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise DataspatiumError("%s must be [x, y] in metres" % key)
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        raise DataspatiumError("%s must be [x, y] in metres" % key)


def _cs_label(i):
    """A, B, ..., Z, AA, AB, ... for the grid's axes along X."""
    out = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _cs_angle(degrees):
    """An IfcCompoundPlaneAngleMeasure: degrees, minutes, seconds and millionths of a second, every part carrying the sign."""
    sign = -1 if float(degrees) < 0 else 1
    micro = int(round(abs(float(degrees)) * 3600.0 * 1000000.0))
    d, micro = divmod(micro, 3600 * 1000000)
    m, micro = divmod(micro, 60 * 1000000)
    s, micro = divmod(micro, 1000000)
    return [sign * d, sign * m, sign * s, sign * micro]


# SWEREF 99's twelve local zones in the order of their EPSG codes 3007 to 3018; the compound codes with RH 2000 heights follow the
# same order: 5845 is SWEREF 99 TM + RH 2000 (3006) and 5846 to 5857 the local zones + RH 2000 (3007 to 3018).
CS_SWEREF_ZONES = (
    "12 00",
    "13 30",
    "15 00",
    "16 30",
    "18 00",
    "14 15",
    "15 45",
    "17 15",
    "18 45",
    "20 15",
    "21 45",
    "23 15",
)
CS_COMPOUND_RH2000 = (5845, 5857)
CS_RH2000 = "RH 2000"


def _cs_crs_plan(code):
    """The projected code of a compound code with RH 2000 (5845 to 5857 give 3006 to 3018), any other code as it is."""
    return code - 2839 if CS_COMPOUND_RH2000[0] <= code <= CS_COMPOUND_RH2000[1] else code


def _cs_crs_height(code):
    """The height system a compound EPSG code gives (RH 2000 for 5845 to 5857), None for a projected code without heights."""
    return CS_RH2000 if CS_COMPOUND_RH2000[0] <= code <= CS_COMPOUND_RH2000[1] else None


def _cs_crs_vertical(code, given=None):
    """IfcProjectedCRS.VerticalDatum: None when the code gives the height system itself (the compound codes), else the one given,
    else RH 2000 for SWEREF 99 (3006 to 3018), Sweden's height system, else None."""
    if _cs_crs_height(code):
        return None
    if given and str(given).strip():
        return str(given).strip()[:255]
    return CS_RH2000 if 3006 <= code <= 3018 else None


def _cs_crs_description(code, vertical=None):
    """IfcProjectedCRS.Description in plain words, such as 'SWEREF 99 18 00 + RH 2000' or 'ETRS89 / UTM zone 32N', with the height
    system of the code or the vertical datum; None for a code outside the table of _cs_crs_facts."""
    datum, projection, zone = _cs_crs_facts("EPSG:%d" % _cs_crs_plan(code))
    if datum is None:
        return None
    if datum == "SWEREF99":
        text = "SWEREF 99 %s" % zone
    else:
        text = "%s / %s" % (
            {"WGS84": "WGS 84"}.get(datum, datum),
            zone.replace("UTM ", "UTM zone ") if zone else projection,
        )
    height = _cs_crs_height(code) or vertical
    return "%s + %s" % (text, height) if height else text


def _cs_crs_facts(crs):
    """The datum, projection and zone an EPSG code stands for, for the projected systems the Nordic countries draw in (and the
    compound codes of SWEREF 99 with RH 2000, whose plan is the projected code's); (None, None, None) for a code outside the table.
    IFC4 requires the datum on IfcProjectedCRS, IFC4X3 does not."""
    code = _cs_crs_plan(int(crs[5:]))
    if code == 3006:
        return "SWEREF99", "Transverse Mercator", "TM"
    if 3007 <= code <= 3018:
        return "SWEREF99", "Transverse Mercator", CS_SWEREF_ZONES[code - 3007]
    if 25828 <= code <= 25838:
        return "ETRS89", "Transverse Mercator", "UTM %dN" % (code - 25800)
    if code == 3067:
        return "ETRS89", "Transverse Mercator", "TM35FIN"
    if 3873 <= code <= 3885:
        return "ETRS89", "Transverse Mercator", "GK%dFIN" % (code - 3854)
    if 5105 <= code <= 5130:
        return "ETRS89", "Transverse Mercator", "NTM zone %d" % (code - 5100)
    if code == 3057:
        return "ISN93", "Lambert Conformal Conic", "Lambert 1993"
    if code == 8088:
        return "ISN2016", "Lambert Conformal Conic", "Lambert 2016"
    if 32601 <= code <= 32660:
        return "WGS84", "Transverse Mercator", "UTM %dN" % (code - 32600)
    if 32701 <= code <= 32760:
        return "WGS84", "Transverse Mercator", "UTM %dS" % (code - 32700)
    if code == 3857:
        return "WGS84", "Pseudo-Mercator", None
    return None, None, None


# Placements as matrices [X, Y, Z, origin] (the axes as columns, in file units), for reading where the walls stand.
def _cs_unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v] if n > 1e-12 else None


def _cs_dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def _cs_turn(m, v):
    """The direction v of the matrix m's coordinates in its parent's."""
    return [m[0][i] * v[0] + m[1][i] * v[1] + m[2][i] * v[2] for i in range(3)]


def _cs_at(m, p):
    """The point p of the matrix m's coordinates in its parent's."""
    return [a + b for a, b in zip(_cs_turn(m, p), m[3])]


def _cs_compose(a, b):
    return [_cs_turn(a, b[0]), _cs_turn(a, b[1]), _cs_turn(a, b[2]), _cs_at(a, b[3])]


def _cs_invert(m):
    """The inverse of a rigid placement: the rotation transposed and the origin turned back."""
    x, y, z, o = m
    return [
        [x[0], y[0], z[0]],
        [x[1], y[1], z[1]],
        [x[2], y[2], z[2]],
        [-_cs_dot(x, o), -_cs_dot(y, o), -_cs_dot(z, o)],
    ]


def _cs_axes(position):
    """An IfcAxis2Placement3D or 2D as a matrix; the identity for none."""
    if position is None:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]
    origin = ([float(c) for c in position.Location.Coordinates] + [0.0, 0.0])[:3]
    if position.is_a("IfcAxis2Placement2D"):
        z = [0.0, 0.0, 1.0]
        ref = (
            ([float(c) for c in position.RefDirection.DirectionRatios] + [0.0])[:3]
            if position.RefDirection is not None
            else [1.0, 0.0, 0.0]
        )
    else:
        z = (
            _cs_unit([float(c) for c in position.Axis.DirectionRatios])
            if position.Axis is not None
            else [0.0, 0.0, 1.0]
        )
        ref = (
            [float(c) for c in position.RefDirection.DirectionRatios]
            if position.RefDirection is not None
            else ([0.0, 1.0, 0.0] if abs(z[0]) > 0.9 else [1.0, 0.0, 0.0])
        )
    x = _cs_unit([a - _cs_dot(ref, z) * b for a, b in zip(ref, z)])
    return [x, [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]], z, origin]


def _cs_placement(placement, depth=0):
    """The absolute matrix of an object placement (a chain of IfcLocalPlacement); None for one the app cannot follow."""
    if placement is None:
        return _cs_axes(None)
    if depth > 64 or not placement.is_a("IfcLocalPlacement"):
        return None
    parent = _cs_placement(placement.PlacementRelTo, depth + 1)
    return None if parent is None else _cs_compose(parent, _cs_axes(placement.RelativePlacement))


def _cs_profile_points(profile):
    """The outer corners of a profile in its plane: rectangles and polyline outlines; [] for curves the app does not read."""
    if profile.is_a("IfcRectangleProfileDef"):
        hx, hy = float(profile.XDim) / 2.0, float(profile.YDim) / 2.0
        m = _cs_axes(profile.Position)
        return [_cs_at(m, (x, y, 0.0))[:2] for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]
    curve = profile.OuterCurve if profile.is_a("IfcArbitraryClosedProfileDef") else None
    if curve is not None and curve.is_a("IfcPolyline"):
        return [[float(c) for c in p.Coordinates][:2] for p in curve.Points]
    if curve is not None and curve.is_a("IfcIndexedPolyCurve"):
        return [[float(c) for c in p][:2] for p in curve.Points.CoordList]
    return []


def _cs_item_points(item, depth=0):
    """The corners of a body item in its own coordinates: extruded rectangles and polylines, Boolean results by their first operand,
    faceted and tessellated solids; [] for what the app does not read."""
    if item is None or depth > 16:
        return []
    if item.is_a("IfcBooleanResult"):
        return _cs_item_points(item.FirstOperand, depth + 1)
    if item.is_a("IfcExtrudedAreaSolid"):
        d = _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
        m = _cs_axes(item.Position)
        return [
            _cs_at(m, (x + d[0] * k, y + d[1] * k, d[2] * k))
            for x, y in _cs_profile_points(item.SweptArea)
            for k in (0.0, float(item.Depth))
        ]
    if item.is_a("IfcFacetedBrep"):
        return [
            [float(c) for c in p.Coordinates]
            for f in item.Outer.CfsFaces
            for b in f.Bounds
            if b.Bound.is_a("IfcPolyLoop")
            for p in b.Bound.Polygon
        ]
    if item.is_a("IfcTessellatedFaceSet"):
        return [[float(c) for c in p] for p in item.Coordinates.CoordList]
    return []


def _cs_rectangle(points):
    """Whether (x, y) points are the four corners of their bounding box (a closing corner may repeat the first)."""
    pts = list(points[:-1]) if len(points) > 4 and tuple(points[0]) == tuple(points[-1]) else list(points)
    if len(pts) != 4:
        return False
    x0, y0, x1, y1 = min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)
    corners = set()
    for x, y in pts:
        cx = 0 if abs(x - x0) < 1e-3 else 1 if abs(x - x1) < 1e-3 else None
        cy = 0 if abs(y - y0) < 1e-3 else 1 if abs(y - y1) < 1e-3 else None
        if cx is None or cy is None:
            return False
        corners.add((cx, cy))
    return len(corners) == 4


def _cs_roof_faces(shape, length, width, overhang, ridge):
    """The faces of a roof over the rectangle [0, length] x [0, width] in metres: (fall, corners), fall the horizontal direction the
    face falls toward and corners its outline in plan, the eaves first and then the upper corners back, with the overhang beyond the
    rectangle on every side. A shed falls toward negative y (ridge x: negative x); a gable's ridge runs along the longer side unless
    ridge says x or y; a hip roof's hips run at 45 degrees in plan, so its ridge lies along the longer side (a point over a square)."""
    a, b, o = float(length), float(width), float(overhang)
    sw, se, ne, nw = (-o, -o), (a + o, -o), (a + o, b + o), (-o, b + o)
    south, north, west, east = (0.0, -1.0), (0.0, 1.0), (-1.0, 0.0), (1.0, 0.0)
    if shape == "flat" or (shape == "shed" and ridge != "x"):
        return [(south, [sw, se, ne, nw])]
    if shape == "shed":
        return [(west, [nw, sw, se, ne])]
    if shape == "gable":
        if ridge == "x" or (ridge != "y" and a >= b):
            return [
                (south, [sw, se, (a + o, b / 2.0), (-o, b / 2.0)]),
                (north, [ne, nw, (-o, b / 2.0), (a + o, b / 2.0)]),
            ]
        return [(west, [nw, sw, (a / 2.0, -o), (a / 2.0, b + o)]), (east, [se, ne, (a / 2.0, b + o), (a / 2.0, -o)])]
    if a >= b:
        r1, r2 = (b / 2.0, b / 2.0), (a - b / 2.0, b / 2.0)
        faces = [(south, [sw, se, r2, r1]), (north, [ne, nw, r1, r2]), (west, [nw, sw, r1]), (east, [se, ne, r2])]
    else:
        r1, r2 = (a / 2.0, a / 2.0), (a / 2.0, b - a / 2.0)
        faces = [(west, [nw, sw, r1, r2]), (east, [se, ne, r2, r1]), (south, [sw, se, r1]), (north, [ne, nw, r2])]
    return [
        (fall, [p for i, p in enumerate(c) if i == 0 or abs(p[0] - c[i - 1][0]) + abs(p[1] - c[i - 1][1]) > 1e-6])
        for fall, c in faces
    ]


def _cs_type_values(model, ifc_class, kind):
    """The PredefinedTypes define_type gives a type class in the file's schema: the class's enumeration without USERDEFINED, which
    needs an ElementType, and without the values IFC4 and later deprecate (STANDARD and POLYGONAL for walls, rule IFC102); a roof
    type is ROOF."""
    if kind == "roof":
        return ["ROOF"]
    import ifcopenshell
    import ifcopenshell.util.attribute

    schema = ifcopenshell.schema_by_name(getattr(model, "schema_identifier", None) or model.schema)
    attr = next(a for a in schema.declaration_by_name(ifc_class).all_attributes() if a.name() == "PredefinedType")  # ty: ignore[unresolved-attribute]
    deprecated = ("STANDARD", "POLYGONAL") if ifc_class == "IfcWallType" and model.schema != "IFC2X3" else ()
    return [v for v in ifcopenshell.util.attribute.get_enum_items(attr) if v != "USERDEFINED" and v not in deprecated]


_QTO_NAMES = {}


def _cs_qto_names(schema, name):
    """The quantities of IfcOpenShell's template of the quantity set `name` in a schema, by their names in lower case to their
    spelling there; None when the schema has no template of it (IFC2X3)."""
    key = (str(schema), str(name))
    if key not in _QTO_NAMES:
        try:
            import ifcopenshell.util.pset

            template = ifcopenshell.util.pset.get_template(str(schema)).get_by_name(str(name))
        except Exception:
            template = None
        _QTO_NAMES[key] = (
            None
            if template is None
            else {str(q.Name).lower(): str(q.Name) for q in (template.HasPropertyTemplates or ())}
        )
    return _QTO_NAMES[key]


_PSET_TEMPLATES = {}
# The units a value might carry after its number ("40 dB"), in lower case without spaces. A value never carries its unit: IFC gives
# a property its unit (the Praxi study, recommendation 8; the native IFC study, recommendation 13).
_CS_UNITS = frozenset(
    (
        "mm",
        "cm",
        "dm",
        "m",
        "km",
        "mm2",
        "cm2",
        "dm2",
        "m2",
        "km2",
        "mm²",
        "cm²",
        "dm²",
        "m²",
        "km²",
        "mm3",
        "cm3",
        "dm3",
        "m3",
        "mm³",
        "cm³",
        "dm³",
        "m³",
        "l",
        "liter",
        "litre",
        "g",
        "kg",
        "t",
        "ton",
        "n",
        "kn",
        "pa",
        "kpa",
        "mpa",
        "gpa",
        "db",
        "dba",
        "db(a)",
        "w",
        "kw",
        "mw",
        "wh",
        "kwh",
        "mwh",
        "w/m2k",
        "w/m²k",
        "w/(m2k)",
        "w/(m²k)",
        "w/m2,k",
        "w/m²,k",
        "w/(m2·k)",
        "w/(m²·k)",
        "w/m²·k",
        "w/mk",
        "w/(mk)",
        "w/m·k",
        "w/(m·k)",
        "m2k/w",
        "m²k/w",
        "°c",
        "°",
        "deg",
        "k",
        "%",
        "h",
        "min",
        "s",
        "l/s",
        "m3/h",
        "m³/h",
        "m3/s",
        "m³/s",
        "kg/m3",
        "kg/m³",
        "kg/m2",
        "kg/m²",
        "kg/m",
        "lux",
        "lx",
        "st",
        "pcs",
        "kr",
        "sek",
        "nok",
        "dkk",
        "isk",
        "eur",
        "kgco2e",
        "kgco2-ekv",
    )
)


def _cs_pset_templates(schema):
    """IfcOpenShell's property and quantity set templates of a schema (as in file.schema_identifier) by their names in lower case;
    empty when it has none."""
    key = str(schema)
    if key not in _PSET_TEMPLATES:
        try:
            import ifcopenshell.util.pset

            files = ifcopenshell.util.pset.get_template(key).templates
            _PSET_TEMPLATES[key] = {
                str(t.Name).lower(): t for f in files for t in f.by_type("IfcPropertySetTemplate") if t.Name
            }
        except Exception:
            _PSET_TEMPLATES[key] = {}
    return _PSET_TEMPLATES[key]


def _cs_unit_in(value):
    """(number, unit) of a text that is a number with one of _CS_UNITS written after it ("40 dB" gives ("40", "dB")); None otherwise."""
    if not isinstance(value, str):
        return None
    import re

    m = re.match(r"^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s.,+-].*?)\s*$", value)
    if not m or m.group(2).replace(" ", "").lower() not in _CS_UNITS:
        return None
    return m.group(1), m.group(2)


class _ChangeSet:
    """Applies a change set to an IfcOpenShell model and reports what it created, changed and deleted.

    The operations are the form "dataspatium-tools/1", which Dataregium takes in a change proposal as well; new operations and fields
    are added, never changed. Since recommendations 3, 5 and 6 of the native IFC study (2026-11-12):

        define_type {kind: wall | slab | roof, name, layers: [{material, thickness, category, role}], predefined_type}
            a type with a ref: IfcWallType, IfcSlabType, or IfcSlabType ROOF for the roof slabs of create_roof, with an
            IfcMaterialLayerSet of the layers (metres; a wall's from its right side to its left seen from start to end, a slab's and
            a roof's from the top down); category (IfcMaterial.Category: concrete, steel, aluminium, block, brick, stone, wood,
            glass, gypsum, plastic, earth) and role (IfcMaterialLayer.Category: load_bearing, insulation, inner_finish,
            outer_finish) are optional and null when not known; predefined_type null takes NOTDEFINED, FLOOR and ROOF.
        create_wall, create_slab, create_roof {..., type}
            type (optional) is the ref of a define_type step or a type's id or GlobalId; the element takes its thickness from the
            layers (a thickness given must equal them), gets IfcRelDefinesByType and an IfcMaterialLayerSetUsage, and has no
            PredefinedType of its own (op_define_type and give_type).

    At the end of apply the walls the change set created are joined where their axes meet (join_walls), and what it created or
    changed gets its base quantities (base_quantities).

    Since recommendation 13 of the native IFC study and 4, 8 and 9 of the Praxi study (2026-11-12):

        create_wall, create_slab {..., load_bearing, status}, create_roof {..., status}
            load_bearing (optional) true or false writes Pset_WallCommon.LoadBearing or Pset_SlabCommon.LoadBearing, and status
            (optional: NEW, EXISTING, DEMOLISH or TEMPORARY) Status in the common set (the roof's slabs and the IfcRoof too), on the
            type when every occurrence of it says the same, else on the element (write_marked); null or missing writes nothing.
        assign {..., category}
            category (optional) is the material's IfcMaterial.Category, one of MATERIAL_CATEGORIES, with material.
        set_properties
            a set named Pset_... or Qto_... is checked against IfcOpenShell's template of the file's schema: unknown sets and
            properties, values of the wrong type and numbers with their unit written after them are refused (op_set_properties).

    At the end of apply IsExternal follows the topology on the walls the change set created or changed, on their doors and windows,
    and on its roofs and roof slabs (write_external).

    Since PLAN.md item 5 (2026-11-13), the space boundaries are first level with their faces: create_spaces bounds each space by the
    faces of its walls and of the doors, windows and openings in them, and at the end of apply the spaces near what the change set
    created or changed (a wall made after the rooms, a slab below or above, a door, a moved wall) get theirs anew (space_boundaries).

    Since recommendations 2 and 3 of the Praxi study and 14 of the native IFC study (2026-11-12), all optional:

        create_storeys_and_grid {storeys: [{..., description, long_name}]}
            a storey's Description (such as "Plan 2 (Entré)") and LongName; given for a storey that exists, they are set on it.
        create_spaces {rooms: [{..., number, room_type}]}
            with number the space's Name is the number (unique among the model's spaces), its LongName the name and its
            Description the long_name; without one the name is the Name and long_name the LongName, as before. room_type is the
            room's type: ObjectType with PredefinedType USERDEFINED (IFC2X3: ObjectType alone).
        set_site {..., vertical_datum}
            the height system of the elevation for IfcProjectedCRS.VerticalDatum, when the EPSG code does not give it; null takes
            RH 2000 for SWEREF 99 (3006 to 3018). The compound codes 5845 to 5857 (SWEREF 99 TM and the local zones with RH 2000)
            are known; the CRS gets a Description in plain words and the metre as MapUnit, every parent representation context its
            own IfcMapConversion with the same values and the file's unit as Scale, and IFC4 and IFC2X3 buildings the height in
            ElevationOfRefHeight (map_conversion, building_height).

    Since recommendation 8 of the study of the IFC and BCF guidelines (2026-11-12), one new operation:

        set_responsible_party {organization, role, user_role, designation, contact_role, contact_person}
            the organisation that answers for the model file (organization, required) with its role (role: a value of IfcRoleEnum,
            COMMISSIONINGENGINEER spelled as IFC2X3 spells it there; USERDEFINED with user_role, the role's name; null for none),
            written natively: an IfcActor of the IfcOrganization, the role an IfcActorRole among its Roles, assigned to the
            IfcProject by an IfcRelAssignsToActor with that ActingRole. designation (the actor's Name: the short designation the
            project gives the party) and contact_role (its Description: whom to contact there, as a role) are optional;
            contact_person (optional, personal data the person enters) makes the actor an IfcPersonAndOrganization: a name sets it,
            "" removes it, and null or missing keeps the one the party has as long as the organisation stays. A second call
            changes the party in place and keeps its GlobalIds (op_set_responsible_party).

    Since recommendation 7 of the Praxi study and 17 of the native IFC study (2026-11-12), elements are changed in place so that their
    GlobalId stays for their whole life:

        edit_element {id, name, start, end, thickness, height, type, outline, offset, width, sill, pitch, overhang, base}
            changes an existing element (id: its id, GlobalId or a ref) and keeps its GlobalId; a field left out or null keeps its
            value. A wall of create_wall's form takes start and end ([x, y] metres in its storey's plane), thickness, height and type;
            a slab of create_slab's form outline, thickness and type, its top where it was; an opening of create_opening's form, or
            the door or window in it, offset, width, height and sill, the two together; a roof of create_roof's form pitch, overhang
            and base, its slabs following; any element name. The parameters are read from the element's representation (a wall's
            start and end from its Axis), never from a store of their own. The body, the Axis, the layer usage, the base quantities
            and the wall joins are made anew (op_edit_element); an element of another form, or a field its kind does not take, is an
            error that says what to do instead.
        transform_elements {ids, dx, dy, dz}
            the displacement is in the storey's plane; a door or window moves with its opening (IfcRelFillsElement,
            IfcRelVoidsElement), the site, the building and the storeys are refused, a point is moved in place or replaced so that
            none is left unused (rule IFC106), and a moved wall is joined anew (op_transform_elements).

    Since recommendations 4 and 11 of the native IFC study (2026-11-12), with new optional fields and one new operation:

        define_type {kind: ... | covering | column | beam | footing | railing | door | window, ..., material, material_category,
                     operation, partitioning, width, height, lining_depth}
            covering is layered as a slab is (IfcCoveringType); column, beam, footing and railing (IfcColumnType, IfcBeamType,
            IfcFootingType, IfcRailingType) take layers null and an optional material; door and window (IfcDoorType and IfcWindowType,
            IfcDoorStyle and IfcWindowStyle in IFC2X3) take operation (OperationType, DOOR_OPERATIONS) or partitioning
            (PartitioningType, WINDOW_PARTITIONS), width and height (metres) and lining_depth (metres), and get IfcOpenShell's door or
            window geometry as the type's representation map and their lining (and a door's panel) properties (define_element_type).
        create_element {storey, ifc_class, predefined_type, object_type, type, name, at: [x, y], z, rotation,
                        shape: {profile: rectangle | circle | polygon, x, y, radius, outline, depth, along: z | x}, stair, part_of, host}
            an element of ELEMENT_CLASSES with a ref: a SweptSolid contained in the storey, or aggregated in part_of (and on its
            storey) and never both; host is what an IfcCovering covers; IfcStair takes stair: {risers, riser_height, tread_length,
            width, thickness, landing} instead of shape and aggregates a sloped IfcStairFlight STRAIGHT with Pset_StairFlightCommon
            and, with a landing, an IfcSlab LANDING (op_create_element, create_stair).
        create_opening {..., type, side}
            type (optional) is a door or window type; the filling takes its size and its mapped geometry, stands flush with the
            wall's left face (side right: the right face) and a door opens toward that side (place_typed_fill).

    Since recommendation 4 of the native IFC study, glass façades (2026-11-13), with one new class and new optional fields:

        define_type {kind: ... | curtain_wall, ..., material, material_category, panel_material, member_width, member_depth,
                     panel_thickness}
            curtain_wall makes an IfcCurtainWallType (NOTDEFINED) with a ref, and its parts' types of the same name: an IfcPlateType
            CURTAIN_PANEL with one material layer of panel_material (null: Glass, category glass) panel_thickness thick, and an
            IfcMemberType MULLION, for mullions and transoms alike (IfcMemberTypeEnum has no TRANSOM), of material (null: Aluminium,
            category aluminium; material_category for another) with a member_width by member_depth rectangle as its material
            profile (IFC2X3: the material alone); metres, null: 0.05, 0.15 and 0.03 (curtain_types).
        create_element {ifc_class: IfcCurtainWall, ..., curtain: {start: [x, y], end: [x, y], height, base, bays, bay_width, rows,
                        row_height}, part_names: {panel, mullion, transom}}
            a curtain wall with a ref along its axis from start to end in the storey's plane, height (null: 3) from base (null: 0)
            metres above the storey, with an 'Axis' and no body of its own, aggregating its parts on an even grid: bays (a count) or
            bay_width (the most a bay is wide; null and null: 1.5 m), rows or row_height likewise; at, shape, stair, part_of and
            host are null. Its mullions, transoms (IfcMember MULLION) and panels (IfcPlate CURTAIN_PANEL) get no refs and are named
            with part_names (optional, the person's words; null: Panel, Mullion, Transom). A curtain wall type types them and gives
            their sizes and materials (create_curtain_wall). It gets Qto_CurtainWallQuantities, and IsExternal in
            Pset_CurtainWallCommon from the topology, which reads it as a wall.
        delete_elements
            a whole goes with its parts (a curtain wall's, a stair's, a roof's), so that none is left in no storey and no whole.
    """

    CREATES = (
        "create_wall",
        "create_slab",
        "create_roof",
        "create_opening",
        "create_spaces",
        "define_type",
        "create_element",
    )
    ROOF_TYPES = {"gable": "GABLE_ROOF", "hip": "HIP_ROOF", "shed": "SHED_ROOF", "flat": "FLAT_ROOF"}

    def __init__(self, model):
        import ifcopenshell.util.unit

        self.m = model
        try:
            self.scale = float(ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0)  # metres per file unit
        except Exception:
            self.scale = 1.0
        self.refs = {}
        self.created = []
        self.changed = {}
        self.deleted = []
        self.notes = []  # what an operation could not do in the file's schema, for the report
        self.n_created = 0
        owners = model.by_type("IfcOwnerHistory")
        self.owner = owners[0] if owners else None
        self._body = None
        self._axis = None
        self._schema = None
        self.marks = {"LoadBearing": {}, "Status": {}}  # element id -> what its create step gave (write_marked)
        self.written = set()  # (element id, set, property) that set_properties wrote, which write_external may replace
        self.rejoin = set()  # ids of walls whose joins edit_element or transform_elements undid, for join_walls
        self.moved = {}  # wall id -> (axis, thickness) before the change set first moved or resized it, for fit_spaces
        self.slid = set()  # ids of doors and windows that moved along a wall that stayed, for fit_spaces
        self.fill_sizes = {}  # (width, height) in metres of the door and window types this change set defines, by STEP id
        self.unbounded = (
            set()
        )  # ids of spaces a deleted element bounded, whose other faces space_boundaries writes anew

    # -- units, lookups and bookkeeping
    def u(self, metres):
        return float(metres) / self.scale

    def entity(self, ref):
        """An element by its STEP id, its GlobalId (22 characters: a proposal names elements so, decision F34 a at Dataregium), or a
        ref of this change set."""
        if isinstance(ref, str) and ref in self.refs:
            return self.refs[ref]
        if isinstance(ref, str) and len(ref) == 22 and not ref.isdigit():
            try:
                e = self.m.by_guid(ref)
            except Exception:
                e = None
            if e is None:
                raise DataspatiumError("no element with GlobalId %r" % (ref,))
            return e
        try:
            e = self.m.by_id(int(ref))
        except Exception:
            raise DataspatiumError("no element with id %r" % (ref,))
        if e is None:
            raise DataspatiumError("no element with id %r" % (ref,))
        return e

    def elements(self, op):
        ids = op.get("ids")
        if not isinstance(ids, (list, tuple)) or not ids:
            raise DataspatiumError("ids must be a list of element ids or refs")
        return [self.entity(x) for x in ids]

    def storey(self, name, top=False):
        """A storey by its name; without one the lowest, or with top the highest."""
        storeys = self.m.by_type("IfcBuildingStorey")
        if not storeys:
            raise DataspatiumError("the model has no storey; create one with create_storeys_and_grid first")
        if name is None or str(name).strip() == "":
            ordered = sorted(
                storeys, key=lambda s: _topo_elevation(s, self.scale)
            )  # IFC4.3's storeys have their height in the placement
            return ordered[-1] if top else ordered[0]
        for exact in (True, False):
            for s in storeys:
                a, b = str(s.Name or ""), str(name)
                if (a == b) if exact else (a.lower() == b.lower()):
                    return s
        raise DataspatiumError(
            "no storey named %r; the storeys are %s" % (name, ", ".join(str(s.Name) for s in storeys))
        )

    def container(self, e):
        import ifcopenshell.util.element as el

        try:
            return el.get_container(e)
        except Exception:
            return None

    def record(self, e, storey=None):
        return {
            "id": e.id(),
            "guid": getattr(e, "GlobalId", None),
            "ifc_class": e.is_a(),
            "name": _ifc_text(getattr(e, "Name", None)),
            "storey": _ifc_text(getattr(storey, "Name", None)) if storey is not None else None,
        }

    def register(self, e, storey=None, ref=True):
        rec = self.record(e, storey)
        if ref:
            self.n_created += 1
            rec["ref"] = "new-%d" % self.n_created
            self.refs[rec["ref"]] = e
        self.created.append(rec)
        return rec

    def touch(self, e):
        if any(c["id"] == e.id() for c in self.created):
            return
        self.changed[e.id()] = self.record(e, self.container(e))

    # -- the right class (decision B-57.2): every class and enumeration value an operation sets is checked against the schema
    def schema_def(self):
        if self._schema is None:
            self._schema = _ifc_schema(getattr(self.m, "schema_identifier", None) or self.m.schema)
        return self._schema

    def check_class(self, ifc_class):
        """The entity's declaration in the file's schema; a DataspatiumError, with the classes to use instead, when it does not exist
        there or is abstract."""
        schema = self.schema_def()
        decl = _ifc_declaration(schema, ifc_class)
        if decl is not None and decl.as_entity() is not None and not decl.is_abstract():
            return decl
        if decl is not None and decl.as_entity() is not None:
            raise DataspatiumError(
                "%s is abstract in %s; use one of %s" % (ifc_class, schema.name(), ", ".join(_ifc_concrete(decl)[:30]))
            )
        import difflib

        near = difflib.get_close_matches(
            str(ifc_class),
            [d.name() for d in schema.declarations() if d.as_entity() is not None and not d.is_abstract()],
            8,
            0.6,
        )
        raise DataspatiumError(
            "%s is not an entity of %s%s" % (ifc_class, schema.name(), "; near: " + ", ".join(near) if near else "")
        )

    def check_enum(self, e, value, attribute="PredefinedType"):
        """Whether the schema gives the element the enumeration `attribute`; a DataspatiumError that lists the allowed values for one
        it does not take, and for USERDEFINED without the name of the type (ObjectType on an occurrence, ElementType on a type)."""
        items = _ifc_enum_items(self.check_class(e.is_a()), attribute)
        if items is None:
            return False
        gone = DEPRECATED_VALUES.get(self.m.schema, {}).get(e.is_a(), ()) if attribute == "PredefinedType" else ()
        allowed = [i for i in items if i not in gone]
        if value in gone:
            raise DataspatiumError(
                "%s is deprecated for %s in %s (bSI rule IFC102); use one of %s"
                % (value, e.is_a(), self.schema_def().name(), ", ".join(allowed))
            )
        if value not in items:
            raise DataspatiumError(
                "%s is not a %s of %s in %s; it takes %s"
                % (value, attribute, e.is_a(), self.schema_def().name(), ", ".join(allowed))
            )
        if value == "USERDEFINED" and attribute == "PredefinedType":
            named = "ElementType" if e.is_a("IfcTypeObject") else "ObjectType"
            if not str(getattr(e, named, None) or "").strip():
                raise DataspatiumError(
                    "%s USERDEFINED needs %s, the name of the user-defined type; or use one of %s"
                    % (e.is_a(), named, ", ".join(i for i in allowed if i not in ("USERDEFINED", "NOTDEFINED")))
                )
        return True

    def predefined(self, e, value):
        """Set the element's PredefinedType after check_enum; an entity whose schema has none (walls, doors and windows in IFC2X3)
        takes nothing."""
        if self.check_enum(e, value):
            e.PredefinedType = value

    # -- the file's structure
    def root(self, ifc_class, name):
        import ifcopenshell.guid

        self.check_class(ifc_class)
        e = self.m.create_entity(ifc_class, GlobalId=ifcopenshell.guid.new(), Name=str(name))
        if self.owner is not None:
            e.OwnerHistory = self.owner
        return e

    def owner_settings(self):
        """IFC2X3 needs an owner history on every IfcRoot, also on the relationships the API makes: lend it the file's."""
        import ifcopenshell.api.owner.settings as settings

        if self.m.schema != "IFC2X3" or self.owner is None:
            return None
        previous = (settings.get_user, settings.get_application)
        owner = self.owner
        settings.get_user = lambda _file: owner.OwningUser  # ty: ignore[invalid-assignment]
        settings.get_application = lambda _file: owner.OwningApplication  # ty: ignore[invalid-assignment]
        return previous

    @staticmethod
    def restore_owner_settings(previous):
        if previous is None:
            return
        import ifcopenshell.api.owner.settings as settings

        settings.get_user, settings.get_application = previous

    def building(self):
        import ifcopenshell.api.aggregate

        buildings = self.m.by_type("IfcBuilding")
        if buildings:
            return buildings[0]
        site = self.site()
        building = self.root("IfcBuilding", "Building")
        building.CompositionType = "ELEMENT"
        building.ObjectPlacement = self.placement(site.ObjectPlacement, 0, 0, 0)
        ifcopenshell.api.aggregate.assign_object(self.m, products=[building], relating_object=site)
        self.register(building, ref=False)
        return building

    def site(self, name=None):
        """The file's site: the first IfcSite, or a new one under the project, with the buildings that hung on the project moved under it."""
        import ifcopenshell.api.aggregate

        sites = self.m.by_type("IfcSite")
        if sites:
            return sites[0]
        projects = self.m.by_type("IfcProject")
        if not projects:
            raise DataspatiumError("the model has no IfcProject")
        site = self.root("IfcSite", name or "Site")
        site.CompositionType = "ELEMENT"
        site.ObjectPlacement = self.placement(None, 0, 0, 0)
        ifcopenshell.api.aggregate.assign_object(self.m, products=[site], relating_object=projects[0])
        for building in self.m.by_type("IfcBuilding"):
            if any(rel.RelatingObject.id() == projects[0].id() for rel in (building.Decomposes or ())):
                ifcopenshell.api.aggregate.assign_object(self.m, products=[building], relating_object=site)
                self.touch(building)
        self.register(site, ref=False)
        return site

    def model_context(self):
        """The 3D model context (ContextType Model, not a sub-context), made when the file has none."""
        parents = [
            c
            for c in self.m.by_type("IfcGeometricRepresentationContext")
            if not c.is_a("IfcGeometricRepresentationSubContext") and (c.ContextType or "Model") == "Model"
        ]
        if parents:
            return parents[0]
        import ifcopenshell.api.context

        return ifcopenshell.api.context.add_context(self.m, context_type="Model")

    def length_unit(self):
        """The project's length unit (an IfcNamedUnit), or None."""
        for project in self.m.by_type("IfcProject"):
            units = project.UnitsInContext
            for unit in (units.Units or ()) if units is not None else ():
                if unit.is_a("IfcNamedUnit") and getattr(unit, "UnitType", None) == "LENGTHUNIT":
                    return unit
        return None

    def body(self):
        if self._body is not None:
            return self._body
        subs = [
            c for c in self.m.by_type("IfcGeometricRepresentationSubContext") if (c.ContextIdentifier or "") == "Body"
        ]
        if subs:
            self._body = subs[0]
            return subs[0]
        import ifcopenshell.api.context

        self._body = ifcopenshell.api.context.add_context(
            self.m,
            context_type="Model",
            context_identifier="Body",
            target_view="MODEL_VIEW",
            parent=self.model_context(),
        )
        return self._body

    def axis_context(self):
        """The sub-context of the walls' 'Axis' representations (Model/Axis/GRAPH_VIEW), made when the file has none."""
        if self._axis is not None:
            return self._axis
        subs = [
            c for c in self.m.by_type("IfcGeometricRepresentationSubContext") if (c.ContextIdentifier or "") == "Axis"
        ]
        if subs:
            self._axis = subs[0]
            return subs[0]
        import ifcopenshell.api.context

        self._axis = ifcopenshell.api.context.add_context(
            self.m,
            context_type="Model",
            context_identifier="Axis",
            target_view="GRAPH_VIEW",
            parent=self.model_context(),
        )
        return self._axis

    def contain(self, e, storey):
        import ifcopenshell.api.spatial

        ifcopenshell.api.spatial.assign_container(self.m, products=[e], relating_structure=storey)

    # -- geometry, in file units
    def point(self, x, y, z=None):
        return self.m.createIfcCartesianPoint([float(x), float(y)] if z is None else [float(x), float(y), float(z)])

    def direction(self, x, y, z=None):
        return self.m.createIfcDirection([float(x), float(y)] if z is None else [float(x), float(y), float(z)])

    def axis3(self, x, y, z, dx=1.0, dy=0.0):
        return self.m.createIfcAxis2Placement3D(self.point(x, y, z), self.direction(0, 0, 1), self.direction(dx, dy, 0))

    def placement(self, parent, x, y, z, dx=1.0, dy=0.0):
        return self.m.createIfcLocalPlacement(parent, self.axis3(x, y, z, dx, dy))

    def shape(self, solid):
        rep = self.m.createIfcShapeRepresentation(self.body(), "Body", "SweptSolid", [solid])
        return self.m.createIfcProductDefinitionShape(None, None, [rep])

    def box(self, length, width, height, x0=0.0, y0=0.0, z0=0.0):
        """A rectangle of length (X) by width (Y) with its corner at (x0, y0), extruded height along Z from z0."""
        position = self.m.createIfcAxis2Placement2D(self.point(x0 + length / 2.0, y0 + width / 2.0), None)
        profile = self.m.createIfcRectangleProfileDef("AREA", None, position, float(length), float(width))
        return self.shape(
            self.m.createIfcExtrudedAreaSolid(profile, self.axis3(0, 0, z0), self.direction(0, 0, 1), float(height))
        )

    def closed(self, outline):
        """A closed IfcPolyline through (x, y) corners: a repeated first corner at the end is dropped, and the line closes on the first
        corner's own IfcCartesianPoint, not a copy of it (bSI rule GEM111)."""
        corners = [tuple(float(v) for v in p) for p in outline]
        while len(corners) > 1 and corners[-1] == corners[0]:
            corners.pop()
        if len(corners) < 3:
            raise DataspatiumError("an outline needs at least three different corners")
        pts = [self.point(x, y) for x, y in corners]
        return self.m.createIfcPolyline(pts + pts[:1])

    def prism(self, outline, height, z0=0.0):
        """A closed outline of (x, y) points extruded height along Z from z0."""
        profile = self.m.createIfcArbitraryClosedProfileDef("AREA", None, self.closed(outline))
        return self.shape(
            self.m.createIfcExtrudedAreaSolid(profile, self.axis3(0, 0, z0), self.direction(0, 0, 1), float(height))
        )

    def wall_thickness(self, wall):
        """The YDim of the wall's rectangle profile, in file units; 0.2 m when the wall has another shape."""
        rep = getattr(wall, "Representation", None)
        for r in rep.Representations if rep is not None else []:
            for item in r.Items or []:
                if item.is_a("IfcExtrudedAreaSolid") and item.SweptArea.is_a("IfcRectangleProfileDef"):
                    return float(item.SweptArea.YDim)
        return self.u(0.2)

    def grid_outline(self):
        pts = []
        for g in self.m.by_type("IfcGrid"):
            for axis in list(g.UAxes or []) + list(g.VAxes or []):
                curve = axis.AxisCurve
                for p in curve.Points if curve.is_a("IfcPolyline") else []:
                    c = list(p.Coordinates)
                    pts.append((c[0] * self.scale, c[1] * self.scale))
        if not pts:
            raise DataspatiumError("give an outline, or create a grid first so that the slab can follow it")
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]

    def wall_extent(self, storey):
        """The bounding box (x0, y0, x1, y1) of the storey's walls in its plane and the height of their tops above it, in metres;
        (None, None) when no wall on the storey has a body the app can read."""
        frame = _cs_placement(storey.ObjectPlacement)
        if frame is None:
            return None, None
        back = _cs_invert(frame)
        pts = []
        for wall in self.m.by_type("IfcWall"):
            container = self.container(wall)
            if container is None or container.id() != storey.id():
                continue
            try:
                m = _cs_placement(wall.ObjectPlacement)
                rep = wall.Representation
                if m is None or rep is None:
                    continue
                m = _cs_compose(back, m)
                for r in rep.Representations or ():
                    if (r.RepresentationIdentifier or "") == "Body":
                        pts += [_cs_at(m, p) for item in (r.Items or ()) for p in _cs_item_points(item)]
            except Exception:  # nosec B112
                continue
        if not pts:
            return None, None
        s = self.scale
        return (
            min(p[0] for p in pts) * s,
            min(p[1] for p in pts) * s,
            max(p[0] for p in pts) * s,
            max(p[1] for p in pts) * s,
        ), max(p[2] for p in pts) * s

    # -- the operations
    def op_create_storeys_and_grid(self, op):
        import ifcopenshell.api.aggregate

        storeys = op.get("storeys") or []
        grid = op.get("grid")
        if not storeys and not grid:
            raise DataspatiumError("give storeys, a grid or both")
        building = self.building()
        made = []
        for s in storeys:
            if not isinstance(s, dict) or not str(s.get("name") or "").strip():
                raise DataspatiumError("every storey needs a name")
            existing = [x for x in self.m.by_type("IfcBuildingStorey") if str(x.Name or "") == str(s["name"])]
            if existing:
                self.storey_texts(existing[0], s, touch=True)
                made.append(existing[0])
                continue
            elevation = _cs_num(s, "elevation", 0.0)
            storey = self.root("IfcBuildingStorey", s["name"])
            storey.CompositionType = "ELEMENT"
            self.storey_texts(storey, s)
            if self.m.schema != "IFC4X3":
                storey.Elevation = self.u(
                    elevation
                )  # deprecated in IFC4.3 (IFC102): there the placement alone gives the height
            storey.ObjectPlacement = self.placement(building.ObjectPlacement, 0, 0, self.u(elevation))
            ifcopenshell.api.aggregate.assign_object(self.m, products=[storey], relating_object=building)
            self.register(storey, ref=False)
            made.append(storey)
        if grid:
            if not isinstance(grid, dict):
                raise DataspatiumError("grid must be an object with x_spacing, x_count, y_spacing and y_count")
            self.make_grid(grid, made[0] if made else self.storey(None))

    def storey_texts(self, storey, s, touch=False):
        """A storey's optional description (Description, such as 'Plan 2 (Entré)': the entrance level said in words) and long name
        (LongName), recommendation 2 of the Praxi study; on a storey that exists already only what differs is set, and it is changed."""
        changed = False
        for key, attribute in (("description", "Description"), ("long_name", "LongName")):
            value = str(s.get(key) or "").strip()[:255]
            if value and getattr(storey, attribute, None) != value:
                setattr(storey, attribute, value)
                changed = True
        if changed and touch:
            self.touch(storey)

    def make_grid(self, grid, storey):
        xs = _cs_num(grid, "x_spacing", positive=True)
        ys = _cs_num(grid, "y_spacing", positive=True)
        xn = int(_cs_num(grid, "x_count", 1.0, positive=True))
        yn = int(_cs_num(grid, "y_count", 1.0, positive=True))
        if xn > 100 or yn > 100:
            raise DataspatiumError("at most 100 bays per direction")
        width, depth = xs * xn, ys * yn
        u_axes = [
            self.m.createIfcGridAxis(
                _cs_label(i),
                self.m.createIfcPolyline([self.point(self.u(i * xs), 0), self.point(self.u(i * xs), self.u(depth))]),
                True,
            )
            for i in range(xn + 1)
        ]
        v_axes = [
            self.m.createIfcGridAxis(
                str(j + 1),
                self.m.createIfcPolyline([self.point(0, self.u(j * ys)), self.point(self.u(width), self.u(j * ys))]),
                True,
            )
            for j in range(yn + 1)
        ]
        g = self.root("IfcGrid", "Grid")
        g.ObjectPlacement = self.placement(storey.ObjectPlacement, 0, 0, 0)
        g.UAxes = u_axes
        g.VAxes = v_axes
        self.contain(g, storey)
        self.register(g, storey, ref=False)

    def op_create_wall(self, op):
        op, typ = self.with_type(op, "wall")
        storey = self.storey(op.get("storey"))
        x1, y1 = _cs_xy(op.get("start"), "start")
        x2, y2 = _cs_xy(op.get("end"), "end")
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            raise DataspatiumError("start and end are the same point")
        thickness = _cs_num(op, "thickness", 0.2, positive=True)
        height = _cs_num(op, "height", 3.0, positive=True)
        wall = self.root("IfcWall", op.get("name") or "Wall")
        wall.ObjectPlacement = self.placement(
            storey.ObjectPlacement, self.u(x1), self.u(y1), 0, (x2 - x1) / length, (y2 - y1) / length
        )
        wall.Representation = self.box(
            self.u(length), self.u(thickness), self.u(height), 0, -self.u(thickness) / 2.0, 0
        )
        # The centreline as the wall's 'Axis' (Curve2D), after the body, so that the topology reads it without the geometry kernel.
        axis = self.m.createIfcPolyline([self.point(0, 0), self.point(self.u(length), 0)])
        wall.Representation.Representations = list(wall.Representation.Representations) + [
            self.m.createIfcShapeRepresentation(self.axis_context(), "Axis", "Curve2D", [axis])
        ]
        # STANDARD is deprecated (IFC102): a wall the AI calls a non-load-bearing interior wall partitions, any other is solid.
        self.predefined(wall, "PARTITIONING" if op.get("partition") is True else "SOLIDWALL")
        self.contain(wall, storey)
        self.register(wall, storey)
        self.mark_common(wall, op)
        self.give_type(
            wall, typ, "AXIS2", "POSITIVE", -thickness / 2.0
        )  # the layers centred on the axis, the first on the right

    def op_create_slab(self, op):
        op, typ = self.with_type(op, "slab")
        storey = self.storey(op.get("storey"))
        thickness = _cs_num(op, "thickness", 0.25, positive=True)
        outline = op.get("outline") or self.grid_outline()
        if not isinstance(outline, (list, tuple)) or len(outline) < 3:
            raise DataspatiumError("outline must be at least three [x, y] points")
        pts = [_cs_xy(p, "outline") for p in outline]
        slab = self.root("IfcSlab", op.get("name") or "Slab")
        slab.ObjectPlacement = self.placement(storey.ObjectPlacement, 0, 0, 0)
        slab.Representation = self.prism(
            [(self.u(x), self.u(y)) for x, y in pts], self.u(thickness), -self.u(thickness)
        )
        self.predefined(slab, "FLOOR")
        self.contain(slab, storey)
        self.register(slab, storey)
        self.mark_common(slab, op)
        self.give_type(slab, typ, "AXIS3", "NEGATIVE", 0.0)  # the top at the storey, the first layer at the top

    def op_create_roof(self, op):
        """A roof over a rectangle: an IfcRoof in the storey (the highest when none is named) that aggregates one roof slab (IfcSlab
        ROOF) per face, the roof alone with a ref. The rectangle is the outline's bounding box or the storey's walls'; the eaves lie on
        its sides at base above the storey (by default the top of the walls, else 2.7 m), every face rises inward at the pitch (the
        ridge base + half the span x tan(pitch) high), and the overhang reaches beyond the sides. A face is the polygon of its outline
        in its own plane (Axis the face's normal, RefDirection along the eaves), extruded straight up by thickness / cos(pitch): its
        thickness at right angles is the given one, and neighbouring faces meet in the vertical planes through the ridge and the hips
        without a gap or an overlap."""
        import ifcopenshell.api.aggregate

        op, typ = self.with_type(op, "roof")
        shape = str(op.get("shape") or "").strip().lower()
        if shape not in self.ROOF_TYPES:
            raise DataspatiumError("shape must be gable, hip, shed or flat")
        storey = self.storey(op.get("storey"), top=True)
        flat = shape == "flat"
        pitch = 0.0 if flat else _cs_num(op, "pitch", 10.0 if shape == "shed" else 30.0, unit="degrees")
        if not flat and not 0.0 < pitch <= 75.0:
            raise DataspatiumError("pitch must be more than 0 and at most 75 degrees; use shape flat for a flat roof")
        overhang = _cs_num(op, "overhang", 0.0 if flat else 0.4)
        if overhang < 0:
            raise DataspatiumError("overhang must be zero or more (metres)")
        thickness = _cs_num(op, "thickness", 0.25, positive=True)
        ridge = str(op.get("ridge") or "").strip().lower() or None
        if ridge not in (None, "x", "y"):
            raise DataspatiumError("ridge must be x, y or null")
        outline = op.get("outline")
        extent, top = self.wall_extent(storey) if not outline or op.get("base") is None else (None, None)
        if outline:
            if not isinstance(outline, (list, tuple)) or len(outline) < 3:
                raise DataspatiumError("outline must be at least three [x, y] points")
            pts = [_cs_xy(p, "outline") for p in outline]
            extent = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
            if not _cs_rectangle(pts):
                self.notes.append(
                    "the roof's outline is not a rectangle along x and y (%d corners), so the roof covers its bounding box"
                    % len(pts)
                )
        elif extent is None:
            raise DataspatiumError(
                "give an outline: storey %r has no walls whose shape the app can read" % _ifc_text(storey.Name)
            )
        x0, y0, x1, y1 = extent
        length, width = x1 - x0, y1 - y0
        if length < 0.1 or width < 0.1:
            raise DataspatiumError(
                "the roof must span at least 0.1 m along x and along y, not %.3g x %.3g m" % (length, width)
            )
        base = _cs_num(op, "base", 2.7 if top is None else top)
        name = op.get("name") or "Roof"
        roof = self.root("IfcRoof", name)
        roof.ObjectPlacement = self.placement(storey.ObjectPlacement, self.u(x0), self.u(y0), self.u(base))
        if self.m.schema == "IFC2X3":
            if self.check_enum(roof, self.ROOF_TYPES[shape], "ShapeType"):
                roof.ShapeType = self.ROOF_TYPES[shape]
        else:
            self.predefined(roof, self.ROOF_TYPES[shape])
        self.contain(roof, storey)
        self.register(roof, storey)
        p = math.radians(pitch)
        sin, cos, tan = math.sin(p), math.cos(p), math.tan(p)
        slabs = []
        for i, (fall, corners) in enumerate(_cs_roof_faces(shape, length, width, overhang, ridge)):
            fx, fy = fall
            eaves = max(x * fx + y * fy for x, y in ((0.0, 0.0), (length, 0.0), (length, width), (0.0, width)))
            pts = [(x, y, (eaves - x * fx - y * fy) * tan) for x, y in corners]  # metres in the roof's placement
            along, up, normal = (-fy, fx, 0.0), (-fx * cos, -fy * cos, sin), (fx * sin, fy * sin, cos)
            o = pts[0]
            face = [
                [self.u(_cs_dot([q[k] - o[k] for k in range(3)], axis)) for axis in (along, up)] for q in pts
            ]  # in the face's plane
            profile = self.m.createIfcArbitraryClosedProfileDef("AREA", None, self.closed(face))
            position = self.m.createIfcAxis2Placement3D(
                self.point(self.u(o[0]), self.u(o[1]), self.u(o[2])), self.direction(*normal), self.direction(*along)
            )
            slab = self.root("IfcSlab", "%s %d" % (name, i + 1))
            slab.ObjectPlacement = self.placement(roof.ObjectPlacement, 0, 0, 0)
            slab.Representation = self.shape(
                self.m.createIfcExtrudedAreaSolid(
                    profile, position, self.direction(0.0, sin, cos), self.u(thickness / cos)
                )
            )
            self.predefined(slab, "ROOF")
            slabs.append(slab)
        ifcopenshell.api.aggregate.assign_object(self.m, products=slabs, relating_object=roof)
        for slab in slabs:
            self.register(slab, storey, ref=False)
        for slab in slabs:  # the layers rise from the face's underside, the plane of the slab's profile
            self.give_type(slab, typ, "AXIS3", "POSITIVE", 0.0)
        for e in [roof] + slabs:
            self.mark_common(e, op)

    def op_create_opening(self, op):
        import ifcopenshell.api.feature

        wall = self.entity(op.get("wall_id"))
        if not wall.is_a("IfcWall"):
            raise DataspatiumError("wall_id must name a wall, not %s" % wall.is_a())
        fill = op.get("fill")
        if fill not in (None, "", "door", "window"):
            raise DataspatiumError("fill must be door, window or null")
        typed = self.fill_type(op, fill or None)  # a door or window type gives the fill, its size and its geometry
        if typed is not None:
            fill = typed["fill"]
        offset = _cs_num(op, "offset", 0.0)
        width = typed["width"] if typed else _cs_num(op, "width", 0.9, positive=True)
        height = typed["height"] if typed else _cs_num(op, "height", 2.1, positive=True)
        sill = _cs_num(op, "sill", 0.0)
        thickness = self.wall_thickness(wall)
        storey = self.container(wall)
        opening = self.root("IfcOpeningElement", op.get("name") or "Opening")
        opening.ObjectPlacement = self.placement(wall.ObjectPlacement, self.u(offset), 0, self.u(sill))
        margin = self.u(0.02)
        opening.Representation = self.box(
            self.u(width), thickness + 2 * margin, self.u(height), 0, -thickness / 2.0 - margin, 0
        )
        self.predefined(opening, "OPENING")
        ifcopenshell.api.feature.add_feature(self.m, feature=opening, element=wall)
        self.touch(wall)
        if not fill:
            self.register(opening, storey)
            return
        self.register(opening, storey, ref=False)
        e = self.root(
            "IfcDoor" if fill == "door" else "IfcWindow", op.get("name") or ("Door" if fill == "door" else "Window")
        )
        if typed is None:
            e.ObjectPlacement = self.placement(opening.ObjectPlacement, 0, 0, 0)
            leaf = self.u(0.05)
            e.Representation = self.box(self.u(width), leaf, self.u(height), 0, -leaf / 2.0, 0)
        else:
            self.place_typed_fill(e, opening, wall, typed, thickness, op.get("side"))
        e.OverallHeight = self.u(height)
        e.OverallWidth = self.u(width)
        if typed is None:
            self.predefined(e, "DOOR" if fill == "door" else "WINDOW")
        ifcopenshell.api.feature.add_filling(self.m, opening=opening, element=e)
        if storey is not None:
            self.contain(e, storey)
        self.register(e, storey)
        if typed is not None:
            self.relate_type(e, typed["type"])
        # A door or window in a wall that already bounds spaces bounds the one whose edge it stands on, as create_spaces would have.
        mid = self.plan_point(e, self.u(width) / 2.0)
        for b in list(getattr(wall, "ProvidesBoundaries", None) or ()):
            outline = self.space_outline(b.RelatingSpace) if b.RelatingSpace.is_a("IfcSpace") else None
            if (
                mid is not None
                and outline is not None
                and outline.boundary.distance(_topo_point(mid)) <= thickness * self.scale / 2.0 + 0.05
            ):
                self.bound(b.RelatingSpace, e, b.InternalOrExternalBoundary == "EXTERNAL")

    # -- spaces and their boundaries
    # First-level boundaries (IFC4: IfcRelSpaceBoundary1stLevel; IFC2X3, which has no such class: IfcRelSpaceBoundary named '1stLevel',
    # the implementer agreement's name): one per planar face where a wall, a slab, a door, a window or an opening bounds the space.
    # A face is an IfcConnectionSurfaceGeometry with an IfcCurveBoundedPlane in the space's object coordinates (the form IFC gives for a
    # planar boundary in all three schemas, which IfcOpenShell's kernel builds; IfcFaceBasedSurfaceModel is deprecated in IFC4 and
    # IFC4.3, rule IFC102): an IfcPlane whose Axis points away from the space, and closed 2D IfcPolylines in its (u, v), the outer one
    # counter-clockwise about the Axis and the inner ones clockwise. As IFC4 defines the first level, a door's, a window's or an
    # opening's face overlaps its wall's, which is not cut, and names it as ParentBoundary (IFC4 and later).
    BOUND_CLASSES = ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcOpeningElement")
    BOUND_EPS = (
        1e-5  # metres: how near a wall's face a stretch of the outline inside the walls must lie to be that face
    )
    BOUND_MIN = 0.01  # metres: the narrowest face written
    BOUND_TOUCH = (
        0.05  # metres: how far a wall may stand from a space, or a slab from its floor or top, and still bound it
    )

    def bound(self, space, element, external, face=None, virtual=False, parent=None, reuse=None):
        """A first-level space boundary of a space against an element: PHYSICAL (VIRTUAL for an opening without a door or window),
        INTERNAL or EXTERNAL by `external` (True, False, or None for the value of the boundary reused, else NOTDEFINED), with the face
        (face_geometry) as its ConnectionGeometry when given, and in IFC4 and later the boundary it lies in as ParentBoundary. `reuse` is
        an earlier boundary of the pair, which keeps its GlobalId (an IfcRelSpaceBoundary of IFC4 becomes IfcRelSpaceBoundary1stLevel)."""
        import ifcopenshell.guid
        import ifcopenshell.util.element

        first = self.m.schema != "IFC2X3"
        cls = "IfcRelSpaceBoundary1stLevel" if first else "IfcRelSpaceBoundary"
        side = (
            "EXTERNAL"
            if external is True
            else "INTERNAL"
            if external is False
            else (reuse.InternalOrExternalBoundary if reuse is not None else None) or "NOTDEFINED"
        )
        guid = None
        if reuse is not None and reuse.is_a() != cls:
            guid = reuse.GlobalId
            self.unbound(reuse)
            reuse = None
        rel = reuse if reuse is not None else self.m.create_entity(cls, GlobalId=guid or ifcopenshell.guid.new())
        old = rel.ConnectionGeometry
        if old is not None:
            rel.ConnectionGeometry = None
            ifcopenshell.util.element.remove_deep2(self.m, old)
        rel.RelatingSpace = space
        rel.RelatedBuildingElement = element
        rel.PhysicalOrVirtualBoundary = "VIRTUAL" if virtual else "PHYSICAL"
        rel.InternalOrExternalBoundary = side
        rel.ConnectionGeometry = self.face_geometry(space, face) if face is not None else None
        if first:
            rel.ParentBoundary = parent
        else:
            rel.Name = "1stLevel"
        if self.owner is not None and rel.OwnerHistory is None:
            rel.OwnerHistory = self.owner
        return rel

    def face_geometry(self, space, face):
        """The IfcConnectionSurfaceGeometry of a face in the space's object coordinates, in file units. face: "o" (the plane's origin),
        "x" (its u direction) and "z" (its normal, away from the space) in the world in metres, and "rings": the outer ring and the
        holes as 3D points in the world in metres, on the plane."""
        from shapely.geometry import Polygon
        from shapely.geometry.polygon import orient

        back = _cs_invert(_cs_placement(space.ObjectPlacement))
        s = self.scale

        def local(p):
            return _cs_at(back, [float(c) / s for c in p])

        o = local(face["o"])
        x = _cs_unit(_cs_turn(back, face["x"]))
        z = _cs_unit(_cs_turn(back, face["z"]))
        y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]

        def uv(p):
            q = [a - b for a, b in zip(local(p), o)]
            return round(_cs_dot(q, x), 6) + 0.0, round(_cs_dot(q, y), 6) + 0.0

        rings = [[uv(p) for p in ring] for ring in face["rings"]]
        poly = orient(
            Polygon(rings[0], rings[1:]), sign=1.0
        )  # the outer ring counter-clockwise about the normal, the holes clockwise

        def curve(ring):
            pts = []
            for c in list(ring.coords)[:-1]:
                if not pts or pts[-1] != c:
                    pts.append(c)
            return self.closed(pts)

        position = self.m.createIfcAxis2Placement3D(
            self.point(*[round(c, 9) + 0.0 for c in o]),
            self.direction(*[round(c, 12) + 0.0 for c in z]),
            self.direction(*[round(c, 12) + 0.0 for c in x]),
        )
        surface = self.m.createIfcCurveBoundedPlane(
            self.m.createIfcPlane(position), curve(poly.exterior), [curve(r) for r in poly.interiors]
        )
        return self.m.createIfcConnectionSurfaceGeometry(surface, None)

    def maintained(self, rel):
        """Whether the app writes a boundary anew: a first-level one (not IfcRelSpaceBoundary2ndLevel, not named '2ndLevel') against a
        wall, slab, door, window or opening. Another program's second-level boundaries, and boundaries to other elements, stay."""
        if rel.is_a("IfcRelSpaceBoundary2ndLevel") or str(rel.Name or "").strip().lower() == "2ndlevel":
            return False
        e = rel.RelatedBuildingElement
        return e is not None and any(e.is_a(c) for c in self.BOUND_CLASSES)

    def prism_of(self, product):
        """(outline, bottom, top) of a product whose body is one extrusion straight up of a polygonal profile (a Boolean result by its
        first operand): the outline a shapely polygon in the world's plan with the profile's voids as holes, the heights in the world,
        all in metres; None for another body."""
        from shapely.geometry import Polygon

        m = _cs_placement(getattr(product, "ObjectPlacement", None))
        rep = getattr(product, "Representation", None)
        if m is None or rep is None:
            return None
        items = [
            i
            for r in (rep.Representations or ())
            if (r.RepresentationIdentifier or "Body") == "Body"
            for i in (r.Items or ())
        ]
        while len(items) == 1 and items[0].is_a("IfcBooleanResult"):
            items = [items[0].FirstOperand]
        if len(items) != 1 or not items[0].is_a("IfcExtrudedAreaSolid"):
            return None
        item = items[0]
        p = _cs_compose(m, _cs_axes(item.Position))
        d = _cs_unit(_cs_turn(p, [float(c) for c in item.ExtrudedDirection.DirectionRatios]))
        if d is None or abs(d[2]) < 0.9999 or abs(p[2][2]) < 0.9999:
            return None
        profile = item.SweptArea
        rings = [_cs_profile_points(profile)]
        for c in getattr(profile, "InnerCurves", None) or ():
            pts = (
                [[float(v) for v in q.Coordinates][:2] for q in c.Points]
                if c.is_a("IfcPolyline")
                else [[float(v) for v in q][:2] for q in c.Points.CoordList]
                if c.is_a("IfcIndexedPolyCurve")
                else []
            )
            if len(pts) >= 3:
                rings.append(pts)
        if len(rings[0]) < 3:
            return None
        s = self.scale
        world = [[(q[0] * s, q[1] * s) for q in (_cs_at(p, (x, y, 0.0)) for x, y in ring)] for ring in rings]
        poly = Polygon(world[0], world[1:])
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            return None
        z0 = p[3][2] * s
        z1 = z0 + float(item.Depth) * d[2] * s
        return poly, min(z0, z1), max(z0, z1)

    def world_points(self, e):
        """The corners of an element's body in the world, in metres (_cs_item_points); [] when the app cannot read them."""
        m = _cs_placement(getattr(e, "ObjectPlacement", None))
        rep = getattr(e, "Representation", None)
        if m is None or rep is None:
            return []
        s = self.scale
        return [
            [c * s for c in _cs_at(m, p)]
            for r in (rep.Representations or ())
            if (r.RepresentationIdentifier or "Body") == "Body"
            for item in (r.Items or ())
            for p in _cs_item_points(item)
        ]

    def host_of(self, e):
        """The element whose opening a door, window or opening is in; None for another element."""
        opening = e
        if not e.is_a("IfcOpeningElement"):
            fills = list(getattr(e, "FillsVoids", None) or ())
            opening = fills[0].RelatingOpeningElement if fills else None
        voids = list(getattr(opening, "VoidsElements", None) or ()) if opening is not None else []
        return voids[0].RelatingBuildingElement if voids else None

    def space_storey(self, space):
        """The storey a space is aggregated in, through spaces it is part of; None when it is in none."""
        e = space
        for _ in range(8):
            rel = next(iter(getattr(e, "Decomposes", None) or ()), None)
            if rel is None:
                return None
            e = rel.RelatingObject
            if e.is_a("IfcBuildingStorey"):
                return e
        return None

    def bound_context(self, external=None):
        """The readings the boundaries of one step share: walls per storey, slabs, spaces' bodies and, per storey, the walls the
        topology finds external and those it finds in rooms (external: {storey id: (external ids, room wall ids)}, given by
        create_spaces, which has the topology)."""
        return {"walls": {}, "slabs": None, "solids": {}, "sides": dict(external or {}), "settings": [None]}

    def ctx_walls(self, ctx, storey):
        """The walls of a storey as the boundaries read them, as the topology does: [{"e", "segs": [(a, b)], "w", "band", "z"}], the
        straight pieces of the centreline, the thickness and the footprint in the world's plan and the body's bottom and top (None
        when unread), in metres."""
        if storey.id() in ctx["walls"]:
            return ctx["walls"][storey.id()]
        from shapely.geometry import LineString
        from shapely.ops import unary_union

        out = []
        for w in (
            e
            for rel in (getattr(storey, "ContainsElements", None) or ())
            for e in (rel.RelatedElements or ())
            if e.is_a("IfcWall")
        ):
            try:
                line = _topo_axis(w, self.scale)
                width = _topo_thickness(w, self.scale) if line else None
            except Exception:
                line = None
            if not line:
                try:
                    if ctx["settings"][0] is None:
                        ctx["settings"][0] = _topo_settings()
                    line, width = _topo_footprint(w, ctx["settings"][0])
                except Exception:
                    line = None
            segs = [(tuple(a), tuple(b)) for a, b in zip(line or (), (line or ())[1:]) if math.dist(a, b) > 1e-6]
            if not segs:
                continue
            width = float(width or 0.2)
            zs = [p[2] for p in self.world_points(w)]
            out.append(
                {
                    "e": w,
                    "segs": segs,
                    "w": width,
                    "band": unary_union([LineString([a, b]).buffer(width / 2.0, cap_style="flat") for a, b in segs]),
                    "z": (min(zs), max(zs)) if zs else None,
                }
            )
        ctx["walls"][storey.id()] = out
        return out

    def ctx_slabs(self, ctx):
        """The model's slabs that lie flat as the boundaries read them: [{"e", "foot", "z0", "z1"}] in metres (prism_of)."""
        if ctx["slabs"] is None:
            ctx["slabs"] = []
            for e in self.m.by_type("IfcSlab"):
                got = self.prism_of(e)
                if got is not None:
                    ctx["slabs"].append({"e": e, "foot": got[0], "z0": got[1], "z1": got[2]})
        return ctx["slabs"]

    def ctx_solid(self, ctx, space):
        if space.id() not in ctx["solids"]:
            ctx["solids"][space.id()] = self.prism_of(space)
        return ctx["solids"][space.id()]

    def ctx_sides(self, ctx, storey):
        """(external wall ids, wall ids in rooms) of a storey's topology; None when it finds no room there or cannot be computed."""
        if storey.id() not in ctx["sides"]:
            try:
                rec = next(
                    (
                        r
                        for r in _ifc_topology(self.m, storeys={_ifc_text(storey.Name)}, shapes=True)
                        if r["_storey"].id() == storey.id()
                    ),
                    None,
                )
            except Exception:
                rec = None
            ctx["sides"][storey.id()] = (
                ({x["wall"] for x in rec["external"]}, {w for room in rec["rooms"] for w in room["walls"]})
                if rec and rec["rooms"]
                else None
            )
        return ctx["sides"][storey.id()]

    def slab_external(self, slab, storey, floor):
        """Whether a slab is external to the spaces above it (floor) or below it: the slab's IsExternal when it has one; else true for a
        roof slab and a base slab, and for a floor on the lowest storey and a ceiling over the highest."""
        import ifcopenshell.util.element as el

        try:
            value = el.get_pset(slab, "Pset_SlabCommon", "IsExternal")
        except Exception:
            value = None
        if isinstance(value, bool):
            return value
        try:
            kind = str(el.get_predefined_type(slab) or "")
        except Exception:
            kind = str(getattr(slab, "PredefinedType", None) or "")
        if kind == "ROOF" or (floor and kind == "BASESLAB"):
            return True
        levels = [_topo_elevation(s, self.scale) for s in self.m.by_type("IfcBuildingStorey")]
        z = _topo_elevation(storey, self.scale)
        return z <= min(levels) + 1e-6 if floor else z >= max(levels) - 1e-6

    def wall_openings(self, wall):
        """[(element, opening, virtual)]: the doors and windows in a wall's openings by their opening, and in IFC4 and later an opening
        without one as a virtual boundary of the IfcOpeningElement (IFC2X3's rule WR1 takes no element for a virtual boundary);
        recesses are left out."""
        out = []
        for v in getattr(wall, "HasOpenings", None) or ():
            o = v.RelatedOpeningElement
            if str(getattr(o, "PredefinedType", None) or "") == "RECESS":
                continue
            fills = [
                f.RelatedBuildingElement
                for f in (getattr(o, "HasFillings", None) or ())
                if f.RelatedBuildingElement.is_a("IfcDoor") or f.RelatedBuildingElement.is_a("IfcWindow")
            ]
            if fills:
                out += [(f, o, False) for f in fills]
            elif self.m.schema != "IFC2X3":
                out.append((o, o, True))
        return out

    def space_faces(self, space, ctx):
        """The faces that bound a space, or None when its body is not an extrusion straight up of a polygon (prism_of) or it is in no
        storey: (faces, read), read the ids of the elements the app read for it (the walls of its storey with their doors, windows and
        openings, and the flat slabs), whose earlier boundaries the faces replace.

        Inside the walls the space's outline is less the walls' footprints; a wall bounds the space along each straight piece of its
        centreline where that outline runs along the piece's face (half the thickness to either side), from the floor's top (the slab
        below, else the space's bottom) to the ceiling's underside (the slab above, else the space's top), within the wall's own height.
        A door, window or opening in such a face bounds it where its opening's body lies, in the same plane. A slab lying flat whose
        thickness reaches the space's bottom (top), within BOUND_TOUCH, bounds it with its top (underside) inside the walls."""
        from shapely.geometry import LineString
        from shapely.ops import unary_union

        solid = self.ctx_solid(ctx, space)
        storey = self.space_storey(space)
        if solid is None or storey is None:
            return None
        P, z0, z1 = solid
        walls = self.ctx_walls(ctx, storey)
        near = [w for w in walls if w["band"].distance(P) <= self.BOUND_TOUCH]
        inside = P.difference(unary_union([w["band"] for w in near])) if near else P
        touch, eps, least = self.BOUND_TOUCH, self.BOUND_EPS, self.BOUND_MIN
        slabs = [x for x in self.ctx_slabs(ctx) if x["foot"].intersects(inside)]
        floors = [x for x in slabs if x["z0"] - touch <= z0 <= x["z1"] + touch]
        ceilings = [x for x in slabs if x not in floors and x["z0"] - touch <= z1 <= x["z1"] + touch]
        bottom = max([z0] + [x["z1"] for x in floors])
        top = min([z1] + [x["z0"] for x in ceilings])
        if top - bottom < least:
            bottom, top = z0, z1
        sides = self.ctx_sides(ctx, storey)
        read = (
            {w["e"].id() for w in walls}
            | {x["e"].id() for x in self.ctx_slabs(ctx)}
            | {e.id() for w in walls for e, _, _ in self.wall_openings(w["e"])}
        )
        edge = inside.boundary
        faces = []
        for w in near:
            if sides is None:
                external = None
            else:
                external = True if w["e"].id() in sides[0] else False if w["e"].id() in sides[1] else None
            holes = self.wall_openings(w["e"])
            lo, hi = (max(bottom, w["z"][0]), min(top, w["z"][1])) if w["z"] else (bottom, top)
            if hi - lo < least:
                continue
            for a, b in w["segs"]:
                length = math.dist(a, b)
                d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
                n = (-d[1], d[0])
                for sign in (1.0, -1.0):
                    fa = (a[0] + n[0] * sign * w["w"] / 2.0, a[1] + n[1] * sign * w["w"] / 2.0)
                    fb = (fa[0] + d[0] * length, fa[1] + d[1] * length)
                    hit = edge.intersection(LineString([fa, fb]).buffer(eps, cap_style="flat"))
                    spans = []
                    for g in getattr(hit, "geoms", [hit]):
                        ts = [(c[0] - fa[0]) * d[0] + (c[1] - fa[1]) * d[1] for c in getattr(g, "coords", ())]
                        if ts:
                            spans.append([max(0.0, min(ts)), min(length, max(ts))])
                    merged = []
                    for span in sorted(spans):
                        if merged and span[0] <= merged[-1][1] + 2 * eps:
                            merged[-1][1] = max(merged[-1][1], span[1])
                        else:
                            merged.append(span)

                    def at(t, zz):
                        return (fa[0] + d[0] * t, fa[1] + d[1] * t, zz)

                    def rect(t0, t1, g0, g1):
                        start = (
                            t0 if sign > 0 else t1
                        )  # u runs along the face as seen from the space, so that the normal points away
                        return {
                            "o": at(start, g0),
                            "x": (sign * d[0], sign * d[1], 0.0),
                            "z": (-sign * n[0], -sign * n[1], 0.0),
                            "rings": [[at(t0, g0), at(t1, g0), at(t1, g1), at(t0, g1)]],
                            "area": (t1 - t0) * (g1 - g0),
                        }

                    for s0, s1 in merged:
                        if s1 - s0 < least:
                            continue
                        faces.append(dict(rect(s0, s1, lo, hi), e=w["e"], external=external))
                        parent = len(faces) - 1
                        for e, opening, virtual in holes:
                            pts = self.world_points(opening) or self.world_points(e)
                            if not pts:
                                continue
                            ts = [(p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] for p in pts]
                            off = sum((p[0] - a[0]) * n[0] + (p[1] - a[1]) * n[1] for p in pts) / len(pts)
                            if abs(off) > w["w"] / 2.0 + touch:
                                continue  # in another straight piece of the wall
                            q0, q1 = max(min(ts), s0), min(max(ts), s1)
                            g0, g1 = max(min(p[2] for p in pts), lo), min(max(p[2] for p in pts), hi)
                            if q1 - q0 >= least and g1 - g0 >= least:
                                faces.append(
                                    dict(rect(q0, q1, g0, g1), e=e, external=external, virtual=virtual, parent=parent)
                                )
        for floor, group in ((True, floors), (False, ceilings)):
            for x in group:
                part = inside.intersection(x["foot"])
                z = x["z1"] if floor else x["z0"]
                for poly in getattr(part, "geoms", [part]):
                    if poly.geom_type != "Polygon" or poly.area < least * least:
                        continue
                    minx, miny, _, maxy = poly.bounds
                    rings = [
                        [(c[0], c[1], z) for c in ring.coords[:-1]] for ring in [poly.exterior] + list(poly.interiors)
                    ]
                    faces.append(
                        {
                            "o": (minx, maxy if floor else miny, z),
                            "x": (1.0, 0.0, 0.0),
                            "z": (0.0, 0.0, -1.0 if floor else 1.0),
                            "rings": rings,
                            "area": poly.area,
                            "e": x["e"],
                            "external": self.slab_external(x["e"], storey, floor),
                        }
                    )
        return faces, read

    def bound_space(self, space, ctx):
        """Write a space's first-level boundaries from its faces (space_faces): an earlier boundary of the space to an element the app
        read is reused for that element's faces in order, so that it keeps its GlobalId, and one left over is removed; boundaries to
        other elements stay. False, with nothing changed, when the faces cannot be read."""
        got = self.space_faces(space, ctx)
        if got is None:
            return False
        faces, read = got
        old = {}
        for rel in list(getattr(space, "BoundedBy", None) or ()):
            e = rel.RelatedBuildingElement
            if e is not None and e.id() in read and self.maintained(rel):
                old.setdefault(e.id(), []).append(rel)
        rels = []
        for f in faces:
            reuse = old.get(f["e"].id())
            parent = rels[f["parent"]] if f.get("parent") is not None else None
            rels.append(
                self.bound(
                    space, f["e"], f["external"], f, f.get("virtual", False), parent, reuse.pop(0) if reuse else None
                )
            )
        for rest in old.values():
            for rel in rest:
                self.unbound(rel)
        return True

    def space_boundaries(self):
        """At the end of apply, after the joins: the boundaries (bound_space) of the spaces that what the change set created or changed
        bounds or bounded — a wall on their storey within BOUND_TOUCH of them, a slab at their floor or top, a door, window or
        opening by its wall, and the spaces themselves — so that a wall made after the rooms bounds them and the faces follow a door
        or a moved wall. A deleted element's boundaries went with it (remove_product), and the spaces it bounded (unbounded) get the
        others anew, so that the faces next to a deleted wall or under a deleted ceiling reach as far as they now do; a space whose body
        the app cannot read keeps its boundaries, with a note."""
        everywhere = self.m.by_type("IfcSpace")
        if not everywhere:
            return
        spaces, walls, slabs = {}, {}, {}
        for i in sorted(self.unbounded):
            try:
                spaces[i] = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # the space itself was deleted
        for i in dict.fromkeys([r["id"] for r in self.created] + list(self.changed)):
            try:
                e = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            for x in (
                e,
                self.host_of(e) if e.is_a("IfcDoor") or e.is_a("IfcWindow") or e.is_a("IfcOpeningElement") else None,
            ):
                for rel in getattr(x, "ProvidesBoundaries", None) or ():
                    if rel.RelatingSpace is not None and rel.RelatingSpace.is_a("IfcSpace"):
                        spaces[rel.RelatingSpace.id()] = rel.RelatingSpace
                for kind, into in (("IfcSpace", spaces), ("IfcWall", walls), ("IfcSlab", slabs)):
                    if x is not None and x.is_a(kind):
                        into[x.id()] = x
        if not (spaces or walls or slabs):
            return
        ctx = self.bound_context()
        storeys = {}
        for s in everywhere:
            storey = self.space_storey(s)
            if storey is not None:
                storeys.setdefault(storey.id(), []).append(s)
        for w in walls.values():
            storey = self.container(w)
            mine = (
                next((x for x in self.ctx_walls(ctx, storey) if x["e"].id() == w.id()), None)
                if storey is not None and storey.id() in storeys
                else None
            )
            for s in storeys[storey.id()] if mine is not None else ():
                solid = self.ctx_solid(ctx, s)
                if solid is not None and mine["band"].distance(solid[0]) <= self.BOUND_TOUCH:  # ty: ignore[not-subscriptable]
                    spaces[s.id()] = s
        for x in (x for x in self.ctx_slabs(ctx) if x["e"].id() in slabs):
            for s in everywhere:
                solid = self.ctx_solid(ctx, s)
                if (
                    solid is not None
                    and x["foot"].intersects(solid[0])
                    and any(x["z0"] - self.BOUND_TOUCH <= z <= x["z1"] + self.BOUND_TOUCH for z in solid[1:])
                ):
                    spaces[s.id()] = s
        for s in sorted(spaces.values(), key=lambda s: s.id()):
            if not self.bound_space(s, ctx):
                self.note_once(
                    "the boundaries of space %s keep their faces: the app reads only a space in a storey whose body is extruded "
                    "straight up from a polygon" % self.element_label(s)
                )

    @staticmethod
    def fillings(wall):
        """The doors and windows in a wall's openings."""
        return [
            f.RelatedBuildingElement
            for v in (getattr(wall, "HasOpenings", None) or ())
            for f in (getattr(v.RelatedOpeningElement, "HasFillings", None) or ())
        ]

    def plan_point(self, e, along=0.0):
        """Where an element's placement stands in the world's plan, `along` file units along its x axis, in metres; None if unknown."""
        m = _cs_placement(getattr(e, "ObjectPlacement", None))
        if m is None:
            return None
        p = _cs_at(m, (along, 0.0, 0.0))
        return p[0] * self.scale, p[1] * self.scale

    def space_outline(self, space):
        """A space's footprint in the world's plan in metres (shapely): the profile of an extruded body, else the hull of its body's
        corners; None without a body the app can read."""
        from shapely.geometry import MultiPoint, Polygon

        m = _cs_placement(space.ObjectPlacement)
        rep = space.Representation
        pts = []
        for r in rep.Representations if rep is not None and m is not None else ():
            if (r.RepresentationIdentifier or "Body") != "Body":
                continue
            for item in r.Items or ():
                corners = [_cs_at(m, p) for p in _cs_item_points(item)]
                if item.is_a("IfcExtrudedAreaSolid") and len(corners) >= 6:
                    return Polygon([(q[0] * self.scale, q[1] * self.scale) for q in corners[::2]])
                pts += corners
        hull = MultiPoint([(q[0] * self.scale, q[1] * self.scale) for q in pts]).convex_hull if len(pts) >= 3 else None
        return hull if hull is not None and hull.geom_type == "Polygon" else None

    def storey_height(self, storey):
        """The default height of a storey's spaces in metres: up to the next storey, else to the top of its walls, else 3."""
        z = _topo_elevation(storey, self.scale)
        above = sorted(
            e for e in (_topo_elevation(s, self.scale) for s in self.m.by_type("IfcBuildingStorey")) if e > z + 1e-6
        )
        if above:
            return above[0] - z
        top = self.wall_extent(storey)[1]
        return top if top and top > 0 else 3.0

    def pick_rooms(self, topo, rooms, frame):
        """The listed rooms of one storey's topology, as (room, name, long name): each by a point inside it in the storey's plane, or by
        its index in get_topology. A point that hits no room, an index that does not exist and a room named twice are errors."""
        storey = _ifc_text(topo["_storey"].Name)
        back = _cs_invert(frame)

        def where(r):
            p = r["polygon"].representative_point()
            q = _cs_at(back, (p.x / self.scale, p.y / self.scale, frame[3][2]))
            return "%d at (%.2f, %.2f)" % (r["index"], q[0] * self.scale, q[1] * self.scale)

        out, seen = [], {}
        for i, entry in enumerate(rooms):
            if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
                raise DataspatiumError("room %d needs a name" % (i + 1))
            if entry.get("at") is not None:
                x, y = _cs_xy(entry.get("at"), "at")
                w = _cs_at(frame, (x / self.scale, y / self.scale, 0.0))
                hit = [
                    r
                    for r in topo["rooms"]
                    if r["polygon"].contains(_topo_point((w[0] * self.scale, w[1] * self.scale)))
                ]
                if not hit:
                    raise DataspatiumError(
                        "room %d: no closed room on storey %r has (%g, %g) inside; its rooms are %s"
                        % (i + 1, storey, x, y, "; ".join(where(r) for r in topo["rooms"]))
                    )
                room = hit[0]
            else:
                index = entry.get("index")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, (int, float))
                    or int(index) != index
                    or not 1 <= int(index) <= len(topo["rooms"])
                ):
                    raise DataspatiumError(
                        "room %d: storey %r has rooms 1 to %d, not %r" % (i + 1, storey, len(topo["rooms"]), index)
                    )
                room = topo["rooms"][int(index) - 1]
            if room["index"] in seen:
                raise DataspatiumError(
                    "rooms %d and %d are the same room (%s)" % (seen[room["index"]], i + 1, where(room))
                )
            seen[room["index"]] = i + 1
            out.append(self.room_names(room, entry, i, out))
        return out

    def room_names(self, room, entry, i, before):
        """(room, Name, LongName) of a listed room. With a number (optional, recommendation 2 of the Praxi study) the number is the
        Name, unique among the model's spaces and the rooms listed before, the name the LongName and long_name the Description;
        without one the name is the Name and long_name the LongName, as before numbers came. room_type (optional) is the room's
        type, written as ObjectType with PredefinedType USERDEFINED by make_space, which reads it and the Description from room["ifc"]
        (the topology's room dicts are made for this operation alone)."""
        name = str(entry["name"]).strip()[:255]
        long_name = str(entry.get("long_name") or "").strip()[:255] or None
        number = str(entry.get("number") if entry.get("number") is not None else "").strip()[:255]
        room["ifc"] = {"room_type": str(entry.get("room_type") or "").strip()[:255] or None}
        if not number:
            return room, name, long_name
        if number in {n for _, n, _ in before}:
            first = next(k for k, (_, n, _) in enumerate(before) if n == number)
            raise DataspatiumError(
                "room %d has the number of room %d; a room number is unique in the building" % (i + 1, first + 1)
            )
        taken = next((s for s in self.m.by_type("IfcSpace") if str(s.Name or "") == number), None)
        if taken is not None:
            raise DataspatiumError(
                "room %d: the space #%d has its number already; a room number is unique in the building"
                % (i + 1, taken.id())
            )
        room["ifc"]["description"] = long_name
        return room, number, name

    def make_space(self, storey, frame, room, name, long_name, height):
        """An IfcSpace with the room's outline (holes as voids) in the storey's plane, extruded height metres up from the storey."""
        back = _cs_invert(frame)

        def curve(ring):
            return self.closed(
                [_cs_at(back, (c[0] / self.scale, c[1] / self.scale, frame[3][2]))[:2] for c in ring.coords]
            )

        poly = room["polygon"]
        if poly.interiors:
            profile = self.m.createIfcArbitraryProfileDefWithVoids(
                "AREA", None, curve(poly.exterior), [curve(r) for r in poly.interiors]
            )
        else:
            profile = self.m.createIfcArbitraryClosedProfileDef("AREA", None, curve(poly.exterior))
        space = self.root("IfcSpace", name)
        space.LongName = long_name
        space.CompositionType = "ELEMENT"
        extra = room.get("ifc") or {}
        if extra.get("description"):
            space.Description = extra["description"]
        if extra.get("room_type"):
            space.ObjectType = extra[
                "room_type"
            ]  # the room's type (Praxi: ObjectType with USERDEFINED); IFC2X3 has no PredefinedType
        if self.m.schema == "IFC2X3":
            if self.check_enum(space, "INTERNAL", "InteriorOrExteriorSpace"):
                space.InteriorOrExteriorSpace = "INTERNAL"
        elif extra.get("room_type"):
            self.predefined(space, "USERDEFINED")
        elif self.m.schema == "IFC4X3":
            self.predefined(
                space, "SPACE"
            )  # INTERNAL is deprecated in IFC4.3 (IFC102); that the room is inside is IsExternal false
        else:
            self.predefined(space, "INTERNAL")
        space.ObjectPlacement = self.placement(storey.ObjectPlacement, 0, 0, 0)
        space.Representation = self.shape(
            self.m.createIfcExtrudedAreaSolid(profile, self.axis3(0, 0, 0), self.direction(0, 0, 1), self.u(height))
        )
        return space

    def space_quantities(self, space, room, height):
        """Qto_SpaceBaseQuantities in the file's units: the outline's area and perimeter, the area inside the walls' faces, the volumes."""
        import ifcopenshell.api.pset
        import ifcopenshell.util.unit

        def per(kind):
            try:
                return float(ifcopenshell.util.unit.calculate_unit_scale(self.m, kind) or 1.0)
            except Exception:
                return 1.0

        area, volume = per("AREAUNIT"), per("VOLUMEUNIT")
        gross, net = room["polygon"].area, room["net_area"]
        qto = ifcopenshell.api.pset.add_qto(self.m, product=space, name="Qto_SpaceBaseQuantities")
        ifcopenshell.api.pset.edit_qto(
            self.m,
            qto=qto,
            properties={
                "Height": self.u(height),
                "GrossPerimeter": self.u(room["polygon"].length),
                "GrossFloorArea": gross / area,
                "NetFloorArea": net / area,
                "GrossVolume": gross * height / volume,
                "NetVolume": net * height / volume,
            },
        )

    def op_create_spaces(self, op):
        """The rooms the walls enclose as spaces, from the topology of the file as it stands at this step, so that walls made earlier
        in the change set count: per room an IfcSpace aggregated in its storey, with the room's outline extruded to the height (by
        default up to the next storey, or to the top of the walls), Qto_SpaceBaseQuantities, and first-level physical space boundaries
        with their faces (bound_space) to the walls along its edge (external where the topology finds the wall external) and to the
        doors, windows and openings in their faces toward it; the slabs below and above get theirs at the end of apply
        (space_boundaries), as do walls made later. Listed rooms need the storey and get a ref each, in their order; without a list
        every room on the storey (or on every storey with rooms) is made, named by default_name with its index for {n}, and none gets
        a ref."""
        import ifcopenshell.api.aggregate

        rooms = op.get("rooms")
        listed = rooms is not None
        if listed and (not isinstance(rooms, list) or not rooms):
            raise DataspatiumError("rooms must be a list of {at or index, name, long_name}, or null for every room")
        named = str(op.get("storey") or "").strip()
        if listed and not named:
            raise DataspatiumError("name the storey the rooms are on; storey null goes with rooms null")
        storey = self.storey(named) if named else None
        height = None if op.get("height") is None else _cs_num(op, "height", positive=True)
        found = [
            x
            for x in _ifc_topology(
                self.m, storeys={_ifc_text(storey.Name)} if storey is not None else None, shapes=True
            )
            if x["rooms"] and (storey is None or x["_storey"].id() == storey.id())
        ]
        if not found:
            raise DataspatiumError(
                "the walls enclose no room on %s; close the walls first (the preview's topology shows the open ends)"
                % ("storey %r" % _ifc_text(storey.Name) if storey is not None else "any storey")
            )
        template = str(op.get("default_name") or "Room {n}")
        made = 0
        for topo in found:
            s = topo["_storey"]
            frame = _cs_placement(s.ObjectPlacement)
            if frame is None:
                raise DataspatiumError("the placement of storey %r cannot be followed" % _ifc_text(s.Name))
            chosen = (
                self.pick_rooms(topo, rooms, frame)
                if listed
                else [(r, template.replace("{n}", str(r["index"])), None) for r in topo["rooms"]]
            )
            # The storey's spaces so far: a room one of them covers is not made twice, and a name is not given twice.
            others = [
                (p, self.space_outline(p))
                for rel in (getattr(s, "IsDecomposedBy", None) or ())
                for p in rel.RelatedObjects
                if p.is_a("IfcSpace")
            ]
            covered = [
                (
                    room,
                    name,
                    next(
                        (p for p, o in others if o is not None and o.contains(room["polygon"].representative_point())),
                        None,
                    ),
                )
                for room, name, _ in chosen
            ]
            for room, name, space in covered:
                if space is not None and listed:
                    raise DataspatiumError(
                        "room %d on storey %r lies in the space %r already"
                        % (room["index"], _ifc_text(s.Name), _ifc_text(space.Name))
                    )
                if space is not None:
                    self.notes.append(
                        "room %d on storey %s was left out: the space %s covers it already"
                        % (room["index"], _ifc_text(s.Name), _ifc_text(space.Name))
                    )
            chosen = [c for c, (_, _, space) in zip(chosen, covered) if space is None]
            twice = [n for _, n, _ in chosen if n in {str(p.Name or "") for p, _ in others}]
            if twice:
                raise DataspatiumError(
                    "storey #%d has a space with the name or number of a listed room already" % s.id()
                )
            if not chosen:
                continue
            made += len(chosen)
            h = height or self.storey_height(s)
            external = {e["wall"] for e in topo["external"]}
            ctx = self.bound_context({s.id(): (external, {w for r in topo["rooms"] for w in r["walls"]})})
            spaces = [self.make_space(s, frame, room, name, long_name, h) for room, name, long_name in chosen]
            ifcopenshell.api.aggregate.assign_object(self.m, products=spaces, relating_object=s)
            for space, (room, _, _) in zip(spaces, chosen):
                self.space_quantities(space, room, h)
                if self.m.schema == "IFC4X3" or (
                    self.m.schema == "IFC4" and space.PredefinedType == "USERDEFINED"
                ):  # no INTERNAL to say it
                    import ifcopenshell.api.pset

                    pset = ifcopenshell.api.pset.add_pset(self.m, product=space, name="Pset_SpaceCommon")
                    ifcopenshell.api.pset.edit_pset(self.m, pset=pset, properties={"IsExternal": False})
                # The first-level boundaries with their faces (bound_space; walls, doors and windows now, slabs made later at the end
                # of apply); a wall of the room's that the faces miss is bound without one, as before faces came.
                self.bound_space(space, ctx)
                faced = {
                    r.RelatedBuildingElement.id()
                    for r in (getattr(space, "BoundedBy", None) or ())
                    if r.RelatedBuildingElement is not None
                }
                for i in room["walls"]:
                    if i not in faced:
                        self.bound(space, self.m.by_id(i), i in external)
                self.register(space, s, ref=listed)
        if not made:
            raise DataspatiumError("every room the walls enclose has a space already")

    def op_set_properties(self, op):
        """Properties of one set on elements or types. A set named Pset_... or Qto_... must be IfcOpenShell's template of that name in
        the file's schema (property_template), and its properties are spelled and checked as the template says (checked_properties); a
        Qto_ set becomes an IfcElementQuantity. A set of the model's own takes text, numbers and booleans as they come, though not a
        number with its unit written after it. The element's own set of the name is edited, never its type's (put_properties)."""
        name = str(op.get("pset") or "").strip()
        props = op.get("properties")
        if not name or not isinstance(props, dict) or not props:
            raise DataspatiumError("pset and properties (an object of name: value) are required")
        template, name = self.property_template(name)
        values = self.checked_properties(template, name, props)
        for e in self.elements(op):
            self.put_properties(e, template, name, values)
            self.written.update((e.id(), name, key) for key in values)
            self.touch(e)

    def op_set_classification(self, op):
        """A reference to a class of the system the person names, on the elements. A reference is reused only when its system, its
        code (Identification; ItemReference in IFC2X3) and its Location all match, and without a code its name too; otherwise it is
        made under that system's IfcClassification. (IfcOpenShell's add_reference reuses any reference with the same code in the
        file, whatever its system, and any reference without a code when none is given.) The system's own facts, when given (edition,
        source and dictionary_uri), go on its IfcClassification as Edition, Source and Location (IFC4) or Specification (IFC4.3); a
        system of the same name with another edition or address is another IfcClassification. Nothing is fetched: the facts come from
        the call. IFC2X3 requires Source and Edition, so an empty label stands for one not given, and its IfcClassification is not
        related to the project (IFC2X3 relates only references)."""
        import ifcopenshell.api.classification
        import ifcopenshell.guid

        system = str(op.get("system") or "").strip()
        name = str(op.get("name") or "").strip()
        if not system or not name:
            raise DataspatiumError("system and name are required")
        edition, source, address = (
            str(op.get(key) or "").strip() or None for key in ("edition", "source", "dictionary_uri")
        )
        address_attribute = {"IFC4": "Location", "IFC4X3": "Specification"}.get(self.m.schema)

        def same_system(c):
            here = getattr(c, address_attribute, None) if address_attribute else None
            return (
                str(c.Name or "") == system
                and not (edition and c.Edition and str(c.Edition) != edition)
                and not (address and here and str(here) != address)
            )

        classification = next((c for c in self.m.by_type("IfcClassification") if same_system(c)), None)
        if classification is None and self.m.schema == "IFC2X3":
            classification = self.m.create_entity(
                "IfcClassification", Source=source or "", Edition=edition or "", Name=system
            )
        elif classification is None:
            classification = ifcopenshell.api.classification.add_classification(self.m, classification=system)
        if edition and not classification.Edition:
            classification.Edition = edition
        if source and not classification.Source:
            classification.Source = source
        if address and address_attribute and not getattr(classification, address_attribute, None):
            setattr(classification, address_attribute, address)
        code = str(op.get("code") or "").strip() or None
        uri = str(op.get("uri") or "").strip() or None
        code_attribute = "ItemReference" if self.m.schema == "IFC2X3" else "Identification"

        def system_of(ref):
            source = ref.ReferencedSource
            for _ in range(16):  # a reference may hang under another reference of the same system
                if source is None or not source.is_a("IfcClassificationReference"):
                    break
                source = source.ReferencedSource
            return source

        # Without a code a class is known by its name alone, so the name must match as well.
        ref = next(
            (
                r
                for r in self.m.by_type("IfcClassificationReference")
                if system_of(r) == classification
                and (getattr(r, code_attribute, None) or None) == code
                and (r.Location or None) == uri
                and (code is not None or str(r.Name or "") == name)
            ),
            None,
        )
        if ref is None:
            ref = self.m.create_entity(
                "IfcClassificationReference",
                Location=uri,
                Name=name,
                ReferencedSource=classification,
                **{code_attribute: code},
            )
        elements = self.elements(op)
        rel = next(
            (r for r in self.m.by_type("IfcRelAssociatesClassification") if r.RelatingClassification == ref), None
        )
        if rel is None:
            rel = self.m.create_entity(
                "IfcRelAssociatesClassification",
                GlobalId=ifcopenshell.guid.new(),
                RelatedObjects=elements,
                RelatingClassification=ref,
            )
            if self.owner is not None:
                rel.OwnerHistory = self.owner
        else:
            rel.RelatedObjects = list(rel.RelatedObjects) + [e for e in elements if e not in rel.RelatedObjects]
        for e in elements:
            self.touch(e)

    def op_assign(self, op):
        import ifcopenshell.api.material

        storey = self.storey(op["storey"]) if op.get("storey") else None
        material = op.get("material")
        if storey is None and not material:
            raise DataspatiumError("give a storey, a material or both")
        category = self.material_category(op, material)
        for e in self.elements(op):
            if storey is not None:
                self.contain(e, storey)
            if material:
                mat = self.named_material(str(material), category)  # the file's material of the name, or a new one
                try:
                    ifcopenshell.api.material.unassign_material(self.m, products=[e])
                except Exception:  # nosec B110
                    pass
                ifcopenshell.api.material.assign_material(self.m, products=[e], type="IfcMaterial", material=mat)
            self.touch(e)

    def op_transform_elements(self, op):
        """Move elements by dx, dy, dz metres in their storey's plane (an opening's: its host's; without a storey the world's), turned
        into the frame each placement is relative to. A door or a window moves with its opening and an opening with its doors and
        windows (IfcRelFillsElement, IfcRelVoidsElement), and what is placed in another moved element follows it instead of moving
        twice. The site, the building and the storeys (IfcSpatialStructureElement other than IfcSpace) are refused: the site's place
        and the storeys' heights are the whole project's and change only by agreement. A placement's point is moved itself when nothing
        else uses it, else replaced, so that no point is left unused (bSI rule IFC106) and nothing else moves along; a moved wall's
        joins are undone and made anew where it meets other walls (unjoin, join_walls), and the spaces along it are fitted to the
        rooms the walls now enclose or noted (fit_spaces). An opening that moves without its wall must stay within the wall's body
        (opening_outside)."""
        dx, dy, dz = _cs_num(op, "dx", 0.0), _cs_num(op, "dy", 0.0), _cs_num(op, "dz", 0.0)
        moved = []
        for e in self.elements(op):
            if e.is_a("IfcSpatialStructureElement") and not e.is_a("IfcSpace"):
                raise DataspatiumError(
                    "%s #%d %r is not moved: the site's place and the storeys' heights are the whole project's and change only by agreement with the person; move the elements on it instead"
                    % (e.is_a(), e.id(), _ifc_text(getattr(e, "Name", None)) or "")
                )
            for x in self.with_fillings(e):
                if all(x.id() != y.id() for y in moved):
                    moved.append(x)
        for e in moved:
            if not self.movable(e):
                raise DataspatiumError("element %d has no local placement to move" % e.id())
        d = (self.u(dx), self.u(dy), self.u(dz))
        for e in moved:
            if not any(y.id() != e.id() and self.placed_in(e, y) for y in moved):
                if e.is_a("IfcWall"):
                    self.moving_wall(e)
                    self.unjoin(e)
                    for end in (0, 1):
                        self.draw_out(e, end)
                self.shift(e, self.plane_of(e), d)
            self.touch(e)
            for rel in getattr(e, "VoidsElements", None) or ():
                self.touch(rel.RelatingBuildingElement)  # the host's net quantities
        hosts = {e.id() for e in moved}
        for o in (e for e in moved if e.is_a("IfcOpeningElement")):
            for wall in (r.RelatingBuildingElement for r in (getattr(o, "VoidsElements", None) or ())):
                if wall.id() in hosts:
                    continue
                why = self.opening_outside(o, wall)
                if why:
                    raise DataspatiumError(
                        "opening #%d %r would %s; move it within the wall with edit_element (offset, sill), or move the wall with it"
                        % (o.id(), _ifc_text(o.Name) or "", why)
                    )
                self.slid.update(r.RelatedBuildingElement.id() for r in (getattr(o, "HasFillings", None) or ()))

    # -- moving and changing in place: placements, points and items that only the element uses
    @staticmethod
    def movable(e):
        """Whether an element has a local placement of a 3D axis placement relative to one the app can follow."""
        pl = getattr(e, "ObjectPlacement", None)
        return (
            pl is not None
            and pl.is_a("IfcLocalPlacement")
            and pl.RelativePlacement is not None
            and pl.RelativePlacement.is_a("IfcAxis2Placement3D")
            and _cs_placement(pl.PlacementRelTo) is not None
        )

    @staticmethod
    def with_fillings(e):
        """An element with what moves along with it: a door or a window with its opening and the opening's other doors and windows, an
        opening with its doors and windows; any other element alone."""
        openings = [r.RelatingOpeningElement for r in (getattr(e, "FillsVoids", None) or ())]
        if e.is_a("IfcOpeningElement"):
            openings.append(e)
        if not openings:
            return [e]
        out = []
        for o in openings:
            for x in [o] + [r.RelatedBuildingElement for r in (getattr(o, "HasFillings", None) or ())]:
                if all(x.id() != y.id() for y in out):
                    out.append(x)
        return out

    @staticmethod
    def placed_in(e, other):
        """Whether the placement of e is relative, directly or through others, to the placement of the element `other`."""
        target = getattr(other, "ObjectPlacement", None)
        p = getattr(e, "ObjectPlacement", None)
        for _ in range(64):
            p = p.PlacementRelTo if p is not None and p.is_a("IfcLocalPlacement") else None
            if p is None or target is None:
                return False
            if p.id() == target.id():
                return True
        return False

    def plane_of(self, e):
        """The absolute matrix of the plane a displacement of e is given in: its storey's (its container's), for an opening its host's,
        else the world's."""
        c = self.container(e)
        if c is None:
            host = next((r.RelatingBuildingElement for r in (getattr(e, "VoidsElements", None) or ())), None)
            c = self.container(host) if host is not None else None
        m = _cs_placement(getattr(c, "ObjectPlacement", None)) if c is not None else None
        return m if m is not None else _cs_axes(None)

    def shift(self, e, plane, d):
        """Move e's placement (movable) by d, a displacement in file units in the frame of the absolute matrix `plane`, turned into the
        frame its placement is relative to."""
        pl = e.ObjectPlacement
        local = _cs_turn(_cs_invert(_cs_placement(pl.PlacementRelTo)), _cs_turn(plane, d))
        pos = self.own(pl, "RelativePlacement")
        c = ([float(v) for v in pos.Location.Coordinates] + [0.0, 0.0, 0.0])[:3]
        self.set_point(pos, "Location", [round(a + b, 9) for a, b in zip(c, local)])

    def own(self, owner, attribute):
        """The instance in owner's `attribute`, copied first when anything else uses it too, so that a change to it changes owner alone."""
        inst = getattr(owner, attribute)
        if inst is not None and self.m.get_total_inverses(inst) > 1:
            import ifcopenshell.util.element

            inst = ifcopenshell.util.element.copy(self.m, inst)
            setattr(owner, attribute, inst)
        return inst

    def set_point(self, owner, attribute, coords):
        """Set the IfcCartesianPoint in owner's `attribute` to coords (file units): the point itself when nothing else uses it, else a
        new one, so that no point is left unused (bSI rule IFC106) and nothing else moves with it."""
        p = getattr(owner, attribute)
        coords = [float(c) for c in coords]
        if (
            p is not None
            and p.is_a("IfcCartesianPoint")
            and len(p.Coordinates) == len(coords)
            and self.m.get_total_inverses(p) == 1
        ):
            p.Coordinates = coords
        else:
            setattr(owner, attribute, self.point(*coords))

    def set_direction(self, owner, attribute, ratios, default=None):
        """Set the IfcDirection in owner's `attribute` to ratios like set_point; an attribute left out stays out while the ratios are its
        default (None: it is always written)."""
        d = getattr(owner, attribute)
        ratios = [float(c) for c in ratios]
        now = (
            [float(c) for c in d.DirectionRatios] if d is not None else (list(default) if default is not None else None)
        )
        if now is not None and len(now) == len(ratios) and all(abs(a - b) < 1e-12 for a, b in zip(now, ratios)):
            return
        if d is not None and len(d.DirectionRatios) == len(ratios) and self.m.get_total_inverses(d) == 1:
            d.DirectionRatios = ratios
        else:
            setattr(owner, attribute, self.direction(*ratios))

    def drop(self, inst):
        """Remove an instance that nothing uses any more, with what only it uses (remove_deep2), so that no resource is left without a
        rooted entity (bSI rule IFC106)."""
        if inst is not None and self.m.get_total_inverses(inst) == 0:
            import ifcopenshell.util.element

            ifcopenshell.util.element.remove_deep2(self.m, inst)

    def replace_item(self, rep, old, new):
        """Put the representation item new in old's place in rep, with old's styles and presentation layers, and drop old."""
        rep.Items = [new if i.id() == old.id() else i for i in (rep.Items or ())]
        for r in list(self.m.get_inverse(old)):
            if r.is_a("IfcStyledItem"):
                r.Item = new
            elif r.is_a("IfcPresentationLayerAssignment"):
                r.AssignedItems = [new if i.id() == old.id() else i for i in (r.AssignedItems or ())]
        self.drop(old)

    # -- joins undone, for walls that change or move
    def path_joins(self, w):
        """A wall's IfcRelConnectsPathElements with other walls: (relationship, the wall's connection type, the other wall, its type)."""
        out = []
        for attribute, mine, theirs in (
            ("ConnectedTo", "Relating", "Related"),
            ("ConnectedFrom", "Related", "Relating"),
        ):
            for rel in getattr(w, attribute, None) or ():
                other = getattr(rel, theirs + "Element", None) if rel.is_a("IfcRelConnectsPathElements") else None
                if other is not None and other.is_a("IfcWall"):
                    out.append(
                        (
                            rel,
                            getattr(rel, mine + "ConnectionType", None),
                            other,
                            getattr(rel, theirs + "ConnectionType", None),
                        )
                    )
        return out

    def unjoin(self, wall):
        """Undo a wall's joins before its axis or thickness changes or it moves, so that join_walls makes them anew where the walls still
        meet (rejoin): every IfcRelConnectsPathElements between it and another wall goes, and where the other wall joined it with an end
        (ATSTART or ATEND), that end's other joins go as well and its body reaches its axis's end again (draw_out)."""
        todo, seen = [(wall, None)], set()
        while todo:
            w, end = todo.pop()
            if (w.id(), end) in seen:
                continue
            seen.add((w.id(), end))
            self.rejoin.add(w.id())
            for rel, mine, other, theirs in self.path_joins(w):
                if end is not None and mine != self.end_type(end):
                    continue
                self.m.remove(rel)
                if theirs in ("ATSTART", "ATEND"):
                    todo.append((other, 0 if theirs == "ATSTART" else 1))
            if end is not None:
                self.draw_out(w, end)

    def draw_out(self, w, end):
        """Let the end of a wall's body (0 on its axis's start's side, 1 on its end's) reach its axis's end again, as before a join cut
        it; nothing for a body of another form than create_wall's."""
        j = self.join_wall(w, False)
        if j is not None and j["box"] is not None and self.cut_end(j, end, j["a"] if end == 0 else j["b"]):
            self.touch(w)

    def moving_wall(self, wall):
        """Keep a wall's axis in the world's plan (metres) and its thickness as they were before the change set first moved or resized
        it, for fit_spaces."""
        if wall.id() not in self.moved:
            try:
                self.moved[wall.id()] = (_topo_axis(wall, self.scale), _topo_thickness(wall, self.scale))
            except Exception:
                self.moved[wall.id()] = (None, None)

    def opening_outside(self, opening, wall):
        """How an opening of create_opening's form (opening_box) reaches out of its wall's body of create_wall's form (wall_box): past
        its ends, above its top or below its base, or off its thickness; None when it lies within, or when either is of another form."""
        body, item = self.wall_box(wall), self.wall_box(opening)
        box = self.opening_box(opening, wall) if body is not None and item is not None else None
        if box is None:
            return None
        s, tol, p, q = self.scale, self.u(0.001), body.SweptArea, item.SweptArea
        cx, cy = [float(c) for c in p.Position.Location.Coordinates][:2]
        x0, x1, z0 = cx - float(p.XDim) / 2.0, cx + float(p.XDim) / 2.0, _cs_axes(body.Position)[3][2]
        rel = _cs_compose(_cs_invert(_cs_placement(wall.ObjectPlacement)), _cs_placement(opening.ObjectPlacement))
        oy = rel[3][1] + float(q.Position.Location.Coordinates[1])
        label = "wall #%d %r" % (wall.id(), _ifc_text(wall.Name) or "")
        if box[0] < x0 - tol or box[1] > x1 + tol:
            return "reach past the ends of %s (%.4g to %.4g m along it, whose body runs from %.4g to %.4g m)" % (
                label,
                box[0] * s,
                box[1] * s,
                x0 * s,
                x1 * s,
            )
        if box[2] < z0 - tol or box[3] > z0 + float(body.Depth) + tol:
            return "reach above or below %s (%.4g to %.4g m up in a body from %.4g to %.4g m)" % (
                label,
                box[2] * s,
                box[3] * s,
                z0 * s,
                (z0 + float(body.Depth)) * s,
            )
        if (
            oy - float(q.YDim) / 2.0 > cy - float(p.YDim) / 2.0 + tol
            or oy + float(q.YDim) / 2.0 < cy + float(p.YDim) / 2.0 - tol
        ):
            return "no longer go through %s, %.4g m off its axis" % (label, (oy - cy) * s)
        return None

    def check_openings(self):
        """After the joins: a note for each opening of the walls whose joins the change set undid that a join's cut leaves reaching out
        of its wall's body (opening_outside)."""
        for i in sorted(self.rejoin):
            try:
                wall = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            for o in (r.RelatedOpeningElement for r in (getattr(wall, "HasOpenings", None) or ())):
                why = self.opening_outside(o, wall)
                if why:
                    self.note_once(
                        "opening %s would %s now that the wall is joined; move it with edit_element (offset, width), or ask the person"
                        % (self.element_label(o), why)
                    )

    # -- the spaces along walls that moved (the room code of create_spaces, on the topology after the change set)
    def fit_spaces(self):
        """After the joins: the spaces along the walls the change set moved or resized in the plan (self.moved), per storey
        (fit_storey), and the spaces along the walls in which doors and windows moved (refill)."""
        storeys = {}
        for i, (axis, thickness) in sorted(self.moved.items()):
            try:
                w = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            new, now = _topo_axis(w, self.scale), _topo_thickness(w, self.scale)
            if not axis or not new:
                if getattr(w, "ProvidesBoundaries", None):
                    self.note_once(
                        "wall %s bounds spaces whose outlines stay as they were: the app cannot follow its axis"
                        % self.element_label(w)
                    )
                continue
            shifted = len(axis) != len(new) or any(math.dist(a, b) > 1e-6 for a, b in zip(axis, new))
            resized = abs(float(now or 0.0) - float(thickness or 0.0)) > 1e-9
            s = self.container(w)
            if (shifted or resized) and s is not None and s.is_a("IfcBuildingStorey"):
                storeys.setdefault(s.id(), (s, {}))[1][w.id()] = (axis, new, resized)
        for s, walls in storeys.values():
            self.fit_storey(s, walls)
        done = {i for _, walls in storeys.values() for i in walls}
        for i in sorted(self.slid):
            try:
                f = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            self.refill(f, done)

    def fit_storey(self, s, walls):
        """The spaces of storey s along its walls that moved, walls {id: (old axis, new axis, resized)}: a space their old or new axis
        runs along, or that a boundary relates to one of them, is fitted to the room the walls now enclose, as create_spaces makes it
        (fit_space), when it is drawn as create_spaces draws it (space_body), no other such space overlaps the same room most, and
        its outline differs from the room only within the topology's tolerance of the walls' old and new axes; else it keeps its
        outline and quantities with a note saying why, and loses its boundaries to the moved walls that no longer run along it
        (loosen)."""
        from shapely.geometry import LineString, MultiPoint, Polygon
        from shapely.ops import unary_union

        spaces = [
            o
            for rel in (getattr(s, "IsDecomposedBy", None) or ())
            for o in (rel.RelatedObjects or ())
            if o.is_a("IfcSpace")
        ]
        topo = (
            next(
                (
                    t
                    for t in _ifc_topology(self.m, storeys={_ifc_text(s.Name)}, shapes=True)
                    if t["_storey"].id() == s.id()
                ),
                None,
            )
            if spaces
            else None
        )
        if topo is None:
            return
        tol = topo["tolerance"]
        meets = 2.0 * tol + 0.01  # as in _topo_plan: a wall that only meets an edge runs at most 2 * tol along it
        lines = {i: (LineString(old), LineString(new)) for i, (old, new, _) in walls.items()}
        zone = unary_union(
            [MultiPoint(list(old) + list(new)).convex_hull.buffer(tol) for old, new, _ in walls.values()]
        )
        affected = []
        for p in spaces:
            outline = self.space_outline(p)
            bounded = {
                b.RelatedBuildingElement.id()
                for b in (getattr(p, "BoundedBy", None) or ())
                if getattr(b, "RelatedBuildingElement", None) is not None
            }
            edge = outline.boundary.buffer(tol) if outline is not None else None
            if bounded & set(walls) or (
                edge is not None and any(self.runs_along(x, edge, meets) for pair in lines.values() for x in pair)
            ):
                affected.append((p, outline))
        rooms = topo["rooms"]
        best = {}
        for p, outline in affected:
            top = (
                max(((outline.intersection(r["polygon"]).area, k) for k, r in enumerate(rooms)), default=(0.0, None))
                if outline is not None
                else (0.0, None)
            )
            best[p.id()] = top[1] if top[0] > 1e-6 else None
        for p, outline in affected:
            k, body = best[p.id()], self.space_body(p)
            others = [q for q, _ in affected if k is not None and q.id() != p.id() and best[q.id()] == k]
            why = None
            if body is None or outline is None:
                why = "it is not drawn as create_spaces draws it"
            elif k is None:
                why = "the walls no longer enclose a room there"
            elif others:
                why = "it now lies in one room with space %s" % ", ".join(self.element_label(q) for q in others)
            elif outline.symmetric_difference(Polygon(rooms[k]["polygon"].exterior)).difference(zone).area > 1e-4:
                why = "its outline does not follow the walls' axes where they stayed, as create_spaces draws it"
            if why is None:
                self.fit_space(
                    p, body, outline, rooms[k], topo, any(walls[i][2] for i in rooms[k]["walls"] if i in walls)
                )
            else:
                self.loosen(p, outline, walls, lines, tol, meets, why)

    @staticmethod
    def runs_along(line, zone, meets):
        """Whether a wall's axis (shapely) runs within zone, the buffer of a space's edge, for more than meets metres in one piece; a
        wall that crosses the edge, or only meets it, does not."""
        part = line.intersection(zone)
        return max([g.length for g in getattr(part, "geoms", [part])] or [0.0]) > meets

    def space_body(self, space):
        """(the extruded solid, the absolute matrix of its profile's plane) of a space drawn as create_spaces draws it: one
        IfcExtrudedAreaSolid of an arbitrary closed profile of polylines (holes too), extruded straight up in an upright frame; else
        None."""
        rep = getattr(space, "Representation", None)
        bodies = [
            r for r in (rep.Representations if rep is not None else ()) if (r.RepresentationIdentifier or "") == "Body"
        ]
        items = list(bodies[0].Items or ()) if len(bodies) == 1 else []
        if (
            len(items) != 1
            or not items[0].is_a("IfcExtrudedAreaSolid")
            or len(getattr(rep, "ShapeOfProduct", None) or ()) > 1
        ):
            return None
        item, m = items[0], _cs_placement(getattr(space, "ObjectPlacement", None))
        p, d = item.SweptArea, _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
        curves = [getattr(p, "OuterCurve", None)] + list(getattr(p, "InnerCurves", None) or ())
        if (
            not p.is_a("IfcArbitraryClosedProfileDef")
            or any(c is None or not c.is_a("IfcPolyline") for c in curves)
            or m is None
            or d is None
        ):
            return None
        frame = _cs_compose(m, _cs_axes(item.Position))
        return (item, frame) if abs(d[2] - 1.0) < 1e-9 and abs(frame[2][2] - 1.0) < 1e-6 else None

    def room_profile(self, frame, poly, name=None):
        """A room's outline (shapely, metres in the world's plan) as an arbitrary closed profile, with its holes as voids, in the plane
        of the absolute matrix `frame`, as make_space draws it."""
        back = _cs_invert(frame)

        def curve(ring):
            return self.closed(
                [_cs_at(back, (c[0] / self.scale, c[1] / self.scale, frame[3][2]))[:2] for c in ring.coords]
            )

        if poly.interiors:
            return self.m.createIfcArbitraryProfileDefWithVoids(
                "AREA", name, curve(poly.exterior), [curve(r) for r in poly.interiors]
            )
        return self.m.createIfcArbitraryClosedProfileDef("AREA", name, curve(poly.exterior))

    @staticmethod
    def space_values(room, height):
        """Qto_SpaceBaseQuantities of a room of the topology `height` metres high, as space_quantities gives them, for write_qto."""
        gross, net = room["polygon"].area, room["net_area"]
        return {
            "Height": ("length", height),
            "GrossPerimeter": ("length", room["polygon"].length),
            "GrossFloorArea": ("area", gross),
            "NetFloorArea": ("area", net),
            "GrossVolume": ("volume", gross * height),
            "NetVolume": ("volume", net * height),
        }

    def fit_space(self, space, body, outline, room, topo, resized):
        """A space fitted to its room: its profile drawn anew when its outline differs (with a note), its boundaries made anew when they
        differ (rebound), and its quantities when either changed or a wall along it changed its thickness; the space keeps its
        GlobalId, its height and everything else."""
        item, frame = body
        poly = room["polygon"]
        from shapely.geometry import Polygon

        reshaped = outline.symmetric_difference(Polygon(poly.exterior)).area > 1e-4 or bool(poly.interiors) != bool(
            getattr(item.SweptArea, "InnerCurves", None)
        )
        if reshaped:
            old = item.SweptArea
            item.SweptArea = self.room_profile(frame, poly, old.ProfileName)
            self.drop(old)
            self.notes.append(
                "space %s was fitted to the room its walls now enclose: %.2f m² instead of %.2f m²"
                % (self.element_label(space), poly.area, outline.area)
            )
        if self.rebound(space, room, topo, reshaped) or reshaped or resized:
            self.write_qto(space, "Qto_SpaceBaseQuantities", self.space_values(room, float(item.Depth) * self.scale))
            self.touch(space)

    @staticmethod
    def bounds_wall(b):
        """Whether a space boundary relates its space to a wall, a door or a window, as create_spaces makes them."""
        e = getattr(b, "RelatedBuildingElement", None) if b.is_a("IfcRelSpaceBoundary") else None
        return e is not None and (e.is_a("IfcWall") or e.is_a("IfcDoor") or e.is_a("IfcWindow"))

    def unbound(self, b):
        """Remove a space boundary with its connection geometry, when nothing else uses that (the one definition: the boundaries
        after a move and the faces of the space boundaries both use it)."""
        geometry = getattr(b, "ConnectionGeometry", None)
        self.m.remove(b)
        self.drop(geometry)

    def rebound(self, space, room, topo, force=False):
        """A fitted space's boundaries to walls, doors and windows as create_spaces makes them: to the room's walls (external where the
        topology finds the wall external) and to the doors and windows in them whose middle stands on the room's edge. Made anew when
        they differ from the space's (or with force), and then True."""
        external = {x["wall"] for x in topo["external"]}
        want = []
        for wall in (self.m.by_id(i) for i in room["walls"]):
            want.append((wall, wall.id() in external))
            for f in self.fillings(wall):
                mid = self.plan_point(f, float(getattr(f, "OverallWidth", None) or 0.0) / 2.0)
                if mid is not None and room["polygon"].boundary.distance(_topo_point(mid)) <= topo["tolerance"]:
                    want.append((f, wall.id() in external))
        have = [b for b in (getattr(space, "BoundedBy", None) or ()) if self.bounds_wall(b)]
        if not force and sorted(
            (b.RelatedBuildingElement.id(), b.InternalOrExternalBoundary == "EXTERNAL") for b in have
        ) == sorted((e.id(), x) for e, x in want):
            return False
        for b in have:
            self.unbound(b)
        for e, x in want:
            self.bound(space, e, x)
        return True

    def loosen(self, space, outline, walls, lines, tol, meets, why):
        """A space the moved walls no longer fit keeps its outline and quantities, with a note: its boundaries to the moved walls whose
        new axis no longer runs along its edge go, and those to the doors and windows in the moved walls whose middle no longer stands
        on it."""
        lost = []
        for b in (
            [b for b in (getattr(space, "BoundedBy", None) or ()) if self.bounds_wall(b)] if outline is not None else ()
        ):
            e = b.RelatedBuildingElement
            host = e if e.is_a("IfcWall") else self.host_of(e)
            if host is None or host.id() not in walls:
                continue
            if e.is_a("IfcWall"):
                gone = not self.runs_along(lines[e.id()][1], outline.boundary.buffer(tol), meets)
            else:
                mid = self.plan_point(e, float(getattr(e, "OverallWidth", None) or 0.0) / 2.0)
                gone = mid is None or outline.boundary.distance(_topo_point(mid)) > tol
            if gone:
                lost.append(self.element_label(e))
                self.unbound(b)
        self.notes.append(
            "space %s keeps its outline and quantities as they were, since %s%s; the person decides whether to move the walls so that they enclose it again, or to delete it and make it anew with create_spaces"
            % (
                self.element_label(space),
                why,
                "; its boundaries to %s went, as they no longer run along it" % ", ".join(lost) if lost else "",
            )
        )
        if lost:
            self.touch(space)

    def refill(self, f, done):
        """A door or window that moved along a wall the change set did not move (done: the walls fit_storey followed) bounds the wall's
        spaces whose edge its middle stands on, as create_opening gives it, and no others."""
        wall = self.host_of(f)
        if wall is None or wall.id() in done or not (f.is_a("IfcDoor") or f.is_a("IfcWindow")):
            return
        mid = self.plan_point(f, float(getattr(f, "OverallWidth", None) or 0.0) / 2.0)
        tol = self.wall_thickness(wall) * self.scale / 2.0 + 0.05
        have = {b.RelatingSpace.id(): b for b in (getattr(f, "ProvidesBoundaries", None) or ())}
        for b in list(getattr(wall, "ProvidesBoundaries", None) or ()):
            space = b.RelatingSpace
            outline = self.space_outline(space) if space is not None and space.is_a("IfcSpace") else None
            if outline is None:
                continue
            on = mid is not None and outline.boundary.distance(_topo_point(mid)) <= tol
            if on and space.id() not in have:
                have[space.id()] = self.bound(space, f, b.InternalOrExternalBoundary == "EXTERNAL")
                self.touch(space)
            elif not on and space.id() in have:
                self.unbound(have.pop(space.id()))
                self.touch(space)

    # -- edit_element (recommendation 7 of the Praxi study, 17 of the native IFC study)
    EDITS = {
        "wall": ("start", "end", "thickness", "height", "type"),
        "slab": ("outline", "thickness", "type"),
        "opening": ("offset", "width", "height", "sill"),
        "roof": ("pitch", "overhang", "base"),
    }
    EDIT_FIELDS = (
        "name",
        "start",
        "end",
        "thickness",
        "height",
        "type",
        "outline",
        "offset",
        "width",
        "sill",
        "pitch",
        "overhang",
        "base",
    )

    def op_edit_element(self, op):
        """Change an existing element in place and keep its GlobalId. The fields given (not null) must be name or those of the element's
        kind (EDITS): the measures go to edit_wall, edit_slab, edit_opening or edit_roof, which read the element's parameters from its
        representation and refuse one of another form with what to do instead. The element and what changed with it are in the changed
        list, so that finish gives them their base quantities."""
        e = self.entity(op.get("id"))
        fields = [
            k
            for k in self.EDIT_FIELDS
            if op.get(k) is not None and not (isinstance(op.get(k), str) and not op.get(k).strip())
        ]
        if not fields:
            raise DataspatiumError("nothing to change: give the name or the measures to change, and null for the rest")
        kind = self.edit_kind(e)
        measures = [k for k in fields if k != "name"]
        wrong = [k for k in measures if k not in self.EDITS.get(kind, ())]
        if wrong:
            raise DataspatiumError(self.edit_refusal(e, kind, wrong))
        if "name" in fields and not e.is_a("IfcRoot"):
            raise DataspatiumError("%s #%d has no name to change" % (e.is_a(), e.id()))
        if measures:
            getattr(self, "edit_" + kind)(e, op)
        if "name" in fields:
            e.Name = str(op["name"]).strip()[:255]
            for rec in self.created:
                if rec["id"] == e.id():
                    rec["name"] = _ifc_text(e.Name)
        self.touch(e)

    def edit_kind(self, e):
        """wall, slab, opening (also for a door or a window), roof, "roof slab" for a slab of a roof, or None."""
        if e.is_a("IfcWall"):
            return "wall"
        if e.is_a("IfcRoof"):
            return "roof"
        if e.is_a("IfcOpeningElement") or e.is_a("IfcDoor") or e.is_a("IfcWindow"):
            return "opening"
        if e.is_a("IfcSlab"):
            return "roof slab" if self.roof_of(e) is not None else "slab"
        return None

    @staticmethod
    def roof_of(slab):
        return next(
            (r.RelatingObject for r in (getattr(slab, "Decomposes", None) or ()) if r.RelatingObject.is_a("IfcRoof")),
            None,
        )

    def edit_refusal(self, e, kind, wrong):
        """Why edit_element does not take these fields for this element, and what the AI can do instead."""
        what = "%s #%d %r" % (e.is_a(), e.id(), _ifc_text(getattr(e, "Name", None)) or "")
        if kind == "roof slab":
            roof = self.roof_of(e)
            return (
                "%s is a slab of roof #%d %r: edit the roof with pitch, overhang or base; the slab itself takes only name"
                % (what, roof.id(), _ifc_text(roof.Name) or "")
            )
        if kind is None:
            return (
                "%s takes only name in edit_element (%s are for walls, slabs, openings with their doors and windows, and roofs); move it with transform_elements, and delete it and create it anew only when the person asks"
                % (what, ", ".join(wrong))
            )
        return "%s takes %s or name, not %s" % (what, ", ".join(self.EDITS[kind]), ", ".join(wrong))

    def cannot(self, e, why, tool):
        """The refusal of measures for an element that is not of the write tools' form."""
        return DataspatiumError(
            "%s #%d %r cannot be changed in place: %s. edit_element can still change its name and transform_elements move it; delete it and draw it anew with %s only when the person asks"
            % (e.is_a(), e.id(), _ifc_text(getattr(e, "Name", None)) or "", why, tool)
        )

    def layers_of(self, e):
        """(total thickness in metres, what gives it) of the material layers an element's own layer set usage or its type gives it;
        None without."""
        import ifcopenshell.util.element as el

        own = [
            r.RelatingMaterial.ForLayerSet
            for r in (getattr(e, "HasAssociations", None) or ())
            if r.is_a("IfcRelAssociatesMaterial") and r.RelatingMaterial.is_a("IfcMaterialLayerSetUsage")
        ]
        t = el.get_type(e)
        layers = own[0] if own else (self.type_layers(t) if t is not None else None)
        if layers is None:
            return None
        total = round(sum(float(x.LayerThickness or 0.0) for x in (layers.MaterialLayers or ())) * self.scale, 9)
        return total, ("type %r" % _ifc_text(t.Name)) if t is not None else (
            "layer set %r" % _ifc_text(layers.LayerSetName)
        )

    def edit_thickness(self, e, op, kind, current):
        """(thickness in metres, the new type or None) of an edited wall or slab: with a type (with_type) its layers', which a thickness
        given must equal, and the type unless the element has it already; else, when its layers fix it, its own, which a thickness given
        must equal; else the thickness given or the current one."""
        import ifcopenshell.util.element as el

        if op.get("type") not in (None, ""):
            typed, t = self.with_type({"type": op["type"], "thickness": op.get("thickness")}, kind)
            old = el.get_type(e)
            return typed["thickness"], (None if old is not None and old.id() == t.id() else t)
        fixed = self.layers_of(e)
        if fixed is not None and fixed[0] > 0:
            if op.get("thickness") is not None and abs(_cs_num(op, "thickness", positive=True) - fixed[0]) > 0.0005:
                raise DataspatiumError(
                    "the thickness of %s #%d comes from its %s (%g m); to change it give another %s type as type (define_type makes one)"
                    % (e.is_a(), e.id(), fixed[1], fixed[0], kind)
                )
            return current, None
        return (_cs_num(op, "thickness", positive=True) if op.get("thickness") is not None else current), None

    def untype(self, e):
        """Take an occurrence off its type before give_type gives it another: out of the type's IfcRelDefinesByType (which goes when it
        relates nothing more), and its own material association removed, with a layer set usage or a material that nothing else uses."""
        rels = list(getattr(e, "IsTypedBy", None) or ()) + [
            r for r in (getattr(e, "IsDefinedBy", None) or ()) if r.is_a("IfcRelDefinesByType")
        ]
        for rel in {r.id(): r for r in rels}.values():
            rest = [o for o in (rel.RelatedObjects or ()) if o.id() != e.id()]
            if rest:
                rel.RelatedObjects = rest
            else:
                self.m.remove(rel)
        for rel in [r for r in (getattr(e, "HasAssociations", None) or ()) if r.is_a("IfcRelAssociatesMaterial")]:
            material = rel.RelatingMaterial
            rest = [o for o in (rel.RelatedObjects or ()) if o.id() != e.id()]
            if rest:
                rel.RelatedObjects = rest
                continue
            self.m.remove(rel)
            if (
                material is not None
                and (material.is_a("IfcMaterialLayerSetUsage") or material.is_a("IfcMaterial"))
                and self.m.get_total_inverses(material) == 0
            ):
                self.m.remove(material)  # not deep: a layer set stays with its type

    def wall_frame(self, wall):
        """A wall of create_wall's form as edit_element reads it: its storey and the storey's absolute matrix S, its placement relative
        to the storey (rel), its Axis (representation, item and two points along its x, file units), its body (wall_box), its start and
        end in the storey's plane (metres), the storey-plane height of its axis (z, file units), its body's centre off the axis (off,
        file units), its thickness and height (metres) and its body's base (file units); else the refusal of cannot."""
        s = self.scale
        storey = self.container(wall)
        S = _cs_placement(storey.ObjectPlacement) if storey is not None else None
        W = _cs_placement(getattr(wall, "ObjectPlacement", None)) if self.movable(wall) else None
        rep = getattr(wall, "Representation", None)
        axis_rep, axis_item = next(
            (
                (r, i)
                for r in (rep.Representations if rep is not None else ())
                if (r.RepresentationIdentifier or "") == "Axis"
                for i in (r.Items or ())
            ),
            (None, None),
        )
        line = _topo_curve(axis_item) if axis_item is not None else None
        box = self.wall_box(wall)
        rel = _cs_compose(_cs_invert(S), W) if S is not None and W is not None else None
        why = None
        if rel is None:
            why = "it is in no storey, or its placement cannot be read"
        elif (
            not line
            or len(line) != 2
            or abs(line[0][1] - line[1][1]) > self.u(1e-6)
            or abs(line[1][0] - line[0][0]) < self.u(0.01)
        ):
            why = "it has no straight Axis of two points along its x"
        elif box is None:
            why = "its body is not one extruded rectangle along its axis"
        elif len(getattr(rep, "ShapeOfProduct", None) or ()) > 1:
            why = "it shares its shape with other elements"
        elif abs(rel[2][2] - 1.0) > 1e-6:
            why = "it does not stand upright in its storey"
        if why:
            raise self.cannot(wall, why, "create_wall")
        a, b = _cs_at(rel, (line[0][0], line[0][1], 0.0)), _cs_at(rel, (line[1][0], line[1][1], 0.0))  # ty: ignore[not-subscriptable]
        p = box.SweptArea
        return {
            "storey": storey,
            "S": S,
            "rel": rel,
            "axis_rep": axis_rep,
            "axis_item": axis_item,
            "line": line,
            "box": box,
            "start": (a[0] * s, a[1] * s),
            "end": (b[0] * s, b[1] * s),
            "z": a[2],
            "off": float(p.Position.Location.Coordinates[1]) - line[0][1],  # ty: ignore[not-subscriptable]
            "thickness": float(p.YDim) * s,
            "height": float(box.Depth) * s,
            "base": _cs_axes(box.Position)[3][2],
        }

    def edit_wall(self, wall, op):
        """edit_element on a wall (wall_frame). start and end place it at the new start along the new axis and draw its Axis and body
        anew from start to end; thickness and height size its body. Its openings keep their place along it when its direction stays the
        same, else their offset from its start, get its new thickness and must stay within it. A new type types it with the layers
        centred as create_wall lays them; a typed wall takes its thickness from its layers. Its joins are undone and made anew at the
        end (unjoin, join_walls), its openings are checked against its body once it is joined (check_openings), and the spaces along
        it are fitted to the rooms the walls now enclose or noted (fit_spaces)."""
        s = self.scale
        f = self.wall_frame(wall)
        start = _cs_xy(op["start"], "start") if op.get("start") is not None else f["start"]
        end = _cs_xy(op["end"], "end") if op.get("end") is not None else f["end"]
        length = math.dist(start, end)
        if length < 0.01:
            raise DataspatiumError("start and end are the same point")
        thickness, typ = self.edit_thickness(wall, op, "wall", f["thickness"])
        height = _cs_num(op, "height", positive=True) if op.get("height") is not None else f["height"]
        moving = math.dist(start, f["start"]) > 1e-9 or math.dist(end, f["end"]) > 1e-9
        resizing = abs(thickness - f["thickness"]) > 1e-9
        line = f["line"]
        u = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
        was = math.dist(f["start"], f["end"])
        same = (
            was > 0 and ((f["end"][0] - f["start"][0]) * u[0] + (f["end"][1] - f["start"][1]) * u[1]) / was > 1.0 - 1e-9
        )
        new_line = [(0.0, 0.0), (self.u(length), 0.0)] if moving else line
        lo, hi = min(new_line[0][0], new_line[1][0]), max(new_line[0][0], new_line[1][0])
        top, tol = f["base"] + self.u(height), self.u(0.001)
        plan = []
        for rel in getattr(wall, "HasOpenings", None) or ():
            o = rel.RelatedOpeningElement
            box, item, opl = self.opening_box(o, wall), self.wall_box(o), getattr(o, "ObjectPlacement", None)
            inside = (
                self.movable(o)
                and opl.PlacementRelTo is not None  # ty: ignore[unresolved-attribute]
                and opl.PlacementRelTo.id() == wall.ObjectPlacement.id()  # ty: ignore[unresolved-attribute]
            )
            label = "opening #%d %r" % (o.id(), _ifc_text(o.Name) or "")
            if box is None or item is None or not inside:
                if moving or resizing:
                    raise DataspatiumError(
                        "%s in wall #%d is not drawn and placed as create_opening does, so the wall's start, end and thickness cannot change with it; change its height or name, or ask the person what to do with the opening"
                        % (label, wall.id())
                    )
                continue
            ox, oy, oz = ([float(c) for c in opl.RelativePlacement.Location.Coordinates] + [0.0, 0.0, 0.0])[:3]  # ty: ignore[unresolved-attribute]
            nx, ny = ox, oy
            if moving and same:  # its place along the line stays
                q = _cs_at(f["rel"], (ox, oy, oz))
                nx, ny = (q[0] - self.u(start[0])) * u[0] + (q[1] - self.u(start[1])) * u[1], oy - line[0][1]
            elif moving:  # its offset from the start stays
                nx, ny = ox - line[0][0], oy - line[0][1]
            x0, x1 = nx + box[0] - ox, nx + box[1] - ox
            if x0 < lo - tol or x1 > hi + tol or box[3] > top + tol:
                raise DataspatiumError(
                    "%s would reach beyond wall #%d (%.3g to %.3g m along a wall of %.3g m, its top %.3g m up in a wall %.3g m high); first move or resize it with edit_element (offset, width, height, sill), or ask the person whether to delete it"
                    % (label, wall.id(), (x0 - lo) * s, (x1 - lo) * s, (hi - lo) * s, (box[3] - f["base"]) * s, height)
                )
            plan.append((o, item, (nx, ny, oz), _cs_placement(opl), self.loose_fillings(o) if moving else []))
        if moving or resizing:
            self.moving_wall(wall)
            self.unjoin(wall)
        if moving:
            self.place_wall(wall, f, start, u)
            self.set_axis(f, new_line)
        if moving or resizing:
            p = self.own(f["box"], "SweptArea")
            p.XDim = round(hi - lo, 9)
            p.YDim = round(self.u(thickness), 9)
            self.set_point(
                self.own(p, "Position"), "Location", (round((lo + hi) / 2.0, 9), round(new_line[0][1] + f["off"], 9))
            )
        f["box"].Depth = round(self.u(height), 9)
        for o, item, at, before, loose in plan:
            if moving:
                self.set_point(self.own(o.ObjectPlacement, "RelativePlacement"), "Location", [round(c, 9) for c in at])
                self.follow(o, before, loose)
            if resizing:
                q = self.own(item, "SweptArea")
                q.YDim = round(float(q.YDim) + self.u(thickness) - self.u(f["thickness"]), 9)
            if moving or resizing:
                self.touch(o)
        if typ is not None:
            self.untype(wall)
            self.give_type(wall, typ, "AXIS2", "POSITIVE", f["off"] * s - thickness / 2.0)
        self.touch(wall)

    def place_wall(self, wall, f, start, u):
        """Set a wall's placement at start (metres in its storey's plane, at the height its axis had) with its x along u, relative to the
        placement it was relative to."""
        pl = wall.ObjectPlacement
        target = [[u[0], u[1], 0.0], [-u[1], u[0], 0.0], [0.0, 0.0, 1.0], [self.u(start[0]), self.u(start[1]), f["z"]]]
        self.set_local(pl, _cs_compose(_cs_invert(_cs_placement(pl.PlacementRelTo)), _cs_compose(f["S"], target)))

    def set_local(self, pl, local):
        """Write the matrix `local` (relative to the placement's PlacementRelTo, file units) into an IfcLocalPlacement's own axis
        placement: its point and directions in place when nothing else uses them, else new ones."""
        pos = self.own(pl, "RelativePlacement")
        self.set_point(pos, "Location", [round(c, 9) for c in local[3]])
        axis, ref = [round(c, 12) for c in local[2]], [round(c, 12) for c in local[0]]
        # Axis and RefDirection are both given or both left out (IfcAxis2Placement3D's rule AxisAndRefDirProvision).
        both = pos.Axis is not None or pos.RefDirection is not None or axis != [0.0, 0.0, 1.0] or ref != [1.0, 0.0, 0.0]
        self.set_direction(pos, "Axis", axis, None if both else (0.0, 0.0, 1.0))
        self.set_direction(pos, "RefDirection", ref, None if both else (1.0, 0.0, 0.0))

    def loose_fillings(self, opening):
        """The doors and windows of an opening that are not placed in it, with their absolute matrices, for follow; a refusal for one
        without a local placement to move."""
        out = []
        for x in [r.RelatedBuildingElement for r in (getattr(opening, "HasFillings", None) or ())]:
            if not self.placed_in(x, opening):
                if not self.movable(x):
                    raise self.cannot(
                        x,
                        "it is not placed in its opening and has no local placement to move with it",
                        "create_opening",
                    )
                out.append((x, _cs_placement(x.ObjectPlacement)))
        return out

    def follow(self, opening, before, loose):
        """Move the doors and windows of loose_fillings as the opening moved from the absolute matrix `before`."""
        turn = _cs_compose(_cs_placement(opening.ObjectPlacement), _cs_invert(before))
        for x, m in loose:
            self.set_local(
                x.ObjectPlacement,
                _cs_compose(_cs_invert(_cs_placement(x.ObjectPlacement.PlacementRelTo)), _cs_compose(turn, m)),
            )
            self.touch(x)

    def set_axis(self, f, line):
        """Draw a wall's Axis (wall_frame) anew as a polyline through two points of its own coordinates (file units)."""
        rep, item = f["axis_rep"], f["axis_item"]
        if not (item.is_a("IfcPolyline") and len(item.Points) == 2 and self.m.get_total_inverses(item) == 1):
            self.replace_item(rep, item, self.m.createIfcPolyline([self.point(*q) for q in line]))
            return
        pts = []
        for p, q in zip(item.Points, line):
            if len(p.Coordinates) == 2 and self.m.get_total_inverses(p) == 1:
                p.Coordinates = [round(float(c), 9) for c in q]
                pts.append(p)
            else:
                pts.append(self.point(*q))
        item.Points = pts

    def slab_frame(self, slab):
        """A slab of create_slab's form as edit_element reads it: its body (one outline of straight edges extruded straight up), the
        matrix M of the body's position in the storey's plane, its outline there (metres), its thickness (metres) and the height of its
        top in the slab's coordinates (file units); else the refusal of cannot."""
        s = self.scale
        storey = self.container(slab)
        S = _cs_placement(storey.ObjectPlacement) if storey is not None else None
        P = _cs_placement(getattr(slab, "ObjectPlacement", None))
        rep = getattr(slab, "Representation", None)
        bodies = [
            r for r in (rep.Representations if rep is not None else ()) if (r.RepresentationIdentifier or "") == "Body"
        ]
        items = list(bodies[0].Items or ()) if len(bodies) == 1 else []
        item = items[0] if len(items) == 1 and items[0].is_a("IfcExtrudedAreaSolid") else None
        profile = item.SweptArea if item is not None else None
        curve = profile.OuterCurve if profile is not None and profile.is_a() == "IfcArbitraryClosedProfileDef" else None
        why, M = None, None
        if S is None or P is None:
            why = "it is in no storey, or its placement cannot be read"
        elif curve is None or not curve.is_a("IfcPolyline"):
            why = "its body is not one outline of straight edges extruded"
        elif len(getattr(rep, "ShapeOfProduct", None) or ()) > 1:
            why = "it shares its shape with other elements"
        else:
            position = _cs_axes(item.Position)
            M = _cs_compose(_cs_compose(_cs_invert(S), P), position)
            d = _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
            if d is None or abs(d[2] - 1.0) > 1e-9 or abs(position[2][2] - 1.0) > 1e-9 or abs(M[2][2] - 1.0) > 1e-6:
                why = "it is not extruded straight up"
        if why:  # ty: ignore[redundant-condition]
            raise self.cannot(slab, why, "create_slab")
        pts = [[float(c) for c in p.Coordinates][:2] for p in curve.Points]
        if len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-9:
            pts = pts[:-1]
        return {
            "item": item,
            "M": M,
            "outline": [(q[0] * s, q[1] * s) for q in (_cs_at(M, (x, y, 0.0)) for x, y in pts)],
            "thickness": float(item.Depth) * s,
            "top": _cs_axes(item.Position)[3][2] + float(item.Depth),
        }

    def edit_slab(self, slab, op):
        """edit_element on a slab (slab_frame): outline (corners in metres in the storey's plane) draws its profile anew, closed on its
        first point (GEM111); thickness keeps its top where it is; a new type types it with the layers down from its top as create_slab
        lays them; a typed slab takes its thickness from its layers."""
        f = self.slab_frame(slab)
        thickness, typ = self.edit_thickness(slab, op, "slab", f["thickness"])
        item = f["item"]
        if op.get("outline") is not None:
            outline = op["outline"]
            if not isinstance(outline, (list, tuple)) or len(outline) < 3:
                raise DataspatiumError("outline must be at least three [x, y] points")
            back = _cs_invert(f["M"])
            local = [
                _cs_at(back, (self.u(x), self.u(y), f["M"][3][2]))[:2]
                for x, y in (_cs_xy(p, "outline") for p in outline)
            ]
            profile = self.own(item, "SweptArea")
            old = profile.OuterCurve
            profile.OuterCurve = self.closed([(round(x, 9), round(y, 9)) for x, y in local])
            self.drop(old)
        if abs(thickness - f["thickness"]) > 1e-9:
            z = round(f["top"] - self.u(thickness), 9)
            if item.Position is None:
                item.Position = self.axis3(0, 0, z)
            else:
                pos = self.own(item, "Position")
                c = ([float(v) for v in pos.Location.Coordinates] + [0.0, 0.0, 0.0])[:3]
                self.set_point(pos, "Location", (c[0], c[1], z))
            item.Depth = round(self.u(thickness), 9)
        if typ is not None:
            self.untype(slab)
            self.give_type(slab, typ, "AXIS3", "NEGATIVE", 0.0)
        self.touch(slab)

    def opening_frame(self, e):
        """An opening of create_opening's form in a wall of create_wall's form, or the door or window in it, as edit_element reads it:
        the opening, its wall, its body (wall_box), its doors and windows, its offset from the wall's start, width, sill above the
        wall's base and height (metres), and the extent of the wall's body along it and up (bx0, bx1, bz0, bz1, metres); else the refusal
        of cannot."""
        s = self.scale
        opening = (
            e
            if e.is_a("IfcOpeningElement")
            else next((r.RelatingOpeningElement for r in (getattr(e, "FillsVoids", None) or ())), None)
        )
        if opening is None:
            raise self.cannot(e, "it fills no opening", "create_opening")
        wall = next((r.RelatingBuildingElement for r in (getattr(opening, "VoidsElements", None) or ())), None)
        box = self.opening_box(opening, wall) if wall is not None else None
        item, body = self.wall_box(opening), (self.wall_box(wall) if wall is not None else None)
        if (
            wall is None
            or not wall.is_a("IfcWall")
            or box is None
            or item is None
            or body is None
            or not self.movable(opening)
        ):
            raise self.cannot(
                e, "it is not an opening of create_opening's form in a wall of create_wall's form", "create_opening"
            )
        cx, half = float(body.SweptArea.Position.Location.Coordinates[0]), float(body.SweptArea.XDim) / 2.0
        bz = _cs_axes(body.Position)[3][2]
        return {
            "opening": opening,
            "wall": wall,
            "item": item,
            "fillings": [r.RelatedBuildingElement for r in (getattr(opening, "HasFillings", None) or ())],
            "offset": box[0] * s,
            "width": (box[1] - box[0]) * s,
            "sill": box[2] * s,
            "height": (box[3] - box[2]) * s,
            "bx0": (cx - half) * s,
            "bx1": (cx + half) * s,
            "bz0": bz * s,
            "bz1": (bz + float(body.Depth)) * s,
        }

    def edit_opening(self, e, op):
        """edit_element on an opening (opening_frame) or the door or window in it: offset (metres from the wall's start, along it), sill
        (above the wall's base), width and height, the opening and its doors and windows together; the opening must stay within the
        wall's body. A door or window placed in the opening follows it (one placed elsewhere is moved as much), and their bodies,
        OverallWidth and OverallHeight take the new size; a door or window that moved bounds the spaces whose edge it then stands on
        (fit_spaces)."""
        f = self.opening_frame(e)
        opening, wall, item, fillings = f["opening"], f["wall"], f["item"], f["fillings"]
        offset, width, sill, height = f["offset"], f["width"], f["sill"], f["height"]
        new = {
            k: (_cs_num(op, k, positive=k in ("width", "height")) if op.get(k) is not None else v)
            for k, v in (("offset", offset), ("width", width), ("sill", sill), ("height", height))
        }
        bx0, bx1, bz0, bz1 = f["bx0"], f["bx1"], f["bz0"], f["bz1"]
        if (
            new["offset"] < bx0 - 0.001
            or new["offset"] + new["width"] > bx1 + 0.001
            or new["sill"] < bz0 - 0.001
            or new["sill"] + new["height"] > bz1 + 0.001
        ):
            raise DataspatiumError(
                "%s #%d would reach beyond wall #%d %r, whose body runs from %.3g to %.3g m from its start and from %.3g to %.3g m up; give an offset, width, sill and height within it"
                % (e.is_a(), e.id(), wall.id(), _ifc_text(wall.Name) or "", bx0, bx1, bz0, bz1)
            )
        resize = abs(new["width"] - width) > 1e-9 or abs(new["height"] - height) > 1e-9
        boxes = [item]
        for x in fillings:
            b = self.wall_box(x)
            if resize and b is None:
                raise self.cannot(
                    x, "its body is not the box create_opening draws, so its size cannot change", "create_opening"
                )
            boxes.append(b)
        loose = self.loose_fillings(opening)
        d = (self.u(new["offset"] - offset), 0.0, self.u(new["sill"] - sill))
        if any(abs(c) > 1e-12 for c in d):
            before = _cs_placement(opening.ObjectPlacement)
            self.shift(opening, _cs_placement(wall.ObjectPlacement), d)
            self.follow(opening, before, loose)
            self.slid.update(x.id() for x in fillings)  # the spaces they bound (fit_spaces)
        if resize:
            for b in boxes:
                self.resize_box(b, self.u(new["width"]), self.u(new["height"]))
            for x in fillings:
                if hasattr(x, "OverallWidth"):
                    x.OverallWidth, x.OverallHeight = round(self.u(new["width"]), 9), round(self.u(new["height"]), 9)
        for x in [opening] + fillings + [wall]:
            self.touch(x)

    def resize_box(self, item, width, height):
        """A box of create_opening's form (wall_box) made width long along x from where it starts, and height high (file units)."""
        p = self.own(item, "SweptArea")
        cx, cy = [float(c) for c in p.Position.Location.Coordinates][:2]
        x0 = cx - float(p.XDim) / 2.0
        p.XDim = round(width, 9)
        self.set_point(self.own(p, "Position"), "Location", (round(x0 + width / 2.0, 9), cy))
        item.Depth = round(height, 9)

    def roof_frame(self, roof):
        """A roof of create_roof's form as edit_element reads it: its storey's absolute matrix S, its shape (PredefinedType, in IFC2X3
        ShapeType), its base above the storey and, from its slabs' faces, the pitch, the rectangle it covers (length and width), the
        overhang and the thickness (metres), the ridge, and per slab its body and the direction its face falls; else the refusal of
        cannot."""
        s = self.scale
        storey = self.container(roof)
        S = _cs_placement(storey.ObjectPlacement) if storey is not None else None
        R = _cs_placement(roof.ObjectPlacement) if self.movable(roof) else None
        shape = {v: k for k, v in self.ROOF_TYPES.items()}.get(
            str(getattr(roof, "ShapeType" if self.m.schema == "IFC2X3" else "PredefinedType", None) or "")
        )
        parts = [
            o
            for rel in (getattr(roof, "IsDecomposedBy", None) or ())
            for o in (rel.RelatedObjects or ())
            if o.is_a("IfcSlab")
        ]
        why, faces = None, []
        rel = _cs_compose(_cs_invert(S), R) if S is not None and R is not None else None
        if rel is None or abs(rel[2][2] - 1.0) > 1e-6:
            why = "it is in no storey, or its placement cannot be read"
        elif shape is None:
            why = "its shape is none of create_roof's (gable, hip, shed, flat)"
        elif not parts:
            why = "it has no roof slabs"
        for slab in parts if why is None else ():
            P = _cs_placement(getattr(slab, "ObjectPlacement", None))
            rep = getattr(slab, "Representation", None)
            bodies = [
                r
                for r in (rep.Representations if rep is not None else ())
                if (r.RepresentationIdentifier or "") == "Body"
            ]
            items = list(bodies[0].Items or ()) if len(bodies) == 1 else []
            item = (
                items[0]
                if len(items) == 1
                and items[0].is_a("IfcExtrudedAreaSolid")
                and items[0].SweptArea.is_a() == "IfcArbitraryClosedProfileDef"
                else None
            )
            inner = _cs_compose(_cs_invert(R), P) if P is not None else None
            if (
                item is None
                or inner is None
                or any(abs(inner[i][j] - (1.0 if i == j else 0.0)) > 1e-6 for i in range(3) for j in range(3))
                or max(abs(c) for c in inner[3]) > self.u(1e-6)
            ):
                why = "its slab #%d is not one of create_roof's faces" % slab.id()
                break
            M = _cs_compose(inner, _cs_axes(item.Position))
            n, h = M[2], math.hypot(M[2][0], M[2][1])
            d = _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
            faces.append(
                {
                    "slab": slab,
                    "rep": bodies[0],
                    "item": item,
                    "pitch": math.degrees(math.atan2(h, n[2])),
                    "fall": (n[0] / h, n[1] / h) if h > 1e-9 else None,
                    "thickness": float(item.Depth) * abs(d[2]) * s if d is not None else 0.0,
                    "corners": [_cs_at(M, (q[0], q[1], 0.0)) for q in _cs_profile_points(item.SweptArea)],
                }
            )
        if why is None:
            xs, ys = [c[0] * s for x in faces for c in x["corners"]], [c[1] * s for x in faces for c in x["corners"]]
            overhang, pitch, thickness = -min(xs), faces[0]["pitch"], faces[0]["thickness"]
            if (
                abs(overhang + min(ys)) > 0.001
                or overhang < -0.001
                or thickness <= 0
                or any(abs(x["pitch"] - pitch) > 0.01 or abs(x["thickness"] - thickness) > 0.0005 for x in faces)
            ):
                why = "its faces are not of one pitch, overhang and thickness"
        if why is None:
            overhang = max(0.0, overhang)
            length, width = max(xs) - overhang, max(ys) - overhang
            falls = [x["fall"] for x in faces if x["fall"] is not None]
            ridge = None
            if shape == "gable":
                ridge = "x" if all(abs(fall[0]) < 1e-6 for fall in falls) else "y"
            elif shape == "shed" and falls and abs(falls[0][0]) > 0.5:
                ridge = "x"
            want = [fall for fall, _ in _cs_roof_faces(shape, length, width, overhang, ridge)]
            if len(want) != len(faces) or (
                shape != "flat"
                and any(
                    sum(1 for x in faces if x["fall"] and x["fall"][0] * w[0] + x["fall"][1] * w[1] > 0.999) != 1
                    for w in want
                )
            ):
                why = "its slabs are not the faces create_roof makes"
        if why:
            raise self.cannot(roof, why, "create_roof")
        return {
            "S": S,
            "shape": shape,
            "base": rel[3][2] * s,  # ty: ignore[not-subscriptable]
            "pitch": 0.0 if shape == "flat" else pitch,
            "length": length,
            "width": width,
            "overhang": overhang,
            "thickness": thickness,
            "ridge": ridge,
            "faces": faces,
        }

    def edit_roof(self, roof, op):
        """edit_element on a roof (roof_frame): base (the eaves' height above the storey) lifts it, and pitch and overhang make its faces
        anew over the same rectangle as create_roof does (roof_solid), each slab keeping its GlobalId, its type and its layers with a
        new body; a flat roof has no pitch."""
        f = self.roof_frame(roof)
        flat = f["shape"] == "flat"
        if flat and op.get("pitch") is not None:
            raise DataspatiumError(
                "roof #%d is flat and has no pitch; another shape is a new roof, which only the person can ask for"
                % roof.id()
            )
        pitch = _cs_num(op, "pitch", unit="degrees") if op.get("pitch") is not None else f["pitch"]
        if not flat and not 0.0 < pitch <= 75.0:
            raise DataspatiumError("pitch must be more than 0 and at most 75 degrees")
        overhang = _cs_num(op, "overhang") if op.get("overhang") is not None else f["overhang"]
        if overhang < 0:
            raise DataspatiumError("overhang must be zero or more (metres)")
        base = _cs_num(op, "base") if op.get("base") is not None else f["base"]
        if abs(base - f["base"]) > 1e-9:
            self.shift(roof, f["S"], (0.0, 0.0, self.u(base - f["base"])))
        if abs(pitch - f["pitch"]) > 1e-9 or abs(overhang - f["overhang"]) > 1e-9:
            p = math.radians(pitch)
            for fall, corners in _cs_roof_faces(f["shape"], f["length"], f["width"], overhang, f["ridge"]):
                face = (
                    f["faces"][0]
                    if flat
                    else next(
                        x for x in f["faces"] if x["fall"] and x["fall"][0] * fall[0] + x["fall"][1] * fall[1] > 0.999
                    )
                )
                self.replace_item(
                    face["rep"],
                    face["item"],
                    self.roof_solid(fall, corners, f["length"], f["width"], p, f["thickness"]),
                )
                self.touch(face["slab"])
        self.touch(roof)

    def roof_solid(self, fall, corners, length, width, pitch, thickness):
        """The body of one face of a roof over [0, length] x [0, width] in metres, as op_create_roof draws it: the face's outline in its
        own plane (Axis the normal, RefDirection along the eaves), extruded straight up by thickness / cos(pitch)."""
        sin, cos, tan = math.sin(pitch), math.cos(pitch), math.tan(pitch)
        fx, fy = fall
        eaves = max(x * fx + y * fy for x, y in ((0.0, 0.0), (length, 0.0), (length, width), (0.0, width)))
        pts = [(x, y, (eaves - x * fx - y * fy) * tan) for x, y in corners]
        along, up, normal = (-fy, fx, 0.0), (-fx * cos, -fy * cos, sin), (fx * sin, fy * sin, cos)
        o = pts[0]
        face = [[self.u(_cs_dot([q[k] - o[k] for k in range(3)], axis)) for axis in (along, up)] for q in pts]
        profile = self.m.createIfcArbitraryClosedProfileDef("AREA", None, self.closed(face))
        position = self.m.createIfcAxis2Placement3D(
            self.point(self.u(o[0]), self.u(o[1]), self.u(o[2])), self.direction(*normal), self.direction(*along)
        )
        return self.m.createIfcExtrudedAreaSolid(
            profile, position, self.direction(0.0, sin, cos), self.u(thickness / cos)
        )

    def parameters(self, e):
        """What edit_element reads of an element, for the read tool get_parameters: its id, GlobalId, class, name and kind (wall, slab,
        opening, roof, or null when edit_element can change only its name), and its kind's measures in metres and degrees: a wall's start
        and end in its storey's plane, thickness, height and type; a slab's outline, thickness and type; an opening's (also for its door
        or window) wall, offset, width, height and sill; a roof's shape, pitch, overhang and base. A note says why an element of another
        form has no measures."""
        import ifcopenshell.util.element as el

        kind = self.edit_kind(e)
        out = {
            "id": e.id(),
            "guid": getattr(e, "GlobalId", None),
            "ifc_class": e.is_a(),
            "name": _ifc_text(getattr(e, "Name", None)),
            "kind": kind if kind in self.EDITS else None,
        }
        t = el.get_type(e) if e.is_a("IfcObject") else None
        typed = _ifc_text(t.Name) if t is not None else None

        def r6(v):
            return round(float(v), 6)

        try:
            if kind == "wall":
                f = self.wall_frame(e)
                out.update(
                    start=[r6(v) for v in f["start"]],
                    end=[r6(v) for v in f["end"]],
                    thickness=r6(f["thickness"]),
                    height=r6(f["height"]),
                    type=typed,
                )
            elif kind == "slab":
                f = self.slab_frame(e)
                out.update(outline=[[r6(v) for v in p] for p in f["outline"]], thickness=r6(f["thickness"]), type=typed)
            elif kind == "opening":
                f = self.opening_frame(e)
                out.update(
                    wall=f["wall"].id(),
                    offset=r6(f["offset"]),
                    width=r6(f["width"]),
                    height=r6(f["height"]),
                    sill=r6(f["sill"]),
                )
            elif kind == "roof":
                f = self.roof_frame(e)
                out.update(shape=f["shape"], pitch=r6(f["pitch"]), overhang=r6(f["overhang"]), base=r6(f["base"]))
            elif kind == "roof slab":
                out["note"] = self.edit_refusal(e, kind, ["pitch"])
            else:
                out["note"] = "edit_element changes only the name of a %s" % e.is_a()
        except DataspatiumError as exc:
            out.update(kind=None, note=str(exc))
        return out

    @staticmethod
    def parts_of(e):
        """The parts an element aggregates, and theirs in turn (a curtain wall's mullions, transoms and panels, a stair's flight and
        landing, a roof's slabs); none for a spatial element, whose spaces are not parts to delete with it."""
        if not e.is_a("IfcElement"):
            return []
        out = []
        for rel in getattr(e, "IsDecomposedBy", None) or ():
            for p in rel.RelatedObjects or ():
                if p.is_a("IfcElement"):
                    out += [p] + _ChangeSet.parts_of(p)
        return out

    def op_delete_elements(self, op):
        """Delete elements; the doors and windows in an element's openings go with it, and so do the parts of a whole (parts_of), so
        nothing is left hanging in the air."""
        import ifcopenshell.api.root

        if op.get("confirm") is not True:
            raise DataspatiumError("deleting needs confirm: true, which the person gives")
        targets = self.elements(op)
        ids = {e.id() for e in targets}
        for e in targets:
            fillings = []
            for rel in getattr(e, "HasOpenings", None) or ():
                opening = rel.RelatedOpeningElement
                for f in getattr(opening, "HasFillings", None) or ():
                    if f.RelatedBuildingElement.id() not in ids:
                        fillings.append(f.RelatedBuildingElement)
            parts = [p for p in self.parts_of(e) if p.id() not in ids]
            ids |= {p.id() for p in parts}
            for f in fillings + parts + [e]:
                rec = self.record(f, self.container(f))
                if f is not e:
                    rec["with"] = e.id()
                self.deleted.append(rec)
                self.changed.pop(f.id(), None)
                self.unbounded.update(
                    b.RelatingSpace.id()
                    for b in (getattr(f, "ProvidesBoundaries", None) or ())
                    if b.RelatingSpace is not None
                )
                ifcopenshell.api.root.remove_product(
                    self.m, product=f
                )  # with its space boundaries (IfcOpenShell's remove_boundary)

    def op_set_site(self, op):
        """The model's place on earth: the site's WGS 84 position (compound plane angles, every part signed), its height above the sea
        and its name, and with a crs the map conversion of the model context to that projected CRS (IFC4 and IFC4X3; IFC2X3 has none,
        which the report notes). The site goes into the changed list; a CRS or conversion that had to be made into the created list."""
        latitude = _cs_num(op, "latitude", unit="degrees")
        longitude = _cs_num(op, "longitude", unit="degrees")
        if not -90.0 <= latitude <= 90.0:
            raise DataspatiumError("latitude must be between -90 and 90 degrees")
        if not -180.0 <= longitude <= 180.0:
            raise DataspatiumError("longitude must be between -180 and 180 degrees")
        elevation = None if op.get("elevation") is None else _cs_num(op, "elevation")
        crs = str(op.get("crs") or "").strip().upper() or None
        eastings = northings = None
        if crs is not None:
            if not (crs.startswith("EPSG:") and crs[5:].isdigit()):
                raise DataspatiumError("crs must be an EPSG code such as EPSG:3006")
            eastings = _cs_num(op, "eastings")
            northings = _cs_num(op, "northings")
        elif op.get("eastings") is not None or op.get("northings") is not None:
            raise DataspatiumError("eastings and northings need a crs such as EPSG:3006")
        rotation = _cs_num(op, "rotation", 0.0, unit="degrees")
        site_name = str(op.get("site_name") or "").strip()[:255] or None
        site = self.site(site_name)
        site.RefLatitude = _cs_angle(latitude)
        site.RefLongitude = _cs_angle(longitude)
        if elevation is not None:
            site.RefElevation = self.u(elevation)
            self.building_height(elevation)
        if site_name:
            site.Name = site_name
        self.touch(site)
        if crs is None:
            return
        if self.m.schema == "IFC2X3":
            self.notes.append(
                "the map conversion to %s was skipped: IFC2X3 has no IfcMapConversion; the site's latitude, longitude and elevation were set"
                % crs
            )
            return
        self.map_conversion(
            crs, eastings, northings, 0.0 if elevation is None else elevation, rotation, op.get("vertical_datum")
        )

    def building_height(self, elevation):
        """The buildings' height above the sea in IfcBuilding.ElevationOfRefHeight (the model origin's elevation plus the building's
        placement), as the Swedish guidance asks, in IFC4 and IFC2X3. IFC4X3 deprecates the attribute (bSI rule IFC102, decision
        B-60.2): there the height is the map conversion's OrthogonalHeight alone."""
        if self.m.schema == "IFC4X3":
            return
        for building in self.m.by_type("IfcBuilding"):
            frame = _cs_placement(building.ObjectPlacement)
            building.ElevationOfRefHeight = self.u(elevation) + (frame[3][2] if frame is not None else 0.0)
            self.touch(building)

    def parent_contexts(self):
        """The representation contexts that are not sub-contexts (Model, Plan …), the model context made when the file has none."""
        found = [
            c
            for c in self.m.by_type("IfcGeometricRepresentationContext")
            if not c.is_a("IfcGeometricRepresentationSubContext")
        ]
        return found or [self.model_context()]

    def metre(self):
        """An IfcSIUnit for the metre, the MapUnit of a projected CRS: the project's length unit when that is the metre, else one the
        file has, else a new one (bSI rule GRF005: the CRS in metres, the conversion's Scale from the file's unit)."""

        def plain(u):
            return (
                u is not None
                and u.is_a("IfcSIUnit")
                and u.UnitType == "LENGTHUNIT"
                and u.Name == "METRE"
                and not u.Prefix
            )

        unit = self.length_unit()
        if plain(unit):
            return unit
        return next((u for u in self.m.by_type("IfcSIUnit") if plain(u)), None) or self.m.create_entity(
            "IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"
        )

    def map_conversion(self, crs, eastings, northings, height, rotation, vertical=None):
        """One IfcProjectedCRS named by the EPSG code, with its datum, projection and zone, a Description in plain words and the
        VerticalDatum when the code does not give the height system (RH 2000 by default for SWEREF 99), and one IfcMapConversion to it
        from every parent representation context, all with the same values (bSI rule GRF001), made or updated, never doubled. The
        MapUnit is the metre and the conversion's Scale the file's length unit in metres (GRF005), so the eastings, northings and
        height, which come in metres, are written as they are. The height is the model origin's height above the vertical datum,
        which in IFC4X3 also carries the building's height (IfcBuilding.ElevationOfRefHeight is deprecated there)."""
        contexts = self.parent_contexts()
        conversions = list(self.m.by_type("IfcMapConversion"))
        target = next(
            (c.TargetCRS for c in conversions if c.TargetCRS is not None and c.TargetCRS.is_a("IfcProjectedCRS")), None
        )
        if target is None:
            found = self.m.by_type("IfcProjectedCRS")
            target = found[0] if found else None
        code = int(crs[5:])
        datum, projection, zone = _cs_crs_facts(crs)
        if datum is None and self.m.schema == "IFC4":
            datum = ""  # IFC4 requires a datum; an empty identifier says the app does not know this code's
            self.notes.append(
                "the geodetic datum of %s is not known to the app; IFC4 requires one, so it is written empty" % crs
            )
        vertical = _cs_crs_vertical(code, vertical)
        values = {
            "Name": crs,
            "Description": _cs_crs_description(code, vertical),
            "GeodeticDatum": datum,
            "VerticalDatum": vertical,
            "MapProjection": projection,
            "MapZone": zone,
            "MapUnit": self.metre(),
        }
        if target is None:
            target = self.m.create_entity("IfcProjectedCRS", **values)
            self.register(target, ref=False)
        else:
            for key, value in values.items():
                setattr(target, key, value)
            self.touch(target)
        angle = math.radians(rotation)
        for context in contexts:
            conversion = next(
                (c for c in conversions if c.SourceCRS is not None and c.SourceCRS.id() == context.id()), None
            )
            values = {
                "SourceCRS": context,
                "TargetCRS": target,
                "Eastings": float(eastings),
                "Northings": float(northings),
                "OrthogonalHeight": float(height),
                "XAxisAbscissa": math.cos(angle),
                "XAxisOrdinate": math.sin(angle),
                "Scale": float(self.scale),
            }
            if conversion is None:
                conversion = self.m.create_entity("IfcMapConversion", **values)
                self.register(conversion, ref=False)
            else:
                for key, value in values.items():
                    setattr(conversion, key, value)
                self.touch(conversion)

    # -- the model's responsible party (recommendation 8 of the study of the IFC and BCF guidelines)
    def op_set_responsible_party(self, op):
        """The organisation that answers for the model, its role, designation and contact (see the class's documentation): one
        responsible party per model. The project's assignment to an actor of the same organisation, else of the same role, else
        its only one, is changed in place, so that the actor and the assignment keep their GlobalIds; otherwise a new actor is
        assigned. An organisation is never renamed, since owner histories and applications share it: another name gets an
        organisation of its own, and what the old party left unused goes."""
        import ifcopenshell.guid

        text = lambda key: str(op.get(key) or "").strip()[:255] or None  # noqa: E731
        name = text("organization")
        if not name:
            raise DataspatiumError(
                "organization is required: the name of the company or authority that answers for the model"
            )
        role = str(op.get("role") or "").strip().upper() or None
        user_role = text("user_role")
        if role is not None:
            items = _ifc_enum_items(self.check_class("IfcActorRole"), "Role") or ()
            if role == "COMMISSIONINGENGINEER" and role not in items and "COMISSIONINGENGINEER" in items:
                role = "COMISSIONINGENGINEER"  # IFC2X3's spelling
            if role not in items:
                raise DataspatiumError("role must be one of %s, or null" % ", ".join(items))
        if role == "USERDEFINED" and not user_role:
            raise DataspatiumError("role USERDEFINED needs user_role, the name of the role")
        if user_role and role != "USERDEFINED":
            raise DataspatiumError("user_role goes with role USERDEFINED")
        projects = self.m.by_type("IfcProject")
        if not projects:
            raise DataspatiumError("the model has no IfcProject to assign its responsible party to")
        project = projects[0]
        rel = self.party_assignment(project, name, role)
        actor = rel.RelatingActor if rel is not None else None
        before = actor.TheActor if actor is not None else None
        old_org = org = self.party_of(before)
        if org is None or str(org.Name or "").strip().lower() != name.lower():
            org = self.m.create_entity("IfcOrganization", Name=name)
        old_role = rel.ActingRole if rel is not None else None
        acting = None
        if role is not None:
            same = old_role is not None and old_role.Role == role and (old_role.UserDefinedRole or None) == user_role
            acting = old_role if same else self.m.create_entity("IfcActorRole", Role=role, UserDefinedRole=user_role)
        # The party's role among its organisation's Roles, in place of the one it played before (also on an organisation it left).
        for o in dict.fromkeys(x for x in (old_org, org) if x is not None):
            roles = [r for r in (o.Roles or ()) if r != old_role] + (
                [acting] if o == org and acting is not None else []
            )
            o.Roles = tuple(roles) or None
        # The contact person: a name sets it, "" removes it, and null keeps the one the party has at the same organisation.
        person = text("contact_person")
        kept = (
            before is not None
            and before.is_a("IfcPersonAndOrganization")
            and before.TheOrganization == org
            and before.ThePerson is not None
        )
        who = before if kept and op.get("contact_person") is None else org
        if person:
            words = person.split()
            given, family = " ".join(words[:-1]) or None, words[-1]
            if kept and before.ThePerson.FamilyName == family and (before.ThePerson.GivenName or None) == given:
                who = before
            else:
                who = self.m.create_entity(
                    "IfcPersonAndOrganization",
                    ThePerson=self.m.create_entity("IfcPerson", FamilyName=family, GivenName=given),
                    TheOrganization=org,
                )
        if actor is None:
            actor = self.m.create_entity(
                "IfcActor",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                Name=text("designation"),
                Description=text("contact_role"),
                TheActor=who,
            )
            rel = self.m.create_entity(
                "IfcRelAssignsToActor",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedObjects=[project],
                RelatingActor=actor,
                ActingRole=acting,
            )
            self.register(actor, ref=False)
            self.register(rel, ref=False)
            return
        actor.Name, actor.Description, actor.TheActor = text("designation"), text("contact_role"), who
        rel.ActingRole = acting
        self.touch(actor)
        self.touch(rel)
        self.party_unused([before, old_role] if before is not who else [old_role])

    def party_assignment(self, project, name, role):
        """The project's assignment to an actor (not an occupant) of the organisation named, else of the role, else its only one."""
        rels = [
            r
            for r in self.m.get_inverse(project)
            if r.is_a("IfcRelAssignsToActor")
            and r.RelatingActor is not None
            and not r.RelatingActor.is_a("IfcOccupant")
        ]
        named = [
            r
            for r in rels
            if str(getattr(self.party_of(r.RelatingActor.TheActor), "Name", None) or "").strip().lower() == name.lower()
        ]
        playing = [r for r in rels if role is not None and r.ActingRole is not None and r.ActingRole.Role == role]
        return (named or playing or (rels if len(rels) == 1 else [None]))[0]

    @staticmethod
    def party_of(who):
        """The organisation of an actor's TheActor: itself, or a person-and-organisation's; None for a person alone."""
        if who is None:
            return None
        if who.is_a("IfcOrganization"):
            return who
        return getattr(who, "TheOrganization", None) if who.is_a("IfcPersonAndOrganization") else None

    def party_unused(self, entities):
        """What a changed party no longer uses, when nothing else does: its person-and-organisation, person, organisation or role."""
        for e in entities:
            if e is None or self.m.get_total_inverses(e):
                continue
            inner = [e.ThePerson, e.TheOrganization] if e.is_a("IfcPersonAndOrganization") else []
            self.m.remove(e)
            self.party_unused(inner)

    # -- types with material layers, wall joins and base quantities (recommendations 3, 5 and 6 of the native IFC study)
    TYPE_KINDS = {
        "wall": ("IfcWallType", "NOTDEFINED"),
        "slab": ("IfcSlabType", "FLOOR"),
        "roof": ("IfcSlabType", "ROOF"),
        "covering": ("IfcCoveringType", "NOTDEFINED"),
    }
    MAX_LAYERS = 20
    # IfcMaterial.Category: the categories IfcOpenShell's add_material recommends; IfcMaterialLayer.Category: the keywords of the
    # schema's documentation of the attribute, (IFC4, IFC4X3). IFC2X3 has neither attribute.
    MATERIAL_CATEGORIES = (
        "concrete",
        "steel",
        "aluminium",
        "block",
        "brick",
        "stone",
        "wood",
        "glass",
        "gypsum",
        "plastic",
        "earth",
    )
    LAYER_ROLES = {
        "load_bearing": ("LoadBearing", "LoadBearing"),
        "insulation": ("Insulation", "Insulation"),
        "inner_finish": ("Finish", "Inner finish"),
        "outer_finish": ("Finish", "Outer finish"),
    }

    def op_define_type(self, op):
        """A type with its material layers and a ref, for the type of later create steps: kind wall makes an IfcWallType, slab an
        IfcSlabType and roof an IfcSlabType ROOF for create_roof's roof slabs (the IfcRoof only aggregates them, and IFC2X3 has no
        IfcRoofType), and covering an IfcCoveringType for create_element's coverings (its layers from the outer face in), with an
        IfcMaterialLayerSet named after it; the kinds without layers go to define_element_type. The layers come in metres from a wall's right side to its left,
        seen from its start toward its end, and from a slab's or a roof's top down; a roof type keeps them from the bottom up, the
        order in which its slabs' usage lays them from the underside. A layer's optional category goes to its material
        (IfcMaterial.Category, one of MATERIAL_CATEGORIES; a material the file has already keeps its own and takes one it lacks) and
        its optional role to the layer (IfcMaterialLayer.Category, LAYER_ROLES in the file's schema); IFC2X3 has neither, which the
        notes say. The PredefinedType is predefined_type, by default NOTDEFINED, FLOOR and ROOF, from the class's list in the file's
        schema (_cs_type_values). A type of the same class and name, in any case, in the file is an error that names its GlobalId, to
        give as type instead (a server's copy of the file keeps the GlobalIds, not necessarily the STEP ids)."""
        kind = str(op.get("kind") or "").strip().lower()
        if kind in self.ELEMENT_TYPE_KINDS:
            return self.define_element_type(op, kind)
        if kind not in self.TYPE_KINDS:
            raise DataspatiumError(
                "kind must be wall, slab, roof, covering, column, beam, footing, railing, door, window or curtain_wall"
            )
        ifc_class, default = self.TYPE_KINDS[kind]
        name = str(op.get("name") or "").strip()[:255]
        if not name:
            raise DataspatiumError("the type needs a name")
        layers = op.get("layers")
        if not isinstance(layers, (list, tuple)) or not layers or len(layers) > self.MAX_LAYERS:
            raise DataspatiumError(
                "layers must be a list of 1 to %d {material, thickness}, the thickness in metres" % self.MAX_LAYERS
            )
        parsed = []
        for i, layer in enumerate(layers):
            material = str(layer.get("material") or "").strip()[:255] if isinstance(layer, dict) else ""
            if not material:
                raise DataspatiumError("layer %d needs a material" % (i + 1))
            category = str(layer.get("category") or "").strip().lower() or None
            role = str(layer.get("role") or "").strip().lower().replace(" ", "_").replace("-", "_") or None
            if category is not None and category not in self.MATERIAL_CATEGORIES:
                raise DataspatiumError(
                    "layer %d: category must be one of %s, or null" % (i + 1, ", ".join(self.MATERIAL_CATEGORIES))
                )
            if role is not None and role not in self.LAYER_ROLES:
                raise DataspatiumError(
                    "layer %d: role must be one of %s, or null" % (i + 1, ", ".join(self.LAYER_ROLES))
                )
            try:
                parsed.append((material, _cs_num(layer, "thickness", positive=True), category, role))
            except DataspatiumError as exc:
                raise DataspatiumError("layer %d: %s" % (i + 1, exc))
        twin = next((t for t in self.m.by_type(ifc_class) if str(t.Name or "").strip().lower() == name.lower()), None)
        if twin is not None:
            raise DataspatiumError(
                "the model has an %s of the same name already (GlobalId %s, id %d); give its GlobalId as type, or choose another name"
                % (ifc_class, twin.GlobalId, twin.id())
            )
        value = str(op.get("predefined_type") or "").strip().upper() or default
        allowed = _cs_type_values(self.m, ifc_class, kind)
        if value not in allowed:
            raise DataspatiumError(
                "%s is not a PredefinedType of a %s type in %s; it takes %s"
                % (value, kind, self.m.schema, ", ".join(allowed))
            )
        old = self.m.schema == "IFC2X3"
        if old and any(c or r for _, _, c, r in parsed):
            self.notes.append("type %s: IFC2X3 has no material categories or layer roles, so they were left out" % name)
        made = []
        for material, thick, category, role in parsed:
            layer = self.m.create_entity(
                "IfcMaterialLayer",
                Material=self.named_material(material, None if old else category),
                LayerThickness=round(self.u(thick), 9),
            )
            if role is not None and not old:
                layer.Category = self.LAYER_ROLES[role][0 if self.m.schema == "IFC4" else 1]
            made.append(layer)
        t = self.root(ifc_class, name)
        t.PredefinedType = value
        self.associate(
            t,
            self.m.create_entity(
                "IfcMaterialLayerSet", MaterialLayers=made[::-1] if kind == "roof" else made, LayerSetName=name
            ),
        )
        self.register(t)

    def named_material(self, name, category=None):
        """The file's IfcMaterial of that name, made when there is none; a category (IFC4 and later) goes to a material without one,
        and one the material has already must match it."""
        import ifcopenshell.api.material

        found = next((x for x in self.m.by_type("IfcMaterial") if str(x.Name or "") == name), None)
        if found is None:
            return ifcopenshell.api.material.add_material(self.m, name=name, category=category)
        own = str(getattr(found, "Category", None) or "").strip()
        if category and own and own.lower() != category:
            raise DataspatiumError(
                "the model's material %r has the category %r, not %r; give that category or null"
                % (name, own, category)
            )
        if category and not own:
            found.Category = category
        return found

    def associate(self, product, material):
        """An IfcRelAssociatesMaterial of one product with a material, a layer set or a layer set usage."""
        import ifcopenshell.guid

        return self.m.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner,
            RelatedObjects=[product],
            RelatingMaterial=material,
        )

    @staticmethod
    def type_layers(t):
        """A type's IfcMaterialLayerSet: its own, else the one its occurrences' layer set usage names; None without one."""
        for rel in getattr(t, "HasAssociations", None) or ():
            if rel.is_a("IfcRelAssociatesMaterial") and rel.RelatingMaterial.is_a("IfcMaterialLayerSet"):
                return rel.RelatingMaterial
            if rel.is_a("IfcRelAssociatesMaterial") and rel.RelatingMaterial.is_a("IfcMaterialLayerSetUsage"):
                return rel.RelatingMaterial.ForLayerSet
        for rel in getattr(t, "Types", None) or getattr(t, "ObjectTypeOf", None) or ():
            for o in rel.RelatedObjects or ():
                for a in getattr(o, "HasAssociations", None) or ():
                    if a.is_a("IfcRelAssociatesMaterial") and a.RelatingMaterial.is_a("IfcMaterialLayerSetUsage"):
                        return a.RelatingMaterial.ForLayerSet
        return None

    def with_type(self, op, kind):
        """(op, type) for a create step: with a type (op["type"], a ref, id or GlobalId) a copy of the op whose thickness is that of
        the type's layers in metres, and the type, which must be a wall type for a wall, a slab type for a slab and a roof type (an
        IfcSlabType ROOF) for a roof; (op, None) without one. A thickness the step gives must be the layers'."""
        ref = op.get("type")
        if ref is None or str(ref).strip() == "":
            return op, None
        t = self.entity(ref)
        roof = str(getattr(t, "PredefinedType", None) or "") == "ROOF"
        if not t.is_a("IfcWallType" if kind == "wall" else "IfcSlabType") or (kind == "roof") != roof:
            what = {
                "wall": "a wall type (IfcWallType)",
                "slab": "a slab type (an IfcSlabType other than ROOF)",
                "roof": "a roof type (IfcSlabType ROOF)",
            }[kind]
            raise DataspatiumError(
                "type must name %s, as define_type makes them, not the %s %r"
                % (what, t.is_a(), _ifc_text(getattr(t, "Name", None)))
            )
        layers = self.type_layers(t)
        total = round(
            sum(float(x.LayerThickness or 0.0) for x in (layers.MaterialLayers if layers is not None else ()))
            * self.scale,
            9,
        )
        if total <= 0:
            raise DataspatiumError("type %r has no material layers to take the thickness from" % _ifc_text(t.Name))
        if op.get("thickness") is not None and abs(_cs_num(op, "thickness", positive=True) - total) > 0.0005:
            raise DataspatiumError(
                "thickness %g m differs from the %g m of the layers of type %r; leave thickness null"
                % (_cs_num(op, "thickness"), total, _ifc_text(t.Name))
            )
        return dict(op, thickness=total), t

    def give_type(self, e, t, direction, sense, offset):
        """Type an occurrence: the type's IfcRelDefinesByType (one per type), the type's layers as an IfcMaterialLayerSetUsage of the
        occurrence's own (LayerSetDirection AXIS2 or AXIS3, DirectionSense POSITIVE or NEGATIVE, OffsetFromReferenceLine from the
        reference line or plane, in metres), and no PredefinedType or ObjectType of its own: the type carries it (OJT001). Nothing
        without a type."""
        if t is None:
            return
        import ifcopenshell.guid

        rel = next(iter(getattr(t, "Types", None) or getattr(t, "ObjectTypeOf", None) or ()), None)
        if rel is None:
            self.m.create_entity(
                "IfcRelDefinesByType",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedObjects=[e],
                RelatingType=t,
            )
        else:
            rel.RelatedObjects = list(rel.RelatedObjects or ()) + [e]
        usage = self.m.create_entity(
            "IfcMaterialLayerSetUsage",
            ForLayerSet=self.type_layers(t),
            LayerSetDirection=direction,
            DirectionSense=sense,
            OffsetFromReferenceLine=round(self.u(offset), 9),
        )
        self.associate(e, usage)
        if getattr(e, "PredefinedType", None) is not None:
            e.PredefinedType = None
        if getattr(e, "ObjectType", None) is not None:
            e.ObjectType = None

    # -- more building elements, straight stairs, and door and window types (recommendations 4 and 11 of the native IFC study)
    # The classes create_element makes; IfcFurniture is IfcFurnishingElement in IFC2X3, IfcStair is a straight stair and
    # IfcCurtainWall a glass façade of panels between mullions and transoms.
    ELEMENT_CLASSES = (
        "IfcColumn",
        "IfcBeam",
        "IfcMember",
        "IfcFooting",
        "IfcPile",
        "IfcCovering",
        "IfcRailing",
        "IfcChimney",
        "IfcShadingDevice",
        "IfcSanitaryTerminal",
        "IfcFurniture",
        "IfcFurnishingElement",
        "IfcStair",
        "IfcCurtainWall",
    )
    ELEMENT_TYPE_KINDS = {
        "column": ("IfcColumnType", "COLUMN"),
        "beam": ("IfcBeamType", "BEAM"),
        "footing": ("IfcFootingType", "NOTDEFINED"),
        "railing": ("IfcRailingType", "NOTDEFINED"),
        "door": ("IfcDoorType", "DOOR"),
        "window": ("IfcWindowType", "WINDOW"),
        "curtain_wall": ("IfcCurtainWallType", "NOTDEFINED"),
    }
    # The OperationTypes and PartitioningTypes whose geometry IfcOpenShell draws; a window's as (panels, columns, rows).
    DOOR_OPERATIONS = (
        "SINGLE_SWING_LEFT",
        "SINGLE_SWING_RIGHT",
        "DOUBLE_SWING_LEFT",
        "DOUBLE_SWING_RIGHT",
        "DOUBLE_DOOR_SINGLE_SWING",
        "DOUBLE_DOOR_DOUBLE_SWING",
        "SLIDING_TO_LEFT",
        "SLIDING_TO_RIGHT",
        "DOUBLE_DOOR_SLIDING",
    )
    WINDOW_PARTITIONS = {
        "SINGLE_PANEL": (1, 1, 1),
        "DOUBLE_PANEL_VERTICAL": (2, 2, 1),
        "DOUBLE_PANEL_HORIZONTAL": (2, 1, 2),
        "TRIPLE_PANEL_VERTICAL": (3, 3, 1),
        "TRIPLE_PANEL_HORIZONTAL": (3, 1, 3),
        "TRIPLE_PANEL_BOTTOM": (3, 2, 2),
        "TRIPLE_PANEL_TOP": (3, 2, 2),
        "TRIPLE_PANEL_LEFT": (3, 2, 2),
        "TRIPLE_PANEL_RIGHT": (3, 2, 2),
    }
    LINING = 0.05  # metres: IfcOpenShell's lining thickness and the least lining depth
    PANEL = 0.035  # the depth of a door panel and of a window frame
    PANEL_X = 0.025  # from the lining's side to the panel or frame
    THRESHOLD = 0.025  # a door's threshold, high
    MAX_RISERS = 60

    def make(self, ifc_class, **values):
        """An entity with the attributes the file's schema gives its class; the others, and those given as None, are left out."""
        names = {a.name() for a in self.check_class(ifc_class).all_attributes()}
        return self.m.create_entity(ifc_class, **{k: v for k, v in values.items() if k in names and v is not None})

    def property_definition(self, ifc_class, name, **values):
        """A rooted property definition of the type, such as IfcDoorLiningProperties, with lengths already in file units."""
        import ifcopenshell.guid

        return self.make(ifc_class, GlobalId=ifcopenshell.guid.new(), OwnerHistory=self.owner, Name=name, **values)

    # IFC4.3 deprecates these property definitions (buildingSMART's rule IFC102) for the standard sets of the same name and values.
    LINING_SETS = {
        "IfcDoorLiningProperties": "Pset_DoorLiningProperties",
        "IfcDoorPanelProperties": "Pset_DoorPanelProperties",
        "IfcWindowLiningProperties": "Pset_WindowLiningProperties",
    }

    def lining_properties(self, t, ifc_class, name, **values):
        """A type's lining or panel properties: the property definition, or in IFC4.3 the standard property set that replaces it,
        typed by its template (lengths already in file units). Returns the definition to add to HasPropertySets, None for a set."""
        if self.m.schema != "IFC4X3":
            return self.property_definition(ifc_class, name, **values)
        import ifcopenshell.api.pset
        import ifcopenshell.util.pset

        name = self.LINING_SETS[ifc_class]
        template = ifcopenshell.util.pset.get_template("IFC4X3").get_by_name(name)
        listed = (
            {x.Name for x in (template.HasPropertyTemplates or ()) if x.TemplateType == "P_ENUMERATEDVALUE"}
            if template is not None
            else set()
        )
        pset = ifcopenshell.api.pset.add_pset(self.m, product=t, name=name)
        # An enumerated property (PanelOperation, PanelPosition) takes its value as a list, so that it is written as one (PSE001).
        ifcopenshell.api.pset.edit_pset(
            self.m, pset=pset, properties={k: ([v] if k in listed else v) for k, v in values.items() if v is not None}
        )
        return None

    def relate_type(self, e, t):
        """Type an occurrence that has no layers: the type's IfcRelDefinesByType (one per type), and no PredefinedType or ObjectType of
        the occurrence's own when the type has a PredefinedType other than NOTDEFINED (OJT001). Its material is the type's."""
        import ifcopenshell.guid

        rel = next(iter(getattr(t, "Types", None) or getattr(t, "ObjectTypeOf", None) or ()), None)
        if rel is None:
            self.m.create_entity(
                "IfcRelDefinesByType",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedObjects=[e],
                RelatingType=t,
            )
        else:
            rel.RelatedObjects = list(rel.RelatedObjects or ()) + [e]
        own = str(getattr(t, "PredefinedType", None) or "")
        if own and own != "NOTDEFINED":
            if getattr(e, "PredefinedType", None) is not None:
                e.PredefinedType = None
            if getattr(e, "ObjectType", None) is not None:
                e.ObjectType = None

    def define_element_type(self, op, kind):
        """define_type for the kinds without layers: an IfcColumnType, IfcBeamType, IfcFootingType, IfcRailingType, IfcDoorType,
        IfcWindowType (IfcDoorStyle and IfcWindowStyle in IFC2X3) or IfcCurtainWallType with a ref, its PredefinedType (by default
        COLUMN, BEAM, NOTDEFINED, NOTDEFINED, DOOR, WINDOW and NOTDEFINED) checked against the class's list, and the material, when
        given, as an IfcMaterial of the type that its occurrences take. A door and a window get their geometry (door_geometry,
        window_geometry), and a curtain wall type its parts' types, which take the material (curtain_types). A type of the same class
        and name in the file is an error that names its GlobalId."""
        ifc_class, default = self.ELEMENT_TYPE_KINDS[kind]
        old = self.m.schema == "IFC2X3"
        if old and kind in ("door", "window"):
            ifc_class = "IfcDoorStyle" if kind == "door" else "IfcWindowStyle"
        name = str(op.get("name") or "").strip()[:255]
        if not name:
            raise DataspatiumError("the type needs a name")
        if op.get("layers"):
            raise DataspatiumError("a %s type has no layers; give its material as material, or null" % kind)
        self.check_class(ifc_class)
        twin = next((t for t in self.m.by_type(ifc_class) if str(t.Name or "").strip().lower() == name.lower()), None)
        if twin is not None:
            raise DataspatiumError(
                "the model has an %s of the same name already (GlobalId %s, id %d); give its GlobalId as type, or choose another name"
                % (ifc_class, twin.GlobalId, twin.id())
            )
        value = str(op.get("predefined_type") or "").strip().upper() or None
        if value == "USERDEFINED":
            raise DataspatiumError(
                "USERDEFINED needs the name of a kind of its own, which define_type does not take; choose a value from the class's list, or NOTDEFINED"
            )
        category = str(op.get("material_category") or "").strip().lower() or None
        if category is not None and category not in self.MATERIAL_CATEGORIES:
            raise DataspatiumError("material_category must be one of %s, or null" % ", ".join(self.MATERIAL_CATEGORIES))
        t = self.root(ifc_class, name)
        if _ifc_enum_items(self.check_class(ifc_class)) is not None:
            self.check_enum(t, value or default)
            t.PredefinedType = value or default
        elif value:
            self.notes.append(
                "type %s: %s has no PredefinedType in %s, so %s was left out" % (name, ifc_class, self.m.schema, value)
            )
        if kind == "curtain_wall":
            self.register(t)
            return self.curtain_types(t, op, category)
        material = str(op.get("material") or "").strip()[:255]
        if material:
            if old and category:
                self.notes.append("type %s: IFC2X3 has no material categories, so it was left out" % name)
            self.associate(t, self.named_material(material, None if old else category))
        if kind == "door":
            self.door_geometry(t, op)
        elif kind == "window":
            self.window_geometry(t, op)
        self.register(t)

    def fill_size(self, op, width, height):
        """(width, height, lining depth) in metres of a door or window type: the op's, else the defaults given and LINING."""
        w = _cs_num(op, "width", width, positive=True)
        h = _cs_num(op, "height", height, positive=True)
        d = _cs_num(op, "lining_depth", self.LINING, positive=True)
        if not 0.3 <= w <= 20.0 or not 0.3 <= h <= 20.0:
            raise DataspatiumError("width and height must be 0.3 to 20 m")
        if not self.LINING <= d <= 2.0:
            raise DataspatiumError("lining_depth must be %g to 2 m" % self.LINING)
        return w, h, d

    def type_geometry(self, t, rep, width, height):
        """The type's geometry as its representation map, which its occurrences map; the size for create_opening."""
        t.RepresentationMaps = list(t.RepresentationMaps or ()) + [
            self.m.createIfcRepresentationMap(self.axis3(0, 0, 0), rep)
        ]
        self.fill_sizes[t.id()] = (width, height)

    def door_geometry(self, t, op):
        """A door type's OperationType (one of DOOR_OPERATIONS), its lining and panel properties and IfcOpenShell's door geometry in
        the type's coordinates: x across the opening from 0 to the width, z up from the floor, and the lining from y = 0 to the lining
        depth with the panel at its far side, flush with y = depth, so that the door opens toward +y; a *_LEFT door has its hinges at
        x = 0, on the left as seen in the direction it opens. The threshold fills the lining's depth and there are no casings, which a
        lining shallower than the wall would leave hanging in the air."""
        import ifcopenshell.api.geometry

        operation = str(op.get("operation") or "SINGLE_SWING_LEFT").strip().upper()
        if operation not in self.DOOR_OPERATIONS:
            raise DataspatiumError("operation must be one of %s" % ", ".join(self.DOOR_OPERATIONS))
        self.check_enum(t, operation, "OperationType")
        t.OperationType = operation
        if self.m.schema == "IFC2X3":
            t.ConstructionType = "NOTDEFINED"
            t.Sizeable = False
        t.ParameterTakesPrecedence = False  # the geometry, not the properties, is the door
        w, h, d = self.fill_size(op, 0.9, 2.1)
        u = self.u
        panel_y = d - self.PANEL
        rep = ifcopenshell.api.geometry.add_door_representation(
            self.m,
            context=self.body(),
            overall_height=u(h),
            overall_width=u(w),
            operation_type=operation,  # ty: ignore[invalid-argument-type]
            unit_scale=self.scale,
            lining_properties={
                "LiningDepth": u(d),
                "LiningThickness": u(self.LINING),
                "LiningOffset": 0.0,
                "LiningToPanelOffsetX": u(self.PANEL_X),
                "LiningToPanelOffsetY": u(panel_y),
                "TransomThickness": 0.0,
                "CasingThickness": 0.0,
                "ThresholdDepth": u(d),
                "ThresholdThickness": u(self.THRESHOLD),
                "ThresholdOffset": 0.0,
            },
            panel_properties={
                "PanelDepth": u(self.PANEL),
                "FrameDepth": u(self.PANEL),
                "FrameThickness": u(self.PANEL),
            },
        )
        how = "SLIDING" if "SLIDING" in operation else "DOUBLE_ACTING" if "DOUBLE_SWING" in operation else "SWINGING"
        panels = [("LEFT", 0.5), ("RIGHT", 0.5)] if "DOUBLE_DOOR" in operation else [("MIDDLE", 1.0)]
        lining = self.lining_properties(
            t,
            "IfcDoorLiningProperties",
            "Lining",
            LiningDepth=u(d),
            LiningThickness=u(self.LINING),
            ThresholdDepth=u(d),
            ThresholdThickness=u(self.THRESHOLD),
            LiningOffset=0.0,
            ThresholdOffset=0.0,
            LiningToPanelOffsetX=u(self.PANEL_X),
            LiningToPanelOffsetY=u(panel_y),
        )
        # One standard set holds one panel, so a door of two panels gets none in IFC4.3; the geometry has both.
        leaves = [
            self.lining_properties(
                t,
                "IfcDoorPanelProperties",
                "Panel %d" % (i + 1),
                PanelDepth=u(self.PANEL),
                PanelOperation=how,
                PanelWidth=share,
                PanelPosition=where,
            )
            for i, (where, share) in enumerate(panels)
            if self.m.schema != "IFC4X3" or len(panels) == 1
        ]
        t.HasPropertySets = list(t.HasPropertySets or ()) + [x for x in [lining] + leaves if x is not None]
        self.type_geometry(t, rep, w, h)

    def window_geometry(self, t, op):
        """A window type's PartitioningType (OperationType in IFC2X3; one of WINDOW_PARTITIONS), its lining properties and
        IfcOpenShell's window geometry in the type's coordinates, as a door's (door_geometry): the lining from y = 0 to its depth, the
        frames flush with y = depth, and the mullions and transoms dividing the width and the height evenly."""
        import ifcopenshell.api.geometry

        part = str(op.get("partitioning") or "SINGLE_PANEL").strip().upper()
        if part not in self.WINDOW_PARTITIONS:
            raise DataspatiumError("partitioning must be one of %s" % ", ".join(self.WINDOW_PARTITIONS))
        old = self.m.schema == "IFC2X3"
        attribute = "OperationType" if old else "PartitioningType"
        self.check_enum(t, part, attribute)
        setattr(t, attribute, part)
        if old:
            t.ConstructionType = "NOTDEFINED"
            t.Sizeable = False
        t.ParameterTakesPrecedence = False
        w, h, d = self.fill_size(op, 1.2, 1.2)
        u = self.u
        panels, cols, rows = self.WINDOW_PARTITIONS[part]
        lining = {
            "LiningDepth": u(d),
            "LiningThickness": u(self.LINING),
            "LiningOffset": 0.0,
            "LiningToPanelOffsetX": u(self.PANEL_X),
            "LiningToPanelOffsetY": 0.0,
            "MullionThickness": u(self.LINING),
            "TransomThickness": u(self.LINING),
            "FirstMullionOffset": u(w / cols),
            "SecondMullionOffset": u(2.0 * w / 3.0),
            "FirstTransomOffset": u(h / rows),
            "SecondTransomOffset": u(2.0 * h / 3.0),
        }
        rep = ifcopenshell.api.geometry.add_window_representation(
            self.m,
            context=self.body(),
            overall_height=u(h),
            overall_width=u(w),
            partition_type=part,  # ty: ignore[invalid-argument-type]
            unit_scale=self.scale,
            lining_properties=lining,
            panel_properties=[{"FrameDepth": u(self.PANEL), "FrameThickness": u(self.PANEL)} for _ in range(panels)],
        )
        # The lining properties give the mullions' and transoms' offsets as shares of the width and the height.
        extra = {}
        if cols > 1:
            extra.update(
                MullionThickness=u(self.LINING),
                FirstMullionOffset=1.0 / cols,
                SecondMullionOffset=2.0 / 3.0 if cols == 3 else None,
            )
        if rows > 1:
            extra.update(
                TransomThickness=u(self.LINING),
                FirstTransomOffset=1.0 / rows,
                SecondTransomOffset=2.0 / 3.0 if rows == 3 else None,
            )
        props = self.lining_properties(
            t,
            "IfcWindowLiningProperties",
            "Lining",
            LiningDepth=u(d),
            LiningThickness=u(self.LINING),
            LiningOffset=0.0,
            LiningToPanelOffsetX=u(self.PANEL_X),
            LiningToPanelOffsetY=u(d - self.PANEL),
            **extra,
        )
        if props is not None:
            t.HasPropertySets = list(t.HasPropertySets or ()) + [props]
        self.type_geometry(t, rep, w, h)

    FILL_TYPES = {"IfcDoorType": "door", "IfcDoorStyle": "door", "IfcWindowType": "window", "IfcWindowStyle": "window"}

    def fill_type(self, op, fill):
        """For create_opening with a type: {type, fill, width, height, depth, map}, the fill from the type's class (a fill given must
        match it), the size in metres from define_type, else from a door or window of the type in the file (OverallWidth and
        OverallHeight), else the op's width and height, which must otherwise equal it; the lining's depth from the type's lining
        properties (0 without them), and the type's Body representation map; None without a type."""
        ref = op.get("type")
        if ref is None or str(ref).strip() == "":
            return None
        t = self.entity(ref)
        kind = self.FILL_TYPES.get(t.is_a())
        if kind is None:
            raise DataspatiumError(
                "type must name a door or window type (IfcDoorType, IfcWindowType), as define_type makes them, not the %s %r"
                % (t.is_a(), _ifc_text(getattr(t, "Name", None)))
            )
        if fill and fill != kind:
            raise DataspatiumError("type %r is a %s type, so fill must be %s or null" % (_ifc_text(t.Name), kind, kind))
        size = self.fill_sizes.get(t.id())
        if size is None:
            for rel in getattr(t, "Types", None) or getattr(t, "ObjectTypeOf", None) or ():
                size = next(
                    (
                        (float(o.OverallWidth) * self.scale, float(o.OverallHeight) * self.scale)
                        for o in rel.RelatedObjects or ()
                        if getattr(o, "OverallWidth", None) and getattr(o, "OverallHeight", None)
                    ),
                    None,
                )
        if size is None:
            if op.get("width") is None or op.get("height") is None:
                raise DataspatiumError(
                    "type %r has no %s in the model to take its size from; give width and height"
                    % (_ifc_text(t.Name), kind)
                )
            size = (_cs_num(op, "width", positive=True), _cs_num(op, "height", positive=True))
        for key, v in zip(("width", "height"), size):
            if op.get(key) is not None and abs(_cs_num(op, key, positive=True) - v) > 0.0005:
                raise DataspatiumError(
                    "%s %g m differs from the %g m of type %r; leave width and height null"
                    % (key, _cs_num(op, key), v, _ifc_text(t.Name))
                )
        lining = next(
            (
                p
                for p in (getattr(t, "HasPropertySets", None) or ())
                if p.is_a() in ("IfcDoorLiningProperties", "IfcWindowLiningProperties")
            ),
            None,
        )
        depth = float(lining.LiningDepth) * self.scale if lining is not None and lining.LiningDepth else 0.0
        if lining is None:  # IFC4.3: the standard set that replaces the deprecated definition (lining_properties)
            import ifcopenshell.util.element as el

            value = el.get_pset(
                t, "Pset_DoorLiningProperties" if kind == "door" else "Pset_WindowLiningProperties", "LiningDepth"
            )
            depth = float(value) * self.scale if value else 0.0  # ty: ignore[invalid-argument-type]
        maps = [
            r for r in (t.RepresentationMaps or ()) if (r.MappedRepresentation.RepresentationIdentifier or "") == "Body"
        ]
        return {
            "type": t,
            "fill": kind,
            "width": size[0],
            "height": size[1],
            "depth": depth,
            "map": maps[0] if maps else None,
        }

    def place_typed_fill(self, e, opening, wall, typed, thickness, side):
        """A typed door or window in its opening, which lies along the wall: flush with the wall's left face (seen from the wall's
        start toward its end; the inside of an outer wall drawn counter-clockwise), its axes the wall's, so that a door opens toward
        that face and a *_LEFT door's hinges are at the opening's side nearest the wall's start; side right turns it around, flush
        with the right face, opening toward it, the hinges of a *_LEFT door at the side nearest the wall's end. The type's
        representation map is mapped as it is; a type without one gets the plain panel of an untyped filling, with a note."""
        side = str(side or "left").strip().lower()
        if side not in ("left", "right"):
            raise DataspatiumError(
                "side must be left or right (seen from the wall's start toward its end), or null for left"
            )
        name = _ifc_text(typed["type"].Name)
        half, depth = thickness / 2.0, self.u(typed["depth"])
        if typed["depth"] <= 0:
            self.notes.append(
                "type %s gives no lining depth (IfcDoorLiningProperties or IfcWindowLiningProperties), so it stands at the wall's face"
                % name
            )
        elif depth > thickness + self.u(0.001):
            self.notes.append(
                "the lining of type %s (%.3g m) is deeper than wall %s (%.3g m), so it stands out of the wall's other face"
                % (name, typed["depth"], _ifc_text(wall.Name), thickness * self.scale)
            )
        if side == "left":
            e.ObjectPlacement = self.placement(opening.ObjectPlacement, 0.0, half - depth, 0.0)
        else:
            e.ObjectPlacement = self.placement(
                opening.ObjectPlacement, self.u(typed["width"]), depth - half, 0.0, -1.0, 0.0
            )
        rmap = typed["map"]
        if rmap is None:
            self.notes.append(
                "type %s has no geometry to map, so the %s is drawn as a plain panel" % (name, typed["fill"])
            )
            leaf = self.u(0.05)
            e.Representation = self.box(
                self.u(typed["width"]), leaf, self.u(typed["height"]), 0, depth - leaf, 0
            )  # inside the wall's face
            return
        target = self.m.createIfcCartesianTransformationOperator3D(
            self.direction(1, 0, 0), self.direction(0, 1, 0), self.point(0, 0, 0), 1.0, self.direction(0, 0, 1)
        )
        rep = self.m.createIfcShapeRepresentation(
            rmap.MappedRepresentation.ContextOfItems,
            "Body",
            "MappedRepresentation",
            [self.m.createIfcMappedItem(rmap, target)],
        )
        e.Representation = self.m.createIfcProductDefinitionShape(None, None, [rep])

    def op_create_element(self, op):
        """A building element of ELEMENT_CLASSES with a ref: its class (element_class), PredefinedType and ObjectType checked against
        the schema (element_predefined), a SweptSolid body (element_body) placed at `at` and z in the storey's plane, turned by
        rotation (element_placement), and contained in the storey, or for a part (part_of) aggregated in its whole and on the whole's
        storey, never both; a covering with a host is, in IFC4 and later, a part of that element (IfcRelAggregates) or contained in
        that space, and only in IFC2X3 gets IfcRelCoversBldgElements or IfcRelCoversSpaces (cover); and a type gets its
        IfcRelDefinesByType (relate_type). IfcStair goes to create_stair, IfcCurtainWall to create_curtain_wall."""
        import ifcopenshell.api.aggregate

        cls = self.element_class(op.get("ifc_class"))
        if cls == "IfcStair":
            return self.create_stair(op)
        if cls == "IfcCurtainWall":
            return self.create_curtain_wall(op)
        if op.get("stair") is not None:
            raise DataspatiumError("stair is for IfcStair; %s takes shape" % cls)
        shape = self.element_shape(op.get("shape"))
        whole, storey = self.element_storey(op)
        typ = self.element_type(op, cls)
        depth = shape["depth"]
        layers = self.type_layers(typ) if typ is not None and cls == "IfcCovering" else None
        if layers is not None:
            total = round(sum(float(x.LayerThickness or 0.0) for x in layers.MaterialLayers or ()) * self.scale, 9)
            if depth is not None and abs(depth - total) > 0.0005:
                raise DataspatiumError(
                    "depth %g m differs from the %g m of the layers of type %r; leave depth null"
                    % (depth, total, _ifc_text(typ.Name))
                )
            depth = total
        if depth is None or depth <= 0:
            raise DataspatiumError("shape.depth is required (metres)")
        if op.get("host") not in (None, "") and cls != "IfcCovering":
            raise DataspatiumError(
                "host is for IfcCovering: the element or space it covers; a part of a whole is part_of"
            )
        e = self.root(cls, str(op.get("name") or "").strip()[:255] or cls[3:])
        object_type = str(op.get("object_type") or "").strip()[:255]
        if object_type:
            e.ObjectType = object_type
        self.element_predefined(e, typ, op.get("predefined_type"))
        e.ObjectPlacement = self.element_placement(op, shape, storey, whole)
        e.Representation = self.element_body(shape, depth)
        if whole is not None:
            ifcopenshell.api.aggregate.assign_object(self.m, products=[e], relating_object=whole)
            self.touch(whole)
        else:
            self.contain(e, storey)
        self.cover(op.get("host"), e)
        self.register(e, storey)
        if typ is not None:
            self.relate_type(e, typ)

    def element_class(self, value):
        """The class create_element writes, checked against the file's schema: IfcFurniture and IfcFurnishingElement are IfcFurniture
        in IFC4 and later and IfcFurnishingElement in IFC2X3; a class the schema lacks (IfcChimney in IFC2X3) is an error."""
        name = str(value or "").strip()
        cls = next((c for c in self.ELEMENT_CLASSES if c.lower() == name.lower()), None)
        if cls is None:
            raise DataspatiumError(
                "ifc_class must be one of %s; walls, slabs, roofs, openings with their doors and windows, and spaces have tools of their own"
                % ", ".join(self.ELEMENT_CLASSES)
            )
        if cls in ("IfcFurniture", "IfcFurnishingElement"):
            cls = "IfcFurnishingElement" if self.m.schema == "IFC2X3" else "IfcFurniture"
            if cls.lower() != name.lower():
                self.notes.append("%s is written as %s in %s" % (name, cls, self.m.schema))
        try:
            self.check_class(cls)
        except DataspatiumError as exc:
            raise DataspatiumError(
                "%s; create_element cannot make it in this file's schema, so leave it out or ask the person" % exc
            )
        return cls

    def element_shape(self, shape):
        """create_element's shape, checked: {profile, x, y, radius, outline, depth, along} in metres, depth None when not given."""
        if not isinstance(shape, dict):
            raise DataspatiumError("shape must be {profile, x, y, radius, outline, depth, along}")
        profile = str(shape.get("profile") or "").strip().lower()
        along = str(shape.get("along") or "z").strip().lower()
        if along not in ("z", "x"):
            raise DataspatiumError(
                "shape.along must be z (standing, extruded up) or x (lying, extruded along the rotation)"
            )
        out = {
            "profile": profile,
            "along": along,
            "depth": None if shape.get("depth") is None else _cs_num(shape, "depth", positive=True),
        }
        if profile == "rectangle":
            out.update(x=_cs_num(shape, "x", positive=True), y=_cs_num(shape, "y", positive=True))
        elif profile == "circle":
            out.update(radius=_cs_num(shape, "radius", positive=True))
        elif profile == "polygon":
            if along != "z":
                raise DataspatiumError("a polygon is an outline in the plan, so shape.along must be z")
            outline = shape.get("outline")
            if not isinstance(outline, (list, tuple)) or len(outline) < 3:
                raise DataspatiumError("shape.outline must be at least three [x, y] corners in metres")
            out.update(outline=[_cs_xy(p, "shape.outline") for p in outline])
        else:
            raise DataspatiumError("shape.profile must be rectangle, circle or polygon")
        return out

    def element_storey(self, op):
        """(whole, storey): without part_of no whole and the named storey; with part_of the whole, an element that can have parts (a
        stair, a roof, a railing ...), and its storey, which a storey given must be."""
        ref = op.get("part_of")
        if ref is None or str(ref).strip() == "":
            return None, self.storey(op.get("storey"))
        whole = self.entity(ref)
        if (
            not whole.is_a("IfcElement")
            or whole.is_a("IfcFeatureElement")
            or whole.is_a("IfcDoor")
            or whole.is_a("IfcWindow")
        ):
            raise DataspatiumError(
                "part_of must name an element that has parts, such as a stair or a roof, not the %s %r"
                % (whole.is_a(), _ifc_text(getattr(whole, "Name", None)))
            )
        storey = self.container(whole)
        if storey is None or not storey.is_a("IfcBuildingStorey"):
            raise DataspatiumError(
                "the whole %s %r lies on no storey, so its parts cannot be placed"
                % (whole.is_a(), _ifc_text(whole.Name))
            )
        if op.get("storey") not in (None, "") and self.storey(op.get("storey")).id() != storey.id():
            raise DataspatiumError("a part lies on its whole's storey %r; leave storey null" % _ifc_text(storey.Name))
        return whole, storey

    def element_type(self, op, cls):
        """The type of a create_element step (a ref, id or GlobalId), which must be of the class's type class in the file's schema
        (IfcColumnType for IfcColumn ...; furniture in IFC2X3 also IfcFurnitureType); None without one."""
        ref = op.get("type")
        if ref is None or str(ref).strip() == "":
            return None
        t = self.entity(ref)
        schema = self.schema_def()
        names = [cls + "Type"] + (
            ["IfcFurnitureType", "IfcSystemFurnitureElementType"] if cls == "IfcFurnishingElement" else []
        )
        allowed = [n for n in names if _ifc_declaration(schema, n) is not None]
        if not allowed:
            raise DataspatiumError("%s has no type class in %s; leave type null" % (cls, schema.name()))
        if not any(t.is_a(n) for n in allowed):
            raise DataspatiumError(
                "type must name an %s, as define_type makes them, not the %s %r"
                % (" or ".join(allowed), t.is_a(), _ifc_text(getattr(t, "Name", None)))
            )
        return t

    def element_predefined(self, e, typ, value):
        """The element's PredefinedType: none when its type has one other than NOTDEFINED (OJT001), and a value given must then be
        the type's; otherwise the value, checked against the class's list (USERDEFINED needs ObjectType), or NOTDEFINED. A class
        without PredefinedType in the file's schema (columns in IFC2X3) takes none, with a note when one was given."""
        value = str(value or "").strip().upper() or None
        if _ifc_enum_items(self.check_class(e.is_a())) is None:
            if value:
                self.notes.append(
                    "%s %s: %s has no PredefinedType in %s, so %s was left out"
                    % (e.is_a(), _ifc_text(e.Name), e.is_a(), self.m.schema, value)
                )
            return
        own = str(getattr(typ, "PredefinedType", None) or "") if typ is not None else ""
        if own and own != "NOTDEFINED":
            if value and value != own:
                raise DataspatiumError(
                    "its type %r is %s, which the element takes; leave predefined_type null or give %s"
                    % (_ifc_text(typ.Name), own, own)
                )
            return
        self.predefined(e, value or "NOTDEFINED")

    def local_placement(self, storey, whole, x, y, z, dx, dy):
        """A placement at (x, y, z) metres in the storey's plane with its x axis along (dx, dy): under the storey's placement, or for a
        part under its whole's, with the same place expressed in the whole's coordinates."""
        if whole is None:
            return self.placement(storey.ObjectPlacement, self.u(x), self.u(y), self.u(z), dx, dy)
        s, w = _cs_placement(storey.ObjectPlacement), _cs_placement(whole.ObjectPlacement)
        if s is None or w is None:
            raise DataspatiumError(
                "the placement of the whole %r cannot be read, so its part cannot be placed in it"
                % _ifc_text(whole.Name)
            )
        here = _cs_compose(s, [[dx, dy, 0.0], [-dy, dx, 0.0], [0.0, 0.0, 1.0], [self.u(x), self.u(y), self.u(z)]])
        m = _cs_compose(_cs_invert(w), here)
        r = [[round(c, 9) + 0.0 for c in v] for v in m]
        return self.m.createIfcLocalPlacement(
            whole.ObjectPlacement,
            self.m.createIfcAxis2Placement3D(self.point(*r[3]), self.direction(*r[2]), self.direction(*r[0])),
        )

    def element_placement(self, op, shape, storey, whole):
        """create_element's placement: at [x, y] and z metres in the storey's plane, its x axis turned rotation degrees counter-clockwise
        from the storey's; a polygon's at the storey's origin, z up, unturned, since its outline is in the storey's plane."""
        z = _cs_num(op, "z", 0.0)
        if shape["profile"] == "polygon":
            return self.local_placement(storey, whole, 0.0, 0.0, z, 1.0, 0.0)
        x, y = _cs_xy(op.get("at"), "at")
        a = math.radians(_cs_num(op, "rotation", 0.0, unit="degrees"))
        return self.local_placement(storey, whole, x, y, z, round(math.cos(a), 12) + 0.0, round(math.sin(a), 12) + 0.0)

    def element_body(self, shape, depth):
        """The SweptSolid body of create_element's shape, depth metres: along z the profile in the element's plan, centred on its
        placement (a polygon's outline as given), extruded up; along x the profile across the element's x axis, x its width along y
        and y its height along z, centred on the axis, extruded along x."""
        u = self.u
        if shape["profile"] == "polygon":
            profile = self.m.createIfcArbitraryClosedProfileDef(
                "AREA", None, self.closed([(u(px), u(py)) for px, py in shape["outline"]])
            )
        else:
            centre = self.m.createIfcAxis2Placement2D(self.point(0.0, 0.0), None)
            if shape["profile"] == "rectangle":
                profile = self.m.createIfcRectangleProfileDef("AREA", None, centre, u(shape["x"]), u(shape["y"]))
            else:
                profile = self.m.createIfcCircleProfileDef("AREA", None, centre, u(shape["radius"]))
        if shape["along"] == "z":
            position = self.axis3(0, 0, 0)
        else:  # the profile's x across (the element's y), its y up (z), extruded along the element's x
            position = self.m.createIfcAxis2Placement3D(
                self.point(0, 0, 0), self.direction(1, 0, 0), self.direction(0, 1, 0)
            )
        return self.shape(self.m.createIfcExtrudedAreaSolid(profile, position, self.direction(0, 0, 1), u(depth)))

    def cover(self, host_ref, covering):
        """The covering covers its host: IfcRelCoversBldgElements for a building element, IfcRelCoversSpaces for a space (RelatedSpace
        in IFC2X3), one relationship per host; nothing without a host. IFC4 deprecates both relationships (buildingSMART's rule
        IFC102): there the covering is part of the element it covers (IfcRelAggregates, and so in no storey of its own, SPS007), or
        contained in the space it covers."""
        import ifcopenshell.guid

        if host_ref is None or str(host_ref).strip() == "":
            return
        host = self.entity(host_ref)
        if self.m.schema != "IFC2X3" and (
            host.is_a("IfcSpace")
            or (host.is_a("IfcElement") and not host.is_a("IfcFeatureElement") and not host.is_a("IfcCovering"))
        ):
            import ifcopenshell.api.aggregate
            import ifcopenshell.api.spatial

            if host.is_a("IfcSpace"):
                self.contain(covering, host)
            else:
                if getattr(covering, "ContainedInStructure", None):
                    ifcopenshell.api.spatial.unassign_container(self.m, products=[covering])
                ifcopenshell.api.aggregate.assign_object(self.m, products=[covering], relating_object=host)
            self.touch(host)
            return
        if host.is_a("IfcSpace"):
            rel_class, relating = "IfcRelCoversSpaces", "RelatedSpace" if self.m.schema == "IFC2X3" else "RelatingSpace"
        elif host.is_a("IfcElement") and not host.is_a("IfcFeatureElement") and not host.is_a("IfcCovering"):
            rel_class, relating = "IfcRelCoversBldgElements", "RelatingBuildingElement"
        else:
            raise DataspatiumError(
                "host must name a building element or a space, not the %s %r"
                % (host.is_a(), _ifc_text(getattr(host, "Name", None)))
            )
        rel = next((r for r in self.m.by_type(rel_class) if getattr(r, relating, None) == host), None)
        if rel is None:
            self.m.create_entity(
                rel_class,
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedCoverings=[covering],
                **{relating: host},
            )
        else:
            rel.RelatedCoverings = list(rel.RelatedCoverings or ()) + [covering]
        self.touch(host)

    def create_stair(self, op):
        """create_element's IfcStair: a straight stair (STRAIGHT_RUN_STAIR; ShapeType in IFC2X3) with a ref, contained in the storey at
        `at` and z, rising in the direction of rotation, that aggregates one sloped IfcStairFlight STRAIGHT (flight_solid) with its
        number of risers and treads, riser height and tread length as attributes and in Pset_StairFlightCommon, and with a landing
        an IfcSlab LANDING at the top: the flight's width, `landing` metres long, its top at the stair's height and as thick as the
        flight. The parts get no refs; a railing is create_element's part_of the stair."""
        import ifcopenshell.api.aggregate
        import ifcopenshell.api.pset

        if op.get("shape") is not None:
            raise DataspatiumError("an IfcStair takes stair, not shape")
        if op.get("part_of") not in (None, "") or op.get("host") not in (None, ""):
            raise DataspatiumError(
                "a stair is not part_of another element and covers nothing, so part_of and host must be null"
            )
        st = op.get("stair")
        if not isinstance(st, dict):
            raise DataspatiumError(
                "an IfcStair needs stair: {risers, riser_height, tread_length, width, thickness, landing}"
            )
        n = st.get("risers")
        if isinstance(n, float) and n.is_integer():
            n = int(n)
        if not isinstance(n, int) or isinstance(n, bool) or not 2 <= n <= self.MAX_RISERS:
            raise DataspatiumError("stair.risers must be a whole number from 2 to %d" % self.MAX_RISERS)
        h = _cs_num(st, "riser_height", positive=True)
        g = _cs_num(st, "tread_length", positive=True)
        width = _cs_num(st, "width", 1.0, positive=True)
        t = _cs_num(st, "thickness", max(0.2, h), positive=True)
        if t < h - 1e-9:
            raise DataspatiumError(
                "stair.thickness (%g m, the waist at right angles to the slope) must be at least the riser height (%g m)"
                % (t, h)
            )
        landing = None if st.get("landing") is None else _cs_num(st, "landing", positive=True)
        value = str(op.get("predefined_type") or "").strip().upper() or "STRAIGHT_RUN_STAIR"
        if value != "STRAIGHT_RUN_STAIR":
            raise DataspatiumError(
                "create_element makes straight stairs of one flight: predefined_type STRAIGHT_RUN_STAIR or null"
            )
        storey = self.storey(op.get("storey"))
        x, y = _cs_xy(op.get("at"), "at")
        z = _cs_num(op, "z", 0.0)
        a = math.radians(_cs_num(op, "rotation", 0.0, unit="degrees"))
        typ = self.element_type(op, "IfcStair")
        name = str(op.get("name") or "").strip()[:255] or "Stair"
        stair = self.root("IfcStair", name)
        object_type = str(op.get("object_type") or "").strip()[:255]
        if object_type:
            stair.ObjectType = object_type
        stair.ObjectPlacement = self.placement(
            storey.ObjectPlacement,
            self.u(x),
            self.u(y),
            self.u(z),
            round(math.cos(a), 12) + 0.0,
            round(math.sin(a), 12) + 0.0,
        )
        if self.m.schema == "IFC2X3":
            if self.check_enum(stair, value, "ShapeType"):
                stair.ShapeType = value
        else:
            self.element_predefined(
                stair,
                typ,
                value
                if typ is None or str(getattr(typ, "PredefinedType", None) or "NOTDEFINED") == "NOTDEFINED"
                else None,
            )
        self.contain(stair, storey)
        self.register(stair, storey)
        flight = self.root("IfcStairFlight", "%s 1" % name)
        flight.ObjectPlacement = self.placement(stair.ObjectPlacement, 0, 0, 0)
        flight.Representation = self.shape(self.flight_solid(n, h, g, width, t))
        self.predefined(flight, "STRAIGHT")
        # IFC4 deprecates the flight's own counts and measures (buildingSMART's rule IFC102); Pset_StairFlightCommon below has them.
        names = (
            {d.name() for d in self.check_class("IfcStairFlight").all_attributes()}
            if self.m.schema == "IFC2X3"
            else set()
        )
        for key, v in (
            ("NumberOfRisers", n),
            ("NumberOfRiser", n),
            ("NumberOfTreads", n - 1),
            ("RiserHeight", self.u(h)),
            ("TreadLength", self.u(g)),
        ):
            if key in names:
                setattr(flight, key, v)
        parts = [flight]
        if landing is not None:
            slab = self.root("IfcSlab", "%s 2" % name)
            slab.ObjectPlacement = self.placement(stair.ObjectPlacement, 0, 0, 0)
            slab.Representation = self.box(
                self.u(landing), self.u(width), self.u(t), self.u((n - 1) * g), -self.u(width) / 2.0, self.u(n * h - t)
            )
            self.predefined(slab, "LANDING")
            parts.append(slab)
        ifcopenshell.api.aggregate.assign_object(self.m, products=parts, relating_object=stair)
        for part in parts:
            self.register(part, storey, ref=False)
        pset = ifcopenshell.api.pset.add_pset(self.m, product=flight, name="Pset_StairFlightCommon")
        ifcopenshell.api.pset.edit_pset(
            self.m,
            pset=pset,
            properties={
                "NumberOfRiser": n,
                "NumberOfTreads": n - 1,
                "RiserHeight": self.u(h),
                "TreadLength": self.u(g),
            },
        )
        if typ is not None:
            self.relate_type(stair, typ)

    def flight_solid(self, n, h, g, width, t):
        """A straight flight in its stair's coordinates (metres in, file units out): the steps' profile in the vertical plane of the
        walking line, x the run and z the rise from the first riser's foot at (0, 0), extruded across the width centred on that line.
        It has the first n - 1 risers of h and the n - 1 treads of g; the last riser is the front edge of the landing or the floor
        above, which begins at x = (n - 1) g, where the flight ends in a vertical cut. Its underside runs parallel to the nosings' line,
        t below it at right angles, and meets the floor, on which the first step stands."""
        drop = t * math.hypot(g, h) / g  # the waist measured vertically, more than h since t >= h
        run, rise = (n - 1) * g, n * h
        pts = [(0.0, 0.0)]
        for i in range(n - 1):
            pts += [(i * g, (i + 1) * h), ((i + 1) * g, (i + 1) * h)]
        if rise - drop <= 1e-9:
            pts.append((run, 0.0))  # a short, thick flight is one block
        else:
            pts += [(run, rise - drop), ((drop - h) * g / h, 0.0)]
        u = self.u
        profile = self.m.createIfcArbitraryClosedProfileDef(
            "AREA", None, self.closed([(u(px), u(pz)) for px, pz in pts])
        )
        position = self.m.createIfcAxis2Placement3D(
            self.point(0.0, u(width) / 2.0, 0.0), self.direction(0, -1, 0), self.direction(1, 0, 0)
        )
        return self.m.createIfcExtrudedAreaSolid(profile, position, self.direction(0, 0, 1), u(width))

    # -- glass façades: a curtain wall of panels between mullions and transoms on an even grid, and its type with its parts' types
    CURTAIN_SIZES = (
        0.05,
        0.15,
        0.03,
    )  # metres: a member's width in the façade's plane and depth across it, and a panel's thickness
    CURTAIN_SPACING = 1.5  # metres: the most a bay is wide, or a row high, when neither a count nor a spacing is given
    MAX_BAYS = 100
    MAX_ROWS = 40
    MAX_PANELS = 200
    CURTAIN_WORDS = {
        "panel": "Panel",
        "mullion": "Mullion",
        "transom": "Transom",
    }  # the parts' names when part_names gives none
    CURTAIN_MATERIALS = (
        ("Glass", "glass"),
        ("Aluminium", "aluminium"),
    )  # the panels' and the members' when no type gives them

    def curtain_memo(self):
        """This change set's curtain walls: {"sizes": {type id: (member width, member depth, panel thickness)}, "facts": {curtain wall
        id: (length, height, depth)}}, in metres."""
        if getattr(self, "_curtain", None) is None:
            self._curtain = {"sizes": {}, "facts": {}}
        return self._curtain

    def curtain_sizes(self, op):
        """(member width, member depth, panel thickness) in metres of a curtain wall type's step, checked, CURTAIN_SIZES for those not
        given: a panel stands in the middle of the members' depth, so it is thinner."""
        w0, d0, t0 = self.CURTAIN_SIZES
        w = _cs_num(op, "member_width", w0, positive=True)
        d = _cs_num(op, "member_depth", d0, positive=True)
        t = _cs_num(op, "panel_thickness", t0, positive=True)
        if not 0.02 <= w <= 0.5 or not 0.02 <= d <= 1.0:
            raise DataspatiumError("member_width must be 0.02 to 0.5 m and member_depth 0.02 to 1 m")
        if not 0.004 <= t < d:
            raise DataspatiumError("panel_thickness must be at least 0.004 m and less than member_depth (%g m)" % d)
        return w, d, t

    @staticmethod
    def type_material(t, cls):
        """The material of a class (IfcMaterialProfileSet ...) that the type's own IfcRelAssociatesMaterial gives; None without one."""
        for rel in getattr(t, "HasAssociations", None) or ():
            if rel.is_a("IfcRelAssociatesMaterial") and rel.RelatingMaterial.is_a(cls):
                return rel.RelatingMaterial
        return None

    def curtain_types(self, t, op, category):
        """A curtain wall type's parts' types, of the type's name: an IfcPlateType CURTAIN_PANEL whose IfcMaterialLayerSet is one layer
        of the panels' material (panel_material, by default Glass of the category glass) as thick as panel_thickness, and an
        IfcMemberType MULLION for the mullions and the transoms alike, since IFC's MULLION is any frame member between panels and
        IfcMemberTypeEnum has no TRANSOM, whose IfcMaterialProfileSet is one rectangle of member_width by member_depth in the frame's
        material (material and material_category, by default Aluminium of the category aluminium); IFC2X3, without profile sets, gives
        the member type the material alone. A plate or member type of the name in the file is an error."""
        name = str(t.Name)
        for cls in ("IfcPlateType", "IfcMemberType"):
            twin = next((x for x in self.m.by_type(cls) if str(x.Name or "").strip().lower() == name.lower()), None)
            if twin is not None:
                raise DataspatiumError(
                    "the model has an %s of the same name already (GlobalId %s, id %d), which the curtain wall type's parts would get; choose another name"
                    % (cls, twin.GlobalId, twin.id())
                )
        w, d, thick = self.curtain_sizes(op)
        old = self.m.schema == "IFC2X3"
        (glass_name, glass_category), (frame_name, frame_category) = self.CURTAIN_MATERIALS
        panel_material = str(op.get("panel_material") or "").strip()[:255]
        material = str(op.get("material") or "").strip()[:255]
        if old and category:
            self.notes.append("type %s: IFC2X3 has no material categories, so it was left out" % name)
        glass = self.named_material(panel_material or glass_name, None if old else glass_category)
        frame = self.named_material(
            material or frame_name, None if old else category or (None if material else frame_category)
        )
        plate = self.root("IfcPlateType", name)
        self.predefined(plate, "CURTAIN_PANEL")
        layer = self.m.create_entity("IfcMaterialLayer", Material=glass, LayerThickness=round(self.u(thick), 9))
        self.associate(plate, self.m.create_entity("IfcMaterialLayerSet", MaterialLayers=[layer], LayerSetName=name))
        member = self.root("IfcMemberType", name)
        self.predefined(member, "MULLION")
        if old:
            self.associate(member, frame)
        else:
            profile = self.m.create_entity(
                "IfcRectangleProfileDef",
                ProfileType="AREA",
                ProfileName=name,
                XDim=round(self.u(w), 9),
                YDim=round(self.u(d), 9),
            )
            item = self.m.create_entity("IfcMaterialProfile", Material=frame, Profile=profile)
            self.associate(member, self.m.create_entity("IfcMaterialProfileSet", Name=name, MaterialProfiles=[item]))
        for x in (plate, member):
            self.register(x, ref=False)
        self.curtain_memo()["sizes"][t.id()] = (w, d, thick)

    def curtain_parts(self, typ):
        """(plate type, member type, (member width, member depth, panel thickness)) of a curtain wall type: its parts' types of its name
        (curtain_types), with the sizes this change set gave them or that their layer and profile sets give; None, with a note, when
        the file has no such pair, and the curtain wall's parts are then made as without a type."""
        name = str(typ.Name or "")
        plate = next(
            (
                x
                for x in self.m.by_type("IfcPlateType")
                if str(x.Name or "") == name and str(x.PredefinedType or "") == "CURTAIN_PANEL"
            ),
            None,
        )
        member = next(
            (
                x
                for x in self.m.by_type("IfcMemberType")
                if str(x.Name or "") == name and str(x.PredefinedType or "") == "MULLION"
            ),
            None,
        )
        if plate is None or member is None:
            self.note_once(
                "curtain wall type %s has no IfcPlateType CURTAIN_PANEL and IfcMemberType MULLION of its name, so the parts of its curtain walls are untyped, "
                "with the default sizes and materials" % _ifc_text(typ.Name)
            )
            return None
        sizes = self.curtain_memo()["sizes"].get(typ.id())
        if sizes is None:
            w, d, thick = self.CURTAIN_SIZES
            layers = self.type_layers(plate)
            if layers is not None and layers.MaterialLayers:
                thick = sum(float(x.LayerThickness or 0.0) for x in layers.MaterialLayers) * self.scale
            profiles = self.type_material(member, "IfcMaterialProfileSet") if self.m.schema != "IFC2X3" else None
            profile = (
                next(
                    (
                        p.Profile
                        for p in (profiles.MaterialProfiles or ())
                        if p.Profile is not None and p.Profile.is_a("IfcRectangleProfileDef")
                    ),
                    None,
                )
                if profiles is not None
                else None
            )
            if profile is not None:
                w, d = float(profile.XDim) * self.scale, float(profile.YDim) * self.scale
            else:
                self.note_once(
                    "member type %s gives no rectangle profile, so the members of its curtain walls are %g × %g m"
                    % (_ifc_text(member.Name), w, d)
                )
            sizes = (w, d, thick)
        return plate, member, sizes

    def curtain_count(self, c, count_key, spacing_key, span, most):
        """How many bays (or rows) divide `span` metres evenly: curtain's count, else as many as keep each within its spacing (by
        default CURTAIN_SPACING), 1 to `most`."""
        count, spacing = c.get(count_key), c.get(spacing_key)
        if count is not None and spacing is not None:
            raise DataspatiumError("give curtain.%s or curtain.%s, not both" % (count_key, spacing_key))
        if count is None:
            s = _cs_num(c, spacing_key, self.CURTAIN_SPACING, positive=True)
            count = max(1, int(math.ceil(span / s - 1e-9)))
            if count > most:
                raise DataspatiumError(
                    "curtain.%s %g m divides %g m into %d; at most %d, so give a larger spacing"
                    % (spacing_key, s, span, count, most)
                )
            return count
        if isinstance(count, float) and count.is_integer():
            count = int(count)
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= most:
            raise DataspatiumError("curtain.%s must be a whole number from 1 to %d" % (count_key, most))
        return count

    def create_curtain_wall(self, op):
        """create_element's IfcCurtainWall, a glass façade with a ref: along its axis from curtain.start to curtain.end in the storey's
        plane, curtain.height high from curtain.base above the storey, contained in the storey, with its axis as its 'Axis' (Curve2D)
        so that the topology reads it as a wall, and no body of its own: it aggregates its parts, each placed relative to it, on an
        even grid of bays along the axis and rows up (curtain_count). The mullions stand on the bays' lines from the base to the top,
        the outer ones flush with the ends; the transoms lie between the mullions on the rows' lines, the lowest on the base and the
        highest under the top; and a panel fills each field between them in the middle of the members' depth (curtain_member,
        curtain_panel). A type (an IfcCurtainWallType of define_type) gives the parts their types, sizes and materials (curtain_parts,
        curtain_materials). The parts get no refs and are named by the curtain wall, their kind (part_names, in the person's language)
        and their place: a mullion by its line from the start, a transom by its bay and its line from the base, a panel by its bay and
        row. Qto_CurtainWallQuantities follows (qto_curtain_wall), and IsExternal from the topology (write_external)."""
        import ifcopenshell.guid

        if op.get("shape") is not None or op.get("stair") is not None:
            raise DataspatiumError("an IfcCurtainWall takes curtain, not shape or stair")
        if op.get("at") is not None or _cs_num(op, "z", 0.0) != 0 or _cs_num(op, "rotation", 0.0, unit="degrees") != 0:
            raise DataspatiumError(
                "a curtain wall runs along its axis as a wall does: give curtain.start, curtain.end and curtain.base, and at, z and rotation null"
            )
        if op.get("part_of") not in (None, "") or op.get("host") not in (None, ""):
            raise DataspatiumError(
                "a curtain wall is not part_of another element and covers nothing, so part_of and host must be null"
            )
        c = op.get("curtain")
        if not isinstance(c, dict):
            raise DataspatiumError(
                "an IfcCurtainWall needs curtain: {start, end, height, base, bays, bay_width, rows, row_height}"
            )
        storey = self.storey(op.get("storey"))
        x1, y1 = _cs_xy(c.get("start"), "curtain.start")
        x2, y2 = _cs_xy(c.get("end"), "curtain.end")
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 0.3:
            raise DataspatiumError("curtain.start and curtain.end must be at least 0.3 m apart")
        height = _cs_num(c, "height", 3.0, positive=True)
        base = _cs_num(c, "base", 0.0)
        typ = self.element_type(op, "IfcCurtainWall")
        parts = self.curtain_parts(typ) if typ is not None else None
        w, d, thick = parts[2] if parts is not None else self.CURTAIN_SIZES
        n = self.curtain_count(c, "bays", "bay_width", length, self.MAX_BAYS)
        m = self.curtain_count(c, "rows", "row_height", height, self.MAX_ROWS)
        if n * m > self.MAX_PANELS:
            raise DataspatiumError(
                "%d bays by %d rows are %d panels; at most %d in one curtain wall, so give fewer or make several"
                % (n, m, n * m, self.MAX_PANELS)
            )
        step, rise = (length - w) / n, (height - w) / m  # between the members' centre lines
        pw, ph = step - w, rise - w  # a panel between the members
        if pw < 0.05 or ph < 0.05:
            raise DataspatiumError(
                "the panels would be %.3g m wide and %.3g m high between members %g m wide; give fewer bays or rows"
                % (pw, ph, w)
            )
        name = str(op.get("name") or "").strip()[:255] or "Curtain wall"
        cw = self.root("IfcCurtainWall", name)
        object_type = str(op.get("object_type") or "").strip()[:255]
        if object_type:
            cw.ObjectType = object_type
        self.element_predefined(cw, typ, op.get("predefined_type"))
        cw.ObjectPlacement = self.placement(
            storey.ObjectPlacement, self.u(x1), self.u(y1), self.u(base), (x2 - x1) / length, (y2 - y1) / length
        )
        axis = self.m.createIfcPolyline([self.point(0, 0), self.point(self.u(length), 0)])
        cw.Representation = self.m.createIfcProductDefinitionShape(
            None, None, [self.m.createIfcShapeRepresentation(self.axis_context(), "Axis", "Curve2D", [axis])]
        )
        self.contain(cw, storey)
        self.register(cw, storey)
        given = op.get("part_names") if isinstance(op.get("part_names"), dict) else {}
        words = {k: str(given.get(k) or "").strip()[:60] or v for k, v in self.CURTAIN_WORDS.items()}
        xs = [w / 2.0 + i * step for i in range(n + 1)]
        zs = [w / 2.0 + j * rise for j in range(m + 1)]
        members = [
            self.curtain_member(cw, "%s %s %d" % (name, words["mullion"], i + 1), xs[i], 0.0, True, height, w, d)
            for i in range(n + 1)
        ]
        members += [
            self.curtain_member(
                cw, "%s %s %d.%d" % (name, words["transom"], i + 1, j + 1), xs[i] + w / 2.0, zs[j], False, pw, w, d
            )
            for j in range(m + 1)
            for i in range(n)
        ]
        panels = [
            self.curtain_panel(
                cw,
                "%s %s %d.%d" % (name, words["panel"], i + 1, j + 1),
                xs[i] + w / 2.0,
                zs[j] + w / 2.0,
                pw,
                ph,
                thick,
            )
            for j in range(m)
            for i in range(n)
        ]
        # One IfcRelAggregates of new parts placed relative to the whole already: aggregate.assign_object would place each anew.
        self.m.create_entity(
            "IfcRelAggregates",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner,
            RelatingObject=cw,
            RelatedObjects=members + panels,
        )
        for p in members + panels:
            self.register(p, storey, ref=False)
        self.curtain_materials(cw, typ, parts, members, panels)
        self.curtain_memo()["facts"][cw.id()] = (length, height, d)

    def curtain_member(self, cw, name, x, z, upright, length, w, d):
        """An IfcMember at (x, 0, z) metres in its curtain wall's coordinates: a mullion (upright) with its placement's z up and x
        along the axis, or a transom with its z along the axis and x up; its body a w by d rectangle centred on its placement, w in
        the façade's plane and d across it, extruded `length` metres along its z, as IFC4's IfcMemberStandardCase is drawn (the
        deprecated class itself is never written, IFC102)."""
        e = self.root("IfcMember", name)
        axis, ref = ((0, 0, 1), (1, 0, 0)) if upright else ((1, 0, 0), (0, 0, 1))
        e.ObjectPlacement = self.m.createIfcLocalPlacement(
            cw.ObjectPlacement,
            self.m.createIfcAxis2Placement3D(
                self.point(self.u(x), 0.0, self.u(z)), self.direction(*axis), self.direction(*ref)
            ),
        )
        profile = self.m.createIfcRectangleProfileDef(
            "AREA", None, self.m.createIfcAxis2Placement2D(self.point(0.0, 0.0), None), self.u(w), self.u(d)
        )
        e.Representation = self.shape(
            self.m.createIfcExtrudedAreaSolid(profile, self.axis3(0, 0, 0), self.direction(0, 0, 1), self.u(length))
        )
        return e

    def curtain_panel(self, cw, name, x, z, width, height, thick):
        """An IfcPlate whose lower corner nearest the curtain wall's start is at (x, z) metres along and up its curtain wall: its
        placement's z across the façade toward the axis's right side (-y), its x along the axis and its y up, and its body the panel's
        face, width by height, extruded `thick` metres along that z from y = thick / 2, so that it lies in the middle of the members'
        depth and its layer set usage (AXIS3) runs through its thickness."""
        e = self.root("IfcPlate", name)
        e.ObjectPlacement = self.m.createIfcLocalPlacement(
            cw.ObjectPlacement,
            self.m.createIfcAxis2Placement3D(
                self.point(self.u(x), self.u(thick / 2.0), self.u(z)), self.direction(0, -1, 0), self.direction(1, 0, 0)
            ),
        )
        position = self.m.createIfcAxis2Placement2D(self.point(self.u(width / 2.0), self.u(height / 2.0)), None)
        profile = self.m.createIfcRectangleProfileDef("AREA", None, position, self.u(width), self.u(height))
        e.Representation = self.shape(
            self.m.createIfcExtrudedAreaSolid(profile, self.axis3(0, 0, 0), self.direction(0, 0, 1), self.u(thick))
        )
        return e

    def relate_all(self, es, t):
        """Type many occurrences at once, as relate_type types one: the type's IfcRelDefinesByType (one per type) gets them all."""
        import ifcopenshell.guid

        rel = next(iter(getattr(t, "Types", None) or getattr(t, "ObjectTypeOf", None) or ()), None)
        if rel is None:
            self.m.create_entity(
                "IfcRelDefinesByType",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedObjects=list(es),
                RelatingType=t,
            )
        else:
            rel.RelatedObjects = list(rel.RelatedObjects or ()) + list(es)

    def curtain_materials(self, cw, typ, parts, members, panels):
        """The kinds and materials of a curtain wall and its parts. A type types the curtain wall. With its parts' types
        (curtain_parts) the panels are typed by the plate type and share one IfcMaterialLayerSetUsage of its layers (AXIS3, through
        their thickness), and the members by the member type with, in IFC4 and later, one IfcMaterialProfileSetUsage of its profiles
        (CardinalPoint 5: the profile's centre on the member's axis); the kinds are then the types' (OJT001). Without them the panels
        are IfcPlate CURTAIN_PANEL of Glass and the members IfcMember MULLION of Aluminium (CURTAIN_MATERIALS), one
        IfcRelAssociatesMaterial each for all of them; IFC2X3 has no PredefinedType on plates and members, which a note says."""
        import ifcopenshell.guid

        def associate_all(products, material):
            self.m.create_entity(
                "IfcRelAssociatesMaterial",
                GlobalId=ifcopenshell.guid.new(),
                OwnerHistory=self.owner,
                RelatedObjects=list(products),
                RelatingMaterial=material,
            )

        if typ is not None:
            self.relate_type(cw, typ)
        if parts is not None:
            plate, member, _ = parts
            self.relate_all(panels, plate)
            self.relate_all(members, member)
            layers = self.type_layers(plate)
            if layers is not None:
                associate_all(
                    panels,
                    self.m.create_entity(
                        "IfcMaterialLayerSetUsage",
                        ForLayerSet=layers,
                        LayerSetDirection="AXIS3",
                        DirectionSense="POSITIVE",
                        OffsetFromReferenceLine=0.0,
                    ),
                )
            profiles = self.type_material(member, "IfcMaterialProfileSet") if self.m.schema != "IFC2X3" else None
            if profiles is not None:
                associate_all(
                    members, self.m.create_entity("IfcMaterialProfileSetUsage", ForProfileSet=profiles, CardinalPoint=5)
                )
            return
        old = self.m.schema == "IFC2X3"
        if old:
            self.note_once(
                "IFC2X3 has no PredefinedType on IfcPlate and IfcMember, so the panels (CURTAIN_PANEL) and the mullions and transoms (MULLION) of "
                "the curtain walls are told by their classes alone; a curtain wall type (define_type curtain_wall) gives them types that say it"
            )
        else:
            for parts_of_kind, value in ((panels, "CURTAIN_PANEL"), (members, "MULLION")):
                if self.check_enum(parts_of_kind[0], value):
                    for p in parts_of_kind:
                        p.PredefinedType = value
        (glass_name, glass_category), (frame_name, frame_category) = self.CURTAIN_MATERIALS
        associate_all(panels, self.named_material(glass_name, None if old else glass_category))
        associate_all(members, self.named_material(frame_name, None if old else frame_category))

    def qto_curtain_wall(self, e):
        """A curtain wall that create_element made in this change set (curtain_memo): Length along its axis, Height, Width the members'
        depth, and its side, gross and net alike since it has no openings; none for another."""
        facts = self.curtain_memo()["facts"].get(e.id())
        if facts is None:
            return None
        length, height, depth = facts
        return {
            "Length": ("length", length),
            "Height": ("length", height),
            "Width": ("length", depth),
            "GrossSideArea": ("area", length * height),
            "NetSideArea": ("area", length * height),
        }

    def finish(self):
        """After the operations: the joins of the walls the change set created (join_walls), the openings of the walls joined anew
        (check_openings) and the spaces along the walls it moved (fit_spaces), then the base quantities of what it created or changed
        (base_quantities) and last the space boundaries near it (space_boundaries), after the spaces have been fitted. A fault in any
        becomes a note in the report, and the operations stand."""
        for step, what in (
            (self.join_walls, "the wall joins"),
            (self.check_openings, "the check of the openings in the joined walls"),
            (self.fit_spaces, "the spaces along the moved walls"),
            (self.base_quantities, "the base quantities"),
            (self.space_boundaries, "the space boundaries"),
        ):
            try:
                step()
            except Exception as exc:
                self.notes.append("%s could not be made: %s: %s" % (what, type(exc).__name__, exc))
        self.finish_properties()

    def finish_properties(self):
        """After the base quantities: LoadBearing and Status as the create steps gave them (write_marked) and IsExternal from the
        topology (write_external). A fault becomes a note in the report, and the operations stand."""
        for step, what in (
            (lambda: self.write_marked("LoadBearing"), "LoadBearing"),
            (lambda: self.write_marked("Status"), "Status"),
            (self.write_external, "IsExternal"),
        ):
            try:
                step()
            except Exception as exc:
                self.note_once("%s could not be set: %s: %s" % (what, type(exc).__name__, exc))

    JOIN_RIGHT = 0.02  # |cos| of the angle between two walls below which they meet at right angles (about one degree)
    JOIN_LINE = 0.98  # and above which they run in line or side by side (about eleven degrees)

    @staticmethod
    def end_type(k):
        return "ATSTART" if k == 0 else "ATEND"

    def join_walls(self):
        """IfcRelConnectsPathElements where the axis of a wall the change set created meets another wall's on its storey, within the
        topology's tolerance (half the storey's thickest wall plus 0.05 m): two ends at one point are an L, ATSTART or ATEND on both
        with the main wall relating, and an end on another wall's axis between its ends a T, ATSTART or ATEND on the abutting wall,
        which relates, and ATPATH on the through wall, as IfcOpenShell's connect_wall writes them. Three or more ends at one point
        join when two of them continue each other in a line, which the others then abut. The bodies are cut square and the axes stay
        as drawn: at an L the main wall (the thicker, then the longer, then the older) reaches half the other's thickness past the
        corner and the other stops half the main wall's thickness short of it, so that the corner has neither a gap outside nor an
        overlap inside, and at a T the abutting wall stops at the through wall's face, so that no volume is counted twice. Only
        bodies of create_wall's form are cut (wall_box); walls at another angle are joined as drawn, and ends the app cannot sort
        out are left alone, both with a note. Walls joined before keep their joint; walls whose joins an edit or a move undid (rejoin)
        count as new."""
        new = {r["id"] for r in self.created if r.get("ifc_class") == "IfcWall"} | self.rejoin
        storeys = {}
        for i in sorted(new):
            try:
                s = self.container(self.m.by_id(i))
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            if s is not None:
                storeys.setdefault(s.id(), s)
        if not storeys:
            return
        done = {
            frozenset((r.RelatingElement.id(), r.RelatedElement.id()))
            for r in self.m.by_type("IfcRelConnectsPathElements")
            if r.RelatingElement is not None and r.RelatedElement is not None
        }
        for s in storeys.values():
            walls = [
                self.join_wall(e, e.id() in new)
                for rel in (getattr(s, "ContainsElements", None) or ())
                for e in (rel.RelatedElements or ())
                if e.is_a("IfcWall")
            ]
            walls = sorted((w for w in walls if w is not None), key=lambda w: w["id"])
            if len(walls) > 1:
                self.join_storey(walls, max([w["t"] for w in walls if w["t"] <= 1.0] or [0.2]) / 2.0 + 0.05, done)

    def join_wall(self, wall, new):
        """A wall as join_walls reads it: its axis in the world's plan in metres, the axis's direction and length, its thickness, its
        placement (a matrix in file units) and its body when create_wall drew it; None without a straight axis of two points."""
        try:
            m = _cs_placement(getattr(wall, "ObjectPlacement", None))
            rep = getattr(wall, "Representation", None)
            line = next(
                (
                    pts
                    for r in (rep.Representations if rep is not None and m is not None else ())
                    if (r.RepresentationIdentifier or "") == "Axis"
                    for pts in (_topo_curve(item) for item in (r.Items or ()))
                    if pts and len(pts) == 2
                ),
                None,
            )
            if line is None:
                return None
            s = self.scale
            a, b = [(q[0] * s, q[1] * s) for q in (_cs_at(m, (x, y, 0.0)) for x, y in line)]
            length = math.dist(a, b)
            box = self.wall_box(wall)
            t = float(box.SweptArea.YDim) * s if box is not None else _topo_thickness(wall, s)
        except Exception:
            return None
        if length <= 1e-6 or not t:
            return None
        return {
            "e": wall,
            "id": wall.id(),
            "new": new,
            "a": a,
            "b": b,
            "u": ((b[0] - a[0]) / length, (b[1] - a[1]) / length),
            "len": length,
            "t": float(t),
            "m": m,
            "line": line,
            "box": box,
        }

    @staticmethod
    def wall_box(element):
        """The body of a wall or an opening when it is one IfcExtrudedAreaSolid of an IfcRectangleProfileDef along the element's x,
        extruded straight up from a position that only lifts it, as create_wall and create_opening draw them; else None."""
        rep = getattr(element, "Representation", None)
        bodies = [
            r for r in (rep.Representations if rep is not None else ()) if (r.RepresentationIdentifier or "") == "Body"
        ]
        items = list(bodies[0].Items or ()) if len(bodies) == 1 else []
        if (
            len(items) != 1
            or not items[0].is_a("IfcExtrudedAreaSolid")
            or not items[0].SweptArea.is_a("IfcRectangleProfileDef")
            or items[0].SweptArea.Position is None
        ):
            return None
        item = items[0]
        m, p = _cs_axes(item.Position), _cs_axes(item.SweptArea.Position)
        d = _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
        upright = (
            abs(m[0][0] - 1.0) < 1e-9 and abs(m[2][2] - 1.0) < 1e-9 and abs(m[3][0]) < 1e-9 and abs(m[3][1]) < 1e-9
        )
        return item if upright and d is not None and abs(d[2] - 1.0) < 1e-9 and abs(p[0][0] - 1.0) < 1e-9 else None

    def cut_end(self, w, end, q):
        """Move the end of a wall's body on the side of its axis's start (end 0) or end (1) to the plan point q (metres, world) along
        the wall; False, with nothing moved, when the body would be shorter than a centimetre."""
        item, m, s = w["box"], w["m"], self.scale
        profile = item.SweptArea
        x = (q[0] / s - m[3][0]) * m[0][0] + (q[1] / s - m[3][1]) * m[0][1]  # q along the wall's x, in file units
        cx, cy = [float(c) for c in profile.Position.Location.Coordinates][:2]
        x0, x1 = cx - float(profile.XDim) / 2.0, cx + float(profile.XDim) / 2.0
        if (end == 1) == (w["line"][1][0] >= w["line"][0][0]):
            x1 = x
        else:
            x0 = x
        if x1 - x0 < self.u(0.01):
            return False
        profile.XDim = round(x1 - x0, 9)
        point, centre = profile.Position.Location, round((x0 + x1) / 2.0, 9)
        if self.m.get_total_inverses(point) > 1:
            profile.Position.Location = self.point(centre, cy)
        else:
            point.Coordinates = (centre, cy)
        return True

    def join_storey(self, walls, tol, done):
        """The joins on one storey: wall ends within tol of each other meet at one point (union-find), which is a T for each of them
        when it lies on another wall's axis between that wall's ends, else an L of two ends or a meeting of three or more."""
        ends = [(w, k) for w in walls for k in (0, 1)]
        where = [w["a"] if k == 0 else w["b"] for w, k in ends]
        parent = list(range(len(ends)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for _, i, j in _topo_pairs(where, tol):
            if ends[i][0] is not ends[j][0]:
                parent[find(i)] = find(j)
        groups = {}
        for i in range(len(ends)):
            groups.setdefault(find(i), []).append(i)
        for members in sorted(groups.values()):
            group = [ends[i] for i in members]
            if len({w["id"] for w, _ in group}) < len(group):
                continue  # both ends of one short wall
            p = (sum(where[i][0] for i in members) / len(members), sum(where[i][1] for i in members) / len(members))
            through = self.through(walls, group, p, tol)
            if through is not None:
                for e in group:
                    self.abut(e, through, "ATPATH", through["t"], done)
            elif len(group) == 2:
                self.join_l(group[0], group[1], done)
            elif len(group) > 2:
                self.join_node(group, p, done)

    @staticmethod
    def through(walls, group, p, tol):
        """The wall, not one of the group's, whose axis passes within tol of the point p between its ends (farther than tol from
        both), the nearest; None."""
        own = {w["id"] for w, _ in group}
        best = None
        for w in walls:
            if w["id"] in own:
                continue
            dx, dy = p[0] - w["a"][0], p[1] - w["a"][1]
            along, off = dx * w["u"][0] + dy * w["u"][1], abs(dx * w["u"][1] - dy * w["u"][0])
            if tol < along < w["len"] - tol and off <= tol and (best is None or off < best[0]):
                best = (off, w)
        return best[1] if best else None

    def label(self, w):
        """A wall of join_walls as the notes name it: its ref in this change set, else its id, and its name."""
        ref = next((r["ref"] for r in self.created if r["id"] == w["id"] and r.get("ref")), None)
        return "%s %r" % (ref or "#%d" % w["id"], _ifc_text(getattr(w["e"], "Name", None)) or "")

    def note_join(self, a, b, cos, angled):
        if angled:
            self.notes.append(
                "walls %s and %s meet at %.0f degrees and were joined as drawn: the app cuts corners at right angles only"
                % (self.label(a), self.label(b), math.degrees(math.acos(min(1.0, cos))))
            )
        else:
            self.notes.append(
                "walls %s and %s were joined as drawn: the app cuts only bodies drawn by create_wall"
                % (self.label(a), self.label(b))
            )

    def abut(self, end, a, a_type, thickness, done):
        """The end `end` (a wall and 0 or 1) against the side of wall a, `thickness` metres thick: the abutting wall relates with
        ATSTART or ATEND and a with a_type (ATPATH at a T), and the abutting wall's body stops at a's face."""
        b, k = end
        pair = frozenset((a["id"], b["id"]))
        cos = abs(a["u"][0] * b["u"][0] + a["u"][1] * b["u"][1])
        if pair in done or not (a["new"] or b["new"]) or cos >= self.JOIN_LINE:
            return  # joined before, both old, or side by side as the layers of a wall
        p = _topo_cross(b["a"], b["u"], a["a"], a["b"])[1]  # where b's axis meets a's
        if cos <= self.JOIN_RIGHT and b["box"] is not None:
            into = b["u"] if k == 0 else (-b["u"][0], -b["u"][1])
            if self.cut_end(b, k, (p[0] + into[0] * thickness / 2.0, p[1] + into[1] * thickness / 2.0)):
                self.touch(b["e"])
        else:
            self.note_join(a, b, cos, cos > self.JOIN_RIGHT)
        self.connect(b, self.end_type(k), a, a_type)
        done.add(pair)

    def join_l(self, e1, e2, done):
        """Two wall ends at one point: an L at right angles, cut so that the corner has neither a gap nor an overlap (join_walls); in
        line a straight joint, ATEND to ATSTART, as drawn; at another angle joined as drawn, with a note."""
        (w1, k1), (w2, k2) = e1, e2
        pair = frozenset((w1["id"], w2["id"]))
        if pair in done or not (w1["new"] or w2["new"]):
            return
        out1 = w1["u"] if k1 == 1 else (-w1["u"][0], -w1["u"][1])
        out2 = w2["u"] if k2 == 1 else (-w2["u"][0], -w2["u"][1])
        dot = out1[0] * out2[0] + out1[1] * out2[1]
        if abs(dot) >= self.JOIN_LINE:
            if dot < 0:  # one continues the other; side by side is no joint
                self.connect(w1, self.end_type(k1), w2, self.end_type(k2))
                done.add(pair)
            return
        (wm, km), (wo, ko) = sorted((e1, e2), key=lambda e: (-round(e[0]["t"], 6), -round(e[0]["len"], 6), e[0]["id"]))
        if abs(dot) <= self.JOIN_RIGHT and wm["box"] is not None and wo["box"] is not None:
            p = _topo_cross(wm["a"], wm["u"], wo["a"], wo["b"])[1]  # where the axes cross
            om = wm["u"] if km == 1 else (-wm["u"][0], -wm["u"][1])  # out of the main wall at the corner
            oo = wo["u"] if ko == 1 else (-wo["u"][0], -wo["u"][1])
            if self.cut_end(wm, km, (p[0] + om[0] * wo["t"] / 2.0, p[1] + om[1] * wo["t"] / 2.0)):
                self.touch(wm["e"])
                if self.cut_end(wo, ko, (p[0] - oo[0] * wm["t"] / 2.0, p[1] - oo[1] * wm["t"] / 2.0)):
                    self.touch(wo["e"])
        else:
            self.note_join(wm, wo, abs(dot), abs(dot) > self.JOIN_RIGHT)
        self.connect(wm, self.end_type(km), wo, self.end_type(ko))
        done.add(pair)

    def join_node(self, group, p, done):
        """Three or more wall ends at one point: two that continue each other in a line get a straight joint and every other end
        abuts that line, the thicker of the two relating, then the older (at a cross the line of the thicker, then the longer,
        walls); without such a line the walls are left as drawn, with a note."""

        def out(e):
            return e[0]["u"] if e[1] == 1 else (-e[0]["u"][0], -e[0]["u"][1])

        lines = [
            (e1, e2)
            for i, e1 in enumerate(group)
            for e2 in group[i + 1 :]
            if out(e1)[0] * out(e2)[0] + out(e1)[1] * out(e2)[1] <= -self.JOIN_LINE
        ]
        if not lines:
            self.notes.append(
                "walls %s meet at (%.2f, %.2f) in a way the app does not join, so they were left as drawn"
                % (", ".join(self.label(w) for w, _ in group), p[0], p[1])
            )
            return
        e1, e2 = min(
            lines,
            key=lambda l: (
                -round(max(l[0][0]["t"], l[1][0]["t"]), 6),
                -round(l[0][0]["len"] + l[1][0]["len"], 6),
                min(l[0][0]["id"], l[1][0]["id"]),
            ),
        )
        self.join_l(e1, e2, done)
        line = min((e1, e2), key=lambda e: (-round(e[0]["t"], 6), e[0]["id"]))
        for e in group:
            if e is not e1 and e is not e2:
                self.abut(e, line[0], self.end_type(line[1]), max(e1[0]["t"], e2[0]["t"]), done)

    def connect(self, relating, relating_type, related, related_type):
        """An IfcRelConnectsPathElements between two walls of join_walls, without priorities or connection geometry."""
        import ifcopenshell.guid

        self.m.create_entity(
            "IfcRelConnectsPathElements",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=self.owner,
            RelatingElement=relating["e"],
            RelatedElement=related["e"],
            RelatingPriorities=[],
            RelatedPriorities=[],
            RelatingConnectionType=relating_type,
            RelatedConnectionType=related_type,
        )

    QUANTITIES = (
        ("IfcWall", "Qto_WallBaseQuantities", "qto_wall"),
        ("IfcSlab", "Qto_SlabBaseQuantities", "qto_slab"),
        ("IfcRoof", "Qto_RoofBaseQuantities", "qto_roof"),
        ("IfcDoor", "Qto_DoorBaseQuantities", "qto_filling"),
        ("IfcWindow", "Qto_WindowBaseQuantities", "qto_filling"),
        ("IfcCurtainWall", "Qto_CurtainWallQuantities", "qto_curtain_wall"),
    )

    def base_quantities(self):
        """The base quantities of the walls, slabs, roofs, doors, windows and curtain walls the change set created or changed, from their
        parameters without a geometry kernel, in the file's units and with MethodOfMeasurement BaseQuantities (add_qto): a set the
        element has of its own is updated, one it shares is left alone. Each quantity has the name in IfcOpenShell's template for the
        file's schema (IFC4X3 spells the wall's footprint areas GrossFootPrintArea and NetFootPrintArea), and one the template lacks
        is left out; IFC2X3 has no templates and gets IFC4's names. An element of another form gets none."""
        for i in dict.fromkeys([r["id"] for r in self.created] + list(self.changed)):
            try:
                e = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            for ifc_class, name, method in self.QUANTITIES:
                if e.is_a(ifc_class):
                    values = getattr(self, method)(e)
                    if values:
                        self.write_qto(e, name, values)
                    break

    def unit_scale(self, kind):
        """The file's unit of a kind (AREAUNIT, VOLUMEUNIT) in SI units."""
        import ifcopenshell.util.unit

        try:
            return float(ifcopenshell.util.unit.calculate_unit_scale(self.m, kind) or 1.0)
        except Exception:
            return 1.0

    def write_qto(self, e, name, values):
        """values {quantity: (length | area | volume, metres, m² or m³)} as the element's quantity set `name`."""
        import ifcopenshell.api.pset

        names = _cs_qto_names(getattr(self.m, "schema_identifier", None) or self.m.schema, name)
        per = {"length": self.scale, "area": self.unit_scale("AREAUNIT"), "volume": self.unit_scale("VOLUMEUNIT")}
        props = {}
        for key, (kind, value) in values.items():
            spelled = key if names is None else names.get(key.lower())
            if spelled is not None:
                props[spelled] = round(float(value) / per[kind], 9)
        rels = [
            r
            for r in (getattr(e, "IsDefinedBy", None) or ())
            if r.is_a("IfcRelDefinesByProperties")
            and hasattr(r.RelatingPropertyDefinition, "is_a")
            and r.RelatingPropertyDefinition.is_a("IfcElementQuantity")
            and r.RelatingPropertyDefinition.Name == name
        ]
        if not props or any(len(r.RelatedObjects or ()) > 1 for r in rels):
            return
        qto = (
            rels[0].RelatingPropertyDefinition if rels else ifcopenshell.api.pset.add_qto(self.m, product=e, name=name)
        )
        if not getattr(qto, "MethodOfMeasurement", None):
            qto.MethodOfMeasurement = (
                "BaseQuantities"  # add_qto sets it only for names that end so, and QTY001 asks it of every Qto_ set
            )
        ifcopenshell.api.pset.edit_qto(self.m, qto=qto, properties=props)  # ty: ignore[invalid-argument-type]

    def qto_wall(self, wall):
        """A wall of create_wall's form (wall_box): Length along its body's centreline as joined, Width, Height, the footprint, the
        side of its middle plane and the volume, gross and, less the openings of create_opening's form within the body, net (the
        footprint loses only an opening through the whole height); without the net values when an opening has another form."""
        item = self.wall_box(wall)
        if item is None:
            return None
        s, p = self.scale, item.SweptArea
        cx = float(p.Position.Location.Coordinates[0])
        x0, x1 = cx - float(p.XDim) / 2.0, cx + float(p.XDim) / 2.0
        z0 = _cs_axes(item.Position)[3][2]
        z1 = z0 + float(item.Depth)
        length, width, height = (x1 - x0) * s, float(p.YDim) * s, float(item.Depth) * s
        side, foot = length * height, length * width
        values = {
            "Length": ("length", length),
            "Width": ("length", width),
            "Height": ("length", height),
            "GrossFootprintArea": ("area", foot),
            "GrossSideArea": ("area", side),
            "GrossVolume": ("volume", side * width),
        }
        cut = through = 0.0
        for rel in getattr(wall, "HasOpenings", None) or ():
            box = self.opening_box(rel.RelatedOpeningElement, wall)
            if box is None:
                return values
            w = max(0.0, min(x1, box[1]) - max(x0, box[0])) * s
            cut += w * max(0.0, min(z1, box[3]) - max(z0, box[2])) * s
            if box[2] <= z0 + 1e-9 and box[3] >= z1 - 1e-9:
                through += w * width
        values.update(
            {
                "NetFootprintArea": ("area", foot - through),
                "NetSideArea": ("area", side - cut),
                "NetVolume": ("volume", (side - cut) * width),
            }
        )
        return values

    def opening_box(self, opening, wall):
        """(x0, x1, z0, z1) of an opening's body in its wall's coordinates, in file units, when it is a box along the wall (wall_box);
        None for another form."""
        item = self.wall_box(opening)
        mw, mo = (
            _cs_placement(getattr(wall, "ObjectPlacement", None)),
            _cs_placement(getattr(opening, "ObjectPlacement", None)),
        )
        if item is None or mw is None or mo is None:
            return None
        rel = _cs_compose(_cs_invert(mw), mo)
        if abs(rel[0][0] - 1.0) > 1e-6 or abs(rel[2][2] - 1.0) > 1e-6:
            return None
        cx, half = rel[3][0] + float(item.SweptArea.Position.Location.Coordinates[0]), float(item.SweptArea.XDim) / 2.0
        z0 = rel[3][2] + _cs_axes(item.Position)[3][2]
        return cx - half, cx + half, z0, z0 + float(item.Depth)

    def slab_face(self, slab):
        """(area m², perimeter m, thickness m, (length, depth) m or None, upness) of a slab whose body is one IfcExtrudedAreaSolid of a
        profile without voids: the profile's area and perimeter in its own plane, the thickness at right angles to it, its sides when
        it is a rectangle along its axes, and the z of its plane's normal, which projects it to the ground; None for another form."""
        rep = getattr(slab, "Representation", None)
        bodies = [
            r for r in (rep.Representations if rep is not None else ()) if (r.RepresentationIdentifier or "") == "Body"
        ]
        items = list(bodies[0].Items or ()) if len(bodies) == 1 else []
        m = _cs_placement(getattr(slab, "ObjectPlacement", None))
        if (
            len(items) != 1
            or not items[0].is_a("IfcExtrudedAreaSolid")
            or m is None
            or getattr(items[0].SweptArea, "InnerCurves", None)
        ):
            return None
        item = items[0]
        pts = [tuple(q) for q in _cs_profile_points(item.SweptArea)]
        if len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-9:
            pts = pts[:-1]
        d = _cs_unit([float(c) for c in item.ExtrudedDirection.DirectionRatios])
        if len(pts) < 3 or d is None:
            return None
        s = self.scale
        ring = pts + pts[:1]
        area = abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(ring, ring[1:]))) / 2.0 * s * s
        perimeter = sum(math.dist(p, q) for p, q in zip(ring, ring[1:])) * s
        rect = None
        if _cs_rectangle(pts):
            rect = (
                (max(p[0] for p in pts) - min(p[0] for p in pts)) * s,
                (max(p[1] for p in pts) - min(p[1] for p in pts)) * s,
            )
        return (
            area,
            perimeter,
            float(item.Depth) * abs(d[2]) * s,
            rect,
            abs(_cs_compose(m, _cs_axes(item.Position))[2][2]),
        )

    def qto_slab(self, slab):
        """A slab of one extruded profile (slab_face): Width its thickness, Perimeter, Length and Depth when the profile is a
        rectangle, the profile's area and the volume; Net as Gross when it has no openings (the write tools cut none in slabs)."""
        face = self.slab_face(slab)
        if face is None:
            return None
        area, perimeter, width, rect, _ = face
        values = {
            "Width": ("length", width),
            "Perimeter": ("length", perimeter),
            "GrossArea": ("area", area),
            "GrossVolume": ("volume", area * width),
        }
        if rect is not None:
            values.update({"Length": ("length", rect[0]), "Depth": ("length", rect[1])})
        if not (getattr(slab, "HasOpenings", None) or ()):  # ty: ignore[redundant-condition]
            values.update({"NetArea": ("area", area), "NetVolume": ("volume", area * width)})
        return values

    def qto_roof(self, roof):
        """A roof by its aggregated slabs (create_roof's form): GrossArea and NetArea the sum of their faces, ProjectedArea that sum
        projected to the ground; none when a part is of another form, and no NetArea when one has openings."""
        parts = [
            o
            for rel in (getattr(roof, "IsDecomposedBy", None) or ())
            for o in (rel.RelatedObjects or ())
            if o.is_a("IfcSlab")
        ]
        faces = [self.slab_face(o) for o in parts]
        if not faces or any(f is None for f in faces):
            return None
        gross = sum(f[0] for f in faces)
        values = {"GrossArea": ("area", gross), "ProjectedArea": ("area", sum(f[0] * f[4] for f in faces))}
        if not any(getattr(o, "HasOpenings", None) for o in parts):
            values["NetArea"] = ("area", gross)
        return values

    def qto_filling(self, e):
        """A door or a window by OverallWidth and OverallHeight, which create_opening sets: Width, Height, Perimeter and Area."""
        w, h = getattr(e, "OverallWidth", None), getattr(e, "OverallHeight", None)
        if not w or not h:
            return None
        w, h = float(w) * self.scale, float(h) * self.scale
        return {
            "Width": ("length", w),
            "Height": ("length", h),
            "Perimeter": ("length", 2.0 * (w + h)),
            "Area": ("area", w * h),
        }

    # -- LoadBearing, IsExternal, the material's category and the checked property sets (recommendation 13 of the native IFC study,
    # 4, 8 and 9 of the Praxi study): the climate declaration tells its categories apart by them.
    COMMON_SETS = (
        ("IfcWall", "Pset_WallCommon"),
        ("IfcWallType", "Pset_WallCommon"),
        ("IfcSlab", "Pset_SlabCommon"),
        ("IfcSlabType", "Pset_SlabCommon"),
        ("IfcDoor", "Pset_DoorCommon"),
        ("IfcWindow", "Pset_WindowCommon"),
        ("IfcRoof", "Pset_RoofCommon"),
        ("IfcCurtainWall", "Pset_CurtainWallCommon"),
        ("IfcCurtainWallType", "Pset_CurtainWallCommon"),
    )
    LENGTH_MEASURES = ("IfcLengthMeasure", "IfcPositiveLengthMeasure", "IfcNonNegativeLengthMeasure")
    ANGLE_MEASURES = ("IfcPlaneAngleMeasure", "IfcPositivePlaneAngleMeasure")
    VALUE_KINDS = {
        "P_BOUNDEDVALUE": "a bounded value (a range)",
        "P_LISTVALUE": "a list of values",
        "P_TABLEVALUE": "a table",
        "P_REFERENCEVALUE": "a reference to another entity",
    }
    QUANTITY_MEASURES = {
        "Q_LENGTH": "IfcLengthMeasure",
        "Q_AREA": "IfcAreaMeasure",
        "Q_VOLUME": "IfcVolumeMeasure",
        "Q_COUNT": "IfcCountMeasure",
        "Q_WEIGHT": "IfcMassMeasure",
        "Q_TIME": "IfcTimeMeasure",
    }

    def material_category(self, op, material):
        """The IfcMaterial.Category assign gives with its material, checked against MATERIAL_CATEGORIES; None without one, and in IFC2X3,
        which has no category (with a note)."""
        category = str(op.get("category") or "").strip().lower() or None
        if category is None:
            return None
        if not material:
            raise DataspatiumError("category goes with a material")
        if category not in self.MATERIAL_CATEGORIES:
            raise DataspatiumError("category must be one of %s, or null" % ", ".join(self.MATERIAL_CATEGORIES))
        if self.m.schema == "IFC2X3":
            self.note_once(
                "IFC2X3 has no material categories, so the category %s of %s was left out" % (category, material)
            )
            return None
        return category

    def note_once(self, text):
        if text not in self.notes:
            self.notes.append(text)

    def element_label(self, e):
        """An element as the notes name it: its ref in this change set, else its id, and its name."""
        ref = next((r["ref"] for r in self.created if r["id"] == e.id() and r.get("ref")), None)
        return "%s %r" % (ref or "#%d" % e.id(), _ifc_text(getattr(e, "Name", None)) or "")

    def property_template(self, name):
        """(template, name) of a set: for one named Pset_... or Qto_... (in any case) IfcOpenShell's template of that name in the file's
        schema and the name as the template spells it; an IFC2X3 file, whose templates lack Pset_Condition and the Qto_ sets, takes
        IFC4's, with a note. A name the schema does not define is an error with the nearest names: a set of the model's own must not
        start with Pset_ or Qto_. (None, name) for any other name, a set of the model's own."""
        if not name.lower().startswith(("pset_", "qto_")):
            return None, name
        known = _cs_pset_templates(getattr(self.m, "schema_identifier", None) or self.m.schema)
        template = known.get(name.lower())
        if template is None and self.m.schema == "IFC2X3":
            template = _cs_pset_templates("IFC4").get(name.lower())
            if template is not None:
                self.note_once("%s is not a set of IFC2X3, so it was written as IFC4 defines it" % template.Name)
        if template is None:
            import difflib

            near = difflib.get_close_matches(name, [str(t.Name) for t in known.values()], 5, 0.6)
            raise DataspatiumError(
                "%s is not a property or quantity set of %s%s; a set of your own must not start with Pset_ or Qto_: name it "
                "country-origin-project_Name, such as SE-ACME-42_%s"
                % (
                    name,
                    self.m.schema,
                    "; nearest: " + ", ".join(near) if near else "",
                    name.split("_", 1)[1] or "Name",
                )
            )
        return template, str(template.Name)

    def checked_properties(self, template, name, props):
        """{property: value} to write: for a template's set each property spelled as the template has it, with its value checked
        (property_value), and a property the template lacks an error with the nearest names; for a set of the model's own the values as
        they come, but never a number with its unit written after it."""
        out = {}
        props_of = {
            str(p.Name).lower(): p
            for p in ((template.HasPropertyTemplates or ()) if template is not None else ())
            if p.Name
        }
        for key, value in props.items():
            key = str(key).strip()
            if not key:
                raise DataspatiumError("every property needs a name")
            if template is None:
                self.no_unit("%s.%s" % (name, key), value)
                out[key] = _ifc_value(value)
                continue
            prop = props_of.get(key.lower())
            if prop is None:
                import difflib

                names = [str(p.Name) for p in template.HasPropertyTemplates or () if p.Name]
                near = difflib.get_close_matches(key, names, 3, 0.6)
                raise DataspatiumError(
                    "%s has no property %s%s; its properties are %s; put another property in a set of your own, whose name does "
                    "not start with Pset_ or Qto_"
                    % (name, key, " (nearest: %s)" % ", ".join(near) if near else "", ", ".join(names))
                )
            out[str(prop.Name)] = self.property_value(name, prop, value)
        return out

    @staticmethod
    def no_unit(where, value):
        """An error for a text that is a number with its unit written after it ("40 dB")."""
        unit = _cs_unit_in(value)
        if unit is not None:
            raise DataspatiumError(
                "%s: give the number only, without its unit (%s, not %r): IFC gives the property its unit"
                % (where, unit[0].replace(",", "."), value)
            )

    def number_of(self, where, value, measure):
        """A value as a number: a number, or a text of a number alone (a decimal comma too); an error for a number with its unit
        after it, or other text."""
        if isinstance(value, bool) or value is None:
            raise DataspatiumError("%s is a number (%s), not %r" % (where, measure, value))
        if isinstance(value, (int, float)):
            return float(value)
        self.no_unit(where, value)
        text = str(value).strip()
        if text.count(",") == 1 and "." not in text:
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            raise DataspatiumError("%s is a number (%s), not %r" % (where, measure, value))
        if not math.isfinite(number):
            raise DataspatiumError("%s is a number (%s), not %r" % (where, measure, value))
        return number

    def base_type(self, measure):
        """The simple type under a measure in the file's schema (STRING, DOUBLE, INT, BOOL, LOGICAL ...), as edit_pset reads it; None
        for an entity or a name the schema lacks."""
        decl = _ifc_declaration(self.schema_def(), measure)
        if decl is None or decl.as_type_declaration() is None:
            return None
        try:
            return self.m.create_entity(measure).attribute_type(0)
        except Exception:
            return None

    def property_value(self, pset, prop, value):
        """One value checked against its property template, as the file takes it. A boolean is true or false (also as the text true or
        false). A measure is a number, without a unit written after it: above zero for a positive measure, at least zero for a
        non-negative one and 0 to 1 for a normalised ratio, a whole number for an integer or a count; lengths, areas and volumes come in
        metres, m² and m³ and angles in degrees, and are written in the file's units. A label or an identifier is text without a unit
        after a number (a number becomes its text), a date YYYY-MM-DD, and an enumeration one of its values in any case. A quantity
        (Q_LENGTH, Q_AREA ...) is a number of zero or more in the same units. A bounded, list, table or reference value is refused:
        set_properties writes single values. A measure the file's schema lacks (IFC4's date in IFC2X3) is written as a label or a real."""
        where = "%s.%s" % (pset, prop.Name)
        kind = str(prop.TemplateType or "P_SINGLEVALUE").strip()
        measure = str(prop.PrimaryMeasureType or "").strip() or None
        if kind.startswith("Ifc"):  # three properties of IFC4's templates name their measure as the template type
            kind, measure = "P_SINGLEVALUE", measure or kind
        per = {"length": self.scale, "area": self.unit_scale("AREAUNIT"), "volume": self.unit_scale("VOLUMEUNIT")}
        if kind.startswith("Q_"):
            number = self.number_of(where, value, kind)
            if number < 0:
                raise DataspatiumError("%s is a quantity of zero or more, not %g" % (where, number))
            if kind == "Q_COUNT" and number != int(number):
                raise DataspatiumError("%s is a count, a whole number, not %g" % (where, number))
            # As a measure, so that edit_qto takes the quantity's class from it (IfcLengthMeasure gives IfcQuantityLength).
            spelled = self.QUANTITY_MEASURES.get(kind, "IfcLengthMeasure")
            if kind == "Q_COUNT":
                return self.m.create_entity(
                    spelled, int(number) if self.base_type(spelled) == "INT" else float(int(number))
                )  # an integer in IFC4.3
            return self.m.create_entity(spelled, round(number / per.get(kind[2:].lower(), 1.0), 9))
        if kind == "P_ENUMERATEDVALUE":
            enumerators = prop.Enumerators
            items = [
                str(getattr(v, "wrappedValue", v))
                for v in ((enumerators.EnumerationValues or ()) if enumerators is not None else ())
            ]
            hit = (
                None
                if isinstance(value, bool)
                else next((i for i in items if i.lower() == str(value).strip().lower()), None)
            )
            if hit is None:
                raise DataspatiumError("%s takes one of %s, not %r" % (where, ", ".join(items), value))
            return (
                "enum",
                hit,
                "IfcLabel",
                str(getattr(enumerators, "Name", None) or "PEnum_" + str(prop.Name)),
                items,
            )
        if kind != "P_SINGLEVALUE":
            raise DataspatiumError(
                "%s is %s, which set_properties does not write; leave it out"
                % (where, self.VALUE_KINDS.get(kind, kind))
            )
        measure = measure or "IfcLabel"
        base = self.base_type(measure)
        if base is None and _ifc_declaration(self.schema_def(), measure) is None:  # a measure of a later schema
            base = (
                "BOOL"
                if measure in ("IfcBoolean", "IfcLogical")
                else "STRING"
                if measure in ("IfcDate", "IfcDateTime", "IfcTime", "IfcDuration", "IfcURIReference")
                else "DOUBLE"
            )
            spelled = {"BOOL": "IfcBoolean", "STRING": "IfcLabel"}.get(base, "IfcReal")
        else:
            spelled = measure
        if base in ("BOOL", "LOGICAL"):
            flag = value if isinstance(value, bool) else {"true": True, "false": False}.get(str(value).strip().lower())
            if flag is None:
                raise DataspatiumError("%s is true or false, not %r" % (where, value))
            return self.m.create_entity(spelled, flag)
        if base in ("DOUBLE", "INT"):
            number = self.number_of(where, value, measure)
            whole = base == "INT" or "Count" in measure
            if whole and number != int(number):
                raise DataspatiumError("%s is a whole number (%s), not %g" % (where, measure, number))
            if (
                ("Positive" in measure and number <= 0)
                or ("NonNegative" in measure and number < 0)
                or (measure == "IfcNormalisedRatioMeasure" and not 0 <= number <= 1)
            ):
                raise DataspatiumError(
                    "%s is %s (%s), not %g"
                    % (
                        where,
                        "0 to 1"
                        if "Normalised" in measure
                        else "above zero"
                        if "Positive" in measure
                        else "zero or more",
                        measure,
                        number,
                    )
                )
            if measure in self.LENGTH_MEASURES:
                number /= per["length"]
            elif measure == "IfcAreaMeasure":
                number /= per["area"]
            elif measure == "IfcVolumeMeasure":
                number /= per["volume"]
            elif measure in self.ANGLE_MEASURES:
                number = math.radians(number) / self.unit_scale("PLANEANGLEUNIT")
            return self.m.create_entity(spelled, int(number) if base == "INT" else round(float(number), 12))
        if base == "STRING":
            if isinstance(value, bool) or value is None:
                raise DataspatiumError("%s is text (%s), not %r" % (where, measure, value))
            text = (
                (str(int(value)) if float(value).is_integer() else repr(float(value)))
                if isinstance(value, (int, float))
                else str(value).strip()
            )
            if measure == "IfcDate" and not (
                len(text) == 10 and text[4] == "-" and text[7] == "-" and text.replace("-", "").isdigit()
            ):
                raise DataspatiumError("%s is a date, YYYY-MM-DD, not %r" % (where, value))
            if measure in ("IfcLabel", "IfcIdentifier"):
                self.no_unit(where, text)
                text = text[:255]
            return self.m.create_entity(spelled, text)
        raise DataspatiumError("%s is a %s, which set_properties does not write; leave it out" % (where, measure))

    def put_properties(self, e, template, name, values):
        """Checked values on one element or type: a Qto_ set as an IfcElementQuantity (add_qto, edit_qto), another as an IfcPropertySet.
        The element's own set of the name is edited, never its type's, and one it shares with other elements is first made its own
        (unshare_pset); an enumeration is written as an IfcPropertyEnumeratedValue unless the set holds it as a single value already."""
        import ifcopenshell.api.pset
        import ifcopenshell.util.element as el

        qto = template is not None and name.lower().startswith("qto_")
        own = el.get_psets(e, psets_only=not qto, qtos_only=qto, should_inherit=False).get(name)
        pset = self.m.by_id(own["id"]) if own and own.get("id") else None
        rels = (
            (getattr(pset, "DefinesOccurrence", None) or getattr(pset, "PropertyDefinitionOf", None) or ())
            if pset is not None
            else ()
        )
        if not e.is_a("IfcTypeObject") and sum(len(r.RelatedObjects or ()) for r in rels) > 1:
            pset = ifcopenshell.api.pset.unshare_pset(self.m, products=[e], pset=pset)[0]  # ty: ignore[invalid-argument-type]
        # Every value is typed already (property_value), so the API needs no template: IFC2X3 writes IFC4's sets as well.
        if qto:
            pset = pset or ifcopenshell.api.pset.add_qto(self.m, product=e, name=name)
            ifcopenshell.api.pset.edit_qto(self.m, qto=pset, properties=values)
            return
        pset = pset or ifcopenshell.api.pset.add_pset(self.m, product=e, name=name)
        held = {str(p.Name): p for p in (getattr(pset, "HasProperties", None) or ())}
        out = {}
        for key, value in values.items():
            if isinstance(value, tuple) and value and value[0] == "enum":
                _, hit, measure, enum_name, items = value
                prop = held.get(key)
                if prop is not None and prop.is_a("IfcPropertyEnumeratedValue"):
                    value = [hit]  # edit_pset keeps the property's enumeration
                elif prop is not None:
                    value = self.m.create_entity(measure, hit)  # the set holds it as a single value
                else:
                    value = self.m.create_entity(
                        "IfcPropertyEnumeratedValue",
                        Name=key,
                        EnumerationValues=[self.m.create_entity(measure, hit)],
                        EnumerationReference=self.enumeration(enum_name, measure, items),
                    )
            out[key] = value
        ifcopenshell.api.pset.edit_pset(self.m, pset=pset, properties=out)

    def enumeration(self, name, measure, items):
        """The file's IfcPropertyEnumeration of the name and values, made once: its name is unique in the file."""
        for e in self.m.by_type("IfcPropertyEnumeration"):
            if str(e.Name or "") == name:
                return (
                    e
                    if [str(getattr(v, "wrappedValue", v)) for v in (e.EnumerationValues or ())] == list(items)
                    else None
                )
        return self.m.create_entity(
            "IfcPropertyEnumeration", Name=name, EnumerationValues=[self.m.create_entity(measure, i) for i in items]
        )

    def own_value(self, e, pset, prop):
        """The value of a property in the element's (or the type's) own set, not inherited; None when it has none."""
        import ifcopenshell.util.element as el

        return el.get_psets(e, psets_only=True, should_inherit=False).get(pset, {}).get(prop)

    def common_set(self, e):
        return next((name for cls, name in self.COMMON_SETS if e.is_a(cls)), None)

    def put_common(self, e, prop, value):
        """One property of the element's or type's common set (Pset_WallCommon ...), through the template check."""
        template, name = self.property_template(self.common_set(e))
        self.put_properties(e, template, name, self.checked_properties(template, name, {prop: value}))
        self.touch(e)

    STATUSES = ("NEW", "EXISTING", "DEMOLISH", "TEMPORARY")

    def mark_common(self, e, op):
        """Keep the load_bearing (true, false or null) and the status (NEW, EXISTING, DEMOLISH, TEMPORARY or null) of a create step for
        write_marked; a partition is not load-bearing."""
        value = op.get("load_bearing")
        if value is not None:
            if not isinstance(value, bool):
                raise DataspatiumError("load_bearing must be true, false or null (not given)")
            if value and op.get("partition") is True:
                raise DataspatiumError(
                    "partition true makes a non-load-bearing wall; leave partition out for a load-bearing wall"
                )
            self.marks["LoadBearing"][e.id()] = value
        status = str(op.get("status") or "").strip().upper() or None
        if status is not None:
            if status not in self.STATUSES:
                raise DataspatiumError("status must be %s, or null" % ", ".join(self.STATUSES))
            self.marks["Status"][e.id()] = status

    @staticmethod
    def plain(value):
        """A property value as get_psets gives it, an enumeration's one value unwrapped."""
        return value[0] if isinstance(value, (list, tuple)) and len(value) == 1 else value

    def write_marked(self, prop):
        """LoadBearing or Status of the elements whose create step gave it (mark_common). An element without a type gets it on itself.
        A typed element's type gets it when the type has none and every occurrence of the type says the same, from its step or its
        own set; otherwise each element gets its own, unless the type says the same already. Nothing for null: the app never guesses
        whether a part bears load or what its status is. A common set whose template in the file's schema lacks the property
        (Status in IFC2X3) is left alone, with a note."""
        import ifcopenshell.util.element as el

        groups = {}
        for i, value in self.marks[prop].items():
            try:
                e = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            t = el.get_type(e)
            groups.setdefault(t.id() if t is not None else -i, (t, []))[1].append((e, value))
        for t, items in groups.values():
            name = self.common_set(items[0][0])
            template = self.property_template(name)[0]
            if template is not None and not any(str(p.Name) == prop for p in template.HasPropertyTemplates or ()):
                self.note_once("%s has no %s in %s, so it was left out" % (name, prop, self.m.schema))
                continue
            on_type = None
            if t is not None:
                said = {e.id(): value for e, value in items}
                on_type = self.plain(self.own_value(t, name, prop))
                values = {
                    said[o.id()] if o.id() in said else self.plain(self.own_value(o, name, prop))
                    for o in el.get_types(t)
                }
                if on_type is None and len(values) == 1 and None not in values:
                    self.put_common(t, prop, values.pop())
                    continue
            for e, value in items:
                if on_type is None or on_type != value:
                    self.put_common(e, prop, value)

    def roof_slab(self, e):
        import ifcopenshell.util.element as el

        try:
            return str(el.get_predefined_type(e) or "") == "ROOF"
        except Exception:
            return str(getattr(e, "PredefinedType", None) or "") == "ROOF"

    def set_external(self, e, value):
        """IsExternal on an element, in its common set; the element's value afterwards. A value the element had before the change set
        (not one set_properties wrote in it) is kept, with a note when the app's differs."""
        name = self.common_set(e)
        if name is None:
            return value
        own = self.own_value(e, name, "IsExternal")
        if own is not None and (e.id(), name, "IsExternal") not in self.written:
            if bool(own) != value:
                self.note_once(
                    "%s %s keeps the model's IsExternal %s; the topology says %s"
                    % (e.is_a(), self.element_label(e), str(bool(own)).lower(), str(value).lower())
                )
            return bool(own)
        if own is None or bool(own) != value:
            self.put_common(e, "IsExternal", value)
        return value

    def write_external(self):
        """IsExternal at the end of apply. The walls the change set created or changed are external when they run along the outer
        boundary of their storey's topology (_ifc_topology: the union of its rooms, a courtyard's walls too) and internal when they
        bound its rooms otherwise, and the doors and windows in them take their wall's value; on a storey whose walls do not close,
        or that has walls the topology cannot read, they are left alone, and so is a wall that bounds no room, with a note. A curtain
        wall counts as a wall (Pset_CurtainWallCommon; its parts are parts of it). The roof slabs and roofs the change set created or
        changed are external. A value that was in the model already stays (set_external)."""
        walls, storeys = [], {}
        for i in dict.fromkeys([r["id"] for r in self.created] + list(self.changed)):
            try:
                e = self.m.by_id(i)
            except Exception:  # nosec B112
                continue  # deleted later in the change set
            if e.is_a("IfcRoof") or (e.is_a("IfcSlab") and self.roof_slab(e)):
                self.set_external(e, True)
            elif e.is_a("IfcWall") or e.is_a("IfcCurtainWall"):
                s = self.container(e)
                if s is not None and s.is_a("IfcBuildingStorey"):
                    walls.append((e, s.id()))
                    storeys.setdefault(s.id(), s)
        if not walls:
            return
        found = {
            rec["_storey"].id(): rec
            for rec in _ifc_topology(self.m, storeys={_ifc_text(s.Name) for s in storeys.values()}, shapes=True)
        }
        for sid, s in storeys.items():
            rec = found.get(sid)
            mine = [w for w, at in walls if at == sid]
            why = (
                "the app could not read its walls"
                if rec is None
                else "%d of its walls could not be read" % rec["unread"]
                if rec["unread"]
                else "%d wall ends stand open" % len(rec["dangles"])
                if rec["dangles"]
                else "its walls enclose no room"
                if not rec["closed"]
                else None
            )
            if why is not None:
                self.note_once(
                    "IsExternal was left out on the walls of storey %s that the change set made or changed, and on their doors and windows: "
                    "%s, so the app cannot tell outside from inside" % (_ifc_text(s.Name), why)
                )
                continue
            outer = {x["wall"] for x in rec["external"]}  # ty: ignore[not-subscriptable]
            inner = {w for room in rec["rooms"] for w in room["walls"]} - outer  # ty: ignore[not-subscriptable]
            alone = []
            for w in mine:
                if w.id() not in outer and w.id() not in inner:
                    alone.append(w)
                    continue
                value = self.set_external(w, w.id() in outer)
                for f in self.fillings(w):
                    self.set_external(f, value)
            if alone:
                self.note_once(
                    "IsExternal was left out on walls %s of storey %s: they bound no room"
                    % (", ".join(self.element_label(w) for w in alone), _ifc_text(s.Name))
                )

    def apply(self, ops):
        if not isinstance(ops, list):
            raise DataspatiumError("the change set must be a list of operations")
        if self.m.schema == "IFC2X3" and self.owner is None:
            raise DataspatiumError("the IFC2X3 file has no owner history to give new entities")
        previous = self.owner_settings()
        try:
            self.apply_all(ops)
            self.finish()
        finally:
            self.restore_owner_settings(previous)
        if self.owner is not None:
            # The API makes some relationships without an owner history; IFC2X3 requires one on every IfcRoot.
            for e in self.m.by_type("IfcRoot"):
                if e.OwnerHistory is None:
                    e.OwnerHistory = self.owner

    def apply_all(self, ops):
        for i, op in enumerate(ops):
            name = op.get("tool") if isinstance(op, dict) else None
            fn = getattr(self, "op_" + str(name), None) if name else None
            if fn is None:
                raise DataspatiumError("step %d: unknown operation %r" % (i + 1, name))
            try:
                fn(op)
            except DataspatiumError as exc:
                raise DataspatiumError("step %d (%s): %s" % (i + 1, name, exc))
            except Exception as exc:
                raise DataspatiumError("step %d (%s): %s: %s" % (i + 1, name, type(exc).__name__, exc))

    def findings(self, limit=50):
        """The schema check of the whole model: (findings, number of errors)."""
        import ifcopenshell.validate

        logger = ifcopenshell.validate.json_logger()
        try:
            ifcopenshell.validate.validate(self.m, logger, express_rules=False)
        except Exception as exc:
            return [
                {
                    "level": "error",
                    "ifc_class": None,
                    "id": None,
                    "attribute": None,
                    "message": "validation failed: %s" % exc,
                }
            ], 1
        out = []
        errors = 0
        for s in logger.statements:
            if not isinstance(s, dict):
                continue
            level = str(s.get("level", "")).lower()
            inst = s.get("instance")
            if level == "error":
                errors += 1
            if len(out) < limit:
                out.append(
                    {
                        "level": level,
                        "ifc_class": inst.is_a() if inst is not None else None,
                        "id": inst.id() if inst is not None else None,
                        "attribute": _ifc_text(s.get("attribute")),
                        "message": str(s.get("message", "")),
                    }
                )
        return out, errors


# ---- the building's topology (decision F46 a) -------------------------------------------------------
# The walls' centrelines per storey become rooms with shapely (which comes with IfcOpenShell): the line ends are snapped to each
# other within a tolerance, the lines noded and polygonized. Rooms that share a stretch of boundary border each other, the walls
# along the rooms' union are the external walls with the compass sector they face, and the lines' free ends are the open ends
# that a closed storey cannot have. Lengths are metres, coordinates the model's plan.

TOPOLOGY_MAX_WALLS = 2000
TOPOLOGY_MIN_ROOM = 0.5  # m²; smaller polygons are slivers between lines, not rooms
TOPOLOGY_MIN_WIDTH = 0.5  # m; so are narrower ones, such as the gap between the layers of a wall
_FACINGS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def _topo_north(model):
    """(a, b, source): the model's plan turned to the map, east = a*dx - b*dy and north = b*dx + a*dy. From IfcMapConversion's
    XAxisAbscissa and XAxisOrdinate when the file has one (it takes precedence over TrueNorth, as IFC 4.3 says), else from the
    model context's TrueNorth, else the model's +y is north."""
    contexts = [
        c
        for c in model.by_type("IfcGeometricRepresentationContext")
        if not c.is_a("IfcGeometricRepresentationSubContext")
    ]
    context = next((c for c in contexts if (c.ContextType or "Model") == "Model"), contexts[0] if contexts else None)
    try:
        conversions = model.by_type("IfcMapConversion")
    except Exception:  # IFC2X3 has no IfcMapConversion
        conversions = []
    own = [
        c for c in conversions if context is not None and c.SourceCRS is not None and c.SourceCRS.id() == context.id()
    ]
    conversion = own[0] if own else (conversions[0] if len(conversions) == 1 else None)
    if conversion is not None:
        a = 1.0 if conversion.XAxisAbscissa is None else float(conversion.XAxisAbscissa)
        b = 0.0 if conversion.XAxisOrdinate is None else float(conversion.XAxisOrdinate)
        if math.hypot(a, b) > 0:
            return a / math.hypot(a, b), b / math.hypot(a, b), "map"
    north = getattr(context, "TrueNorth", None) if context is not None else None
    if north is not None:
        r = [float(v) for v in north.DirectionRatios] + [0.0, 0.0]
        if math.hypot(r[0], r[1]) > 0:
            return r[1] / math.hypot(r[0], r[1]), r[0] / math.hypot(r[0], r[1]), "true_north"
    return 1.0, 0.0, "model"


def _topo_point(p):
    """A shapely point of (x, y)."""
    from shapely.geometry import Point

    return Point(float(p[0]), float(p[1]))


def _topo_facing(dx, dy, north):
    """The compass sector (N, NE, ... NW) a direction in the plan points to."""
    a, b = north[0], north[1]
    bearing = math.degrees(math.atan2(a * dx - b * dy, b * dx + a * dy)) % 360.0
    return _FACINGS[int((bearing + 22.5) // 45.0) % 8]


def _topo_curve(item):
    """The (x, y) points of an axis curve in its own coordinates: an IfcPolyline, an IfcIndexedPolyCurve or an IfcTrimmedCurve of an
    IfcLine; None for another curve."""
    if item.is_a("IfcPolyline"):
        return [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in item.Points]
    if item.is_a("IfcIndexedPolyCurve"):
        coords = [(float(c[0]), float(c[1])) for c in item.Points.CoordList]
        if not getattr(item, "Segments", None):
            return coords
        out = []
        for segment in item.Segments:
            for i in segment.wrappedValue:
                if not out or out[-1] != coords[int(i) - 1]:
                    out.append(coords[int(i) - 1])
        return out
    if item.is_a("IfcTrimmedCurve") and item.BasisCurve.is_a("IfcLine"):
        line = item.BasisCurve
        p0 = [float(v) for v in line.Pnt.Coordinates]
        o = [float(v) for v in line.Dir.Orientation.DirectionRatios]
        step = float(line.Dir.Magnitude) / (math.sqrt(sum(v * v for v in o)) or 1.0)

        def at(trim):
            for t in trim:
                if hasattr(t, "is_a") and t.is_a("IfcCartesianPoint"):
                    return float(t.Coordinates[0]), float(t.Coordinates[1])
            for t in trim:
                u = float(getattr(t, "wrappedValue", t))  # an IfcParameterValue along the line
                return p0[0] + u * step * o[0], p0[1] + u * step * o[1]
            return None

        ends = at(item.Trim1), at(item.Trim2)
        return list(ends) if ends[0] and ends[1] else None
    return None


def _topo_axis(wall, scale):
    """The wall's 'Axis' representation in plan, in metres in world coordinates through its placement; None without one."""
    import ifcopenshell.util.placement as placement

    rep = getattr(wall, "Representation", None)
    for r in rep.Representations if rep is not None else ():
        if (r.RepresentationIdentifier or "") != "Axis":
            continue
        for item in r.Items or ():
            pts = _topo_curve(item)
            if pts and len(pts) >= 2:
                m = (getattr(placement, "get_local_placement", None) or placement.get_placement)(  # ty: ignore[unresolved-attribute]
                    wall.ObjectPlacement
                )  # get_placement since IfcOpenShell 0.9.1
                return [
                    ((m[0][0] * x + m[0][1] * y + m[0][3]) * scale, (m[1][0] * x + m[1][1] * y + m[1][3]) * scale)
                    for x, y in pts
                ]
    return None


def _topo_curtain(e, scale):
    """Whether an element is a curtain wall that the topology reads as a wall: one with an 'Axis' (create_element draws it so). One
    without, as other programs often write them, is left out rather than counted as a wall the topology cannot read."""
    if not e.is_a("IfcCurtainWall"):
        return False
    try:
        return bool(_topo_axis(e, scale))
    except Exception:
        return False


def _topo_thickness(wall, scale):
    """The wall's thickness in metres: the narrow side of an extruded rectangle, else its material layers; None when unknown."""
    rep = getattr(wall, "Representation", None)
    for r in rep.Representations if rep is not None else ():
        for item in r.Items or ():
            if item.is_a("IfcExtrudedAreaSolid") and item.SweptArea.is_a("IfcRectangleProfileDef"):
                return min(float(item.SweptArea.XDim), float(item.SweptArea.YDim)) * scale
    try:
        import ifcopenshell.util.element as el

        mat = el.get_material(wall)
        layers = mat.ForLayerSet if mat is not None and mat.is_a("IfcMaterialLayerSetUsage") else mat
        if layers is not None and layers.is_a("IfcMaterialLayerSet"):
            return sum(float(x.LayerThickness or 0) for x in layers.MaterialLayers) * scale or None
    except Exception:  # nosec B110
        pass
    return None


def _topo_settings():
    import ifcopenshell.geom

    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    try:
        settings.set("disable-opening-subtractions", True)  # the footprint needs no openings, and the booleans are slow
    except Exception:  # nosec B110
        pass
    return settings


def _topo_footprint(wall, settings):
    """(centreline, width) from the body's footprint: its vertices (world coordinates, metres) projected to the plan, their minimum
    rotated rectangle and its long axis."""
    import ifcopenshell.geom
    import ifcopenshell.util.shape as shape_util
    import shapely

    verts = shape_util.get_vertices(ifcopenshell.geom.create_shape(settings, wall).geometry)  # ty: ignore[invalid-argument-type, unresolved-attribute]
    if len(verts) < 2:
        return None, None
    rect = shapely.oriented_envelope(shapely.MultiPoint([(float(v[0]), float(v[1])) for v in verts]))
    if rect.geom_type == "LineString":
        return [rect.coords[0], rect.coords[-1]], 0.0
    if rect.geom_type != "Polygon" or rect.is_empty:
        return None, None
    c = list(rect.exterior.coords)

    def mid(p, q):
        return (p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0

    if math.dist(c[0], c[1]) >= math.dist(c[1], c[2]):
        return [mid(c[0], c[3]), mid(c[1], c[2])], math.dist(c[1], c[2])
    return [mid(c[0], c[1]), mid(c[3], c[2])], math.dist(c[0], c[1])


def _topo_cross(p, d, a, b):
    """(t, x): where the line through p along the unit vector d crosses the line through a and b, p + t * d; None when parallel."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    den = d[0] * ry - d[1] * rx
    if abs(den) < 1e-9 * (math.hypot(rx, ry) or 1.0):
        return None
    t = ((a[0] - p[0]) * ry - (a[1] - p[1]) * rx) / den
    return t, (round(p[0] + t * d[0], 6), round(p[1] + t * d[1], 6))


def _topo_pairs(pts, radius):
    """The pairs (distance, a, b) of points at most radius apart, closest first, found on a grid of cells radius wide."""
    cells = {}
    size = max(radius, 1e-6)
    out = []
    for n, p in enumerate(pts):
        cx, cy = math.floor(p[0] / size), math.floor(p[1] / size)
        for cell in [(cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]:
            out.extend((math.dist(pts[m], p), m, n) for m in cells.get(cell, ()) if math.dist(pts[m], p) <= radius)
        cells.setdefault((cx, cy), []).append(n)
    return sorted(out)


def _topo_snap(lines, tol, widths=None):
    """The lines with their ends snapped, in three steps. Ends that coincide within a centimetre meet at their mean. Another end goes
    along its own line, extended or trimmed, to the nearest crossing with a line near it (a T junction; or a corner, where that
    line's free end goes there too, as when both walls of a corner stop at the other's face), moving at most half the thickness of
    the wall it meets plus 0.05 m, and never more than tol. Ends still free pair up within tol, closest first, unless their walls run
    side by side as the layers of a wall do. A point that lands inside a line is inserted in it, so that noding joins the two exactly."""
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree

    out = [[(round(float(c[0]), 6), round(float(c[1]), 6)) for c in line] for line in lines]
    reach = [min(tol, float(w or 0.2) / 2.0 + 0.05) for w in (widths or [None] * len(out))]
    ends = [(i, k) for i in range(len(out)) for k in (0, -1)]

    def end(m):
        return out[ends[m][0]][ends[m][1]]

    def direction(m):
        """The unit vector along the line out of its end m, or None."""
        i, k = ends[m]
        p, q = out[i][k], (out[i][1] if k == 0 else out[i][-2])
        n = math.dist(p, q)
        return ((p[0] - q[0]) / n, (p[1] - q[1]) / n) if n > 0 else None

    def meet(group):
        mean = (
            round(sum(end(m)[0] for m in group) / len(group), 6),
            round(sum(end(m)[1] for m in group) / len(group), 6),
        )
        for m in group:
            out[ends[m][0]][ends[m][1]] = mean

    parent = list(range(len(ends)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for _, a, b in _topo_pairs([end(m) for m in range(len(ends))], 0.01):
        parent[find(a)] = find(b)
    groups = {}
    for m in range(len(ends)):
        groups.setdefault(find(m), []).append(m)
    free = {g[0] for g in groups.values() if len(g) == 1}
    for g in groups.values():
        if len(g) > 1:
            meet(g)
    geoms = [LineString(c) for c in out]
    tree = STRtree(geoms)
    inserted = {}
    for m in sorted(free):
        d = direction(m)
        if m not in free or d is None:
            continue  # taken to a corner by the other wall's end, or a line without length
        i, k = ends[m]
        p = end(m)
        best = None
        for j in (int(x) for x in tree.query(Point(p).buffer(2.0 * tol)) if int(x) != i):
            for a, b in zip(out[j], out[j][1:]):
                hit = _topo_cross(p, d, a, b)
                if hit is None or abs(hit[0]) > reach[j] or (best is not None and abs(hit[0]) >= best[0]):
                    continue
                corner = None
                off = LineString([a, b]).distance(Point(hit[1]))
                if off > 1e-6:  # beyond the other line's end: a corner, when that end is free and near enough
                    corner = 2 * j + (0 if math.dist(hit[1], out[j][0]) < math.dist(hit[1], out[j][-1]) else 1)
                    if off > reach[i] or corner not in free:
                        continue
                best = (abs(hit[0]), j, hit[1], corner)
        if best is None:
            continue
        _, j, x, corner = best
        free.discard(m)
        out[i][k] = x
        if corner is None:
            inserted.setdefault(j, []).append(x)
        else:
            free.discard(corner)
            out[j][ends[corner][1]] = x
    left = sorted(free)
    for _, a, b in _topo_pairs([end(m) for m in left], tol):
        ma, mb = left[a], left[b]
        if ma not in free or mb not in free:
            continue
        da, db = direction(ma), direction(mb)
        pa, pb = end(ma), end(mb)
        if (
            da
            and db
            and abs(da[0] * db[1] - da[1] * db[0]) < 0.17
            and abs((pb[0] - pa[0]) * da[1] - (pb[1] - pa[1]) * da[0]) > 0.05
        ):
            continue  # parallel within 10 degrees and side by side
        free.difference_update((ma, mb))
        meet([ma, mb])
    for j, points in inserted.items():
        first, last = out[j][0], out[j][-1]
        inner = sorted(set(out[j][1:-1]) | set(points), key=lambda c: geoms[j].project(Point(c)))
        out[j] = [first] + [c for c in inner if c != first and c != last] + [last]
    return out


def _topo_outward(union, part, tol, north):
    """The compass sector a wall along the rooms' union faces: the normal of its longest piece there that points out of the rooms."""
    from shapely.geometry import Point

    pieces = [g for g in getattr(part, "geoms", [part]) if g.geom_type == "LineString" and g.length > 0]
    if not pieces:
        return None
    piece = max(pieces, key=lambda g: g.length)
    (x0, y0), (x1, y1) = piece.coords[0][:2], piece.coords[-1][:2]
    n = math.hypot(x1 - x0, y1 - y0)
    if n == 0:
        return None
    nx, ny = -(y1 - y0) / n, (x1 - x0) / n
    mid = piece.interpolate(0.5, normalized=True)
    for eps in (0.05, tol + 0.05):
        left = union.covers(Point(mid.x + nx * eps, mid.y + ny * eps))
        right = union.covers(Point(mid.x - nx * eps, mid.y - ny * eps))
        if left != right:
            return _topo_facing(-nx, -ny, north) if left else _topo_facing(nx, ny, north)
    return None


def _topo_plan(walls, tol, north=(1.0, 0.0, "model"), shapes=False):
    """One storey's plan from its walls' centrelines, with shapely alone: `walls` are dicts {"id", "guid", "name", "line": [(x, y),
    ...], "width"} in metres (width may be None). Rooms are the polygons over TOPOLOGY_MIN_ROOM and TOPOLOGY_MIN_WIDTH. A wall
    belongs to a room, and is external, when it runs along the room's boundary (or the union's) for longer than one that only meets
    it there; two rooms are neighbours when their boundaries share such a stretch. With shapes, every room also has its "polygon"
    (shapely, counter-clockwise) and its "net_area" inside the walls' faces, for create_spaces; neither goes into JSON."""
    from shapely.geometry import LineString
    from shapely.geometry.polygon import orient
    from shapely.ops import polygonize_full, unary_union
    from shapely.strtree import STRtree

    snapped = _topo_snap([w["line"] for w in walls], tol, [w.get("width") for w in walls])
    keep = [i for i, c in enumerate(snapped) if LineString(c).length > 1e-6]
    if not keep:
        return {"rooms": [], "external": [], "dangles": [], "closed": False, "dangling": []}  # Dataregium
    walls = [walls[i] for i in keep]
    geoms = [LineString(snapped[i]) for i in keep]
    noded = unary_union(geoms)
    edges = [g for g in getattr(noded, "geoms", [noded]) if g.geom_type == "LineString"]
    degree = {}
    for e in edges:
        for c in (e.coords[0], e.coords[-1]):
            key = (round(c[0], 6), round(c[1], 6))
            degree[key] = degree.get(key, 0) + 1
    dangles = sorted([round(x, 3), round(y, 3)] for (x, y), n in degree.items() if n == 1)
    # Dataregium: the walls whose line ends where an end dangles, by GlobalId.
    loose = {key for key, n in degree.items() if n == 1}
    dangling = sorted(
        {
            walls[i].get("guid")
            for i, g in enumerate(geoms)
            for c in (g.coords[0], g.coords[-1])
            if (round(c[0], 6), round(c[1], 6)) in loose
        }
        - {None}
    )
    polygons = list(polygonize_full(edges)[0].geoms)
    rooms = sorted(
        (p for p in polygons if p.area > TOPOLOGY_MIN_ROOM and not p.buffer(-TOPOLOGY_MIN_WIDTH / 2.0).is_empty),
        key=lambda p: (round(p.centroid.y), round(p.centroid.x, 6), -round(p.area, 6)),
    )  # a room around another comes first
    tree = STRtree(geoms)
    meets = 2.0 * tol + 0.01  # a wall that only meets a boundary runs at most 2 * tol inside its buffer

    def along(zone):
        out = []
        for j in tree.query(zone):
            part = geoms[int(j)].intersection(zone)
            if part.length > meets:
                out.append((int(j), part))
        return out

    neighbours = [set() for _ in rooms]
    room_tree = STRtree(rooms)
    for i, r in enumerate(rooms):
        for k in (int(x) for x in room_tree.query(r)):
            if k > i and r.boundary.intersection(rooms[k].boundary).length > meets:
                neighbours[i].add(k)
                neighbours[k].add(i)
    out_rooms = []
    for i, r in enumerate(rooms):
        members = along(r.boundary.buffer(tol))
        rec = {
            "index": i + 1,
            "area": round(r.area, 2),
            "perimeter": round(r.length, 2),
            "walls": sorted({walls[j]["id"] for j, _ in members}),
            "neighbours": sorted(k + 1 for k in neighbours[i]),
        }
        if shapes:
            # inside the faces: the outline less a band of half each wall's thickness along it
            bands = unary_union(
                [geoms[j].buffer(float(walls[j].get("width") or 0.2) / 2.0, cap_style="flat") for j, _ in members]
            )
            rec["polygon"], rec["net_area"] = orient(r, sign=1.0), r.difference(bands).area
        out_rooms.append(rec)
    external = []
    if rooms:
        union = unary_union(polygons)  # with the slivers between the layers of a wall, which are inside too
        for j, part in along(union.boundary.buffer(tol)):
            facing = _topo_outward(union, part, tol, north)
            if facing:
                external.append(
                    {
                        "wall": walls[j]["id"],
                        "guid": walls[j].get("guid"),
                        "name": walls[j].get("name"),
                        "facing": facing,
                        "length": round(part.length, 2),
                    }
                )
        external.sort(key=lambda e: (_FACINGS.index(e["facing"]), e["wall"]))
    return {
        "rooms": out_rooms,
        "external": external,
        "dangles": dangles,
        "closed": bool(rooms) and not dangles,
        "dangling": dangling,  # Dataregium
    }


def _topo_elevation(storey, scale):
    if storey.Elevation is not None:
        return float(storey.Elevation) * scale
    try:
        import ifcopenshell.util.placement as placement

        return (
            float(
                (getattr(placement, "get_local_placement", None) or placement.get_placement)(storey.ObjectPlacement)[2][  # ty: ignore[unresolved-attribute]
                    3
                ]
            )
            * scale
        )  # get_placement since 0.9.1
    except Exception:
        return 0.0


def _ifc_topology(model, tolerance=None, storeys=None, shapes=False):
    """The building's topology per storey (decision F46 a), as a list in the storeys' order of elevation:

        {"storey", "elevation" (m), "rooms": [{"index" (from 1), "area" (m²), "perimeter" (m), "walls": [wall ids], "neighbours":
         [room indices]}], "external": [{"wall", "guid", "name", "facing" (N, NE, E, SE, S, SW, W, NW), "length" (m)}], "dangles":
         [[x, y], ...] (open wall ends, metres), "closed" (at least one room and nothing dangles), "walls" (on the storey), "unread"
         (walls whose centreline could not be read), "tolerance" (m), "north" (map, true_north or model)}

    A wall's centreline is its 'Axis' representation through its placement, else the long axis of its body's footprint
    (ifcopenshell.geom); a curtain wall with an 'Axis' (create_element's) counts as a wall, one without is left out (_topo_curtain).
    The ends are snapped within `tolerance` metres (by default half the storey's thickest wall plus 0.05 m).
    `storeys` (names) limits the storeys read. At most TOPOLOGY_MAX_WALLS walls. With shapes (for create_spaces, never for JSON) the
    rooms carry their polygons and net areas (_topo_plan) and every storey its entity as "_storey".
    """
    import ifcopenshell.util.unit

    try:
        scale = float(ifcopenshell.util.unit.calculate_unit_scale(model) or 1.0)  # metres per file unit
    except Exception:
        scale = 1.0
    north = _topo_north(model)
    chosen = []
    for s in model.by_type("IfcBuildingStorey"):
        if storeys is not None and _ifc_text(s.Name) not in storeys:
            continue
        chosen.append(
            (
                s,
                [
                    e
                    for rel in (getattr(s, "ContainsElements", None) or ())
                    for e in (rel.RelatedElements or ())
                    if e.is_a("IfcWall") or _topo_curtain(e, scale)
                ],
            )
        )
    total = sum(len(walls) for _, walls in chosen)
    if total > TOPOLOGY_MAX_WALLS:
        raise DataspatiumError("the topology reads at most %d walls; there are %d" % (TOPOLOGY_MAX_WALLS, total))
    settings = None
    out = []
    for s, walls in sorted(chosen, key=lambda x: _topo_elevation(x[0], scale)):
        read, widths, unread = [], [], 0
        missed = []  # Dataregium
        for w in walls:
            try:
                line = _topo_axis(w, scale)
                width = _topo_thickness(w, scale) if line else None
            except Exception:
                line = None
            if not line:
                try:
                    if settings is None:
                        settings = _topo_settings()
                    line, width = _topo_footprint(w, settings)
                except Exception:
                    line = None
            if not line or math.dist(line[0], line[-1]) <= 1e-6:
                unread += 1
                missed.append(getattr(w, "GlobalId", None))  # Dataregium
                continue
            read.append(
                {
                    "id": w.id(),
                    "guid": getattr(w, "GlobalId", None),
                    "name": _ifc_text(getattr(w, "Name", None)),
                    "line": line,
                    "width": width,
                }
            )
            if width:
                widths.append(width)
        tol = float(tolerance) if tolerance else max([x for x in widths if x <= 1.0] or [0.2]) / 2.0 + 0.05
        rec = {"storey": _ifc_text(s.Name), "elevation": round(_topo_elevation(s, scale), 3)}
        rec.update(_topo_plan(read, tol, north, shapes))
        rec.update({"walls": len(walls), "unread": unread, "tolerance": round(tol, 3), "north": north[2]})
        rec["unread_walls"] = missed  # Dataregium
        if shapes:
            rec["_storey"] = s
        out.append(rec)
    return out


# ---- the right IFC class (decision B-57.2) ------------------------------------------------------------
# In native IFC the class is the classification (decision B-59): the most specific entity with its PredefinedType, an ObjectType
# only for USERDEFINED, and a type object; a system the person names comes on top through set_classification. Of this part of the
# original only what _ChangeSet calls is copied: the app's read tools _ifc_class_search and _ifc_class_report are not.

# PredefinedTypes the IFC releases deprecate (bSI rule IFC102), by the file's schema family: never written, flagged in the search.
DEPRECATED_VALUES = {
    "IFC4": {
        "IfcWall": ("STANDARD", "POLYGONAL", "ELEMENTEDWALL"),
        "IfcWallType": ("STANDARD", "POLYGONAL", "ELEMENTEDWALL"),
    },
    "IFC4X3": {
        "IfcWall": ("STANDARD", "POLYGONAL"),
        "IfcWallType": ("STANDARD", "POLYGONAL"),
        "IfcSpace": ("INTERNAL", "EXTERNAL"),
        "IfcSpaceType": ("INTERNAL", "EXTERNAL"),
        "IfcBuildingElementProxy": ("PROVISIONFORSPACE", "PROVISIONFORVOID"),
    },
}


def _ifc_schema(name):
    """IfcOpenShell's definition of a schema by its name (IFC2X3, IFC4, IFC4X3_ADD2 ...), or by the one of its line that the app's
    IfcOpenShell carries (a file header's IFC4X3 or IFC2X3_TC1)."""
    import ifcopenshell

    upper = str(name or "").upper()
    for candidate in (str(name), next((s for s in IFC_NEW_SCHEMAS if upper.startswith(s.split("_")[0])), None)):
        try:
            return ifcopenshell.schema_by_name(candidate)
        except Exception:  # nosec B112
            continue
    raise DataspatiumError("unknown IFC schema %r; the app reads %s" % (name, ", ".join(IFC_NEW_SCHEMAS)))


def _ifc_declaration(schema, name):
    """A declaration of the schema by name; None for one it lacks."""
    try:
        return schema.declaration_by_name(str(name))
    except Exception:
        return None


def _ifc_enum_items(decl, attribute="PredefinedType"):
    """The items of an entity's enumeration attribute; None when the entity has no such attribute."""
    import ifcopenshell.util.attribute

    attr = next((a for a in decl.all_attributes() if a.name() == attribute), None)
    if attr is None:
        return None
    try:
        return list(ifcopenshell.util.attribute.get_enum_items(attr))
    except Exception:
        return None


def _ifc_concrete(decl):
    """The names of the non-abstract entities at or below a declaration, in order."""
    out, stack = set(), [decl]
    while stack:
        d = stack.pop()
        if not d.is_abstract():
            out.add(d.name())
        stack.extend(d.subtypes())
    return sorted(out)


# The schemas a new model can be written in (OB-06): the newest first, which is the default.
IFC_NEW_SCHEMAS = ("IFC4X3_ADD2", "IFC4", "IFC2X3")


# ---- the server's own, below the copy -----------------------------------------------------------------------------

SERVER = "Dataregium"  # the organisation and the application that the owner histories name beside the proposer
MAX_NOTES = 100
MAX_MESSAGE = 300
# How apply_all() above words the operation that failed: its step counted from 1, its tool, and why.
FAILED_STEP = re.compile(r"^step ([0-9]+)(?: \(([a-z_]+)\))?: (.*)$", re.DOTALL)


class ServerChangeSet(_ChangeSet):
    """``_ChangeSet`` as the server runs it: the owner histories that IfcOpenShell's API makes name the proposer and
    this server, as an IfcPersonAndOrganization and an IfcApplication found in the file from an earlier proposal or
    made in it once, instead of the file's own owner. That holds where the file records owners: always in IFC2X3, where
    every IfcRoot needs one and the original lends the file's, and in a later schema when the file has a user and an
    application, which IfcOpenShell's defaults would name; a file without owners gets none, as in the app. What the
    copy itself makes still gets the file's first owner history (``self.owner``), as in the app."""

    def __init__(self, model: Any, proposer: str) -> None:
        super().__init__(model)
        self.proposer = str(proposer or "unknown")[:255]
        self._owning: tuple[Any, Any] | None = None

    def owner_settings(self) -> Any:
        import ifcopenshell.api.owner.settings as settings

        if self.m.schema == "IFC2X3":
            if self.owner is None:
                return None
        elif not (self.m.by_type("IfcPersonAndOrganization") and self.m.by_type("IfcApplication")):
            return None
        previous = (settings.get_user, settings.get_application)
        settings.get_user = lambda _file: self.owning()[0]  # ty: ignore[invalid-assignment]
        settings.get_application = lambda _file: self.owning()[1]  # ty: ignore[invalid-assignment]
        return previous

    def _identification(self, actor: Any) -> Any:
        return actor.Id if self.m.schema == "IFC2X3" else actor.Identification

    def owning(self) -> tuple[Any, Any]:
        """The proposer as the user and this server as the application of an owner history."""
        if self._owning is not None:
            return self._owning
        import ifcopenshell.api.owner

        application = next(
            (
                found
                for found in self.m.by_type("IfcApplication")
                if found.ApplicationIdentifier == SERVER and found.Version == __version__
            ),
            None,
        )
        if application is None:
            organisation = ifcopenshell.api.owner.add_organisation(self.m, identification=SERVER, name=SERVER)
            application = ifcopenshell.api.owner.add_application(
                self.m,
                application_developer=organisation,
                version=__version__,
                application_full_name=SERVER,
                application_identifier=SERVER,
            )
        organisation = application.ApplicationDeveloper
        user = next(
            (
                found
                for found in self.m.by_type("IfcPersonAndOrganization")
                if found.TheOrganization.id() == organisation.id()
                and self._identification(found.ThePerson) == self.proposer
            ),
            None,
        )
        if user is None:
            named = "Id" if self.m.schema == "IFC2X3" else "Identification"
            person = self.m.create_entity("IfcPerson", **{named: self.proposer, "FamilyName": self.proposer})
            user = ifcopenshell.api.owner.add_person_and_organisation(self.m, person=person, organisation=organisation)
        self._owning = (user, application)
        return self._owning


def run(model: Any, ops: list[dict[str, Any]], *, proposer: str) -> dict[str, Any]:
    """The operations applied to ``model`` in their order, as the app applies them.

    The answer says how many operations there are and how many applied, the GlobalId of each created element by its
    ref (``created``), the GlobalIds of what they created or changed and is still there (``objects``) and of what they
    deleted (``removed``), the notes on what an operation could not do in the file's schema, and in ``failed`` the
    operation that failed: its index from 0, its tool and why, in the words of the copy, which may quote its
    arguments and the names in the file. The operations after it are not applied, as in the app.
    """
    changeset = ServerChangeSet(model, proposer)
    failed: dict[str, Any] | None = None
    try:
        changeset.apply(ops)
    except DataspatiumError as exc:
        failed = _failed(str(exc))
    removed = {str(record["guid"]) for record in changeset.deleted if record.get("guid")}
    made = [*changeset.created, *changeset.changed.values()]
    return {
        "count": len(ops),
        "applied": len(ops) if failed is None else int(failed["op"] or 0),
        "created": {
            str(record["ref"]): str(record["guid"])
            for record in changeset.created
            if record.get("ref") and record.get("guid")
        },
        "objects": sorted({str(record["guid"]) for record in made if record.get("guid")} - removed),
        "removed": sorted(removed),
        "notes": [str(note)[:MAX_MESSAGE] for note in changeset.notes[:MAX_NOTES]],
        "failed": failed,
    }


def _failed(message: str) -> dict[str, Any]:
    """The failed operation from the words of ``apply_all``; without an index and a tool when the file cannot take the
    operations at all, as an IFC2X3 file without an owner history."""
    match = FAILED_STEP.match(message)
    if match is None:
        return {"op": None, "tool": None, "error": message[:MAX_MESSAGE]}
    return {"op": int(match.group(1)) - 1, "tool": match.group(2), "error": match.group(3)[:MAX_MESSAGE]}
