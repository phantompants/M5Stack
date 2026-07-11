# M5StickC EV Charger Control

ESPHome firmware for an original M5Stack M5Stick-C. The device shows EV charger power on the built-in TFT display and lets you toggle the charger from Button A through Home Assistant.

## What It Does

- Displays live EV charging power from Home Assistant.
- Shows charger state as `ON` or `OFF`.
- Uses Button A to toggle the charger switch.
- Shows an offline message when the Home Assistant API is unavailable.
- Keeps WiFi, API, OTA, and fallback hotspot credentials out of GitHub.

## Hardware

- Board: M5Stack M5Stick-C, original ESP32 model
- ESP32 board ID: `m5stick-c`
- Display: built-in ST7735 80x160 TFT
- Button A: GPIO37
- Backlight: GPIO32
- TFT SPI clock: GPIO13
- TFT SPI MOSI: GPIO15
- TFT chip select: GPIO5
- TFT data/command: GPIO23
- TFT reset: GPIO18

## Files

- `dark-orange-stick-ev.yaml`: ESPHome device configuration.
- `secrets.example.yaml`: example secret keys to copy into your ESPHome secrets file.

## Quick Start

1. Copy the keys from `secrets.example.yaml` into your ESPHome `secrets.yaml`.
2. Fill in your WiFi, API encryption, OTA, and fallback AP values.
3. Check the Home Assistant entity IDs at the top of `dark-orange-stick-ev.yaml`.
4. Flash the M5Stick-C over USB for the first install.
5. Use OTA updates after the device is online.

From the ESPHome CLI:

```bash
esphome run dark-orange-stick-ev.yaml
```

## Home Assistant Entities

The default configuration expects these entities:

- Charger switch: `switch.matter_ev_charge`
- Power sensor: `sensor.smart_wi_fi_plug_power`

If your entities have different IDs, edit the substitutions at the top of `dark-orange-stick-ev.yaml`:

```yaml
substitutions:
  ev_charger_switch_entity: switch.matter_ev_charge
  ev_charger_power_entity: sensor.smart_wi_fi_plug_power
```

## Secrets

Do not commit real secrets to this repository. Keep them in your ESPHome `secrets.yaml`:

```yaml
wifi_ssid: "YOUR_WIFI_SSID"
wifi_password: "YOUR_WIFI_PASSWORD"
dark_orange_stick_ev_api_key: "BASE64_32_BYTE_ESPHOME_API_KEY"
dark_orange_stick_ev_ota_password: "CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD"
dark_orange_stick_ev_ap_password: "CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD"
```

If an ESPHome API encryption key, OTA password, WiFi password, or fallback hotspot password has been shared or committed before, rotate it before using the device.

## Notes About This Config

The config specifies both:

```yaml
esp32:
  board: m5stick-c
  variant: esp32
```

`variant: esp32` matches the original ESP32 chip in the M5Stick-C. `board: m5stick-c` provides the PlatformIO board metadata for this specific device.

The display uses ESPHome's ST7735 component with `INITR_MINI160X80`, which matches the built-in 80x160 TFT used by the M5Stick-C. ESPHome now recommends newer MIPI SPI display support for many ST7735 panels, but this config keeps the ST7735 driver because it matches the known M5Stick-C pin mapping.

## Troubleshooting

If the display is blank:

- Confirm the device is an original M5Stick-C, not a M5Stick-C Plus or Plus2.
- Confirm the backlight entity is on in Home Assistant.
- Try flashing over USB once before relying on OTA.

If Button A does not toggle charging:

- Confirm Home Assistant shows the ESPHome device as connected.
- Confirm `switch.matter_ev_charge` exists, or update `ev_charger_switch_entity`.
- Check ESPHome logs for `Button press ignored: Home Assistant API is not connected`.

If power always shows as waiting:

- Confirm `sensor.smart_wi_fi_plug_power` exists, or update `ev_charger_power_entity`.
- Confirm the sensor state is numeric watts.

## Changes From The Original Broken Config

- Added `board: m5stick-c` for the M5Stick-C hardware.
- Moved all credentials into ESPHome secrets.
- Imported the charger as a Home Assistant switch so Button A can toggle it directly.
- Made display updates explicit on a 2 second interval instead of redrawing every 250 ms.
- Added API/offline handling on the display.
- Added setup and troubleshooting documentation.
