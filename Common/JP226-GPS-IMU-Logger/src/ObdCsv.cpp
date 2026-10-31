#include "ObdCsv.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace obd::csv {

const char kColumns[] =
    "obd_connected,obd_ecu_responding,obd_age_ms,"
    "obd_rpm,obd_speed_kmh,obd_load_pct,obd_throttle_pct,obd_boost_kpa,"
    "obd_map_kpa,obd_baro_kpa,obd_coolant_c,obd_oil_c,obd_intake_c,"
    "obd_ambient_c,obd_voltage_v,obd_fuel_pct,obd_mil_on,obd_ecu_dtc_count,"
    "obd_stored_dtc_count,obd_pending_dtc_count,obd_stored_dtcs,obd_pending_dtcs,"
    "obd_stored_dtc_age_ms,obd_pending_dtc_age_ms,"
    "obd_stored_dtcs_truncated,obd_pending_dtcs_truncated,"
    "obd_tyre_lf_psi,obd_tyre_rf_psi,obd_tyre_lr_psi,obd_tyre_rr_psi,"
    "obd_tyre_lf_c,obd_tyre_rf_c,obd_tyre_lr_c,obd_tyre_rr_c,"
    "obd_tyre_lf_pressure_age_ms,obd_tyre_rf_pressure_age_ms,"
    "obd_tyre_lr_pressure_age_ms,obd_tyre_rr_pressure_age_ms,"
    "obd_tyre_lf_temp_age_ms,obd_tyre_rf_temp_age_ms,"
    "obd_tyre_lr_temp_age_ms,obd_tyre_rr_temp_age_ms,"
    "obd_tpms_last_sensor_id_hex,obd_tpms_last_sensor_c,obd_tpms_last_sensor_age_ms";

namespace {
class Writer {
 public:
  Writer(char* data, size_t capacity) : data_(data), capacity_(capacity) {
    if (capacity_) data_[0] = '\0';
    else good_ = false;
  }
  void append(const char* format, ...) {
    if (!good_) return;
    va_list args;
    va_start(args, format);
    const int size = std::vsnprintf(data_ + length_, capacity_ - length_, format, args);
    va_end(args);
    if (size < 0 || size_t(size) >= capacity_ - length_) {
      good_ = false;
      return;
    }
    length_ += size_t(size);
  }
  void number(float value, unsigned decimals) {
    append(",");
    if (std::isfinite(value)) append("%.*f", int(decimals), double(value));
  }
  void integer(uint32_t value, bool valid) {
    append(",");
    if (valid) append("%lu", static_cast<unsigned long>(value));
  }
  void codes(const Faults& faults) {
    append(",");
    if (!faults.valid) return;
    const size_t count = faults.count < kMaxCodes ? faults.count : kMaxCodes;
    for (size_t i = 0; i < count; ++i) {
      char code[6];
      formatDtc(faults.codes[i], code);
      append("%s%s", i ? ";" : "", code);
    }
  }
  bool good() const { return good_; }
 private:
  char* data_;
  size_t capacity_;
  size_t length_ = 0;
  bool good_ = true;
};
}  // namespace

bool writeFields(const Readings& snapshot, uint32_t nowMs,
                  char* output, size_t capacity) {
  Readings state = snapshot.connected ? snapshot : Readings{};
  expire(state, nowMs);
  Writer row(output, capacity);
  row.append("%u,%u", state.connected ? 1U : 0U, state.ecuResponding ? 1U : 0U);
  row.integer(nowMs - state.updatedMs, state.ecuResponding);
  row.number(state.engine[Rpm], 0);
  row.number(state.engine[Speed], 0);
  row.number(state.engine[Load], 1);
  row.number(state.engine[Throttle], 1);
  const float boost = std::isfinite(state.engine[Map]) && std::isfinite(state.engine[Barometer])
                          ? state.engine[Map] - state.engine[Barometer] : NAN;
  row.number(boost, 1);
  row.number(state.engine[Map], 0);
  row.number(state.engine[Barometer], 0);
  row.number(state.engine[Coolant], 1);
  row.number(state.engine[Oil], 1);
  row.number(state.engine[Intake], 1);
  row.number(state.engine[Ambient], 1);
  row.number(state.engine[Voltage], 3);
  row.number(state.engine[Fuel], 1);
  row.integer(state.milOn ? 1 : 0, state.milKnown);
  row.integer(state.ecuCodeCount, state.milKnown);
  row.integer(state.stored.reportedCount, state.stored.valid);
  row.integer(state.pending.reportedCount, state.pending.valid);
  row.codes(state.stored);
  row.codes(state.pending);
  row.integer(nowMs - state.stored.updatedMs, state.stored.valid);
  row.integer(nowMs - state.pending.updatedMs, state.pending.valid);
  row.integer(state.stored.reportedCount > state.stored.count, state.stored.valid);
  row.integer(state.pending.reportedCount > state.pending.count, state.pending.valid);
  for (const auto value : state.tyrePsi) row.number(value, 1);
  for (const auto value : state.tyreC) row.number(value, 1);
  for (size_t wheel = 0; wheel < 4; ++wheel)
    row.integer(nowMs - state.tyrePressureMs[wheel], std::isfinite(state.tyrePsi[wheel]));
  for (size_t wheel = 0; wheel < 4; ++wheel)
    row.integer(nowMs - state.tyreTempMs[wheel], std::isfinite(state.tyreC[wheel]));
  row.append(",");
  if (std::isfinite(state.lastSensorC))
    row.append("%08lX", static_cast<unsigned long>(state.lastSensorId));
  row.number(state.lastSensorC, 1);
  row.integer(nowMs - state.lastSensorMs, std::isfinite(state.lastSensorC));
  if (!row.good()) {
    if (capacity) output[0] = '\0';
    return false;
  }
  return true;
}

bool isDailyFileName(const char* name, const char* prefix) {
  const size_t prefixSize = std::strlen(prefix);
  const size_t size = std::strlen(name);
  if (size < prefixSize + 14 || std::strncmp(name, prefix, prefixSize) != 0) return false;
  const char* date = name + prefixSize;
  for (size_t i = 0; i < 10; ++i) {
    if (i == 4 || i == 7) { if (date[i] != '-') return false; }
    else if (date[i] < '0' || date[i] > '9') return false;
  }
  const char* suffix = date + 10;
  return std::strcmp(suffix, ".csv") == 0 || std::strcmp(suffix, kFileSuffix) == 0;
}

}  // namespace obd::csv
