#!/usr/bin/env bash
set -euo pipefail

install -D -m 0644 jp226_logger.py "$STAGE$APP_INSTALL_DIR/jp226_logger.py"
for f in jp226cp0/*.py; do
  install -D -m 0644 "$f" "$STAGE$APP_INSTALL_DIR/$f"
done
install -D -m 0644 sample_data/demo_drive.nmea "$STAGE$APP_INSTALL_DIR/sample_data/demo_drive.nmea"
install -D -m 0644 logger.cfg.example "$STAGE$APP_INSTALL_DIR/logger.cfg.example"

tmp=$(mktemp)
cat >"$tmp" <<'LAUNCH'
#!/bin/sh
exec python3 /usr/share/APPLaunch/apps/jp226-gps-imu-logger-cp0/jp226_logger.py "$@"
LAUNCH
install -D -m 0755 "$tmp" "$STAGE$INSTALL_PREFIX/bin/jp226-gps-imu-logger-cp0"
rm -f "$tmp"
chmod 0755 "$STAGE$INSTALL_PREFIX/bin/jp226-gps-imu-logger-cp0"
