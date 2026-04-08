from __future__ import annotations

import json
from typing import Any, Optional

import requests

from config import settings


def call_ai(task: str, *, input: Any, model: Optional[str] = None, timeout: Optional[int] = None) -> Optional[dict]:
    """
    Generic AI HTTP caller. Uses the same endpoint/config as AI scam analysis.
    Expected response: JSON object; task-specific contract handled by callers.
    Returns parsed dict or None on error.
    """
    endpoint = settings.ai_scam_endpoint
    if not settings.ai_scam_enabled or not endpoint:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.ai_scam_api_key:
        headers["Authorization"] = f"Bearer {settings.ai_scam_api_key}"
    payload = {
        "model": model or settings.ai_scam_model,
        "task": task,
        "input": input,
    }
    try:
        resp = requests.post(endpoint, headers=headers, data=json.dumps(payload), timeout=timeout or settings.ai_scam_timeout)
        if 200 <= resp.status_code < 300:
            data = resp.json()
            if isinstance(data, dict):
                if "result" in data and isinstance(data["result"], dict):
                    return data["result"]
                return data
    except Exception:
        return None
    return None
