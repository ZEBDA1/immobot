from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional


@dataclass
class ScrapedListing:
    source: str
    external_id: str
    url: str
    title: Optional[str] = None
    price: Optional[int] = None
    surface_m2: Optional[float] = None
    price_per_m2: Optional[float] = None
    location: Optional[str] = None
    rooms: Optional[int] = None
    description: Optional[str] = None
    images: Optional[list[str]] = None
    published_at: Optional[datetime] = None
    db_id: Optional[int] = None


class BaseScraper:
    source: str

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        raise NotImplementedError
