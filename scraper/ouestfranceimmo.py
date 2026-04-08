from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import fetch_html, http_client
from utils.text import parse_float, parse_int


class OuestFranceImmoScraper(BaseScraper):
    source = "ouestfranceimmo"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        return [
            f"https://www.ouestfrance-immo.com/louer/?{urlencode({'q': term})}",
            f"https://www.ouestfrance-immo.com/acheter/?{urlencode({'q': term})}",
            "https://www.ouestfrance-immo.com/louer/",
            "https://www.ouestfrance-immo.com/acheter/",
        ]

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        all_listings = []
        for base_url in self._candidate_urls(city, postal_code):
            for page in range(1, 4):  # Fetch up to 3 pages
                url = f"{base_url}&page={page}" if "?" in base_url else f"{base_url}?page={page}"
                html = fetch_html(url, referer="https://www.ouestfrance-immo.com/")
                if not html:
                    break
                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select("article.annonce") or soup.select("div.annonce") or soup.select("a[href*='/annonce/']")
                if not cards:
                    break
                for card in cards:
                    href = card.get("href") if card.name == "a" else card.select_one("a")["href"] if card.select_one("a") else None
                    if not href:
                        continue
                    full_url = urljoin("https://www.ouestfrance-immo.com", href)
                    title = card.get_text(" ", strip=True) or ""
                    price = parse_int(str(card.find(string=lambda s: s and "€" in s)))
                    surface = parse_float(str(card.find(string=lambda s: s and ("m2" in s.lower() or "m²" in s))))
                    location = card.find(string=lambda s: s and ("(" in s and ")" in s))
                    external_id = full_url.rstrip("/").split("/")[-1] or hash_str(full_url)
                    all_listings.append(ScrapedListing(
                        source=self.source,
                        external_id=external_id,
                        url=full_url,
                        title=title,
                        price=price,
                        surface_m2=surface,
                        location=str(location) if location else None,
                    ))
                if len(all_listings) >= 50:  # Limit per source
                    break
            if all_listings:
                break
        for listing in all_listings:
            yield listing
