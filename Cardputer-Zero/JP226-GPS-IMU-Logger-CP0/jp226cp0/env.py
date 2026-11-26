"""M5Stack ENV unit on the Grove port: temperature, humidity and pressure.

Supported, found by address on the Grove I2C bus (I2C1, /dev/i2c-1):

  0x44  SHT30 (ENV III) or SHT40 (ENV IV): temperature + humidity
  0x70  QMP6988 (ENV III): pressure + temperature
  0x76  BMP280 (ENV IV, ENV II): pressure + temperature

UNVERIFIED on the CardputerZero: no ENV unit has been plugged in yet. The
drivers follow the Sensirion, QST and Bosch datasheets (the same maths as
M5Stack's Arduino libraries) and are unit-tested against datasheet examples.

The Grove port must be in I2C mode with Grove 5V on (gps_power.apply_env).
In I2C mode the Grove pins are the Zero's system I2C bus, so a Grove GPS must
never be plugged in at the same time: the ENV page turns this on (key 4) and
it is refused while the GPS source is GROVE.

Only the standard library is used (Linux i2c-dev ioctls).
"""

import os
import struct
import threading
import time
from typing import Callable, Dict, Optional

from . import hardware

_I2C_SLAVE = 0x0703

SHT_ADDRESS = 0x44
QMP6988_ADDRESS = 0x70
BMP280_ADDRESS = 0x76

POLL_INTERVAL_S = 2.0


def sensirion_crc(data: bytes) -> int:
    """CRC-8 used by the SHT3x/SHT4x (polynomial 0x31, init 0xFF)."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def sht_convert(raw: bytes):
    """(temperature C, humidity %) from a 6-byte SHT3x/SHT4x reading, or
    None if a CRC fails. Both chips use the same 16-bit scaling; SHT4x
    humidity can read slightly outside 0-100, so it is clamped."""
    if len(raw) != 6 or sensirion_crc(raw[0:2]) != raw[2] or \
            sensirion_crc(raw[3:5]) != raw[5]:
        return None
    t_ticks = (raw[0] << 8) | raw[1]
    h_ticks = (raw[3] << 8) | raw[4]
    temperature = -45.0 + 175.0 * t_ticks / 65535.0
    humidity = 100.0 * h_ticks / 65535.0
    return temperature, max(0.0, min(100.0, humidity))


def _signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


class Qmp6988Calibration:
    """QMP6988 OTP coefficients (25 bytes from 0xA0), datasheet section 4."""

    def __init__(self, otp: bytes):
        a = otp
        def s16(i):
            return _signed((a[i] << 8) | a[i + 1], 16)
        b00 = _signed((a[0] << 12) | (a[1] << 4) | ((a[24] & 0xF0) >> 4), 20)
        a0 = _signed((a[18] << 12) | (a[19] << 4) | (a[24] & 0x0F), 20)
        self.a0 = a0 / 16.0
        self.a1 = -6.30e-03 + 4.30e-04 * s16(20) / 32767.0
        self.a2 = -1.9e-11 + 1.2e-10 * s16(22) / 32767.0
        self.b00 = b00 / 16.0
        self.bt1 = 1.00e-01 + 9.10e-02 * s16(2) / 32767.0
        self.bt2 = 1.20e-08 + 1.20e-06 * s16(4) / 32767.0
        self.bp1 = 3.30e-02 + 1.90e-02 * s16(6) / 32767.0
        self.b11 = 2.10e-07 + 1.40e-07 * s16(8) / 32767.0
        self.bp2 = -6.30e-10 + 3.50e-10 * s16(10) / 32767.0
        self.b12 = 2.90e-13 + 7.60e-13 * s16(12) / 32767.0
        self.b21 = 2.10e-15 + 1.20e-14 * s16(14) / 32767.0
        self.bp3 = 1.30e-16 + 7.90e-17 * s16(16) / 32767.0

    def convert(self, raw_pressure: int, raw_temperature: int):
        """(temperature C, pressure hPa) from the 24-bit raw values."""
        dt = raw_temperature - (1 << 23)
        dp = raw_pressure - (1 << 23)
        tr = self.a0 + self.a1 * dt + self.a2 * dt * dt      # 1/256 C
        pr = (self.b00 + self.bt1 * tr + self.bp1 * dp + self.b11 * dp * tr +
              self.bt2 * tr * tr + self.bp2 * dp * dp +
              self.b12 * dp * tr * tr + self.b21 * dp * dp * tr +
              self.bp3 * dp * dp * dp)                        # Pa
        return tr / 256.0, pr / 100.0


class Bmp280Calibration:
    """BMP280 trimming values (24 bytes from 0x88), datasheet 3.11.3."""

    def __init__(self, trim: bytes):
        values = struct.unpack("<HhhHhhhhhhhh", trim)
        self.t1, self.t2, self.t3 = values[0:3]
        self.p = values[3:]

    def convert(self, raw_pressure: int, raw_temperature: int):
        """(temperature C, pressure hPa), the datasheet's double version."""
        var1 = (raw_temperature / 16384.0 - self.t1 / 1024.0) * self.t2
        var2 = ((raw_temperature / 131072.0 - self.t1 / 8192.0) ** 2) * self.t3
        t_fine = var1 + var2
        temperature = t_fine / 5120.0
        p1, p2, p3, p4, p5, p6, p7, p8, p9 = self.p
        var1 = t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * p6 / 32768.0
        var2 = var2 + var1 * p5 * 2.0
        var2 = var2 / 4.0 + p4 * 65536.0
        var1 = (p3 * var1 * var1 / 524288.0 + p2 * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * p1
        if var1 == 0:
            return temperature, None
        pressure = 1048576.0 - raw_pressure
        pressure = (pressure - var2 / 4096.0) * 6250.0 / var1
        var1 = p9 * pressure * pressure / 2147483648.0
        var2 = pressure * p8 / 32768.0
        pressure = pressure + (var1 + var2 + p7) / 16.0
        return temperature, pressure / 100.0


class I2CDevice:
    """One device on a Linux i2c-dev bus (plain read/write transfers)."""

    def __init__(self, bus: int, address: int,
                 opener: Callable[[str, int], int] = os.open):
        self.fd = opener("/dev/i2c-{}".format(bus), os.O_RDWR)
        try:
            import fcntl
            fcntl.ioctl(self.fd, _I2C_SLAVE, address)
        except Exception:
            os.close(self.fd)
            raise

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def read(self, count: int) -> bytes:
        return os.read(self.fd, count)

    def read_register(self, register: int, count: int) -> bytes:
        self.write(bytes((register,)))
        return self.read(count)

    def close(self) -> None:
        os.close(self.fd)


class GroveEnv(threading.Thread):
    """Polls whichever ENV sensors answer, every POLL_INTERVAL_S."""

    def __init__(self, bus: int = hardware.GROVE_I2C_BUS,
                 device_factory: Callable[[int, int], I2CDevice] = I2CDevice):
        super().__init__(name="grove-env", daemon=True)
        self.bus = bus
        self.device_factory = device_factory
        self.temp_c: Optional[float] = None
        self.humidity: Optional[float] = None
        self.pressure_hpa: Optional[float] = None
        self.sensors = ""
        self.status = "SEARCHING"
        self.updated = 0.0
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def readings(self) -> Dict:
        fresh = self.updated and time.monotonic() - self.updated < 10
        return {"status": self.status, "sensors": self.sensors,
                "temp_c": self.temp_c if fresh else None,
                "humidity": self.humidity if fresh else None,
                "pressure_hpa": self.pressure_hpa if fresh else None}

    def _open(self, address: int) -> Optional[I2CDevice]:
        try:
            return self.device_factory(self.bus, address)
        except OSError:
            return None

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # never let a sensor stop the app
                self.status = "ENV ERROR"
                self.sensors = str(exc)[:40]
            self._stop_event.wait(POLL_INTERVAL_S)

    def _poll_once(self) -> None:
        found = []
        temp_c = humidity = pressure = None
        sht = self._read_sht()
        if sht is not None:
            name, (temp_c, humidity) = sht
            found.append(name)
        baro = self._read_qmp6988() or self._read_bmp280()
        if baro is not None:
            name, (baro_temp, pressure) = baro
            found.append(name)
            if temp_c is None:
                temp_c = baro_temp
        if found:
            self.temp_c, self.humidity, self.pressure_hpa = temp_c, humidity, pressure
            self.updated = time.monotonic()
            self.sensors = " + ".join(found)
            self.status = "OK"
        else:
            self.status = "NO ENV UNIT"
            self.sensors = ""

    def _read_sht(self):
        device = self._open(SHT_ADDRESS)
        if device is None:
            return None
        try:
            for name, command, wait in (("SHT4X", b"\xfd", 0.01),
                                        ("SHT3X", b"\x24\x00", 0.02)):
                try:
                    device.write(command)
                    time.sleep(wait)
                    values = sht_convert(device.read(6))
                except OSError:
                    continue
                if values is not None:
                    return name, values
            return None
        finally:
            device.close()

    def _read_qmp6988(self):
        device = self._open(QMP6988_ADDRESS)
        if device is None:
            return None
        try:
            if device.read_register(0xD1, 1) != b"\x5c":
                return None
            calibration = Qmp6988Calibration(device.read_register(0xA0, 25))
            device.write(b"\xf4\x27")           # normal mode, x1 oversampling
            time.sleep(0.02)
            raw = device.read_register(0xF7, 6)
            pressure = (raw[0] << 16) | (raw[1] << 8) | raw[2]
            temperature = (raw[3] << 16) | (raw[4] << 8) | raw[5]
            return "QMP6988", calibration.convert(pressure, temperature)
        except OSError:
            return None
        finally:
            device.close()

    def _read_bmp280(self):
        device = self._open(BMP280_ADDRESS)
        if device is None:
            return None
        try:
            if device.read_register(0xD0, 1) not in (b"\x58", b"\x60"):
                return None
            calibration = Bmp280Calibration(device.read_register(0x88, 24))
            device.write(b"\xf4\x27")           # normal mode, x1 oversampling
            time.sleep(0.02)
            raw = device.read_register(0xF7, 6)
            pressure = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
            temperature = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
            return "BMP280", calibration.convert(pressure, temperature)
        except OSError:
            return None
        finally:
            device.close()


def null_readings(status: str = "OFF") -> Dict:
    return {"status": status, "sensors": "", "temp_c": None,
            "humidity": None, "pressure_hpa": None}
