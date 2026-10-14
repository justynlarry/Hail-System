"""Part A, source 4: mPING feasibility with the API key. Run from ~/hail-analysis:
    .venv/bin/python weather_sources/probe_mping.py
Reads only MPING_* lines from ~/Hail-System/.env and never prints the key. One request at a time,
a pause between pages, every request logged (URL only) to fetch_log.tsv. Results cached per query."""
import os, re, json, time, math, csv, urllib.request, urllib.parse, datetime as dt, collections, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "samples", "mping"); os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(HERE, "fetch_log.tsv")
ENV = os.path.expanduser("~/Hail-System/.env")
KEY = None
for line in open(ENV):
    m = re.match(r"\s*(?:export\s+)?MPING_API_KEY\s*=\s*(.*)\s*$", line)
    if m: KEY = m.group(1).strip().strip('"').strip("'")
assert KEY, "MPING_API_KEY not found in .env"
BASE = "https://mping.ou.edu/mping/api/v2/reports"
BBOX = "-105.58,38.52,-103.71,40.89"          # territory: lon_min,lat_min,lon_max,lat_max

def get_all(params, tag, max_pages=60):
    cache = os.path.join(OUT, f"q_{tag}.json")
    if os.path.exists(cache): return json.load(open(cache))
    url = BASE + "?" + urllib.parse.urlencode(params)
    rows, pages, count = [], 0, None
    while url and pages < max_pages:
        req = urllib.request.Request(url, headers={"Authorization": f"Token {KEY}", "Accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r: d = json.load(r); code = r.status; break
            except urllib.error.HTTPError as e:
                code = e.code
                with open(LOG, "a") as fh: fh.write(f"{dt.datetime.now(dt.timezone.utc):%FT%TZ}\tmping {tag} p{pages + 1}\t{code}\t\t{url}\n")
                raise SystemExit(f"HTTP {code} on {tag}: {e.read()[:200]!r}")
            except OSError:
                if attempt == 2: raise
                time.sleep(5)
        with open(LOG, "a") as fh: fh.write(f"{dt.datetime.now(dt.timezone.utc):%FT%TZ}\tmping {tag} p{pages + 1}\t{code}\t{len(d.get('results', []))} rows\t{url}\n")
        count = d.get("count"); rows += d.get("results", []); url = d.get("next"); pages += 1
        time.sleep(1.0)
    res = dict(params=params, count=count, pages=pages, truncated=bool(url), results=rows)
    json.dump(res, open(cache, "w"))
    return res

def hail_size(desc):
    m = re.search(r"\(([\d.]+) in", desc or ""); return float(m.group(1)) if m else None

out = {}
# 1. test day, territory, hail
q = get_all({"category": "Hail", "obtime_gte": "2023-05-10 18:00:00", "obtime_lte": "2023-05-11 06:00:00", "in_bbox": BBOX}, "test_20230510")
out["test_day"] = dict(count=q["count"], sizes=collections.Counter(r["description"] for r in q["results"]).most_common(10),
                       example=q["results"][:2])
# 2. history: earliest hail reports anywhere (by year filter)
hist = {}
for y in (2012, 2013, 2014):
    r = get_all({"category": "Hail", "year": y, "in_bbox": BBOX}, f"hist_{y}", max_pages=3)
    hist[y] = dict(count=r["count"], earliest=min((x["obtime"] for x in r["results"]), default=None))
out["history_territory_hail_by_year"] = hist
# 3. density: 2023 season, territory, hail
s = get_all({"category": "Hail", "obtime_gte": "2023-04-01 00:00:00", "obtime_lte": "2023-10-01 00:00:00", "in_bbox": BBOX}, "season_2023")
R = s["results"]
out["season_2023"] = dict(count=s["count"], fetched=len(R), truncated=s["truncated"], days=len({x["obtime"][:10] for x in R}),
                          sizes=collections.Counter(x["description"] for x in R).most_common(12),
                          ge_1in=sum(1 for x in R if (hail_size(x["description"]) or 0) >= 1.0))
# 4. overlap with IEM HAIL 2023 (reports_raw.csv): same UTC +-60 min and within 3 km
L = [dict(t=dt.datetime.strptime(r["utc"], "%Y-%m-%dT%H:%M:%SZ"), la=float(r["latitude"]), lo=float(r["longitude"]), src=r["report_source_norm"])
     for r in csv.DictReader(open(os.path.join(HERE, "..", "storm_shape", "reports_raw.csv"))) if r["report_text"] == "HAIL" and r["utc"][:4] == "2023"]
def match(x):
    t = dt.datetime.strptime(x["obtime"][:19], "%Y-%m-%dT%H:%M:%S"); lo, la = x["geom"]["coordinates"]
    return [l for l in L if abs((l["t"] - t).total_seconds()) <= 3600 and math.hypot((l["la"] - la) * 111, (l["lo"] - lo) * 85) <= 3]
m = [match(x) for x in R]
out["overlap_iem_2023"] = dict(mping=len(R), with_iem_within_3km_60min=sum(1 for z in m if z),
                               matched_iem_source=collections.Counter(z[0]["src"] for z in m if z).most_common(5),
                               ge_1in_mping=sum(1 for x in R if (hail_size(x["description"]) or 0) >= 1.0),
                               ge_1in_with_iem=sum(1 for x, z in zip(R, m) if z and (hail_size(x["description"]) or 0) >= 1.0))
# 5. latency proxy: newest reports anywhere vs now
n = get_all({"obtime_gte": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")}, "recent_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H"), max_pages=2)
newest = max((x["obtime"] for x in n["results"]), default=None)
out["recent_6h"] = dict(count=n["count"], newest_obtime=newest, queried_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%FT%TZ"))
json.dump(out, open(os.path.join(OUT, "probe_mping.json"), "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
