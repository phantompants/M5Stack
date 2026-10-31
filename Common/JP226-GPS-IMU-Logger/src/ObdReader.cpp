#include "ObdReader.h"

#include <WiFi.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {
constexpr uint32_t kReconnectMs = 5000;
constexpr uint32_t kCommandTimeoutMs = 3500;
constexpr uint32_t kCommandGapMs = 100;
constexpr uint32_t kSlowScanMs = 30'000;
// Ranger uses 11-bit, 500 kbit/s CAN. M0 keeps adapter protocol selection
// session-local. All vehicle services below are read-only (01/03/07/22).
constexpr const char* kInitCommands[] = {
    "ATZ\r", "ATE0\r", "ATL0\r", "ATS0\r", "ATH0\r", "ATM0\r",
    "ATSP6\r", "ATCAF1\r", "ATCFC1\r", "ATSH7E0\r"};
constexpr uint8_t kPids[] = {
    0x0C, 0x0D, 0x05, 0x0C, 0x0D, 0x11, 0x04, 0x0F,
    0x0B, 0x33, 0x2F, 0x46, 0x5C, 0x42, 0x01};
constexpr size_t kPidCount = sizeof(kPids) / sizeof(kPids[0]);
constexpr size_t kFordCount = sizeof(obd::kFordDids) / sizeof(obd::kFordDids[0]);
// Pressures must not wait behind optional ID/temperature requests that may
// be unsupported on a different Ford BCM revision.
constexpr uint8_t kFordPollOrder[] = {4, 5, 6, 7, 0, 1, 2, 3, 8};

void compactReply(const char* source, char* output, size_t capacity) {
  size_t length = 0;
  for (; *source && length + 1 < capacity; ++source) {
    const unsigned char c = static_cast<unsigned char>(*source);
    if (c == '\r' || c == '\n' || c == '\t') {
      if (length && output[length - 1] != ' ') output[length++] = ' ';
    } else if (c >= 32 && c <= 126) output[length++] = char(c);
  }
  while (length && output[length - 1] == ' ') --length;
  output[length] = '\0';
}

bool isOk(const char* response) {
  while (*response) {
    while (*response == '\r' || *response == '\n' || *response == ' ') ++response;
    if (response[0] == 'O' && response[1] == 'K' &&
        (response[2] == 0 || response[2] == '\r' || response[2] == '\n' || response[2] == ' '))
      return true;
    while (*response && *response != '\r' && *response != '\n') ++response;
  }
  return false;
}
}  // namespace

void ObdReader::begin(fs::FS& storage) {
  File file = storage.open("/telemetry/logger.cfg", FILE_READ);
  if (!file) return;
  while (file.available()) {
    String line = file.readStringUntil('\n');
    line.trim();
    if (line.isEmpty() || line.startsWith("#") || line.startsWith(";")) continue;
    const int equals = line.indexOf('=');
    if (equals <= 0) continue;
    String key = line.substring(0, equals);
    String value = line.substring(equals + 1);
    key.trim(); value.trim(); key.toLowerCase();
    if (key == "obd_wifi_host" && !value.isEmpty() && value.length() < sizeof(host_)) {
      value.toCharArray(host_, sizeof(host_));
    } else if (key == "obd_wifi_port") {
      char* end = nullptr;
      const long parsed = strtol(value.c_str(), &end, 10);
      if (end != value.c_str() && *end == '\0' && parsed > 0 && parsed <= 65535)
        port_ = static_cast<uint16_t>(parsed);
    } else if (key == "obd_ford_tpms") {
      value.toLowerCase();
      fordTpmsEnabled_ = value == "1" || value == "true" || value == "yes";
    }
  }
  file.close();
}

void ObdReader::disconnect(uint32_t nowMs) {
  client_.stop();
  phase_ = Phase::Disconnected;
  state_ = Snapshot{};
  waitingForPrompt_ = false;
  responseLength_ = 0;
  overflow_ = false;
  lastConnectMs_ = nowMs;
}

void ObdReader::setPaused(bool paused) {
  if (paused == paused_) return;
  paused_ = paused;
  if (paused) disconnect(millis());
  else lastConnectMs_ = millis() - kReconnectMs;
}

bool ObdReader::pidSupported(uint8_t pid) const {
  if (pid == 0 || pid > 0x60) return true;
  const unsigned group = (pid - 1) / 32;
  const unsigned bit = 31 - ((pid - 1) % 32);
  return !supportKnown_[group] || (supported_[group] & (uint32_t(1) << bit));
}

void ObdReader::update(uint32_t nowMs) {
  obd::expire(state_, nowMs);
  if (paused_) return;
  if (phase_ != Phase::Disconnected &&
      (WiFi.status() != WL_CONNECTED || !client_.connected())) disconnect(nowMs);
  if (phase_ == Phase::Disconnected) {
    if (WiFi.status() != WL_CONNECTED || nowMs - lastConnectMs_ < kReconnectMs) return;
    lastConnectMs_ = nowMs;
    client_.setTimeout(0);
    if (!client_.connect(host_, port_, 250)) {
      lastError_ = "TCP connection failed";
      return;
    }
    state_.connected = true;
    phase_ = Phase::Initializing;
    initIndex_ = supportIndex_ = pidIndex_ = fordIndex_ = 0;
    for (auto& known : supportKnown_) known = false;
    faultScanRequested_ = fordScanRequested_ = true;
    lastError_ = "";
    lastCommandMs_ = nowMs - kCommandGapMs;
  }

  // Bound serial work per loop even if an adapter sends continuous junk.
  size_t budget = 512;
  while (budget-- && client_.available()) {
    const char c = static_cast<char>(client_.read());
    if (!waitingForPrompt_) continue;
    if (c == '>') {
      if (overflow_) {
        lastError_ = "ELM response too long";
        disconnect(nowMs);
        return;
      }
      response_[responseLength_] = '\0';
      waitingForPrompt_ = false;
      processResponse(nowMs);
      responseLength_ = 0;
      lastCommandMs_ = nowMs;
      if (phase_ == Phase::Disconnected) return;
    } else if (responseLength_ < sizeof(response_) - 1) {
      response_[responseLength_++] = c;
    } else {
      overflow_ = true;
    }
  }
  if (waitingForPrompt_ && nowMs - commandStartedMs_ >= kCommandTimeoutMs) {
    // Reconnect to resynchronise: a late reply must never be attributed to
    // the following request, especially after switching ECU addresses.
    lastError_ = "ELM prompt timed out";
    if (phase_ == Phase::Ford) {
      notePressureResult(obd::kFordDids[kFordPollOrder[fordIndex_]], TpmsResult::TimedOut, nowMs);
      // Keep ordinary engine/pressure polling usable if optional Ford IDs
      // hang the adapter. A manual TPMS retry permits another attempt.
      if (fordIndex_ >= 4) skipFordOptional_ = true;
    } else if (phase_ == Phase::SelectBcm || phase_ == Phase::SelectBcmFilter) {
      noteTpmsSetupFailure("BCM SETUP TIMEOUT", nowMs);
    }
    disconnect(nowMs);
    return;
  }
  if (!waitingForPrompt_ && nowMs - lastCommandMs_ >= kCommandGapMs) sendNext(nowMs);
}

void ObdReader::finishEngineCycle(uint32_t nowMs) {
  pidIndex_ = 0;
  if (faultScanRequested_ || nowMs - lastFaultMs_ >= kSlowScanMs) {
    phase_ = Phase::StoredCodes;
    faultScanRequested_ = false;
    lastFaultMs_ = nowMs;
  } else if (fordTpmsEnabled_ && (fordScanRequested_ || nowMs - lastFordMs_ >= kSlowScanMs)) {
    phase_ = Phase::SelectBcm;
    fordScanRequested_ = false;
    lastFordMs_ = nowMs;
  }
}

void ObdReader::sendNext(uint32_t nowMs) {
  char command[20]{};
  switch (phase_) {
    case Phase::Initializing:
      std::snprintf(command, sizeof(command), "%s", kInitCommands[initIndex_]);
      break;
    case Phase::Discovering:
      std::snprintf(command, sizeof(command), "01%02X\r", supportIndex_ * 0x20);
      break;
    case Phase::Polling:
      while (pidIndex_ < kPidCount && !pidSupported(kPids[pidIndex_])) ++pidIndex_;
      if (pidIndex_ == kPidCount) { finishEngineCycle(nowMs); return; }
      std::snprintf(command, sizeof(command), "01%02X\r", kPids[pidIndex_]);
      break;
    case Phase::StoredCodes: std::strcpy(command, "03\r"); break;
    case Phase::PendingCodes: std::strcpy(command, "07\r"); break;
    case Phase::SelectBcm:
      tpmsSetupError_ = "";
      std::strcpy(command, "ATSH726\r");
      break;
    case Phase::SelectBcmFilter: std::strcpy(command, "ATCRA72E\r"); break;
    case Phase::SelectEngine: std::strcpy(command, "ATSH7E0\r"); break;
    case Phase::SelectEngineFilter: std::strcpy(command, "ATCRA7E8\r"); break;
    case Phase::RestoreAutoFilter: std::strcpy(command, "ATAR\r"); break;
    case Phase::Ford:
      if (skipFordOptional_ && fordIndex_ >= 4) { phase_ = Phase::SelectEngine; return; }
      std::snprintf(command, sizeof(command), "22%04X\r", obd::kFordDids[kFordPollOrder[fordIndex_]]);
      break;
    default: return;
  }
  client_.print(command);
  commandStartedMs_ = lastCommandMs_ = nowMs;
  responseLength_ = 0;
  overflow_ = false;
  waitingForPrompt_ = true;
}

void ObdReader::processResponse(uint32_t nowMs) {
  uint8_t data[256]{};
  size_t size = 0;
  switch (phase_) {
    case Phase::Initializing:
      if (initIndex_ == 0 ? !std::strstr(response_, "ELM") && !std::strstr(response_, "STN") && !isOk(response_)
                          : !isOk(response_)) {
        lastError_ = "ELM setup rejected";
        disconnect(nowMs);
        return;
      }
      if (++initIndex_ == sizeof(kInitCommands) / sizeof(kInitCommands[0]))
        phase_ = Phase::Discovering;
      break;
    case Phase::Discovering:
      size = obd::payload(response_, 0x41, supportIndex_ * 0x20, data, sizeof(data));
      if (size >= 6) {
        supported_[supportIndex_] = (uint32_t(data[2]) << 24) | (uint32_t(data[3]) << 16) |
                                    (uint32_t(data[4]) << 8) | data[5];
        supportKnown_[supportIndex_] = true;
        state_.ecuResponding = true;
        state_.updatedMs = nowMs;
        if (!(supported_[supportIndex_] & 1)) {
          for (size_t i = supportIndex_ + 1; i < 3; ++i) {
            supportKnown_[i] = true;
            supported_[i] = 0;
          }
          phase_ = Phase::Polling;
          break;
        }
      }
      if (++supportIndex_ == 3) phase_ = Phase::Polling;
      break;
    case Phase::Polling:
      size = obd::payload(response_, 0x41, kPids[pidIndex_], data, sizeof(data));
      if (size >= 2 && obd::applyPid(state_, kPids[pidIndex_], data + 2, size - 2, nowMs))
        lastError_ = "";
      else lastError_ = "ECU PID unavailable";
      if (++pidIndex_ == kPidCount) finishEngineCycle(nowMs);
      break;
    case Phase::StoredCodes:
      size = obd::payload(response_, 0x43, -1, data, sizeof(data));
      if (size < 2 || !obd::applyFaults(state_.stored, data + 1, size - 1, nowMs))
        state_.stored.valid = false;
      phase_ = Phase::PendingCodes;
      break;
    case Phase::PendingCodes:
      size = obd::payload(response_, 0x47, -1, data, sizeof(data));
      if (size < 2 || !obd::applyFaults(state_.pending, data + 1, size - 1, nowMs))
        state_.pending.valid = false;
      phase_ = Phase::Polling;
      finishEngineCycle(nowMs);
      break;
    case Phase::SelectBcm:
      if (isOk(response_)) phase_ = Phase::SelectBcmFilter;
      else {
        noteTpmsSetupFailure("BCM TX HEADER REJECTED", nowMs);
        phase_ = Phase::SelectEngine;
      }
      break;
    case Phase::SelectBcmFilter:
      if (isOk(response_)) { phase_ = Phase::Ford; fordIndex_ = 0; }
      else {
        noteTpmsSetupFailure("BCM RX FILTER REJECTED", nowMs);
        phase_ = Phase::SelectEngine;
      }
      break;
    case Phase::SelectEngine:
      if (!isOk(response_)) {
        lastError_ = "Engine header restore rejected";
        disconnect(nowMs);
        return;
      }
      phase_ = Phase::SelectEngineFilter;
      break;
    case Phase::SelectEngineFilter:
      // Older clones may lack CRA; retain working engine polling by restoring
      // the default automatic filter, without issuing a BCM read unfiltered.
      phase_ = isOk(response_) ? Phase::Polling : Phase::RestoreAutoFilter;
      break;
    case Phase::RestoreAutoFilter:
      if (isOk(response_)) phase_ = Phase::Polling;
      else {
        lastError_ = "Engine RX filter restore rejected";
        disconnect(nowMs);
      }
      break;
    case Phase::Ford: {
      const uint16_t did = obd::kFordDids[kFordPollOrder[fordIndex_]];
      size = obd::payload(response_, 0x62, did, data, sizeof(data));
      if (size >= 3 && obd::applyFordDid(state_, did, data + 3, size - 3, nowMs)) {
        notePressureResult(did, TpmsResult::Ok, nowMs);
      } else {
        lastError_ = "Ford TPMS unavailable";
        const size_t negativeSize = obd::payload(response_, 0x7F, -1, data, sizeof(data));
        if (negativeSize >= 3 && data[1] == 0x22) {
          const uint8_t code = data[2];
          notePressureResult(did, code == 0x78 ? TpmsResult::Pending : TpmsResult::Rejected, nowMs, code);
          if (code == 0x78) {
            waitingForPrompt_ = true;
            return;  // Await the final reply within the original timeout.
          }
        } else {
          notePressureResult(did, size >= 5 ? TpmsResult::Unavailable :
              std::strstr(response_, "NO DATA") ? TpmsResult::NoData : TpmsResult::Malformed, nowMs);
        }
      }
      if (++fordIndex_ == kFordCount) phase_ = Phase::SelectEngine;
      break;
    }
    default: break;
  }
}

void ObdReader::notePressureResult(uint16_t did, TpmsResult result,
                                   uint32_t nowMs, uint8_t negativeResponse) {
  for (size_t wheel = 0; wheel < 4; ++wheel) {
    if (did != obd::kFordDids[wheel + 4]) continue;
    auto& diagnostic = pressureDiagnostics_[wheel];
    diagnostic.did = did;
    diagnostic.result = result;
    diagnostic.negativeResponse = negativeResponse;
    diagnostic.updatedMs = nowMs;
    response_[responseLength_] = '\0';
    compactReply(response_, diagnostic.reply, sizeof(diagnostic.reply));
    Serial.printf("TPMS %04X: %s NRC=%02X reply=%s\n", did,
                  tpmsResultText(result), negativeResponse, diagnostic.reply);
    break;
  }
}

void ObdReader::noteTpmsSetupFailure(const char* message, uint32_t nowMs) {
  tpmsSetupError_ = message;
  for (size_t wheel = 0; wheel < 4; ++wheel)
    notePressureResult(obd::kFordDids[wheel + 4], TpmsResult::SetupFailed, nowMs);
}

const char* ObdReader::tpmsResultText(TpmsResult result) {
  switch (result) {
    case TpmsResult::Ok: return "READ OK";
    case TpmsResult::NoData: return "NO DATA";
    case TpmsResult::Rejected: return "REQUEST REJECTED";
    case TpmsResult::Malformed: return "BAD REPLY";
    case TpmsResult::Unavailable: return "NO SENSOR DATA";
    case TpmsResult::TimedOut: return "TIMED OUT";
    case TpmsResult::Pending: return "RESPONSE PENDING";
    case TpmsResult::SetupFailed: return "SETUP FAILED";
    default: return "NOT READ YET";
  }
}

const char* ObdReader::tpmsStatusText() const {
  if (!fordTpmsEnabled_) return "FORD TPMS DISABLED";
  if (!state_.connected) return "WAITING FOR ADAPTER";
  if (tpmsSetupError_[0]) return tpmsSetupError_;
  for (float value : state_.tyrePsi)
    if (std::isfinite(value)) return "PRESSURES RECEIVED";
  for (const auto& result : pressureDiagnostics_)
    if (result.result != TpmsResult::Waiting && result.result != TpmsResult::Ok)
      return tpmsResultText(result.result);
  for (const auto& result : pressureDiagnostics_)
    if (result.result == TpmsResult::Ok) return "PRESSURES STALE";
  return "WAITING FOR BCM";
}

const char* ObdReader::statusText() const {
  if (paused_) return "WI-FI SETUP";
  if (state_.ecuResponding) return "ECU DATA";
  if (state_.connected) return "ADAPTER ONLY";
  if (WiFi.status() != WL_CONNECTED) return "WI-FI OFFLINE";
  return "CONNECTING TCP";
}
