from __future__ import annotations

import os
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import http_client
from utils.text import parse_float, parse_int


class PAPScraper(BaseScraper):
    source = "pap"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        return [
            f"https://www.pap.fr/annonce/locations?{urlencode({'q': term})}",
            f"https://www.pap.fr/annonce/vente-immobiliere?{urlencode({'q': term})}",
            f"https://www.pap.fr/?{urlencode({'q': term})}",
        ]

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        cards = []
        cookie = os.getenv("PAP_COOKIE", "").strip()
        extra_headers = {"Cookie": cookie} if cookie else None
        for url in self._candidate_urls(city, postal_code):
            resp = http_client.get(
                url,
                referer="https://www.pap.fr/",
                headers=extra_headers,
                retries=2,
                allow_statuses={403, 404, 429},
            )
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("a.annonce") or soup.select("a.item-annonce") or soup.select("a[href*='/annonces/']")
            if cards:
                break
        if not cards:
            return

        for a in cards:
            href = a.get("href") or ""
            if href.startswith("/"):
                href = "https://www.pap.fr" + href

            title = a.get_text(" ", strip=True)
            price = parse_int(str(a.find(string=lambda s: s and ("\u20ac" in s or "eur" in s.lower()))) or None)
            surface = parse_float(str(a.find(string=lambda s: s and ("m2" in s.lower() or "m\u00b2" in s.lower()))) or None)
            loc = a.find(string=lambda s: s and ("(" in s and ")" in s))

            raw_id = href.rstrip("/").split("-")[-1] if href else ""
            external_id = raw_id or hash_str(href or (title or ""))

            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                surface_m2=surface,
                location=str(loc) if loc else None,
            )
