from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select, and_, func, delete, update, desc

from .session import SessionLocal
from . import models as m


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Users
def get_or_create_user(telegram_id: int, username: Optional[str] = None) -> m.User:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if user:
            if username and user.username != username:
                user.username = username
                s.add(user)
            return user
        user = m.User(telegram_id=telegram_id, username=username)
        s.add(user)
        s.flush()
        return user


def set_user_premium(telegram_id: int, is_premium: bool) -> None:
    with session_scope() as s:
        user = s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()
        if not user:
            user = m.User(telegram_id=telegram_id)
        user.is_premium = is_premium
        s.add(user)


def get_user_by_id(user_id: int) -> m.User | None:
    with session_scope() as s:
        return s.execute(select(m.User).where(m.User.id == user_id)).scalar_one_or_none()


def get_user_by_telegram_id(telegram_id: int) -> m.User | None:
    with session_scope() as s:
        return s.execute(select(m.User).where(m.User.telegram_id == telegram_id)).scalar_one_or_none()


def get_user_filters(user_id: int) -> list[m.Filter]:
    with session_scope() as s:
        return list(s.execute(select(m.Filter).where(m.Filter.user_id == user_id, m.Filter.active == True)).scalars())


def create_or_update_filter(
    user_id: int,
    name: str = "default",
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
    surface_min: Optional[float] = None,
    rooms_min: Optional[int] = None,
    property_type: Optional[str] = None,
    budget_max_with_charges: Optional[int] = None,
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    radius_km: Optional[float] = None,
) -> m.Filter:
    with session_scope() as s:
        f = s.execute(
            select(m.Filter).where(m.Filter.user_id == user_id, m.Filter.name == name)
        ).scalar_one_or_none()
        if not f:
            f = m.Filter(user_id=user_id, name=name)
        f.price_min = price_min
        f.price_max = price_max
        f.surface_min = surface_min
        f.rooms_min = rooms_min
        f.property_type = property_type
        f.budget_max_with_charges = budget_max_with_charges
        f.city = city
        f.postal_code = postal_code
        f.radius_km = radius_km
        s.add(f)
        s.flush()
        return f


# Listings
def get_or_create_listing(
    *,
    source: str,
    external_id: str,
    url: str,
    title: Optional[str] = None,
    price: Optional[int] = None,
    surface_m2: Optional[float] = None,
    price_per_m2: Optional[float] = None,
    location: Optional[str] = None,
    rooms: Optional[int] = None,
    description: Optional[str] = None,
    images: Optional[str] = None,
    published_at: Optional[datetime] = None,
) -> m.Listing | None:
    with session_scope() as s:
        existing = s.execute(
            select(m.Listing).where(m.Listing.source == source, m.Listing.external_id == external_id)
        ).scalar_one_or_none()
        if existing:
            # Only brand-new listings should be processed by the scheduler.
            return None
        listing = m.Listing(
            source=source,
            external_id=external_id,
            url=url,
            title=title,
            price=price,
            surface_m2=surface_m2,
            price_per_m2=price_per_m2,
            location=location,
            rooms=rooms,
            description=description,
            images=images,
            published_at=published_at,
        )
        s.add(listing)
        s.flush()
        return listing


def has_sent_alert(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        cnt = s.execute(
            select(func.count()).select_from(m.SentAlert).where(
                m.SentAlert.user_id == user_id, m.SentAlert.listing_id == listing_id
            )
        ).scalar_one()
        return cnt > 0


def mark_alert_sent(user_id: int, listing_id: int) -> None:
    with session_scope() as s:
        rec = m.SentAlert(user_id=user_id, listing_id=listing_id)
        s.add(rec)


def add_pending_alert(user_id: int, listing_id: int, not_before: datetime) -> None:
    with session_scope() as s:
        existing = s.execute(
            select(m.PendingAlert).where(
                m.PendingAlert.user_id == user_id,
                m.PendingAlert.listing_id == listing_id,
                m.PendingAlert.status == "pending",
            )
        ).scalar_one_or_none()
        if existing:
            return
        pa = m.PendingAlert(user_id=user_id, listing_id=listing_id, not_before=not_before)
        s.add(pa)


def fetch_due_pending_alerts(now: datetime) -> list[m.PendingAlert]:
    with session_scope() as s:
        rows = list(
            s.execute(
                select(m.PendingAlert).where(m.PendingAlert.status == "pending", m.PendingAlert.not_before <= now)
            ).scalars()
        )
        # Eager load IDs before session closes
        for r in rows:
            _ = r.id
        return rows


def set_pending_alert_status(pending_id: int, status: str) -> None:
    with session_scope() as s:
        s.execute(update(m.PendingAlert).where(m.PendingAlert.id == pending_id).values(status=status))


def get_all_active_filters() -> list[m.Filter]:
    with session_scope() as s:
        return list(s.execute(select(m.Filter).where(m.Filter.active == True)).scalars())


def get_listing_by_id(listing_id: int) -> m.Listing | None:
    with session_scope() as s:
        return s.execute(select(m.Listing).where(m.Listing.id == listing_id)).scalar_one_or_none()


def get_recent_listings(*, hours: int = 48, limit: int = 300) -> list[m.Listing]:
    since = datetime.utcnow() - timedelta(hours=hours)
    with session_scope() as s:
        rows = s.execute(
            select(m.Listing)
            .where(m.Listing.first_seen_at >= since)
            .order_by(m.Listing.first_seen_at.desc())
            .limit(limit)
        ).scalars()
        return list(rows)


# Favorites
def add_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        existing = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if existing:
            return False
        s.add(m.Favorite(user_id=user_id, listing_id=listing_id))
        return True


def remove_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        fav = s.execute(
            select(m.Favorite).where(m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id)
        ).scalar_one_or_none()
        if not fav:
            return False
        s.delete(fav)
        return True


def is_favorite(user_id: int, listing_id: int) -> bool:
    with session_scope() as s:
        cnt = s.execute(
            select(func.count()).select_from(m.Favorite).where(
                m.Favorite.user_id == user_id, m.Favorite.listing_id == listing_id
            )
        ).scalar_one()
        return cnt > 0


def get_user_favorite_listings(user_id: int, *, limit: int = 50) -> list[m.Listing]:
    with session_scope() as s:
        rows = s.execute(
            select(m.Listing)
            .join(m.Favorite, m.Favorite.listing_id == m.Listing.id)
            .where(m.Favorite.user_id == user_id)
            .order_by(desc(m.Favorite.created_at))
            .limit(limit)
        ).scalars()
        return list(rows)
