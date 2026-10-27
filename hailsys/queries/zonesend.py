"""Which listing agents get a zone email for storm days a person selected.

Phase 5c step 4, piece 3a.  The zone twin of sendlist.py.  The matches here
rest on the hail zones (storm_listing_matches rows with no iem_id), not on a
National Weather Service report.  The eligibility rules are sendlist's, applied
in the same order by sendlist.select.  This module gathers the hits and groups
the events.  Two things differ from the report path:
    * A house already emailed for the same storm day by ANY send, report, or zone,
      is dropped.
    * A match whose wording is not approved yet (basis observer or both, while
      settings.zone_observer_wording_approved is false) is left out under its own
      reason, so the count says why.  The database refuses such a send anyway (sql/060).

One parameter set per list, because the radar sentence's numbers come from the set.
**Read Only** - nothing is sent.
"""

from datetime import timedelta

from hailsys.queries import sendlist
from hailsys.queries.sendlist import _house_day, _rank
from hailsys.tuning import DISPLAY_TZ

LEVELS = ("agree", "one_source")
DEFAULT_LEVELS = ("agree",)
OBSERVER_BASES = ("observer", "both")
BLOCKED = "observer_wording_not_approved"

# Newest set unless one is named, with the radar numbers its sentence will use.
_SET_SQL = """
SELECT s.set_id, s.name, r.hail_threshold_in AS hail_in, r.report_radius_km AS radius_km
FROM likelihood_parameter_sets s
JOIN radar_parameter_sets r ON r.set_id = s.radar_set_id
WHERE %(set_id)s::bigint IS NULL OR s.set_id = %(set_id)s
ORDER BY s.set_id DESC
LIMIT 1
"""

_APPROVED_SQL = "SELECT zone_observer_wording_approved AS approved FROM settings WHERE id = 1"

# The storm day stands in for the report time, at local noon, so sendlist's Denver-day rules
# read it as that day.  There is no size and no distance.
_HITS_SQL = """
SELECT m.match_id, m.listing_id, l.realtor_id, l.list_status, l.list_last_seen,
    p.property_address AS address,
    p.address_key,
    (m.storm_date + time '12:00') AT TIME ZONE %(tz)s AS utc_datetime,
    NULL::numeric AS magnitude, NULL::numeric AS distance_miles,
    m.basis, m.level, m.set_id,
    r.agent_name, r.email_norm AS email,
    EXISTS (SELECT 1 FROM send_log s
            WHERE s.match_id = m.match_id AND s.send_status <> 'failed')
        AS already_sent,
    EXISTS (SELECT 1 FROM dnc_list d
            WHERE d.email_norm = r.email_norm AND d.removed_at IS NULL)
        AS on_dnc,
    EXISTS (SELECT 1 FROM sent_emails e
              JOIN send_log s ON s.email_id = e.email_id
            WHERE e.realtor_id = r.realtor_id AND s.send_status <> 'failed'
              AND e.created_at >= %(since)s)
        AS in_cap,
    EXISTS (SELECT 1 FROM properties p2
        JOIN listings l2 ON l2.rentcast_id = p2.rentcast_id
        JOIN storm_listing_matches m2 ON m2.listing_id = l2.listing_id
        JOIN send_log s2 ON s2.match_id = m2.match_id
      WHERE p.address_key IS NOT NULL AND p2.address_key = p.address_key
        AND s2.send_status <> 'failed' AND m2.storm_date = m.storm_date)
        AS key_emailed
FROM storm_listing_matches m
JOIN listings l     ON l.listing_id = m.listing_id
JOIN properties p   ON p.rentcast_id = l.rentcast_id
LEFT JOIN realtors r ON r.realtor_id = l.realtor_id
WHERE m.iem_id IS NULL
    AND m.set_id = %(set_id)s
    AND m.storm_date = ANY(%(days)s)
    AND m.level = ANY(%(levels)s)
ORDER BY m.match_id
"""


def collapse_zone(hits):
    """One event per (house, Denver Day), by time.

    A house carried by several listing rows shows the address of the best ranked one.  Each
    match keeps its own basis, level and set in 'matches':  the send record must say what its
    own match says.  'bases' is what the email as a whole rests on.
    """
    events = {}
    for h in hits:
        key = _house_day(h)
        e = events.get(key)
        if e is None:
            e = events[key] = {"listing_id": h["listing_id"], "address": h["address"],
                               "utc_datetime": h["utc_datetime"], "match_ids": [],
                               "matches": [], "bases": set(), "rank": _rank(h)}
        elif _rank(h) < e["rank"]:
            e["rank"] = _rank(h)
            e["listing_id"] = h["listing_id"]
            e["address"] = h["address"]
        e["match_ids"].append(h["match_id"])
        e["matches"].append({"match_id": h["match_id"], "basis": h["basis"],
                             "level": h["level"], "set_id": h["set_id"]})
        e["bases"].add(h["basis"])
    for e in events.values():
        del e["rank"]
        e["bases"] = sorted(e["bases"])
    return sorted(events.values(), key=lambda e: (e["utc_datetime"], e["listing_id"]))


def build_zone_send_list(conn, storm_days, *, now, set_id=None, levels=DEFAULT_LEVELS,
                         max_age_days=sendlist.MAX_AGE_DAYS, cap_days=sendlist.CAP_DAYS,
                         freshness_days=sendlist.FRESH_DAYS):
    """Realtors to email for the ticked Denver days from the hail zones, and why some were dropped.

    Same shape as sendlist.build_send_list, plus 'set' (the set used and the radar numbers its
    sentence needs), 'levels', and one more excluded reason, BLOCKED.
    """
    levels = list(levels)
    if not levels or set(levels) - set(LEVELS):
        raise ValueError(f"levels must be some of {LEVELS}, not {levels}")
    chosen = conn.execute(_SET_SQL, {"set_id": set_id}).fetchone()
    if chosen is None:
        raise ValueError("there is no such hail-zone parameter set")
    since = None if cap_days is None else now - timedelta(days=cap_days)
    hits = conn.execute(_HITS_SQL, {"days": list(storm_days), "tz": DISPLAY_TZ.key,
                                    "since": since, "set_id": chosen["set_id"],
                                    "levels": levels}).fetchall()
    approved = conn.execute(_APPROVED_SQL).fetchone()["approved"]
    blocked = [h for h in hits if not approved and h["basis"] in OBSERVER_BASES]
    open_hits = [h for h in hits if approved or h["basis"] not in OBSERVER_BASES]

    out = sendlist.select(open_hits, today=now.astimezone(DISPLAY_TZ).date(),
                          max_age_days=max_age_days, freshness_days=freshness_days,
                          collapse_fn=collapse_zone)
    out["considered"] = {"matches": len(hits),
                         "realtors": len({h["realtor_id"] for h in hits
                                          if h["realtor_id"] is not None})}
    out["excluded"][BLOCKED] = {"matches": len(blocked),
                                "realtors": len({h["realtor_id"] for h in blocked
                                                 if h["realtor_id"] is not None})}
    out["set"] = {"set_id": chosen["set_id"], "name": chosen["name"],
                  "hail_in": chosen["hail_in"], "radius_km": chosen["radius_km"]}
    out["levels"] = levels
    return out
