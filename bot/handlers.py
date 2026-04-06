from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from database import repo
from database import models as m
from .states import FilterStates
from .keyboards import skip_kb, remove_kb

log = logging.getLogger(__name__)


router = Router()


@router.message(Command("start"))
async def cmd_start(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    text = (
        "Bienvenue sur le bot de surveillance immobilière !\n\n"
        "Utilisez /set_filters pour définir vos critères, /view_filters pour les voir, /edit_filters pour modifier, /premium pour les avantages, et /stop pour désactiver."
    )
    await msg.answer(text)


@router.message(Command("premium"))
async def cmd_premium(msg: Message):
    text = (
        "Plans:\n\n"
        "FREE: délai 5 minutes, 1 ville, filtres limités.\n"
        "PREMIUM: alertes instantanées, multi-villes, filtres avancés, priorité.\n\n"
        "Le paiement (Stripe) sera bientôt disponible."
    )
    await msg.answer(text)


@router.message(Command("stop"))
async def cmd_stop(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    with repo.session_scope() as s:
        u = s.get(m.User, user.id)
        u.active = False
        s.add(u)
    await msg.answer("Vous ne recevrez plus d'alertes. Utilisez /start pour réactiver.")


@router.message(Command("view_filters"))
async def cmd_view_filters(msg: Message):
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    flt = repo.get_user_filters(user.id)
    if not flt:
        await msg.answer("Aucun filtre. Utilisez /set_filters pour commencer.")
        return
    lines = ["Vos filtres actifs:"]
    for f in flt:
        lines.append(
            f"- {f.name}: ville={f.city or '-'}, CP={f.postal_code or '-'}, rayon={f.radius_km or '-'} km, prix={f.price_min or '-'}-{f.price_max or '-'}, surface≥{f.surface_min or '-'} m², pièces≥{f.rooms_min or '-'}"
        )
    await msg.answer("\n".join(lines))


@router.message(Command("edit_filters"))
async def cmd_edit_filters(msg: Message, state: FSMContext):
    # For MVP, reuse the same flow as set_filters overwriting 'default' filter
    await cmd_set_filters(msg, state)


@router.message(Command("set_filters"))
async def cmd_set_filters(msg: Message, state: FSMContext):
    await state.clear()
    await state.set_state(FilterStates.waiting_city)
    await msg.answer("Ville (ex: Paris) — ou tapez 'Passer' pour ignorer:", reply_markup=skip_kb())


@router.message(FilterStates.waiting_city, F.text)
async def set_city(msg: Message, state: FSMContext):
    city = msg.text.strip()
    if city.lower() == "passer":
        city = None
    await state.update_data(city=city)
    await state.set_state(FilterStates.waiting_postal)
    await msg.answer("Code postal (ex: 75011) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_postal, F.text)
async def set_postal(msg: Message, state: FSMContext):
    postal = msg.text.strip()
    if postal.lower() == "passer":
        postal = None
    await state.update_data(postal_code=postal)
    await state.set_state(FilterStates.waiting_radius)
    await msg.answer("Rayon (km, ex: 3) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_radius, F.text)
async def set_radius(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    radius = None
    if txt.lower() != "passer":
        try:
            radius = float(txt.replace(",", "."))
        except Exception:
            radius = None
    await state.update_data(radius_km=radius)
    await state.set_state(FilterStates.waiting_price_min)
    await msg.answer("Prix minimum (€) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_min, F.text)
async def set_price_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmin = None
    if txt.lower() != "passer":
        try:
            pmin = int(txt.replace(" ", ""))
        except Exception:
            pmin = None
    await state.update_data(price_min=pmin)
    await state.set_state(FilterStates.waiting_price_max)
    await msg.answer("Prix maximum (€) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_price_max, F.text)
async def set_price_max(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    pmax = None
    if txt.lower() != "passer":
        try:
            pmax = int(txt.replace(" ", ""))
        except Exception:
            pmax = None
    await state.update_data(price_max=pmax)
    await state.set_state(FilterStates.waiting_surface_min)
    await msg.answer("Surface minimum (m²) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_surface_min, F.text)
async def set_surface_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    surf = None
    if txt.lower() != "passer":
        try:
            surf = float(txt.replace(",", "."))
        except Exception:
            surf = None
    await state.update_data(surface_min=surf)
    await state.set_state(FilterStates.waiting_rooms_min)
    await msg.answer("Nombre de pièces minimum — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_rooms_min, F.text)
async def set_rooms_min(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    rooms = None
    if txt.lower() != "passer":
        try:
            rooms = int(txt)
        except Exception:
            rooms = None
    await state.update_data(rooms_min=rooms)
    await state.set_state(FilterStates.waiting_property_type)
    await msg.answer("Type de bien (studio, appartement, maison) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_property_type, F.text)
async def set_property_type(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    if txt.lower() == "passer":
        txt = None
    await state.update_data(property_type=txt)
    await state.set_state(FilterStates.waiting_budget_charges)
    await msg.answer("Budget max avec charges (€) — ou 'Passer':", reply_markup=skip_kb())


@router.message(FilterStates.waiting_budget_charges, F.text)
async def set_budget_charges(msg: Message, state: FSMContext):
    txt = msg.text.strip()
    budget = None
    if txt.lower() != "passer":
        try:
            budget = int(txt.replace(" ", ""))
        except Exception:
            budget = None
    await state.update_data(budget_max_with_charges=budget)

    data = await state.get_data()
    user = repo.get_or_create_user(msg.from_user.id, msg.from_user.username)
    repo.create_or_update_filter(
        user_id=user.id,
        name="default",
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
    await state.clear()
    await msg.answer("Filtres enregistrés ✅", reply_markup=remove_kb())