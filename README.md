# Telegram Real Estate Alert Bot (SaaS-ready)

Ultra-fast Telegram bot that monitors multiple real-estate sources and sends real-time alerts for new listings matching each user's filters. Built for robustness, scalability, and monetization readiness.

## Features

- Bot commands: /start, /set_filters, /view_filters, /edit_filters, /stop, /premium
- Multi-user with individual filters
- Scraping sources: Leboncoin, SeLoger, PAP (requests + BeautifulSoup; Playwright fallback optional)
- Anti-blocking: rotating user-agent, random delays, optional proxy
- Real-time detection: 30–60s schedule, dedupe via unique IDs
- Intelligent filtering with quality score and scam detection
- Notifications: structured Telegram messages with score and alert tags
- Premium structure: instant alerts vs 5-minute delay, multi-city, priority
- Database: SQLite (default) or PostgreSQL via DATABASE_URL
- Clean architecture: bot/ scraper/ database/ services/ utils/
- Scheduler: asyncio + APScheduler
- Logging and monitoring hooks

## Quick Start

### 1) Requirements

- Python 3.11+
- Windows, macOS, or Linux

### 2) Setup

Create and activate a virtual environment, then install deps:

```bash
python -m venv .venv
.\.venv\Scripts\activate   # Windows PowerShell
pip install -r requirements.txt
```

Optional (Playwright fallback):

```bash
pip install playwright
python -m playwright install chromium
```

### 3) Configure

Copy `.env.example` to `.env` and set your values:

- BOT_TOKEN: Telegram bot token from @BotFather
- DATABASE_URL: e.g., `sqlite:///data.db` (default) or Postgres `postgresql+psycopg2://user:pass@host:5432/db`
- PROXY_URL: optional HTTP(S) proxy for scraping
- PREMIUM_FREE_DELAY_SECONDS: delay for free users (default 300)

### 4) Run

```bash
python main.py
```

Then open your bot in Telegram and run /start.

## Project Structure

- bot/: Telegram bot handlers, states, keyboards
- scraper/: Scrapers for each source + base interfaces
- database/: SQLAlchemy models and repository helpers
- services/: Matching, scoring, scam detection, scheduler, notifications
- utils/: HTTP client, UA rotation, hashing, text utils, geocoding (optional)

## Notes

- Geolocation radius: basic string matching by default; optional Nominatim geocoding can be enabled via env for more precise radius filtering. Caching included.
- Premium: structure implemented (instant vs delayed, multi-city). Payment integration hooks left for Stripe or other providers.
- This is a solid foundation; tune scrapers and selectors as sites evolve.

## License

Commercial use permitted by the project owner. No warranty.