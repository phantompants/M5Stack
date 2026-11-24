#!/usr/bin/env bash
# Run on the CardputerZero (Linux). /dev/fb0, the keyboard (evdev) and the serial
# port normally need root or membership of the video/input/dialout groups
# (the .deb postinst adds user pi to them), as in the AppBuilder example.
set -e

if [ "$(uname)" != "Linux" ]; then
  echo "This only drives the display on Linux (aarch64 M5 CardputerZero)." >&2
  echo "On a desktop use:  python3 jp226_logger.py --nmea-file sample_data/demo_drive.nmea --out ./replay_out" >&2
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
exec sudo python3 "$DIR/jp226_logger.py" "$@"
