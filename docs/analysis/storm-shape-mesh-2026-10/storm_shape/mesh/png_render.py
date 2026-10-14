"""Tiny stdlib raster renderer (no matplotlib on this machine; installing it needs the user's OK). Throwaway.
Draws filled/outlined polygons from WKT, MESH cells, points, scale bar. No text (no fonts): legend is colour swatches, keyed in summary.md."""
import math, re, struct, zlib

class Canvas:
    def __init__(self, w, h, bg=(255, 255, 255)):
        self.w, self.h = w, h; self.px = bytearray(bytes(bg) * (w * h))
    def set(self, x, y, c, a=1.0):
        if 0 <= x < self.w and 0 <= y < self.h:
            o = (y * self.w + x) * 3
            if a >= 1: self.px[o:o + 3] = bytes(c)
            else:
                p = self.px; p[o] = int(p[o] * (1 - a) + c[0] * a); p[o + 1] = int(p[o + 1] * (1 - a) + c[1] * a); p[o + 2] = int(p[o + 2] * (1 - a) + c[2] * a)
    def line(self, x0, y0, x1, y1, c, wd=1, dash=0):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0); sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1; err = dx + dy; n = 0
        while True:
            if not dash or (n // dash) % 2 == 0:
                for ox in range(-(wd // 2), wd - wd // 2):
                    for oy in range(-(wd // 2), wd - wd // 2): self.set(x0 + ox, y0 + oy, c)
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
            n += 1
    def rect(self, x0, y0, x1, y1, c, a=1.0):
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1): self.set(x, y, c, a)
    def disc(self, cx, cy, r, c, outline=None):
        for y in range(int(cy - r - 1), int(cy + r + 2)):
            for x in range(int(cx - r - 1), int(cx + r + 2)):
                d = math.hypot(x - cx, y - cy)
                if d <= r: self.set(x, y, c)
                elif outline and d <= r + 1.2: self.set(x, y, outline)
    def fill_poly(self, rings, c, a=0.4):   # even-odd across all rings (holes work)
        ys = [p[1] for r in rings for p in r]
        if not ys: return
        for y in range(max(0, int(min(ys))), min(self.h - 1, int(max(ys))) + 1):
            xs = []
            yc = y + 0.5
            for r in rings:
                for (xa, ya), (xb, yb) in zip(r, r[1:] + r[:1]):
                    if (ya <= yc < yb) or (yb <= yc < ya): xs.append(xa + (yc - ya) / (yb - ya) * (xb - xa))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                for x in range(int(math.ceil(xs[i])), int(xs[i + 1]) + 1): self.set(x, y, c, a)
    def outline(self, rings, c, wd=1, dash=0):
        for r in rings:
            for (xa, ya), (xb, yb) in zip(r, r[1:] + r[:1]): self.line(xa, ya, xb, yb, c, wd, dash)
    def png(self, path):
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))
        def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
        open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

def wkt_rings(wkt):
    """All rings (exterior and holes) of a POLYGON / MULTIPOLYGON WKT as lists of (lon, lat)."""
    rings = []
    for m in re.finditer(r"\(([-\d.eE+ ,]+)\)", wkt):
        pts = [tuple(map(float, p.split())) for p in m.group(1).split(",")]
        if len(pts) >= 4: rings.append(pts[:-1])
    return rings

def make_projection(bbox, width_px):
    lon0, lat0, lon1, lat1 = bbox; latm = (lat0 + lat1) / 2
    wkm = (lon1 - lon0) * math.cos(math.radians(latm)); hkm = lat1 - lat0
    h = int(width_px * hkm / wkm)
    return h, (lambda lon, lat: ((lon - lon0) / (lon1 - lon0) * width_px, (lat1 - lat) / (lat1 - lat0) * h))
