from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove


def yes_no_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Oui"), KeyboardButton(text="Non")]], resize_keyboard=True, one_time_keyboard=True
    )


def skip_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Passer")]], resize_keyboard=True, one_time_keyboard=True)


def main_panel_kb(*, is_admin: bool = False, is_premium: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🎯 Configurer mes filtres"), KeyboardButton(text="🗂️ Gérer mes filtres")],
        [KeyboardButton(text="✏️ Modifier mes filtres"), KeyboardButton(text="➕ Ajouter un filtre")],
        [KeyboardButton(text="💎 Plans Premium")]
        + ([KeyboardButton(text="🧠 Filtre IA")] if is_premium else [])
        + [KeyboardButton(text="❤️ Mes favoris")],
        [KeyboardButton(text="⏸️ Pause alertes")],
        [KeyboardButton(text="❓ Aide")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠️ Admin"), KeyboardButton(text="📦 Toutes annonces")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=False)


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
