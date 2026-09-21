"""Tests for hailsys/formatting.py -- the one magnitude formatter.

Run from the repo root:
    python3 -m unittest tests.test_formatting

Pure functions, no Flask and no database.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hailsys.formatting import magnitude


class InchesTest(unittest.TestCase):
    def test_always_two_decimals(self):
        self.assertEqual(magnitude(Decimal("1.75"), "inches"), '1.75"')
        self.assertEqual(magnitude(Decimal("1.00"), "inches"), '1.00"')
        self.assertEqual(magnitude(Decimal("1"), "inches"), '1.00"')
        self.assertEqual(magnitude(Decimal("0.5"), "inches"), '0.50"')

    def test_float_input(self):
        # map_points used to hand over a float; str() keeps 1.75 as 1.75.
        self.assertEqual(magnitude(1.75, "inches"), '1.75"')


class MphTest(unittest.TestCase):
    # The three cases below are the reason strip-zeros is not normalize():
    # Decimal('60.00').normalize() is 6E+1.
    def test_whole_number_keeps_its_zero(self):
        self.assertEqual(magnitude(Decimal("60.00"), "mph"), "60 mph")

    def test_fraction_is_kept_not_rounded(self):
        self.assertEqual(magnitude(Decimal("58.60"), "mph"), "58.6 mph")
        self.assertEqual(magnitude(Decimal("61.50"), "mph"), "61.5 mph")
        self.assertEqual(magnitude(Decimal("9.75"), "mph"), "9.75 mph")

    def test_hundred_is_not_scientific_or_truncated(self):
        self.assertEqual(magnitude(Decimal("100.00"), "mph"), "100 mph")

    def test_other_wholes(self):
        self.assertEqual(magnitude(Decimal("66.00"), "mph"), "66 mph")
        self.assertEqual(magnitude(Decimal("0.00"), "mph"), "0 mph")
        self.assertEqual(magnitude(Decimal("10.00"), "mph"), "10 mph")

    def test_float_and_int_input(self):
        self.assertEqual(magnitude(58.6, "mph"), "58.6 mph")
        self.assertEqual(magnitude(60.0, "mph"), "60 mph")
        self.assertEqual(magnitude(100, "mph"), "100 mph")

    def test_exponent_form_input(self):
        self.assertEqual(magnitude(Decimal("1E+2"), "mph"), "100 mph")


class NoneAndOtherUnitsTest(unittest.TestCase):
    def test_none_value_is_empty_for_any_unit(self):
        for unit in ("inches", "mph", "none", None, ""):
            self.assertEqual(magnitude(None, unit), "")

    def test_other_units_return_value_as_stored(self):
        # 'none' is a real mag_unit string in report_types; NULL is also real.
        self.assertEqual(magnitude(Decimal("59.00"), "none"), "59.00")
        self.assertEqual(magnitude(Decimal("1500.00"), "none"), "1500.00")
        self.assertEqual(magnitude(Decimal("0.25"), None), "0.25")


class NonFiniteTest(unittest.TestCase):
    # CLAUDE.md trap: Postgres NUMERIC accepts NaN, so one can reach a template.
    # It must render, not raise and take the page down.
    def test_nan_and_infinity_do_not_raise(self):
        for unit in ("inches", "mph"):
            for raw in ("NaN", "Infinity", "-Infinity"):
                magnitude(Decimal(raw), unit)

    def test_nan_mph_is_not_mangled(self):
        self.assertEqual(magnitude(Decimal("NaN"), "mph"), "NaN mph")


if __name__ == "__main__":
    unittest.main()
