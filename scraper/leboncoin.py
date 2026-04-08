from __future__ import annotations

import os
from bs4 import BeautifulSoup
from urllib.parse import urlencode
from typing import Iterable, Optional

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import http_client, fetch_html
from utils.text import parse_int


class LeboncoinScraper(BaseScraper):
    source = "leboncoin"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        urls = [
            f"https://www.leboncoin.fr/recherche?{urlencode({'category': '9', 'locations': term, 'owner_type': 'pro,private', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'category': '10', 'locations': term, 'owner_type': 'pro,private', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'text': term, 'category': '9', 'sort': 'time'})}",
            f"https://www.leboncoin.fr/recherche?{urlencode({'text': term, 'category': '10', 'sort': 'time'})}",
            "https://www.leboncoin.fr/recherche?category=9&sort=time",
            "https://www.leboncoin.fr/recherche?category=10&sort=time",
        ]
        return urls

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        cookie = os.getenv("LEBONCOIN_COOKIE", "").strip()
        extra_headers = {"Cookie": cookie} if cookie else None
        all_cards = []
        for base_url in self._candidate_urls(city, postal_code):
            for page in range(1, 6):  # Fetch up to 5 pages to maximize listings
                url = f"{base_url}&page={page}" if "?" in base_url else f"{base_url}?page={page}"
                cards = []
                # First try with direct HTTP + optional cookie
                try:
                    resp = http_client.get(
                        url,
                        referer="https://www.leboncoin.fr/",
                        headers=extra_headers,
                        retries=2,
                        allow_statuses={403, 404, 429},
                    )
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        cards = soup.select("a[data-qa-id='aditem_container']") or soup.select("a[data-test-id='ad-card-link']")
                        if not cards:
                            # Try more generic anchors when structure changes
                            cards = soup.select("a[href*='/vi/']") or soup.select("a[href*='/ad/']")
                except Exception:
                    pass

                if not cards:
                    # Fallback to dynamic fetching (Selenium/Playwright) if blocked
                    html = fetch_html(url, referer="https://www.leboncoin.fr/")
                    if html:
                        soup = BeautifulSoup(html, "html.parser")
                        cards = soup.select("a[data-qa-id='aditem_container']") or soup.select("a[data-test-id='ad-card-link']")
                        if not cards:
                            cards = soup.select("a[href*='/vi/']") or soup.select("a[href*='/ad/']")

                if not cards:
                    break  # No more listings on this page
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
                href = "https://www.leboncoin.fr" + href

            title_node = a.select_one("p[data-qa-id='aditem_title']") or a.select_one("p[data-test-id='ad-title']") or a.find("h2")
            price_node = a.select_one("span[data-qa-id='aditem_price']") or a.select_one("p[data-test-id='price']") or a.find(string=lambda s: s and "€" in s)
            loc_node = a.select_one("p[data-qa-id='aditem_location']") or a.select_one("p[data-test-id='location']")
            desc_node = a.select_one("p[data-qa-id='aditem_description']") or a.select_one("p[data-test-id='ad-description']")

            title = title_node.get_text(strip=True) if hasattr(title_node, "get_text") else (str(title_node).strip() if title_node else None)
            price_txt = price_node.get_text(strip=True) if hasattr(price_node, "get_text") else (str(price_node).strip() if price_node else None)
            price = parse_int(price_txt)
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
