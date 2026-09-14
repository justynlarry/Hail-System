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

DEFAULT_ZIP_RADIUS_MILES = 5.0

# How far from a report a listing may be and still be matched.  This is the
# number that appears in an email, so it is the one that has to survive a
# homeowner asking whether their house was actually hit.
#
# Deliberately a separate constant from the zip radius, even though both are 5.0
# at inception.  One bounds what we look at, the other bounds what we claim;
# collapsing them into one name would hide that they can diverge.

DEFAULT_MATCH_RADIUS_MILES = 5.0

# Radius does NOT vary by event type yet.  Hail cores are narrow, straight-line
# wind is broad, and a downburst is very local, so eventually it should.  When
# there is evidence, it belongs as a column on report_types next to
# roof_relevant and min_magnitude -- same kind of per-type business judgment,
# already version-controlled through planning/report_types.csv, and queryable
# from SQL, which a Python constant is not.  Not added now because there is no
# wind analysis to base a number on.


def miles_to_metres(miles):
    """PostGIS geography predicates work in metres, but the unit of record here
    is miles -- matching storm_listing_matches.distance_miles and radius_used.
    """
    return miles * METRES_PER_MILE
