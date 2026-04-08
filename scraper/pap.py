from __future__ import annotations

import os
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import http_client, fetch_html
from utils.text import parse_float, parse_int


class PAPScraper(BaseScraper):
    source = "pap"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        return [
            f"https://www.pap.fr/annonce/locations?{urlencode({'q': term})}",
            f"https://www.pap.fr/annonce/vente-immobiliere?{urlencode({'q': term})}",
            f"https://www.pap.fr/?{urlencode({'q': term})}",
            "https://www.pap.fr/annonce/locations",
            "https://www.pap.fr/annonce/vente-immobiliere",
        ]

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        cookie = os.getenv("PAP_COOKIE", "").strip()
        extra_headers = {"Cookie": cookie} if cookie else None
        all_cards = []
        for base_url in self._candidate_urls(city, postal_code):
            for page in range(1, 6):  # Fetch up to 5 pages to maximize listings
                url = f"{base_url}&page={page}" if "?" in base_url else f"{base_url}?page={page}"
                html = None
                try:
                    resp = http_client.get(
                        url,
                        referer="https://www.pap.fr/",
                        headers=extra_headers,
                        retries=2,
                        allow_statuses={403, 404, 429},
                    )
                    if resp.status_code == 200:
                        html = resp.text
                except Exception:
                    html = None

                if not html:
                    html = fetch_html(url, referer="https://www.pap.fr/")

                if not html:
                    break

                soup = BeautifulSoup(html, "html.parser")
                cards = soup.select("a.annonce") or soup.select("a.item-annonce") or soup.select("a[href*='/annonces/']")
                if not cards:
                    break  # No more listings
                all_cards.extend(cards)
                if len(all_cards) >= 100:  # Limit to 100 per source to avoid overload
                    break
            if all_cards:
                break
        if not all_cards:
            return

        for a in all_cards:
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
