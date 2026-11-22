"""IMU hook. NOT WIRED IN.

The CardputerZero has an IMU but its chip and bus are not known, so this port
reads none: NullImu reports available=False and the CSV IMU columns stay empty
with imu_available=0 and imu_type=none.

To add one later, implement ImuReader (e.g. read the chip over I2C with smbus2,
or through an iio device under /sys/bus/iio) and pass it to LoggerApp(imu=...).
update() is called every loop; snapshot_and_reset() is called once per CSV row
and must return an ImuSample (SI units, as in the firmware) or None, resetting
the per-row statistics (RMS, peaks).
"""

from dataclasses import dataclass
from typing import Optional


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
    """Interface; subclass and override."""

    available = False
    type_name = "none"

    def update(self, now_ms: int) -> None:
        """Sample the sensor (firmware target: 100 Hz)."""

    def snapshot_and_reset(self) -> Optional[ImuSample]:
        return None


class NullImu(ImuReader):
    pass
