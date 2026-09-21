"""Display formatting shared by the templates and the map's GeoJSON.

Kept free of Flask so tests can import it without an app.  There is one
implementation of magnitude formatting: create_app() registers it as a Jinja
filter and map_points() calls it directly, so the table and the map popup
cannot drift apart.
"""

from decimal import Decimal


def magnitude(value, unit):
    """Render a report magnitude with its unit for display.

    inches -> two decimals, always (1.00", 1.75"), the convention for hail size.
    mph    -> exactly what was reported, no trailing zeros (66.00 -> 66 mph,
              58.60 -> 58.6 mph).  No rounding: a fractional wind speed is
              information the reporter gave us.
    None   -> ''.
    other  -> the value as stored, no unit.
    """
    if value is None:
        return ""

    if unit == "inches":
        return f"{_as_decimal(value):.2f}\""

    if unit == "mph":
        return f"{_strip_zeros(_as_decimal(value))} mph"

    return str(value)


def _as_decimal(value):
    # str() first so a float 58.6 becomes Decimal('58.6'), not the binary
    # expansion.  NUMERIC columns already arrive as Decimal.
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _strip_zeros(d):
    # Not Decimal.normalize(): Decimal('60.00').normalize() is 6E+1, which
    # prints in scientific notation.  Format as fixed-point first, then strip,
    # and only when there is a '.', otherwise '60' would lose its zero and
    # become '6'.  NaN/Infinity have no '.', so they pass through unchanged
    # (NUMERIC accepts NaN -- see CLAUDE.md data traps) rather than raising.
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s
