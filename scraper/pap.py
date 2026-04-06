from __future__ import annotations

from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.http import http_client, get_with_playwright
from utils.text import parse_int, parse_float


class PAPScraper(BaseScraper):
    source = "pap"

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        term = postal_code or city or ""
        params = {"q": term}
        url = f"https://www.pap.fr/annonce/locations?{urlencode(params)}"
        r = http_client.get(url)
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select("a.annonce")
        if not cards:
            html = get_with_playwright(url)
            if html:
                soup = BeautifulSoup(html, "lxml")
                cards = soup.select("a.annonce")
        for a in cards:
            href = a.get("href") or ""
            if href.startswith("/"):
                href = "https://www.pap.fr" + href
            title = a.get_text(strip=True)
            price = parse_int(a.find(string=lambda s: s and "€" in s))
            surface = parse_float(a.find(string=lambda s: s and ("m²" in s or "m2" in s)))
            loc = a.find(string=lambda s: s and ("Paris" in s or "-" in s))
            external_id = href.rstrip("/").split("-")[-1] if href else ""
            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                surface_m2=surface,
                location=str(loc) if loc else None,
            )