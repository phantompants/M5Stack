// "Cheap Yellow Display" (ESP32-2432S028R) as a JP226 dashboard and
// waypoint remote. It pairs with a logger exactly like the M5Dial: it scans
// channels 1-13 for the logger's discovery beacon, pairs with the chosen
// board type, sends a heartbeat every second and shows the DialStatus the
// logger sends back. Touch buttons save waypoints in four categories.
//
// Screen (portrait, 240x320, USB at the bottom):
//   top bar     logger, link and signal. Tap: change which logger to pair
//               with (CP ZERO, CARDPUTER ADV, CARDPUTER, CORE2, ANY).
//               Hold 1.5 s: turn the screen upside down. Both are saved.
//   tabs        DASH / TILT / TRIPS
//   DASH        speed, fix, log state, road, tyres, place, last waypoint;
//               WAYPOINT / FUEL / CAMP / HAZARD buttons, 2 x 2
//   TILT        pitch and roll from the logger's IMU (position reports,
//               once a second). Hold 1.5 s to zero on level ground.
//   TRIPS       two trip meters (distance, moving time, average and top
//               speed) from the logger's GPS. Hold a trip 1.5 s to reset.
//               Saved, so they survive power off.
// RGB LED: blue blink = searching, green = linked, red flash = rejected.

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include <algorithm>
#include <cmath>
#include <cstring>

#include "CydDisplay.h"
#include "TelemetryProtocol.h"
#include "Version.h"

namespace {

constexpr uint8_t kChannels = 13;
// Longer than the 1 s beacon interval, so a visit to the right channel
// always hears it.
constexpr uint32_t kScanDwellMs = 1100;
constexpr uint32_t kHeartbeatMs = 1000;
constexpr uint32_t kStatusStaleMs = 5000;
constexpr uint32_t kDrawIntervalMs = 250;
constexpr uint32_t kMessageMs = 3000;

constexpr telemetry::LoggerBoard kTargets[] = {
    telemetry::LoggerBoard::CardputerZero, telemetry::LoggerBoard::CardputerAdv,
    telemetry::LoggerBoard::Cardputer, telemetry::LoggerBoard::Core2,
    telemetry::LoggerBoard::Unknown};
constexpr size_t kTargetCount = sizeof(kTargets) / sizeof(kTargets[0]);

struct Button {
  const char* label;
  const char* category;
  uint16_t color;
};
constexpr Button kButtons[] = {
    {"WAYPOINT", "GENERIC", 0x0400},  // dark green
    {"FUEL", "FUEL", 0x8200},         // brown
    {"CAMP", "CAMP", 0x0011},         // navy
    {"HAZARD", "HAZARD", 0x9000},     // dark red
};
constexpr int kButtonCount = sizeof(kButtons) / sizeof(kButtons[0]);
constexpr int kButtonColumns = 2;
constexpr int kButtonRows = 2;
constexpr int kButtonTop = 248;
constexpr int kTopBarHeight = 28;
constexpr int kTabTop = 28;
constexpr int kTabHeight = 26;
constexpr int kBodyTop = kTabTop + kTabHeight;
constexpr uint32_t kHoldMs = 1500;

enum class Page : uint8_t { Dash, Tilt, Trips, Count };
constexpr const char* kPageNames[] = {"DASH", "TILT", "TRIPS"};
constexpr int kPageCount = static_cast<int>(Page::Count);

// Trip meters: GPS points closer together than this in time are joined;
// a jump faster than kMaxStepKmh is GPS noise and is skipped.
constexpr uint32_t kTripMaxGapMs = 5000;
constexpr float kTripMinKmh = 2.0f;
constexpr float kMaxStepKmh = 300.0f;
constexpr uint32_t kTripSaveMs = 30000;
// Tilt colours, degrees.
constexpr float kTiltWarnDeg = 20.0f;
constexpr float kTiltBadDeg = 30.0f;

constexpr const char* kModes[] = {"WAIT FIX", "MOVING", "STOPPED",
                                  "STOPPED HR", "FIX LOST"};

cyd::Display display;
lgfx::LGFX_Sprite canvas(&display);
bool canvasReady = false;
Preferences preferences;

// Read by the receive callback; a single byte, so no lock is needed.
volatile uint8_t targetBoard =
    static_cast<uint8_t>(telemetry::LoggerBoard::CardputerZero);

portMUX_TYPE receiveMux = portMUX_INITIALIZER_UNLOCKED;
telemetry::DialStatusPacket latestStatus{};
telemetry::DialStatusPacket receivedStatus{};
telemetry::DialAckPacket receivedAck{};
int8_t receivedRssi = 0;
int8_t latestRssi = 0;
uint8_t candidateMac[6]{};
uint8_t candidateChannel = 0;
bool candidateReady = false;
bool statusReady = false;
bool ackReady = false;
bool haveStatus = false;
bool paired = false;
uint8_t peerMac[6]{};
uint8_t channel = 1;
uint32_t lastScanMs = 0;
uint32_t lastStatusMs = 0;
uint32_t lastHeartbeatMs = 0;
uint32_t nextSequence = 1;
uint32_t pendingSequence = 0;
uint32_t pendingSinceMs = 0;
uint32_t lastPendingSendMs = 0;
uint8_t pendingAttempts = 0;
telemetry::DialCommandPacket pendingPacket{};
char message[40] = "";
uint16_t messageColor = TFT_WHITE;
uint32_t messageSinceMs = 0;
uint32_t rejectFlashUntilMs = 0;
bool touchWasDown = false;
uint32_t touchDownMs = 0;
int32_t touchDownX = 0;
int32_t touchDownY = 0;
uint8_t rotation = 0;  // 0 = USB at the bottom, 2 = upside down
Page page = Page::Dash;
bool holdDone = false;

telemetry::PositionReportPacket receivedPosition{};
bool positionReady = false;
uint32_t lastPositionMs = 0;
bool havePosition = false;

// Tilt: smoothed pitch/roll, and the "level" offsets saved by a hold.
bool tiltValid = false;
float pitchDeg = 0.0f;
float rollDeg = 0.0f;
float pitchZero = 0.0f;
float rollZero = 0.0f;

struct Trip {
  double meters = 0.0;
  uint32_t movingS = 0;
  float maxKmh = 0.0f;
};
Trip trips[2];
bool tripsDirty = false;
uint32_t lastTripSaveMs = 0;
bool haveLastPoint = false;
double lastLat = 0.0;
double lastLon = 0.0;
uint32_t lastPointMs = 0;
// Serial diagnostics: every JP226 beacon heard, and the last one's board.
volatile uint32_t beaconsHeard = 0;
volatile uint8_t lastBeaconBoard = 0xFF;
volatile uint8_t lastBeaconChannel = 0;

const char* loggerName(uint8_t board) {
  switch (static_cast<telemetry::LoggerBoard>(board)) {
    case telemetry::LoggerBoard::Cardputer: return "CARDPUTER";
    case telemetry::LoggerBoard::CardputerAdv: return "CARDPUTER ADV";
    case telemetry::LoggerBoard::Core2: return "CORE2";
    case telemetry::LoggerBoard::CardputerZero: return "CP ZERO";
    default: return "LOGGER";
  }
}

const char* targetName() {
  return targetBoard == static_cast<uint8_t>(telemetry::LoggerBoard::Unknown)
             ? "ANY LOGGER"
             : loggerName(targetBoard);
}

void showMessage(const char* text, uint16_t color = TFT_WHITE) {
  std::snprintf(message, sizeof(message), "%s", text);
  messageColor = color;
  messageSinceMs = millis();
}

void onReceive(const esp_now_recv_info_t* info, const uint8_t* bytes,
               int length) {
  if (info == nullptr || info->src_addr == nullptr || bytes == nullptr ||
      length < static_cast<int>(sizeof(telemetry::PacketHeader))) return;
  telemetry::PacketHeader header{};
  std::memcpy(&header, bytes, sizeof(header));
  if (header.magic != telemetry::kMagic ||
      header.version != telemetry::kProtocolVersion ||
      header.size != length) return;
  portENTER_CRITICAL(&receiveMux);
  const bool currentlyPaired = paired;
  const bool samePeer =
      currentlyPaired && std::memcmp(peerMac, info->src_addr, 6) == 0;
  portEXIT_CRITICAL(&receiveMux);
  const auto type = static_cast<telemetry::PacketType>(header.type);
  if (type == telemetry::PacketType::Discovery &&
      length == sizeof(telemetry::DiscoveryPacket)) {
    telemetry::DiscoveryPacket discovery{};
    std::memcpy(&discovery, bytes, sizeof(discovery));
    if (telemetry::validatePacket(discovery, type)) {
      ++beaconsHeard;
      lastBeaconBoard = static_cast<uint8_t>(
          telemetry::beaconBoard(discovery.time_flags));
      lastBeaconChannel =
          info->rx_ctrl != nullptr ? info->rx_ctrl->channel : 0;
    }
    if (!telemetry::validatePacket(discovery, type) || currentlyPaired ||
        !telemetry::boardMatches(
            static_cast<telemetry::LoggerBoard>(targetBoard),
            telemetry::beaconBoard(discovery.time_flags))) {
      return;
    }
    portENTER_CRITICAL(&receiveMux);
    std::memcpy(candidateMac, info->src_addr, 6);
    candidateChannel =
        info->rx_ctrl != nullptr ? info->rx_ctrl->channel : channel;
    candidateReady = true;
    portEXIT_CRITICAL(&receiveMux);
  } else if (samePeer && type == telemetry::PacketType::DialStatus &&
             length == sizeof(telemetry::DialStatusPacket)) {
    telemetry::DialStatusPacket status{};
    std::memcpy(&status, bytes, sizeof(status));
    if (!telemetry::validatePacket(status, type)) return;
    portENTER_CRITICAL(&receiveMux);
    receivedStatus = status;
    receivedRssi = info->rx_ctrl != nullptr
                       ? static_cast<int8_t>(info->rx_ctrl->rssi)
                       : 0;
    statusReady = true;
    portEXIT_CRITICAL(&receiveMux);
  } else if (samePeer && type == telemetry::PacketType::PositionReport &&
             length == sizeof(telemetry::PositionReportPacket)) {
    telemetry::PositionReportPacket report{};
    std::memcpy(&report, bytes, sizeof(report));
    if (!telemetry::validatePacket(report, type)) return;
    portENTER_CRITICAL(&receiveMux);
    receivedPosition = report;
    positionReady = true;
    portEXIT_CRITICAL(&receiveMux);
  } else if (samePeer && type == telemetry::PacketType::DialAck &&
             length == sizeof(telemetry::DialAckPacket)) {
    telemetry::DialAckPacket ack{};
    std::memcpy(&ack, bytes, sizeof(ack));
    if (!telemetry::validatePacket(ack, type)) return;
    portENTER_CRITICAL(&receiveMux);
    receivedAck = ack;
    ackReady = true;
    portEXIT_CRITICAL(&receiveMux);
  }
}

void sendCommand(telemetry::DialAction action, const char* text = "") {
  const bool heartbeat = action == telemetry::DialAction::Heartbeat;
  if (!heartbeat && pendingSequence != 0) {
    showMessage("WAIT FOR CONFIRMATION", TFT_YELLOW);
    return;
  }
  if (!paired) {
    if (!heartbeat) showMessage("NO LOGGER LINK", TFT_RED);
    return;
  }
  telemetry::DialCommandPacket packet{};
  telemetry::preparePacket(packet, telemetry::PacketType::DialCommand);
  packet.sequence = nextSequence++;
  packet.action = static_cast<uint8_t>(action);
  packet.sender = static_cast<uint8_t>(telemetry::ControllerKind::Cyd);
  std::snprintf(packet.text, sizeof(packet.text), "%s", text);
  telemetry::sealPacket(packet);
  if (esp_now_send(peerMac, reinterpret_cast<const uint8_t*>(&packet),
                   sizeof(packet)) != ESP_OK) {
    if (!heartbeat) showMessage("SEND FAILED", TFT_RED);
    return;
  }
  if (!heartbeat) {
    pendingPacket = packet;
    pendingSequence = packet.sequence;
    pendingSinceMs = millis();
    lastPendingSendMs = pendingSinceMs;
    pendingAttempts = 1;
    showMessage("WAITING FOR ACK", TFT_YELLOW);
  }
}

void unpair() {
  if (paired) esp_now_del_peer(peerMac);
  portENTER_CRITICAL(&receiveMux);
  paired = false;
  portEXIT_CRITICAL(&receiveMux);
  haveStatus = false;
  pendingSequence = 0;
}

void connectCandidate() {
  uint8_t mac[6]{};
  uint8_t foundChannel = 0;
  portENTER_CRITICAL(&receiveMux);
  if (candidateReady) {
    std::memcpy(mac, candidateMac, 6);
    foundChannel = candidateChannel;
    candidateReady = false;
  }
  portEXIT_CRITICAL(&receiveMux);
  if (foundChannel == 0 || paired) return;
  if (esp_wifi_set_channel(foundChannel, WIFI_SECOND_CHAN_NONE) != ESP_OK)
    return;
  esp_now_peer_info_t peer{};
  std::memcpy(peer.peer_addr, mac, 6);
  peer.channel = 0;
  peer.ifidx = WIFI_IF_STA;
  if (esp_now_add_peer(&peer) != ESP_OK) return;
  portENTER_CRITICAL(&receiveMux);
  std::memcpy(peerMac, mac, 6);
  channel = foundChannel;
  paired = true;
  portEXIT_CRITICAL(&receiveMux);
  lastStatusMs = millis();
  lastHeartbeatMs = 0;
  showMessage("LOGGER FOUND", TFT_GREEN);
}

double distanceMeters(double lat1, double lon1, double lat2, double lon2) {
  constexpr double kEarthRadiusM = 6371000.0;
  constexpr double kRad = M_PI / 180.0;
  const double dLat = (lat2 - lat1) * kRad;
  const double dLon = (lon2 - lon1) * kRad;
  const double a = std::sin(dLat / 2) * std::sin(dLat / 2) +
                   std::cos(lat1 * kRad) * std::cos(lat2 * kRad) *
                       std::sin(dLon / 2) * std::sin(dLon / 2);
  return 2.0 * kEarthRadiusM * std::atan2(std::sqrt(a), std::sqrt(1.0 - a));
}

float savedFloat(const char* key) {
  return preferences.isKey(key) ? preferences.getFloat(key, 0.0f) : 0.0f;
}

void loadTrips() {
  for (int i = 0; i < 2; ++i) {
    char key[12];
    std::snprintf(key, sizeof(key), "trip%d_m", i);
    trips[i].meters =
        preferences.isKey(key) ? preferences.getDouble(key, 0.0) : 0.0;
    std::snprintf(key, sizeof(key), "trip%d_s", i);
    trips[i].movingS =
        preferences.isKey(key) ? preferences.getULong(key, 0) : 0;
    std::snprintf(key, sizeof(key), "trip%d_max", i);
    trips[i].maxKmh = savedFloat(key);
  }
  pitchZero = savedFloat("pitch0");
  rollZero = savedFloat("roll0");
}

void saveTrips(uint32_t nowMs) {
  lastTripSaveMs = nowMs;
  tripsDirty = false;
  for (int i = 0; i < 2; ++i) {
    char key[12];
    std::snprintf(key, sizeof(key), "trip%d_m", i);
    preferences.putDouble(key, trips[i].meters);
    std::snprintf(key, sizeof(key), "trip%d_s", i);
    preferences.putULong(key, trips[i].movingS);
    std::snprintf(key, sizeof(key), "trip%d_max", i);
    preferences.putFloat(key, trips[i].maxKmh);
  }
}

void resetTrip(int index, uint32_t nowMs) {
  trips[index] = Trip{};
  saveTrips(nowMs);
  showMessage(index == 0 ? "TRIP A RESET" : "TRIP B RESET", TFT_CYAN);
}

// One position report: trip distance and time, and the tilt reading.
void usePosition(const telemetry::PositionReportPacket& report,
                 uint32_t nowMs) {
  havePosition = true;
  lastPositionMs = nowMs;
  if (report.flags & telemetry::PositionImuValid) {
    // Light smoothing: the logger sends one reading a second.
    if (!tiltValid) {
      pitchDeg = report.pitch_deg;
      rollDeg = report.roll_deg;
    } else {
      pitchDeg = 0.5f * pitchDeg + 0.5f * report.pitch_deg;
      rollDeg = 0.5f * rollDeg + 0.5f * report.roll_deg;
    }
    tiltValid = std::isfinite(pitchDeg) && std::isfinite(rollDeg);
  } else {
    tiltValid = false;
  }
  const bool usable = (report.flags & telemetry::PositionFix) &&
                      (report.flags & telemetry::PositionValid);
  if (!usable) {
    haveLastPoint = false;
    return;
  }
  const float kmh = std::isfinite(report.speed_kmh) ? report.speed_kmh : 0.0f;
  if (haveLastPoint && nowMs - lastPointMs <= kTripMaxGapMs) {
    const uint32_t stepMs = nowMs - lastPointMs;
    const double meters = distanceMeters(lastLat, lastLon,
                                         report.latitude_deg,
                                         report.longitude_deg);
    const double stepKmh = stepMs > 0 ? meters / stepMs * 3600.0 : 0.0;
    if (kmh >= kTripMinKmh && stepKmh <= kMaxStepKmh) {
      for (auto& trip : trips) {
        trip.meters += meters;
        trip.movingS += (stepMs + 500) / 1000;
        trip.maxKmh = std::max(trip.maxKmh, kmh);
      }
      tripsDirty = true;
    }
  }
  haveLastPoint = true;
  lastLat = report.latitude_deg;
  lastLon = report.longitude_deg;
  lastPointMs = nowMs;
}

void updateRadio(uint32_t nowMs) {
  if (paired && nowMs - lastStatusMs > kStatusStaleMs) {
    unpair();
    showMessage("LINK LOST", TFT_RED);
  }
  connectCandidate();
  if (!paired && nowMs - lastScanMs >= kScanDwellMs) {
    lastScanMs = nowMs;
    const uint8_t nextChannel = channel >= kChannels ? 1 : channel + 1;
    if (esp_wifi_set_channel(nextChannel, WIFI_SECOND_CHAN_NONE) == ESP_OK) {
      portENTER_CRITICAL(&receiveMux);
      channel = nextChannel;
      portEXIT_CRITICAL(&receiveMux);
    }
  }
  if (paired && nowMs - lastHeartbeatMs >= kHeartbeatMs) {
    lastHeartbeatMs = nowMs;
    sendCommand(telemetry::DialAction::Heartbeat);
  }
  telemetry::DialStatusPacket status{};
  telemetry::DialAckPacket ack{};
  telemetry::PositionReportPacket position{};
  bool gotStatus = false;
  bool gotAck = false;
  bool gotPosition = false;
  portENTER_CRITICAL(&receiveMux);
  if (positionReady) {
    position = receivedPosition;
    positionReady = false;
    gotPosition = true;
  }
  if (statusReady) {
    status = receivedStatus;
    latestRssi = receivedRssi;
    statusReady = false;
    gotStatus = true;
  }
  if (ackReady) {
    ack = receivedAck;
    ackReady = false;
    gotAck = true;
  }
  portEXIT_CRITICAL(&receiveMux);
  if (gotPosition) usePosition(position, nowMs);
  if (tripsDirty && nowMs - lastTripSaveMs >= kTripSaveMs) saveTrips(nowMs);
  if (havePosition && nowMs - lastPositionMs > kStatusStaleMs) {
    havePosition = false;
    tiltValid = false;
    haveLastPoint = false;
  }
  if (gotStatus) {
    latestStatus = status;
    haveStatus = true;
    lastStatusMs = nowMs;
  }
  if (gotAck && ack.command_sequence == pendingSequence) {
    pendingSequence = 0;
    if (ack.accepted) {
      char text[40];
      std::snprintf(text, sizeof(text), "SAVED %.24s",
                    ack.waypoint_id[0] ? ack.waypoint_id : "");
      showMessage(text, TFT_GREEN);
    } else {
      showMessage("REJECTED: CHECK FIX", TFT_RED);
      rejectFlashUntilMs = nowMs + 600;
    }
  }
  if (pendingSequence != 0 && nowMs - lastPendingSendMs >= 1000 &&
      pendingAttempts < 3) {
    esp_now_send(peerMac, reinterpret_cast<const uint8_t*>(&pendingPacket),
                 sizeof(pendingPacket));
    lastPendingSendMs = nowMs;
    ++pendingAttempts;
  }
  if (pendingSequence != 0 && nowMs - pendingSinceMs > 3500) {
    pendingSequence = 0;
    showMessage("NO CONFIRMATION", TFT_RED);
  }
}

void cycleTarget() {
  size_t index = 0;
  while (index < kTargetCount &&
         static_cast<uint8_t>(kTargets[index]) != targetBoard) {
    ++index;
  }
  targetBoard = static_cast<uint8_t>(kTargets[(index + 1) % kTargetCount]);
  preferences.putUChar("target", targetBoard);
  unpair();
  char text[40];
  std::snprintf(text, sizeof(text), "PAIRS WITH %s", targetName());
  showMessage(text, TFT_CYAN);
}

// Changes with every build, so each newly flashed firmware calibrates the
// touchscreen once on its first start.
constexpr char kBuildId[] = __DATE__ " " __TIME__;

// LovyanGFX's four-corner calibration: tap the arrow in each corner. The
// result is saved with the build and the screen orientation it belongs to.
void calibrateTouch() {
  display.fillScreen(TFT_BLACK);
  display.setTextDatum(textdatum_t::middle_center);
  display.setFont(&fonts::FreeSansBold12pt7b);
  display.setTextColor(TFT_WHITE);
  display.drawString("TOUCH SETUP", display.width() / 2,
                     display.height() / 2 - 24);
  display.setFont(&fonts::FreeSans9pt7b);
  display.setTextColor(TFT_LIGHTGREY);
  display.drawString("Tap the tip of each arrow", display.width() / 2,
                     display.height() / 2 + 6);
  display.drawString("as it appears", display.width() / 2,
                     display.height() / 2 + 26);
  uint16_t calibration[8];
  display.calibrateTouch(calibration, TFT_WHITE, TFT_BLACK, 20);
  preferences.putBytes("cal", calibration, sizeof(calibration));
  preferences.putString("cal_build", kBuildId);
  preferences.putUChar("cal_rot", rotation);
  touchWasDown = true;  // ignore the finger still on the screen
  holdDone = true;
  Serial.println("Touch calibrated");
  showMessage("TOUCH CALIBRATED", TFT_GREEN);
}

// Saved calibration if it belongs to this build and orientation, else ask.
void loadOrCalibrateTouch() {
  uint16_t calibration[8];
  const bool saved =
      preferences.isKey("cal") &&
      preferences.getBytes("cal", calibration, sizeof(calibration)) ==
          sizeof(calibration) &&
      preferences.isKey("cal_build") &&
      preferences.getString("cal_build", "") == kBuildId &&
      preferences.getUChar("cal_rot", 255) == rotation;
  if (saved) {
    display.setTouchCalibrate(calibration);
  } else {
    calibrateTouch();
  }
}

void flipScreen() {
  rotation = rotation == 0 ? 2 : 0;
  preferences.putUChar("rot", rotation);
  display.setRotation(rotation);
  showMessage("SCREEN TURNED", TFT_CYAN);
}

// Which trip card a body y position is on (TRIPS page), or -1.
int tripAt(int32_t y) {
  const int cardHeight = (display.height() - kBodyTop) / 2;
  const int index = (static_cast<int>(y) - kBodyTop) / cardHeight;
  return y >= kBodyTop && index >= 0 && index < 2 ? index : -1;
}

void holdAction(uint32_t nowMs) {
  if (touchDownY < kTopBarHeight) {
    flipScreen();
    calibrateTouch();  // touch mapping follows the screen orientation
  } else if (touchDownY < kBodyTop) {
    calibrateTouch();
  } else if (touchDownY >= kBodyTop && page == Page::Tilt) {
    if (tiltValid) {
      pitchZero = pitchDeg;
      rollZero = rollDeg;
      preferences.putFloat("pitch0", pitchZero);
      preferences.putFloat("roll0", rollZero);
      showMessage("LEVEL SET", TFT_GREEN);
    } else {
      showMessage("NO TILT DATA", TFT_RED);
    }
  } else if (page == Page::Trips && tripAt(touchDownY) >= 0) {
    resetTrip(tripAt(touchDownY), nowMs);
  }
}

// Taps act on release; holds act once the finger has been down 1.5 s.
void handleTouch(uint32_t nowMs) {
  int32_t x = 0;
  int32_t y = 0;
  const bool down = display.getTouch(&x, &y);
  if (down && !touchWasDown) {
    touchDownMs = nowMs;
    touchDownX = x;
    touchDownY = y;
    holdDone = false;
  }
  if (down && !holdDone && nowMs - touchDownMs >= kHoldMs) {
    holdDone = true;
    holdAction(nowMs);
  }
  const bool released = !down && touchWasDown;
  touchWasDown = down;
  if (!released || holdDone) return;
  if (touchDownY < kTopBarHeight) {
    cycleTarget();
    return;
  }
  if (touchDownY < kBodyTop) {
    const int index = static_cast<int>(touchDownX) * kPageCount /
                      display.width();
    if (index >= 0 && index < kPageCount) {
      page = static_cast<Page>(index);
      preferences.putUChar("page", index);
    }
    return;
  }
  if (page == Page::Dash && touchDownY >= kButtonTop) {
    const int column = static_cast<int>(touchDownX) * kButtonColumns /
                       display.width();
    const int row = static_cast<int>(touchDownY - kButtonTop) * kButtonRows /
                    (display.height() - kButtonTop);
    const int index = row * kButtonColumns + column;
    if (column >= 0 && column < kButtonColumns && row >= 0 &&
        row < kButtonRows && index < kButtonCount) {
      sendCommand(telemetry::DialAction::MarkWaypoint,
                  kButtons[index].category);
    }
  }
}

void setLed(uint32_t nowMs) {
  bool red = false, green = false, blue = false;
  if (static_cast<int32_t>(rejectFlashUntilMs - nowMs) > 0) {
    red = true;
  } else if (paired) {
    green = true;
  } else {
    blue = (nowMs / 500) % 2 == 0;
  }
  // Active low.
  digitalWrite(cyd::kRedLedPin, red ? LOW : HIGH);
  digitalWrite(cyd::kGreenLedPin, green ? LOW : HIGH);
  digitalWrite(cyd::kBlueLedPin, blue ? LOW : HIGH);
}

uint16_t tiltColor(float degrees) {
  const float a = std::fabs(degrees);
  return a >= kTiltBadDeg ? TFT_RED : a >= kTiltWarnDeg ? TFT_ORANGE
                                                        : TFT_GREEN;
}

// A half-dial: -45 to +45 degrees across the top half of a circle.
void drawTiltGauge(lgfx::LovyanGFX& g, int cx, int cy, int r, float degrees,
                   const char* label, const char* hint) {
  g.fillArc(cx, cy, r, r - 10, 180, 360, 0x2104);
  // Green, orange and red bands, both sides.
  const float bands[][3] = {{0, kTiltWarnDeg, 0}, {kTiltWarnDeg, kTiltBadDeg, 1},
                            {kTiltBadDeg, 45, 2}};
  const uint16_t colors[] = {0x0320, 0x7A00, 0x7800};
  for (const auto& band : bands) {
    const uint16_t c = colors[static_cast<int>(band[2])];
    g.fillArc(cx, cy, r, r - 10, 270 + band[0] * 2, 270 + band[1] * 2, c);
    g.fillArc(cx, cy, r, r - 10, 270 - band[1] * 2, 270 - band[0] * 2, c);
  }
  const float shown = std::max(-45.0f, std::min(45.0f, degrees));
  const float angle = (270.0f + shown * 2.0f) * static_cast<float>(M_PI) / 180.0f;
  const int nx = cx + static_cast<int>(std::cos(angle) * (r - 4));
  const int ny = cy + static_cast<int>(std::sin(angle) * (r - 4));
  g.drawWideLine(cx, cy, nx, ny, 3, TFT_WHITE);
  g.fillCircle(cx, cy, 5, TFT_WHITE);
  char text[16];
  std::snprintf(text, sizeof(text), "%+.0f", degrees);
  g.setTextDatum(textdatum_t::bottom_center);
  g.setFont(&fonts::FreeSansBold18pt7b);
  g.setTextColor(tiltColor(degrees));
  g.drawString(text, cx, cy - 18);
  g.setTextDatum(textdatum_t::top_center);
  g.setFont(&fonts::FreeSansBold9pt7b);
  g.setTextColor(TFT_WHITE);
  g.drawString(label, cx, cy + 6);
  g.setFont(&fonts::Font0);
  g.setTextColor(TFT_DARKGREY);
  g.drawString(hint, cx, cy + 26);
}

void drawTilt(lgfx::LovyanGFX& g) {
  const int w = g.width();
  if (!tiltValid) {
    g.setTextDatum(textdatum_t::middle_center);
    g.setFont(&fonts::FreeSansBold12pt7b);
    g.setTextColor(TFT_WHITE);
    g.drawString("NO TILT DATA", w / 2, 150);
    g.setFont(&fonts::FreeSans9pt7b);
    g.setTextColor(TFT_DARKGREY);
    g.drawString("The logger sends pitch and", w / 2, 186);
    g.drawString("roll when its IMU is working", w / 2, 206);
    return;
  }
  drawTiltGauge(g, w / 2, kBodyTop + 112, 88, pitchDeg - pitchZero, "PITCH",
                "nose up +");
  drawTiltGauge(g, w / 2, kBodyTop + 236, 88, rollDeg - rollZero, "ROLL",
                "right side down +");
  g.setTextDatum(textdatum_t::bottom_center);
  g.setFont(&fonts::Font0);
  g.setTextColor(TFT_DARKGREY);
  g.drawString("Hold on level ground to zero", w / 2, g.height() - 2);
}

void drawTripCard(lgfx::LovyanGFX& g, int index, int top, int height) {
  const int w = g.width();
  const Trip& trip = trips[index];
  g.drawRoundRect(4, top + 3, w - 8, height - 6, 8, 0x4208);
  g.setTextDatum(textdatum_t::top_left);
  g.setFont(&fonts::FreeSansBold9pt7b);
  g.setTextColor(index == 0 ? TFT_CYAN : TFT_YELLOW);
  g.drawString(index == 0 ? "TRIP A" : "TRIP B", 14, top + 12);
  char text[32];
  std::snprintf(text, sizeof(text), "%.1f", trip.meters / 1000.0);
  g.setTextDatum(textdatum_t::top_right);
  g.setFont(&fonts::FreeSansBold24pt7b);
  g.setTextColor(TFT_WHITE);
  g.drawString(text, w - 50, top + 30);
  g.setFont(&fonts::FreeSansBold9pt7b);
  g.setTextColor(TFT_LIGHTGREY);
  g.drawString("km", w - 14, top + 50);
  const uint32_t hours = trip.movingS / 3600;
  const uint32_t minutes = (trip.movingS / 60) % 60;
  const float average =
      trip.movingS > 0 ? trip.meters / 1000.0 / (trip.movingS / 3600.0) : 0.0;
  g.setFont(&fonts::FreeSans9pt7b);
  g.setTextDatum(textdatum_t::top_left);
  std::snprintf(text, sizeof(text), "%lu:%02lu moving",
                static_cast<unsigned long>(hours),
                static_cast<unsigned long>(minutes));
  g.drawString(text, 14, top + 82);
  std::snprintf(text, sizeof(text), "AVG %.0f  MAX %.0f km/h", average,
                trip.maxKmh);
  g.drawString(text, 14, top + 102);
  g.setTextDatum(textdatum_t::top_right);
  g.setFont(&fonts::Font0);
  g.setTextColor(TFT_DARKGREY);
  g.drawString("hold to reset", w - 14, top + 14);
}

void drawTrips(lgfx::LovyanGFX& g) {
  const int cardHeight = (g.height() - kBodyTop) / 2;
  drawTripCard(g, 0, kBodyTop, cardHeight);
  drawTripCard(g, 1, kBodyTop + cardHeight, cardHeight);
  if (!havePosition) {
    g.setTextDatum(textdatum_t::bottom_center);
    g.setFont(&fonts::Font0);
    g.setTextColor(TFT_ORANGE);
    g.drawString("no GPS from the logger: not counting", g.width() / 2,
                 g.height() - 4);
  }
}

void drawDash(lgfx::LovyanGFX& g, uint32_t nowMs) {
  const int w = g.width();
  const int h = g.height();
  char line[48];
  if (paired && haveStatus) {
    const auto& s = latestStatus;
    g.setTextDatum(textdatum_t::top_center);
    g.setFont(&fonts::Font7);  // 48-px seven-segment digits
    g.setTextColor(s.fix_valid ? TFT_WHITE : TFT_DARKGREY);
    std::snprintf(line, sizeof(line), "%.0f",
                  std::isfinite(s.speed_kmh) ? s.speed_kmh : 0.0f);
    g.drawString(line, w / 2, kBodyTop + 6);
    g.setFont(&fonts::FreeSans9pt7b);
    g.setTextColor(TFT_LIGHTGREY);
    g.drawString("km/h", w / 2, kBodyTop + 56);

    g.setTextDatum(textdatum_t::top_left);
    g.setFont(&fonts::FreeSansBold9pt7b);
    g.setTextColor(s.fix_valid ? TFT_GREEN : TFT_RED);
    std::snprintf(line, sizeof(line), "%s  %u SATS",
                  s.fix_valid ? "GPS FIX" : "NO FIX", s.satellites);
    g.drawString(line, 8, kBodyTop + 78);
    g.setTextColor(TFT_YELLOW);
    g.drawString(s.log_mode < 5 ? kModes[s.log_mode] : "?", 8, kBodyTop + 96);
    if (s.battery_percent >= 0) {
      std::snprintf(line, sizeof(line), "BATT %d%%", s.battery_percent);
      g.setTextDatum(textdatum_t::top_right);
      g.setTextColor(s.battery_percent < 20 ? TFT_RED : TFT_LIGHTGREY);
      g.drawString(line, w - 8, kBodyTop + 96);
      g.setTextDatum(textdatum_t::top_left);
    }
    g.setFont(&fonts::FreeSans9pt7b);
    g.setTextColor(TFT_WHITE);
    const char* surface =
        s.road_surface > 0 && s.road_surface < telemetry::kRoadSurfaceCount
            ? telemetry::kRoadSurfaces[s.road_surface]
            : "";
    std::snprintf(line, sizeof(line), "%.15s  %s   %.0f/%.0f PSI", s.road,
                  surface, s.front_psi, s.rear_psi);
    g.drawString(line, 8, kBodyTop + 116);
    g.setTextColor(TFT_CYAN);
    std::snprintf(line, sizeof(line), "%.24s", s.place[0] ? s.place : "-");
    g.drawString(line, 8, kBodyTop + 135);
    g.setTextColor(TFT_LIGHTGREY);
    std::snprintf(line, sizeof(line), "LAST %.20s",
                  s.last_waypoint[0] ? s.last_waypoint : "-");
    g.drawString(line, 8, kBodyTop + 154);
  } else {
    g.setTextDatum(textdatum_t::middle_center);
    g.setFont(&fonts::FreeSansBold12pt7b);
    g.setTextColor(TFT_WHITE);
    g.drawString(paired ? "LINKED" : "SEARCHING", w / 2, kBodyTop + 40);
    g.setFont(&fonts::FreeSans9pt7b);
    g.setTextColor(TFT_LIGHTGREY);
    g.drawString(paired ? "waiting for status" : "for the logger", w / 2,
                 kBodyTop + 66);
    g.setTextColor(TFT_DARKGREY);
    g.drawString("Tap the top bar to change", w / 2, kBodyTop + 106);
    g.drawString("which logger to pair with", w / 2, kBodyTop + 126);
    std::snprintf(line, sizeof(line), "JP226 CYD %s", version::kNumber);
    g.drawString(line, w / 2, kBodyTop + 160);
  }
  // Buttons, 2 x 2.
  const int buttonWidth = w / kButtonColumns;
  const int buttonHeight = (h - kButtonTop) / kButtonRows;
  g.setFont(&fonts::FreeSansBold9pt7b);
  g.setTextDatum(textdatum_t::middle_center);
  for (int i = 0; i < kButtonCount; ++i) {
    const int bx = (i % kButtonColumns) * buttonWidth;
    const int by = kButtonTop + (i / kButtonColumns) * buttonHeight;
    g.fillRoundRect(bx + 3, by + 2, buttonWidth - 6, buttonHeight - 4, 8,
                    paired ? kButtons[i].color : 0x2104);
    g.setTextColor(paired ? TFT_WHITE : TFT_DARKGREY);
    g.drawString(kButtons[i].label, bx + buttonWidth / 2,
                 by + buttonHeight / 2);
  }
  (void)nowMs;
}

void drawScreen(uint32_t nowMs) {
  lgfx::LovyanGFX& g = canvasReady ? static_cast<lgfx::LovyanGFX&>(canvas)
                                   : static_cast<lgfx::LovyanGFX&>(display);
  const int w = g.width();
  g.fillScreen(TFT_BLACK);
  char line[48];

  // Top bar: logger and link.
  g.fillRect(0, 0, w, kTopBarHeight, 0x18E3);
  g.setFont(&fonts::FreeSansBold9pt7b);
  g.setTextDatum(textdatum_t::middle_left);
  g.setTextColor(TFT_WHITE);
  if (paired && haveStatus) {
    g.drawString(loggerName(latestStatus.logger_board), 6, 14);
    std::snprintf(line, sizeof(line), "%d", latestRssi);
    g.setTextDatum(textdatum_t::middle_right);
    g.setTextColor(TFT_GREEN);
    g.drawString(line, w - 6, 14);
  } else {
    std::snprintf(line, sizeof(line), "FIND %s", targetName());
    g.drawString(line, 6, 14);
    g.setTextDatum(textdatum_t::middle_right);
    g.setTextColor(TFT_DARKGREY);
    std::snprintf(line, sizeof(line), "CH%u", channel);
    g.drawString(line, w - 6, 14);
  }

  // Tabs.
  const int tabWidth = w / kPageCount;
  g.setTextDatum(textdatum_t::middle_center);
  for (int i = 0; i < kPageCount; ++i) {
    const bool selected = static_cast<int>(page) == i;
    g.fillRect(i * tabWidth, kTabTop, tabWidth - 1, kTabHeight - 2,
               selected ? 0x2B5F : 0x10A2);
    g.setTextColor(selected ? TFT_WHITE : TFT_DARKGREY);
    g.drawString(kPageNames[i], i * tabWidth + tabWidth / 2,
                 kTabTop + kTabHeight / 2 - 1);
  }

  switch (page) {
    case Page::Tilt: drawTilt(g); break;
    case Page::Trips: drawTrips(g); break;
    default: drawDash(g, nowMs); break;
  }

  // Message line.
  if (message[0] && nowMs - messageSinceMs < kMessageMs) {
    const int y = page == Page::Dash ? kButtonTop - 8 : kBodyTop + 12;
    g.setTextDatum(textdatum_t::middle_center);
    g.setFont(&fonts::FreeSansBold9pt7b);
    g.fillRect(0, y - 10, w, 20, TFT_BLACK);
    g.setTextColor(messageColor);
    g.drawString(message, w / 2, y);
  }
  if (canvasReady) canvas.pushSprite(0, 0);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  for (const int pin : {cyd::kRedLedPin, cyd::kGreenLedPin, cyd::kBlueLedPin}) {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, HIGH);
  }
  const uint32_t panelId = display.begin();
  Serial.printf("Display RDDID 0x%08lX, 0xD3 0x%08lX, driver %s\n",
                static_cast<unsigned long>(panelId),
                static_cast<unsigned long>(display.id4()),
                display.isSt7789() ? "ST7789" : "ILI9341");
  preferences.begin("cyd", false);
  rotation = preferences.getUChar("rot", 0) == 2 ? 2 : 0;
  display.setRotation(rotation);  // portrait
  display.setBrightness(200);
  loadOrCalibrateTouch();
  canvas.setColorDepth(8);
  canvasReady = canvas.createSprite(display.width(), display.height()) != nullptr;

  page = static_cast<Page>(preferences.getUChar("page", 0) % kPageCount);
  loadTrips();
  targetBoard = preferences.getUChar(
      "target", static_cast<uint8_t>(telemetry::LoggerBoard::CardputerZero));

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  nextSequence = esp_random();
  if (nextSequence == 0) nextSequence = 1;
  esp_wifi_set_channel(channel, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK ||
      esp_now_register_recv_cb(onReceive) != ESP_OK) {
    Serial.println("ERROR: ESP-NOW init failed");
    showMessage("ESP-NOW FAILED", TFT_RED);
  }
  uint8_t mac[6]{};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  Serial.printf("JP226 CYD %02X:%02X:%02X:%02X:%02X:%02X, firmware %s %s %s, "
                "pairs with board %u\n",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
                version::kNumber, version::kGit, version::kDate, targetBoard);
}

void loop() {
  const uint32_t nowMs = millis();
  updateRadio(nowMs);
  handleTouch(nowMs);
  setLed(nowMs);
  static uint32_t lastDrawMs = 0;
  static bool wasPaired = false;
  if (nowMs - lastDrawMs >= kDrawIntervalMs || paired != wasPaired) {
    lastDrawMs = nowMs;
    wasPaired = paired;
    drawScreen(nowMs);
  }
  static uint32_t lastReportMs = 0;
  if (nowMs - lastReportMs >= 5000) {
    lastReportMs = nowMs;
    Serial.printf("%s ch %u%s, beacons %lu (last board %u on ch %u)\n",
                  paired ? "Linked" : "Searching", channel,
                  haveStatus ? ", status OK" : "",
                  static_cast<unsigned long>(beaconsHeard), lastBeaconBoard,
                  lastBeaconChannel);
  }
  delay(5);
}
