from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scraper.base import ScrapedListing
from config import settings
from utils.text import parse_int
from html import unescape as html_unescape
import json
import requests


SUSPICIOUS_KEYWORDS = [
    "western union",
    "mandat cash",
    "urgent partir",
    "ne pas appeler",
    "pas sur place",
    "copies de papiers",
    "aucune visite",
    "payer a distance",
    "virement international",
    "prepaiement",
    "transfert d'argent",
    "sans visite",
]


@dataclass
class ScamResult:
    is_scam: bool
    reason: Optional[str] = None


def detect_scam(l: ScrapedListing) -> ScamResult:
    # Normalize text for detection
    text = html_unescape(f"{l.title or ''} \n {l.description or ''}").lower()
    text = " ".join(text.split())

    # Explicit price anomalies
    if l.price is not None:
        if l.price <= 0:
            return ScamResult(is_scam=True, reason="Prix nul")
        # Extremely low monthly rents often indicate scams
        if ("/mois" in text or "par mois" in text or "mois" in text) and l.price < 150:
            return ScamResult(is_scam=True, reason="Loyer mensuel anormalement bas")
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in text:
            return ScamResult(is_scam=True, reason=f"Mot-cle suspect: {kw}")

    if l.price is not None and l.surface_m2 and l.surface_m2 > 0:
        ppm2 = l.price / l.surface_m2
        if ppm2 < 1000:
            return ScamResult(is_scam=True, reason="Prix au m2 anormalement bas")

    if l.price is not None and l.price < 200:
        return ScamResult(is_scam=True, reason="Prix total anormalement bas")

    # Optional AI-based analysis
    if settings.ai_scam_enabled and settings.ai_scam_endpoint:
        try:
            result = _ai_assess_listing(l)
            if result and result.get("is_scam"):
                reason = str(result.get("reason") or "IA: signal scam")[:200]
                return ScamResult(is_scam=True, reason=reason)
        except Exception:
            pass

    return ScamResult(is_scam=False)


def _ai_assess_listing(l: ScrapedListing) -> Optional[dict]:
    """
    Calls a configurable AI endpoint to assess scam likelihood.
    Expected JSON response schema: {"is_scam": bool, "reason": str}
    """
    endpoint = settings.ai_scam_endpoint
    if not endpoint:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.ai_scam_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_scam_api_key}"
    payload = {
        "model": settings.ai_scam_model,
        "task": "scam-detection",
        "input": {
            "title": l.title,
            "description": l.description,
            "price": l.price,
            "surface_m2": l.surface_m2,
            "price_per_m2": l.price_per_m2,
            "location": l.location,
            "rooms": l.rooms,
            "url": l.url,
            "source": l.source,
        },
    }
    try:
        resp = requests.post(endpoint, headers=headers, data=json.dumps(payload), timeout=settings.ai_scam_timeout)
        if resp.status_code >= 200 and resp.status_code < 300:
            data = resp.json()
            # Accept either direct schema or nested result
            if isinstance(data, dict) and ("is_scam" in data or "reason" in data):
                return data
            if isinstance(data, dict) and isinstance(data.get("result"), dict):
                return data["result"]
    except Exception:
        return None
    return None
