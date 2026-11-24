#!/usr/bin/env bash
set -euo pipefail
python3 -m py_compile jp226_obd_scanner.py jp226obd/*.py
python3 -I -m unittest discover
