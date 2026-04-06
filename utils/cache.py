from __future__ import annotations

import time
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self.store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        v = self.store.get(key)
        if not v:
            return None
        ts, data = v
        if time.time() - ts > self.ttl:
            self.store.pop(key, None)
            return None
        return data

    def set(self, key: str, value: Any) -> None:
        self.store[key] = (time.time(), value)