#!/usr/bin/env bash
# GPS diagnostics for the CardputerZero. Run on the device AS pi (the user
# APPLaunch runs the app as), with the logger and the built-in GPS app closed:
#
#   bash tools/gps_diag.sh cap      # LoRa Cap GPS
#   bash tools/gps_diag.sh grove    # Grove GPS unit
#
# It only reads system state, except that it sets the same power rails the
# logger sets for that source (ext_5v_out / grove_5v_out / grove_fun) and then
# listens on each serial port. Paste the whole output back.

SRC="${1:-cap}"
LED=/sys/class/leds

section() { printf '\n== %s\n' "$1"; }

section "user and groups"
id
section "processes holding a GPS / logger"
pgrep -af 'jp226_logger|gps' || echo "none"

section "serial device nodes"
ls -l /dev/serial* /dev/ttyS* /dev/ttyAMA* 2>/dev/null
section "who has each port open (empty = nobody)"
for d in /dev/ttyS0 /dev/ttyS1 /dev/ttyAMA0 /dev/ttyAMA1; do
  [ -e "$d" ] && { printf '%s: ' "$d"; fuser "$d" 2>/dev/null; echo; }
done

section "kernel console and login gettys on the UART"
cat /proc/cmdline
for u in serial-getty@ttyS0 serial-getty@ttyAMA0 serial-getty@serial0 serial-getty@ttyS1; do
  printf '%s: %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"
done

section "UART boot config"
grep -hnE '^[[:space:]]*(enable_uart|dtoverlay|dtparam|core_freq|force_turbo)' \
  /boot/firmware/config.txt /boot/config.txt 2>/dev/null || echo "config.txt not readable"
section "pins 4 (grove_fun), 14 (TX), 15 (RX)"
pinctrl get 4 2>/dev/null; pinctrl get 14 2>/dev/null; pinctrl get 15 2>/dev/null

section "power rails before"
for l in ext_5v_out grove_5v_out grove_fun; do
  f="$LED/$l/brightness"
  if [ -e "$f" ]; then printf '%-13s %s  value=%s\n' "$l" "$(stat -c '%A %U:%G' "$f")" "$(cat "$f")"
  else printf '%-13s missing\n' "$l"; fi
done

section "python / pyserial"
python3 -c 'import sys, serial; print(sys.version.split()[0], "pyserial", serial.__version__)' 2>&1

section "installed logger"
APPDIR=/usr/share/APPLaunch/apps/jp226-gps-imu-logger-cp0
for d in "$APPDIR" /home/pi/JP226-GPS-IMU-Logger-CP0; do
  [ -d "$d" ] && printf '%s: %s\n' "$d" \
    "$(grep -h '__version__' "$d/jp226cp0/__init__.py" 2>/dev/null)"
done
grep -h gps_ ~/.config/jp226-gps-imu-logger/logger.cfg /etc/jp226-gps-imu-logger/logger.cfg 2>/dev/null \
  | grep -v '^#' || echo "no gps_* keys in logger.cfg"
grep -o '"gps_source": *"[a-z]*"' ~/telemetry/logger_state.json 2>/dev/null || echo "no saved gps_source"

section "built-in GPS app: what port/baud/power it uses"
for d in /usr/share/APPLaunch/apps/*; do
  case "$d" in *jp226*) continue ;; esac
  hits=$(grep -rIlE 'tty(S|AMA)[0-9]|serial0|ext_5v|grove_5v|grove_fun|baud' "$d" 2>/dev/null | head -5)
  [ -n "$hits" ] || continue
  echo "-- $d"
  grep -rIhoE '/dev/[A-Za-z0-9]+|ext_5v_out|grove_5v_out|grove_fun|[Bb]aud[A-Za-z_]*[ =:(]*[0-9]{4,6}|systemctl[^"]*' \
    "$d" 2>/dev/null | sort | uniq -c | head -20
done
ls /usr/share/APPLaunch/apps 2>/dev/null

section "set power rails for '$SRC' (as the logger does)"
setrail() {
  if echo "$2" > "$LED/$1/brightness" 2>/dev/null; then echo "$1=$2 ok"
  else echo "$1=$2 FAILED (permission?)"; fi
}
if [ "$SRC" = grove ]; then
  setrail ext_5v_out 0; setrail grove_fun 0; setrail grove_5v_out 1
  BAUDS="9600 115200 38400"
else
  setrail grove_5v_out 0; setrail ext_5v_out 1
  BAUDS="115200 9600 38400"
fi
echo "waiting 3 s for the receiver to start"; sleep 3

section "listen 3 s on each port and baud"
python3 - "$BAUDS" <<'PY'
import os, sys, time
try:
    import serial
except ImportError:
    sys.exit("pyserial missing")

def ok(line):
    line = line.strip()
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, cs = line[1:].rpartition("*")
    x = 0
    for ch in body:
        x ^= ord(ch)
    try:
        return int(cs[:2], 16) == x
    except ValueError:
        return False

seen = set()
for dev in ("/dev/serial0", "/dev/ttyS0", "/dev/ttyAMA0", "/dev/ttyAMA1", "/dev/ttyS1"):
    if not os.path.exists(dev) or os.path.realpath(dev) in seen:
        continue
    seen.add(os.path.realpath(dev))
    for baud in map(int, sys.argv[1].split()):
        try:
            port = serial.Serial(dev, baud, timeout=0.1)
        except Exception as exc:
            print("{} @{}: OPEN FAIL {}".format(dev, baud, exc))
            continue
        data, end = b"", time.monotonic() + 3
        while time.monotonic() < end:
            data += port.read(512)
        port.close()
        text = data.decode("ascii", "ignore")
        good = sum(ok(l) for l in text.splitlines())
        print("{} -> {} @{}: {} bytes, {} valid NMEA".format(
            dev, os.path.realpath(dev), baud, len(data), good))
        if data:
            print("   sample:", repr(data[:160]))
PY

section "done"
echo "Power rails are left set for '$SRC'."
