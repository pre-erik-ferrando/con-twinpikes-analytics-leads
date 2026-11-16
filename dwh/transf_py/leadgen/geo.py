"""Subregion and municipality from the business's coordinates (DT.4.1, DF.8 "Zona").

Never from the postal code (a postal code can cross several districts: with postal-code lists, 62 of
78 Austrian leads had no zone) nor from the actor's `state` field, which arrives empty.

The polygons are the official boundaries (BKG VG250, Statistik Austria, swisstopo, Statbel, CBS) loaded
as GeoJSON into landing.reference_geo. The lookup is done here, in Python, with a bounding-box
prefilter and even-odd ray casting: no geometry library and no ClickHouse polygon dictionary needed,
and the same function serves the tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

Ring = list[tuple[float, float]]            # (lon, lat)
Polygon = list[Ring]                        # outer ring first, then holes


@dataclass
class AdminArea:
    country: str
    admin_level: str                        # SUBREGION | MUNICIPALITY
    admin_code: str
    admin_name: str
    parent_code: str | None
    polygons: list[Polygon]
    bbox: tuple[float, float, float, float] = field(init=False)

    def __post_init__(self):
        xs = [x for poly in self.polygons for ring in poly for x, _ in ring]
        ys = [y for poly in self.polygons for ring in poly for _, y in ring]
        self.bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)

    def contains(self, lon: float, lat: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            return False
        return any(_in_polygon(lon, lat, poly) for poly in self.polygons)


def _in_ring(x: float, y: float, ring: Ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def _in_polygon(x: float, y: float, poly: Polygon) -> bool:
    if not poly or not _in_ring(x, y, poly[0]):
        return False
    return not any(_in_ring(x, y, hole) for hole in poly[1:])


def polygons_from_geojson(geometry: dict | str) -> list[Polygon]:
    """GeoJSON Polygon or MultiPolygon geometry -> list of polygons of (lon, lat) tuples."""
    if isinstance(geometry, str):
        geometry = json.loads(geometry)
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        coords = [coords]
    elif kind != "MultiPolygon":
        raise ValueError(f"unsupported geometry type {kind!r}")
    return [[[(float(p[0]), float(p[1])) for p in ring] for ring in poly] for poly in coords]


class AreaIndex:
    """The admin areas of one country and level, queried by point."""

    def __init__(self, areas: list[AdminArea]):
        self.areas = areas

    def locate(self, lon: float | None, lat: float | None) -> AdminArea | None:
        if lon is None or lat is None:
            return None
        for area in self.areas:
            if area.contains(lon, lat):
                return area
        return None
