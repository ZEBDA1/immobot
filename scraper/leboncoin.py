from __future__ import annotations

import os
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import http_client
from utils.text import parse_int


class LeboncoinScraper(BaseScraper):
    source = "leboncoin"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        return [
            f"https://www.leboncoin.fr/recherche?{urlencode({'category': '9', 'locations': term, 'owner_type': 'pro,private', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'category': '10', 'locations': term, 'owner_type': 'pro,private', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'text': term, 'category': '9', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'text': term, 'category': '10', 'sort': 'time'})}",
        ]

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        cards = []
        cookie = os.getenv("LEBONCOIN_COOKIE", "").strip()
        extra_headers = {"Cookie": cookie} if cookie else None
        for url in self._candidate_urls(city, postal_code):
            resp = http_client.get(
                url,
                referer="https://www.leboncoin.fr/",
                headers=extra_headers,
                retries=2,
                allow_statuses={403, 404, 429},
            )
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("a[data-qa-id='aditem_container']") or soup.select("a[data-test-id='ad-card-link']")
            if cards:
                break
        if not cards:
            return

        for a in cards:
            href = a.get("href") or ""
            if href.startswith("/"):
                href = "https://www.leboncoin.fr" + href

            title_node = a.select_one("p[data-qa-id='aditem_title']") or a.select_one("p[data-test-id='ad-title']")
            price_node = a.select_one("span[data-qa-id='aditem_price']") or a.select_one("p[data-test-id='price']")
            loc_node = a.select_one("p[data-qa-id='aditem_location']") or a.select_one("p[data-test-id='location']")
            desc_node = a.select_one("p[data-qa-id='aditem_description']") or a.select_one("p[data-test-id='ad-description']")

            title = title_node.get_text(strip=True) if title_node else None
            price = parse_int(price_node.get_text(strip=True) if price_node else None)
            loc = loc_node.get_text(strip=True) if loc_node else None
            desc = desc_node.get_text(strip=True) if desc_node else None

            raw_id = href.rstrip("/").split("-")[-1] if href else ""
            external_id = raw_id or hash_str(href or (title or ""))

            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                location=loc,
                description=desc,
            )
