# JP226 OBD-II Scanner for M5Stack CardputerZero (v0.1)

A standalone, **read-only** OBD-II / UDS discovery scanner, live signal monitor,
and compatibility profiler for the M5Stack CardputerZero. It connects over
**USB serial**, **Wi-Fi (TCP)**, or **Bluetooth Low Energy (BLE)** to identify
what usable telemetry a vehicle exposes for:

- **`JP226-GPS-IMU-Logger`** (Cardputer Zero & Cardputer ADV ESP32 versions)
- **`EV-Sound-Emulator`** (planned Cardputer Zero & Cardputer ADV real-time
  synthesizer)

## Features

- **Three real transport links + simulated demo:**
  - **USB serial** (`/dev/serial/by-id/*`, `/dev/ttyUSB*`, `/dev/ttyACM*`) with
    auto-probing (`115200`, `38400`, `500000`, `9600`) for vLinker FS USB,
    OBDLink EX, FTDI, CH340, and CP210x adapters.
  - **Wi-Fi TCP** (`192.168.0.10:35000`, `192.168.4.1:35000`, or custom host/port).
  - **BLE GATT** (`bleak`) with active device scanning (`R` to refresh on-device
    or `--ble-scan` from CLI) for vLinker MC/FD/FS BLE, Vgate iCar Pro BLE, and
    OBDLink CX.
  - **Demo simulator** (`ranger`, `ev`, and `hybrid` vehicle profiles) for
    testing and desktop previews without an adapter.
- **Strictly read-only:** only adapter `AT`/`ST` commands and OBD/UDS read
  services `01`, `03`, `07`, `09`, and `22` are allowed; write, clear, reset,
  and security services are refused.
- **Downstream app compatibility analysis:**
  - **Logger Fit:** checks all 12 core `ObdProtocol.h` metrics (`RPM`, `SPEED`,
    `COOLANT`, `THROTTLE`, `LOAD`, `INTAKE`, `MAP`, `BARO`, `FUEL`, `AMBIENT`,
    `OIL`, `VOLTAGE`), MIL/DTC support (`01`, `03`, `07`), and Ford BCM TPMS
    DIDs (`280F`–`2816`, `280B`).
  - **EV Sound Fit:** evaluates real-time channels needed for audio synthesis
    (`Speed`, `Pedal/Throttle`, `RPM/Motor`, `Torque/Load`, `HV SoC/12V`),
    guesses powertrain (`BEV`, `HYBRID`, `ICE`), measures single-PID latency
    (ms / Hz), and tests **multi-PID batching** (`01 <PID1> <PID2> ...`).
- **Live Monitor page:** polls the recommended fast PIDs in real time and
  displays live Speed, RPM, Pedal/Throttle, Torque/Load, SoC/Voltage, Temp,
  and actual refresh rate (`Hz`).
- **Portable outputs:** saves both raw audit logs (`obd_scan_<UTC>.jsonl`) and
  structured capability profiles (`obd_profile_<VIN>_<UTC>.json`) under
  `~/telemetry/obd_scanner/`.

## Controls (CardputerZero 320x170 LCD + evdev)

| Key | Action |
| --- | --- |
| `Left` / `Right` / `Z` / `C` / `Tab` / `[` / `]` | Switch pages (`CONNECT`, `SCAN`, `LIVE`, `LOGGER FIT`, `EV SOUND FIT`, `MODULES`, `DIAG`) |
| `4`–`8` (directly below LCD) | Contextual page shortcuts shown in the bottom bar (e.g. `4 LINK`, `5 TGT`, `6 PROT`, `7 MODE`, `8 SCAN` / `LIVE`) |
| `Up` / `Down` / `F` / `X` | Select row on `CONNECT` or scroll `MODULES` |
| `A` / `D` / `W` / `E` / `-` / `=` | Change selected setting on `CONNECT` |
| `R` | Refresh USB ports or scan nearby BLE adapters |
| `Enter` / `Space` | Start/cancel scan, or toggle live polling on `LIVE` |
| `Esc` / `Q` | Quit cleanly |

## Running

On the CardputerZero:

```bash
sudo apt install python3 python3-pil python3-numpy python3-evdev python3-serial fonts-dejavu-core
# Optional for BLE adapters:
sudo apt install python3-bleak || pip3 install bleak
sudo ./run.sh
```

Headless CLI or desktop demo:

```bash
python3 jp226_obd_scanner.py --headless --demo --demo-profile ev --scan-mode ev_probe
python3 jp226_obd_scanner.py --usb-list
python3 jp226_obd_scanner.py --ble-scan
python3 jp226_obd_scanner.py --screenshot ./screenshots --demo-profile ev
python3 -I -m unittest discover
```

## Packaging (CardputerZero AppStore)

Follows the official [CardputerZero AppBuilder](https://github.com/CardputerZero/AppBuilder)
layout (`app-builder.json` + `packaging/`). Maintainer: `jp226prints <github@jp226prints.au>`.
Before submitting to the AppStore, replace the `"TODO"` `share_code` in
[`app-builder.json`](app-builder.json) and capture real-device screenshots.

## License

MIT — `jp226prints <github@jp226prints.au>` (see [LICENSE](LICENSE)).
