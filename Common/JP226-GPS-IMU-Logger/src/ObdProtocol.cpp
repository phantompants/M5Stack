#include "ObdProtocol.h"

#include <cstdio>
#include <cstring>

namespace obd {
namespace {
int hex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool matches(const uint8_t* bytes, size_t size, uint8_t service, int pid) {
  if (size == 0 || bytes[0] != service) return false;
  if (pid < 0) return true;
  if (service == 0x62)
    return size >= 3 && bytes[1] == (pid >> 8) && bytes[2] == (pid & 0xFF);
  return size >= 2 && bytes[1] == pid;
}

uint32_t u32(const uint8_t* data) {
  return (uint32_t(data[0]) << 24) | (uint32_t(data[1]) << 16) |
         (uint32_t(data[2]) << 8) | data[3];
}
}  // namespace

Readings::Readings() {
  for (auto& value : engine) value = NAN;
  for (auto& value : tyrePsi) value = NAN;
  for (auto& value : tyreC) value = NAN;
}

size_t payload(const char* response, uint8_t service, int pid,
               uint8_t* output, size_t capacity) {
  size_t total = 0;
  size_t declared = 0;
  bool numbered = false;
  unsigned nextLine = 0;
  while (*response) {
    const char* end = response;
    while (*end && *end != '\r' && *end != '\n' && *end != '>') ++end;
    const char* start = response;
    while (start < end && (*start == ' ' || *start == '\t')) ++start;
    bool indexed = end - start >= 2 && hex(start[0]) >= 0 && start[1] == ':';
    unsigned line = indexed ? unsigned(hex(start[0])) : 0;
    if (indexed) start += 2;
    uint8_t bytes[256];
    size_t size = 0;
    int high = -1;
    bool valid = true;
    for (const char* p = start; p < end; ++p) {
      if (*p == ' ' || *p == '\t') continue;
      int digit = hex(*p);
      if (digit < 0) { valid = false; break; }
      if (high < 0) high = digit;
      else {
        if (size == sizeof(bytes)) { valid = false; break; }
        bytes[size++] = uint8_t((high << 4) | digit);
        high = -1;
      }
    }
    // ELM puts a three-hex-digit payload length before numbered CAN lines.
    if (!indexed && end - start == 3 && hex(start[0]) >= 0 &&
        hex(start[1]) >= 0 && hex(start[2]) >= 0) {
      declared = (hex(start[0]) << 8) | (hex(start[1]) << 4) | hex(start[2]);
    } else if (valid && high < 0 && size > 0) {
      if (indexed) {
        if (!numbered) {
          if (line != 0 || !matches(bytes, size, service, pid)) return 0;
          numbered = true;
        }
        if (line != (nextLine++ & 0xF)) return 0;
        size_t copy = size;
        if (declared && total + copy > declared) copy = declared - total;
        if (total + copy > capacity) return 0;
        std::memcpy(output + total, bytes, copy);
        total += copy;
      } else if (!numbered && matches(bytes, size, service, pid)) {
        if (size > capacity) return 0;
        std::memcpy(output, bytes, size);
        return size;
      }
    } else if (indexed) {
      return 0;
    }
    response = *end ? end + 1 : end;
  }
  if (!numbered || (declared && total != declared)) return 0;
  return total;
}

bool applyPid(Readings& state, uint8_t pid, const uint8_t* data,
              size_t length, uint32_t nowMs) {
  if (pid == 0x01) {
    if (length < 4) return false;
    state.milKnown = true;
    state.milOn = (data[0] & 0x80) != 0;
    state.ecuCodeCount = data[0] & 0x7F;
    state.milMs = nowMs;
  } else {
    size_t metric = 0;
    while (metric < MetricCount && kMetricPids[metric] != pid) ++metric;
    if (metric == MetricCount || length < 1) return false;
    float value = data[0];
    switch (metric) {
      case Rpm:
      case Voltage:
        if (length < 2) return false;
        value = ((data[0] << 8) | data[1]) / (metric == Rpm ? 4.0f : 1000.0f);
        break;
      case Coolant: case Intake: case Ambient: case Oil: value -= 40; break;
      case Throttle: case Load: case Fuel: value *= 100.0f / 255.0f; break;
      default: break;
    }
    state.engine[metric] = value;
    state.metricMs[metric] = nowMs;
  }
  state.ecuResponding = true;
  state.updatedMs = nowMs;
  return true;
}

bool applyFordDid(Readings& state, uint16_t did, const uint8_t* data,
                  size_t length, uint32_t nowMs) {
  for (size_t wheel = 0; wheel < 4; ++wheel) {
    if (did == kFordDids[wheel]) {
      if (length < 4) return false;
      const uint32_t id = u32(data);
      if (id == 0 || id == UINT32_MAX) return false;
      // Do not carry a temperature across a wheel rotation / ID reassignment.
      if (state.sensorId[wheel] != id) state.tyreC[wheel] = NAN;
      state.sensorId[wheel] = id;
      return true;
    }
    if (did == kFordDids[wheel + 4]) {
      if (length < 2) return false;
      const unsigned raw = (data[0] << 8) | data[1];
      // OBDb's Ranger profile uses raw / 20 psi, not the older Focus formula.
      // All-ones is unavailable. Preserve a reported zero pressure.
      if (raw == 0xFFFF) return false;
      state.tyrePsi[wheel] = raw / 20.0f;
      state.tyrePressureMs[wheel] = nowMs;
      return true;
    }
  }
  if (did != 0x280B || length < 9) return false;
  // Last-received sensor record: ID[4], pressure[2], temp, status, checksum.
  // The positional ID mapping is experimental on the Next Gen Ranger.
  const uint32_t id = u32(data);
  if (id == 0 || id == UINT32_MAX || data[6] == 0xFF || data[7] < 6 || data[7] == 0xFF)
    return false;
  state.lastSensorC = float(data[6]) - 40.0f;
  state.lastSensorId = id;
  state.lastSensorMs = nowMs;
  for (size_t wheel = 0; wheel < 4; ++wheel) {
    if (state.sensorId[wheel] == id) {
      state.tyreC[wheel] = state.lastSensorC;
      state.tyreTempMs[wheel] = nowMs;
    }
  }
  return true;
}

bool applyFaults(Faults& faults, const uint8_t* data, size_t length,
                 uint32_t nowMs) {
  // ISO 15765-4 mode 03/07 includes a count before the two-byte DTCs.
  if (length == 0 || size_t(data[0]) * 2 + 1 > length) return false;
  Faults next;
  next.reportedCount = data[0];
  next.count = data[0] < kMaxCodes ? data[0] : kMaxCodes;
  for (size_t i = 0; i < data[0]; ++i) {
    const uint16_t code = (data[1 + i * 2] << 8) | data[2 + i * 2];
    if (code == 0) return false;
    if (i < kMaxCodes) next.codes[i] = code;
  }
  next.valid = true;
  next.updatedMs = nowMs;
  faults = next;
  return true;
}

void expire(Readings& state, uint32_t nowMs) {
  for (size_t i = 0; i < MetricCount; ++i)
    if (nowMs - state.metricMs[i] >= 15'000) state.engine[i] = NAN;
  if (nowMs - state.updatedMs >= 15'000) state.ecuResponding = false;
  if (nowMs - state.milMs >= 15'000) state.milKnown = false;
  for (size_t i = 0; i < 4; ++i) {
    if (nowMs - state.tyrePressureMs[i] >= 90'000) state.tyrePsi[i] = NAN;
    if (nowMs - state.tyreTempMs[i] >= 90'000) state.tyreC[i] = NAN;
  }
  if (nowMs - state.lastSensorMs >= 90'000) state.lastSensorC = NAN;
  if (nowMs - state.stored.updatedMs >= 90'000) state.stored.valid = false;
  if (nowMs - state.pending.updatedMs >= 90'000) state.pending.valid = false;
}

void formatDtc(uint16_t code, char (&text)[6]) {
  std::snprintf(text, sizeof(text), "%c%u%03X", "PCBU"[(code >> 14) & 3],
                (code >> 12) & 3, code & 0xFFF);
}

const char* dtcDescription(uint16_t code) {
  switch (code) {
    case 0x0087: return "Fuel rail pressure low";
    case 0x0101: return "Airflow range/performance";
    case 0x0128: return "Coolant below thermostat temp";
    case 0x0234: return "Turbo overboost";
    case 0x0299: return "Turbo underboost";
    case 0x0401: return "EGR flow insufficient";
    case 0x2002: return "DPF efficiency below threshold";
    case 0x2453: return "DPF pressure sensor range";
    case 0x2463: return "DPF soot accumulation";
    default: return "Look up code for this vehicle";
  }
}
}  // namespace obd
