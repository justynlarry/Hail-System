"""Tuning parameters, derived from Colorado hail evidence and revisited as more
accumulates.  No mechanism here -- each value is a judgement call, and the
comment beside it records the basis.

NOT the same as config/.  That directory is per-customer configuration -- the
coverage zip list a Dallas install replaces wholesale.  This file is the
opposite: nationally-derived constants every install shares.  Same word,
opposite halves of the generic/specific split (decision-log 2026-09-09).

Why a module and not a settings table: a few values, each with a single
consumer, changing rarely, and worth keeping under version control with the
reasoning attached.  A table adds operational overhead and drops the git
history.

Move a value to a settings table when a non-developer has to change it, or a
second consumer needs the same number.  The frequency cap is the one that
forces the table -- it has to be enforced in the database and cannot live here.
"""

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, time

METRES_PER_MILE = 1609.344

# How far from a storm report to collect Zip Code Tabulation Areas (ZCTAs).
# This sets how many RentCast lookups a pull requests, so it is also the main
# cost lever.
#
# Chosen 2026-09-10, informed by Colorado hail at >= 1.00 in over the archive
# (2004-01-26 to 2026-09-07).  Pair distances between same-day reports look flat
# in raw counts, but pair counts grow with ring area -- normalising by radius
# shows density halving between 1 and 3 miles and halving again by 10.  The
# signal supports a tight number; the flat histogram was geometry, not weather.
#
# Cost, from a 200-report sample of that hail since 2025-01-01, counting only
# ZCTAs present in coverage_zips: 5.1 per report at 3 miles, 9.4 at 5, 15.1 at
# 8, 18.2 at 10.  Roughly linear rather than quadratic -- a report near the
# territory edge picks up zips on one side only.


# CEILING:  report-to-zip distances are precomputed out to
# hail_pair_ceiling_m() (sql/017, set at inception at 10 miles)
# That value lives in the DATABASE not in this .py file.  Radius must
# stay at or below that threshold.  Raising the ceiling means a migration
# and a full recompute (scripts/backfill_zip_distances.py)


# This is the value 020_settings_radii.sql seeded settings.default_zip_radius_miles
# with at inception.  Live request-serving code reads the settings table
# (hailsys.settings.fetch_settings, exposed as g.settings) instead of this
# constant, so an admin's change on the settings page takes effect without a
# deploy.  Kept here as the default/fallback for standalone scripts that run
# outside a request and don't have g.settings.
DEFAULT_ZIP_RADIUS_MILES = 5.0

# How far from a report a listing may be and still be matched.  This is the
# number that appears in an email, so it is the one that has to survive a
# homeowner asking whether their house was actually hit.
#
# Deliberately a separate constant from the zip radius, even though both are 5.0
# at inception.  One bounds what we look at, the other bounds what we claim;
# collapsing them into one name would hide that they can diverge.
#
# This is the value 020_settings_radii.sql seeded
# settings.default_match_radius_miles with at inception.  Live code reads the
# settings table (hailsys.settings.fetch_settings) instead of this constant;
# kept as the default/fallback for standalone scripts run outside a request.
DEFAULT_MATCH_RADIUS_MILES = 5.0

# Radius does NOT vary by event type yet.  Hail cores are narrow, straight-line
# wind is broad, and a downburst is very local, so eventually it should.  When
# there is evidence, it belongs as a column on report_types next to
# roof_relevant and min_magnitude -- same kind of per-type business judgment,
# already version-controlled through planning/report_types.csv, and queryable
# from SQL, which a Python constant is not.  Not added now because there is no
# wind analysis to base a number on.

RECENT_PULL_WINDOW_DAYS = 7

# Starting Number, needs to be revisited based on actual needs.

DISPLAY_TZ = ZoneInfo("America/Denver")

def miles_to_metres(miles):
    """PostGIS geography predicates work in metres, but the unit of record here
    is miles -- matching storm_listing_matches.distance_miles and radius_used.
    """
    return miles * METRES_PER_MILE

def denver_day_bounds(day):
    """UTC half-open range covering one Calendar Day in Denver

    If a storm occurs later in the day, the UTC will record it as
    the next day, this portion counters that problem.

    The end bound is built by combining the next date with midnight,
    not by adding the timedelta(days=1) to the start.
    """
    start = datetime.combine(day, time.min, tzinfo=DISPLAY_TZ)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=DISPLAY_TZ)
    return start, end
