#!/usr/bin/env bash
set -euo pipefail

install -D -m 0644 jp226_obd_scanner.py "$STAGE$APP_INSTALL_DIR/jp226_obd_scanner.py"
for f in jp226obd/*.py; do
  install -D -m 0644 "$f" "$STAGE$APP_INSTALL_DIR/$f"
done
install -D -m 0644 obd_scanner.cfg.example "$STAGE$APP_INSTALL_DIR/obd_scanner.cfg.example"
if [ -f build_stamp.json ]; then
  install -D -m 0644 build_stamp.json "$STAGE$APP_INSTALL_DIR/build_stamp.json"
fi

tmp=$(mktemp)
cat >"$tmp" <<'LAUNCH'
#!/bin/sh
exec python3 /usr/share/APPLaunch/apps/jp226-obdii-scanner-cp0/jp226_obd_scanner.py "$@"
LAUNCH
install -D -m 0755 "$tmp" "$STAGE$INSTALL_PREFIX/bin/jp226-obdii-scanner-cp0"
rm -f "$tmp"
chmod 0755 "$STAGE$INSTALL_PREFIX/bin/jp226-obdii-scanner-cp0"
