"""Parsed CoCoRaHS reports into the database.

Idempotent:  a job that reads the last three days every night sees the same reports again,
and that must not look like three days of edits.  A report is new, changed (the station edited
it, a size, a note, anything) or unchanged, and an unchanged report isn't written at all, so its
updated_at and load_id stay where they were.  A report that later fetches no longer return is never
deleted.

The caller owns the transaction, nothing here commits.  One load per window is the natural unit,
so commit after each store_window.
"""

import logging

logger = logging.getLogger(__name__)

_START_RUN_SQL = """
INSERT INTO cocorahs_runs (run_mode) VALUES (%(mode)s) RETURNING run_id
"""

_FINISH_RUN_SQL = """
UPDATE cocorahs_runs
    SET run_status = %(status)s, finished_at = now(), loads = %(loads)s,
        reports_seen = %(seen)s, reports_new = %(new)s, reports_changed = %(changed)s,
        reports_rejected = %(rejected)s, error_detail = %(error)s
WHERE run_id = %(run_id)s
"""

_START_LOAD_SQL = """
INSERT INTO cocorahs_loads
    (run_id, state, date_type, window_start, window_end,
     rows_seen, rows_new, rows_changed, rows_rejected)
VALUES
    (%(run_id)s, %(state)s, %(date_type)s, %(start)s, %(end)s, 0, 0, 0, 0)
RETURNING load_id
"""

_FINISH_LOAD_SQL = """
UPDATE cocorahs_loads
    SET rows_seen = %(seen)s, rows_new = %(new)s, rows_changed = %(changed)s,
    rows_rejected = %(rejected)s
WHERE load_id = %(load_id)s
"""


# The fields a station can edit.  A report is "changed" only if one of these differs, compared in the database
# after the column types have rounded the new values (the table keeps 6 decimals of position), so a coordinate
# that rounds is not an edit.  RETURNING gives a row for an insert (xmax = 0) or an update, and none for a 
# report that did not change.
_FIELDS = ("station_name", "latitude", "longitude", "smallest_in", "average_in", "largest_in",
           "duration_minutes", "stone_consistency", "damage", "entered_at", "source_updated_at", "raw_row")

_SAVE_SQL = f"""
INSERT INTO cocorahs_hail_reports
    (station_number, observed_at, {", ".join(_FIELDS)}, load_id)
VALUES
    (%(station_number)s, %(observed_at)s, {", ".join(f"%({f})s" for f in _FIELDS)}, %(load_id)s)
ON CONFLICT (station_number, observed_at) DO UPDATE SET
    {", ".join(f"{f} = EXCLUDED.{f}" for f in _FIELDS)},
    load_id = EXCLUDED.load_id, updated_at = now()
WHERE ({", ".join(f"cocorahs_hail_reports.{f}" for f in _FIELDS)})
    IS DISTINCT FROM ({", ".join(f"EXCLUDED.{f}" for f in _FIELDS)})
RETURNING (xmax = 0) AS inserted
"""


def start_run(conn, mode):
    return conn.execute(_START_RUN_SQL, {"mode": mode}).fetchone()["run_id"]


def finish_run(conn, run_id, *, status, loads, seen, new, changed, rejected, error=None):
    conn.execute(_FINISH_RUN_SQL, {"run_id": run_id, "status": status, "loads": loads, "seen": seen,
                                   "new": new, "changed": changed, "rejected": rejected, "error": error})


def start_load(conn, run_id, state, date_type, start, end):
    """Open a load at zero counts:  reports point at it, and the counts are known only afterwards."""
    return conn.execute(_START_LOAD_SQL, {"run_id": run_id, "state": state, "date_type": date_type,
                                          "start": start, "end": end}).fetchone()["load_id"]


def finish_load(conn, load_id, *, seen, new, changed, rejected):
    conn.execute(_FINISH_LOAD_SQL, {"load_id": load_id, "seen": seen, "new": new, "changed": changed,
                                    "rejected": rejected})


def save_reports(conn, load_id, reports):
    """Write parsed reports one at a time, in order, so a later line for the same report wins.
    
    Returns {'new', 'changed', 'unchanged'}.
    """
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    for report in reports:
        row = conn.execute(_SAVE_SQL, dict(report, load_id=load_id)).fetchone()
        if row is None:
            counts["unchanged"] += 1
        elif row["inserted"]:
            counts["new"] += 1
        else:
            counts["changed"] += 1
    return counts


def store_window(conn, run_id, state, date_type, start, end, parsed):
    """One fetched window, start to finish:  open its load, log the rejects, save the reports, close the load.
    
    'parsed' is what parse.parse_hail returned.  Every reject is logged with its line number, reason and the line,
    and counted in the load.  Returns {'load_id', 'seen', 'new', 'changed', 'unchanged', 'rejected'}.
    """
    load_id = start_load(conn, run_id, state, date_type, start, end)
    for rej in parsed.rejected:
        logger.warning("CoCoRaHS %s %s %s to %s: rejected line %s (%s): %s",
                       state, date_type, start, end, rej["line"], rej["reason"], rej["raw"])
    counts = save_reports(conn, load_id, parsed.reports)
    seen, rejected = len(parsed.reports), len(parsed.rejected)
    finish_load(conn, load_id, seen=seen, new=counts["new"], changed=counts["changed"], rejected=rejected)
    return {"load_id": load_id, "seen": seen, "rejected": rejected, **counts}
