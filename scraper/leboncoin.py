from __future__ import annotations

from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.http import http_client, get_with_playwright
from utils.text import parse_int, parse_float


class LeboncoinScraper(BaseScraper):
    source = "leboncoin"

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        # Basic search URL for real estate. Adjust categories/params as needed.
        params = {
            "category": "immobilier",
            "locations": postal_code or city or "",
            "owner_type": "pro,private",
            "sort": "time",
        }
        url = f"https://www.leboncoin.fr/recherche?{urlencode(params)}"
        r = http_client.get(url)
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select("a[data-qa-id='aditem_container']")
        if not cards:
            html = get_with_playwright(url)
            if html:
                soup = BeautifulSoup(html, "lxml")
                cards = soup.select("a[data-qa-id='aditem_container']")
        for a in cards:
            href = a.get("href") or ""
            if href and href.startswith("/"):
                href = "https://www.leboncoin.fr" + href
            title = (a.select_one("p[data-qa-id='aditem_title']") or {}).get_text(strip=True) if a else None
            price_txt = (a.select_one("span[data-qa-id='aditem_price']") or {}).get_text(strip=True) if a else None
            price = parse_int(price_txt)
            loc = (a.select_one("p[data-qa-id='aditem_location']") or {}).get_text(strip=True) if a else None
            desc = (a.select_one("p[data-qa-id='aditem_description']") or {}).get_text(strip=True) if a else None
            # crude external id from URL
            external_id = href.rstrip("/").split("-")[-1] if href else ""

            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                location=loc,
                description=desc,
            )