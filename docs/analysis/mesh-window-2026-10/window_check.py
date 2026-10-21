"""Slice 0 (Phase 5b): does MESH_Max_1440min stamped T equal the max of the 24 hourly MESH_Max_60min files
ending at T, and is the stamp the END of the window?  Throwaway; read-only; public NOAA bucket."""
import sys, os, time, urllib.request, datetime as dt
sys.path.insert(0, "/home/hail-user/hail-system/docs/analysis/storm-shape-mesh-2026-10/storm_shape/mesh")
import mesh_lib
B = "https://noaa-mrms-pds.s3.amazonaws.com/CONUS"
BOX = (41.5, 36.5, -109.7, -101.4)
IN = 25.4
def fetch(prod, t):
    stamp = t.strftime("%Y%m%d-%H%M%S"); name = f"MRMS_{prod}_{stamp}.grib2.gz"
    if not os.path.exists(name):
        for attempt in range(5):
            try:
                urllib.request.urlretrieve(f"{B}/{prod}/{stamp[:8]}/{name}", name + ".part")
                os.replace(name + ".part", name)
                break
            except OSError as e:
                if attempt == 4: raise
                print(f"  retry {attempt + 1} {name}: {e}", flush=True); time.sleep(5 * (attempt + 1))
    return name
cache = {}
def grid(prod, t):
    k = (prod, t)
    if k not in cache:
        d = mesh_lib.read_crop(fetch(prod, t), *BOX)
        cache[k] = [v for row in d["mm"] for v in row]
    return cache[k]
def hourly_max(end):
    out = None
    for h in range(24):
        g = grid("MESH_Max_60min_00.50", end - dt.timedelta(hours=h))
        out = list(g) if out is None else [a if a >= b else b for a, b in zip(out, g)]
    return out
def compare(a, b):
    n = len(a); eq = sum(abs(x - y) < 0.05 for x, y in zip(a, b))
    big_a = {i for i, v in enumerate(a) if v >= IN}; big_b = {i for i, v in enumerate(b) if v >= IN}
    worst = max(abs(x - y) for x, y in zip(a, b))
    return dict(cells=n, equal=eq, pct=round(100 * eq / n, 3), worst_mm=round(worst, 2),
                big_1440=len(big_a), big_ref=len(big_b), big_only_1440=len(big_a - big_b), big_only_ref=len(big_b - big_a))
U = dt.timezone.utc
print("== A. which end does the stamp mark?  2026-06-08, hail 21:02-22:00 UTC", flush=True)
for hh in (20, 21, 22, 23):
    T = dt.datetime(2026, 6, 8, hh, 0, tzinfo=U)
    g1440 = grid("MESH_Max_1440min_00.50", T)
    for off in (-1, 0, 1):
        ref = hourly_max(T + dt.timedelta(hours=off))
        print(f"1440@{T:%H:%M}Z vs 24h-hourly ending {off:+d}h:", compare(g1440, ref), flush=True)
print("== B. Denver midnight stamps (MDT: 06:00Z next day)", flush=True)
for day in (dt.date(2026, 6, 8), dt.date(2023, 5, 10)):
    T = dt.datetime(day.year, day.month, day.day, 6, 0, tzinfo=U) + dt.timedelta(days=1)
    g1440 = grid("MESH_Max_1440min_00.50", T)
    print(f"Denver day {day}: 1440@{T:%Y-%m-%d %H:%MZ}:", compare(g1440, hourly_max(T)), flush=True)
