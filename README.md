# M5Atom GPS Logger with Temperature Sensor

This project logs GPS data and temperature readings using the M5Atom development board, a DS18B20 temperature sensor, and an SD card for storing GPX files. The code supports logging GPS coordinates and temperature data periodically and saving it in GPX format on an SD card. Additionally, it features button-controlled logging, LED notifications, and deep sleep mode to optimize power consumption.

## Features

- **GPS Logging:** Logs latitude, longitude, and timestamp from a GPS module.
- **Temperature Logging:** Reads temperature data from a DS18B20 sensor.
- **GPX File Format:** Stores data in the widely-used GPX format.
- **SD Card Support:** Saves logs to an SD card.
- **Button Controlled:** Start or stop logging using a button.
- **LED Notifications:** Visual feedback for logging status using the M5Atom’s LED.
- **Power Management:** Deep sleep is activated based on user interaction, optimizing power usage.

## Components Used

- [M5Atom Lite](https://docs.m5stack.com/en/core/atom_lite)
- GPS module (using UART)
- DS18B20 temperature sensor
- MicroSD card (for data logging)
- M5Atom button (for controlling the logging process)
- **4.7kΩ resistor** between the +5V pin and the DS18B20 data wire (mandatory for proper sensor operation).

## Wiring

| Component      | Pin on M5Atom | Notes               |
|----------------|---------------|---------------------|
| GPS TX         | GPIO 22       | GPS module TX to RX |
| OneWire (Temp) | GPIO 26       | DS18B20 Data pin    |
| Button         | GPIO 39       | M5Atom button       |
| SD Card        | SPI Pins      | MISO: 23, MOSI: 19, CLK: 18 |
| Resistor       | +5V to Data   | 4.7kΩ required between +5V and DS18B20 data pin |

## Installation

1. Clone this repository to your local machine.
2. Install the required libraries:
   - [M5Atom](https://github.com/m5stack/M5Atom)
   - [TinyGPSPlus](https://github.com/mikalhart/TinyGPSPlus)
   - [OneWire](https://github.com/PaulStoffregen/OneWire)
   - [DallasTemperature](https://github.com/milesburton/Arduino-Temperature-Control-Library)
   - [SD](https://www.arduino.cc/en/Reference/SD)
3. Open the project in the Arduino IDE.
4. Select the correct board (M5Atom) and COM port.
5. Upload the code to the M5Atom.

## Usage

1. **Start Logging:** When the device is powered on, it initializes the GPS module and temperature sensor. If the GPS data is valid, a GPX file is created.
2. **Button Control:** 
   - Press the button to toggle between logging modes.
   - The LED on the M5Atom will give feedback (Red, Amber, Green) indicating the logging state.
3. **GPX Files:** GPX files are stored in the SD card with the filename format `YYYYMMDD-HHmm.gpx`.
4. **Deep Sleep:** If deep sleep mode is activated, the device will wake up based on an external signal (button press) or other configured triggers.

## File Structure

- **GPX Files:** The GPS and temperature data is saved in `.gpx` format, which includes:
  - Latitude and Longitude of each GPS point.
  - Time of data acquisition.

## Configuration

You can customize the following settings in the code:

- **RECORD_FREQUENCY**: Interval (in milliseconds) at which GPS points are logged.
- **GPS_TIMEOUT**: Timeout for acquiring GPS data.
- **DISPLAY_GPS_DATA**: Set to `true` if you want to display GPS data via serial monitor.

## Troubleshooting

1. **SD Card Not Detected:** Check your wiring and ensure that the SD card is formatted as FAT32.
2. **No GPS Data:** Ensure that your GPS module has a clear view of the sky for accurate readings.
3. **Button Not Responding:** Ensure proper debounce logic is implemented, and check the wiring of the button.

## Original Code

This project is based on the original code available at [Gps-a-gpx-mediante-Atomic-M5stack-con-registro-temperatura-de-DS18b20](https://github.com/mendilazo/Gps-a-gpx-mediante-Atomic-M5stack-con-registro-temperatura-de-DS18b20/tree/main). Special thanks to the original creator for the excellent foundation!

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for more information.

