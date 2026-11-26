"""Lightweight persistent JSON state for scanner settings and last profile."""

import json
import os
from typing import Any, Dict


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.last_error = ""
        self._data: Dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = loaded
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self.last_error = str(exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def put(self, **kwargs: Any) -> None:
        self._data.update(kwargs)
        parent = os.path.dirname(os.path.abspath(self.path))
        try:
            os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
            self.last_error = ""
        except OSError as exc:
            self.last_error = str(exc)
