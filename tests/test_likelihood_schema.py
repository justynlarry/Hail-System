"""Tests for sql/054_likelihood.sql -- the hail-likelihood tables (Phase 5c, step 1b).

Needs a role that can insert into them (hail_admin), the same as the radar tests; everything runs in a transaction that
is always rolled back:

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_likelihood_schema

What they pin:  the first parameter set and its 20 rules are seeded (every layer by every band, the design grid);
parameter sets and rules are append-only;  a set needs a real radar set, a CoCoRaHS size in range and three ordered
edges;  a rule's layer, band and level are known and there is one per layer and band;  a build needs a loaded radar
day and a real set, has ordered window and non-negative counts, and one per day and set;  a zone has a known level
(never 'none'), a valid MultiPolygon and a positive area, and belongs to a build;  and the grants:  both app and
ingest read all four and write only builds and zones (insert and delete), never the sets or rules.
"""

import os
import unittest
from datetime import date, datetime, timezone

UTC = timezone.utc
LAYERS = ("core", "margin", "radar_indicated", "outer", "none")
BANDS = ("le_1", "1_3", "3_5", "over_5")
SQUARE = "MULTIPOLYGON(((-105 40, -104.9 40, -104.9 40.1, -105 40.1, -105 40)))"
START = datetime(2024, 5, 31, 6, 0, tzinfo=UTC)
END = datetime(2024, 6, 1, 6, 0, tzinfo=UTC)

# The design grid of 2026-10-23, as seeded.
SEEDED = {
    "core": ("agree", "agree", "agree", "one_source"),
    "margin": ("agree", "agree", "one_source", "one_source"),
    "radar_indicated": ("one_source",) * 4,
    "outer": ("one_source", "one_source", "context", "context"),
    "none": ("one_source", "one_source", "one_source", "none"),
}


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class LikelihoodSchemaTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT to_regclass('likelihood_builds') IS NOT NULL AS ok").fetchone()["ok"]:
            self.fail("sql/054_likelihood.sql is not applied")
        if not self.conn.execute("SELECT has_table_privilege('likelihood_builds', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert likelihood rows (hail_admin)")
        self.radar_set = self.conn.execute("SELECT max(set_id) AS s FROM radar_parameter_sets").fetchone()["s"]
        # A day of its own:  the real days have real builds now, and a test must not collide with them.
        run = self.conn.execute("INSERT INTO radar_runs (run_mode) VALUES ('manual') RETURNING run_id").fetchone()["run_id"]
        self.day = date(1999, 6, 8)
        self.conn.execute(
            "INSERT INTO radar_days (day, run_id, status, stamp_utc, source_key, box_lat_max, box_lat_min, "
            "box_lon_min, box_lon_max, floor_mm, cells_in_box) VALUES (%s, %s, 'loaded', now(), 'k', "
            "41.5, 36.5, -109.7, -101.4, 12.7, 1000)", (self.day, run))
        self.set_id = self.conn.execute("SELECT min(set_id) AS s FROM likelihood_parameter_sets").fetchone()["s"]

    def refused(self, sql, args=()):
        import psycopg
        with self.assertRaises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation,
                                psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation,
                                psycopg.errors.RestrictViolation)):
            self.conn.execute(sql, args)
        self.conn.rollback()
        self.setUp()          # a refused statement aborts the transaction;  start a clean one

    def new_set(self, **kw):
        row = dict(name="test set", radar_set_id=self.radar_set, cocorahs_min_in=1.00,
                   band_1_mi=1.0, band_2_mi=3.0, band_3_mi=5.0)
        row.update(kw)
        cols = ", ".join(row)
        marks = ", ".join(f"%({k})s" for k in row)
        return self.conn.execute(
            f"INSERT INTO likelihood_parameter_sets ({cols}) VALUES ({marks}) RETURNING set_id", row)

    def build(self, **kw):
        row = dict(day=self.day, set_id=self.set_id, window_start=START, window_end=END, iem_reports=1,
                   cocorahs_reports=2, duplicates_dropped=0, patches=3, patches_anchored=2)
        row.update(kw)
        cols = ", ".join(row)
        marks = ", ".join(f"%({k})s" for k in row)
        return self.conn.execute(f"INSERT INTO likelihood_builds ({cols}) VALUES ({marks})", row)

    def zone(self, **kw):
        row = dict(day=self.day, set_id=self.set_id, level="agree", wkt=SQUARE, area=100.0)
        row.update(kw)
        return self.conn.execute(
            "INSERT INTO likelihood_zones (day, set_id, level, geom, area_km2) "
            "VALUES (%(day)s, %(set_id)s, %(level)s, ST_GeomFromText(%(wkt)s, 4326), %(area)s)", row)

    # -- the seed ---------------------------------------------------------------------------

    def test_the_first_set_is_seeded_on_the_radar_set_with_the_design_edges(self):
        row = self.conn.execute("SELECT * FROM likelihood_parameter_sets WHERE set_id = %s", (self.set_id,)).fetchone()
        self.assertEqual(row["radar_set_id"], 1)
        self.assertEqual(float(row["cocorahs_min_in"]), 1.00)
        self.assertEqual((float(row["band_1_mi"]), float(row["band_2_mi"]), float(row["band_3_mi"])), (1.0, 3.0, 5.0))
        self.assertIsNone(row["created_by"])

    def test_the_seeded_set_has_every_layer_by_every_band_as_designed(self):
        rows = self.conn.execute("SELECT layer, band, level FROM likelihood_level_rules WHERE set_id = %s",
                                 (self.set_id,)).fetchall()
        got = {(r["layer"], r["band"]): r["level"] for r in rows}
        self.assertEqual(len(rows), 20)
        want = {(layer, band): SEEDED[layer][i] for layer in LAYERS for i, band in enumerate(BANDS)}
        self.assertEqual(got, want)

    # -- append-only ------------------------------------------------------------------------

    def test_a_parameter_set_cannot_be_updated_or_deleted(self):
        self.refused("UPDATE likelihood_parameter_sets SET name = 'changed' WHERE set_id = %s", (self.set_id,))
        self.refused("DELETE FROM likelihood_parameter_sets WHERE set_id = %s", (self.set_id,))

    def test_a_rule_cannot_be_updated_or_deleted(self):
        self.refused("UPDATE likelihood_level_rules SET level = 'none' WHERE set_id = %s", (self.set_id,))
        self.refused("DELETE FROM likelihood_level_rules WHERE set_id = %s", (self.set_id,))

    def test_a_new_set_and_its_rules_can_be_added(self):
        new = self.new_set().fetchone()["set_id"]
        self.conn.execute("INSERT INTO likelihood_level_rules (set_id, layer, band, level) "
                          "VALUES (%s, 'core', 'le_1', 'agree')", (new,))
        self.assertGreater(new, self.set_id)

    # -- parameter sets ---------------------------------------------------------------------

    def test_a_set_needs_a_real_radar_set(self):
        self.refused("INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, band_1_mi, "
                     "band_2_mi, band_3_mi) VALUES ('x', 999999, 1.0, 1, 3, 5)")

    def test_the_cocorahs_size_floor_must_be_in_range(self):
        for bad in (0.49, 4.01, -1):
            with self.subTest(bad=bad):
                self.refused("INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, "
                             "band_1_mi, band_2_mi, band_3_mi) VALUES ('x', %s, %s, 1, 3, 5)",
                             (self.radar_set, bad))

    def test_the_band_edges_must_be_positive_ordered_and_within_fifty(self):
        for edges in ((0, 3, 5), (3, 3, 5), (1, 5, 3), (5, 3, 1), (1, 3, 50.1), (-1, 3, 5)):
            with self.subTest(edges=edges):
                self.refused("INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, "
                             "band_1_mi, band_2_mi, band_3_mi) VALUES ('x', %s, 1.0, %s, %s, %s)",
                             (self.radar_set, *edges))

    def test_the_name_must_not_be_empty_or_over_a_hundred_characters(self):
        for name in ("", "x" * 101):
            with self.subTest(length=len(name)):
                self.refused("INSERT INTO likelihood_parameter_sets (name, radar_set_id, cocorahs_min_in, "
                             "band_1_mi, band_2_mi, band_3_mi) VALUES (%s, %s, 1.0, 1, 3, 5)",
                             (name, self.radar_set))

    def test_edges_of_one_three_and_fifty_are_accepted(self):
        self.new_set(band_1_mi=0.5, band_2_mi=3.0, band_3_mi=50.0)

    # -- rules ------------------------------------------------------------------------------

    def test_a_rule_has_a_known_layer_band_and_level(self):
        for layer, band, level in (("hail", "le_1", "agree"), ("core", "le_2", "agree"), ("core", "le_1", "maybe")):
            with self.subTest(layer=layer, band=band, level=level):
                new = self.new_set().fetchone()["set_id"]
                self.refused("INSERT INTO likelihood_level_rules (set_id, layer, band, level) VALUES (%s, %s, %s, %s)",
                             (new, layer, band, level))

    def test_one_rule_per_set_layer_and_band(self):
        self.refused("INSERT INTO likelihood_level_rules (set_id, layer, band, level) "
                     "VALUES (%s, 'core', 'le_1', 'agree')", (self.set_id,))

    def test_a_rule_needs_a_real_set(self):
        self.refused("INSERT INTO likelihood_level_rules (set_id, layer, band, level) "
                     "VALUES (999999, 'core', 'le_1', 'agree')")

    # -- builds -----------------------------------------------------------------------------

    def test_a_build_is_accepted_and_built_at_defaults(self):
        self.build()
        row = self.conn.execute("SELECT * FROM likelihood_builds WHERE day = %s AND set_id = %s",
                                (self.day, self.set_id)).fetchone()
        self.assertIsNotNone(row["built_at"])
        self.assertIsNone(row["built_by"])

    def test_one_build_per_day_and_set(self):
        self.build()
        self.refused("INSERT INTO likelihood_builds (day, set_id, window_start, window_end, iem_reports, "
                     "cocorahs_reports, duplicates_dropped, patches, patches_anchored) "
                     "VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0)", (self.day, self.set_id, START, END))

    def test_a_build_needs_a_loaded_radar_day_row_and_a_real_set(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.build(day="1999-01-01")
        self.conn.rollback()
        self.setUp()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.build(set_id=999999)

    def test_the_window_must_be_ordered(self):
        import psycopg
        for start, end in ((END, START), (START, START)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.build(window_start=start, window_end=end)
                self.conn.rollback()
                self.setUp()

    def test_build_counts_cannot_be_negative(self):
        import psycopg
        for col in ("iem_reports", "cocorahs_reports", "duplicates_dropped", "patches"):
            with self.subTest(col=col):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.build(**{col: -1})
                self.conn.rollback()
                self.setUp()

    def test_anchored_patches_cannot_exceed_patches(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.build(patches=2, patches_anchored=3)

    def test_a_build_with_nothing_in_it_is_allowed(self):
        self.build(iem_reports=0, cocorahs_reports=0, duplicates_dropped=0, patches=0, patches_anchored=0)

    # -- zones ------------------------------------------------------------------------------

    def test_a_zone_needs_its_build(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.zone()

    def test_the_three_stored_levels_are_accepted_and_none_is_not_one(self):
        import psycopg
        self.build()
        for level in ("agree", "one_source", "context"):
            self.zone(level=level)
        n = self.conn.execute("SELECT count(*) AS n FROM likelihood_zones WHERE day = %s AND set_id = %s",
                              (self.day, self.set_id)).fetchone()["n"]
        self.assertEqual(n, 3)
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.zone(level="none")

    def test_one_zone_per_level_per_build(self):
        import psycopg
        self.build()
        self.zone()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.zone()

    def test_an_invalid_geometry_is_refused(self):
        import psycopg
        self.build()
        bow_tie = "MULTIPOLYGON(((0 0, 1 1, 1 0, 0 1, 0 0)))"
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.zone(wkt=bow_tie)

    def test_the_area_must_be_positive(self):
        import psycopg
        self.build()
        for area in (0, -1):
            with self.subTest(area=area):
                with self.assertRaises(psycopg.errors.CheckViolation):
                    self.zone(area=area)
                self.conn.rollback()
                self.setUp()
                self.build()

    def test_a_zone_must_be_a_multipolygon(self):
        import psycopg
        self.build()
        with self.assertRaises(psycopg.errors.InvalidParameterValue):      # the column type refuses it
            self.zone(wkt="POINT(-105 40)")

    # -- grants -----------------------------------------------------------------------------

    def priv(self, role, table, priv):
        return self.conn.execute("SELECT has_table_privilege(%s, %s, %s) AS ok", (role, table, priv)).fetchone()["ok"]

    def test_both_roles_read_all_four_tables(self):
        for role in ("hail_app", "hail_ingest"):
            for table in ("likelihood_parameter_sets", "likelihood_level_rules", "likelihood_builds",
                          "likelihood_zones"):
                self.assertTrue(self.priv(role, table, "SELECT"), f"{role} {table}")

    def test_both_roles_insert_and_delete_builds_and_zones_but_never_update_them(self):
        for role in ("hail_app", "hail_ingest"):
            for table in ("likelihood_builds", "likelihood_zones"):
                for p in ("INSERT", "DELETE"):
                    self.assertTrue(self.priv(role, table, p), f"{role} {table} {p}")
                self.assertFalse(self.priv(role, table, "UPDATE"), f"{role} {table} UPDATE")
                self.assertFalse(self.priv(role, table, "TRUNCATE"), f"{role} {table} TRUNCATE")

    def test_neither_role_can_write_the_sets_or_the_rules(self):
        for role in ("hail_app", "hail_ingest"):
            for table in ("likelihood_parameter_sets", "likelihood_level_rules"):
                for p in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    self.assertFalse(self.priv(role, table, p), f"{role} {table} {p}")


if __name__ == "__main__":
    unittest.main()
