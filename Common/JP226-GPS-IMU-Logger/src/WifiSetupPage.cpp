#include "WifiSetupPage.h"

#include <M5Cardputer.h>
#include <WiFi.h>
#include <esp_wifi.h>

#include "LocationTime.h"

namespace {

constexpr uint16_t kBackground = TFT_BLACK;
constexpr uint16_t kForeground = TFT_WHITE;
constexpr uint16_t kAccent = TFT_CYAN;
constexpr uint16_t kSuccess = TFT_GREEN;
constexpr uint16_t kWarning = TFT_ORANGE;

String clipped(const String& value, size_t maximum) {
  if (value.length() <= maximum) return value;
  if (maximum <= 3) return value.substring(0, maximum);
  return value.substring(0, maximum - 3) + "...";
}

uint32_t frameBufferHash(const M5Canvas& canvas) {
  const auto* bytes = static_cast<const uint8_t*>(canvas.getBuffer());
  const size_t length = canvas.bufferLength();
  uint32_t hash = 2166136261U;
  for (size_t index = 0; index < length; ++index) {
    hash ^= bytes[index];
    hash *= 16777619U;
  }
  return hash;
}

}  // namespace

void WifiSetupPage::open() {
  if (active()) return;
  if (!scanDiagnosticsRegistered_) {
    WiFi.onEvent([this](arduino_event_id_t, arduino_event_info_t info) {
      scanEventCount_.store(info.wifi_scan_done.number);
      scanEventStatus_.store(info.wifi_scan_done.status);
      Serial.printf("Wi-Fi scan event: status=%lu count=%u id=%u\n",
                    static_cast<unsigned long>(info.wifi_scan_done.status),
                    info.wifi_scan_done.number, info.wifi_scan_done.scan_id);
    }, ARDUINO_EVENT_WIFI_SCAN_DONE);
    scanDiagnosticsRegistered_ = true;
  }
  savedAutoReconnect_ = WiFi.getAutoReconnect();
  startScan();
}

void WifiSetupPage::startScan() {
  stopScan();
  scanError_ = false;
  passiveScan_ = false;
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  // A saved-SSID connection attempt takes priority over a user scan. Stop
  // that attempt without erasing credentials or shutting down ESP-NOW.
  if (WiFi.status() != WL_CONNECTED) WiFi.disconnect(false, false);
  WiFi.scanDelete();
  networkCount_ = 0;
  selected_ = 0;
  state_ = State::PreparingScan;
  scanStartedMs_ = millis();
  resultMessage_ = "Scanning for Wi-Fi...";
  haveFrameHash_ = false;
  markDirty();
}

void WifiSetupPage::stopScan() {
  // Don't try to stop the driver's internal saved-network connection scan.
  if (state_ == State::Scanning && WiFi.scanComplete() == WIFI_SCAN_RUNNING)
    esp_wifi_scan_stop();
}

void WifiSetupPage::scanFailed(const char* message) {
  scanError_ = true;
  stopScan();
  WiFi.scanDelete();
  resultMessage_ = message;
  state_ = State::Result;
  Serial.printf("Wi-Fi scan: %s\n", message);
  markDirty();
}

void WifiSetupPage::collectScanResults(int count) {
  networkCount_ = 0;
  for (int i = 0; i < count && networkCount_ < kMaxNetworks; ++i) {
    const String ssid = WiFi.SSID(i);
    if (ssid.isEmpty()) continue;

    size_t existing = kMaxNetworks;
    for (size_t n = 0; n < networkCount_; ++n) {
      if (networks_[n].ssid == ssid) {
        existing = n;
        break;
      }
    }

    const int32_t rssi = WiFi.RSSI(i);
    const bool secure = WiFi.encryptionType(i) != WIFI_AUTH_OPEN;
    if (existing < networkCount_) {
      if (rssi > networks_[existing].rssi) {
        networks_[existing].rssi = rssi;
        networks_[existing].secure = secure;
      }
      continue;
    }

    networks_[networkCount_].ssid = ssid;
    networks_[networkCount_].rssi = rssi;
    networks_[networkCount_].secure = secure;
    ++networkCount_;
  }
  WiFi.scanDelete();
  Serial.printf("Wi-Fi scan: %s pass, %d access points, %u visible networks\n",
                passiveScan_ ? "passive" : "active", count,
                static_cast<unsigned>(networkCount_));
  if (networkCount_ == 0 && !passiveScan_) {
    // Some APs do not answer probe requests. Listen for beacons on a second
    // all-channel pass before concluding that the area has no named APs.
    passiveScan_ = true;
    state_ = State::PreparingScan;
    scanStartedMs_ = millis();
    markDirty();
    return;
  }
  state_ = State::Networks;
  resultMessage_ = networkCount_ == 0 ? "No networks found" : "";
  Serial.printf("Wi-Fi scan: complete, %d access points, %u visible networks\n",
                count, static_cast<unsigned>(networkCount_));
  markDirty();
}

void WifiSetupPage::handleInput(LocationTime& locationTime) {
  if (!active() || !M5Cardputer.Keyboard.isChange() ||
      !M5Cardputer.Keyboard.isPressed()) {
    return;
  }

  auto& keys = M5Cardputer.Keyboard.keysState();

  if (state_ == State::Password) {
    if (keys.esc) {
      state_ = State::Networks;
      password_ = "";
      markDirty();
      return;
    }
    if (keys.tab) {
      showPassword_ = !showPassword_;
      markDirty();
      return;
    }
    if (keys.del || keys.backspace) {
      if (!password_.isEmpty()) password_.remove(password_.length() - 1);
      markDirty();
      return;
    }
    if (keys.enter) {
      beginConnection(locationTime);
      return;
    }
    for (char c : keys.word) {
      if (c >= 32 && c <= 126 && password_.length() < kMaxPasswordLength) {
        password_ += c;
      }
    }
    markDirty();
    return;
  }

  if (keys.esc || M5Cardputer.Keyboard.isKeyPressed('q')) {
    close();
    return;
  }

  if (M5Cardputer.Keyboard.isKeyPressed('r')) {
    startScan();
    return;
  }

  if (state_ == State::Networks) {
    if (networkCount_ > 0 &&
               (keys.up || M5Cardputer.Keyboard.isKeyPressed(','))) {
      selected_ = selected_ == 0 ? networkCount_ - 1 : selected_ - 1;
      markDirty();
    } else if (networkCount_ > 0 &&
               (keys.down || M5Cardputer.Keyboard.isKeyPressed('.'))) {
      selected_ = (selected_ + 1) % networkCount_;
      markDirty();
    } else if (networkCount_ > 0 && keys.enter) {
      password_ = "";
      showPassword_ = false;
      if (networks_[selected_].secure) {
        state_ = State::Password;
        markDirty();
      } else {
        beginConnection(locationTime);
      }
    }
  } else if (state_ == State::Result) {
    if (keys.enter) {
      close();
    }
  }
}

void WifiSetupPage::beginConnection(LocationTime& locationTime) {
  if (networkCount_ == 0 || selected_ >= networkCount_) return;
  locationTime.setWifiCredentials(networks_[selected_].ssid, password_);
  password_ = "";
  connectStartedMs_ = millis();
  resultMessage_ = "Connecting...";
  state_ = State::Connecting;
  markDirty();
}

void WifiSetupPage::update(uint32_t nowMs) {
  if (state_ == State::PreparingScan) {
    // Let the disconnect event finish before starting the asynchronous scan.
    if (nowMs - scanStartedMs_ < 250) return;
    scanEventStatus_.store(kNoScanEvent);
    scanEventCount_.store(0);
    const int result = WiFi.scanNetworks(true, true, passiveScan_, 400, 0);
    Serial.printf("Wi-Fi scan: start returned %d\n", result);
    scanStartedMs_ = nowMs;
    if (result >= 0) collectScanResults(result);
    else if (result == WIFI_SCAN_RUNNING) state_ = State::Scanning;
    else scanFailed("Scan could not start. Press R.");
  } else if (state_ == State::Scanning) {
    const int result = WiFi.scanComplete();
    const uint32_t status = scanEventStatus_.load();
    if (status != kNoScanEvent && status != 0) {
      scanFailed("Scan interrupted. Press R.");
    } else if (result == 0 && status == 0 && scanEventCount_.load() > 0) {
      scanFailed("Cannot read scan results. Press R.");
    } else if (result >= 0 && status == 0) {
      collectScanResults(result);
    } else if (result == WIFI_SCAN_FAILED) {
      scanFailed("Wi-Fi scan failed. Press R.");
    } else if (nowMs - scanStartedMs_ >= kScanTimeoutMs) {
      scanFailed("Wi-Fi scan timed out. Press R.");
    }
  } else if (state_ == State::Connecting) {
    if (WiFi.status() == WL_CONNECTED) {
      resultMessage_ = "Connected to " + WiFi.SSID();
      state_ = State::Result;
      markDirty();
    } else if (nowMs - connectStartedMs_ >= kConnectTimeoutMs) {
      resultMessage_ = "Connection failed or timed out";
      state_ = State::Result;
      markDirty();
    }
  }
}

void WifiSetupPage::draw(uint32_t nowMs) {
  (void)nowMs;
  if (!active() || canvas_ == nullptr || !dirty_) return;
  dirty_ = false;

  auto& display = *canvas_;
  display.startWrite();
  display.fillScreen(kBackground);
  display.setTextDatum(top_left);
  display.setTextWrap(false);
  display.setTextSize(2);
  display.setCursor(2, 2);
  display.setTextColor(kAccent, kBackground);
  display.println("Wi-Fi setup");
  display.drawFastHLine(0, 19, display.width(), kAccent);
  display.setCursor(2, 23);
  display.setTextColor(kForeground, kBackground);

  switch (state_) {
    case State::PreparingScan:
    case State::Scanning:
      display.println(passiveScan_ ? "Retrying scan..." : "Scanning nearby");
      display.println("Wi-Fi networks...");
      display.setTextColor(kSuccess, kBackground);
      display.println("Logging continues");
      display.setTextSize(2);
      display.setTextColor(kForeground, kBackground);
      display.println("Q: QUIT");
      break;
    case State::Networks:
      drawNetworks();
      break;
    case State::Password:
      drawPassword();
      break;
    case State::Connecting:
      drawConnecting();
      break;
    case State::Result:
      drawResult();
      break;
    default:
      break;
  }
  display.endWrite();
  const uint32_t frameHash = frameBufferHash(display);
  if (!haveFrameHash_ || frameHash != lastFrameHash_) {
    display.pushSprite(0, 0);
    lastFrameHash_ = frameHash;
    haveFrameHash_ = true;
  }
}

void WifiSetupPage::drawNetworks() {
  auto& display = *canvas_;
  display.setTextSize(2);
  if (networkCount_ == 0) {
    display.setTextColor(kWarning, kBackground);
    display.println("No named networks");
    display.setTextSize(1);
    display.println("2.4 GHz only; active + passive tried");
    display.println("R: retry  Q: quit");
  } else {
    size_t first = selected_ >= kVisibleNetworks
                       ? selected_ - kVisibleNetworks + 1
                       : 0;
    for (size_t row = 0; row < kVisibleNetworks; ++row) {
      const size_t index = first + row;
      if (index >= networkCount_) break;
      display.setTextColor(index == selected_ ? TFT_BLACK : kForeground,
                           index == selected_ ? kAccent : kBackground);
      display.printf("%c%-16s%s\n", index == selected_ ? '>' : ' ',
                     clipped(networks_[index].ssid, 16).c_str(),
                     networks_[index].secure ? "*" : " ");
    }
  }
  display.setTextSize(2);
  display.setTextColor(kForeground, kBackground);
  display.drawString("ENTER: SELECT", 2, 91);
  display.drawString("R: AGAIN  Q: QUIT", 2, 108);
}

void WifiSetupPage::drawPassword() {
  auto& display = *canvas_;
  display.setTextSize(2);
  display.println("Network:");
  display.println(clipped(networks_[selected_].ssid, 19));
  display.println("Password:");
  // Show the last 16 characters, as asterisks unless Tab revealed them.
  const size_t visible = password_.length() > 16 ? 16 : password_.length();
  String shown = password_.substring(password_.length() - visible);
  if (!showPassword_) {
    for (size_t i = 0; i < visible; ++i) shown.setCharAt(i, '*');
  }
  if (password_.length() > visible) shown = ".." + shown;
  display.setTextColor(kAccent, kBackground);
  display.println(shown + "_");
  display.setTextColor(kForeground, kBackground);
  display.setTextSize(2);
  display.println("ENTER: CONNECT");
  display.setTextSize(1);
  display.printf("%u/63 Tab:%s Bksp:del Fn+`:back",
                 static_cast<unsigned>(password_.length()),
                 showPassword_ ? "hide" : "show");
}

void WifiSetupPage::drawConnecting() {
  auto& display = *canvas_;
  display.setTextSize(2);
  display.println("Connecting to:");
  display.println(clipped(networks_[selected_].ssid, 19));
  display.setTextColor(kSuccess, kBackground);
  display.println("Logging continues");
  display.setTextSize(2);
  display.setTextColor(kForeground, kBackground);
  display.println("Q: QUIT");
}

void WifiSetupPage::drawResult() {
  auto& display = *canvas_;
  if (scanError_) {
    display.setTextSize(2);
    display.setTextColor(kWarning, kBackground);
    display.println("SCAN ERROR");
    display.setTextSize(1);
    display.println(resultMessage_);
    display.println("2.4 GHz Wi-Fi networks only");
    display.setTextSize(2);
    display.setTextColor(kForeground, kBackground);
    display.drawString("ENTER/Q: QUIT", 2, 91);
    display.drawString("R: START AGAIN", 2, 108);
    return;
  }
  display.setTextSize(2);
  display.setTextColor(WiFi.status() == WL_CONNECTED ? kSuccess : kWarning,
                       kBackground);
  if (WiFi.status() == WL_CONNECTED) display.println("CONNECTED");
  else display.println("NOT CONNECTED");
  if (WiFi.status() == WL_CONNECTED) {
    display.setTextColor(kForeground, kBackground);
    display.println(clipped(WiFi.SSID(), 19));
    display.setTextSize(1);
    const String ip = WiFi.localIP().toString();
    display.printf("IP: %s\n", ip.c_str());
    display.printf("WEB: http://%s/\n", ip.c_str());
    display.println("PORT: 80");
  } else {
    display.setTextColor(kForeground, kBackground);
    display.setTextSize(1);
    display.println(resultMessage_);
  }
  display.setTextColor(kForeground, kBackground);
  display.setTextSize(2);
  display.drawString("ENTER/Q: QUIT", 2, 91);
  display.drawString("R: START AGAIN", 2, 108);
}

void WifiSetupPage::close() {
  stopScan();
  WiFi.scanDelete();
  WiFi.setAutoReconnect(savedAutoReconnect_);
  password_ = "";
  state_ = State::Closed;
  haveFrameHash_ = false;
  dirty_ = false;
}

void WifiSetupPage::markDirty() {
  dirty_ = true;
}
