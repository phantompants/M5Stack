"""IMU readers and the existing telemetry sample contract."""

import math
import os
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ImuSample:
    valid: bool = True
    ax_mps2: float = 0.0
    ay_mps2: float = 0.0
    az_mps2: float = 0.0
    gx_dps: float = 0.0
    gy_dps: float = 0.0
    gz_dps: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    g_total_mps2: float = 0.0
    legacy_roughness_mps2: float = 0.0
    accel_rms_mps2: float = 0.0
    vertical_accel_rms_mps2: float = 0.0
    vertical_accel_peak_pos_mps2: float = 0.0
    vertical_accel_peak_neg_mps2: float = 0.0
    lateral_accel_peak_mps2: float = 0.0
    longitudinal_accel_peak_mps2: float = 0.0
    vibration_rms_mps2: float = 0.0
    sample_count: int = 0


class ImuReader:
    type_name = "none"
    available = False
    last_error = ""

    def update(self, now_ms: int) -> None:
        raise NotImplementedError

    def snapshot_and_reset(self) -> Optional[ImuSample]:
        return None

    def latest_sample(self) -> Optional[ImuSample]:
        return getattr(self, "latest", None)


class NullImu(ImuReader):
    """Used on development hosts or devices without a supported IMU."""

    def update(self, now_ms: int) -> None:
        return


class UnavailableImu(NullImu):
    def __init__(self, type_name: str, reason: str):
        self.type_name = type_name
        self.last_error = reason


class Bmi270IioReader(ImuReader):
    """Reads the CardputerZero BMI270 from Linux IIO sysfs."""

    def __init__(self, device_path: str):
        self.device_path = device_path
        self.accel_scale = float(self._read("in_accel_scale"))
        self.gyro_scale = float(self._read("in_anglvel_scale"))
        self.available = True
        self.last_error = ""
        self.latest = ImuSample()
        self.sample_count = 0
        self.accel_sum_sq = 0.0
        self.vertical_sum_sq = 0.0
        self.magnitude_highpass_sum_sq = 0.0
        self.vertical_peak_pos = 0.0
        self.vertical_peak_neg = 0.0
        self.lateral_peak = 0.0
        self.longitudinal_peak = 0.0
        self.gravity_magnitude = 9.80665
        self.gravity_filter_ready = False
        self.last_sample_ms: Optional[int] = None

    def update(self, now_ms: int) -> None:
        if (self.last_sample_ms is not None and
                now_ms - self.last_sample_ms < 10):
            return
        self.last_sample_ms = now_ms
        try:
            ax_raw, ay_raw, az_raw = self._read_vector("in_accel")
            gx_raw, gy_raw, gz_raw = self._read_vector("in_anglvel")
            ax = ax_raw * self.accel_scale
            ay = ay_raw * self.accel_scale
            az = az_raw * self.accel_scale
            gx = math.degrees(gx_raw * self.gyro_scale)
            gy = math.degrees(gy_raw * self.gyro_scale)
            gz = math.degrees(gz_raw * self.gyro_scale)
        except (OSError, ValueError) as exc:
            self.last_error = str(exc)
            self.latest = ImuSample()
            return

        magnitude = math.sqrt(ax * ax + ay * ay + az * az)
        if not self.gravity_filter_ready:
            self.gravity_magnitude = magnitude
            self.gravity_filter_ready = True
        else:
            self.gravity_magnitude += 0.02 * (magnitude - self.gravity_magnitude)
        vertical = az - self.gravity_magnitude
        magnitude_highpass = magnitude - self.gravity_magnitude
        dynamic_magnitude = math.sqrt(
            ax * ax + ay * ay + vertical * vertical)
        pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
        roll = math.degrees(math.atan2(ay, az))

        self.sample_count += 1
        self.accel_sum_sq += dynamic_magnitude * dynamic_magnitude
        self.vertical_sum_sq += vertical * vertical
        self.magnitude_highpass_sum_sq += magnitude_highpass * magnitude_highpass
        self.vertical_peak_pos = max(self.vertical_peak_pos, vertical)
        self.vertical_peak_neg = min(self.vertical_peak_neg, vertical)
        self.lateral_peak = max(self.lateral_peak, abs(ay))
        self.longitudinal_peak = max(self.longitudinal_peak, abs(ax))
        self.last_error = ""
        self.latest = ImuSample(
            valid=True,
            ax_mps2=ax,
            ay_mps2=ay,
            az_mps2=az,
            gx_dps=gx,
            gy_dps=gy,
            gz_dps=gz,
            pitch_deg=pitch,
            roll_deg=roll,
            g_total_mps2=magnitude,
            sample_count=1,
        )
        self._apply_statistics(self.latest)

    def snapshot_and_reset(self) -> Optional[ImuSample]:
        if self.sample_count == 0:
            return None
        sample = ImuSample(**self.latest.__dict__)
        self._apply_statistics(sample)
        self.sample_count = 0
        self.accel_sum_sq = 0.0
        self.vertical_sum_sq = 0.0
        self.magnitude_highpass_sum_sq = 0.0
        self.vertical_peak_pos = 0.0
        self.vertical_peak_neg = 0.0
        self.lateral_peak = 0.0
        self.longitudinal_peak = 0.0
        return sample

    def _apply_statistics(self, sample: ImuSample) -> None:
        if self.sample_count == 0:
            return
        sample.legacy_roughness_mps2 = math.sqrt(
            self.magnitude_highpass_sum_sq / self.sample_count)
        sample.accel_rms_mps2 = math.sqrt(
            self.accel_sum_sq / self.sample_count)
        sample.vertical_accel_rms_mps2 = math.sqrt(
            self.vertical_sum_sq / self.sample_count)
        sample.vibration_rms_mps2 = sample.vertical_accel_rms_mps2
        sample.vertical_accel_peak_pos_mps2 = self.vertical_peak_pos
        sample.vertical_accel_peak_neg_mps2 = self.vertical_peak_neg
        sample.lateral_accel_peak_mps2 = self.lateral_peak
        sample.longitudinal_accel_peak_mps2 = self.longitudinal_peak
        sample.sample_count = min(self.sample_count, 65535)

    def _read(self, name: str) -> str:
        path = os.path.join(self.device_path, name)
        with open(path, "r", encoding="ascii") as handle:
            return handle.read().strip()

    def _read_vector(self, prefix: str) -> Tuple[int, int, int]:
        return tuple(
            int(self._read("{}_{}_raw".format(prefix, axis)))
            for axis in ("x", "y", "z")
        )


def create_default_imu(device_root: str = "/sys/bus/iio/devices") -> ImuReader:
    """Select the BMI270 when present; otherwise keep desktop use supported."""
    try:
        devices = sorted(os.listdir(device_root))
    except OSError:
        return NullImu()
    for device in devices:
        device_path = os.path.join(device_root, device)
        try:
            with open(os.path.join(device_path, "name"), "r",
                      encoding="ascii") as handle:
                name = handle.read().strip().lower()
        except OSError:
            continue
        if name == "bmi270":
            try:
                return Bmi270IioReader(device_path)
            except (OSError, ValueError) as exc:
                return UnavailableImu("BMI270", str(exc))
    return NullImu()
