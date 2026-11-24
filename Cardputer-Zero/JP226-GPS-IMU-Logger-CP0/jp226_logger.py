#!/usr/bin/env python3
"""Entry point (like fb_hello.py in the AppBuilder example)."""

import os
import sys

# `python3 -I` and some launchers do not put the script directory on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jp226cp0.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
