#!/usr/bin/env bash
set -e

if [ "$(uname)" != "Linux" ]; then
  echo "Display mode runs on Linux (CardputerZero). On desktop try:" >&2
  echo "  python3 jp226_obd_scanner.py --headless --demo --demo-profile ev" >&2
  exit 1
fi

DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -w /dev/fb0 ]; then
  exec python3 "$DIR/jp226_obd_scanner.py" "$@"
fi
exec sudo python3 "$DIR/jp226_obd_scanner.py" "$@"
