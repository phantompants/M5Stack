"""Tiny persistent key/value store in one JSON file.

Stands in for the firmware's NVS Preferences: stopped-session state
(state/stop_utc/next_utc), the saved timezone (tz_rule/tz_name), the waypoint
counter and the last waypoint. path=None keeps everything in memory only.
"""

import json
import os
from typing import Any, Optional


class StateStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.data = {}
        self.last_error = ""
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    self.data = loaded
            except (OSError, ValueError) as exc:
                self.last_error = "state file unreadable: {}".format(exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def put(self, **values: Any) -> None:
        self.data.update(values)
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
            self.last_error = ""
        except OSError as exc:
            self.last_error = "state file not saved: {}".format(exc)
