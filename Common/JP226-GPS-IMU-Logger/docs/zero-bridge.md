# AtomS3 bridge for the CardputerZero

`atoms3_zero_bridge` turns an AtomS3 on an Atomic GPS Base into the
CardputerZero logger's radio and GPS. The Zero (Raspberry Pi CM0) cannot send
or receive ESP-NOW. Its only pin UART (GPIO14/15) is shared by the Grove port
and both Caps, and the NFC/Sub-1GHz Cap holds GPIO15 with a ~130 kHz clock.
The AtomS3 therefore connects to the Zero by **USB** and shows up as
`/dev/ttyACM0` at 115200 baud. USB also powers it. The Grove cable is not
used.

The Zero has no ESP-NOW of its own, so the bridge does three jobs:

- **ESP-NOW hub.** It sends the discovery beacon on channel 1 (board
  `CardputerZero` = 4), keeps a list of every JP226 device it hears and passes
  their packets to the Zero.
- **GPS.** It forwards the GPS Base NMEA to the Zero unchanged. With the GPS
  Cap fitted, the Zero sends `$PJP226,GPS,0` and uses the Cap instead.
- **Keep-alive.** The Dial, Echo and black box drop the link after 5 s without
  a `DialStatus`. While the Zero sends none, the bridge sends its own from its
  GPS, with place `ZERO OFFLINE` or `ZERO NOT LOGGING`. It refuses commands
  (`accepted = 0`) until the Zero says it handles them.

Accessories only pair with the bridge if they are unassigned or assigned to
`LoggerBoard::CardputerZero`. On the M5Dial (v0.21 and later), tap the
CONNECTION page until the target is `CP ZERO` or `ANY LOGGER`.

The CardputerZero app (2026.11.03 and later) sends `C` in HELLO and answers
the Dial itself, like the ESP32 logger: it acknowledges each `DialCommand`
once per sequence number, and sends `DialStatus` every 500 ms and
`PositionReport` every second to each controller heard in the last 5 s.

## Screen

Tapping the screen moves to the next page. On the game page a tap jumps, and
holding for 1.5 s moves to the next page.

| Page | Shows |
| --- | --- |
| ZERO BRIDGE | USB link to the Zero, GPS state, whether GPS goes to the Zero, number of fresh devices, channel, packet counters, MAC |
| DEVICES | One row per device heard, newest first: type, last two MAC bytes, RSSI, time since last heard. Green under 5 s, orange under 30 s, grey after that; forgotten after 2 min |
| GPS | Fix, position, speed, HDOP, lines forwarded |
| FOSSIL RECORD | Version, git commit, build date, dinosaur |
| JP226PRINTS | Dino Dash: runs by itself above 5 km/h, playable when parked |

Device types are worked out from what each device sends:

| Packet | Shown as |
| --- | --- |
| `DialCommand` | `DIAL/ECHO` |
| `TemperatureReport` | `M5DIAL`, `ATOM ECHO` or `BLACKBOX`, from its probe source |
| `BlackBoxStatus` | `BLACKBOX` |
| `Telemetry` | `ATOM GPS` (another AtomS3 GPS/IMU node) |
| `Discovery` | Another logger: `CARDPUTER`, `CP ADV`, `CORE2` or `LOGGER` |

The hold-to-zero IMU calibration is retired, because the Zero has its own
BMI270.

## Link protocol

The link is text lines in NMEA style, `$<body>*HH` followed by CRLF, where
`HH` is the XOR of the body. Packets are base64.

### AtomS3 to Zero

| Line | Meaning |
| --- | --- |
| `$GN...`, `$GP...` | GPS Base NMEA, unchanged (while forwarding is on) |
| `$PJP226,BRIDGE,<ver>,<git>,<channel>,<devices>,<gps baud>,<gps live 0/1>,<forwarding 0/1>` | Once a second |
| `$PJP226,RX,<MAC hex 12>,<rssi>,<base64 packet>` | Every valid JP226 ESP-NOW packet heard, with its CRC already checked |

### Zero to AtomS3

| Line | Meaning |
| --- | --- |
| `$PJP226,HELLO,<zero version>,<caps>` | Send once a second. The link counts as up for 3 s after any valid line. `C` in caps means the Zero acknowledges `DialCommand`s itself |
| `$PJP226,TIME,<utc s>,<utc offset min>,<flags>,<battery %>,<battery mV>` | Clock and battery for the beacon. Flags are `TimeFlag` bits (1 = UTC valid, 2 = offset valid); battery is -1 when unknown |
| `$PJP226,GPS,<0/1>` | Switch NMEA forwarding off or on. The setting is saved |
| `$PJP226,TX,<MAC hex 12>,<base64 packet>` | Send a sealed packet to a device that has been heard, or to `FFFFFFFFFFFF`. A `DialStatus` or `PositionReport` from the Zero stops the bridge's own for 2 s |

The Zero's NMEA parser ignores `$PJP226` lines, because their sentence head is
not five letters. A bridge-aware reader takes them out of the stream before
parsing.

## Build and flash

Flash the AtomS3 from a PC over USB:

```powershell
platformio run -d atoms3_zero_bridge
platformio run -d atoms3_zero_bridge -t upload --upload-port COMx
```

Then plug the AtomS3 into one of the Zero's USB ports.

The firmware keeps the core logger quiet (`CORE_DEBUG_LEVEL=0`), because USB
is the data link. It never blocks on USB: if the Zero is not reading, lines are
dropped and counted under DROP.
