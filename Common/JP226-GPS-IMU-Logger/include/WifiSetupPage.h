#pragma once

#include <Arduino.h>
#include <M5GFX.h>
#include <atomic>

class LocationTime;

class WifiSetupPage {
 public:
  void open();
  void handleInput(LocationTime& locationTime);
  void update(uint32_t nowMs);
  void draw(uint32_t nowMs);
  void setCanvas(M5Canvas& canvas) { canvas_ = &canvas; }
  void requestRedraw() {
    haveFrameHash_ = false;
    markDirty();
  }

  bool active() const { return state_ != State::Closed; }

 private:
  enum class State : uint8_t {
    Closed,
    PreparingScan,
    Scanning,
    Networks,
    Password,
    Connecting,
    Result,
  };

  struct Network {
    String ssid;
    int32_t rssi = -127;
    bool secure = true;
  };

  static constexpr size_t kMaxNetworks = 20;
  static constexpr size_t kVisibleNetworks = 4;
  static constexpr size_t kMaxPasswordLength = 63;
  static constexpr uint32_t kConnectTimeoutMs = 20'000;
  static constexpr uint32_t kScanTimeoutMs = 15'000;

  void startScan();
  void stopScan();
  void scanFailed(const char* message);
  void collectScanResults(int count);
  void beginConnection(LocationTime& locationTime);
  void close();
  void markDirty();
  void drawNetworks();
  void drawPassword();
  void drawConnecting();
  void drawResult();

  State state_ = State::Closed;
  Network networks_[kMaxNetworks];
  size_t networkCount_ = 0;
  size_t selected_ = 0;
  String password_;
  bool showPassword_ = false;  // Tab toggles while typing the password.
  String resultMessage_;
  uint32_t connectStartedMs_ = 0;
  uint32_t scanStartedMs_ = 0;
  bool savedAutoReconnect_ = true;
  bool scanError_ = false;
  bool passiveScan_ = false;
  bool scanDiagnosticsRegistered_ = false;
  // The Wi-Fi event callback runs on another task. A completion error is
  // separate from scanComplete(), whose wrapper can report it as zero APs.
  static constexpr uint32_t kNoScanEvent = UINT32_MAX;
  std::atomic<uint32_t> scanEventStatus_{kNoScanEvent};
  std::atomic<uint16_t> scanEventCount_{0};
  uint32_t lastFrameHash_ = 0;
  bool haveFrameHash_ = false;
  bool dirty_ = false;
  M5Canvas* canvas_ = nullptr;
};
