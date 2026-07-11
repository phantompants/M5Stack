# M5StickC EV Charger Control

ESPHome configuration for an original M5Stick-C that displays EV charging power from Home Assistant and toggles an EV charger switch with Button A.

## Hardware

- Board: M5Stack M5Stick-C
- ESP32 board ID: `m5stick-c`
- Display: ST7735 80x160 TFT on the built-in SPI pins
- Button A: GPIO37
- Backlight: GPIO32

## Home Assistant entities

The default configuration expects these Home Assistant entities:

- Charger switch: `switch.matter_ev_charge`
- Power sensor: `sensor.smart_wi_fi_plug_power`

Change them at the top of `dark-orange-stick-ev.yaml` if your entity IDs differ:

```yaml
substitutions:
  ev_charger_switch_entity: switch.matter_ev_charge
  ev_charger_power_entity: sensor.smart_wi_fi_plug_power
```

## Secrets

Copy the keys from `secrets.example.yaml` into your ESPHome `secrets.yaml` and fill in real values. Do not commit the real values to GitHub.

Important: if you previously committed or shared an ESPHome API encryption key, OTA password, WiFi password, or fallback AP password, rotate those values in Home Assistant/ESPHome before relying on the device.

## Flashing

From the ESPHome dashboard, add this repository/configuration or paste `dark-orange-stick-ev.yaml` into a new device, then install by USB for the first flash. After that, OTA updates should work once the device is online.

From the ESPHome CLI:

```bash
esphome run dark-orange-stick-ev.yaml
```

## Fixes Compared With The Previous Config

- Adds the `board: m5stick-c` setting for the original M5Stick-C hardware.
- Keeps `variant: esp32`, which matches the original ESP32 in the M5Stick-C.
- Moves API, OTA, WiFi, and fallback AP credentials into ESPHome secrets.
- Uses a Home Assistant switch component for `switch.matter_ev_charge`, so Button A can toggle the imported switch directly.
- Changes the display to manual updates from a 2 second interval to avoid unnecessary redraw work.
- Adds API/offline handling on the display so it is clearer whether Home Assistant is connected.

## Notes

ESPHome 2026 documents `variant` as the recommended ESP32 selector, while PlatformIO still lists `m5stick-c` as the board ID for this hardware. Both are specified here so older and newer ESPHome builds have the board metadata they need.

The legacy ST7735 display component still supports `INITR_MINI160X80`, but ESPHome marks it as redundant because ST7735 support is moving under the MIPI SPI display stack. This config intentionally keeps ST7735 for the built-in M5Stick-C TFT because it matches the existing working pin mapping.
