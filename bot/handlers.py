from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from html import escape
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from config import settings
from database import repo
from database import models as m
from services.scheduler import get_sources_health, sample_source_listings, run_full_scan_once
from services.matcher import match_and_score
from services.scam import detect_scam
from services.notification import send_alert, format_message
from scraper.base import ScrapedListing
from .states import FilterStates
from .keyboards import skip_kb, main_panel_kb
from services.ai_features import ai_parse_filter

log = logging.getLogger(__name__)

router = Router()


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    txt = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    txt = txt.lower().strip()
    txt = re.sub(r"[^a-z0-9]+", " ", txt)
    return " ".join(txt.split())


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.admin_telegram_ids


def _panel(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    return main_panel_kb(is_admin=_is_admin(msg.from_user.id), is_premium=user.is_premium)


def _panel_for_user_id(telegram_id: int):
    u = repo.get_user_by_telegram_id(telegram_id)
    is_premium = bool(u and u.is_premium)
    return main_panel_kb(is_admin=_is_admin(telegram_id), is_premium=is_premium)


def _parse_target_telegram_id(args: str | None) -> int | None:
    if not args:
        return None
    raw = args.strip().split()[0]
    if raw.startswith('@'):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _sanitize_filter_name(name: str | None) -> str:
    if not name:
        return "default"
    cleaned = "".join(ch for ch in name.strip() if ch.isalnum() or ch in ("-", "_"))
    return cleaned[:32] if cleaned else "default"


def _next_filter_name(filters: list[m.Filter]) -> str:
    existing = {f.name for f in filters}
    idx = 1
    while True:
        candidate = f"filter_{idx}"
        if candidate not in existing:
            return candidate
        idx += 1


async def _start_filter_wizard(msg: Message, state: FSMContext, *, filter_name: str):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = False
            s.add(u)

    await state.clear()
    await state.update_data(filter_name=filter_name)
    await state.set_state(FilterStates.waiting_city)
    await msg.answer(
        f"?? Configuration du filtre <b>{filter_name}</b>\n\n?? Ville (ex: Paris) - ou tapez 'Passer' pour ignorer:",
        reply_markup=skip_kb(),
    )


def _filter_summary(f: m.Filter) -> str:
    return (
        f"\n?? <b>Filtre: {f.name}</b>\n"
        f"?? Zone: {f.city or '-'} | CP: {f.postal_code or '-'} | Rayon: {f.radius_km or '-'} km\n"
        f"?? Prix: {f.price_min or '-'} -> {f.price_max or '-'} EUR\n"
        f"??? Budget charges: {f.budget_max_with_charges or '-'} EUR\n"
        f"?? Surface min: {f.surface_min or '-'} m2\n"
        f"??? Pieces min: {f.rooms_min or '-'}\n"
        f"?? Type: {f.property_type or '-'}"
    )


def _manage_filters_kb(filters: list[m.Filter], *, is_premium: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for f in filters:
        rows.append([
            InlineKeyboardButton(text=f"?? Modifier: {f.name}", callback_data=f"editflt:{f.name}"),
            InlineKeyboardButton(text="??? Supprimer", callback_data=f"delflt:{f.name}"),
        ])
    if is_premium:
        rows.append([InlineKeyboardButton(text="? Nouveau filtre", callback_data="addflt:auto")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _favorite_action_kb(listing_id: int, *, liked: bool) -> InlineKeyboardMarkup:
    if liked:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="?? Retirer des favoris", callback_data=f"fav:remove:{listing_id}")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="?? J'aime", callback_data=f"fav:add:{listing_id}")]]
    )


async def _show_manage_filters(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("?? Aucun filtre actif. Utilise /set_filters pour créer le premier.", reply_markup=_panel(msg))
        return
    text = (
        "??? <b>Gestion des filtres</b>\n\n"
        f"Tu as <b>{len(filters)}</b> filtre(s).\n"
        "Choisis un filtre à modifier ou supprimer:"
    )
    await msg.answer(text, reply_markup=_manage_filters_kb(filters, is_premium=user.is_premium))


async def _send_initial_matches(msg: Message, user: m.User, flt: m.Filter, *, limit: int = 5) -> int:
    # Build a pool of recent matches, then interleave sources to diversify output
    pool_by_source: dict[str, list[ScrapedListing]] = {}
    for db_l in repo.get_recent_listings(hours=72, limit=400):
        if repo.has_sent_alert(user.id, db_l.id):
            continue
        l = ScrapedListing(
            source=db_l.source,
            external_id=db_l.external_id,
            url=db_l.url,
            title=db_l.title,
            price=db_l.price,
            surface_m2=db_l.surface_m2,
            price_per_m2=db_l.price_per_m2,
            location=db_l.location,
            rooms=db_l.rooms,
            description=db_l.description,
            images=db_l.images.split(",") if db_l.images else None,
            published_at=db_l.published_at,
            db_id=db_l.id,
        )
        res = match_and_score(flt, l)
        if not res.matched:
            continue
        pool_by_source.setdefault(l.source, []).append(l)

    # Round-robin per source
    sent = 0
    sources = list(pool_by_source.keys())
    idx = 0
    while sent < limit and sources:
        src = sources[idx % len(sources)]
        arr = pool_by_source.get(src) or []
        if not arr:
            sources.pop(idx % len(sources))
            continue
        l = arr.pop(0)
        try:
            scam = detect_scam(l)
            scam_tag = "Potentielle arnaque" if scam.is_scam else None
            await send_alert(msg.bot, user, l, score_label="", scam_tag=scam_tag)
            if l.db_id is not None:
                repo.mark_alert_sent(user.id, l.db_id)
            sent += 1
        except Exception:
            pass
        idx += 1
    return sent


@router.message(Command("start"))
async def cmd_start(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.active:
        with repo.session_scope() as s:
            u = s.get(m.User, user.id)
            if u:
                u.active = True
                s.add(u)

    plan = "PREMIUM" if user.is_premium else "FREE"
    admin_tag = "\nMode admin actif" if _is_admin(msg.from_user.id) else ""

    text = (
        "<b>Bienvenue sur ton assistant immobilier</b>\n\n"
        f"Statut: {plan}{admin_tag}\n\n"
        "Actions rapides:\n"
        "- Configurer tes filtres\n"
        "- Recevoir des alertes ciblees\n"
        "- Gerer ton abonnement"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("premium"))
async def cmd_premium(msg: Message):
    text = (
        "?? <b>Plans disponibles</b>\n\n"
        "?? <b>FREE</b>\n"
        "• Délai: 5 minutes\n"
        "• 1 ville max\n"
        "• Filtres limités\n\n"
        "?? <b>PREMIUM</b>\n"
        "• Alertes instantanées\n"
        "• Multi-villes\n"
        "• Filtres avancés\n"
        "• Priorité de traitement\n"
        "• ?? Filtre IA (description ? filtre)\n\n"
        "?? Multi-filtres:\n"
        "• /add_filter [nom]\n"
        "• /edit_filters [nom]"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("ai_filter"))
async def cmd_ai_filter(msg: Message, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    raw = (command.args or "").strip()
    if not raw:
        await msg.answer(
            "Usage: /ai_filter &lt;description libre&gt;\nExemple: /ai_filter Paris 11, 2 pièces, 35m2 min, 1200€ max",
            reply_markup=_panel(msg),
        )
        return
    parsed = ai_parse_filter(raw)
    if not parsed:
        await msg.answer("? Impossible d'interpréter ce texte. Réessaye avec plus de détails.", reply_markup=_panel(msg))
        return

    # Determine target filter name
    filters = repo.get_user_filters(user.id)
    if not user.is_premium:
        target = "default"
    else:
        target = _next_filter_name(filters)

    repo.create_or_update_filter(
        user_id=user.id,
        name=target,
        price_min=parsed.get("price_min"),
        price_max=parsed.get("price_max"),
        surface_min=parsed.get("surface_min"),
        rooms_min=parsed.get("rooms_min"),
        property_type=parsed.get("property_type"),
        budget_max_with_charges=parsed.get("budget_max_with_charges"),
        city=parsed.get("city"),
        postal_code=parsed.get("postal_code"),
        radius_km=parsed.get("radius_km"),
    )

    # Reactivate alerts if needed
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = True
            s.add(u)

    flt = next((f for f in repo.get_user_filters(user.id) if f.name == target), None)
    summary = _filter_summary(flt) if flt else ""
    await msg.answer(f"? Filtre IA <b>{target}</b> enregistré.{summary}", reply_markup=_panel(msg))

    if flt:
        sent = await _send_initial_matches(msg, user, flt, limit=5)
        if sent > 0:
            await msg.answer(f"?? {sent} annonce(s) récente(s) correspondant déjà à ce filtre.")
        else:
            await msg.answer("?? Aucune annonce récente à pousser pour ce filtre. Je surveille les nouvelles en continu.")


@router.message(Command("my_id"))
async def cmd_my_id(msg: Message):
    await msg.answer(f"?? Votre Telegram ID: <code>{msg.from_user.id}</code>", reply_markup=_panel(msg))


@router.message(Command("stop"))
async def cmd_stop(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = False
            s.add(u)
    await msg.answer("?? Alertes en pause. Utilise /start pour réactiver.", reply_markup=_panel(msg))


@router.message(Command("view_filters"))
async def cmd_view_filters(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    flt = repo.get_user_filters(user.id)
    if not flt:
        await msg.answer("?? Aucun filtre actif. Utilise /set_filters pour commencer.", reply_markup=_panel(msg))
        return

    plan = "?? PREMIUM" if user.is_premium else "?? FREE"
    header = f"?? <b>Vos filtres actifs</b>\nStatut: {plan}\nNombre: {len(flt)}"
    text = header + "\n" + "\n".join(_filter_summary(f) for f in flt)
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("manage_filters"))
async def cmd_manage_filters(msg: Message):
    await _show_manage_filters(msg)


@router.message(Command("favorites"))
async def cmd_favorites(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    favs = repo.get_user_favorite_listings(user.id, limit=50)
    if not favs:
        await msg.answer("?? Tu n'as pas encore de favoris. Clique sur ?? sous une annonce.", reply_markup=_panel(msg))
        return

    await msg.answer(f"?? <b>Mes favoris</b> ({len(favs)})", reply_markup=_panel(msg))
    for i, l in enumerate(favs, start=1):
        title = escape(l.title or "Annonce")
        location = escape(l.location or "-")
        price = f"{l.price:,} EUR".replace(",", " ") if l.price is not None else "-"
        surf = f"{l.surface_m2:.0f} m2" if l.surface_m2 is not None else "-"
        link = escape(l.url, quote=True)
        txt = (
            f"#{i} <b>{title}</b>\n"
            f"?? {location}\n"
            f"?? {price}\n"
            f"?? {surf}\n"
            f"?? <a href=\"{link}\">Voir l'annonce</a>"
        )
        await msg.answer(txt, disable_web_page_preview=True, reply_markup=_favorite_action_kb(l.id, liked=True))


@router.message(Command("edit_filters"))
async def cmd_edit_filters(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("?? Aucun filtre à modifier. Utilise /set_filters pour créer le premier.", reply_markup=_panel(msg))
        return
    target = _sanitize_filter_name(command.args)
    if target == "default" and user.is_premium and command.args is None and len(filters) > 1:
        names = ", ".join(f.name for f in filters)
        await msg.answer(
            f"?? Tu as plusieurs filtres: <code>{names}</code>\nUtilise /edit_filters &lt;nom&gt; pour choisir.",
            reply_markup=_panel(msg),
        )
        return
    if target not in {f.name for f in filters}:
        names = ", ".join(f.name for f in filters)
        await msg.answer(
            f"? Filtre introuvable: <code>{target}</code>\nFiltres existants: <code>{names}</code>",
            reply_markup=_panel(msg),
        )
        return
    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("set_filters"))
async def cmd_set_filters(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    target = _sanitize_filter_name(command.args)

    if not user.is_premium and target != "default":
        await msg.answer(
            "?? En mode FREE, un seul filtre est disponible (default).\nPasse en PREMIUM pour créer plusieurs filtres.",
            reply_markup=_panel(msg),
        )
        return

    if not user.is_premium and filters and target != "default":
        target = "default"

    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("add_filter"))
async def cmd_add_filter(msg: Message, state: FSMContext, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not user.is_premium:
        await msg.answer(
            "?? Multi-filtres réservé au PREMIUM.\nEn FREE, utilise /set_filters pour ton filtre unique.",
            reply_markup=_panel(msg),
        )
        return

    requested = _sanitize_filter_name(command.args) if command.args else None
    if requested and requested != "default":
        target = requested
    else:
        target = _next_filter_name(filters)

    if target in {f.name for f in filters}:
        await msg.answer(f"?? Le filtre <code>{target}</code> existe déjà. Utilise /edit_filters {target}", reply_markup=_panel(msg))
        return

    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(Command("delete_filter"))
async def cmd_delete_filter(msg: Message, command: CommandObject):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    name = _sanitize_filter_name(command.args)
    if not name:
        await msg.answer("Usage: /delete_filter &lt;nom&gt;", reply_markup=_panel(msg))
        return
    ok = repo.delete_filter(user.id, name)
    if ok:
        await msg.answer(f"??? Filtre <b>{name}</b> supprimé.", reply_markup=_panel(msg))
    else:
        await msg.answer(f"? Filtre introuvable: <code>{name}</code>.", reply_markup=_panel(msg))


@router.message(FilterStates.waiting_city, F.text)
async def set_city(msg: Message, state: FSMContext):
    city = msg.text.strip()
    if city.lower() == "passer":
        city = None
    await state.update_data(city=city)
    await state.set_state(FilterStates.waiting_postal)
    await msg.answer("??? Code postal (ex: 75011) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_postal, F.text)
async def set_postal(msg: Message, state: FSMContext):
    postal = msg.text.strip()
    if postal.lower() == "passer":
        postal = None
    await state.update_data(postal_code=postal)
    await state.set_state(FilterStates.waiting_radius)
    await msg.answer("?? Rayon (km, ex: 3) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_radius, F.text)
async def set_radius(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    radius = None
    if txt.lower() != "passer":
        try:
            radius = float(txt.replace(",", "."))
        except ValueError:
            radius = None
    await state.update_data(radius_km=radius)
    await state.set_state(FilterStates.waiting_price_min)
    await msg.answer("?? Prix minimum (EUR) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_min, F.text)
async def set_price_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmin = None
    if txt.lower() != "passer":
        try:
            pmin = int(txt.replace(" ", ""))
        except ValueError:
            pmin = None
    await state.update_data(price_min=pmin)
    await state.set_state(FilterStates.waiting_price_max)
    await msg.answer("?? Prix maximum (EUR) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_max, F.text)
async def set_price_max(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmax = None
    if txt.lower() != "passer":
        try:
            pmax = int(txt.replace(" ", ""))
        except ValueError:
            pmax = None
    await state.update_data(price_max=pmax)
    await state.set_state(FilterStates.waiting_surface_min)
    await msg.answer("?? Surface minimum (m2) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_surface_min, F.text)
async def set_surface_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    surf = None
    if txt.lower() != "passer":
        try:
            surf = float(txt.replace(",", "."))
        except ValueError:
            surf = None
    await state.update_data(surface_min=surf)
    await state.set_state(FilterStates.waiting_rooms_min)
    await msg.answer("??? Nombre de pieces minimum - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_rooms_min, F.text)
async def set_rooms_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    rooms = None
    if txt.lower() != "passer":
        try:
            rooms = int(txt)
        except ValueError:
            rooms = None
    await state.update_data(rooms_min=rooms)
    await state.set_state(FilterStates.waiting_property_type)
    await msg.answer("?? Type de bien (studio, appartement, maison) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_property_type, F.text)
async def set_property_type(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    if txt.lower() == "passer":
        txt = None
    await state.update_data(property_type=txt)
    await state.set_state(FilterStates.waiting_budget_charges)
    await msg.answer("?? Budget max avec charges (EUR) - ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_budget_charges, F.text)
async def set_budget_charges(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    budget = None
    if txt.lower() != "passer":
        try:
            budget = int(txt.replace(" ", ""))
        except ValueError:
            budget = None
    await state.update_data(budget_max_with_charges=budget)

    data = await state.get_data()
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filter_name = _sanitize_filter_name(data.get("filter_name"))
    repo.create_or_update_filter(
        user_id=user.id,
        name=filter_name,
        price_min=data.get("price_min"),
        price_max=data.get("price_max"),
        surface_min=data.get("surface_min"),
        rooms_min=data.get("rooms_min"),
        property_type=data.get("property_type"),
        budget_max_with_charges=data.get("budget_max_with_charges"),
        city=data.get("city"),
        postal_code=data.get("postal_code"),
        radius_km=data.get("radius_km"),
    )
    flt = next((f for f in repo.get_user_filters(user.id) if f.name == filter_name), None)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        if u:
            u.active = True
            s.add(u)
    await state.clear()
    await msg.answer(
        f"? Filtre <b>{filter_name}</b> enregistré avec succès. Les alertes sont réactivées.",
        reply_markup=_panel(msg),
    )
    if flt:
        sent = await _send_initial_matches(msg, user, flt, limit=5)
        if sent > 0:
            await msg.answer(f"?? J'ai trouvé {sent} annonce(s) récente(s) correspondant déjà à ce filtre.")
        else:
            await msg.answer("?? Aucune annonce récente à pousser pour ce filtre. Je surveille les nouvelles en continu.")


@router.message(Command("admin"))
async def cmd_admin(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    text = (
        "??? <b>Mode admin</b>\n\n"
        "Commandes:\n"
        "• /grant_premium &lt;telegram_id&gt;\n"
        "• /revoke_premium &lt;telegram_id&gt;\n"
        "• /debug_sources\n"
        "• /debug_sample &lt;source&gt;\n"
        "• /full_scan\n"
        "• /all_listings\n"
        "• /admin\n\n"
        "Exemple: <code>/grant_premium 123456789</code>"
    )
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(Command("grant_premium"))
async def cmd_grant_premium(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    target_id = _parse_target_telegram_id(command.args)
    if not target_id:
        await msg.answer("Usage: /grant_premium &lt;telegram_id&gt;", reply_markup=_panel(msg))
        return
    repo.set_user_premium(target_id, True)
    user = repo.get_user_by_telegram_id(target_id)
    status = "?? PREMIUM" if user and user.is_premium else "Inconnu"
    await msg.answer(f"? Utilisateur <code>{target_id}</code> mis en premium. Statut: {status}", reply_markup=_panel(msg))


@router.message(Command("revoke_premium"))
async def cmd_revoke_premium(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    target_id = _parse_target_telegram_id(command.args)
    if not target_id:
        await msg.answer("Usage: /revoke_premium &lt;telegram_id&gt;", reply_markup=_panel(msg))
        return
    repo.set_user_premium(target_id, False)
    await msg.answer(f"? Utilisateur <code>{target_id}</code> repassé en FREE.", reply_markup=_panel(msg))


@router.message(Command("debug_sources"))
async def cmd_debug_sources(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    health = get_sources_health()
    if not health:
        await msg.answer("?? Pas encore de données source. Laisse tourner le scheduler 1 cycle.", reply_markup=_panel(msg))
        return

    lines = ["?? <b>Etat des sources scraping</b>"]
    for source, h in sorted(health.items()):
        disabled_until = h.get("disabled_until")
        status = "? active"
        if disabled_until:
            status = f"?? backoff jusqu'à {disabled_until}"
        if not disabled_until and h.get("consecutive_empty_runs", 0) >= 3:
            status = "?? active (aucune annonce recente)"
        safe_source = escape(str(source))
        safe_last_error = escape(str(h.get("last_error") or "-"))
        lines.append(
            "\n".join(
                [
                    f"• <b>{safe_source}</b> - {status}",
                    f"  ? runs={h.get('total_runs', 0)} | success={h.get('total_success', 0)} | fail={h.get('total_failures', 0)}",
                    f"  ? listings_total={h.get('total_listings', 0)} | last_batch={h.get('last_listings_count', 0)} | empty_streak={h.get('consecutive_empty_runs', 0)}",
                    f"  ? streak_fail={h.get('consecutive_failures', 0)}",
                    f"  ? last_error={safe_last_error}",
                ]
            )
        )
    await msg.answer("\n\n".join(lines), reply_markup=_panel(msg))


@router.message(Command("debug_sample"))
async def cmd_debug_sample(msg: Message, command: CommandObject):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    arg = (command.args or "").strip().lower()
    if not arg:
        await msg.answer("Usage: /debug_sample &lt;source&gt;\nSources: paruvendu, entreparticuliers, leboncoin, seloger, pap", reply_markup=_panel(msg))
        return

    filters = repo.get_all_active_filters()
    locs_set: set[tuple[str | None, str | None]] = set()
    for f in filters:
        locs_set.add((f.city, f.postal_code))
    locs = list(locs_set) or [(None, None)]

    listings, last_error = await sample_source_listings(arg, locs, limit_locations=2)
    if listings:
        # Show up to 3 sample items
        lines = [f"?? Echantillon <b>{escape(arg)}</b>: {len(listings)} annonce(s)"]
        for i, l in enumerate(listings[:3], start=1):
            title = escape(l.title or "Annonce")
            loc = escape(l.location or "-")
            link = escape(l.url or "#", quote=True)
            price = f"{l.price:,} EUR".replace(",", " ") if l.price is not None else "-"
            lines.append(f"#{i} <b>{title}</b> — {loc} — {price}\n?? <a href=\"{link}\">Ouvrir</a>")
        await msg.answer("\n".join(lines), disable_web_page_preview=True, reply_markup=_panel(msg))
        return
    safe_err = escape(last_error or "Aucune annonce et aucune erreur retournée.")
    await msg.answer(
        f"?? Echantillon <b>{escape(arg)}</b>: 0 annonce.\nDernière erreur: <code>{safe_err}</code>",
        reply_markup=_panel(msg),
    )


@router.message(Command("full_scan"))
async def cmd_full_scan(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("⛔ Accès refusé.", reply_markup=_panel(msg))
        return
    note = await msg.answer("🚀 Full scan lancé en arrière-plan. Je t’envoie le résultat dès que c’est fini.")

    async def _run_and_report():
        try:
            counts = await run_full_scan_once(msg.bot)
            if counts:
                lines = ["✅ Full scan terminé:"]
                for src, n in sorted(counts.items()):
                    lines.append(f"• {escape(src)}: {n} annonce(s)")
                await note.edit_text("\n".join(lines))
            else:
                await note.edit_text("✅ Full scan terminé: 0 annonce collectée.")
        except Exception as e:
            await note.edit_text(f"❌ Echec du full scan: <code>{escape(str(e))}</code>")

    asyncio.create_task(_run_and_report())


@router.message(Command("all_listings"))
async def cmd_all_listings(msg: Message):
    if not _is_admin(msg.from_user.id):
        await msg.answer("? Accès refusé.", reply_markup=_panel(msg))
        return
    rows = repo.get_recent_listings(hours=72, limit=30)
    if not rows:
        await msg.answer("?? Aucune annonce récente en base.", reply_markup=_panel(msg))
        return
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(f"?? <b>Toutes les annonces récentes</b> ({len(rows)})", reply_markup=_panel(msg))
    for db_l in rows:
        l = ScrapedListing(
            source=db_l.source,
            external_id=db_l.external_id,
            url=db_l.url,
            title=db_l.title,
            price=db_l.price,
            surface_m2=db_l.surface_m2,
            price_per_m2=db_l.price_per_m2,
            location=db_l.location,
            rooms=db_l.rooms,
            description=db_l.description,
            images=db_l.images.split(",") if db_l.images else None,
            published_at=db_l.published_at,
            db_id=db_l.id,
        )
        text = format_message(l, score_label="", scam_tag=None)
        await msg.answer(
            text,
            disable_web_page_preview=True,
            reply_markup=_favorite_action_kb(db_l.id, liked=repo.is_favorite(user.id, db_l.id)),
        )


@router.message(F.text == "?? Configurer mes filtres")
async def panel_set_filters(msg: Message, state: FSMContext):
    await _start_filter_wizard(msg, state, filter_name="default")


@router.message(F.text == "Configurer mes filtres")
async def panel_set_filters_legacy(msg: Message, state: FSMContext):
    await _start_filter_wizard(msg, state, filter_name="default")


@router.message(F.text == "?? Voir mes filtres")
async def panel_view_filters(msg: Message):
    await cmd_view_filters(msg)


@router.message(F.text == "Voir mes filtres")
async def panel_view_filters_legacy(msg: Message):
    await cmd_view_filters(msg)


@router.message(F.text == "??? Gérer mes filtres")
async def panel_manage_filters(msg: Message):
    await _show_manage_filters(msg)


@router.message(F.text == "?? Modifier mes filtres")
async def panel_edit_filters(msg: Message, state: FSMContext):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    filters = repo.get_user_filters(user.id)
    if not filters:
        await msg.answer("?? Aucun filtre à modifier.", reply_markup=_panel(msg))
        return
    if len(filters) == 1:
        await _start_filter_wizard(msg, state, filter_name=filters[0].name)
        return
    names = ", ".join(f.name for f in filters)
    await msg.answer(
        f"?? Tu as plusieurs filtres: <code>{names}</code>\nUtilise /edit_filters &lt;nom&gt;.",
        reply_markup=_panel(msg),
    )


@router.message(F.text == "Modifier mes filtres")
async def panel_edit_filters_legacy(msg: Message, state: FSMContext):
    await panel_edit_filters(msg, state)


@router.message(F.text == "?? Plans Premium")
async def panel_premium(msg: Message):
    await cmd_premium(msg)


@router.message(F.text == "Plans Premium")
async def panel_premium_legacy(msg: Message):
    await cmd_premium(msg)


@router.message(F.text == "?? Mes favoris")
async def panel_favorites(msg: Message):
    await cmd_favorites(msg)


@router.message(F.text == "? Ajouter un filtre")
async def panel_add_filter(msg: Message, state: FSMContext):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.is_premium:
        await msg.answer(
            "?? Multi-filtres réservé au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel(msg),
        )
        return
    filters = repo.get_user_filters(user.id)
    target = _next_filter_name(filters)
    await _start_filter_wizard(msg, state, filter_name=target)


@router.message(F.text == "?? Filtre IA")
async def panel_ai_filter(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    if not user.is_premium:
        await msg.answer(
            "?? Fonctionnalité réservée au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel(msg),
        )
        return
    await msg.answer(
        "Décris ton besoin en une phrase.\nEx: ‘Mantes-La-Jolie dans un rayon de 3 km, loyer max 780€’.\nPuis envoie: /ai_filter &lt;ta description&gt;",
        reply_markup=_panel(msg),
    )


@router.message(F.text == "?? Pause alertes")
async def panel_stop(msg: Message):
    await cmd_stop(msg)


@router.message(F.text == "Pause alertes")
async def panel_stop_legacy(msg: Message):
    await cmd_stop(msg)


@router.message(F.text == "??? Admin")
async def panel_admin(msg: Message):
    await cmd_admin(msg)


@router.message(F.text == "?? Toutes annonces")
async def panel_all_listings(msg: Message):
    await cmd_all_listings(msg)


@router.message(F.text == "? Aide")
async def panel_help(msg: Message):
    text = (
        "? <b>Aide rapide</b>\n\n"
        "• /start: ouvrir le tableau de bord\n"
        "• /set_filters [nom]: créer/modifier un filtre\n"
        "• /add_filter [nom]: ajouter un filtre (PREMIUM)\n"
        "• /manage_filters: menu des filtres (boutons)\n"
        "• /view_filters: voir vos critères\n"
        "• /favorites: voir vos annonces aimées\n"
        "• /edit_filters [nom]: modifier un filtre existant\n"
        "• /premium: voir les plans\n"
        "• /stop: mettre les alertes en pause\n"
        "• /my_id: afficher votre Telegram ID"
    )
    if _is_admin(msg.from_user.id):
        text += "\n• /admin: commandes d'administration"
        text += "\n• /debug_sources: diagnostic des sources"
        text += "\n• /all_listings: voir toutes les annonces récentes"
    await msg.answer(text, reply_markup=_panel(msg))


@router.message(F.text == "Aide")
async def panel_help_legacy(msg: Message):
    await panel_help(msg)


@router.message(F.text)
async def panel_text_fallback(msg: Message, state: FSMContext):
    # Keep wizard flows untouched.
    if await state.get_state():
        return
    raw = (msg.text or "").strip()
    if not raw or raw.startswith("/"):
        return

    t = _normalize_text(raw)
    if "configurer" in t and "filtre" in t:
        await _start_filter_wizard(msg, state, filter_name="default")
        return
    if "gerer" in t and "filtre" in t:
        await _show_manage_filters(msg)
        return
    if "modifier" in t and "filtre" in t:
        await panel_edit_filters(msg, state)
        return
    if "ajouter" in t and "filtre" in t:
        await panel_add_filter(msg, state)
        return
    if "plans" in t and "premium" in t:
        await cmd_premium(msg)
        return
    if "filtre" in t and "ia" in t:
        await panel_ai_filter(msg)
        return
    if "favoris" in t:
        await cmd_favorites(msg)
        return
    if "pause" in t and "alerte" in t:
        await cmd_stop(msg)
        return
    if t == "admin":
        await cmd_admin(msg)
        return
    if "toutes" in t and "annonce" in t:
        await cmd_all_listings(msg)
        return
    if t == "aide":
        await panel_help(msg)
        return


@router.callback_query(F.data.startswith("editflt:"))
async def cb_edit_filter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    name = _sanitize_filter_name(callback.data.split(":", 1)[1] if callback.data else None)
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    filters = repo.get_user_filters(user.id)
    if name not in {f.name for f in filters}:
        await callback.message.answer(
            "? Ce filtre n'existe plus. Ouvre à nouveau la gestion des filtres.",
            reply_markup=_panel_for_user_id(callback.from_user.id),
        )
        return
    await _start_filter_wizard(callback.message, state, filter_name=name)


@router.callback_query(F.data == "addflt:auto")
async def cb_add_filter(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    if not user.is_premium:
        await callback.message.answer(
            "?? Multi-filtres réservé au PREMIUM.\nUtilise /premium pour voir les avantages.",
            reply_markup=_panel_for_user_id(callback.from_user.id),
        )
        return
    filters = repo.get_user_filters(user.id)
    target = _next_filter_name(filters)
    await _start_filter_wizard(callback.message, state, filter_name=target)


@router.callback_query(F.data.startswith("delflt:"))
async def cb_delete_filter(callback: CallbackQuery):
    await callback.answer()
    if not callback.message or not callback.data:
        return
    name = _sanitize_filter_name(callback.data.split(":", 1)[1])
    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    ok = repo.delete_filter(user.id, name)
    if ok:
        await callback.message.answer(
            f"??? Filtre <b>{name}</b> supprimé.", reply_markup=_panel_for_user_id(callback.from_user.id)
        )
    else:
        await callback.message.answer(
            f"? Filtre introuvable: <code>{name}</code>.", reply_markup=_panel_for_user_id(callback.from_user.id)
        )


@router.callback_query(F.data.startswith("fav:add:"))
async def cb_add_favorite(callback: CallbackQuery):
    await callback.answer()
    if not callback.data:
        return
    try:
        listing_id = int(callback.data.split(":", 2)[2])
    except Exception:
        if callback.message:
            await callback.message.answer("? Impossible d'ajouter ce favori.")
        return

    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    added = repo.add_favorite(user.id, listing_id)
    if callback.message:
        if added:
            await callback.message.answer("?? Ajouté aux favoris.")
        else:
            await callback.message.answer("?? Déjà dans tes favoris.")
        try:
            await callback.message.edit_reply_markup(reply_markup=_favorite_action_kb(listing_id, liked=True))
        except Exception:
            pass


@router.callback_query(F.data.startswith("fav:remove:"))
async def cb_remove_favorite(callback: CallbackQuery):
    await callback.answer()
    if not callback.data:
        return
    try:
        listing_id = int(callback.data.split(":", 2)[2])
    except Exception:
        if callback.message:
            await callback.message.answer("? Impossible de retirer ce favori.")
        return

    user = repo.get_or_create_user(callback.from_user.id, callback.from_user.username)
    removed = repo.remove_favorite(user.id, listing_id)
    if callback.message:
        if removed:
            await callback.message.answer("?? Retiré des favoris.")
        else:
            await callback.message.answer("?? Cette annonce n'était plus en favoris.")
        try:
            await callback.message.edit_reply_markup(reply_markup=_favorite_action_kb(listing_id, liked=False))
        except Exception:
            pass



