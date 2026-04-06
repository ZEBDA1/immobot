from __future__ import annotations

from aiogram import Bot
from database import models as m
from scraper.base import ScrapedListing


def format_message(l: ScrapedListing, score_label: str, scam_tag: str | None) -> str:
    lines = []
    if l.title:
        lines.append(f"🏠 {l.title}")
    if l.location:
        lines.append(f"📍 {l.location}")
    if l.price is not None:
        lines.append(f"💰 {l.price:,} €".replace(",", " "))
    if l.surface_m2 is not None:
        lines.append(f"📐 {l.surface_m2:.0f} m²")
    if l.price_per_m2 is not None:
        lines.append(f"📊 {int(l.price_per_m2):,} €/m²".replace(",", " "))
    if score_label:
        lines.append(f"⭐ {score_label}")
    if scam_tag:
        lines.append(f"🚨 {scam_tag}")
    lines.append(f"🔗 {l.url}")
    return "\n".join(lines)


async def send_alert(bot: Bot, user: m.User, l: ScrapedListing, score_label: str, scam_tag: str | None):
    text = format_message(l, score_label, scam_tag)
    await bot.send_message(chat_id=user.telegram_id, text=text, disable_web_page_preview=False)