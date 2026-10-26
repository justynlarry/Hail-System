"""Admin-page reads and writes for the hail-zone parameter sets.

A set is never edited, saving adds a radar set (only when a radar number changed) and a
likelihood set with its 20 rules, in the caller's transaction.  The newest rows are the ones
in use.  Going back is saving the old numbers again, values are checked here with the rules
the tables' CHECKs hold, so a refusal reads as a message.  The database is the backstop
(sql/054, sql/056).  The caller commits, a refusal is a SetAdminError.  This does not build
zones, a rebuild is a command, and unbuilt_days says how many days wait for one.
"""

from decimal import Decimal

import psycopg

from hailsys.likelihood import build
from hailsys.radar import warnings
from hailsys.tuning import DISPLAY_TZ


LAYERS = ("core", "margin", "radar_indicated", "outer", "none")
BANDS = ("le_1", "1_3", "3_5", "over_5")
LEVELS = ("agree", "one_source", "context", "none")
MAX_NAME = 100
HISTORY_LIMIT = 20

RADAR_KEYS = ("hail_threshold_in", "margin_km", "report_radius_km")
LIKELIHOOD_KEYS = ("cocorahs_min_in", "band_1_mi", "band_2_mi", "band_3_mi")

# key: label, decimal places the column keeps, lowest and highest allowed (inclusive)
_NUMBERS = {
    "hail_threshold_in": ("Hail threshold", 2, "0.50", "4.00"),
    "margin_km": ("Margin", 1, "0", "50"),
    "report_radius_km": ("Report radius", 1, "0", "50"),
    "cocorahs_min_in": ("CoCoRaHS size floor", 2, "0.50", "4.00"),
    "band_1_mi": ("First distance", 1, "0", "50"),
    "band_2_mi": ("Second distance", 1, "0", "50"),
    "band_3_mi": ("Third distance", 1, "0", "50"),
}


class SetAdminError(Exception):
    """A value was refused, message explains."""


_HISTORY_SQL = """
SELECT s.set_id, s.name, s.created_at, u.user_name AS created_by_name, s.radar_set_id,
    r.hail_threshold_in, r.margin_km, r.report_radius_km,
    s.cocorahs_min_in, s.band_1_mi, s.band_2_mi, s.band_3_mi
FROM likelihood_parameter_sets s
JOIN radar_parameter_sets r ON r.set_id = s.radar_set_id
LEFT JOIN users u ON u.emp_id = s.created_by
ORDER BY s.set_id DESC
LIMIT %(limit)s
"""

_RULES_SQL = """
SELECT set_id, layer, band, level FROM likelihood_level_rules WHERE set_id = ANY (%(ids)s)
"""

_NEWEST_RADAR_SQL = """
SELECT set_id, hail_threshold_in, margin_km, report_radius_km
FROM radar_parameter_sets ORDER BY set_id DESC LIMIT 1
"""

_INSERT_RADAR_SQL = """
INSERT INTO radar_parameter_sets
    (name, hail_threshold_in, margin_km, report_radius_km, created_by)
VALUES (%(name)s, %(hail_threshold_in)s, %(margin_km)s, %(report_radius_km)s, %(emp_id)s)
RETURNING set_id
"""

_INSERT_SET_SQL = """
INSERT INTO likelihood_parameter_sets
    (name, radar_set_id, cocorahs_min_in, band_1_mi, band_2_mi, band_3_mi, created_by)
VALUES (%(name)s, %(radar_set_id)s, %(cocorahs_min_in)s, %(band_1_mi)s, %(band_2_mi)s,
        %(band_3_mi)s, %(emp_id)s)
RETURNING set_id
"""

_INSERT_RULE_SQL = """
INSERT INTO likelihood_level_rules (set_id, layer, band, level)
VALUES (%(set_id)s, %(layer)s, %(band)s, %(level)s)
"""

# Loaded radar days with no build for the newest set, for the radar zones and the likelihood.
_UNBUILT_SQL = """
SELECT
    (SELECT count(*) FROM radar_days d
     WHERE d.status = 'loaded' AND NOT EXISTS (
        SELECT 1 FROM radar_zone_builds b WHERE b.day = d.day
            AND b.set_id = (SELECT max(set_id) FROM radar_parameter_sets))) AS radar,
    (SELECT count(*) FROM radar_days d
        WHERE d.status = 'loaded' AND NOT EXISTS (
        SELECT 1 FROM likelihood_builds b WHERE b.day = d.day
            AND b.set_id = (SELECT max(set_id) FROM likelihood_parameter_sets))) AS likelihood
"""

# The newest day with an Agree zone:  a day worth trying a change on.
_SUGGEST_DAY_SQL = "SELECT max(day) AS day FROM likelihood_zones WHERE level = 'agree'"


_SET_STATES_SQL = "UPDATE settings SET storm_states = %(states)s WHERE id = 1"


def history(conn, limit=HISTORY_LIMIT):
    """The newest parameter sets first, each with its numbers, its 'rules' as
    {(layer, band): level}, and 'in_use' (true for the newest only)."""
    rows = conn.execute(_HISTORY_SQL, {"limit": limit}).fetchall()
    grids = {row["set_id"]: {} for row in rows}
    for rule in conn.execute(_RULES_SQL, {"ids": list(grids)}).fetchall():
        grids[rule["set_id"]][(rule["layer"], rule["band"])] = rule["level"]
    return [{**row, "rules": grids[row["set_id"]], "in_use": n == 0}
            for n, row in enumerate(rows)]


def in_use(conn):
    """The newest set, or None if there is none."""
    rows = history(conn, 1)
    return rows[0] if rows else None


def unbuilt_days(conn):
    """{'radar': n, 'likelihood': n}: loaded days the newest sets have not built yet."""
    return dict(conn.execute(_UNBUILT_SQL).fetchone())


def _number(key, text):
    label, places, low, high = _NUMBERS[key]
    try:
        value = Decimal(str(text).strip())
    except ArithmeticError:
        raise SetAdminError(f"{label} must be a number.") from None
    # Decimal accepts NaN and Infinity, and a NaN breaks every comparison below.
    if not value.is_finite():
        raise SetAdminError(f"{label} must be a number.")
    if not Decimal(low) <= value <= Decimal(high):
        raise SetAdminError(f"{label} must be from {low} to {high}.")
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise SetAdminError(f"{label} can have at most {places} decimal places.")
    return value


def clean(values):
    """Check a form's values and return them as Decimals, 'name' and 'rules'.

    values has the seven numbers by key, 'name', and 'rules' as {(layer, band): level}.
    """
    out = {key: _number(key, values.get(key)) for key in _NUMBERS}
    if not 0 < out["band_1_mi"] < out["band_2_mi"] < out["band_3_mi"]:
        raise SetAdminError("The distances must grow:  first above 0, then the second, "
                            "then the third, none above 50.")
    name = (values.get('name') or "").strip()
    if not name:
        raise SetAdminError("Give the change a name, for the history.")
    if len(name) > MAX_NAME:
        raise SetAdminError(f"The name is limited to {MAX_NAME} characters.")
    given = values.get("rules") or {}
    rules = {}
    for layer in LAYERS:
        for band in BANDS:
            level = given.get((layer, band))
            if level not in LEVELS:
                raise SetAdminError(f"Pick a level for {layer} at {band}.")
            rules[(layer, band)] = level
    out["name"] = name
    out["rules"] = rules
    return out



def _compare(current, radar, new):
    """(same_radar, unchanged):  do the radar numbers match the newest radar set, and does
    everything match the set in use
    """
    same_radar = all(radar[key] == new[key] for key in RADAR_KEYS)
    unchanged = (same_radar and current["radar_set_id"] == radar["set_id"]
                 and all(current[key] == new[key] for key in LIKELIHOOD_KEYS)
                 and current["rules"] == new["rules"])
    return same_radar, unchanged


def _write_sets(conn, emp_id, new, radar, same_radar):
    """Add the sets for these checked values and return (set_id, radar_set_id).

    The radar set is added only when a radar number differs from the newest one.
    """
    args = {**new, "emp_id": emp_id}
    try:
        # The 20-rule check is deferred, and a set's rules go in after the set.  An earlier
        # save in this transaction may have left it immediate, so say deferred again.
        conn.execute("SET CONSTRAINTS likelihood_sets_complete DEFERRED")
        if same_radar:
            args["radar_set_id"] = radar["set_id"]
        else:
            args["radar_set_id"] = conn.execute(_INSERT_RADAR_SQL, args).fetchone()["set_id"]
        set_id = conn.execute(_INSERT_SET_SQL, args).fetchone()["set_id"]
        for (layer, band), level in new["rules"].items():
            conn.execute(_INSERT_RULE_SQL, {"set_id": set_id, "layer": layer,
                                            "band": band, "level": level})
        # Run the deferred 20-rule check now, so a refusal is raised here and not at commit.
        conn.execute("SET CONSTRAINTS likelihood_sets_complete IMMEDIATE")
        conn.execute("SET CONSTRAINTS likelihood_sets_complete DEFERRED")
    except psycopg.errors.CheckViolation as exc:
        raise SetAdminError(f"Those values are not allowed: {exc.diag.message_primary}") from None
    return set_id, args["radar_set_id"]


def save(conn, emp_id, values):
    """Add the sets for these values and return {'set_id', 'radar_set_id', 'radar_changed'}.

    Nothing is written when the values equal the set in use.
    """
    new = clean(values)
    current = in_use(conn)
    if current is None:
        raise SetAdminError("There is no parameter set to compare with.")
    radar = conn.execute(_NEWEST_RADAR_SQL).fetchone()
    same_radar, unchanged = _compare(current, radar, new)
    if unchanged:
        raise SetAdminError("Nothing changed, so nothing was saved.")
    set_id, radar_set_id = _write_sets(conn, emp_id, new, radar, same_radar)
    return {"set_id": set_id, "radar_set_id": radar_set_id, "radar_changed": not same_radar}


def suggest_day(conn):
    """The newest day with an Agree zone, or None."""
    return conn.execute(_SUGGEST_DAY_SQL).fetchone()["day"]


def try_day(conn, emp_id, values, day):
    """Build one day under the set in use and under these values, and return both.

    The proposed sets are added only so the build can read them, and the day's stored build
    for the set in use is rebuilt only to compare.  Everything is rolled back, always.  The connection
    is left with nothing pending and nothing is kept.  A blank name is allowed.
    Returns {'day', 'changed', 'current', 'proposed'}, each side being build_day's counts
    with 'areas' (km2 per level) and 'layer_areas'.
    """
    new = clean({**values, "name": (values.get("name") or "").strip() or "trial"})
    current = in_use(conn)
    if current is None:
        raise SetAdminError("There is no parameter set to compare with.")
    radar = conn.execute(_NEWEST_RADAR_SQL).fetchone()
    same_radar, unchanged = _compare(current, radar, new)
    try:
        before = build.build_day(conn, day, DISPLAY_TZ, set_id=current["set_id"])
        if unchanged:
            after = before
        else:
            set_id, _ = _write_sets(conn, emp_id, new, radar, same_radar)
            after = build.build_day(conn, day, DISPLAY_TZ, set_id=set_id)
    except build.LikelihoodError as exc:
        raise SetAdminError(str(exc)) from None
    finally:
        conn.rollback()
    return {"day": day, "changed": not unchanged, "current": before, "proposed": after}


def storm_states(conn):
    """The states whose NWS warnings are fetched, as 'CO', or 'CO,WY'."""
    return warnings.current_states(conn)


def save_storm_states(conn, emp_id, text):
    """Set settings.storm_states (logged to settings_history by the trigger)."""
    try:
        states = warnings.normalize_states(text)
    except warnings.WarningError as exc:
        raise SetAdminError(str(exc)) from None
    conn.execute("SELECT set_config('app.current_emp_id', %s, true)", (str(emp_id),))
    conn.execute(_SET_STATES_SQL, {"states": states})
    return states
