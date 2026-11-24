#!/usr/bin/env python3
"""Entry point for JP226 OBD-II Scanner on M5Stack CardputerZero."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jp226obd.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
