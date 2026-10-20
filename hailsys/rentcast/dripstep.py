"""Spending RentCast calls on one drip step:  re-check each listing the follow-up would
mention, and log what RentCast replied with in drip_listing_checks.

Two paths, chosen by the caller from dripplan.choose_method:
    check_by_id         one lookup per listing
    check_by_zip_pull   pull the zips the listings sit in, then read each listing off the pull

With either path, every listing ends the loop with a drip_listing_checks row, and select_followup
reads only those rows.  Nothing here decides who is emailed.

A step that cannot go on raises StepStopped with the calls spent so far, so the run row can
say what happened.  Its reason never holds text from a RentCast response.
"""

import logging

from hailsys.rentcast import client, dripcheck, dripplan, pull, upsert

logger = logging.getLogger(__name__)

# A fixed safety limit, not a setting:  this many lookups in a row failing means RentCast is
# down or refusing us, and every further call would be billed for nothing.
CONSECUTIVE_FAILURES = 10

# A definite answer this new is reused when a step is run again after a crash.
REUSE_HOURS = 24

_RECENT_SQL = """
SELECT DISTINCT listing_id FROM drip_listing_checks
WHERE campaign_id = %(campaign_id)s AND step_no = %(step_no)s
    AND outcome IN ('active', 'inactive', 'not_found')
    AND checked_at > now() - make_interval(hours => %(hours)s)
"""

_ZIPS_SQL = """
SELECT l.listing_id, p.zip_code
FROM listings l JOIN properties p ON p.rentcast_id = l.rentcast_id
WHERE l.listing_id = ANY(%(listing_ids)s)
"""

_LINK_PULL_SQL = """
INSERT INTO drip_step_pulls (pull_id, campaign_id, step_no)
VALUES (%(pull_id)s, %(campaign_id)s, %(step_no)s)
"""


class StepStopped(Exception):
    """The step cannot go on.  calls is what was spent before it stopped."""

    def __init__(self, reason, calls):
        super().__init__(reason)
        self.reason = reason
        self.calls = calls

def recent_definite(conn, campaign_id, step_no, *, hours=REUSE_HOURS):
    """listing_ids this step already has a definite answer for, from the last day."""
    rows = conn.execute(_RECENT_SQL, {"campaign_id": campaign_id, "step_no": step_no,
                                      "hours": hours}).fetchall()
    return {r["listing_id"] for r in rows}


def listing_zips(conn, listing_ids):
    """{listing_id: zip_code} for these listings, zip_code None when the property has none."""
    rows = conn.execute(_ZIPS_SQL, {"listing_ids": list(listing_ids)}).fetchall()
    return {r["listing_id"]: r["zip_code"] for r in rows}


def plan_method(conn, todo):
    """(zips, plan) for the listings still to check:  the zip each sits in, and which way costs less
    (dripplan.choose_method).  A listing with no zip on file can only be checked by id, so it forces
    the method and plan['calls'] is then the by-id count.  Read-Only, no RentCast call.  Step screen
    shows this plan and run_step follows it.
    """
    zips = listing_zips(conn, [i["listing_id"] for i in todo]) if todo else {}
    plan = dripplan.choose_method(
        listings=len(todo),
        zip_calls=dripplan.zip_calls_estimate(conn, {z for z in zips.values() if z}))
    no_zip = len(zips) < len(todo) or None in zips.values()
    if todo and no_zip and plan["method"] != dripplan.BY_ID:
        plan = dict(plan, method=dripplan.BY_ID, calls=plan["by_id_calls"])
    return zips, plan


def _refused(exc):
    return f"RentCast refused the request ({type(exc).__name__})"


def check_by_id(conn, *, campaign_id, step_no, listings, skip, today, freshness_days):
    """One lookup per listing.  listings are listings_to_check rows (listing_id, rentcast_id,
    email).  Skip is the set of listing_ids that already have a recent answer."""
    calls = checked = failures = 0
    for item in listings:
        if item["listing_id"] in skip:
            continue
        try:
            verdict = dripcheck.check_listing(
                conn, campaign_id=campaign_id, step_no=step_no, listing_id=item["listing_id"],
                rentcast_id=item["rentcast_id"], today=today, freshness_days=freshness_days,
                agent_email=item["email"]
            )
        except dripcheck.FATAL as exc:
            raise StepStopped(_refused(exc), calls + exc.attempts) from exc
        calls += verdict["calls_made"]
        checked += 1
        failures = failures + 1 if verdict["outcome"] == "error" else 0
        if failures >= CONSECUTIVE_FAILURES:
            raise StepStopped(f"{failures} lookups in a row failed", calls)
    return {"method": "by_id", "calls": calls, "checked": checked,
            "reused": len(listings) - checked}


def check_by_zip_pull(conn, *, campaign_id, step_no, listings, skip, zips, emp_id,
                      estimated_calls, today, freshness_days):
    """Pull the zips these listings sit in and read each one off the pull.  zips is
    {listing_id: zip_code} from listing_zips."""
    todo = [i for i in listings if i["listing_id"] not in skip]
    by_zip = {}
    for item in todo:
        by_zip.setdefault(zips.get(item["listing_id"]), []).append(item)

    def record(item, verdict):
        dripcheck.record_check(conn, campaign_id=campaign_id, step_no=step_no,
                               listing_id=item["listing_id"], verdict=verdict)

    for item in by_zip.pop(None, []):
        record(item, dripcheck._verdict("error", detail="the listing has no zip code"))

    zip_codes = sorted(by_zip)
    pull_id = pull.create_pull(conn, emp_id=emp_id, storm_date=None, report_text=None,
                               zip_count=len(zip_codes), estimated_api_calls=estimated_calls)
    conn.execute(_LINK_PULL_SQL, {"pull_id": pull_id, "campaign_id": campaign_id,
                                  "step_no": step_no})
    conn.commit()

    calls = returned = 0
    for zip_code in zip_codes:
        try:
            found, made = client.search_sale_listings(zip_code, status="Active", days_old=None)
        except client.RentCastError as exc:
            pull._log_zip(conn, pull_id, zip_code, exc.attempts, 0, exc.status)
            calls += exc.attempts
            logger.warning("event=drip_zip_failed pull_id=%s zip=%s status=%s",
                           pull_id, zip_code, exc.status)
            if isinstance(exc, dripcheck.FATAL):
                pull._finish_pull(conn, pull_id, calls, returned, "failed")
                raise StepStopped(_refused(exc), calls) from exc
            for item in by_zip[zip_code]:
                record(item, dripcheck._verdict(
                    "error", http_status=exc.status,
                    detail=f"the zip pull failed ({type(exc).__name__})"))
            continue
        except Exception:
            logger.exception("event=drip_zip_unclassified pull_id=%s zip=%s", pull_id, zip_code)
            pull._log_zip(conn, pull_id, zip_code, 1, 0, None)
            pull._finish_pull(conn, pull_id, calls + 1, returned, "failed")
            raise

        pull._log_zip(conn, pull_id, zip_code, made, len(found), 200)
        calls += made
        returned += len(found)
        if found:
            try:
                upsert.upsert_listings(conn, found)
            except Exception:
                logger.exception("event=drip_upsert_failed pull_id=%s zip=%s", pull_id, zip_code)
                conn.rollback()
                pull._finish_pull(conn, pull_id, calls, returned, "failed")
                raise

        seen = {f.get("id"): f for f in found}
        for item in by_zip[zip_code]:
            raw = seen.get(item["rentcast_id"])
            if raw is None:
                record(item, dripcheck._verdict("not_found", detail="not in the zip pull"))
                continue
            verdict = dripcheck.classify(raw, today=today, freshness_days=freshness_days,
                                         agent_email=item["email"])
            verdict["http_status"] = 200
            record(item, verdict)
    pull._finish_pull(conn, pull_id, calls, returned, "complete")
    return {"method": "zip_pull", "calls": calls, "checked": len(todo), "pull_id": pull_id,
            "reused": len(listings) - len(todo)}