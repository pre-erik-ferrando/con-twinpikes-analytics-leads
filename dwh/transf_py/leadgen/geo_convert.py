"""Official boundaries in a GeoPackage -> GeoJSON in WGS84 lon/lat, ready for `load-geo` (DT.4.1).

Germany (BKG VG250) and Switzerland (swisstopo swissBOUNDARIES3D) publish their boundaries as
GeoPackages in projected coordinates: ETRS89 / UTM 32N (EPSG:25832) and LV95 (EPSG:2056). This reads
the GeoPackage with sqlite3 and converts with the published formulas, so no GIS library is needed:
- UTM: Krüger series to n^4 on GRS80 (sub-millimetre); ETRS89 is taken as WGS84 (under 1 m apart).
- LV95: swisstopo's approximate formulas (about 1 m), well under the 100 m of the outlines sent to Apify.
Z coordinates (swissBOUNDARIES3D) are dropped.

The rows come from a SQL query over the GeoPackage that returns geom, code and name, so one file can mix
layers (Swiss districts plus the cantons without districts) and filter (VG250's land areas, GF = 4).
"""
from __future__ import annotations

import json
import math
import sqlite3
import struct
from pathlib import Path

# ---------------------------------------------------------------------------- coordinates
_A, _F = 6378137.0, 1 / 298.257222101          # GRS80
_N = _F / (2 - _F)
_RECT = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)
_BETA = (_N / 2 - 2 * _N ** 2 / 3 + 37 * _N ** 3 / 96 - _N ** 4 / 360,
         _N ** 2 / 48 + _N ** 3 / 15 - 437 * _N ** 4 / 1440,
         17 * _N ** 3 / 480 - 37 * _N ** 4 / 840,
         4397 * _N ** 4 / 161280)
_DELTA = (2 * _N - 2 * _N ** 2 / 3 - 2 * _N ** 3 + 116 * _N ** 4 / 45,
          7 * _N ** 2 / 3 - 8 * _N ** 3 / 5 - 227 * _N ** 4 / 45,
          56 * _N ** 3 / 15 - 136 * _N ** 4 / 35,
          4279 * _N ** 4 / 630)


def utm_to_lonlat(e: float, n: float, zone: int = 32) -> tuple[float, float]:
    """ETRS89 / UTM (northern hemisphere) -> lon, lat in degrees."""
    xi = n / (0.9996 * _RECT)
    eta = (e - 500000) / (0.9996 * _RECT)
    xi1 = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    eta1 = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(_BETA, 1))
    chi = math.asin(math.sin(xi1) / math.cosh(eta1))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(_DELTA, 1))
    lon0 = math.radians(zone * 6 - 183)
    lon = lon0 + math.atan2(math.sinh(eta1), math.cos(xi1))
    return math.degrees(lon), math.degrees(lat)


def lv95_to_lonlat(e: float, n: float) -> tuple[float, float]:
    """Swiss LV95 (EPSG:2056) -> WGS84 lon, lat in degrees (swisstopo's approximate formulas, ~1 m)."""
    y, x = (e - 2600000) / 1e6, (n - 1200000) / 1e6
    lon = 2.6779094 + 4.728982 * y + 0.791484 * y * x + 0.1306 * y * x ** 2 - 0.0436 * y ** 3
    lat = 16.9023892 + 3.238272 * x - 0.270978 * y ** 2 - 0.002528 * x ** 2 - 0.0447 * y ** 2 * x - 0.0140 * x ** 3
    return lon * 100 / 36, lat * 100 / 36


CRS = {25832: lambda e, n: utm_to_lonlat(e, n, 32), 2056: lv95_to_lonlat}


# ---------------------------------------------------------------------------- geometry
_ENVELOPE = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _wkb(buf: bytes, pos: int = 0):
    """WKB (ISO or EWKB, 2-4 dimensions) Polygon / MultiPolygon -> (GeoJSON-like rings, next position)."""
    order = "<" if buf[pos] == 1 else ">"
    (gtype,) = struct.unpack_from(order + "I", buf, pos + 1)
    pos += 5
    dims = 2
    if gtype & 0x80000000 or gtype & 0x40000000:           # EWKB Z / M flags
        dims += bool(gtype & 0x80000000) + bool(gtype & 0x40000000)
        if gtype & 0x20000000:                             # EWKB SRID
            pos += 4
        gtype &= 0x0FFFFFFF
    else:                                                  # ISO: 1000 Z, 2000 M, 3000 ZM
        dims += {0: 0, 1: 1, 2: 1, 3: 2}[gtype // 1000]
        gtype %= 1000
    if gtype == 3:
        (nrings,) = struct.unpack_from(order + "I", buf, pos)
        pos += 4
        rings = []
        for _ in range(nrings):
            (npts,) = struct.unpack_from(order + "I", buf, pos)
            pos += 4
            vals = struct.unpack_from(order + "d" * (npts * dims), buf, pos)
            pos += 8 * npts * dims
            rings.append([(vals[i], vals[i + 1]) for i in range(0, len(vals), dims)])
        return [rings], pos
    if gtype == 6:
        (nparts,) = struct.unpack_from(order + "I", buf, pos)
        pos += 4
        polys = []
        for _ in range(nparts):
            part, pos = _wkb(buf, pos)
            polys.extend(part)
        return polys, pos
    raise ValueError(f"geometry type {gtype}: only Polygon and MultiPolygon")


def gpkg_polygons(blob: bytes) -> list:
    """A GeoPackage geometry blob -> list of polygons, each a list of rings of (x, y)."""
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    flags = blob[3]
    if flags & 0x10:                                       # empty geometry
        return []
    polys, _ = _wkb(blob, 8 + _ENVELOPE[(flags >> 1) & 0x07])
    return polys


def convert(gpkg: Path, sql: str, crs: int, decimals: int = 6) -> dict:
    """Run `sql` (returning geom, code, name) on the GeoPackage -> a FeatureCollection in lon/lat."""
    to_lonlat = CRS[crs]
    con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
    features = []
    for blob, code, name in con.execute(sql):
        polys = [[[[round(v, decimals) for v in to_lonlat(x, y)] for x, y in ring] for ring in poly]
                 for poly in gpkg_polygons(blob)]
        code = str(code).removesuffix(".0")
        features.append({"type": "Feature", "properties": {"code": code, "name": name},
                         "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    con.close()
    return {"type": "FeatureCollection", "features": features}


def write(gpkg: Path, out: Path, sql: str, crs: int) -> int:
    data = convert(gpkg, sql, crs)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(data["features"])
