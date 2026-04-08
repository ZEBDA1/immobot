from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup
from urllib.parse import urlencode
import requests

from .base import BaseScraper, ScrapedListing
from utils.hash import hash_str
from utils.http import fetch_html, fetch_json_with_playwright
from utils.text import parse_float, parse_int


class SeLogerScraper(BaseScraper):
    source = "seloger"

    def _candidate_urls(self, city: Optional[str], postal_code: Optional[str]) -> list[str]:
        term = postal_code or city or ""
        return [
            f"https://www.seloger.com/list.htm?{urlencode({'q': term})}",
            f"https://www.seloger.com/recherche.htm?{urlencode({'q': term})}",
            f"https://www.seloger.com/?{urlencode({'q': term})}",
            "https://www.seloger.com/list.htm",
            "https://www.seloger.com/classified-search",
        ]

    def _extract_first_place_id(self, data: Any) -> Optional[str]:
        if not isinstance(data, list):
            return None
        if not data:
            return None
        first = data[0] if isinstance(data[0], dict) else None
        if not first:
            return None
        val = first.get("id") or first.get("placeId")
        return str(val) if val else None

    def _search_api(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        term = postal_code or city or ""
        if not term:
            return []

        autocomplete_payload = {
            "text": term,
            "limit": 8,
            "placeTypes": ["NBH1", "NBH2", "NBH3", "AD04", "AD06", "AD08", "AD09", "POCO", "AD02"],
            "parentTypes": ["NBH1", "NBH2", "NBH3", "AD04", "AD06", "AD08", "AD09", "POCO", "AD02"],
            "locale": "fr",
        }
        ac_data = fetch_json_with_playwright(
            "https://www.seloger.com/search-mfe-bff/autocomplete",
            method="POST",
            json_body=autocomplete_payload,
            warmup_url="https://www.seloger.com/",
            timeout_ms=35000,
        )
        if not ac_data:
            ac_data = self._api_with_cookie("https://www.seloger.com/search-mfe-bff/autocomplete", autocomplete_payload)
        place_id = self._extract_first_place_id(ac_data)
        if not place_id:
            return []

        search_payload = {
            "criteria": {
                "distributionTypes": ["Rent"],
                "estateTypes": ["Apartment", "House"],
                "projectTypes": ["Resale", "New_Build", "Projected", "Life_Annuity"],
                "location": {"placeIds": [place_id]},
            },
            "paging": {"page": 1, "size": 100, "order": "Default"},
        }
        search_data = fetch_json_with_playwright(
            "https://www.seloger.com/serp-bff/search",
            method="POST",
            json_body=search_payload,
            warmup_url="https://www.seloger.com/classified-search",
            timeout_ms=35000,
        )
        if not search_data:
            search_data = self._api_with_cookie("https://www.seloger.com/serp-bff/search", search_payload)
        if not isinstance(search_data, dict):
            return []

        classified_rows = search_data.get("classifieds") or []
        ids: list[str] = []
        for row in classified_rows:
            if not isinstance(row, dict):
                continue
            val = row.get("id") or row.get("classifiedId")
            if val:
                ids.append(str(val))
        if not ids:
            return []

        details_data = fetch_json_with_playwright(
            f"https://www.seloger.com/classifiedList/{','.join(ids[:50])}",
            method="GET",
            json_body=None,
            headers={"accept": "*/*", "x-language": "fr"},
            warmup_url="https://www.seloger.com/classified-search",
            timeout_ms=35000,
        )
        if not details_data:
            details_data = self._api_with_cookie(
                f"https://www.seloger.com/classifiedList/{','.join(ids[:50])}",
                None,
                method="GET",
            )
        if not isinstance(details_data, list):
            details_data = classified_rows

        listings: list[ScrapedListing] = []
        for row in details_data:
            if not isinstance(row, dict):
                continue
            ext_id = row.get("id") or row.get("classifiedId")
            if not ext_id:
                continue
            url = row.get("permalink") or row.get("url")
            if isinstance(url, str) and url.startswith("/"):
                url = "https://www.seloger.com" + url
            if not url:
                url = f"https://www.seloger.com/annonces/{ext_id}.htm"
            title = row.get("title") or row.get("subject")

            pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
            price = row.get("price") or pricing.get("price") or pricing.get("amount")
            area = (
                row.get("livingArea")
                or row.get("surface")
                or row.get("area")
                or (row.get("features", {}).get("area") if isinstance(row.get("features"), dict) else None)
            )
            rooms = row.get("rooms") or row.get("numberOfRooms")
            loc = None
            if isinstance(row.get("location"), dict):
                loc = (
                    row["location"].get("label")
                    or row["location"].get("city")
                    or row["location"].get("district")
                )
            if not loc:
                loc = row.get("city")
            desc = row.get("description") or row.get("summary")

            images = None
            medias = row.get("photos") or row.get("images")
            if isinstance(medias, list):
                urls = [m.get("url") if isinstance(m, dict) else m for m in medias]
                images = [u for u in urls if isinstance(u, str) and u]

            listings.append(
                ScrapedListing(
                    source=self.source,
                    external_id=str(ext_id),
                    url=str(url),
                    title=str(title) if title else None,
                    price=parse_int(str(price)) if price is not None else None,
                    surface_m2=parse_float(str(area)) if area is not None else None,
                    location=str(loc) if loc else None,
                    rooms=parse_int(str(rooms)) if rooms is not None else None,
                    description=str(desc) if desc else None,
                    images=images,
                )
            )
        return listings

    def _api_with_cookie(self, url: str, payload: Optional[dict[str, Any]], *, method: str = "POST") -> Optional[Any]:
        raw_cookie = os.getenv("SELOGER_COOKIE", "").strip()
        if not raw_cookie:
            return None

        headers = {
            "accept": "application/json, text/plain, */*",
            "origin": "https://www.seloger.com",
            "referer": "https://www.seloger.com/classified-search",
            "user-agent": os.getenv(
                "SELOGER_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            ),
            "cookie": raw_cookie,
        }
        if method.upper() == "POST":
            headers["content-type"] = "application/json"

        try:
            if method.upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=20)
            else:
                resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code < 200 or resp.status_code >= 300:
                return None
            return resp.json()
        except Exception:
            return None

    def _search_html(self, city: Optional[str], postal_code: Optional[str]) -> list[ScrapedListing]:
        cards = []
        for url in self._candidate_urls(city, postal_code):
            html = fetch_html(url, referer="https://www.seloger.com/")
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            cards = soup.select(".c-pa-list .c-pa-list_item a")
            if cards:
                break
        if not cards:
            return []

        rows: list[ScrapedListing] = []
        for a in cards:
            href = a.get("href") or ""
            if href.startswith("/"):
                href = "https://www.seloger.com" + href
            title = a.get_text(strip=True)
            price_txt = a.find_next(string=lambda s: s and ("€" in s or "eur" in s.lower()))
            price = parse_int(str(price_txt) if price_txt else None)
            details = a.find_next("ul")
            surface = None
            rooms = None
            if details:
                txt = details.get_text(" ", strip=True)
                surface = parse_float(txt)
                rooms = parse_int(txt)
            raw_id = href.rstrip("/").split("-")[-1] if href else ""
            external_id = raw_id or hash_str(href or (title or ""))
            rows.append(
                ScrapedListing(
                    source=self.source,
                    external_id=external_id,
                    url=href,
                    title=title,
                    price=price,
                    surface_m2=surface,
                    rooms=rooms,
                )
            )
        return rows

    def fetch_city(self, city: Optional[str], postal_code: Optional[str]) -> Iterable[ScrapedListing]:
        # Priority: internal SeLoger APIs via browser session. Fallback: static HTML parsing.
        api_rows = self._search_api(city, postal_code)
        if api_rows:
            for row in api_rows:
                yield row
            return

        for row in self._search_html(city, postal_code):
            yield row
