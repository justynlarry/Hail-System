"""Render B2 maps (stdlib PNG, no matplotlib). Left panel: MESH + circles + gap. Right panel: MESH + capsules. No text; legend swatches keyed in summary.md."""
import json, math, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from png_render import Canvas, wkt_rings, make_projection
d = json.load(open("b2_numbers.json"))
PW = 760
def tier(v):
    return (150, 0, 0) if v >= 50.8 else ((230, 60, 30) if v >= 38.1 else ((250, 150, 40) if v >= 25.4 else (252, 215, 120)))
def panel(cv, ox, run, bbox, proj, h, which):
    cells = run["cells19"]
    for la, lo, v in cells:
        x0, y0 = proj(lo - 0.005, la + 0.005); x1, y1 = proj(lo + 0.005, la - 0.005)
        x0c, x1c, y0c, y1c = max(x0, 2), min(x1 - 1, PW - 3), max(y0, 2), min(y1 - 1, h - 3)   # clip to the panel
        if x1c >= x0c and y1c >= y0c: cv.rect(ox + x0c, y0c, ox + x1c, y1c, tier(v))
    W = run["wkt"]
    def rings(k): return [[(ox + x, y) for x, y in (proj(lo, la) for lo, la in r)] for r in wkt_rings(W[k])]
    if which == "circles":
        g = rings("gap"); cv.fill_poly(g, (30, 90, 220), 0.55); cv.outline(g, (20, 60, 180), 2)
        cv.outline(rings("corridor"), (110, 110, 110), 1, dash=6)
        cv.outline(rings("circle"), (0, 0, 0), 2)
    else:
        cv.outline(rings("cap_wide"), (120, 40, 160), 2)
        cv.outline(rings("cap_p90"), (20, 140, 60), 2)
    for r in run["reports"]:
        x, y = proj(r["lon"], r["lat"]); x += ox
        if r["type"] != "HAIL": cv.rect(x - 3, y - 3, x + 3, y + 3, (0, 0, 0))
        elif r["act"]: cv.disc(x, y, 4, (0, 0, 0))
        else: cv.disc(x, y, 3, (200, 200, 200), outline=(0, 0, 0))
    cv.rect(ox, 0, ox + PW - 1, 1, (0, 0, 0)); cv.rect(ox, h - 2, ox + PW - 1, h - 1, (0, 0, 0)); cv.rect(ox, 0, ox + 1, h - 1, (0, 0, 0)); cv.rect(ox + PW - 2, 0, ox + PW - 1, h - 1, (0, 0, 0))
for name in ("piece22", "whole34"):
    run = d[f"{name}|pca"]
    pts = [p for k in ("circle", "cap_wide", "cap_p90") for r in wkt_rings(run["wkt"][k]) for p in r]
    lo0, lo1 = min(p[0] for p in pts) - 0.04, max(p[0] for p in pts) + 0.04; la0, la1 = min(p[1] for p in pts) - 0.04, max(p[1] for p in pts) + 0.04
    h, proj = make_projection((lo0, la0, lo1, la1), PW)
    cv = Canvas(2 * PW + 20, h + 70)
    panel(cv, 0, run, None, proj, h, "circles"); panel(cv, PW + 20, run, None, proj, h, "capsules")
    # legend row (swatches only)
    y = h + 20; x = 10
    for c in [(252, 215, 120), (250, 150, 40), (230, 60, 30), (150, 0, 0)]: cv.rect(x, y, x + 34, y + 16, c); x += 40
    x += 20; cv.line(x, y + 8, x + 34, y + 8, (0, 0, 0), 2); x += 60
    cv.rect(x, y, x + 34, y + 16, (30, 90, 220), 0.55); cv.outline([[(x, y), (x + 34, y), (x + 34, y + 16), (x, y + 16)]], (20, 60, 180), 2); x += 60
    cv.line(x, y + 8, x + 34, y + 8, (110, 110, 110), 1, dash=6); x += 60
    cv.line(x, y + 8, x + 34, y + 8, (20, 140, 60), 2); x += 60
    cv.line(x, y + 8, x + 34, y + 8, (120, 40, 160), 2); x += 60
    cv.disc(x + 10, y + 8, 4, (0, 0, 0)); x += 30; cv.disc(x + 10, y + 8, 3, (200, 200, 200), outline=(0, 0, 0)); x += 30; cv.rect(x + 6, y + 5, x + 12, y + 11, (0, 0, 0)); x += 40
    km = 20; lat_m = (la0 + la1) / 2; pxkm = PW / ((lo1 - lo0) * 111.32 * math.cos(math.radians(lat_m)))
    cv.line(x, y + 8, x + km * pxkm, y + 8, (0, 0, 0), 2); cv.line(x, y + 2, x, y + 14, (0, 0, 0), 2); cv.line(x + km * pxkm, y + 2, x + km * pxkm, y + 14, (0, 0, 0), 2)
    cv.png(f"s0025_{name}.png"); print(name, cv.w, cv.h, (lo0, la0, lo1, la1), "20 km bar =", round(km * pxkm), "px")
