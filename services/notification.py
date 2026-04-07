from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import models as m
from scraper.base import ScrapedListing


def format_message(l: ScrapedListing, score_label: str, scam_tag: str | None) -> str:
    lines: list[str] = []
    if l.title:
        lines.append(f"<b>Nouvelle annonce</b>")
        lines.append(f"🏠 {escape(l.title)}")
    if l.location:
        lines.append(f"📍 {escape(l.location)}")
    if l.price is not None:
        lines.append(f"💰 {l.price:,} EUR".replace(",", " "))
    if l.surface_m2 is not None:
        lines.append(f"📐 {l.surface_m2:.0f} m2")
    if l.price_per_m2 is not None:
        lines.append(f"📊 {int(l.price_per_m2):,} EUR/m2".replace(",", " "))
    if score_label:
        lines.append(f"⭐ {score_label}")
    if scam_tag:
        lines.append(f"🚨 {scam_tag}")
    lines.append(f"🔗 <a href=\"{escape(l.url, quote=True)}\">Voir l'annonce</a>")
    return "\n".join(lines)


async def send_alert(bot: Bot, user: m.User, l: ScrapedListing, score_label: str, scam_tag: str | None):
    text = format_message(l, score_label, scam_tag)
    kb = None
    if getattr(l, "db_id", None):
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❤️ J'aime", callback_data=f"fav:add:{l.db_id}")],
            ]
        )
    await bot.send_message(chat_id=user.telegram_id, text=text, disable_web_page_preview=False, reply_markup=kb)
