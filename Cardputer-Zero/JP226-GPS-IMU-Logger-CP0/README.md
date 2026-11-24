# JP226 GPS-IMU Logger for the M5Stack CardputerZero (v2026.11.01)

Release versions use `yyyy.mm.nn`: the sequence starts at `01` each month and
increments for each release in that month.

A Python 3 port of the [JP226-GPS-IMU-Logger](https://github.com/phantompants/JP226-GPS-IMU-Logger)
ESP32 firmware: it logs a vehicle's GPS track to daily CSV files, with the same
state machine, thresholds and CSV columns, plus waypoints and a 320x170 display.

> **Hardware status:** installed on the CardputerZero (Raspberry Pi CM0) at
> `/home/pi/JP226-GPS-IMU-Logger-CP0`. The 320x170 RGB565 framebuffer, GPS Cap,
> BMI270 IMU (`/sys/bus/iio`), IMX219 camera (`/dev/video0`), and required Linux
> utilities have been confirmed. Run `python3 -m unittest discover` for the
> current desktop suite; hardware-dependent camera behavior still needs a
> device-side check after installing the updated package.

## Platform and package

The CardputerZero is a Raspberry Pi CM0 (quad Cortex-A53, 512 MB) running
Linux, with a 320x170 ST7789V LCD on `/dev/fb0` (RGB565), a 46-key matrix
keyboard, an IMU, an RTC, a 14-pin expansion header and a Grove port. This is a
Linux Python application; ESP32 firmware binaries do not run on the Zero.

CardputerZero apps use the official
[AppBuilder](https://github.com/CardputerZero/AppBuilder) toolchain. This app
is packaged as an aarch64 **`.deb`**, published with the `czdev` CLI to the
CardputerZero AppStore, and launched by the on-device **APPLaunch**. It is plain
Python 3 (like AppBuilder's `Python_FrameBuffer_HelloWorld` example).

## How it relates to the ESP32 firmware

Ported faithfully (same constants, from `include/Config.h`):

| Area | Behaviour |
| --- | --- |
| NMEA | GGA, RMC, GSA (VDOP), optional VTG; checksum required; no external library |
| Fix gate | fresh position/speed (<= 3 s), fresh UTC date/time (<= 5 s), >= 4 satellites, HDOP <= 5; then the plausibility guard (teleport and altitude-spike rejection, ported from `GpsPlausibility`) |
| States | `WAITING_FIX`, `MOVING`, `STOPPED_15M`, `STOPPED_HOURLY`, `FIX_LOST`, with the same start/stop hysteresis (>= 8 km/h at once, >= 3 km/h for 5 s, <= 1.5 km/h for 10 s) |
| Rates | 1 Hz moving; stop+15, 30, 45, 60 min then hourly while stopped; 60 s while fix lost; the stop session is persisted (`logger_state.json`) and continued after a restart |
| Files | `telemetry_YYYY-MM-DD.csv` named by **local** date, the identical 63-column header, UTC `timestamp` + `local_timestamp` with offset, timestamps never go backwards, a file with another header is renamed `-old.csv` |
| Time zone | offline Australian table from lat/lon (`TimezoneFallback`), same answer 3 checks 30 s apart before it changes, 100 km trust radius around an online lookup (lookup itself is a TODO), POSIX rules evaluated by a built-in engine so no tz database is needed |
| Waypoints | `W`/Enter saves a waypoint and attempts a camera photo; it can save a photo-only waypoint without a GPS fix |
| IMU | BMI270 IIO acceleration/gyro feed the unchanged telemetry schema and IMU page |
| Vehicle setup | On-device presets for road, surface, suspension and load plus per-wheel tyre PSI; saved in `logger_state.json` |
| Game/version | FOSSIL RECORD shows app/build details and dinosaur art; parked Dino Dash uses an articulated-tail T. rex or brontosaurus and auto-runs above 5 km/h |
| OBD-II | Read-only scanner supports USB, Wi-Fi, Bluetooth Classic and BLE ELM327 adapters, automatic protocol detection and optional full module/DID discovery |
| Settings | `key=value` file mirrors `logger.cfg` (`timezone`, `timezone_auto`) |

`telemetry_source` remains `LOCAL_GPS`. The 63-column telemetry header is
unchanged; setup values are written to the existing waypoint columns. Existing
tooling (`tools/merge_obd.py`, the KML scripts) reads the telemetry files
unchanged.

Deliberate differences: date and time come from RMC only (one atomic pair); an
empty course field is left to age out instead of becoming 0; the clock is kept
as a GPS-disciplined offset (a Linux app normally cannot set the system clock)
that never steps backwards; `uptime_ms` is milliseconds since the app started;
there is no ESP-NOW, AtomS3/Dial remote, temperature probe, weather, KML export
or web portal in this base.

## Hardware status and remaining checks

- **GPS:** the Cap receiver uses UART1 on GPIO14/15 at 115200 baud. On the
  current device, those pins are muxed as TXD1/RXD1, but only
  `/dev/serial0 -> /dev/ttyS0` is exposed; `/dev/ttyAMA1` and `/dev/ttyS1`
  are absent and the app has not confirmed NMEA traffic. The OS profile must
  expose the Cap's UART before the app can read it.
- **Display:** `/dev/fb0` is confirmed at 320x170, 16 bpp (RGB565).
- **IMU:** BMI270 is read through Linux IIO at its reported acceleration and
  angular-velocity scales. If no BMI270 is present, desktop use falls back to
  the null reader and shows the reason on DIAG when sensor initialization fails.
- **Camera:** the built-in IMX219 is available at `/dev/video0`; waypoint photos
  use `rpicam-still` and are stored under `~/telemetry/photos/`. With no GPS fix,
  the app saves a photo-only waypoint with blank coordinates. If capture fails,
  a GPS-valid waypoint is still saved and the error is shown/logged.
- **Keyboard:** Left/Right or Z/C and Tab/brackets change pages. On SETUP and
  OBD SCAN, Up/Down or F/X select an option; W/E change it. On DINO DASH, B
  switches between T. rex and brontosaurus; Space/Enter/semicolon jumps.
  `W` or Enter saves a waypoint on other pages; Esc/Q quits.
- **Number shortcuts:** 4 opens SPEED, 5 GPS, 6 IMU, 7 saves a waypoint/photo,
  and 8 opens OBD SCAN and starts a scan. The footer labels these functions.
- **AtomS3:** the Zero already has a BMI270, so an AtomS3 is not needed for
  local IMU. An optional ESP-NOW bridge is planned, but the Grove bus and
  device protocol must be verified before adding firmware. The app must remain
  usable when the AtomS3 is absent.
- **LoRa antenna:** keep the external antenna attached whenever the Cap LoRa
  module is powered, even if the logger is not actively using LoRa.

The `pi` account belongs to `video`, `input`, `dialout` and `i2c`. The installed
runtime is Linux aarch64 with Python 3.13.5.

## Install and run

On the CardputerZero (from the `.deb`, once published, or from a checkout):

```bash
sudo apt install python3 python3-pil python3-numpy python3-evdev python3-serial fonts-dejavu-core
sudo ./run.sh                      # display + keys + serial GPS
sudo ./run.sh --headless           # log only, status line every 10 s
python3 jp226_logger.py --gps-device /dev/ttyAMA0 --gps-baud 115200
```

Keys `4`/`5`/`6` open SPEED/GPS/IMU; `7` saves a waypoint/photo; `8` opens OBD
SCAN and starts a scan. `Left`/`Right` or `z`/`c` (also `Tab`/brackets)
navigate pages; `f`/`x` mirror Up/Down. `w`/`Enter` saves a waypoint except on
SETUP, OBD SCAN and DINO DASH; `Esc`/`q` quits. Pages: SPEED, GPS, IMU, LOGGER,
WAYPOINT, SETUP, OBD SCAN, DIAG, FOSSIL RECORD, and DINO DASH. The SETUP page
saves vehicle settings immediately. A waypoint automatically captures a photo
when `rpicam-still` is available, including a photo-only waypoint when GPS has
no fix.

The Cap GPS is wired to UART1 (`G14=TXD1`, `G15=RXD1`); auto-detection probes
`/dev/ttyAMA1` and `/dev/ttyS1` as well as the usual Linux serial aliases. Check
the OS UART profile if these nodes are missing. Useful read-only checks:

```bash
ls -l /dev/serial0 /dev/ttyAMA1 /dev/ttyAMA0 /dev/ttyS0 /dev/ttyS1 2>/dev/null
pinctrl get 14
pinctrl get 15
systemctl is-active serial-getty@ttyS0.service
```

On SETUP, Up/Down selects ROAD (`PAVED`, `GRAVEL`, `DIRT`, `TRAIL`), SURFACE
(`DRY`, `DAMP`, `WET`, `VERY WET`), FL/FR/RL/RR tyre PSI, front/rear suspension
(`SOFT`, `NORMAL`, `FIRM`) or LOAD (`SOLO`, `LIGHT`, `HEAVY`); W/E (or A/D)
changes the selected value. Defaults are PAVED, DRY, 36 PSI front, 45 PSI rear,
NORMAL suspension and LIGHT load. Tire PSI changes by 1. Dino Dash runs
automatically above 5 km/h and becomes playable below 3 km/h; B toggles the
T. rex/brontosaurus, and Space, Enter or semicolon jumps.

The OBD SCAN page offers USB, Wi-Fi, Bluetooth Classic and BLE adapters,
automatic or common ELM327 protocol selection, and a quick standard-PID scan
or a full read-only module/DID scan. Press Enter to start. Full scans may take
up to the configured 90-minute budget. Adapter host/address options can be
set in `~/telemetry/obd_scan.cfg`; copy `obd_scan.cfg.example` from the installed
app directory to start. The scanner only sends read-only OBD services and
adapter setup commands; it does not code, clear or write ECU settings.

## Build an AppStore-compatible package

The app's `app-builder.json` uses the official AppBuilder manifest schema. The
repository workflow **Build CardputerZero DEB** runs the unit tests and packages
the app with AppBuilder's official `pack-deb.sh`, producing an `arm64` `.deb`
artifact without a systemd service. Use this workflow to build the Python app;
the AppBuilder repository's online build workflow currently expects an SCons
project.

To publish a built package, clone
[CardputerZero/AppBuilder](https://github.com/CardputerZero/AppBuilder), run
`./czdev login` once, then from that checkout run:

```bash
./czdev bump --deb /path/to/jp226-gps-imu-logger-cp0_0.3-1_arm64.deb
./czdev publish --deb /path/to/the-version-shown-by-bump_arm64.deb
```

The first submission requires a real-device video in the publish pull request.
The store listing also needs real author display text, a unique four-character
`share_code`, and actual app screenshots; the current values/screenshots are
placeholders or desktop renders and must not be submitted. Follow
[`app-builder.json`](app-builder.json) and the official
[manifest requirements](https://github.com/CardputerZero/AppBuilder/blob/main/docs/APP_BUILDER_JSON.md).

On a desktop (standard library only; developed and tested on Windows with Python 3.14):

```bash
python -I -m unittest discover                      # all tests
python -I tools/make_demo_nmea.py                   # regenerate sample_data/demo_drive.nmea
python -I jp226_logger.py --nmea-file sample_data/demo_drive.nmea --out ./replay_out
python -I jp226_logger.py --nmea-file my_drive.nmea --out ./out --speed 10   # 10x real time
python jp226_logger.py --nmea-file sample_data/demo_drive.nmea --replay-seconds 100 --screenshot ./shots   # needs Pillow
```

Replay uses a virtual clock taken from the RMC times, runs as fast as possible
by default, ignores the host clock, and never touches a serial port or
framebuffer. `--screenshot DIR` writes one 320x170 PNG per page.

## Data and settings

Default data directory: `~/telemetry` (override with `data_dir` or
`JP226_CP0_DATA_DIR`, or `--out`). It contains `telemetry_*.csv`,
`waypoints_*.csv`, `events_*.csv`, waypoint photos in `photos/`, and
`logger_state.json` (stopped-session state, saved time zone, waypoint counter,
vehicle setup and Dino Dash personal best).

Settings file, first found of: `--config`, `$JP226_CP0_CONFIG`,
`~/.config/jp226-gps-imu-logger/logger.cfg`, `/etc/jp226-gps-imu-logger/logger.cfg`.
See [`logger.cfg.example`](logger.cfg.example). Keys: `timezone`, `timezone_auto`,
`gps_device`, `gps_baud`, `data_dir`, `clock_source`; each can be overridden by
`JP226_CP0_<KEY>`. **Wi-Fi is not configured here** (the firmware's `wifi_*`
keys are ignored and never stored); join networks with the OS. Do not put a Wi-Fi
password in this repository.

## Extension points

- IMU: [`jp226cp0/imu.py`](jp226cp0/imu.py) implements the BMI270 IIO reader;
  other hosts without that sensor use `NullImu`.
- AtomS3 ESP-NOW bridge: not implemented. Grove bus/pin and bridge protocol
  must be verified before adding firmware or claiming support.
- OBD-II: the on-device OBD SCAN page and [`jp226cp0/obdscan.py`](jp226cp0/obdscan.py)
  provide read-only discovery. Live telemetry logging is not implemented.
- Online time-zone lookup: `LocationTime.lookup_online()` is a documented TODO
  (`note_online_lookup()` and the 100 km trust logic are already in and tested).

## OBD-II scanner

The OBD SCAN page offers the same read-only scanner from the on-device UI. It
asks the vehicle what it supports, then logs each request and response. The
standalone CLI remains available for more detailed configuration. The scanner
only sends adapter (AT/ST) commands and diagnostic read services 01, 09 and 22;
it refuses ECU coding, clearing, and other write commands.

```bash
cp obd_scan.cfg.example obd_scan.cfg          # pick the link and ranges
python3 -m jp226cp0.obdscan --config obd_scan.cfg
python3 -m jp226cp0.obdscan --link usb        # vLinker USB, first USB port
python3 -m jp226cp0.obdscan --demo            # pretend car, no adapter
```

Links: `usb` (vLinker USB; most reliable here), `wifi` (join the adapter's
network first), `bt` (Bluetooth Classic, paired with `bluetoothctl`) and `ble`
(needs `python3-bleak`). Protocol 0 lets ELM327 auto-detect across common OBD-II
protocols; protocols 1-9 and A can be selected explicitly. The UI quick scan
reads adapter details and standard PIDs/Mode 09. The optional full read-only
scan also checks module addresses 700-7F7 and Mode 22 DIDs, within a configured
time budget (90 minutes by default). `did_ranges = everything` tries all
65,536 DIDs per module and can take many hours. Ignition on; engine running is
best. A vehicle or adapter may not support every protocol or PID.

The log is `obd_scans/obd_scan_<UTC time>.jsonl`: one line per request (raw
reply lines, and each module's reply decoded as positive, refusal with its
NRC, no data or error), ending in a summary of modules, VINs and answered
DIDs. Ctrl+C stops it and still writes the summary. Copy the file off and
upload it for analysis.

Tested only against the pretend adapter ([`fake_elm.py`](jp226cp0/fake_elm.py))
so far; nothing has talked to a real adapter yet.

## Must be replaced before `czdev publish`

The store rejects placeholders; these are deliberately still placeholders:

1. `store.share_code` in `app-builder.json` is `"TODO"` (replace with 4 letters/digits, unique store-wide).
2. `screenshots/*.png` are **renders of synthetic demo data made on a desktop**,
   not captures from a device: replace them with real device screenshots
   (re-render with `--screenshot` once the UI is tuned on the device).
3. `packaging/icon.png` is a generated placeholder pin icon (valid 256x256, but not designed).
4. The store requires a short video of the app running on a **real device** for a first release.
5. Review `permissions` in `app-builder.json` when adding any additional
   hardware or network behavior.

## Next-week test checklist

1. Boot the Zero, confirm Python 3 and the dependencies (`python3 -c "import PIL, numpy, evdev, serial"`).
2. `sudo ./run.sh`: does the UI appear on the LCD? Are the pages readable at 320x170? Does it quit with Esc/q?
3. Press every key; fix `KEYS_*` from the DIAG "LAST KEY" readout. Do Tab/[ ] page and w/Enter waypoint work?
4. Attach the Cap LoRa-1262 GPS; note the device node and baud; does auto-detect find it (DIAG: NMEA ok count rises, bad stays ~0)? Record the wiring.
5. Sky view: time to first fix, satellites, HDOP; does `fix_valid` become 1 and the state leave `WAITING_FIX`?
6. Check the CSV in the data directory: header equals the firmware's, `timestamp`/`local_timestamp`, local-date file name, `STOPPED_15M` row on stopping, 1 Hz when moving.
7. Run `tools/merge_obd.py` and the KML scripts from the ESP32 repo on a Zero-written CSV.
8. Reboot while stopped: does the stop schedule continue (`logger_state.json`)?
9. Drive a short loop, press the waypoint key at a known place; check `waypoints_*.csv`.
10. Unplug the GPS: `FIX_LOST` after 10 s, a row a minute, recovery when it returns.
11. Confirm RTC/system clock behaviour (assumption 6) and that no time ever goes backwards in the CSV.
12. Check BMI270 values on the IMU page, test camera capture both with and without GPS fix,
    and verify photo references and coordinates in waypoint rows.
13. Test the OBD SCAN page with a supported adapter, automatic protocol selection,
    and both quick and full read-only scans.
14. Build and install the `.deb` through AppBuilder; check APPLaunch launches it and as which user.
15. Replace the placeholders above, capture real screenshots and the demo video.

## Layout

```
jp226_logger.py        entry point        jp226cp0/hardware.py   ALL hardware assumptions
jp226cp0/nmea.py       NMEA parsing       jp226cp0/snapshot.py   fix validity gate
jp226cp0/logmode.py    state machine      jp226cp0/schedule.py   stopped schedule
jp226cp0/csvlog.py     CSV schema/files   jp226cp0/waypoints.py  waypoints/events
jp226cp0/tzfallback.py zone table         jp226cp0/posixtz.py    POSIX TZ rule engine
jp226cp0/location_time.py zone selection  jp226cp0/clock.py      GPS-disciplined clock
jp226cp0/gps_source.py serial detect      jp226cp0/ui.py         Pillow pages
jp226cp0/framebuffer.py /dev/fb0          jp226cp0/keys.py       evdev keys
tests/                 unittest suite     packaging/             AppBuilder files
tools/                 demo NMEA + icon generators
```

## License

MIT, same as the ESP32 repository (see [LICENSE](LICENSE)).
