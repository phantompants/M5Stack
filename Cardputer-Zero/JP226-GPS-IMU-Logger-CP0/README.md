# JP226 GPS-IMU Logger for the M5Stack CardputerZero (v2026.11.04)

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
| OBD-II | Placeholder only: the OBD page stores a make/model/year vehicle profile and lists planned readings. There is no adapter connection, scanning or OBD logging |
| Share | SHARE page key 4 starts a read-only Wi-Fi web page (port 8226) for Safari: per-day CSV, KML and GPX links and a ZIP of the last N days with waypoint photos. KML/GPX are rebuilt nightly in `~/telemetry/exports/` |
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

- **GPS:** three sources. Choose one with key 4 on the GPS page (saved on the
  device) or `gps_source=cap|grove|atom` in `logger.cfg`. The first two share
  the Zero's only pin UART (CM0 GPIO14/15, `/dev/serial0 -> /dev/ttyS0`):
  - **LORA CAP** (default): the Cap LoRa-1262 GPS at 115200 baud. The app turns
    on its EXT 5V supply (`/sys/class/leds/ext_5v_out`); without it the Cap GPS
    sends nothing. Verified on the device 2026-11-25.
  - **GROVE**: a Grove GPS unit on the Zero's own HY2.0-4P port. The app
    switches the port to UART (`grove_fun` low), turns on Grove 5V
    (`grove_5v_out`) and tries 9600 baud first. It turns EXT 5V off so the two
    receivers never share the RX line. Not usable with the NFC/Sub-1GHz Cap:
    see below.
  - **ATOMS3 USB**: the AtomS3 bridge on an Atomic GPS Base, plugged into a
    Zero USB port (`/dev/ttyACM0`). Its GPS Base NMEA comes over USB, so this
    is the source to use with the **NFC/Sub-1GHz (CC1101) Cap** fitted. Checked
    on the device 2026-11-26: that Cap drives a ~130 kHz clock onto GPIO15
    (UART RX), which stops both the Cap and Grove GPS, and the Grove socket on
    the Cap itself goes to GPIO3 (I2C1 SCL), not the UART.
  The power controls need the `gpio` group, which the package adds for `pi`.
  DIAG shows `GPS PWR`; the GPS page shows the chosen source.
- **Display:** `/dev/fb0` is confirmed at 320x170, 16 bpp (RGB565).
- **IMU:** BMI270 is read through Linux IIO at its reported acceleration and
  angular-velocity scales. If no BMI270 is present, desktop use falls back to
  the null reader and shows the reason on DIAG when sensor initialization fails.
- **Camera:** the built-in IMX219. Photos go to `~/telemetry/photos/` at
  1640x1232 with the GPS fix in their EXIF (position, altitude, UTC time,
  speed, course, DOP, satellites) and the local capture time, so iPhone
  Photos and Google Earth place them on the map. Verified on the device
  2026-11-26.
  - **WAYPOINT** page: W/Enter or 7 saves a waypoint with a photo
    (`rpicam-still`). With no GPS fix it saves a photo-only waypoint with
    blank coordinates; if capture fails, a GPS-valid waypoint is still saved.
  - **CAMERA** page: a live preview (picamera2, about 13 frames/s) with fix,
    satellites, position and time beside it. 7 or Enter takes a photo only,
    no waypoint, named `IMG_YYYYMMDD_HHMMSS.jpg` in local time.
  - **GALLERY** page: browse the photos, newest first (4/5 or Up/Down), with
    the position read back from each photo.
- **ENV page:** an M5Stack ENV unit on the Grove port (ENV III: SHT30 +
  QMP6988; ENV IV: SHT40 + BMP280) shows temperature, humidity and pressure.
  Key 4 switches the Grove port to I2C and turns Grove 5V on; it is refused
  while the GPS source is GROVE, because a GPS on that bus would disturb the
  Zero's own I2C devices. The right half lists the DS18B20 probe temperatures
  the Dial, Atom Echo and black box send through the AtomS3. The ENV unit
  drivers are unit-tested against datasheet examples but not yet tried on the
  device.
- **Keyboard:** Left/Right or Z/C and Tab/brackets change pages. On SETUP and
  OBD, Up/Down or F/X select an option; W/E change it. On DINO DASH, B
  switches between T. rex and brontosaurus; Space/Enter/semicolon jumps.
  `W` or Enter saves a waypoint on other pages; Esc/Q quits.
- **Number keys:** 4-8 act within the current page and never change pages.
  The footer shows only the actions that page offers, coloured by type:
  navigation blue, edit/select yellow, save green, unavailable gray. 7 saves a
  waypoint/photo on SPEED, GPS, IMU, LOGGER, WAYPOINT, DIAG and FOSSIL RECORD
  and saves the setup on SETUP. On OBD: 4 make, 5 model, 6 year, 7 save
  profile, 8 shows that the OBD connection is not implemented.
- **AtomS3 bridge:** an AtomS3 running `atoms3_zero_bridge` from the ESP32
  repo (see its `docs/zero-bridge.md`) connects by USB, not Grove. It sends the
  ESP-NOW beacon for the Dial, Echo and black box, and lists the devices it
  hears on its own screen. The app reads its status (DIAG `ATOMS3`, with the
  number of paired Dials), sends it the time once a second and tells it
  whether to forward GPS. The app works without it and picks it up when it is
  plugged in. Verified on the device 2026-11-26: the app reads the bridge
  status and its GPS NMEA, and the AtomS3 shows ZERO USB while the app runs.
- **FOSSIL RECORD page:** version, build and the dinosaurs on the left; a
  system monitor on the right: CPU, SoC temperature, RAM, free storage,
  battery (BQ27220 gauge: percent, volts, charging) and uptime. The battery
  level is also sent to the AtomS3, so the Dial and CYD show it.
- **ESP-NOW page:** every JP226 device the AtomS3 hears (Dial, Echo, black
  box, CYD, Atom GPS nodes, other loggers), with type, MAC, signal, time
  since last heard and PAIRED for controllers the app is answering. Green
  under 5 s, orange under 30 s, grey after; gone after 2 minutes.
- **M5Dial:** through the AtomS3 the app answers the Dial like the ESP32
  logger: waypoints (with the Dial's category), road, surface, tyre pressures
  (front sets FL and FR, rear sets RL and RR), suspension and load, all saved.
  It sends the Dial its status twice a second and a position report once a
  second. On the Dial, tap the CONNECTION page until the target is `CP ZERO`
  (or `ANY LOGGER`); this needs Dial firmware v0.21. While the app is not
  running, the AtomS3 keeps the Dial paired and refuses its commands.
  Desktop-tested only so far.
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

Keys `4`-`8` are page actions shown in the footer (for example `7` saves a
waypoint/photo on SPEED, GPS, IMU, LOGGER, WAYPOINT, DIAG and FOSSIL RECORD);
they never change pages. `Left`/`Right` or `z`/`c` (also `Tab`/brackets)
navigate pages; `f`/`x` mirror Up/Down. `w`/`Enter` saves a waypoint except on
SETUP, OBD and DINO DASH; `Esc`/`q` quits. Pages: SPEED, GPS, IMU, LOGGER,
SHARE, WAYPOINT, SETUP, OBD, DIAG, FOSSIL RECORD, and DINO DASH. The SETUP page
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

The OBD page is a placeholder for future OBD-II readings; see
[OBD-II placeholder](#obd-ii-placeholder).

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
- AtomS3 ESP-NOW bridge: [`jp226cp0/bridge.py`](jp226cp0/bridge.py) handles
  the link, GPS, time and the Dial; [`jp226cp0/espnow.py`](jp226cp0/espnow.py)
  packs the JP226 packets. Black box and temperature packets are only counted.
- OBD-II: not implemented. The OBD page only stores a vehicle profile;
  [`jp226cp0/obd.py`](jp226cp0/obd.py) is the unused `ObdSource` interface a
  future adapter connection would implement. For read-only OBD discovery, use
  the separate `JP226-OBDii-Scanner-CP0` app.
- Online time-zone lookup: `LocationTime.lookup_online()` is a documented TODO
  (`note_online_lookup()` and the 100 km trust logic are already in and tested).

## SHARE: logs and photos to a phone over Wi-Fi

1. Put the CardputerZero and the iPhone on the same Wi-Fi network, or press
   **8** on the SHARE page for the Zero's own hotspot (below).
2. Open the SHARE page and press **4** (WEB ON). The page shows an address such
   as `http://192.168.1.220:8226/`; `http://cardputerzero.local:8226/` also
   works on iPhones.
3. Open the address in Safari. Each day has Track CSV, Google Earth KML, GPX,
   Waypoints CSV and Events CSV links. "Download ZIP" bundles the last N days
   (1, 3, 7, 14, 30 or all; keys **5**/**6** set the default) with their
   KML/GPX and waypoint photos. Safari saves to Files > Downloads.

KML and GPX files are written to `~/telemetry/exports/track_YYYY-MM-DD.kml` /
`.gpx`. A background job converts each finished day after local midnight, and
catches up on days missed while the device was off; key **7** exports now,
including today. Downloads always rebuild a stale KML/GPX first. Track
segments break after a gap of more than 5 minutes; waypoints become
placemarks/`<wpt>`. The telemetry CSV itself is never changed.

**Photos:** the page shows the newest photos, and **All photos** opens the
gallery. Tap a photo to open it full size, then the share button > **Save to
Photos**; the GPS location comes with it. **Download all (ZIP)** puts every
photo in Files.

**Hotspot (key 8):** with no Wi-Fi around, the Zero makes its own network
(`JP226-CZ-xxxx`, WPA2, password made on the device the first time). The
SHARE page shows a QR code to scan with the iPhone camera to join, then a
second one that opens the page (`http://10.42.0.1:8226/`). The hotspot turns
the web page on too. While it is on, the Zero is off your normal Wi-Fi; key 8
again turns it off and NetworkManager rejoins. The password lives only in the
NetworkManager profile `JP226-Hotspot`, never in the app's files. The app
runs as a background user service that polkit does not count as the logged-in
user, so the package installs a narrow polkit rule
(`/usr/share/polkit-1/rules.d/60-jp226-gps-imu-logger-hotspot.rules`) that
lets `pi` change Wi-Fi; it is removed with the package.

The server is read-only: it serves only the daily log files, their exports and
photos, with no login. Turn it off (key 4) on untrusted networks. The
on/off choice is remembered. Change the port with `web_port` in `logger.cfg`.

## OBD-II placeholder

The OBD page does not connect to an adapter, scan the vehicle or log OBD
values. It lets you pick a built-in vehicle profile and shows which readings a
future version plans to add:

- **Profile:** make, model and year from a built-in list of common makes and
  models; years run from the current year back to 1980. Press 4/5/6 (or F/X to
  select and W/E to change) and 7 or Enter to save. The profile is kept in
  `logger_state.json` as `vehicle_profile`.
- **Planned standard OBD-II readings:** engine RPM, engine load, coolant
  temperature and fuel level.
- **Planned vehicle-dependent readings:** TPMS tyre pressure and temperature,
  12 V battery voltage and ignition times. Many vehicles expose these only
  through manufacturer-specific data, so availability will vary by vehicle.

None of these values are connected or logged today, and the 63-column
telemetry CSV schema is unchanged. Key 8 reports that the OBD connection is not
implemented. For read-only OBD discovery scans, use the separate
`JP226-OBDii-Scanner-CP0` app.

## Must be replaced before `czdev publish`

The store rejects placeholders; these are deliberately still placeholders:

1. `store.share_code` in `app-builder.json` is `"TODO"` (replace with 4 letters/digits, unique store-wide).
2. `screenshots/*.png` are **renders of synthetic demo data made on a desktop**,
   not captures from a device: replace them with real device screenshots
   (re-render with `--screenshot` once the UI is tuned on the device).
3. The store requires a short video of the app running on a **real device** for a first release.
4. Review `permissions` in `app-builder.json` when adding any additional
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
13. On the OBD page, pick and save a make/model/year profile, restart the app
    and check it is kept; check that 8 shows the not-implemented status.
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
jp226cp0/camera.py     photos + preview   jp226cp0/exif.py       GPS EXIF
jp226cp0/webshare.py   SHARE web page     jp226cp0/hotspot.py    Wi-Fi hotspot
jp226cp0/bridge.py     AtomS3 link        jp226cp0/espnow.py     JP226 packets
jp226cp0/env.py        Grove ENV unit
tests/                 unittest suite     packaging/             AppBuilder files
tools/                 demo NMEA + old placeholder icon generators
```

## License

MIT, same as the ESP32 repository (see [LICENSE](LICENSE)).
