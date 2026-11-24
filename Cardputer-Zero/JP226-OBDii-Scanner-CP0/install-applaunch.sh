#!/usr/bin/env bash
# Pull/build the AppBuilder .deb and install/upgrade JP226 OBD-II Scanner in APPLaunch.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ "$(uname)" != "Linux" ]; then
  echo "Run this script on the CardputerZero (Linux)." >&2
  exit 1
fi

if git rev-parse --short HEAD >/dev/null 2>&1; then
  git_hash="$(git rev-parse --short HEAD)"
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    git_hash="${git_hash}+"
  fi
  printf '{"git_hash":"%s","build_date":"%s"}\n' "$git_hash" "$(date -u +%F)" > build_stamp.json
fi

APPBUILDER_DIR="${APPBUILDER_DIR:-/tmp/CardputerZero-AppBuilder}"
if [ ! -x "$APPBUILDER_DIR/scripts/pack-deb.sh" ]; then
  rm -rf "$APPBUILDER_DIR"
  git clone --depth 1 https://github.com/CardputerZero/AppBuilder.git "$APPBUILDER_DIR"
fi

rm -rf "$DIR/build"
chmod +x "$DIR/packaging/build.sh" "$DIR/packaging/stage.sh" "$DIR/packaging/postinst" "$DIR/run.sh" "$DIR/jp226_obd_scanner.py"
"$APPBUILDER_DIR/scripts/pack-deb.sh" "$DIR" --output-dir "$DIR/build"

deb="$(find "$DIR/build" -maxdepth 1 -name 'jp226-obdii-scanner-cp0_*.deb' -print -quit)"
if [ -z "$deb" ]; then
  echo "No .deb produced in $DIR/build" >&2
  exit 1
fi

echo "Installing $deb into APPLaunch..."
sudo apt-get install -y --reinstall "$deb"
echo "Done! Launch 'JP226 OBD-II Scanner' from M5Stack APPLaunch."
