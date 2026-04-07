from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import logging
import time

from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderServiceError, GeocoderTimedOut, GeocoderUnavailable, GeocoderInsufficientPrivileges

from config import settings
from .cache import TTLCache


_geo_cache = TTLCache(ttl_seconds=24 * 3600)
_geocoder: Optional[Nominatim] = None
_MISS = "__MISS__"
_geocoding_blocked_until = 0.0
_GEO_BLOCK_SECONDS = 60 * 60

# Prevent geopy informational noise in normal operation.
logging.getLogger("geopy").setLevel(logging.WARNING)


def _get_geocoder() -> Optional[Nominatim]:
    global _geocoder
    if not settings.enable_geocoding:
        return None
    if _geocoder is None:
        # Nominatim can be slow or rate-limited; use a safer timeout.
        _geocoder = Nominatim(user_agent=settings.geocoding_user_agent, timeout=4)
    return _geocoder


@dataclass
class Point:
    lat: float
    lon: float


def geocode(text: str) -> Optional[Point]:
    global _geocoding_blocked_until
    g = _get_geocoder()
    if not g:
        return None
    if _geocoding_blocked_until > time.time():
        return None
    key = f"geocode:{text.lower()}"
    cached = _geo_cache.get(key)
    if cached == _MISS:
        return None
    if cached:
        return cached
    try:
        loc = g.geocode(text, exactly_one=True, timeout=4)
    except GeocoderInsufficientPrivileges:
        # Nominatim denied this client (403). Stop hammering for a while.
        _geocoding_blocked_until = time.time() + _GEO_BLOCK_SECONDS
        _geo_cache.set(key, _MISS)
        return None
    except (GeocoderTimedOut, GeocoderUnavailable, GeocoderServiceError, OSError):
        # Never propagate geocoding failures to scheduler/scrapers.
        _geo_cache.set(key, _MISS)
        return None
    except Exception:
        _geo_cache.set(key, _MISS)
        return None
    if not loc:
        _geo_cache.set(key, _MISS)
        return None
    pt = Point(lat=loc.latitude, lon=loc.longitude)
    _geo_cache.set(key, pt)
    return pt


def distance_km(a: Point, b: Point) -> float:
    return geodesic((a.lat, a.lon), (b.lat, b.lon)).km
