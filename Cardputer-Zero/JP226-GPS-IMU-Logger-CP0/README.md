# JP226 GPS-IMU Logger for the M5Stack CardputerZero (v0.2)

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
| Waypoints | `W`/Enter saves a waypoint and attempts a camera photo; existing 28-column waypoint schema stores the photo reference and vehicle setup |
| IMU | BMI270 IIO acceleration/gyro feed the unchanged telemetry schema and IMU page |
| Vehicle setup | On-device presets for road, surface, suspension and load plus per-wheel tyre PSI; saved in `logger_state.json` |
| Game/version | FOSSIL RECORD shows app/build details; parked Dino Dash uses an articulated-tail, Flexi-Rex-style T. rex that auto-runs above 5 km/h |
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

- **GPS:** the Cap GPS receiver is visible through the Linux serial interface;
  DIAG shows the detected port, baud and NMEA counters.
- **Display:** `/dev/fb0` is confirmed at 320x170, 16 bpp (RGB565).
- **IMU:** BMI270 is read through Linux IIO at its reported acceleration and
  angular-velocity scales. If no BMI270 is present, desktop use falls back to
  the null reader and shows the reason on DIAG when sensor initialization fails.
- **Camera:** the built-in IMX219 is available at `/dev/video0`; waypoint photos
  use `rpicam-still` and are stored under `~/telemetry/photos/`. If capture
  fails, the waypoint is still saved and the error is shown/logged.
- **Keyboard:** Left/Right and Tab/brackets change pages. On SETUP, Up/Down
  select a value and A/D adjust it. On DINO DASH, Space/Enter/semicolon jumps.
  `W` or Enter saves a waypoint on other pages; Esc/Q quits.
- **AtomS3:** the Zero already has a BMI270, so an AtomS3 is not needed for
  local IMU. It could provide ESP-NOW as a radio coprocessor, but that requires
  separate bridge firmware and a confirmed Grove transport; this release does
  not claim AtomS3-to-Linux ESP-NOW support.
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

Keys: `Left`/`Right` (or `Tab`/brackets) navigate pages; `w`/`Enter` saves a
waypoint outside the SETUP and DINO DASH pages; `Esc`/`q` quits. Pages: SPEED,
GPS, IMU, LOGGER, WAYPOINT, SETUP, DIAG, FOSSIL RECORD, and DINO DASH. The
SETUP page edits the vehicle profile, saved immediately to app state. A
waypoint automatically captures a photo when `rpicam-still` is available.

On SETUP, Up/Down selects ROAD (`PAVED`, `GRAVEL`, `DIRT`, `TRAIL`), SURFACE
(`DRY`, `DAMP`, `WET`, `VERY WET`), FL/FR/RL/RR tyre PSI, front/rear suspension
(`SOFT`, `NORMAL`, `FIRM`) or LOAD (`SOLO`, `LIGHT`, `HEAVY`); A/D changes the
selected value. The first tyre-pressure edit sets it to 30 PSI, then each edit
changes it by 1 PSI. Dino Dash runs automatically above 5 km/h and becomes
playable below 3 km/h; Space, Enter or semicolon jumps.

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
./czdev bump --deb /path/to/jp226-gps-imu-logger-cp0_0.2-1_arm64.deb
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
- OBD-II logging: [`jp226cp0/obd.py`](jp226cp0/obd.py), an empty `ObdSource` stub
  for a future module. Not implemented (but see the scanner below, whose
  [`elm327.py`](jp226cp0/elm327.py) link it can reuse).
- Online time-zone lookup: `LocationTime.lookup_online()` is a documented TODO
  (`note_online_lookup()` and the 100 km trust logic are already in and tested).

## OBD discovery scanner

A separate terminal tool that asks the car for everything it will answer and
logs every request and reply, for finding values such as the Ranger's tyre
pressures. It is **read-only**: it only sends adapter (AT/ST) commands and
services 01, 09 and 22, and refuses anything else.

```bash
cp obd_scan.cfg.example obd_scan.cfg          # pick the link and ranges
python3 -m jp226cp0.obdscan --config obd_scan.cfg
python3 -m jp226cp0.obdscan --link usb        # vLinker USB, first USB port
python3 -m jp226cp0.obdscan --demo            # pretend car, no adapter
```

Links: `usb` (vLinker USB; most reliable here), `wifi` (join the adapter's
network first), `bt` (Bluetooth Classic, paired with `bluetoothctl`) and `ble`
(needs `python3-bleak`; untested). Steps: adapter details, the standard PIDs
and Mode 09 (VIN, calibration IDs), every module address in 700-7F7, then a
Mode 22 DID sweep of each module found, within a time budget. The default
ranges take about 20-40 minutes; `did_ranges = everything` tries all 65,536
DIDs per module and takes hours. Ignition on, engine running is best.

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
2. `store.author.display_name` is `"TODO: real display name"` (replace with the real display name).
3. `screenshots/*.png` are **renders of synthetic demo data made on a desktop**,
   not captures from a device: replace them with real device screenshots
   (re-render with `--screenshot` once the UI is tuned on the device).
4. `packaging/icon.png` is a generated placeholder pin icon (valid 256x256, but not designed).
5. Confirm the package maintainer/contact field in `packaging/meta.env` is the contact you want displayed in package metadata.
6. The store requires a short video of the app running on a **real device** for a first release.
7. `permissions` is `additional_hardware: true` only. Set `imu: true` once an IMU reader exists,
   `background_service: true` if it is ever run as a service, `network: true` if the online lookup is added.

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
12. Identify the IMU chip/bus; write an `ImuReader`.
13. Build and install the `.deb` through AppBuilder; check APPLaunch launches it and as which user.
14. Replace the placeholders above, capture real screenshots and the demo video.

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
