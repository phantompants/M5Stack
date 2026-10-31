#include "ObdProtocol.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace {
bool near(float a, float b) { return std::fabs(a - b) < 0.01f; }

void test_elm_framing() {
  uint8_t data[256]{};
  assert(obd::payload("010C\rSEARCHING...\r41 0C 1A F8\r>", 0x41, 0x0C, data, sizeof(data)) == 4);
  assert(data[2] == 0x1A && data[3] == 0xF8);
  assert(obd::payload("410C1AF8\r", 0x41, 0x0C, data, sizeof(data)) == 4);
  assert(obd::payload("NO DATA\r>", 0x41, 0x0C, data, sizeof(data)) == 0);
  assert(obd::payload("BUS INIT: ERROR\r>", 0x41, 0x0C, data, sizeof(data)) == 0);
  assert(obd::payload("7F2231\r>", 0x62, 0x2813, data, sizeof(data)) == 0);
  assert(obd::payload("41 0C 1G F8\r>", 0x41, 0x0C, data, sizeof(data)) == 0);
  assert(obd::payload("41 0C 1AF\r>", 0x41, 0x0C, data, sizeof(data)) == 0);
  assert(obd::payload("410D20\r>", 0x41, 0x0C, data, sizeof(data)) == 0);
  assert(obd::payload("410C1AF8\r>", 0x41, 0x0C, data, 3) == 0);

  const char* multi = "00C\r0: 62 28 0B 12 34 56\r1: 78 02 BC 40 06 AA\r>";
  assert(obd::payload(multi, 0x62, 0x280B, data, sizeof(data)) == 12);
  assert(data[6] == 0x78 && data[9] == 0x40);
  assert(obd::payload("00C\r0:62280B123456\r>", 0x62, 0x280B, data, sizeof(data)) == 0);
  assert(obd::payload("00C\r0:62280B123456\r2:7802BC4006AA\r>", 0x62, 0x280B, data, sizeof(data)) == 0);
}

void test_engine_conversions_and_expiry() {
  obd::Readings s;
  uint8_t rpm[] = {0x1A, 0xF8};
  assert(!obd::applyPid(s, 0x0C, rpm, 1, 100));
  assert(!s.ecuResponding && std::isnan(s.engine[obd::Rpm]));
  assert(obd::applyPid(s, 0x0C, rpm, 2, 100));
  assert(near(s.engine[obd::Rpm], 1726));
  uint8_t temp[] = {80};
  assert(obd::applyPid(s, 0x05, temp, 1, 200));
  assert(near(s.engine[obd::Coolant], 40));
  uint8_t full[] = {255};
  assert(obd::applyPid(s, 0x04, full, 1, 200));
  assert(near(s.engine[obd::Load], 100));
  uint8_t volts[] = {0x36, 0xB0};
  assert(obd::applyPid(s, 0x42, volts, 2, 200));
  assert(near(s.engine[obd::Voltage], 14));
  uint8_t mil[] = {0x82, 0, 0, 0};
  assert(obd::applyPid(s, 0x01, mil, 4, 200));
  assert(s.milKnown && s.milOn && s.ecuCodeCount == 2);
  obd::expire(s, 15100);
  assert(std::isnan(s.engine[obd::Rpm]));
  assert(std::isfinite(s.engine[obd::Coolant]));
  assert(s.ecuResponding);
  obd::expire(s, 15200);
  assert(!s.ecuResponding && !s.milKnown);

  // The freshness clock must also work through millis() rollover.
  assert(obd::applyPid(s, 0x0C, rpm, 2, UINT32_MAX - 100));
  obd::expire(s, 100);
  assert(std::isfinite(s.engine[obd::Rpm]));
  obd::expire(s, 15000);
  assert(std::isnan(s.engine[obd::Rpm]));
}

void test_ford_wheel_mapping_and_temperature() {
  obd::Readings s;
  uint8_t pressure[] = {0x02, 0xBC};  // 700 / 20 = 35 psi.
  assert(obd::applyFordDid(s, 0x2815, pressure, 2, 100));
  assert(near(s.tyrePsi[3], 35)); // Ford 2815 is RR, not LR.
  assert(std::isnan(s.tyrePsi[2]));
  uint8_t empty[] = {0xFF, 0xFF};
  assert(!obd::applyFordDid(s, 0x2813, empty, 2, 100));
  uint8_t zero[] = {0, 0};
  assert(obd::applyFordDid(s, 0x2813, zero, 2, 100));
  assert(s.tyrePsi[0] == 0);
  uint8_t id[] = {0x12, 0x34, 0x56, 0x78};
  assert(obd::applyFordDid(s, 0x2811, id, 4, 100));
  uint8_t record[] = {0x12, 0x34, 0x56, 0x78, 0x02, 0xBC, 64, 6, 0xAA};
  assert(!obd::applyFordDid(s, 0x280B, record, 6, 100));
  assert(obd::applyFordDid(s, 0x280B, record, 9, 100));
  assert(near(s.tyreC[3], 24) && std::isnan(s.tyreC[2]));
  record[7] = 3; // A cached sensor record must not refresh a live temperature.
  assert(!obd::applyFordDid(s, 0x280B, record, 9, 200));
  assert(s.tyreTempMs[3] == 100);
  record[7] = 6;
  record[0] = 0x99; // An unknown sensor must not be assigned to a wheel.
  assert(obd::applyFordDid(s, 0x280B, record, 9, 200));
  assert(s.tyreTempMs[3] == 100 && std::isnan(s.tyreC[0]));
  id[0] = 0x98;
  assert(obd::applyFordDid(s, 0x2811, id, 4, 300));
  assert(std::isnan(s.tyreC[3]));
  obd::expire(s, 90100);
  assert(std::isnan(s.tyrePsi[3]));
}

void test_faults_and_multiframe_counts() {
  obd::Faults faults;
  const uint8_t stored[] = {2, 0x02, 0x99, 0xD0, 0x16};
  assert(obd::applyFaults(faults, stored, sizeof(stored), 100));
  assert(faults.valid && faults.count == 2 && faults.codes[0] == 0x0299);
  char code[6];
  obd::formatDtc(faults.codes[0], code);
  assert(std::strcmp(code, "P0299") == 0);
  obd::formatDtc(faults.codes[1], code);
  assert(std::strcmp(code, "U1016") == 0);
  const uint8_t incomplete[] = {2, 0x02, 0x99};
  assert(!obd::applyFaults(faults, incomplete, sizeof(incomplete), 200));
  assert(faults.count == 2 && faults.updatedMs == 100);
  const uint8_t invalid[] = {1, 0, 0};
  assert(!obd::applyFaults(faults, invalid, sizeof(invalid), 200));
  const uint8_t clear[] = {0};
  assert(obd::applyFaults(faults, clear, 1, 300));
  assert(faults.valid && faults.reportedCount == 0);
  uint8_t many[41];
  many[0] = 20;
  for (size_t i = 0; i < 20; ++i) { many[i * 2 + 1] = 2; many[i * 2 + 2] = uint8_t(i + 1); }
  assert(obd::applyFaults(faults, many, sizeof(many), 400));
  assert(faults.count == obd::kMaxCodes && faults.reportedCount == 20);
  uint8_t data[256];
  size_t n = obd::payload("008\r0:430302990401\r1:246300000000\r>", 0x43, -1, data, sizeof(data));
  assert(n == 8 && obd::applyFaults(faults, data + 1, n - 1, 500));
  assert(faults.count == 3 && faults.codes[2] == 0x2463);
}
}

int main() {
  test_elm_framing();
  test_engine_conversions_and_expiry();
  test_ford_wheel_mapping_and_temperature();
  test_faults_and_multiframe_counts();
  std::puts("OBD protocol: framing, conversions, tyre IDs, stale data and fault tests passed");
}
