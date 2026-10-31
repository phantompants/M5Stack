#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>

// Protocol decoding has no Arduino dependencies so malformed adapter replies
// and Ford conversions can be tested on a host before using a vehicle.
namespace obd {

enum Metric : uint8_t {
  Rpm, Speed, Coolant, Throttle, Load, Intake, Map, Barometer, Fuel,
  Ambient, Oil, Voltage, MetricCount
};
constexpr uint8_t kMetricPids[MetricCount] = {
    0x0C, 0x0D, 0x05, 0x11, 0x04, 0x0F, 0x0B, 0x33,
    0x2F, 0x46, 0x5C, 0x42};
// Wheel order is LF, RF, LR, RR; Ford DID order swaps the two rear wheels.
constexpr uint16_t kFordDids[] = {
    0x280F, 0x2810, 0x2812, 0x2811,
    0x2813, 0x2814, 0x2816, 0x2815, 0x280B};
constexpr size_t kMaxCodes = 16;

struct Faults {
  bool valid = false;
  uint8_t reportedCount = 0;
  uint8_t count = 0;
  uint16_t codes[kMaxCodes]{};
  uint32_t updatedMs = 0;
};

struct Readings {
  Readings();
  bool connected = false;
  bool ecuResponding = false;
  float engine[MetricCount];
  uint32_t metricMs[MetricCount]{};
  bool milKnown = false;
  bool milOn = false;
  uint8_t ecuCodeCount = 0;
  uint32_t milMs = 0;
  float tyrePsi[4];
  float tyreC[4];
  uint32_t tyrePressureMs[4]{};
  uint32_t tyreTempMs[4]{};
  uint32_t sensorId[4]{};
  float lastSensorC = NAN;
  uint32_t lastSensorId = 0;
  uint32_t lastSensorMs = 0;
  Faults stored;
  Faults pending;
  uint32_t updatedMs = 0;
};

// ELM headers must be off (ATH0), with automatic CAN formatting (ATCAF1).
// Supports single-line and ELM numbered, reassembled ISO-TP responses. Text
// banners, command echoes and malformed/incomplete hex are never data.
size_t payload(const char* response, uint8_t service, int pid,
               uint8_t* output, size_t capacity);
bool applyPid(Readings& state, uint8_t pid, const uint8_t* data,
              size_t length, uint32_t nowMs);
bool applyFordDid(Readings& state, uint16_t did, const uint8_t* data,
                  size_t length, uint32_t nowMs);
bool applyFaults(Faults& faults, const uint8_t* data, size_t length,
                 uint32_t nowMs);
void expire(Readings& state, uint32_t nowMs);
void formatDtc(uint16_t code, char (&text)[6]);
const char* dtcDescription(uint16_t code);

}  // namespace obd
