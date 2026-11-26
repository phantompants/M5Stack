# CYD dashboard

`cyd_dashboard` turns a "Cheap Yellow Display" (ESP32-2432S028R: ESP32,
2.8-inch 320x240 ILI9341 touchscreen) into a dashboard and waypoint remote for
any JP226 logger. It pairs exactly like the M5Dial: it listens for the
logger's discovery beacon on channels 1 to 13, sends a heartbeat every second,
and shows the status the logger sends back twice a second. With the
CardputerZero it talks through the AtomS3 USB bridge
([zero-bridge.md](zero-bridge.md)).

## Screen

The layout is portrait (240x320), with the USB port at the bottom, and has
three pages, chosen with the tabs under the top bar: DASH, TILT and TRIPS.

**Touch setup:** the first start after each new firmware shows four arrows,
one corner at a time; tap the tip of each. The calibration is saved with that
build and the screen orientation, so it is not asked again until the next
flash or a flip. Hold the tab strip for 1.5 s to run it again at any time.

| Area | Shows |
| --- | --- |
| Top bar | The paired logger and signal strength. While searching: the logger type it looks for and the channel. **Tap it** to change the logger type: CP ZERO (default), CARDPUTER ADV, CARDPUTER, CORE2, ANY LOGGER. **Hold it for 1.5 s** to turn the screen upside down (USB at the top). Both choices are saved |
| Middle | Speed, fix and satellites, log state, the logger's battery, road type and surface, tyre pressures, the nearby place and the last waypoint |
| DASH | Speed, fix and satellites, log state, the logger's battery, road, surface and tyre pressures, the nearby place and the last waypoint. WAYPOINT, FUEL, CAMP and HAZARD buttons, 2 x 2: each saves a waypoint with that category on the logger; the message line above them says SAVED with the ID, or why not |
| TILT | Pitch and roll half-dials (green under 20 degrees, orange under 30, red beyond), from the logger's IMU in its once-a-second position report. **Hold** on level ground to zero them for how the logger is mounted (saved). Shows NO TILT DATA if the logger sends none |
| TRIPS | Two trip meters, A and B: distance, moving time, average and top speed, from the logger's GPS positions (only while moving faster than 2 km/h with a fix, so parked GPS drift is not counted). Saved every 30 s, so they survive power off. **Hold** a trip for 1.5 s to reset it |

The RGB LED on the back blinks blue while searching, stays green while linked
and flashes red when the logger refuses a waypoint (for example, no fix).

The logger counts the CYD as a controller like the Dial, so it uses one of
the logger's four controller slots. From firmware v0.22 every controller
names itself in its commands (`ControllerKind` in `TelemetryProtocol.h`), so
the CardputerZero's ESP-NOW page and the AtomS3's DEVICES page show it as
`CYD`.

## Hardware notes

The pins are those of the ESP32-2432S028R (see
`cyd_dashboard/include/CydDisplay.h`). Boards ship with an ILI9341 or an
ST7789 display; the firmware reads the panel ID at start-up and uses the
ILI9341 driver only when the panel confirms it, otherwise the ST7789 driver.
The serial log prints which. The board tested on 2026-11-26 (MAC
8C:94:DF:92:47:F0) has the ST7789. If touches land in the wrong place, adjust
`x_min`/`x_max`/`y_min`/`y_max` or `offset_rotation` in the touch config.

Its scan stays 1.1 s on each channel, longer than the 1 s beacon interval,
so one sweep of channels 1-13 (about 14 s) always finds a logger in range.
The CYD's antenna is weaker than the M5 devices', so keep it within a few
metres of the AtomS3 or logger.

## Build and flash

```powershell
platformio run -d cyd_dashboard
platformio run -d cyd_dashboard -t upload --upload-port COMx
```

The CYD's CH340 USB chip shows up as `USB-SERIAL CH340`.
