#!/usr/bin/env bash
set -euo pipefail
python3 -m py_compile jp226_logger.py jp226cp0/*.py
# Standard-library unit tests; they need no hardware (some skip without Pillow
# or the ESP32 source tree).
python3 -I -m unittest discover
