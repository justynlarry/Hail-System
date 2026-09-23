"""Fetch DOLA municipal boundaries (or any ArcGIS layer) as a dated raw snapshot.

    python3 scripts/fetch_municipal.py
    python3 scripts/fetch_municipal.py --layer-url URL --out-dir DIR --name NAME

Writes two files side by side:
    <out-dir>/<name>_<YYYY-MM-DD>.geojson      one FeatureCollection
    <out-dir>/<name>_<YYYY-MM-DD>.layer.json   the layer's own metadata

scripts/load_municipal.sh reads both: the source edit date comes from
editingInfo.dataLastEditDate in the .layer.json.

Runs on the host, not in a container: stdlib only, and the loader image has
no Python.  Safe to re-run -- a same-day run overwrites that day's files.

Counts are checked, not assumed.  The total comes from returnCountOnly before
paging; the run fails if the pages do not add up to it or if any OBJECTID
repeats.  A page can come back shorter than asked for (polygon GeoJSON hits
the server's transfer limit before maxRecordCount), so the offset advances by
what actually arrived, never by the page size.
"""

import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DOLA_URL = ("https://services3.arcgis.com/DgjqnJA1rgO92Soi/arcgis/rest/services/"
            "DOLA_Municipalities_(Boundaries_Dissolved)/FeatureServer/0")

# Some public ArcGIS servers (ags.auroragov.org) return 403 to urllib's
# default User-Agent while serving curl.  Name ourselves instead.
USER_AGENT = "hail-system-fetch/1.0 (reference-data snapshot)"


def get(url, params, attempts=4):
    q = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(q, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            # ArcGIS reports query errors as HTTP 200 with an "error" body.
            if "error" in d:
                raise RuntimeError(f"server error: {d['error']}")
            return d
        except Exception as e:
            if attempt == attempts:
                raise
            print(f"event=retry attempt={attempt} error={e!r}", file=sys.stderr)
            time.sleep(2 ** attempt)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layer-url", default=DOLA_URL)
    ap.add_argument("--out-dir", default="data/raw/dola")
    ap.add_argument("--name", default="municipal_boundaries")
    ap.add_argument("--page-size", type=int, default=200)
    ap.add_argument("--allow-null-geometry", action="store_true",
                    help="keep features with no geometry (tables, ungeocoded "
                         "records); without it a null geometry fails the run")
    args = ap.parse_args()

    layer = args.layer_url.rstrip("/")
    meta = get(layer, {"f": "json"})
    oid = meta.get("objectIdField") or next(
        f["name"] for f in meta["fields"] if f["type"] == "esriFieldTypeOID")
    page = min(args.page_size, meta.get("maxRecordCount") or args.page_size)

    total = get(layer + "/query",
                {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    print(f"event=fetch_start layer={layer} total={total} page={page} oid={oid}")

    feats, offset = [], 0
    while offset < total:
        d = get(layer + "/query", {
            "where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson",
            "orderByFields": f"{oid} ASC", "resultOffset": offset,
            "resultRecordCount": page,
        })
        batch = d.get("features", [])
        if not batch:
            sys.exit(f"error: empty page at offset {offset} of {total}")
        feats.extend(batch)
        offset += len(batch)

    ids = [f.get("id", f.get("properties", {}).get(oid)) for f in feats]
    if len(feats) != total or len(set(ids)) != total:
        sys.exit(f"error: count mismatch: server {total}, fetched {len(feats)}, "
                 f"distinct {oid} {len(set(ids))}")
    null_geom = sum(1 for f in feats if f.get("geometry") is None)
    if null_geom and not args.allow_null_geometry:
        sys.exit(f"error: {null_geom} features with null geometry")

    stamp = datetime.date.today().isoformat()
    os.makedirs(args.out_dir, exist_ok=True)
    base = os.path.join(args.out_dir, f"{args.name}_{stamp}")
    with open(base + ".geojson", "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    with open(base + ".layer.json", "w") as fh:
        json.dump(meta, fh)
    print(f"event=fetch_done total={total} fetched={len(feats)} "
          f"null_geometry={null_geom} out={base}.geojson")


if __name__ == "__main__":
    main()
