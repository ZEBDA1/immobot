from __future__ import annotations

import re
from typing import Optional, Dict, Any

from utils.ai import call_ai


def _coerce_int(v) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(str(v).replace(" ", "").replace(",", ".")))
    except Exception:
        return None


def _coerce_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(" ", "").replace(",", "."))
    except Exception:
        return None


def _fallback_parse(text: str) -> Dict[str, Any]:
    """Very light regex-based parser when AI is disabled/unavailable."""
    out: Dict[str, Any] = {}
    # postal code
    m = re.search(r"\b(\d{5})\b", text)
    if m:
        out["postal_code"] = m.group(1)
    # rooms: e.g., "2 pieces" or "T2"
    m = re.search(r"\b(\d+)\s*pi[eè]ces?\b|\bT(\d)\b", text, re.IGNORECASE)
    if m:
        val = m.group(1) or m.group(2)
        out["rooms_min"] = _coerce_int(val)
    # surface min
    m = re.search(r"(\d+[\.,]?\d*)\s*m(?:2|²)\b", text, re.IGNORECASE)
    if m:
        out["surface_min"] = _coerce_float(m.group(1))
    # radius km: various phrasings
    m = re.search(r"rayon\s+(?:max\s+de\s+|de\s+)?(\d+[\.,]?\d*)\s*(?:km|kilom(?:e|é)tre[s]?)", text, re.IGNORECASE)
    if m:
        out["radius_km"] = _coerce_float(m.group(1))
    # price max: look for euro token
    m = re.search(r"(\d+[\s\.,]?\d*)\s*(?:€|eur)\b", text, re.IGNORECASE)
    if m:
        out["price_max"] = _coerce_int(m.group(1))
    # city heuristic: a capitalized word (very rough)
    m = re.search(r"\b([A-Z][a-zA-Zéèàùûôîïç-]{2,}(?:\s+[A-Z][a-zA-Zéèàùûôîïç-]{2,})*)\b", text)
    if m and len(m.group(1)) <= 32:
        out["city"] = m.group(1)
    return out


def ai_parse_filter(text: str) -> Dict[str, Any]:
    """
    Use AI (if available) to parse a natural language filter text into filter fields.
    Returns a dict with keys compatible with repo.create_or_update_filter.
    Fallbacks to a simple regex-based parser if AI is disabled or fails.
    """
    text = text.strip()
    if not text:
        return {}

    result = call_ai(
        task="parse-filter",
        input={
            "instruction": "From French natural language, extract a housing search filter.",
            "fields": [
                "city", "postal_code", "radius_km", "price_min", "price_max",
                "surface_min", "rooms_min", "property_type", "budget_max_with_charges"
            ],
            "text": text,
            "constraints": {
                "city": "string or null",
                "postal_code": "5-digit string or null",
                "radius_km": "float or null",
                "price_min": "int or null",
                "price_max": "int or null",
                "surface_min": "float or null",
                "rooms_min": "int or null",
                "property_type": "one of: studio, appartement, maison, null",
                "budget_max_with_charges": "int or null",
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "city": {"type": ["string", "null"]},
                    "postal_code": {"type": ["string", "null"]},
                    "radius_km": {"type": ["number", "null"]},
                    "price_min": {"type": ["integer", "null"]},
                    "price_max": {"type": ["integer", "null"]},
                    "surface_min": {"type": ["number", "null"]},
                    "rooms_min": {"type": ["integer", "null"]},
                    "property_type": {"type": ["string", "null"]},
                    "budget_max_with_charges": {"type": ["integer", "null"]},
                },
                "additionalProperties": False,
            },
        },
    )

    fb = _fallback_parse(text)
    if not isinstance(result, dict):
        return fb

    out: Dict[str, Any] = {}
    out["city"] = result.get("city") if isinstance(result.get("city"), str) else fb.get("city")
    pc = result.get("postal_code") if isinstance(result.get("postal_code"), str) else fb.get("postal_code")
    out["postal_code"] = pc if isinstance(pc, str) and len(pc) == 5 and pc.isdigit() else None
    out["radius_km"] = _coerce_float(result.get("radius_km")) if result.get("radius_km") is not None else fb.get("radius_km")
    out["price_min"] = _coerce_int(result.get("price_min")) if result.get("price_min") is not None else fb.get("price_min")
    out["price_max"] = _coerce_int(result.get("price_max")) if result.get("price_max") is not None else fb.get("price_max")
    out["surface_min"] = _coerce_float(result.get("surface_min")) if result.get("surface_min") is not None else fb.get("surface_min")
    out["rooms_min"] = _coerce_int(result.get("rooms_min")) if result.get("rooms_min") is not None else fb.get("rooms_min")
    pt = result.get("property_type")
    out["property_type"] = pt if isinstance(pt, str) and len(pt) <= 32 else fb.get("property_type")
    out["budget_max_with_charges"] = (
        _coerce_int(result.get("budget_max_with_charges"))
        if result.get("budget_max_with_charges") is not None
        else fb.get("budget_max_with_charges")
    )
    return out
