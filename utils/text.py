import re
from typing import Optional


def parse_int(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.findall(r"\d+", s.replace("\xa0", " ").replace(",", "."))
    if not m:
        return None
    try:
        return int("".join(m))
    except Exception:
        return None


def parse_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s2 = s.replace("\xa0", " ")
    m = re.search(r"(\d+[\.,]?\d*)", s2)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except Exception:
        return None