from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from database import models as m
from scraper.base import ScrapedListing
from utils.geo import geocode, distance_km


@dataclass
class MatchResult:
    matched: bool
    score_label: str = ""
    score_value: float = 0.0


def _within_radius(listing_loc: Optional[str], city: Optional[str], radius_km: Optional[float]) -> bool:
    if not city or not radius_km:
        return True
    if not listing_loc:
        return False
    # Fast coarse match first to avoid unnecessary geocoding calls.
    if city.lower() in listing_loc.lower():
        return True
    a = geocode(city)
    b = geocode(listing_loc)
    if not a or not b:
        return city.lower() in listing_loc.lower()
    return distance_km(a, b) <= radius_km


def match_and_score(f: m.Filter, l: ScrapedListing) -> MatchResult:
    if f.price_min is not None and l.price is not None and l.price < f.price_min:
        return MatchResult(matched=False)
    if f.price_max is not None and l.price is not None and l.price > f.price_max:
        return MatchResult(matched=False)
    if f.surface_min is not None and l.surface_m2 is not None and l.surface_m2 < f.surface_min:
        return MatchResult(matched=False)
    if f.rooms_min is not None and l.rooms is not None and l.rooms < f.rooms_min:
        return MatchResult(matched=False)
    if f.city and not _within_radius(l.location, f.city, f.radius_km):
        return MatchResult(matched=False)
    if f.postal_code and f.postal_code not in (l.location or ""):
        return MatchResult(matched=False)

    score_label = "Bon prix"
    score_value = 0.5

    if l.price_per_m2 and f.price_max and f.surface_min and f.surface_min > 0:
        expected_ppm2 = f.price_max / max(f.surface_min, 1)
        ratio = l.price_per_m2 / max(expected_ppm2, 1)
        if ratio <= 0.8:
            score_label = "Tres bonne affaire"
            score_value = 0.9
        elif ratio <= 1.1:
            score_label = "Bon prix"
            score_value = 0.7
        else:
            score_label = "Cher"
            score_value = 0.3
    elif l.price_per_m2:
        if l.price_per_m2 < 2_500:
            score_label = "Tres bonne affaire"
            score_value = 0.9
        elif l.price_per_m2 < 4_500:
            score_label = "Bon prix"
            score_value = 0.7
        else:
            score_label = "Cher"
            score_value = 0.3

    return MatchResult(matched=True, score_label=score_label, score_value=score_value)
