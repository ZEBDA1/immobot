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
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert
from config import settings
from utils.cache import TTLCache
from utils.hash import hash_str

log = logging.getLogger(__name__)


SCRAPERS = [ParuVenduScraper(), EntreParticuliersScraper(), LeboncoinScraper(), SeLogerScraper(), PAPScraper()]
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
    for f in filters:
        key = (f.city, f.postal_code)
        if key not in seen:
            seen.add(key)
    return list(seen)


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
        timeout=35,
    )


async def _collect_source_listings(scraper, locs: list[tuple[str | None, str | None]]) -> tuple[str, list[ScrapedListing], bool, int]:
    """
    Collect listings for one source across all locations.
    Returns: (source, listings, had_hard_error, total_listings_count)
    """
    source_had_hard_error = False
    source_total_listings = 0
    all_listings: list[ScrapedListing] = []

    for city, postal in locs:
        try:
            listings = await _fetch_listings_non_blocking(scraper, city, postal)
            source_total_listings += len(listings)
            all_listings.extend(listings)
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

    return scraper.source, all_listings, source_had_hard_error, source_total_listings


async def run_scheduler(bot: Bot, *, interval_min: int = 30, interval_max: int = 60):
    while True:
        try:
            filters = repo.get_all_active_filters()
            locs = _unique_locations(filters)
            enabled_scrapers = []
            for scraper in SCRAPERS:
                if not _source_enabled(scraper.source):
                    h = _ensure_source_health(scraper.source)
                    log.info("Skipping source %s (disabled until %s)", scraper.source, h.get("disabled_until"))
                    continue
                enabled_scrapers.append(scraper)

            tasks = [asyncio.create_task(_collect_source_listings(scraper, locs)) for scraper in enabled_scrapers]
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
