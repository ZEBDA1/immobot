from __future__ import annotations

from typing import Iterable, Optional
from urllib.parse import urlencode, urljoin
import re

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.http import fetch_html
from utils.text import parse_float, parse_int


class LogicImmoScraper(BaseScraper):
    source = "logicimmo"

    def _search_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = (postal_code or city or "").strip()
        # Fallback to generic listing when no term
        base = "https://www.logic-immo.com/location-immobilier"
        if not term:
            return [base]
        return [
            f"{base}?{urlencode({'loc': term})}",
            f"{base}?{urlencode({'loc': term, 'order': 'date_desc'})}",
        ]

    def _extract_cards(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        # Try several selectors as the site evolves frequently
        for a in soup.select("a[data-testid='linkToDetail'], a[href*='/location-immobilier/']"):
            href = a.get("href") or ""
            if not href:
                continue
            full = urljoin("https://www.logic-immo.com", href)
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _parse_detail(self, url: str) -> Optional[ScrapedListing]:
        html = fetch_html(url, referer="https://www.logic-immo.com/")
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        title_node = soup.select_one("h1")
        title = title_node.get_text(" ", strip=True) if title_node else None
        price_node = soup.find(string=lambda s: isinstance(s, str) and ("€" in s or "EUR" in s))
        price = parse_int(price_node)
        # Surface and rooms from detail bullets
        details_txt = soup.get_text(" ", strip=True)
        surface = None
        m_s = re.search(r"(\d+[\.,]?\d*)\s*m(?:²|2)", details_txt, re.IGNORECASE)
        if m_s:
            surface = parse_float(m_s.group(1))
        rooms = None
        m_r = re.search(r"(\d+)\s*pi[eè]ces?", details_txt, re.IGNORECASE)
        if m_r:
            try:
                rooms = int(m_r.group(1))
            except Exception:
                rooms = None
        loc = None
        loc_node = soup.find(string=lambda s: isinstance(s, str) and ("/" in s and "-" in s))
        if loc_node:
            loc = str(loc_node).strip()

        images: list[str] | None = None
        for m in soup.select("meta[property='og:image']"):
            u = m.get("content")
            if u:
                images = images or []
                images.append(u)

        external_id = url.rstrip("/").split("-")[-1]
        if not external_id:
            external_id = url

        return ScrapedListing(
            source=self.source,
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            surface_m2=surface,
            rooms=rooms,
            location=loc,
            images=images,
        )

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        # Fetch listing pages, then hydrate a subset of details for accuracy
        for url in self._search_urls(city, postal_code):
            html = fetch_html(url, referer="https://www.logic-immo.com/")
            if not html:
                continue
            for href in self._extract_cards(html)[:20]:
                l = self._parse_detail(href)
                if l:
                    yield l
