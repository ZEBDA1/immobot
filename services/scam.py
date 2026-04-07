from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scraper.base import ScrapedListing


SUSPICIOUS_KEYWORDS = [
    "western union",
    "mandat cash",
    "urgent partir",
    "ne pas appeler",
    "pas sur place",
    "copies de papiers",
    "aucune visite",
]


@dataclass
class ScamResult:
    is_scam: bool
    reason: Optional[str] = None


def detect_scam(l: ScrapedListing) -> ScamResult:
    text = f"{l.title or ''} \n {l.description or ''}".lower()
    for kw in SUSPICIOUS_KEYWORDS:
        if kw in text:
            return ScamResult(is_scam=True, reason=f"Mot-cle suspect: {kw}")

    if l.price and l.surface_m2 and l.surface_m2 > 0:
        ppm2 = l.price / l.surface_m2
        if ppm2 < 1000:
            return ScamResult(is_scam=True, reason="Prix au m2 anormalement bas")

    if l.price and l.price < 200:
        return ScamResult(is_scam=True, reason="Prix total anormalement bas")

    return ScamResult(is_scam=False)
