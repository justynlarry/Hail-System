"""Confound check: does MESH counted for a storm include hail from other clusters? For each storm, count reports from OTHER clusters
that fall inside its 30 km domain during its file window (first file stamp minus 60 min to last file stamp), and recompute the
summary for storms with no such neighbour. Reports only; a neighbouring cell with no reports is invisible to this check.
Run from ~/hail-analysis: .venv/bin/python mesh60/neighbour_check.py"""
import os, sys, json, glob, csv, math, contextlib, io, datetime as dt, statistics as st
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "run_storms.py")).read().split("# ---------------- grid geometry")[0]
ns = {"__file__": os.path.join(HERE, "run_storms.py")}
with contextlib.redirect_stdout(io.StringIO()): exec(compile(src, "run_storms_members", "exec"), ns)
members, rows = ns["members"], ns["rows"]
owner = {r["iem_id"]: k for k, c in members.items() for r in c}
S = {d["storm_id"]: d for d in (json.load(open(p)) for p in glob.glob(os.path.join(HERE, "per_storm", "*.json")))}
out = {}
for k, d in S.items():
    c = members[k] if k in members else None
    if c is None: continue            # reference rows are handled by their host cluster
    hrs = [dt.datetime.fromisoformat(f["hour"]) for f in d["files"]]
    lo, hi = min(hrs) - dt.timedelta(minutes=60), max(hrs)
    ids = {r["iem_id"] for r in c}
    nb = [r for r in rows if r["iem_id"] not in ids and lo < r["utc"] <= hi
          and min(math.hypot(r["x"] - s["x"], r["y"] - s["y"]) for s in c) <= 30_000]
    out[k] = dict(n_other_reports=len(nb), other_clusters=sorted({owner[r["iem_id"]] for r in nb}),
                  n_other_hail_ge100=sum(1 for r in nb if r["type"] == "HAIL" and (r["mag"] or 0) >= 1.0))
json.dump(out, open(os.path.join(HERE, "neighbours.json"), "w"), indent=1)
sel = [k for k in out if S[k]["in_selection"]]
clean = [k for k in sel if out[k]["n_other_reports"] == 0]
dirty = [k for k in sel if out[k]["n_other_reports"] > 0]
def summ(ks):
    A = sum(S[k]["mesh100_km2"] for k in ks)
    ka = [k for k in ks if S[k]["mesh100_km2"] >= 10]
    m = lambda key: (f"{100*st.median([S[k][key] for k in ka]):.0f}%" if ka else "n/a")
    return dict(storms=len(ks), ranked=len(ka), mesh100_km2=round(A),
                covered_pooled=round(sum(S[k]["mesh100_in_circles_km2"] for k in ks) / A, 3) if A else None,
                beyond8_pooled=round(sum(S[k]["mesh100_beyond8mi_km2"] for k in ks) / A, 3) if A else None,
                covered_median=m("share100_covered"), beyond8_median=m("share100_beyond8mi"))
res = dict(no_neighbour=summ(clean), with_neighbour=summ(dirty),
           worst5={k: out[k] for k in ["A02451", "A02418", "A02263", "A02156", "A02419"]})
print(json.dumps(res, indent=1))
json.dump(res, open(os.path.join(HERE, "neighbour_summary.json"), "w"), indent=1)
