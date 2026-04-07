from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import http_client
from utils.text import parse_float, parse_int


class EntreParticuliersScraper(BaseScraper):
    source = "entreparticuliers"

    def _slug(self, txt: str) -> str:
        s = txt.lower().strip()
        s = re.sub(r"[àâä]", "a", s)
        s = re.sub(r"[éèêë]", "e", s)
        s = re.sub(r"[îï]", "i", s)
        s = re.sub(r"[ôö]", "o", s)
        s = re.sub(r"[ùûü]", "u", s)
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        urls = ["https://www.entreparticuliers.com/annonces-immobilieres/location"]
        if city and postal_code:
            urls.append(
                f"https://www.entreparticuliers.com/annonces-immobilieres/location/{self._slug(city)}-{postal_code}"
            )
        if city and not postal_code:
            urls.append(
                f"https://www.entreparticuliers.com/annonces-immobilieres/location/{self._slug(city)}"
            )
        if postal_code and len(postal_code) >= 2:
            dep = postal_code[:2]
            urls.append(f"https://www.entreparticuliers.com/annonces-immobilieres/location/{dep}")
        return urls

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        links: dict[str, str] = {}
        for url in self._candidate_urls(city, postal_code):
            resp = http_client.get(
                url,
                referer="https://www.entreparticuliers.com/",
                allow_statuses={403, 404, 429},
                retries=2,
            )
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.select("a[href*='/annonces-immobilieres/']"):
                href = a.get("href") or ""
                if "/ref-" not in href:
                    continue
                full = urljoin("https://www.entreparticuliers.com", href)
                txt = " ".join(a.get_text(" ", strip=True).split())
                if not txt:
                    # Some anchors are image wrappers; text is in sibling anchor.
                    continue
                if full not in links:
                    links[full] = txt
            if links:
                break

        for href, txt in list(links.items())[:80]:
            title = " ".join(txt.split()) if txt else None
            price = None
            surface = None
            m_price = re.search(r"(\d[\d\s]{2,})\s*€", txt)
            if m_price:
                price = parse_int(m_price.group(1))
            m_surface = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:²|2)", txt, re.IGNORECASE)
            if m_surface:
                surface = parse_float(m_surface.group(1))
            rooms = None
            m_rooms = re.search(r"(\d+)\s*pi[eè]ces?", txt, re.IGNORECASE)
            if m_rooms:
                rooms = parse_int(m_rooms.group(1))
            elif "studio" in txt.lower():
                rooms = 1

            m_loc = re.search(r"([A-Za-zÀ-ÿ\-\s]+\(\d{5}\))", txt)
            location = m_loc.group(1).strip() if m_loc else None

            m_id = re.search(r"ref-(\d+)", href)
            external_id = m_id.group(1) if m_id else hash_str(href)

            yield ScrapedListing(
                source=self.source,
                external_id=external_id,
                url=href,
                title=title,
                price=price,
                surface_m2=surface,
                location=location,
                rooms=rooms,
            )
