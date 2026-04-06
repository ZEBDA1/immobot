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
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert
from config import settings

log = logging.getLogger(__name__)


SCRAPERS = [LeboncoinScraper(), SeLogerScraper(), PAPScraper()]


def _unique_locations(filters: list[m.Filter]) -> list[tuple[str | None, str | None]]:
    seen: set[tuple[str | None, str | None]] = set()
    for f in filters:
        key = (f.city, f.postal_code)
        if key not in seen:
            seen.add(key)
    return list(seen)


async def _process_new_listing(bot: Bot, listing: ScrapedListing):
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
        )
        try:
            # delayed sends use same score label heuristic without recomputation context; label omitted for simplicity
            await send_alert(bot, user, l, score_label="", scam_tag=None)
            repo.set_pending_alert_status(pa.id, "sent")
            repo.mark_alert_sent(user.id, db_listing.id)
        except Exception:
            repo.set_pending_alert_status(pa.id, "canceled")


async def run_scheduler(bot: Bot, *, interval_min: int = 30, interval_max: int = 60):
    while True:
        try:
            filters = repo.get_all_active_filters()
            locs = _unique_locations(filters)
            for scraper in SCRAPERS:
                for city, postal in locs:
                    try:
                        for listing in scraper.fetch_city(city, postal):
                            await _process_new_listing(bot, listing)
                    except Exception as e:
                        log.warning("Scraper %s failed for %s/%s: %s", scraper.source, city, postal, e)

            await _dispatch_pending(bot)
        except Exception as e:
            log.exception("Scheduler iteration failed: %s", e)
        await asyncio.sleep(random.randint(interval_min, interval_max))