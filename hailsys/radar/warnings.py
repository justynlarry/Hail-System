"""Fetches NWS severe thunderstorm and tornado warning polygons from IEM and stores them.

Source:  IEM's watchwarn.py export as a shapefile, the only format with both the polygon and its
begin and end times (the CSV has no geometry, the KML hides the details in HTML).  Standard
library only.  The states come from settings.storm_states so another territory needs no code
change.  Runs on the connection it is given and leaves the commit to the caller.

Traps:
    - limitps=1 without an explicit phenomena list returns tornado warnings only, silently.
    - Field names in the dbf are cut to 10 characters (PRODUCT_ID arrives as PROD_ID).
    - IEM's JSON API, sbw_interval.geojson, ignores states= and phenomena= without an error and
        returns every warning in the country.  It is not used here for that reason.

Only storm-based polygons are asked for (limit1=1)
"""

import os
import re
import struct
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone

from hailsys.radar import fetch

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py"
PHENOMENA = ("SV", "TO")

# A fetch covers the local day plus this much each side, and the database decides which 
# polygons overlap the day.  IEM's own time filter is on the warning, not the polygon, so a 
# warning issued the evening before can still hold a polygon inside the day.
PAD = timedelta(days=1)

# A day counts as covered only when the fetch ran this long after the day ended, so a fetch made
# just after midnight is not taken for the whole day.
LAG = timedelta(hours=2)

_STATE = re.compile(r"^[A-Za-z]{2}$")
_SHP_POLYGON = 5


class WarningError(Exception):
    """Nothing was stored, message explains."""


# No WHERE id = 1:  hail_ingest may read the storm_states column and nothing else, and the table
# holds one row by its CHECK.
_STATES_SQL = "SELECT storm_states FROM settings"

_COVERED_SQL = """
SELECT EXISTS (SELECT 1 FROM warning_loads
                WHERE states = %(states)s AND window_start <= %(start)s
                  AND window_end >= %(end)s + %(lag)s) AS covered
"""

_HELD_SQL = """
SELECT count(*) AS held
FROM warning_polygons p
JOIN unnest(%(wfo)s::text[], %(phenom)s::text[], %(sig)s::text[], %(eid)s::int[],
            %(yr)s::smallint[], %(begin)s::timestamptz[])
    AS k(wfo, phenomena, significance, eventid, vtec_year, poly_begin)
  USING (wfo, phenomena, significance, eventid, vtec_year, poly_begin)
"""

_LOAD_SQL = """
INSERT INTO warning_loads (window_start, window_end, states, run_id, polygons_seen, polygons_new)
VALUES (%(start)s, %(end)s, %(states)s, %(run_id)s, %(seen)s, %(new)s)
RETURNING load_id
"""

# ST_MakeValid because a warning polygon is now and then self-intersecting (a bow tie) and the
# table refuses invalid geometry.
_POLYGON_SQL = """
INSERT INTO warning_polygons (wfo, phenomena, significance, eventid, vtec_year, poly_begin,
    poly_end, issued, expires, product_id, load_id, geom)
VALUES (%(wfo)s, %(phenomena)s, %(significance)s, %(eventid)s, %(vtec_year)s, %(poly_begin)s,
    %(poly_end)s, %(issued)s, %(expires)s, %(product_id)s, %(load_id)s,
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromText(%(wkt)s, 4326)), 3)))
ON CONFLICT DO NOTHING
"""


def normalize_states(text):
    """'co, WY' -> 'CO,WY': upper case, sorted, no repeats.  WarningError if empty or malformed."""
    codes = sorted({part.strip().upper() for part in (text or "").split(",") if part.strip()})
    if not codes or not all(_STATE.match(code) for code in codes):
        raise WarningError(f"Storm states must be two letter codes separated by commas, not {text!r}.")
    return ",".join(codes)


def current_states(conn):
    return normalize_states(conn.execute(_STATES_SQL).fetchone()["storm_states"])


def request_url(states, start, end):
    """The export URL for polygons in [start, end) for these states (already normalized)"""
    stamp = "%Y-%m-%dT%H:%MZ"
    query = urllib.parse.urlencode({
        "accept": "shapefile",
        "sts": start.astimezone(timezone.utc).strftime(stamp),
        "ets": end.astimezone(timezone.utc).strftime(stamp),
        "location_group": "states", "states": states,
        "limitps": 1, "phenomena": ",".join(PHENOMENA),
        "significance": ",".join("W" for _ in PHENOMENA),
        "limit1": 1}, safe=",:")
    return f"{URL}?{query}"


def _time(text):
    """dbf times are "YYYYMMDDHHMM' in UTC or empty."""
    if not text:
        return None
    return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def _records(dbf):
    count, header, size = struct.unpack("<IHH", dbf[4:12])
    fields, pos = [], 32
    while dbf[pos] != 0x0D:
        fields.append((dbf[pos:pos + 11].split(b"\0")[0].decode(), dbf[pos + 16]))
        pos +=32
    for i in range(count):
        raw = dbf[header + i * size: header + (i + 1) * size]
        row, at = {}, 1
        for name, width in fields:
            row[name] = raw[at:at + width].decode("latin-1").strip()
            at += width
        yield raw[0] == 0x2A, row          # 0x2A marks a deleted record


def _rings(shp):
    pos = 100
    while pos < len(shp):
        length = struct.unpack(">I", shp[pos +4:pos + 8])[0] * 2
        body = shp[pos + 8:pos + 8 + length]
        pos += 8 + length
        kind = struct.unpack("<i", body[:4])[0]
        if kind != _SHP_POLYGON:
            raise WarningError(f"A warning shape of type {kind} was not a polygon.")
        parts, points = struct.unpack("<ii", body[36:44])
        if parts != 1:
            raise WarningError(f"A warning polygon with {parts} parts is not expected.")
        coords = struct.unpack(f"<{2 * points}d", body[48:48 + 16 * points])
        ring = list(zip(coords[0::2], coords[1::2]))
        if ring[0] != ring[-1]:
            raise WarningError("A warning polygon ring is not closed.")
        yield ring


def read_shapefile(path):
    """The warnings in an IEM export zip as dicts, each with its 'ring' as (lon, lat) pairs."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            dbf = z.read(next(n for n in names if n.endswith(".dbf")))
            shp = z.read(next(n for n in names if n.endswith(".shp")))
    except (zipfile.BadZipFile, StopIteration) as exc:
        raise WarningError(f"The warning download is not a shapefile zip: {exc}") from exc
    rings = list(_rings(shp))
    records = list(_records(dbf))
    if len(rings) != len(records):
        raise WarningError(f"{len(rings)} shapes but {len(records)} records.")
    out = []
    for (deleted, row), ring in zip(records, rings):
        if deleted:
            continue
        if row["PHENOM"] not in PHENOMENA or row["SIG"] != "W" or row["GTYPE"] != "P":
            raise WarningError(f"IEM returned a {row['PHENOM']}.{row['SIG']} ({row['GTYPE']}) "
                               f"warning, only {PHENOMENA} storm-based warnings were asked for.")
        if not row["POLY_BEG"] or not row["POLY_END"]:
            raise WarningError(f"A {row['WFO']} warning ({row['ETN']}) has no polygon begin or end time.")
        out.append({"wfo": row["WFO"], "phenomena": row["PHENOM"], "significance": row["SIG"],
                        "eventid": int(row["ETN"]), "vtec_year": int(row["VTEC_YR"]),
                        "poly_begin": _time(row["POLY_BEG"]), "poly_end": _time(row["POLY_END"]),
                        "issued": _time(row["ISSUED"]), "expires": _time(row["EXPIRED"]),
                        "product_id": row["PROD_ID"] or None, "ring": ring})
    return out


def _wkt(ring):
    return "MULTIPOLYGON(((" + ",".join(f"{x:.7f} {y:.7f}" for x, y in ring) + ")))"


def covered(conn, start, end, states):
    """True if a fetch for these states spans [start, end) and ran at least LAG after end."""
    return conn.execute(_COVERED_SQL, {"states": states, "start": start, "end": end,
                                       "lag": LAG}).fetchone()["covered"]


def load_window(conn, start, end, states, directory, *, run_id=None, **download_kw):
    """Fetch and store the polygons for [start, end).  Returns {'seen', 'new', 'load_id'}.
    
    A polygon version already held is left alone, so a window can be fetched again.  The 
    download is deleted afterwards.  FetchError or WarningError propagate and nothing is stored.
    """
    path = os.path.join(directory, "warnings.zip")
    fetch.download(request_url(states, start, end), path, **download_kw)
    try:
        rows = read_shapefile(path)
    finally:
        os.remove(path)
    unique = {}
    for row in rows:
        key = (row["wfo"], row["phenomena"], row["significance"], row["eventid"],
               row["vtec_year"], row["poly_begin"])
        unique.setdefault(key, row)
    keys = list(unique)
    held = conn.execute(_HELD_SQL, {
        "wfo": [k[0] for k in keys], "phenom": [k[1] for k in keys], "sig": [k[2] for k in keys],
        "eid": [k[3] for k in keys], "yr": [k[4] for k in keys],
        "begin": [k[5] for k in keys]}).fetchone()["held"]
    load_id = conn.execute(_LOAD_SQL, {
        "start": start, "end": end, "states": states, "run_id": run_id,
        "seen": len(rows), "new": len(keys) - held}).fetchone()["load_id"]
    for row in unique.values():
        conn.execute(_POLYGON_SQL, {**row, "load_id": load_id, "wkt": _wkt(row["ring"])})
    return {"seen": len(rows), "new": len(keys) - held, "load_id": load_id}


def ensure_day(conn, day, tz, directory, *, states=None, run_id=None, now=None, **download_kw):
    """Make sure the warnings for local date 'day' are held.  Returns 'held' or 'loaded'.

    The fetch covers the day plus PAD each side but never reaches past 'now', otherwise it would
    claim hours that have not happened.  WarningError if the day ended less than LAG ago.    
    """
    states = normalize_states(states) if states else current_states(conn)
    start, end = fetch.day_window(day, tz)
    if covered(conn, start, end, states):
        return "held"
    now = now or datetime.now(timezone.utc)
    if now < end + LAG:
        raise WarningError(f"{day} ended less than {LAG} ago, so its warnings are not all in yet.")
    load_window(conn, start - PAD, min(end + PAD, now), states, directory,
                run_id=run_id, **download_kw)
    return "loaded"