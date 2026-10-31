#pragma once

#include <Arduino.h>
#include <FS.h>
#include <WiFiClient.h>

#include "ObdProtocol.h"

class ObdReader {
 public:
  using Snapshot = obd::Readings;
  enum class TpmsResult : uint8_t {
    Waiting, Ok, NoData, Rejected, Malformed, Unavailable, TimedOut, Pending, SetupFailed
  };
  struct TpmsDiagnostic {
    uint16_t did = 0;
    TpmsResult result = TpmsResult::Waiting;
    uint8_t negativeResponse = 0;
    uint32_t updatedMs = 0;
    char reply[80]{};
  };
  void begin(fs::FS& storage);
  void update(uint32_t nowMs);
  void setPaused(bool paused);
  void requestFaultScan() { faultScanRequested_ = true; }
  void requestTpmsScan() { fordScanRequested_ = true; skipFordOptional_ = false; }
  Snapshot snapshot() const { return state_; }
  const char* host() const { return host_; }
  uint16_t port() const { return port_; }
  bool fordTpmsEnabled() const { return fordTpmsEnabled_; }
  const char* statusText() const;
  const char* lastError() const { return lastError_; }
  const char* tpmsStatusText() const;
  const TpmsDiagnostic& tpmsDiagnostic(size_t wheel) const {
    return pressureDiagnostics_[wheel < 4 ? wheel : 0];
  }
  static const char* tpmsResultText(TpmsResult result);

 private:
  enum class Phase : uint8_t {
    Disconnected, Initializing, Discovering, Polling, StoredCodes,
    PendingCodes, SelectBcm, SelectBcmFilter, Ford, SelectEngine,
    SelectEngineFilter, RestoreAutoFilter
  };
  void disconnect(uint32_t nowMs);
  void sendNext(uint32_t nowMs);
  void processResponse(uint32_t nowMs);
  void finishEngineCycle(uint32_t nowMs);
  bool pidSupported(uint8_t pid) const;
  void notePressureResult(uint16_t did, TpmsResult result, uint32_t nowMs,
                           uint8_t negativeResponse = 0);
  void noteTpmsSetupFailure(const char* message, uint32_t nowMs);

  WiFiClient client_;
  Snapshot state_;
  char host_[48] = "192.168.0.10";
  uint16_t port_ = 35000;
  char response_[1024]{};
  size_t responseLength_ = 0;
  uint32_t lastConnectMs_ = 0;
  uint32_t commandStartedMs_ = 0;
  uint32_t lastCommandMs_ = 0;
  uint32_t lastFaultMs_ = 0;
  uint32_t lastFordMs_ = 0;
  uint32_t supported_[3]{};
  bool supportKnown_[3]{};
  uint8_t initIndex_ = 0;
  uint8_t supportIndex_ = 0;
  uint8_t pidIndex_ = 0;
  uint8_t fordIndex_ = 0;
  Phase phase_ = Phase::Disconnected;
  bool waitingForPrompt_ = false;
  bool overflow_ = false;
  bool paused_ = false;
  bool faultScanRequested_ = true;
  bool fordScanRequested_ = true;
  bool fordTpmsEnabled_ = true;
  bool skipFordOptional_ = false;
  TpmsDiagnostic pressureDiagnostics_[4]{};
  const char* tpmsSetupError_ = "";
  const char* lastError_ = "";
};
