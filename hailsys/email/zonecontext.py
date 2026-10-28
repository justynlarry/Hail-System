"""The template variables for one realtor's zone email.

Phase 5c step 4, piece 3b.  The zone twin of render.build_context:  the events come from
the zone send list (a house and a storm day each, with the bases they rest on), and there is no hail
size and no distance to show.  The evidence sentences are made by zonewording from the
parameter set's own radar threshold and report radius, never typed here.  No database.
"""

from hailsys.email import render, zonewording


def build_context(agent_name, events, settings, wording, max_listings=render.MAX_LISTINGS):
    """Variables for one realtor's eligible zone events.

    events:  that realtor's events from the zone send list.
    wording: {'hail_in', 'radius_km'}, the 'set' of the same list, for the radar sentence.
    """
    if not events:
        raise render.RenderError("no matches to render")

    grouped = {}
    for e in events:
        grouped.setdefault(e["listing_id"], {"address": e["address"], "events": []})
        grouped[e["listing_id"]]["events"].append(e)

    listings = []
    for item in grouped.values():
        ordered = sorted(item["events"], key=lambda e: e["utc_datetime"])
        listings.append({
            "address": item["address"],
            "_rank": (-len(ordered), item["address"]),
            "events": [{"date": render.date_label(render.local_date(e["utc_datetime"]))}
                       for e in ordered],
        })
    listings.sort(key=lambda p: p["_rank"])
    for p in listings:
        del p["_rank"]

    days = [render.local_date(e["utc_datetime"]) for e in events]
    bases = {b for e in events for b in e["bases"]}
    shown = listings[:max_listings]
    return {
        "logo_url": settings.logo_url,
        "badge_cra_url": settings.badge_cra_url,
        "badge_bbb_url": settings.badge_bbb_url,
        "contact_email": settings.contact_email,
        "schedule_url": settings.schedule_url,
        "agent_first_name": render.first_name(agent_name) or "",
        "storm_count": len(set(days)),
        "storm_dates_label": render.storm_dates_label(days),
        "evidence": zonewording.sentences(bases, wording["hail_in"], wording["radius_km"]),
        "attribution": zonewording.attribution_for(bases),
        "properties": shown,
        "more_count": len(listings) - len(shown),
        "total": len(listings),
        "images": {name: dict(image) for name, image in settings.images.items()},
    }
