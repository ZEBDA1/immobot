import os
from dataclasses import dataclass
from dotenv import load_dotenv


load_dotenv()


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


@dataclass
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///data.db")
    proxy_url: str | None = os.getenv("PROXY_URL") or None
    premium_free_delay_seconds: int = int(os.getenv("PREMIUM_FREE_DELAY_SECONDS", "300"))
    enable_geocoding: bool = os.getenv("ENABLE_GEOCODING", "false").lower() in ("1", "true", "yes")
    geocoding_user_agent: str = os.getenv("GEOCODING_USER_AGENT", "immobot/1.0")
    admin_telegram_ids: set[int] = None  # type: ignore[assignment]

    def __post_init__(self):
        self.admin_telegram_ids = _parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS"))


settings = Settings()

if not settings.bot_token:
    raise RuntimeError("BOT_TOKEN is not set. Configure it in .env or environment.")
