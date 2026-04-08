from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Iterable

from aiogram import Bot

from database import repo
from database import models as m
from scraper.base import ScrapedListing
from scraper.leboncoin import LeboncoinScraper
from scraper.seloger import SeLogerScraper
from scraper.pap import PAPScraper
from scraper.paruvendu import ParuVenduScraper
from scraper.entreparticuliers import EntreParticuliersScraper
from scraper.logicimmo import LogicImmoScraper
from scraper.rentola import RentolaScraper
from scraper.ouestfranceimmo import OuestFranceImmoScraper
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert
from config import settings
from utils.cache import TTLCache
from utils.hash import hash_str

log = logging.getLogger(__name__)


SCRAPERS = [
    ParuVenduScraper(),
    EntreParticuliersScraper(),
    LeboncoinScraper(),
    SeLogerScraper(),
    PAPScraper(),
    LogicImmoScraper(),
    RentolaScraper(),
    OuestFranceImmoScraper(),
]
_cross_source_dedupe = TTLCache(ttl_seconds=24 * 3600)
_source_health: dict[str, dict] = {}


def _now_utc() -> datetime:
    return datetime.utcnow()


def _ensure_source_health(source: str) -> dict:
    if source not in _source_health:
        _source_health[source] = {
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_success": 0,
            "total_runs": 0,
            "total_listings": 0,
            "last_listings_count": 0,
            "consecutive_empty_runs": 0,
            "last_error": None,
            "last_failure_at": None,
            "last_success_at": None,
            "disabled_until": None,
        }
    return _source_health[source]


def get_sources_health() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for source, raw in _source_health.items():
        out[source] = dict(raw)
    return out


def get_scraper(source: str):
    for s in SCRAPERS:
        if s.source == source:
            return s
    return None


def _source_enabled(source: str) -> bool:
    h = _ensure_source_health(source)
    until = h.get("disabled_until")
    if not until:
        return True
    return until <= _now_utc()


def _mark_source_success(source: str, listings_count: int) -> None:
    h = _ensure_source_health(source)
    h["total_runs"] += 1
    h["last_listings_count"] = listings_count
    h["total_listings"] += max(0, listings_count)
    if listings_count > 0:
        h["consecutive_empty_runs"] = 0
    else:
        h["consecutive_empty_runs"] += 1
    h["consecutive_failures"] = 0
    h["total_success"] += 1
    h["last_success_at"] = _now_utc()
    h["last_error"] = None
    h["disabled_until"] = None


def _disable_backoff_seconds(consecutive_failures: int) -> int:
    if consecutive_failures < 3:
        return 0
    # 3 -> 60s, 4 -> 180s, 5+ -> 600s
    if consecutive_failures == 3:
        return 60
    if consecutive_failures == 4:
        return 180
    return 600


def _mark_source_failure(source: str, error_text: str) -> None:
    h = _ensure_source_health(source)
    h["total_runs"] += 1
    h["last_listings_count"] = 0
    h["consecutive_empty_runs"] += 1
    h["consecutive_failures"] += 1
    h["total_failures"] += 1
    h["last_error"] = error_text[:400]
    h["last_failure_at"] = _now_utc()
    backoff = _disable_backoff_seconds(h["consecutive_failures"])
    if backoff > 0:
        h["disabled_until"] = _now_utc() + timedelta(seconds=backoff)


def _listing_signature(listing: ScrapedListing) -> str:
    title = (listing.title or "").lower().strip()
    title = " ".join(title.split())
    location = (listing.location or "").lower().strip()
    location = " ".join(location.split())
    price = str(listing.price or "")
    surface = f"{(listing.surface_m2 or 0):.1f}" if listing.surface_m2 is not None else ""
    rooms = str(listing.rooms or "")
    return hash_str(f"{title}|{price}|{surface}|{rooms}|{location}")


def _unique_locations(filters: list[m.Filter]) -> list[tuple[str | None, str | None]]:
    seen: set[tuple[str | None, str | None]] = set()
    out: list[tuple[str | None, str | None]] = []

    def _norm(value: str | None) -> str | None:
        if value is None:
            return None
        v = value.strip()
        return v or None

    for f in filters:
        key = (_norm(f.city), _norm(f.postal_code))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _build_scheduler_locations(filters: list[m.Filter]) -> list[tuple[str | None, str | None]]:
    """
    Build scheduler search locations with optional variant expansion:
    - exact (city, postal)
    - city-only
    - postal-only
    - generic (None, None), if enabled
    """
    base = _unique_locations(filters)
    if not settings.expand_location_variants:
        out = list(base)
        if settings.include_generic_location_pass and (None, None) not in out:
            out.append((None, None))
        return out

    seen: set[tuple[str | None, str | None]] = set()
    out: list[tuple[str | None, str | None]] = []

    def _add(city: str | None, postal: str | None) -> None:
        key = (city, postal)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    for city, postal in base:
        _add(city, postal)
        if city and postal:
            _add(city, None)
            _add(None, postal)
        elif city:
            _add(city, None)
        elif postal:
            _add(None, postal)

    if settings.include_generic_location_pass:
        _add(None, None)

    return out


async def _process_new_listing(bot: Bot, listing: ScrapedListing):
    sig = _listing_signature(listing)
    if _cross_source_dedupe.get(sig):
        return
    _cross_source_dedupe.set(sig, True)

    # compute price per m2 if possible
    if listing.price is not None and listing.surface_m2 and not listing.price_per_m2 and listing.surface_m2 > 0:
        listing.price_per_m2 = listing.price / listing.surface_m2
    # Persist listing (dedupe via (source, external_id))
    db_listing = repo.get_or_create_listing(
        source=listing.source,
        external_id=listing.external_id,
        url=listing.url,
        title=listing.title,
        price=listing.price,
        surface_m2=listing.surface_m2,
        price_per_m2=listing.price_per_m2,
        location=listing.location,
        rooms=listing.rooms,
        description=listing.description,
        images=",".join(listing.images) if listing.images else None,
        published_at=listing.published_at,
    )
    if not db_listing:
        return
    listing.db_id = db_listing.id

    filters = repo.get_all_active_filters()
    users_cache: dict[int, m.User] = {}

    for f in filters:
        # Load user
        if f.user_id not in users_cache:
            u = repo.get_user_by_id(f.user_id)
            if not u or not u.active:
                continue
            users_cache[f.user_id] = u
        user = users_cache[f.user_id]

        res = match_and_score(f, listing)
        if not res.matched:
            continue

        if repo.has_sent_alert(user.id, db_listing.id):
            continue

        scam = detect_scam(listing)
        scam_tag = "Potentielle arnaque" if scam.is_scam else None

        if user.is_premium:
            try:
                await send_alert(bot, user, listing, res.score_label, scam_tag)
                repo.mark_alert_sent(user.id, db_listing.id)
            except Exception as e:
                log.exception("Failed to send premium alert: %s", e)
        else:
            not_before = datetime.utcnow() + timedelta(seconds=settings.premium_free_delay_seconds)
            repo.add_pending_alert(user.id, db_listing.id, not_before)


async def _dispatch_pending(bot: Bot):
    now = datetime.utcnow()
    for pa in repo.fetch_due_pending_alerts(now):
        user = repo.get_user_by_id(pa.user_id)
        if not user or not user.active:
            repo.set_pending_alert_status(pa.id, "canceled")
            continue
        db_listing = repo.get_listing_by_id(pa.listing_id)
        if not db_listing:
            repo.set_pending_alert_status(pa.id, "canceled")
            continue
        # Map DB listing to ScrapedListing for message formatting
        l = ScrapedListing(
            source=db_listing.source,
            external_id=db_listing.external_id,
            url=db_listing.url,
            title=db_listing.title,
            price=db_listing.price,
            surface_m2=db_listing.surface_m2,
            price_per_m2=db_listing.price_per_m2,
            location=db_listing.location,
            rooms=db_listing.rooms,
            description=db_listing.description,
            images=db_listing.images.split(",") if db_listing.images else None,
            published_at=db_listing.published_at,
            db_id=db_listing.id,
        )
        try:
            # delayed sends use same score label heuristic without recomputation context; label omitted for simplicity
            await send_alert(bot, user, l, score_label="", scam_tag=None)
            repo.set_pending_alert_status(pa.id, "sent")
            repo.mark_alert_sent(user.id, db_listing.id)
        except Exception:
            repo.set_pending_alert_status(pa.id, "canceled")


async def _fetch_listings_non_blocking(scraper, city: str | None, postal: str | None) -> list[ScrapedListing]:
    """
    Run blocking scraper code in a worker thread to avoid blocking Telegram event loop.
    """
    return await asyncio.wait_for(
        asyncio.to_thread(lambda: list(scraper.fetch_city(city, postal))),
        timeout=max(8, settings.scraper_task_timeout_seconds),
    )


async def _collect_source_listings(scraper, locs: list[tuple[str | None, str | None]]) -> tuple[str, list[ScrapedListing], bool, int]:
    """
    Collect listings for one source across locations in parallel (bounded).
    Returns: (source, listings, had_hard_error, total_listings_count)
    """
    source_had_hard_error = False
    all_listings: list[ScrapedListing] = []

    sem = asyncio.Semaphore(max(1, settings.per_source_concurrency))

    async def _one_loc(city: str | None, postal: str | None) -> list[ScrapedListing]:
        nonlocal source_had_hard_error
        async with sem:
            try:
                return await _fetch_listings_non_blocking(scraper, city, postal)
            except asyncio.TimeoutError:
                source_had_hard_error = True
                msg = f"timeout for {city}/{postal}"
                _mark_source_failure(scraper.source, msg)
                log.warning("Scraper %s timed out for %s/%s", scraper.source, city, postal)
            except Exception as e:
                source_had_hard_error = True
                err = str(e)
                _mark_source_failure(scraper.source, err)
                if "404 Client Error" in err or "Playwright Sync API" in err:
                    log.info("Scraper %s unavailable for %s/%s: %s", scraper.source, city, postal, e)
                else:
                    log.warning("Scraper %s failed for %s/%s: %s", scraper.source, city, postal, e)
            return []

    tasks = [asyncio.create_task(_one_loc(city, postal)) for city, postal in locs]
    results = await asyncio.gather(*tasks, return_exceptions=False) if tasks else []
    for lst in results:
        all_listings.extend(lst)

    return scraper.source, all_listings, source_had_hard_error, len(all_listings)


def _sample_locs_for_source(source: str, locs: list[tuple[str | None, str | None]]) -> list[tuple[str | None, str | None]]:
    if not locs:
        return []
    if settings.full_scan_mode:
        # In scheduler mode, keep only user locations to avoid heavy duplicate scans.
        return list(locs)
    quota = settings.source_quota_per_cycle.get(source, settings.default_source_quota)
    if quota <= 0:
        return []
    if len(locs) <= quota:
        return list(locs)
    try:
        return random.sample(locs, k=quota)
    except Exception:
        return list(locs)[:quota]


async def sample_source_listings(
    source: str,
    locs: list[tuple[str | None, str | None]],
    *,
    limit_locations: int = 1,
) -> tuple[list[ScrapedListing], str | None]:
    """
    Collect a small sample of listings for one source without impacting health stats.
    Returns: (listings, last_error)
    """
    scraper = get_scraper(source)
    if not scraper:
        return ([], f"Unknown source: {source}")
    results: list[ScrapedListing] = []
    last_error: str | None = None
    to_visit = list(locs)[: max(0, limit_locations)] if limit_locations > 0 else []
    if not to_visit:
        to_visit = [(None, None)]
    for city, postal in to_visit:
        try:
            listings = await _fetch_listings_non_blocking(scraper, city, postal)
            results.extend(listings)
        except Exception as e:
            last_error = str(e)
            continue
    return (results, last_error)


async def run_scheduler(bot: Bot, *, interval_min: int = 30, interval_max: int = 60):
    while True:
        try:
            filters = repo.get_all_active_filters()
            locs = _build_scheduler_locations(filters)
            enabled_scrapers = []
            for scraper in SCRAPERS:
                if not _source_enabled(scraper.source):
                    h = _ensure_source_health(scraper.source)
                    log.info("Skipping source %s (disabled until %s)", scraper.source, h.get("disabled_until"))
                    continue
                enabled_scrapers.append(scraper)

            tasks = []
            for scraper in enabled_scrapers:
                sample_locs = _sample_locs_for_source(scraper.source, locs)
                if not sample_locs:
                    continue
                tasks.append(asyncio.create_task(_collect_source_listings(scraper, sample_locs)))
            if tasks:
                results = await asyncio.gather(*tasks)
            else:
                results = []

            # Process all source outputs (sources are now collected in parallel).
            for source, listings, source_had_hard_error, source_total_listings in results:
                for listing in listings:
                    await _process_new_listing(bot, listing)
                if not source_had_hard_error:
                    _mark_source_success(source, source_total_listings)

            await _dispatch_pending(bot)
        except Exception as e:
            log.exception("Scheduler iteration failed: %s", e)
        await asyncio.sleep(random.randint(interval_min, interval_max))


async def run_full_scan_once(bot: Bot) -> dict[str, int]:
    """Run one full-scan pass across all enabled sources and all locations regardless of quotas.
    Returns a dict of source->listings_count processed in this pass.
    """
    filters = repo.get_all_active_filters()
    locs = _build_scheduler_locations(filters)
    enabled_scrapers = []
    for scraper in SCRAPERS:
        if not _source_enabled(scraper.source):
            continue
        enabled_scrapers.append(scraper)

    # Full scan keeps all expanded locations.
    scan_locs = list(locs) or [(None, None)]

    tasks = []
    for scraper in enabled_scrapers:
        tasks.append(asyncio.create_task(_collect_source_listings(scraper, scan_locs)))
    results = await asyncio.gather(*tasks) if tasks else []

    per_source_counts: dict[str, int] = {}
    for source, listings, source_had_hard_error, source_total_listings in results:
        for listing in listings:
            await _process_new_listing(bot, listing)
        if not source_had_hard_error:
            _mark_source_success(source, source_total_listings)
        per_source_counts[source] = source_total_listings

    await _dispatch_pending(bot)
    return per_source_counts
