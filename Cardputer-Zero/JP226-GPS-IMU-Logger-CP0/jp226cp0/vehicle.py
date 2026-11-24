"""Editable vehicle setup recorded in waypoint rows."""

import datetime
from typing import Dict, Optional, Tuple


PROFILE_FIELD_LABELS: Tuple[Tuple[str, str], ...] = (
    ("make", "MAKE"),
    ("model", "MODEL"),
    ("year", "YEAR"),
)

PROFILE_MAKES = (
    "SELECT MAKE", "AUDI", "BMW", "BYD", "CHEVROLET", "FORD", "HONDA",
    "HOLDEN", "HYUNDAI", "ISUZU", "JEEP", "KIA", "LAND ROVER", "LEXUS",
    "MAZDA", "MERCEDES-BENZ", "MG", "MITSUBISHI", "NISSAN", "SUBARU",
    "SUZUKI", "TESLA", "TOYOTA", "VOLKSWAGEN", "VOLVO",
)

PROFILE_MODELS = {
    "AUDI": ("A3", "A4", "A6", "Q3", "Q5", "Q7", "E-TRON"),
    "BMW": ("1 SERIES", "3 SERIES", "5 SERIES", "X1", "X3", "X5", "I3", "I4"),
    "BYD": ("ATTO 3", "DOLPHIN", "SEAL", "SEALION 6"),
    "CHEVROLET": ("CAMARO", "COLORADO", "CORVETTE", "SILVERADO"),
    "FORD": ("EVEREST", "FOCUS", "MUSTANG", "RANGER"),
    "HOLDEN": ("ASTRA", "COMMODORE", "COLORADO", "TRAX"),
    "HONDA": ("ACCORD", "CIVIC", "CR-V", "HR-V"),
    "HYUNDAI": ("I30", "KONA", "SANTA FE", "TUCSON"),
    "ISUZU": ("D-MAX", "MU-X"),
    "JEEP": ("CHEROKEE", "COMPASS", "GRAND CHEROKEE", "WRANGLER"),
    "KIA": ("CERATO", "EV6", "NIRO", "SORENTO", "SPORTAGE"),
    "LAND ROVER": ("DEFENDER", "DISCOVERY", "DISCOVERY SPORT", "RANGE ROVER"),
    "LEXUS": ("ES", "IS", "NX", "RX", "UX"),
    "MAZDA": ("2", "3", "6", "BT-50", "CX-3", "CX-5", "CX-9"),
    "MERCEDES-BENZ": ("A-CLASS", "C-CLASS", "E-CLASS", "GLA", "GLC", "GLE"),
    "MG": ("3", "4", "ZS", "HS"),
    "MITSUBISHI": ("ASX", "ECLIPSE CROSS", "OUTLANDER", "PAJERO", "TRITON"),
    "NISSAN": ("JUKE", "LEAF", "NAVARA", "PATROL", "QASHQAI", "X-TRAIL"),
    "SUBARU": ("CROSSTREK", "FORESTER", "IMPREZA", "OUTBACK", "WRX"),
    "SUZUKI": ("JIMNY", "SWIFT", "VITARA"),
    "TESLA": ("MODEL 3", "MODEL S", "MODEL X", "MODEL Y"),
    "TOYOTA": ("CAMRY", "COROLLA", "HILUX", "LAND CRUISER", "PRADO", "RAV4"),
    "VOLKSWAGEN": ("AMAROK", "GOLF", "PASSAT", "TIGUAN", "TOUAREG"),
    "VOLVO": ("S60", "V60", "XC40", "XC60", "XC90"),
}

FIELD_LABELS: Tuple[Tuple[str, str], ...] = (
    ("road_type", "ROAD"),
    ("road_surface", "SURFACE"),
    ("tyre_set_fl_psi", "FL PSI"),
    ("tyre_set_fr_psi", "FR PSI"),
    ("tyre_set_rl_psi", "RL PSI"),
    ("tyre_set_rr_psi", "RR PSI"),
    ("suspension_front", "F SUSP"),
    ("suspension_rear", "R SUSP"),
    ("vehicle_load", "LOAD"),
)

CHOICES = {
    "road_type": ("PAVED", "GRAVEL", "DIRT", "TRAIL"),
    "road_surface": ("DRY", "DAMP", "WET", "VERY WET"),
    "suspension_front": ("SOFT", "NORMAL", "FIRM"),
    "suspension_rear": ("SOFT", "NORMAL", "FIRM"),
    "vehicle_load": ("SOLO", "LIGHT", "HEAVY"),
}
TYRE_FIELDS = frozenset(
    ("tyre_set_fl_psi", "tyre_set_fr_psi", "tyre_set_rl_psi", "tyre_set_rr_psi")
)


def defaults() -> Dict[str, object]:
    return {
        "road_type": "PAVED",
        "road_surface": "DRY",
        "tyre_set_fl_psi": 36,
        "tyre_set_fr_psi": 36,
        "tyre_set_rl_psi": 45,
        "tyre_set_rr_psi": 45,
        "suspension_front": "NORMAL",
        "suspension_rear": "NORMAL",
        "vehicle_load": "LIGHT",
    }


def load(saved: object) -> Dict[str, object]:
    result = defaults()
    if not isinstance(saved, dict):
        return result
    for field, choices in CHOICES.items():
        value = saved.get(field)
        if value in choices:
            result[field] = value
    for field in TYRE_FIELDS:
        value = saved.get(field)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and 0 <= value <= 100):
            result[field] = int(round(value))
    return result


def adjust(values: Dict[str, object], field: str, delta: int) -> bool:
    if field in CHOICES:
        options = CHOICES[field]
        try:
            index = options.index(values[field])
        except ValueError:
            index = -1 if delta > 0 else 0
        values[field] = options[(index + delta) % len(options)]
        return True
    if field in TYRE_FIELDS:
        current: Optional[object] = values.get(field)
        next_value = 30 if current is None else int(current) + delta
        values[field] = max(0, min(100, next_value))
        return True
    return False


def display_value(field: str, value: object) -> str:
    if value is None or value == "":
        return "--"
    if field in TYRE_FIELDS:
        return "{} PSI".format(value)
    return str(value)


def profile_year_choices(current_year: Optional[int] = None) -> Tuple[str, ...]:
    if current_year is None:
        current_year = datetime.date.today().year
    return ("SELECT YEAR",) + tuple(
        str(year) for year in range(current_year, 1979, -1))


def profile_defaults() -> Dict[str, str]:
    return {"make": PROFILE_MAKES[0], "model": "SELECT MODEL",
            "year": "SELECT YEAR"}


def load_profile(saved: object,
                 current_year: Optional[int] = None) -> Dict[str, str]:
    result = profile_defaults()
    if not isinstance(saved, dict):
        return result
    make = saved.get("make")
    if make in PROFILE_MAKES:
        result["make"] = make
    model_choices = ("SELECT MODEL",) + PROFILE_MODELS.get(result["make"], ())
    model = saved.get("model")
    if model in model_choices:
        result["model"] = model
    year = saved.get("year")
    if year in profile_year_choices(current_year):
        result["year"] = year
    return result


def adjust_profile(values: Dict[str, str], field: str, delta: int,
                   current_year: Optional[int] = None) -> bool:
    if field == "make":
        choices = PROFILE_MAKES
    elif field == "model":
        choices = ("SELECT MODEL",) + PROFILE_MODELS.get(values["make"], ())
    elif field == "year":
        choices = profile_year_choices(current_year)
    else:
        return False
    try:
        index = choices.index(values[field])
    except ValueError:
        index = -1 if delta > 0 else 0
    values[field] = choices[(index + delta) % len(choices)]
    if field == "make":
        values["model"] = "SELECT MODEL"
    return True
