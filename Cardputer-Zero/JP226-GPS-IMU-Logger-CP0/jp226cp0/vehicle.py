"""Editable vehicle setup recorded in waypoint rows."""

from typing import Dict, Optional, Tuple


FIELD_LABELS: Tuple[Tuple[str, str], ...] = (
    ("road_type", "ROAD"),
    ("road_surface", "SURFACE"),
    ("tyre_set_fl_psi", "FL PSI"),
    ("tyre_set_fr_psi", "FR PSI"),
    ("tyre_set_rl_psi", "RL PSI"),
    ("tyre_set_rr_psi", "RR PSI"),
    ("suspension_front", "FRONT SUSP"),
    ("suspension_rear", "REAR SUSP"),
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
