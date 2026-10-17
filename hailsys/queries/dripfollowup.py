"""Who gets a drip follow-up, and which houses the email names.  Read-Only.

The matches are the send_log rows of the campaign's first batch, for recipients not yet
stopped.  build_send_list cannot be used here, its already_emailed rule would drop every
one of them, and its cap would drop anyone emailed inside the cap window, which a
short gap always is.

Rules, in a fixed order, so each match is counted under the first one that drops it:

  recipient_bounced     a step-1 email to this address bounced
  on_dnc_list           the address is on dnc_list now
  within_cap            emailed within cap_days by anything outside this campaign (the campaign's
                        own steps do not count)
  listing_dead_earlier  an earlier step found the listing inactive or not found, so it is not
                        checked again and stays out
  listing_inactive      this step's check says inactive (removed, not seen lately, or another agent)
  listing_not_found     this step's check says not found (or a zip pull did not return it)
  listing_unconfirmed   no check, or the check was an error:  nothing is sent on a guess

The storm-age rule (match_max_age_days) is not applied, a step never expires, and the
listing rules guard the content.  verdicts come only from drip_listing_checks, whichever
way the step re-checks.

to_stop lists recipients the job should stop, with the drip_recipients reason:  bounced,
unsubscribed, or no_active_listings (every one of their listings definitely dead, none
unconfirmed and not held back).  A recipient held back by the cap, or with an unconfirmed
listing is only skipped.
"""


from collections import defaultdict
from datetime import timedelta

from hailsys.queries import sendlist

CAP_DAYS = sendlist.CAP_DAYS

REASONS = ("recipient_bounced", "on_dnc_list", "within_cap", "listing_dead_earlier",
           "listing_inactive", "listing_not_found", "listing_unconfirmed")
_DEAD = {"listing_dead_earlier", "listing_inactive", "listing_not_found"}

_HITS_SQL = """
SELECT m.match_id, m.listing_id, l.rentcast_id, e.realtor_id, l.list_last_seen,
    p.property_address AS address, p.address_key,
    i.utc_datetime, i.magnitude, m.distance_miles,
    r.agent_name, lower(btrim(dr.recipient_email)) AS email, s.send_status,
    EXISTS (SELECT 1 FROM dnc_list d
            WHERE d.email_norm = lower(btrim(dr.recipient_email)) AND d.removed_at IS NULL)
        AS on_dnc,
    EXISTS (SELECT 1 FROM sent_emails e2
              JOIN send_log s2 ON s2.email_id = e2.email_id
            WHERE e2.realtor_id = e.realtor_id AND s2.send_status <> 'failed'
              AND e2.created_at >= %(since)s
              AND e2.batch_id <> c.first_batch_id
              AND NOT EXISTS (SELECT 1 FROM drip_steps ds
                              WHERE ds.campaign_id = c.campaign_id AND ds.batch_id = e2.batch_id))
        AS in_cap
FROM drip_campaigns c
JOIN sent_emails e           ON e.batch_id = c.first_batch_id
JOIN drip_recipients dr      ON dr.campaign_id = c.campaign_id AND dr.realtor_id = e.realtor_id
JOIN send_log s              ON s.email_id = e.email_id AND s.send_status <> 'failed'
JOIN storm_listing_matches m ON m.match_id = s.match_id
JOIN iem_data i              ON i.iem_id = m.iem_id
JOIN listings l              ON l.listing_id = m.listing_id
JOIN properties p            ON p.rentcast_id = l.rentcast_id
JOIN realtors r              ON r.realtor_id = e.realtor_id
WHERE c.campaign_id = %(campaign_id)s AND dr.stopped_at IS NULL
ORDER BY m.match_id
"""

_DEAD_SQL = """
SELECT DISTINCT listing_id FROM drip_listing_checks
WHERE campaign_id = %(campaign_id)s AND step_no < %(step_no)s
  AND outcome IN ('inactive', 'not_found')
"""

_VERDICTS_SQL = """
SELECT DISTINCT ON (listing_id) listing_id, outcome FROM drip_listing_checks
WHERE campaign_id = %(campaign_id)s AND step_no = %(step_no)s
ORDER BY listing_id, checked_at DESC, check_id DESC
"""


def _held_back(hits):
    """realtor_id -> the recipient-level reason that holds them back, for those that have one."""
    bounced = {h["realtor_id"] for h in hits if h["send_status"] == "bounced"}
    held = {}
    for h in hits:
        rid = h["realtor_id"]
        if rid in held:
            continue
        if rid in bounced:
            held[rid] = "recipient_bounced"
        elif h["on_dnc"]:
            held[rid] = "on_dnc_list"
        elif h["in_cap"]:
            held[rid] = "within_cap"
    return held


def _listing_reason(hit, dead, verdicts):
    if hit["listing_id"] in dead:
        return "listing_dead_earlier"
    outcome = verdicts.get(hit["listing_id"])
    if outcome == "active":
        return None
    if outcome == "inactive":
        return "listing_inactive"
    if outcome == "not_found":
        return "listing_not_found"
    return "listing_unconfirmed"            # an error, or never checked


def listings_to_check(hits, dead):
    """The listings this step still has to check, for the cost shown before the click.
    Nobody held back is checked, and nothing already found dead.
    """
    held = _held_back(hits)
    found = {}
    for h in hits:
        if h["realtor_id"] in held or h["listing_id"] in dead:
            continue
        found.setdefault(h["listing_id"], {"listing_id": h["listing_id"],
                                           "rentcast_id": h["rentcast_id"],
                                           "email": h["email"]})
    return sorted(found.values(), key=lambda r: r["listing_id"])


def select_followup(hits, dead, verdicts):
    """Apply the rules.  dead is a set of listing_ids, verdicts maps listing_id to the 
    outcome of this step's latest check.  Same shape as sendlist.select, plus to_stop.
    """
    held = _held_back(hits)
    emails = {h["realtor_id"]: h["email"] for h in hits}
    dropped = {r: {"matches": 0, "realtors": set()} for r in REASONS}
    reasons = defaultdict(list)
    kept = defaultdict(list)
    for h in hits:
        rid = h["realtor_id"]
        reason = held.get(rid) or _listing_reason(h, dead, verdicts)
        if reason is None:
            kept[rid].append(h)
            continue
        dropped[reason]["matches"] += 1
        dropped[reason]["realtors"].add(rid)
        reasons[rid].append(reason)

    realtors = []
    for rid in sorted(kept):
        rows = kept[rid]
        realtors.append({"realtor_id": rid, "agent_name": rows[0]["agent_name"],
                         "email": emails[rid], "events": sendlist.collapse(rows)})

    to_stop = []
    for rid in sorted(set(emails) - set(kept)):
        why = held.get(rid)
        if why == "recipient_bounced":
            reason = "bounced"
        elif why == "on_dnc_list":
            reason = "unsubscribed"
        elif why is None and all(r in _DEAD for r in reasons[rid]):
            reason = "no_active_listings"
        else:
            continue
        to_stop.append({"realtor_id": rid, "email": emails[rid], "reason": reason})

    return {
        "realtors": realtors,
        "considered": {"matches": len(hits), "realtors": len(emails)},
        "excluded": {r: {"matches": d["matches"], "realtors": len(d["realtors"])}
                     for r, d in dropped.items()},
        "to_stop": to_stop,
    }


def fetch_hits(conn, campaign_id, *, now, cap_days=CAP_DAYS):
    """cap_days None turns the cap off (sender's override)."""
    since = None if cap_days is None else now - timedelta(days=cap_days)
    return conn.execute(_HITS_SQL, {"campaign_id": campaign_id, "since": since}).fetchall()


def fetch_dead(conn, campaign_id, step_no):
    rows = conn.execute(_DEAD_SQL, {"campaign_id": campaign_id, "step_no": step_no}).fetchall()
    return {r["listing_id"] for r in rows}


def fetch_verdicts(conn, campaign_id, step_no):
    rows = conn.execute(_VERDICTS_SQL, {"campaign_id": campaign_id, "step_no": step_no}).fetchall()
    return {r["listing_id"]: r["outcome"] for r in rows}


def build_followup_list(conn, campaign_id, step_no, *, now, cap_days=CAP_DAYS):
    """The follow-up for one step, from what the database holds now."""
    hits = fetch_hits(conn, campaign_id, now=now, cap_days=cap_days)
    return select_followup(hits, fetch_dead(conn, campaign_id, step_no),
                           fetch_verdicts(conn, campaign_id, step_no))