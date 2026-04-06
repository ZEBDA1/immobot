from aiogram.fsm.state import State, StatesGroup


class FilterStates(StatesGroup):
    waiting_city = State()
    waiting_postal = State()
    waiting_radius = State()
    waiting_price_min = State()
    waiting_price_max = State()
    waiting_surface_min = State()
    waiting_rooms_min = State()
    waiting_property_type = State()
    waiting_budget_charges = State()