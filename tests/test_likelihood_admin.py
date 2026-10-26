"""Tests for hailsys/queries/likelihoodadmin.py -- the admin page's parameter-set reads and writes (slice 7).

Needs a role that can insert the set tables (hail_admin);  everything runs in a transaction that is always rolled back
(save leaves the commit to its caller, so nothing is ever kept):

    PW=$(grep '^POSTGIS_ROOT_PASSWORD=' .env | cut -d= -f2-)
    docker compose run --rm -T --no-deps -e PGHOST=postgis -e PGUSER=hail_admin -e PGPASSWORD="$PW" \
        -v ./tests:/app/tests:ro web python -m unittest tests.test_likelihood_admin

What they pin:  every number is checked (not a number, NaN and Infinity, range, decimal places, distances that do not
grow), the name and all 20 levels are required;  a save that changes nothing writes nothing;  a changed number or rule adds
a likelihood set with its 20 rules, and a radar set only when a radar number changed;  the newest set is the one in use and
saving old numbers again is how you go back;  the unbuilt-day counts follow the newest sets;  storm states are tidied and
logged.
"""

import os
import unittest

from hailsys.queries import likelihoodadmin as la


@unittest.skipUnless(os.environ.get("PGHOST"), "no database (PGHOST unset)")
class LikelihoodAdminTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.conn = psycopg.connect(row_factory=dict_row)
        self.addCleanup(self.conn.close)
        self.addCleanup(self.conn.rollback)
        if not self.conn.execute("SELECT has_table_privilege('likelihood_parameter_sets', 'INSERT') AS ok").fetchone()["ok"]:
            self.skipTest("needs a role that can insert parameter sets (hail_admin)")
        self.emp_id = self.conn.execute("SELECT min(emp_id) AS e FROM users").fetchone()["e"]
        self.current = la.in_use(self.conn)

    def values(self, **changes):
        """The form for the set in use, with these changes."""
        c = self.current
        out = {key: c[key] for key in la.RADAR_KEYS + la.LIKELIHOOD_KEYS}
        out.update(name="test change", rules=dict(c["rules"]))
        out.update(changes)
        return out

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]

    # -- checking values --------------------------------------------------------------------

    def test_the_values_of_the_set_in_use_are_accepted(self):
        out = la.clean(self.values())
        self.assertEqual(len(out["rules"]), 20)
        self.assertEqual(out["name"], "test change")

    def test_a_number_that_is_not_a_number_is_refused(self):
        for bad in ("abc", "", None, "1,5", "NaN", "Infinity", "-Infinity"):
            for key in la.RADAR_KEYS + la.LIKELIHOOD_KEYS:
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(la.SetAdminError):
                        la.clean(self.values(**{key: bad}))

    def test_each_number_has_its_own_range(self):
        cases = {"hail_threshold_in": ("0.49", "4.01"), "margin_km": ("-0.1", "50.1"),
                 "report_radius_km": ("-1", "51"), "cocorahs_min_in": ("0.49", "4.01")}
        for key, bad in cases.items():
            for value in bad:
                with self.subTest(key=key, value=value):
                    with self.assertRaises(la.SetAdminError):
                        la.clean(self.values(**{key: value}))

    def test_the_edges_of_the_ranges_are_accepted(self):
        la.clean(self.values(hail_threshold_in="0.50", margin_km="0", report_radius_km="50",
                             cocorahs_min_in="4.00"))

    def test_too_many_decimal_places_are_refused_not_rounded(self):
        for key, value in (("hail_threshold_in", "1.005"), ("margin_km", "3.05"), ("band_1_mi", "1.25")):
            with self.subTest(key=key):
                with self.assertRaises(la.SetAdminError):
                    la.clean(self.values(**{key: value}))

    def test_a_huge_number_is_refused_and_does_not_raise_anything_else(self):
        with self.assertRaises(la.SetAdminError):
            la.clean(self.values(margin_km="1E+30"))

    def test_the_distances_must_grow(self):
        for b in ((3, 3, 5), (4, 3, 5), (1, 3, 3), (0, 3, 5), (1, 3, 2)):
            with self.subTest(b=b):
                with self.assertRaises(la.SetAdminError):
                    la.clean(self.values(band_1_mi=b[0], band_2_mi=b[1], band_3_mi=b[2]))

    def test_the_name_is_required_and_limited(self):
        for bad in ("", "   ", None, "x" * 101):
            with self.subTest(bad=bad):
                with self.assertRaises(la.SetAdminError):
                    la.clean(self.values(name=bad))
        la.clean(self.values(name="x" * 100))

    def test_every_one_of_the_twenty_levels_is_needed_and_known(self):
        for layer in la.LAYERS:
            for band in la.BANDS:
                with self.subTest(layer=layer, band=band):
                    missing = dict(self.current["rules"])
                    del missing[(layer, band)]
                    with self.assertRaises(la.SetAdminError):
                        la.clean(self.values(rules=missing))
        unknown = dict(self.current["rules"])
        unknown[("core", "le_1")] = "certain"
        with self.assertRaises(la.SetAdminError):
            la.clean(self.values(rules=unknown))
        with self.assertRaises(la.SetAdminError):
            la.clean(self.values(rules={}))

    # -- saving -----------------------------------------------------------------------------

    def test_a_save_that_changes_nothing_writes_nothing(self):
        before = (self.count("likelihood_parameter_sets"), self.count("likelihood_level_rules"),
                  self.count("radar_parameter_sets"))
        with self.assertRaises(la.SetAdminError) as ctx:
            la.save(self.conn, self.emp_id, self.values())
        self.assertIn("Nothing changed", str(ctx.exception))
        after = (self.count("likelihood_parameter_sets"), self.count("likelihood_level_rules"),
                 self.count("radar_parameter_sets"))
        self.assertEqual(before, after)

    def test_a_refused_value_writes_nothing(self):
        before = self.count("likelihood_parameter_sets")
        with self.assertRaises(la.SetAdminError):
            la.save(self.conn, self.emp_id, self.values(band_2_mi="9999"))
        self.assertEqual(self.count("likelihood_parameter_sets"), before)

    def test_a_changed_distance_adds_one_likelihood_set_and_no_radar_set(self):
        radar_before = self.count("radar_parameter_sets")
        old = self.current
        result = la.save(self.conn, self.emp_id, self.values(band_2_mi="4.0"))
        self.assertFalse(result["radar_changed"])
        self.assertEqual(self.count("radar_parameter_sets"), radar_before)
        new = la.in_use(self.conn)
        self.assertEqual(new["set_id"], result["set_id"])
        self.assertGreater(new["set_id"], old["set_id"])
        self.assertEqual(float(new["band_2_mi"]), 4.0)
        self.assertEqual(new["radar_set_id"], old["radar_set_id"])
        self.assertEqual(len(new["rules"]), 20)
        self.assertEqual(new["rules"], old["rules"])
        self.assertEqual(new["name"], "test change")
        self.assertEqual(new["created_by_name"],
                         self.conn.execute("SELECT user_name FROM users WHERE emp_id = %s",
                                           (self.emp_id,)).fetchone()["user_name"])

    def test_a_changed_rule_alone_adds_a_set_with_that_one_rule_different(self):
        old = self.current
        rules = dict(old["rules"])
        rules[("margin", "3_5")] = "agree"
        la.save(self.conn, self.emp_id, self.values(rules=rules))
        new = la.in_use(self.conn)
        self.assertNotEqual(new["set_id"], old["set_id"])
        differing = {k for k in rules if new["rules"][k] != old["rules"][k]}
        self.assertEqual(differing, {("margin", "3_5")})

    def test_a_changed_radar_number_adds_a_radar_set_and_points_the_new_set_at_it(self):
        radar_before = self.count("radar_parameter_sets")
        result = la.save(self.conn, self.emp_id, self.values(hail_threshold_in="1.25"))
        self.assertTrue(result["radar_changed"])
        self.assertEqual(self.count("radar_parameter_sets"), radar_before + 1)
        new = la.in_use(self.conn)
        self.assertEqual(new["radar_set_id"], result["radar_set_id"])
        self.assertEqual(float(new["hail_threshold_in"]), 1.25)
        # The other two radar numbers carried over.
        self.assertEqual(new["margin_km"], self.current["margin_km"])
        self.assertEqual(new["report_radius_km"], self.current["report_radius_km"])
        row = self.conn.execute("SELECT created_by FROM radar_parameter_sets WHERE set_id = %s",
                                (result["radar_set_id"],)).fetchone()
        self.assertEqual(row["created_by"], self.emp_id)

    def test_the_same_values_twice_are_saved_once(self):
        la.save(self.conn, self.emp_id, self.values(band_1_mi="0.5"))
        count = self.count("likelihood_parameter_sets")
        with self.assertRaises(la.SetAdminError):
            la.save(self.conn, self.emp_id, self.values(band_1_mi="0.5"))
        self.assertEqual(self.count("likelihood_parameter_sets"), count)

    def test_going_back_is_saving_the_old_numbers_again(self):
        old = self.current
        la.save(self.conn, self.emp_id, self.values(band_3_mi="6.0"))
        again = la.save(self.conn, self.emp_id, self.values(name="back to before"))
        new = la.in_use(self.conn)
        self.assertEqual(new["set_id"], again["set_id"])
        self.assertEqual(new["band_3_mi"], old["band_3_mi"])
        self.assertEqual(new["rules"], old["rules"])
        self.assertGreater(new["set_id"], old["set_id"])

    # -- reading ----------------------------------------------------------------------------

    def test_history_is_newest_first_with_the_newest_in_use(self):
        la.save(self.conn, self.emp_id, self.values(band_2_mi="4.0"))
        rows = la.history(self.conn)
        self.assertEqual([r["in_use"] for r in rows[:2]], [True, False])
        self.assertTrue(all(not r["in_use"] for r in rows[1:]))
        ids = [r["set_id"] for r in rows]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertTrue(all(len(r["rules"]) == 20 for r in rows))
        self.assertEqual(la.in_use(self.conn)["set_id"], rows[0]["set_id"])

    def test_history_limit_is_respected(self):
        la.save(self.conn, self.emp_id, self.values(band_2_mi="4.0"))
        self.assertEqual(len(la.history(self.conn, 1)), 1)

    def test_the_seeded_set_has_no_author(self):
        first = la.history(self.conn, 1000)[-1]
        self.assertIsNone(first["created_by_name"])

    def test_unbuilt_days_follow_the_newest_sets(self):
        loaded = self.conn.execute("SELECT count(*) AS n FROM radar_days WHERE status = 'loaded'").fetchone()["n"]
        before = la.unbuilt_days(self.conn)
        la.save(self.conn, self.emp_id, self.values(band_2_mi="4.0"))
        after = la.unbuilt_days(self.conn)
        self.assertEqual(after["likelihood"], loaded)           # no day has a build for the new set
        self.assertEqual(after["radar"], before["radar"])       # the radar set did not change
        la.save(self.conn, self.emp_id, self.values(band_2_mi="4.0", margin_km="4.0"))
        self.assertEqual(la.unbuilt_days(self.conn)["radar"], loaded)

    # -- trying a day -----------------------------------------------------------------------

    def a_day(self):
        day = la.suggest_day(self.conn)
        if day is None:
            self.skipTest("no day with an Agree zone in this database")
        return day

    def snapshot(self, day):
        """Everything a trial has to leave as it found it."""
        stored = self.conn.execute(
            "SELECT level, round(area_km2::numeric, 6) AS area FROM likelihood_zones "
            "WHERE day = %s AND set_id = %s ORDER BY level", (day, self.current["set_id"])).fetchall()
        return (self.count("radar_parameter_sets"), self.count("likelihood_parameter_sets"),
                self.count("likelihood_level_rules"), self.count("likelihood_builds"),
                self.count("likelihood_zones"), stored)

    def test_the_suggested_day_has_an_agree_zone(self):
        day = self.a_day()
        row = self.conn.execute("SELECT count(*) AS n FROM likelihood_zones WHERE day = %s AND level = 'agree'",
                                (day,)).fetchone()
        self.assertGreater(row["n"], 0)

    def test_a_trial_of_the_set_in_use_gives_the_same_on_both_sides(self):
        day = self.a_day()
        out = la.try_day(self.conn, self.emp_id, self.values(), day)
        self.assertFalse(out["changed"])
        self.assertEqual(out["current"]["areas"], out["proposed"]["areas"])
        self.assertEqual(out["day"], day)
        self.assertGreater(out["current"]["areas"]["agree"], 0)

    def test_a_trial_keeps_nothing(self):
        day = self.a_day()
        rules = dict(self.current["rules"])
        rules[("core", "over_5")] = "agree"
        before = self.snapshot(day)
        la.try_day(self.conn, self.emp_id, self.values(hail_threshold_in="1.50", band_2_mi="4.0", rules=rules), day)
        self.assertEqual(self.snapshot(day), before)

    def test_a_trial_leaves_the_connection_with_nothing_pending(self):
        import psycopg
        la.try_day(self.conn, self.emp_id, self.values(band_2_mi="4.0"), self.a_day())
        self.assertEqual(self.conn.info.transaction_status, psycopg.pq.TransactionStatus.IDLE)

    def test_making_every_level_agree_moves_everything_into_agree(self):
        day = self.a_day()
        rules = {key: "agree" for key in self.current["rules"]}
        out = la.try_day(self.conn, self.emp_id, self.values(rules=rules), day)
        self.assertTrue(out["changed"])
        self.assertEqual(out["proposed"]["areas"].get("one_source", 0), 0)
        self.assertEqual(out["proposed"]["areas"].get("context", 0), 0)
        self.assertGreater(out["proposed"]["areas"]["agree"], out["current"]["areas"]["agree"])

    def test_a_wider_distance_cannot_shrink_agree(self):
        # Agree is the near-a-report rule for core and margin;  widening the bands only adds reach.
        day = self.a_day()
        out = la.try_day(self.conn, self.emp_id,
                         self.values(band_1_mi="2.0", band_2_mi="6.0", band_3_mi="10.0"), day)
        self.assertGreaterEqual(out["proposed"]["areas"]["agree"] + 1e-6, out["current"]["areas"]["agree"])

    def test_a_radar_change_is_tried_without_keeping_the_radar_set(self):
        day = self.a_day()
        radar_before = self.count("radar_parameter_sets")
        out = la.try_day(self.conn, self.emp_id, self.values(hail_threshold_in="2.00"), day)
        self.assertTrue(out["changed"])
        self.assertNotEqual(out["proposed"]["set_id"], out["current"]["set_id"])
        self.assertEqual(self.count("radar_parameter_sets"), radar_before)
        # The radar layers move with the threshold.  (Not the patch count:  a higher threshold can split one
        # patch into several.)
        self.assertNotEqual(out["proposed"]["layer_areas"], out["current"]["layer_areas"])

    def test_a_trial_needs_no_name(self):
        la.try_day(self.conn, self.emp_id, self.values(name="", band_2_mi="4.0"), self.a_day())
        la.try_day(self.conn, self.emp_id, self.values(name=None, band_2_mi="4.0"), self.a_day())

    def test_a_day_without_a_radar_file_is_refused_and_leaves_nothing(self):
        from datetime import date
        before = self.snapshot(self.a_day())
        with self.assertRaises(la.SetAdminError) as ctx:
            la.try_day(self.conn, self.emp_id, self.values(band_2_mi="4.0"), date(1999, 1, 1))
        self.assertIn("no radar file", str(ctx.exception))
        self.assertEqual(self.snapshot(self.a_day()), before)

    def test_bad_values_are_refused_before_anything_is_built(self):
        with self.assertRaises(la.SetAdminError):
            la.try_day(self.conn, self.emp_id, self.values(margin_km="abc"), self.a_day())
        with self.assertRaises(la.SetAdminError):
            la.try_day(self.conn, self.emp_id, self.values(band_1_mi="5", band_2_mi="3"), self.a_day())

    # -- storm states -----------------------------------------------------------------------

    def test_storm_states_are_tidied_and_saved(self):
        self.assertEqual(la.save_storm_states(self.conn, self.emp_id, " wy, co,CO "), "CO,WY")
        self.assertEqual(la.storm_states(self.conn), "CO,WY")

    def test_bad_storm_states_are_refused(self):
        for bad in ("", "   ", "colorado", "C", "CO;WY", None):
            with self.subTest(bad=bad):
                with self.assertRaises(la.SetAdminError):
                    la.save_storm_states(self.conn, self.emp_id, bad)

    def test_a_storm_states_change_is_logged_to_the_settings_history(self):
        before = self.count("settings_history")
        la.save_storm_states(self.conn, self.emp_id, "CO,NE")
        self.assertEqual(self.count("settings_history"), before + 1)


if __name__ == "__main__":
    unittest.main()
