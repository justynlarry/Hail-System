"""Part B, source 1: NWS SVR/TOR warning polygons against MESH and today's circles, on the 60 MESH storms.
Run from ~/hail-analysis: .venv/bin/python weather_sources/partb_warnings.py
Polygons: IEM watchwarn.py, Colorado, SV+TO warnings, storm-based only, one request per year (cached).
For each storm, polygons whose polygon validity overlaps the storm's MESH window are rasterised onto the MESH grid
(cell centre inside any polygon) and compared inside the same 30 km domain as mesh60."""
import os, sys, json, struct, zipfile, io, csv, datetime as dt, collections
import numpy as np
from matplotlib.path import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

OUT = os.path.join(C.HERE, "partb", "warnings"); os.makedirs(OUT, exist_ok=True)
RAW = os.path.join(C.HERE, "samples", "warnings", "partb")
Q = "location_group=states&states=CO&limitps=1&phenomena=SV,TO&significance=W,W&limit1=1"
# phenomena list is required: without it limitps returns tornado warnings only (Part A trap)

def read_shapefile(zpath):
    """Minimal .shp (polygon, type 5) + .dbf reader: returns list of (attrs, [rings as Nx2 lon/lat])."""
    z = zipfile.ZipFile(zpath)
    shp = z.read([n for n in z.namelist() if n.endswith(".shp")][0])
    dbf = z.read([n for n in z.namelist() if n.endswith(".dbf")][0])
    nrec, hlen, rlen = struct.unpack("<IHH", dbf[4:12])
    fields, p = [], 32
    while dbf[p] != 0x0D:
        name = dbf[p:p + 11].split(b"\0")[0].decode(); flen = dbf[p + 16]; fields.append((name, flen)); p += 32
    attrs = []
    for i in range(nrec):
        rec = dbf[hlen + i * rlen: hlen + (i + 1) * rlen]; q = 1; a = {}
        for name, flen in fields:
            a[name] = rec[q:q + flen].decode("latin-1").strip(); q += flen
        attrs.append(a)
    shapes, p = [], 100
    while p < len(shp):
        clen = struct.unpack(">I", shp[p + 4:p + 8])[0] * 2; body = shp[p + 8:p + 8 + clen]; p += 8 + clen
        st = struct.unpack("<i", body[:4])[0]
        if st == 0: shapes.append([]); continue
        assert st == 5, f"shape type {st}"
        nparts, npts = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
        pts = np.frombuffer(body[44 + 4 * nparts:44 + 4 * nparts + 16 * npts], dtype="<f8").reshape(-1, 2)
        shapes.append([pts[parts[k]:parts[k + 1]] for k in range(nparts)])
    assert len(shapes) == len(attrs), (len(shapes), len(attrs))
    return list(zip(attrs, shapes))

def ts(s): return dt.datetime.strptime(s[:12], "%Y%m%d%H%M") if s and s[0].isdigit() and len(s) >= 12 else None

def main():
    ns = C.load_members(); members = ns["members"]; S = C.per_storm()
    SEL = sorted(k for k, d in S.items() if d["in_selection"])
    years = sorted({S[k]["start_local"][:4] for k in SEL})
    polys = []
    for y in years:
        z = os.path.join(RAW, f"wwa_CO_SVTO_{y}.zip")
        C.fetch(f"https://mesonet.agron.iastate.edu/cgi-bin/request/gis/watchwarn.py?accept=shapefile&sts={y}-01-01T00:00Z&ets={int(y)+1}-01-01T00:00Z&{Q}", z)
        polys += read_shapefile(z)
    print("polygons read:", len(polys), collections.Counter(a["PHENOM"] for a, _ in polys), "fields:", sorted(polys[0][0]))
    # times: dbf stores YYYYMMDDHHMM strings (UTC); check which fields exist
    LON2, LAT2 = C.LON2, C.LAT2
    rows = []
    for k in SEL:
        jpath = os.path.join(OUT, f"{k}.json")
        if os.path.exists(jpath): rows.append(json.load(open(jpath))); continue
        d = S[k]; c = members[k]
        w0, w1 = C.window_utc(d)
        rr, cc = C.domain(c)
        lon, lat = LON2[rr, cc], LAT2[rr, cc]
        pts = np.column_stack([lon, lat])
        inpoly = np.zeros(rr.size, bool); used = []
        lo0, lo1, la0, la1 = lon.min(), lon.max(), lat.min(), lat.max()
        for a, rings in polys:
            b = ts(a.get("POLY_BEG") or a.get("POLYBEGIN") or a.get("ISSUED")); e = ts(a.get("POLY_END") or a.get("POLYEND") or a.get("EXPIRED"))
            if b is None or e is None or not (b < w1 and e > w0): continue
            if not rings: continue
            allp = np.vstack(rings)
            if allp[:, 0].max() < lo0 or allp[:, 0].min() > lo1 or allp[:, 1].max() < la0 or allp[:, 1].min() > la1: continue
            hit = np.zeros(rr.size, bool)
            for ring in rings: hit |= Path(ring).contains_points(pts)       # holes ignored (none expected in warnings)
            if hit.any():
                inpoly |= hit
                used.append(dict(phenom=a["PHENOM"], wfo=a["WFO"], begin=str(b), end=str(e), hailtag=a.get("HAILTAG"),
                                 windtag=a.get("WINDTAG"), status=a.get("STATUS"), product=a.get("PROD_ID")))
        mx = C.mesh_max(d); v = np.nan_to_num(mx[rr, cc], nan=-1.0); area = C.CELL_KM2[rr, cc]
        act = [r for r in c if r["act"]]; dact = C.min_dist(rr, cc, act)
        m100, m075, circ = v >= C.T100, v >= C.T075, dact <= C.R_M
        A = lambda m: float(area[m].sum())
        tags = [float(u["hailtag"]) for u in used if u["hailtag"] not in (None, "", "None", "0.0", "0")]
        res = dict(storm_id=k, start_local=d["start_local"], window_utc=[str(w0), str(w1)], n_polygons=len(used),
                   n_sv=sum(u["phenom"] == "SV" for u in used), n_to=sum(u["phenom"] == "TO" for u in used),
                   wfos=sorted({u["wfo"] for u in used}),
                   domain_km2=A(np.ones(rr.size, bool)), poly_km2=A(inpoly), circle_km2=A(circ),
                   mesh100_km2=A(m100), mesh075_km2=A(m075),
                   mesh100_in_poly_km2=A(m100 & inpoly), mesh100_in_circle_km2=A(m100 & circ),
                   mesh100_in_either_km2=A(m100 & (inpoly | circ)),
                   poly_no_mesh075_far_km2=A(inpoly & ~m075 & (dact > C.FAR_M)),
                   mesh100_outside_poly_km2=A(m100 & ~inpoly),
                   max_hailtag_in=max(tags) if tags else None,
                   max_mesh_domain_in=float(v.max() / 25.4) if v.max() > 0 else 0.0,
                   max_mesh_in_poly_in=float(v[inpoly].max() / 25.4) if inpoly.any() and v[inpoly].max() > 0 else 0.0,
                   max_reported_hail_in=d["max_hail_reported_in"], polygons=used)
        json.dump(res, open(jpath + ".tmp", "w"), indent=1); os.replace(jpath + ".tmp", jpath)
        rows.append(res)
        print(f"{k} polys {len(used):2d} | MESH>=1 {res['mesh100_km2']:6.0f} km2 | in poly {res['mesh100_in_poly_km2']:6.0f} | in circle {res['mesh100_in_circle_km2']:6.0f} | poly {res['poly_km2']:6.0f} km2", flush=True)
    return rows

if __name__ == "__main__":
    main()
