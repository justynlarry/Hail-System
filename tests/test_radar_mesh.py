"""Tests for hailsys.radar.mesh:  the MESH GRIB2 reader, on small synthetic files.

The golden test at the bottom compares with the analysis decoder on a real NOAA file and
runs only when MESH_GOLDEN_DIR points at a folder holding the downloaded file.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from functools import lru_cache

from hailsys.radar import mesh
from tests import radar_fixtures as fx

CELLS = {(10, 100): 137, (12, 105): 300, (20, 130): 200, (15, 120): 136, (14, 110): 0}


_DIR = tempfile.TemporaryDirectory()


@lru_cache(maxsize=None)
def _file(depth, filters=(), cells=tuple(sorted(CELLS.items())), **kw):
    """Build each distinct file once, they are 7000 x 3500 and slow to encode."""
    handle, path = tempfile.mkstemp(suffix=".grib2", dir=_DIR.name)
    os.close(handle)
    fx.write(path, fx.grib(dict(cells), depth, dict(filters), **kw))
    return path


def tearDownModule():
    _DIR.cleanup()


class TestRead(unittest.TestCase):
    def test_returns_cells_at_or_above_the_floor(self):
        got = mesh.read_cells(_file(16), fx.BOX, fx.FLOOR_MM)
        self.assertEqual([(r, c) for r, c, _ in got["cells"]],
                         [(10, 100), (12, 105), (20, 130)])
        self.assertAlmostEqual(got["cells"][1][2], 29.0)

    def test_cell_exactly_at_the_floor_is_kept_and_one_below_is_not(self):
        got = mesh.read_cells(_file(16), fx.BOX, fx.FLOOR_MM)
        rows = {(r, c) for r, c, _ in got["cells"]}
        self.assertIn((10, 100), rows)       # 12.7 mm
        self.assertNotIn((15, 120), rows)    # 12.6 mm

    def test_max_and_count(self):
        got = mesh.read_cells(_file(16), fx.BOX, fx.FLOOR_MM)
        self.assertAlmostEqual(got["max_mm"], 29.0)
        self.assertEqual(got["cells_in_box"], 11 * 31)

    def test_valid_time_comes_from_the_file(self):
        path = _file(16, when=(2023, 5, 11, 7, 0, 0))
        got = mesh.read_cells(path, fx.BOX, fx.FLOOR_MM)
        self.assertEqual(got["valid_time"], datetime(2023, 5, 11, 7, 0, tzinfo=timezone.utc))

    def test_cells_outside_the_box_are_left_out(self):
        path = _file(16, cells=tuple(sorted({(9, 105): 300, (21, 105): 300, (12, 99): 300,
                                             (12, 131): 300, (12, 105): 250}.items())))
        got = mesh.read_cells(path, fx.BOX, fx.FLOOR_MM)
        self.assertEqual([(r, c) for r, c, _ in got["cells"]], [(12, 105)])

    def test_nothing_at_the_floor_gives_no_max(self):
        path = _file(16, cells=((( 12, 105), 100),))
        got = mesh.read_cells(path, fx.BOX, fx.FLOOR_MM)
        self.assertEqual(got["cells"], [])
        self.assertIsNone(got["max_mm"])

    def test_gzip_is_read_by_name(self):
        handle, path = tempfile.mkstemp(suffix=".grib2.gz")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        fx.write(path, fx.grib(CELLS, 16), gz=True)
        got = mesh.read_cells(path, fx.BOX, fx.FLOOR_MM)
        self.assertEqual(len(got["cells"]), 3)

    def test_a_higher_floor_drops_cells(self):
        got = mesh.read_cells(_file(16), fx.BOX, 25.4)
        self.assertEqual([(r, c) for r, c, _ in got["cells"]], [(12, 105)])


class TestPacking(unittest.TestCase):
    def test_eight_bit_files_are_read(self):
        # 8-bit tops out at x = 255, 24.5 mm here, so use a lower floor and values
        cells = tuple(sorted({(12, 105): 200, (15, 120): 136, (13, 106): 140}.items()))
        got = mesh.read_cells(_file(8, cells=cells), fx.BOX, 12.7)
        self.assertEqual([(r, c) for r, c, _ in got["cells"]], [(12, 105), (13, 106)])
        self.assertAlmostEqual(got["cells"][0][2], 19.0)

    def test_every_png_filter_at_both_depths(self):
        cells = tuple(sorted({(11, 101): 150, (12, 101): 160, (12, 102): 170, (13, 101): 180,
                              (13, 102): 190, (13, 103): 200, (14, 103): 210}.items()))
        for depth in (8, 16):
            for kind in (1, 2, 3, 4):
                with self.subTest(depth=depth, filter=kind):
                    filters = tuple((r, kind) for r in range(10, 16))
                    got = mesh.read_cells(_file(depth, filters, cells), fx.BOX, 12.7)
                    want = [(r, c, fx.mm(x)) for (r, c), x in cells]
                    self.assertEqual(len(got["cells"]), len(want))
                    for have, expected in zip(got["cells"], want):
                        self.assertEqual(have[:2], expected[:2])
                        self.assertAlmostEqual(have[2], expected[2])

    def test_mixed_filters_on_neighbouring_rows(self):
        cells = tuple(sorted({(11, 101): 150, (12, 102): 160, (13, 103): 170, (14, 104): 180}.items()))
        filters = ((10, 1), (11, 4), (12, 3), (13, 2), (14, 4))
        got = mesh.read_cells(_file(16, filters, cells), fx.BOX, 12.7)
        self.assertEqual([(r, c) for r, c, _ in got["cells"]],
                         [(11, 101), (12, 102), (13, 103), (14, 104)])


class TestRefusals(unittest.TestCase):
    def _fails(self, data, match, box=fx.BOX):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        self.addCleanup(os.unlink, path)
        fx.write(path, data)
        with self.assertRaisesRegex(mesh.MeshError, match):
            mesh.read_cells(path, box, fx.FLOOR_MM)

    def test_not_grib(self):
        self._fails(b"hello world, definitely not a grib file", "not a GRIB2")

    def test_wrong_grid(self):
        self._fails(fx.grib({}, ni=6999), "not the MRMS CONUS grid")

    def test_wrong_packing_template(self):
        self._fails(fx.grib({}, template=0), "not PNG packed")

    def test_bit_depth_must_agree_with_the_png(self):
        self._fails(fx.grib({}, 16, nbits=8), "packed at 8 bits but the PNG holds 16")

    def test_bitmap_is_refused(self):
        self._fails(fx.grib({}, bitmap=True), "bitmap")

    def test_box_outside_the_grid(self):
        self._fails(fx.grib({}), "outside the grid", box=(60.0, 59.0, -129.0, -128.0))


@unittest.skipUnless(os.environ.get("MESH_GOLDEN_DIR"), "set MESH_GOLDEN_DIR to run")
class TestGolden(unittest.TestCase):
    def test_matches_the_analysis_decoder_on_a_real_file(self):
        folder = os.environ["MESH_GOLDEN_DIR"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docs", "analysis",
                                        "storm-shape-mesh-2026-10", "storm_shape", "mesh"))
        import mesh_lib
        path = os.path.join(folder, "MRMS_MESH_Max_1440min_00.50_20260609-060000.grib2.gz")
        box = (41.5, 36.5, -109.7, -101.4)
        old = mesh_lib.read_crop(path, *box)
        new = mesh.read_cells(path, box, 12.7)
        want = {(r, c): v for r, row in enumerate(old["mm"])
                for c, v in enumerate(row) if v >= 12.7}
        got = {(r - round((mesh.LA1 - old["lat_top"]) / mesh.STEP),
                c - round((old["lon_left"] - mesh.LO1) / mesh.STEP)): v
               for r, c, v in new["cells"]}
        self.assertEqual(len(got), len(want))
        self.assertEqual(set(got), set(want))
        self.assertEqual(max(abs(got[k] - want[k]) for k in want), 0.0)
        # the 2026-06-08 Denver day: 5,196 cells at or above 1.00 in (slice 0)
        self.assertEqual(sum(v >= mesh.MM_PER_INCH for v in want.values()), 5196)


if __name__ == "__main__":
    unittest.main()
