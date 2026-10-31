#include "WifiSetupPage.h"
#include "LocationTime.h"
#include <M5Cardputer.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <cassert>
#include <cstdio>

void LocationTime::setWifiCredentials(const String&, const String&) {}

namespace {
bool shown(const char* text) {
  for (const auto& line : fakeScreenText) if (line.find(text) != std::string::npos) return true;
  return false;
}
void reset() {
  WiFi = FakeWiFi{};
  M5Cardputer = FakeCardputer{};
  fakeWifiStatus = WL_CONNECTED;
  fakeMillis = 0;
  fakeScanStops = 0;
  fakeScreenText.clear();
}
void update(WifiSetupPage& page, uint32_t advance = 0) {
  fakeMillis += advance;
  page.update(fakeMillis);
  page.draw(fakeMillis);
}
void press(WifiSetupPage& page, char key, LocationTime& location) {
  M5Cardputer.Keyboard.key = key;
  M5Cardputer.Keyboard.changed = true;
  page.handleInput(location);
}

void test_six_networks_reach_the_display() {
  reset();
  WifiSetupPage page;
  M5Canvas canvas;
  page.setCanvas(canvas);
  page.open();
  update(page, 250);
  assert(!WiFi.reconnect && WiFi.scanStarts == 1 && fakeScanStops == 0);
  WiFi.complete(0, 6, {"WiFi-OBD", "Home", "Network3", "Network4", "Network5", "Network6"});
  update(page);
  assert(shown("WiFi-OBD") && shown("Home") && !shown("No named networks"));
}

void test_zero_results_require_driver_event_and_passive_retry() {
  reset();
  WifiSetupPage page;
  M5Canvas canvas;
  page.setCanvas(canvas);
  page.open();
  update(page, 250);
  // Arduino's completion bit can become visible before our event callback.
  WiFi.scanResult = 0;
  update(page);
  assert(!shown("No named networks") && WiFi.scanStarts == 1);
  WiFi.complete(0, 0, {});
  update(page);
  assert(shown("Retrying scan") && !shown("No named networks"));
  update(page, 250);
  assert(WiFi.lastPassive && WiFi.scanStarts == 2);
  WiFi.complete(0, 1, {"WiFi-OBD"});
  update(page);
  assert(shown("WiFi-OBD") && !shown("No named networks"));
}

void test_interrupted_scan_is_an_error_even_while_connected() {
  reset();
  WifiSetupPage page;
  M5Canvas canvas;
  page.setCanvas(canvas);
  page.open();
  update(page, 250);
  WiFi.complete(1, 0, {});
  update(page);
  assert(shown("SCAN ERROR") && shown("interrupted") && !shown("No named networks"));
  LocationTime location;
  press(page, 'r', location);
  update(page, 250);
  WiFi.complete(0, 1, {"Retry worked"});
  update(page);
  assert(shown("Retry worked") && !shown("SCAN ERROR"));
}

void test_wrapper_result_loss_is_not_an_empty_area() {
  reset();
  WifiSetupPage page;
  M5Canvas canvas;
  page.setCanvas(canvas);
  page.open();
  update(page, 250);
  WiFi.complete(0, 6, {}); // Driver found APs but wrapper lost the records.
  update(page);
  assert(shown("SCAN ERROR") && shown("Cannot read scan results"));
  assert(!shown("No named networks"));
}

void test_genuine_empty_scan_and_timeout_and_cancel() {
  reset();
  WifiSetupPage page;
  M5Canvas canvas;
  page.setCanvas(canvas);
  page.open();
  update(page, 250);
  WiFi.complete(0, 0, {});
  update(page);
  update(page, 250);
  WiFi.complete(0, 0, {});
  update(page);
  assert(shown("No named networks") && shown("active + passive"));
  LocationTime location;
  press(page, 'r', location);
  update(page, 250);
  update(page, 15000);
  assert(shown("SCAN ERROR") && shown("timed out"));
  press(page, 'q', location);
  assert(!page.active() && WiFi.reconnect);
}
}

int main() {
  test_six_networks_reach_the_display();
  test_zero_results_require_driver_event_and_passive_retry();
  test_interrupted_scan_is_an_error_even_while_connected();
  test_wrapper_result_loss_is_not_an_empty_area();
  test_genuine_empty_scan_and_timeout_and_cancel();
  std::puts("Wi-Fi setup: populated scans, callback ordering, passive retry, interrupted/lost results, timeout and cancel tests passed");
}
