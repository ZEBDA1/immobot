from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.http import fetch_html
from utils.text import parse_float, parse_int


def _slugify_city(city: str) -> str:
    s = city.strip().lower()
    s = re.sub(r"[^a-z0-9\-\s]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s


class RentolaScraper(BaseScraper):
    source = "rentola"

    def _search_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        base = "https://rentola.fr"
        urls: list[str] = []
        if city:
            urls.append(f"{base}/location/{_slugify_city(city)}")
        if postal_code and postal_code.isdigit():
            urls.append(f"{base}/location/{quote(postal_code)}")
        if not urls:
            urls.append(f"{base}/location")
        return urls

    def _extract_cards(self, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/annonces/'], a[href*='/listings/'], a[href*='/property/']"):
            href = a.get("href") or ""
            if not href:
                continue
            full = urljoin("https://rentola.fr", href)
            if full in seen:
                continue
            seen.add(full)
            urls.append(full)
        return urls

    def _parse_detail(self, url: str) -> Optional[ScrapedListing]:
        html = fetch_html(url, referer="https://rentola.fr/")
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        title = (soup.select_one("h1") or soup.title)
        title_txt = title.get_text(" ", strip=True) if title else None
        # price and surface/rooms heuristics from page text
        price_txt = soup.find(string=lambda s: isinstance(s, str) and ("€" in s or "EUR" in s))
        price = parse_int(price_txt)
        whole = soup.get_text(" ", strip=True)
        m_surf = re.search(r"(\d+[\.,]?\d*)\s*m(?:2|²)\b", whole, re.IGNORECASE)
        surface = parse_float(m_surf.group(1)) if m_surf else None
        m_rooms = re.search(r"\b(\d+)\s*pi[eè]ces?\b|\bT(\d)\b", whole, re.IGNORECASE)
        rooms = None
        if m_rooms:
            val = m_rooms.group(1) or m_rooms.group(2)
            try:
                rooms = int(val)
            except Exception:
                rooms = None
        loc_meta = soup.select_one("meta[property='og:locale']")
        location = None
        # Try a more explicit location field if present
        for sel in [".address", ".location", "[data-testid='listing-location']"]:
            node = soup.select_one(sel)
            if node:
                location = node.get_text(" ", strip=True)
                break

        imgs: list[str] | None = None
        for m in soup.select("meta[property='og:image']"):
            u = m.get("content")
            if u:
                imgs = imgs or []
                imgs.append(u)

        external_id = url.rstrip("/").split("/")[-1]
        return ScrapedListing(
            source=self.source,
            external_id=external_id,
            url=url,
            title=title_txt,
            price=price,
            surface_m2=surface,
            rooms=rooms,
            location=location,
            images=imgs,
        )

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        for u in self._search_urls(city, postal_code):
            html = fetch_html(u, referer="https://rentola.fr/")
            if not html:
                continue
            for href in self._extract_cards(html)[:25]:
                l = self._parse_detail(href)
                if l:
                    yield l
