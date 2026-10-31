#include "ObdCsv.h"

#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <limits>
#include <map>
#include <string>
#include <vector>

namespace {
std::vector<std::string> split(const std::string& text) {
  std::vector<std::string> fields;
  size_t start = 0;
  while (true) {
    const auto end = text.find(',', start);
    fields.push_back(text.substr(start, end == std::string::npos ? end : end - start));
    if (end == std::string::npos) return fields;
    start = end + 1;
  }
}
std::map<std::string, std::string> row(const obd::Readings& state, uint32_t nowMs) {
  char data[obd::csv::kBufferSize];
  assert(obd::csv::writeFields(state, nowMs, data, sizeof(data)));
  const auto names = split(obd::csv::kColumns);
  const auto values = split(data);
  assert(names.size() == 45 && values.size() == names.size());
  std::map<std::string, std::string> fields;
  for (size_t i = 0; i < names.size(); ++i) {
    assert(fields.emplace(names[i], values[i]).second);
    assert(values[i].find("nan") == std::string::npos);
    assert(values[i].find("inf") == std::string::npos);
  }
  return fields;
}
obd::Readings sample(uint32_t stamp) {
  obd::Readings s;
  s.connected = true;
  for (size_t i = 0; i < obd::MetricCount; ++i) {
    s.metricMs[i] = stamp;
    s.engine[i] = float(i + 1);
  }
  s.ecuResponding = true;
  s.updatedMs = stamp;
  s.engine[obd::Rpm] = 1726;
  s.engine[obd::Speed] = 40;
  s.engine[obd::Map] = 180;
  s.engine[obd::Barometer] = 100;
  s.engine[obd::Coolant] = -10;
  s.engine[obd::Voltage] = 14.2f;
  s.milKnown = true;
  s.milOn = true;
  s.milMs = stamp;
  s.ecuCodeCount = 2;
  const uint8_t stored[] = {2, 0x02, 0x99, 0x24, 0x63};
  const uint8_t pending[] = {1, 0xD0, 0x16};
  assert(obd::applyFaults(s.stored, stored, sizeof(stored), stamp));
  assert(obd::applyFaults(s.pending, pending, sizeof(pending), stamp));
  for (size_t wheel = 0; wheel < 4; ++wheel) {
    s.tyrePsi[wheel] = 35 + float(wheel);
    s.tyreC[wheel] = 20 + float(wheel);
    s.tyrePressureMs[wheel] = stamp;
    s.tyreTempMs[wheel] = stamp;
  }
  return s;
}

void test_named_values_and_fault_cells() {
  auto fields = row(sample(1000), 2000);
  assert(fields.at("obd_connected") == "1");
  assert(fields.at("obd_ecu_responding") == "1");
  assert(fields.at("obd_age_ms") == "1000");
  assert(fields.at("obd_rpm") == "1726");
  assert(fields.at("obd_speed_kmh") == "40");
  assert(fields.at("obd_boost_kpa") == "80.0");
  assert(fields.at("obd_coolant_c") == "-10.0");
  assert(fields.at("obd_voltage_v") == "14.200");
  assert(fields.at("obd_mil_on") == "1");
  assert(fields.at("obd_ecu_dtc_count") == "2");
  assert(fields.at("obd_stored_dtcs") == "P0299;P2463");
  assert(fields.at("obd_pending_dtcs") == "U1016");
  assert(fields.at("obd_stored_dtc_age_ms") == "1000");
  assert(fields.at("obd_stored_dtcs_truncated") == "0");
  assert(fields.at("obd_tyre_lf_psi") == "35.0");
  assert(fields.at("obd_tyre_rf_psi") == "36.0");
  assert(fields.at("obd_tyre_lr_psi") == "37.0");
  assert(fields.at("obd_tyre_rr_psi") == "38.0");
  assert(fields.at("obd_tyre_lr_c") == "22.0");
  assert(fields.at("obd_tyre_rr_temp_age_ms") == "1000");
}

void test_offline_unknown_and_zero() {
  auto s = sample(1000);
  s.connected = false;
  auto fields = row(s, 2000);
  for (const auto& field : fields) {
    if (field.first == "obd_connected" || field.first == "obd_ecu_responding") assert(field.second == "0");
    else assert(field.second.empty());
  }
  s = sample(1000);
  s.engine[obd::Rpm] = 0;
  s.tyrePsi[0] = 0;
  s.tyreC[0] = 0;
  s.engine[obd::Oil] = NAN;
  s.engine[obd::Barometer] = NAN;
  s.milKnown = false;
  s.stored = obd::Faults{};
  s.pending = obd::Faults{};
  const uint8_t noCodes[] = {0};
  assert(obd::applyFaults(s.stored, noCodes, 1, 1000));
  fields = row(s, 2000);
  assert(fields.at("obd_rpm") == "0" && fields.at("obd_tyre_lf_psi") == "0.0");
  assert(fields.at("obd_tyre_lf_c") == "0.0");
  assert(fields.at("obd_oil_c").empty() && fields.at("obd_boost_kpa").empty());
  assert(fields.at("obd_mil_on").empty());
  assert(fields.at("obd_stored_dtc_count") == "0");
  assert(fields.at("obd_pending_dtc_count").empty());
}

void test_expiry_independent_ages_and_rollover() {
  auto s = sample(1000);
  s.tyreTempMs[0] = 500;
  auto fields = row(s, 16000);
  assert(fields.at("obd_rpm").empty() && fields.at("obd_ecu_responding") == "0");
  assert(fields.at("obd_age_ms").empty());
  assert(fields.at("obd_tyre_lf_temp_age_ms") == "15500");
  assert(fields.at("obd_tyre_lf_pressure_age_ms") == "15000");
  fields = row(s, 91000);
  assert(fields.at("obd_tyre_lf_psi").empty() && fields.at("obd_tyre_rr_c").empty());
  assert(fields.at("obd_tyre_lf_temp_age_ms").empty());
  assert(fields.at("obd_stored_dtc_count").empty() && fields.at("obd_stored_dtcs").empty());
  // Formatting expires a copy; it must not mutate the reader's snapshot.
  assert(std::isfinite(s.engine[obd::Rpm]) && s.stored.valid);
  fields = row(sample(UINT32_MAX - 500), 100);
  assert(fields.at("obd_age_ms") == "601");
  assert(fields.at("obd_tyre_rr_temp_age_ms") == "601");
}

void test_ford_decoding_to_csv() {
  obd::Readings s;
  s.connected = true;
  uint8_t id[] = {0x12, 0x34, 0x56, 0x78};
  uint8_t pressure[] = {0x02, 0xBC};
  uint8_t record[] = {0x12, 0x34, 0x56, 0x78, 0x02, 0xBC, 64, 6, 0xAA};
  assert(obd::applyFordDid(s, 0x2811, id, 4, 1000)); // RR
  assert(obd::applyFordDid(s, 0x2815, pressure, 2, 1000));
  assert(obd::applyFordDid(s, 0x280B, record, 9, 1200));
  auto fields = row(s, 2000);
  assert(fields.at("obd_tyre_rr_psi") == "35.0" && fields.at("obd_tyre_rr_c") == "24.0");
  assert(fields.at("obd_tyre_lr_psi").empty() && fields.at("obd_tyre_lr_c").empty());
  assert(fields.at("obd_tyre_rr_temp_age_ms") == "800");
  assert(fields.at("obd_tpms_last_sensor_id_hex") == "12345678");
  assert(fields.at("obd_tpms_last_sensor_c") == "24.0");
  // Preserve a received temperature even when its wheel mapping is unknown.
  s = obd::Readings{};
  s.connected = true;
  assert(obd::applyFordDid(s, 0x280B, record, 9, 1200));
  fields = row(s, 2000);
  assert(fields.at("obd_tyre_rr_c").empty());
  assert(fields.at("obd_tpms_last_sensor_c") == "24.0");
  assert(fields.at("obd_tpms_last_sensor_age_ms") == "800");
  fields = row(s, 91200);
  assert(fields.at("obd_tpms_last_sensor_id_hex").empty());
  assert(fields.at("obd_tpms_last_sensor_c").empty());
}

void test_bounded_output_and_dtc_capacity() {
  auto s = sample(1000);
  s.stored.count = obd::kMaxCodes;
  s.stored.reportedCount = 20;
  s.pending = s.stored;
  for (auto& code : s.stored.codes) code = 0xFFFF;
  for (auto& code : s.pending.codes) code = 0xFFFF;
  for (auto& value : s.tyrePsi) value = 3276.7f;
  auto fields = row(s, 2000);
  assert(fields.at("obd_stored_dtc_count") == "20");
  assert(fields.at("obd_stored_dtcs_truncated") == "1");
  assert(std::count(fields.at("obd_stored_dtcs").begin(), fields.at("obd_stored_dtcs").end(), ';') == 15);
  char small[32];
  std::memset(small, 'X', sizeof(small));
  assert(!obd::csv::writeFields(s, 2000, small, 16));
  assert(small[0] == '\0');
  for (size_t i = 16; i < sizeof(small); ++i) assert(small[i] == 'X');
  assert(!obd::csv::writeFields(s, 2000, nullptr, 0));
  for (auto& value : s.engine) value = std::numeric_limits<float>::max();
  for (auto& value : s.tyrePsi) value = std::numeric_limits<float>::max();
  for (auto& value : s.tyreC) value = std::numeric_limits<float>::max();
  char output[obd::csv::kBufferSize];
  assert(!obd::csv::writeFields(s, 2000, output, sizeof(output)));
  assert(output[0] == '\0');
}

void test_daily_file_schema_names() {
  assert(obd::csv::isDailyFileName("telemetry_2026-10-31.csv", "telemetry_"));
  assert(obd::csv::isDailyFileName("telemetry_2026-10-31_obd1.csv", "telemetry_"));
  assert(!obd::csv::isDailyFileName("telemetry_2026-10-31_obd1.csv.tmp", "telemetry_"));
  assert(!obd::csv::isDailyFileName("telemetry_2026-10-31_bad.csv", "telemetry_"));
  assert(!obd::csv::isDailyFileName("telemetry_2026-xx-31_obd1.csv", "telemetry_"));
  assert(!obd::csv::isDailyFileName("imu_2026-10-31.csv", "telemetry_"));
  assert(!obd::csv::isDailyFileName("t", "telemetry_"));
}
}

int main() {
  test_named_values_and_fault_cells();
  test_offline_unknown_and_zero();
  test_expiry_independent_ages_and_rollover();
  test_ford_decoding_to_csv();
  test_bounded_output_and_dtc_capacity();
  test_daily_file_schema_names();
  std::puts("OBD CSV: 45-column alignment, numeric values, Ford wheels/unmapped temperatures, missing/stale/zero values, fault cells, overflow and legacy/new filenames passed");
}
