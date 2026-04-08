import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


load_dotenv(encoding="utf-8-sig")


def _parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    out: set[int] = set()
    for part in value.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            continue
    return out


def _parse_quota_map(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    out: dict[str, int] = {}
    for part in value.split(","):
        p = part.strip()
        if not p or "=" not in p:
            continue
        name, val = p.split("=", 1)
        name = name.strip().lower()
        try:
            out[name] = int(val.strip())
        except ValueError:
            continue
    return out


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data.db")
    proxy_url: str | None = os.getenv("PROXY_URL") or None
    proxy_fallback_direct: bool = os.getenv("PROXY_FALLBACK_DIRECT", "true").lower() in ("1", "true", "yes")
    premium_free_delay_seconds: int = int(os.getenv("PREMIUM_FREE_DELAY_SECONDS", "300"))
    enable_geocoding: bool = os.getenv("ENABLE_GEOCODING", "false").lower() in ("1", "true", "yes")
    geocoding_user_agent: str = os.getenv("GEOCODING_USER_AGENT", "immobot/1.0")
    admin_telegram_ids: set[int] = None  # type: ignore[assignment]
    # Scheduler quotas: per-source limit of locations processed per cycle
    source_quota_per_cycle: dict[str, int] = field(default_factory=dict)
    default_source_quota: int = int(os.getenv("DEFAULT_SOURCE_QUOTA", "3"))
    # Optional AI-based scam analysis
    ai_scam_enabled: bool = os.getenv("AI_SCAM_ENABLED", "false").lower() in ("1", "true", "yes")
    ai_scam_endpoint: str | None = os.getenv("AI_SCAM_ENDPOINT") or None
    ai_scam_api_key: str | None = os.getenv("AI_SCAM_API_KEY") or None
    ai_scam_model: str = os.getenv("AI_SCAM_MODEL", "gpt-4o-mini")
    ai_scam_timeout: int = int(os.getenv("AI_SCAM_TIMEOUT", "12"))
    # Parallelism
    per_source_concurrency: int = int(os.getenv("PER_SOURCE_CONCURRENCY", "4"))
    scraper_task_timeout_seconds: int = int(os.getenv("SCRAPER_TASK_TIMEOUT_SECONDS", "20"))
    # Full scan mode (ignore per-source quotas and scan all locations)
    full_scan_mode: bool = os.getenv("FULL_SCAN", "false").lower() in ("1", "true", "yes")
    # Search amplification: query additional location variants to increase discovery.
    expand_location_variants: bool = os.getenv("EXPAND_LOCATION_VARIANTS", "true").lower() in ("1", "true", "yes")
    # Always add one generic (None/None) pass in normal scheduler mode.
    include_generic_location_pass: bool = os.getenv("INCLUDE_GENERIC_LOCATION_PASS", "true").lower() in ("1", "true", "yes")

    def __post_init__(self):
        self.admin_telegram_ids = _parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS"))
        self.source_quota_per_cycle = _parse_quota_map(os.getenv("SOURCE_QUOTA_PER_CYCLE"))


settings = Settings()

if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN is not set. Configure it in .env or environment.")
