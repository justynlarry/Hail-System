"""Which Listing Agents get an email for storm days a User selected.

SQL only gathers hail match on selected Denver Days, with the facts
the rules need attached as flags.  Rules are applied here, in a fixed
order, so each hit is counted under the first rule that drops it and 
the send screen can say why a count shrank:

  * too_old             Storm day is more than max_age_days ago
  * inactive_listing    Listing is inactive
  * stale_listing       RentCast has not seen the listing within freshness_days
  * no_agent_email      Listing has no realtor/email
  * already_emailed     Match already has a send_log row success
  * on_dnc_list         Realtor's address is on dnc_list
  * within_cap          Realtor was emailed less than cap_days ago (14)

  Remaining records are collapsed to one event per listing per Denver Day,
  largest hail, nearest distance, earliest time.  Half of all current
  listing days have several reports.  Each event keeps a match_id it 
  covers, so each match gets its own send_log row.  
  
  **Read Only** - Nothing is sent.

  Warnings:  Postgres sorts NaN above every number, so the maximum hail size
  is taken here, and anything that is not finite is skipped.  Denver time, not
  UTC.
  Failed sends don't count as emailed, so it can be retried.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from hailsys.tuning import DISPLAY_TZ

MAX_AGE_DAYS = 30
CAP_DAYS = 14
FRESH_DAYS = 7

REASONS = ("too_old", "inactive_listing", "stale_listing", "no_agent_email",
           "already_emailed", "on_dnc_list", "within_cap")

_HITS_SQL = """
SELECT m.match_id, m.listing_id, l.realtor_id, l.list_status, l.list_last_seen,
    p.property_address AS address,
    i.utc_datetime, i.magnitude, m.distance_miles,
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
        AS in_cap
FROM storm_listing_matches m
JOIN iem_data i     ON i.iem_id = m.iem_id
JOIN listings l     ON l.listing_id = m.listing_id
JOIN properties p   ON p.rentcast_id = l.rentcast_id
LEFT JOIN realtors r ON r.realtor_id = l.realtor_id
WHERE i.report_text = 'HAIL'
  AND (i.utc_datetime AT TIME ZONE %(tz)s)::date = ANY(%(days)s)
ORDER BY m.match_id
"""


def _day(hit):
    return hit["utc_datetime"].astimezone(DISPLAY_TZ).date()


def _finite(value):
    return value is not None and Decimal(str(value)).is_finite()


def _reason(hit, oldest_day, freshest_day):
    """The first rule that drops this hit, or None."""
    if _day(hit) < oldest_day:
        return "too_old"
    if hit["list_status"] != "Active":
        return "inactive_listing"
    seen = hit["list_last_seen"]
    if seen is None or seen.astimezone(DISPLAY_TZ).date() < freshest_day:
        return "stale_listing"
    if hit["realtor_id"] is None or not hit["email"]:
        return "no_agent_email"
    if hit["already_sent"]:
        return "already_emailed"
    if hit["on_dnc"]:
        return "on_dnc_list"
    if hit["in_cap"]:
        return "within_cap"
    return None


def collapse(hits):
    """One event per (listing, Denver day), sorted by time."""
    events = {}
    for h in hits:
        key = (h["listing_id"], _day(h))
        e = events.get(key)
        if e is None:
            e = events[key] = {"listing_id": h["listing_id"], "address": h["address"],
                               "utc_datetime": h["utc_datetime"], "magnitude": None,
                               "distance_miles": h["distance_miles"], "match_ids": []}
        e["match_ids"].append(h["match_id"])
        if h["utc_datetime"] < e["utc_datetime"]:
            e["utc_datetime"] = h["utc_datetime"]
        if h["distance_miles"] < e["distance_miles"]:
            e["distance_miles"] = h["distance_miles"]
        if _finite(h["magnitude"]) and (e["magnitude"] is None
                                        or h["magnitude"] > e["magnitude"]):
            e["magnitude"] = h["magnitude"]
    return sorted(events.values(), key=lambda e: (e["utc_datetime"], e["listing_id"]))


def select(hits, *, today, max_age_days=MAX_AGE_DAYS, freshness_days=FRESH_DAYS):
    """Apply the rules to the gathered hits."""
    oldest_day = today - timedelta(days=max_age_days)
    freshest_day = today - timedelta(days=freshness_days)
    dropped = {r: {"matches": 0, "realtors": set()} for r in REASONS}
    kept = defaultdict(list)
    for h in hits:
        reason = _reason(h, oldest_day, freshest_day)
        if reason is None:
            kept[h["realtor_id"]].append(h)
            continue
        dropped[reason]["matches"] += 1
        if h["realtor_id"] is not None:
            dropped[reason]["realtors"].add(h["realtor_id"])

    realtors = []
    for realtor_id in sorted(kept):
        rows = kept[realtor_id]
        realtors.append({"realtor_id": realtor_id, "agent_name": rows[0]["agent_name"],
                         "email": rows[0]["email"], "events": collapse(rows)})
    return {
        "realtors": realtors,
        "considered": {"matches": len(hits),
                       "realtors": len({h["realtor_id"] for h in hits
                                        if h["realtor_id"] is not None})},
        "excluded": {r: {"matches": d["matches"], "realtors": len(d["realtors"])}
                     for r, d in dropped.items()},
    }
def build_send_list(conn, storm_days, *, now, max_age_days=MAX_AGE_DAYS,
                    cap_days=CAP_DAYS, freshness_days=FRESH_DAYS):
    """Realtors to email for the ticked Denver days, and why some may have been dropped."""
    since = None if cap_days is None else now - timedelta(days=cap_days)
    hits = conn.execute(_HITS_SQL, {"days": list(storm_days), "tz": DISPLAY_TZ.key,
                                    "since": since}).fetchall()
    return select(hits, today=now.astimezone(DISPLAY_TZ).date(),
                  max_age_days=max_age_days, freshness_days=freshness_days)