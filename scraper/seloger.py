from __future__ import annotations

from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.http import http_client, get_with_playwright
from utils.text import parse_int, parse_float


class SeLogerScraper(BaseScraper):
    source = "seloger"

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        # Public search page; selectors subject to change.
        q = postal_code or city or ""
        params = {"q": q}
        url = f"https://www.seloger.com/list.htm?{urlencode(params)}"
        r = http_client.get(url)
        soup = BeautifulSoup(r.text, "lxml")
        cards = soup.select(".c-pa-list .c-pa-list_item a")
        if not cards:
            html = get_with_playwright(url)
            if html:
                soup = BeautifulSoup(html, "lxml")
                cards = soup.select(".c-pa-list .c-pa-list_item a")
        for a in cards:
            href = a.get("href") or ""
            title = a.get_text(strip=True)
            # attempt to find price and details in nearby nodes
            price_txt = a.find_next(string=lambda s: s and "€" in s)
            price = parse_int(str(price_txt) if price_txt else None)
            details = a.find_next("ul")
            surface = None
            rooms = None
            if details:
                txt = details.get_text(" ", strip=True)
                surface = parse_float(txt)
                rooms = parse_int(txt)
            external_id = href.rstrip("/").split("-")[-1] if href else ""
            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                surface_m2=surface,
                rooms=rooms,
            )