from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from config import settings
from .cache import TTLCache


_geo_cache = TTLCache(ttl_seconds=24 * 3600)
_geocoder: Optional[Nominatim] = None


def _get_geocoder() -> Optional[Nominatim]:
    global _geocoder
    if not settings.enable_geocoding:
        return None
    if _geocoder is None:
        _geocoder = Nominatim(user_agent=settings.geocoding_user_agent)
    return _geocoder


@dataclass
class Point:
    lat: float
    lon: float


def geocode(text: str) -> Optional[Point]:
    g = _get_geocoder()
    if not g:
        return None
    key = f"geocode:{text.lower()}"
    cached = _geo_cache.get(key)
    if cached:
        return cached
    loc = g.geocode(text)
    if not loc:
        return None
    pt = Point(lat=loc.latitude, lon=loc.longitude)
    _geo_cache.set(key, pt)
    return pt


def distance_km(a: Point, b: Point) -> float:
    return geodesic((a.lat, a.lon), (b.lat, b.lon)).km