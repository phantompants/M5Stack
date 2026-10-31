#pragma once
#include "Arduino.h"
#include <functional>
#include <vector>
constexpr int WL_CONNECTED = 3;
constexpr int WIFI_STA = 1;
constexpr int WIFI_SCAN_RUNNING = -1;
constexpr int WIFI_SCAN_FAILED = -2;
constexpr int WIFI_AUTH_OPEN = 0;
enum arduino_event_id_t { ARDUINO_EVENT_WIFI_SCAN_DONE };
struct arduino_event_info_t {
  struct { uint32_t status; uint16_t number; uint8_t scan_id; } wifi_scan_done{};
};
inline int fakeWifiStatus = WL_CONNECTED;
struct FakeWiFi {
  int status() const { return fakeWifiStatus; }
  bool reconnect = true;
  bool lastPassive = false;
  unsigned scanStarts = 0;
  int scanResult = WIFI_SCAN_FAILED;
  std::vector<String> networks;
  std::function<void(arduino_event_id_t, arduino_event_info_t)> callback;
  bool getAutoReconnect() const { return reconnect; }
  void setAutoReconnect(bool value) { reconnect = value; }
  void mode(int) {}
  void disconnect(bool, bool) { fakeWifiStatus = 0; }
  void scanDelete() { scanResult = WIFI_SCAN_FAILED; networks.clear(); }
  int scanNetworks(bool, bool, bool passive, unsigned, int) {
    lastPassive = passive;
    ++scanStarts;
    scanResult = WIFI_SCAN_RUNNING;
    return scanResult;
  }
  int scanComplete() const { return scanResult; }
  void onEvent(std::function<void(arduino_event_id_t, arduino_event_info_t)> cb, arduino_event_id_t) { callback = std::move(cb); }
  String SSID() const { return String("Existing network"); }
  String SSID(unsigned i) const { return networks.at(i); }
  int RSSI(unsigned) const { return -45; }
  int encryptionType(unsigned) const { return WIFI_AUTH_OPEN; }
  struct Ip { String toString() const { return String("192.168.0.20"); } };
  Ip localIP() const { return {}; }
  void complete(uint32_t driverStatus, uint16_t driverCount, std::vector<String> visible) {
    networks = std::move(visible);
    scanResult = int(networks.size());
    if (callback) callback(ARDUINO_EVENT_WIFI_SCAN_DONE, {{driverStatus, driverCount, 1}});
  }
};
inline FakeWiFi WiFi;
