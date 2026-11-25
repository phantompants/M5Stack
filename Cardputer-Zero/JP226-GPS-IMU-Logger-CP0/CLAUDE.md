# CLAUDE.md

Python 3 port of the JP226-GPS-IMU-Logger ESP32 firmware for the M5Stack
CardputerZero (Raspberry Pi CM0, Linux aarch64, 320x170 RGB565 LCD on
`/dev/fb0`). See [README.md](README.md) for features, keys and hardware status.

## Commands

Development happens on Windows (Python 3.14); the device runs Python 3.13.5.

```bash
python -I -m unittest discover                      # full test suite, no hardware needed
python -I -m unittest tests.test_state_machine      # one module
python -I jp226_logger.py --nmea-file sample_data/demo_drive.nmea --out ./replay_out
python jp226_logger.py --nmea-file sample_data/demo_drive.nmea --replay-seconds 100 --screenshot ./shots   # needs Pillow
python -I tools/make_demo_nmea.py                   # regenerate the demo NMEA
bash packaging/build.sh                             # py_compile + tests + package
```

`run.sh` is for the device only (Linux, uses sudo).

## Rules

- **Core code and tests use the standard library only.** Pillow, numpy, evdev
  and pyserial are device/UI-only. Import them lazily, and let tests skip when
  they are missing.
- **Keep the firmware port faithful.** Constants, thresholds, the state machine
  and the **63-column CSV header** match the ESP32 firmware (`include/Config.h`).
  Do not change the CSV schema: external tools (`tools/merge_obd.py`, the KML
  scripts) read it. New per-row data goes into existing columns, the way the
  vehicle setup reuses the waypoint columns.
- **Put hardware assumptions in [jp226cp0/hardware.py](jp226cp0/hardware.py)**
  (serial candidates, bauds, key codes, framebuffer). Don't hard-code device
  paths anywhere else.
- **Time never goes backwards.** The clock is a GPS-disciplined offset
  ([clock.py](jp226cp0/clock.py)); the app does not set the system clock.
  Replay uses a virtual clock from the RMC times and must never touch a serial
  port or the framebuffer.
- **Don't claim hardware support that hasn't been checked on the device.**
  OBD-II, the AtomS3/ESP-NOW bridge and the online time-zone lookup are
  placeholders or TODOs. The README marks what has been confirmed on the device.
- **Never store Wi-Fi credentials** in settings or in the repo. Wi-Fi is set up
  in the OS.
- Keys 4-8 are page-specific actions shown in the footer
  ([keys.py](jp226cp0/keys.py) `PAGE_ACTIONS`). They never change pages.
- The app must keep working when optional hardware is missing (IMU, camera,
  Grove/Cap GPS, AtomS3). Fall back to null readers and report the reason on DIAG.

## Releases

- The version format is `yyyy.mm.nn`, with `nn` restarting at `01` each month.
  Update `app-builder.json` and the README title together.
- `app-builder.json` follows the official CardputerZero AppBuilder schema. The
  `.deb` is built by the **Build CardputerZero DEB** workflow and published with
  `czdev`.
- Placeholders that must not be submitted to the store: `store.share_code` is
  `"TODO"`, and `screenshots/*.png` are desktop renders. See the README section
  "Must be replaced before `czdev publish`".
- Review `permissions` in `app-builder.json` whenever new hardware or network
  behaviour is added.

## Layout

- `jp226_logger.py`: entry point; the code is in `jp226cp0/`
- `app.py`: wiring and main loop
- `nmea.py` → `snapshot.py` (fix gate) → `plausibility.py` → `logmode.py` (state machine) → `schedule.py` / `csvlog.py`
- `location_time.py`, `tzfallback.py`, `posixtz.py`: offline time-zone handling, no tz database
- `ui.py` (Pillow pages), `framebuffer.py`, `keys.py` (evdev)
- `gps_source.py`, `gps_power.py`: serial auto-detection and EXT/Grove 5V control
- `imu.py`, `camera.py`, `webshare.py`, `export.py`, `dino.py`, `obd.py` (unused interface)
- `statestore.py`: `logger_state.json`, which keeps the stop session, time zone, vehicle setup and game best
- `tests/helpers.py`: shared fixtures; `sample_data/`: demo NMEA
- Sibling app: `../JP226-OBDii-Scanner-CP0` handles read-only OBD discovery
