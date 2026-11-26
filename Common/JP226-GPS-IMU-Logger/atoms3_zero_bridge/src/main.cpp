// AtomS3 USB bridge for the CardputerZero logger.
//
// The CardputerZero (Raspberry Pi CM0) cannot speak ESP-NOW, and its only
// pin UART is shared by the Grove port and both Caps, so this AtomS3 connects
// to it over USB (USB CDC, /dev/ttyACM0 on the Zero) and:
//
//  * acts as the logger end of the JP226 ESP-NOW protocol: it sends the
//    discovery beacon, keeps a list of every device it hears (DEVICES page)
//    and forwards their packets to the Zero;
//  * forwards the Atomic GPS Base NMEA to the Zero (for when the Zero has the
//    NFC/Sub-1GHz Cap instead of the GPS Cap; the Zero can switch it off);
//  * keeps the accessories paired with its own status packets whenever the
//    Zero is not sending them.
//
// Link protocol (text lines, NMEA-style "$...*HH" checksums): see
// docs/zero-bridge.md.

#include <Arduino.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <TinyGPSPlus.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#include "BridgeConfig.h"
#include "DinoSprite.h"
#include "TelemetryProtocol.h"
#include "Version.h"

namespace {

namespace cfg = bridge_config;

constexpr uint8_t kBroadcastMac[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
constexpr auto kThisBoard = telemetry::LoggerBoard::CardputerZero;

HardwareSerial gpsSerial(2);
TinyGPSPlus gps;
Preferences preferences;
M5Canvas canvas(&M5.Display);
bool canvasReady = false;

// ---------------------------------------------------------------- GPS ----

size_t gpsBaudIndex = 0;
uint32_t gpsBaudStartedMs = 0;
uint32_t lastGpsSentenceMs = 0;
char gpsLine[128]{};
size_t gpsLineLength = 0;
bool gpsLineOverflow = false;
bool forwardGps = true;  // The Zero sends $PJP226,GPS,0 when it uses the Cap.
uint32_t gpsLinesForwarded = 0;

int64_t daysFromCivil(int year, unsigned month, unsigned day) {
  year -= month <= 2;
  const int era = (year >= 0 ? year : year - 399) / 400;
  const unsigned yearOfEra = static_cast<unsigned>(year - era * 400);
  const unsigned dayOfYear =
      (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
  const unsigned dayOfEra = yearOfEra * 365 + yearOfEra / 4 -
                            yearOfEra / 100 + dayOfYear;
  return era * 146097LL + static_cast<int64_t>(dayOfEra) - 719468LL;
}

uint64_t gpsUtcEpochMs() {
  if (!gps.date.isValid() || !gps.time.isValid()) return 0;
  const int year = gps.date.year();
  const unsigned month = gps.date.month();
  const unsigned day = gps.date.day();
  if (year < 2024 || year > 2099 || month < 1 || month > 12 || day < 1 ||
      day > 31) {
    return 0;
  }
  const int64_t seconds =
      daysFromCivil(year, month, day) * 86400LL + gps.time.hour() * 3600LL +
      gps.time.minute() * 60LL + gps.time.second();
  return static_cast<uint64_t>(seconds) * 1000ULL +
         static_cast<uint64_t>(gps.time.centisecond()) * 10ULL;
}

bool gpsNmeaLive(uint32_t nowMs) {
  return lastGpsSentenceMs != 0 &&
         telemetry::elapsedMs(nowMs, lastGpsSentenceMs) <= cfg::kGpsFreshMs;
}

bool gpsPositionFresh() {
  return gps.location.isValid() && gps.location.age() <= cfg::kGpsFreshMs;
}

bool gpsSpeedFresh() {
  return gps.speed.isValid() && gps.speed.age() <= cfg::kGpsFreshMs;
}

bool gpsUtcFresh() {
  return gps.date.isValid() && gps.time.isValid() &&
         gps.date.age() <= cfg::kGpsFreshMs &&
         gps.time.age() <= cfg::kGpsFreshMs;
}

// Same gate as the loggers: fresh position, speed and time, >= 4 satellites
// and HDOP <= 5.
bool gpsFixValid() {
  return gpsPositionFresh() && gpsSpeedFresh() && gpsUtcFresh() &&
         gps.satellites.isValid() && gps.satellites.value() >= 4 &&
         gps.hdop.isValid() && gps.hdop.hdop() <= 5.0;
}

uint8_t gpsSatellites() {
  return gps.satellites.isValid()
             ? static_cast<uint8_t>(
                   std::min<uint32_t>(gps.satellites.value(), UINT8_MAX))
             : 0;
}

void startGps(uint32_t baud) {
  gpsSerial.end();
  gpsSerial.setRxBufferSize(2048);
  gpsSerial.begin(baud, SERIAL_8N1, cfg::kGpsRxPin, cfg::kGpsTxPin);
  gpsBaudStartedMs = millis();
  lastGpsSentenceMs = 0;
  gpsLineLength = 0;
  gpsLineOverflow = false;
}

// ----------------------------------------------------------- Zero link ----

char zeroLine[640]{};
size_t zeroLineLength = 0;
bool zeroLineOverflow = false;
uint32_t lastZeroLineMs = 0;
bool zeroHandlesCommands = false;  // HELLO caps contain 'C'
uint32_t lastZeroDialStatusMs = 0;
uint32_t lastZeroPositionMs = 0;
uint32_t zeroLinesIn = 0;
uint32_t zeroLinesBad = 0;
uint32_t linkDrops = 0;
uint32_t packetsToZero = 0;
uint32_t packetsFromZero = 0;

struct ZeroInfo {
  bool have = false;
  uint32_t utcEpochS = 0;
  uint32_t setMs = 0;
  int16_t utcOffsetMin = 0;
  uint8_t timeFlags = 0;
  int8_t batteryPercent = telemetry::kBatteryUnknown;
  uint16_t batteryMv = 0;
};
ZeroInfo zeroInfo;

bool zeroLinked(uint32_t nowMs) {
  return lastZeroLineMs != 0 &&
         telemetry::elapsedMs(nowMs, lastZeroLineMs) <= cfg::kZeroLinkTimeoutMs;
}

// Never block: when the Zero is not reading (or not plugged in) the USB
// buffer fills, and lines are dropped and counted instead.
void writeToZero(const char* text, size_t length) {
  if (Serial.availableForWrite() < static_cast<int>(length)) {
    ++linkDrops;
    return;
  }
  Serial.write(reinterpret_cast<const uint8_t*>(text), length);
}

uint8_t nmeaChecksum(const char* body, size_t length) {
  uint8_t checksum = 0;
  for (size_t i = 0; i < length; ++i) checksum ^= static_cast<uint8_t>(body[i]);
  return checksum;
}

// body is everything between '$' and '*'.
void sendToZero(const char* body) {
  char line[640];
  const int length = snprintf(line, sizeof(line), "$%s*%02X\r\n", body,
                              nmeaChecksum(body, std::strlen(body)));
  if (length > 0 && static_cast<size_t>(length) < sizeof(line)) {
    writeToZero(line, static_cast<size_t>(length));
  }
}

constexpr char kBase64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

size_t base64Encode(const uint8_t* data, size_t length, char* out) {
  size_t o = 0;
  for (size_t i = 0; i < length; i += 3) {
    const uint32_t n = (static_cast<uint32_t>(data[i]) << 16) |
                       (i + 1 < length ? data[i + 1] << 8 : 0) |
                       (i + 2 < length ? data[i + 2] : 0);
    out[o++] = kBase64[(n >> 18) & 63];
    out[o++] = kBase64[(n >> 12) & 63];
    out[o++] = i + 1 < length ? kBase64[(n >> 6) & 63] : '=';
    out[o++] = i + 2 < length ? kBase64[n & 63] : '=';
  }
  out[o] = '\0';
  return o;
}

int base64Value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Returns the decoded length, or -1 for bad input or too much data.
int base64Decode(const char* text, uint8_t* out, size_t capacity) {
  const size_t length = std::strlen(text);
  if (length % 4 != 0) return -1;
  size_t o = 0;
  for (size_t i = 0; i < length; i += 4) {
    int v[4];
    for (int k = 0; k < 4; ++k) {
      v[k] = text[i + k] == '=' ? 0 : base64Value(text[i + k]);
      if (v[k] < 0) return -1;
    }
    const uint32_t n = (v[0] << 18) | (v[1] << 12) | (v[2] << 6) | v[3];
    const int bytes = text[i + 2] == '=' ? 1 : text[i + 3] == '=' ? 2 : 3;
    for (int k = 0; k < bytes; ++k) {
      if (o >= capacity) return -1;
      out[o++] = static_cast<uint8_t>(n >> (16 - 8 * k));
    }
  }
  return static_cast<int>(o);
}

void formatMac(const uint8_t mac[6], char* out) {
  snprintf(out, 13, "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2],
           mac[3], mac[4], mac[5]);
}

bool parseMac(const char* text, uint8_t mac[6]) {
  if (std::strlen(text) != 12) return false;
  for (int i = 0; i < 6; ++i) {
    char pair[3] = {text[i * 2], text[i * 2 + 1], '\0'};
    char* end = nullptr;
    const long value = std::strtol(pair, &end, 16);
    if (end != pair + 2) return false;
    mac[i] = static_cast<uint8_t>(value);
  }
  return true;
}

// ------------------------------------------------------------- ESP-NOW ----

bool espNowReady = false;
uint32_t transmitFailures = 0;
portMUX_TYPE rxMux = portMUX_INITIALIZER_UNLOCKED;

struct RxItem {
  uint8_t mac[6];
  int8_t rssi;
  uint8_t length;
  uint8_t data[telemetry::kEspNowV1PayloadLimit];
};
constexpr size_t kRxQueueSize = 16;
RxItem rxQueue[kRxQueueSize];
size_t rxHead = 0;
size_t rxTail = 0;
uint32_t rxOverflows = 0;
uint32_t rxPackets = 0;

enum class Kind : uint8_t {
  Unknown, Controller, Dial, Echo, BlackBox, GpsImu, Logger
};

struct Device {
  bool used = false;
  uint8_t mac[6]{};
  Kind kind = Kind::Unknown;
  telemetry::LoggerBoard board = telemetry::LoggerBoard::Unknown;
  int8_t rssi = 0;
  uint32_t lastMs = 0;
  uint32_t packets = 0;
  uint32_t controllerMs = 0;  // Last DialCommand: it wants our status packets.
  bool espPeer = false;
};
Device devices[cfg::kMaxDevices];

const char* deviceName(const Device& device) {
  switch (device.kind) {
    case Kind::Controller: return "DIAL/ECHO";
    case Kind::Dial: return "M5DIAL";
    case Kind::Echo: return "ATOM ECHO";
    case Kind::BlackBox: return "BLACKBOX";
    case Kind::GpsImu: return "ATOM GPS";
    case Kind::Logger:
      switch (device.board) {
        case telemetry::LoggerBoard::Cardputer: return "CARDPUTER";
        case telemetry::LoggerBoard::CardputerAdv: return "CP ADV";
        case telemetry::LoggerBoard::Core2: return "CORE2";
        case telemetry::LoggerBoard::CardputerZero: return "ZERO BRG";
        default: return "LOGGER";
      }
    default: return "UNKNOWN";
  }
}

bool deviceFresh(const Device& device, uint32_t nowMs) {
  return device.used &&
         telemetry::elapsedMs(nowMs, device.lastMs) <= cfg::kDeviceFreshMs;
}

bool controllerFresh(const Device& device, uint32_t nowMs) {
  return device.used && device.controllerMs != 0 &&
         telemetry::elapsedMs(nowMs, device.controllerMs) <=
             cfg::kDeviceFreshMs;
}

size_t freshDeviceCount(uint32_t nowMs) {
  size_t count = 0;
  for (const auto& device : devices) count += deviceFresh(device, nowMs);
  return count;
}

void forgetDevice(Device& device) {
  if (device.espPeer) esp_now_del_peer(device.mac);
  device = Device{};
}

Device* findDevice(const uint8_t mac[6]) {
  for (auto& device : devices) {
    if (device.used && std::memcmp(device.mac, mac, 6) == 0) return &device;
  }
  return nullptr;
}

Device& findOrAddDevice(const uint8_t mac[6], uint32_t nowMs) {
  if (Device* found = findDevice(mac)) return *found;
  Device* slot = nullptr;
  for (auto& device : devices) {
    if (!device.used) {
      slot = &device;
      break;
    }
  }
  if (slot == nullptr) {  // Full: reuse the device heard longest ago.
    slot = &devices[0];
    for (auto& device : devices) {
      if (telemetry::elapsedMs(nowMs, device.lastMs) >
          telemetry::elapsedMs(nowMs, slot->lastMs)) {
        slot = &device;
      }
    }
    forgetDevice(*slot);
  }
  slot->used = true;
  std::memcpy(slot->mac, mac, 6);
  slot->lastMs = nowMs;
  return *slot;
}

bool ensurePeer(Device& device) {
  if (device.espPeer) return true;
  esp_now_peer_info_t peer{};
  std::memcpy(peer.peer_addr, device.mac, 6);
  peer.channel = 0;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  const esp_err_t result = esp_now_add_peer(&peer);
  device.espPeer = result == ESP_OK || result == ESP_ERR_ESPNOW_EXIST;
  return device.espPeer;
}

void forgetOldDevices(uint32_t nowMs) {
  for (auto& device : devices) {
    if (device.used &&
        telemetry::elapsedMs(nowMs, device.lastMs) > cfg::kDeviceForgetMs) {
      forgetDevice(device);
    }
  }
}

// The receive callback runs in the Wi-Fi task: it only checks the frame and
// queues it. Everything else happens in loop().
void receiveCallback(const esp_now_recv_info_t* info, const uint8_t* data,
                     int length) {
  if (info == nullptr || info->src_addr == nullptr || data == nullptr ||
      length < static_cast<int>(sizeof(telemetry::PacketHeader) + 4) ||
      length > static_cast<int>(telemetry::kEspNowV1PayloadLimit)) {
    return;
  }
  telemetry::PacketHeader header{};
  std::memcpy(&header, data, sizeof(header));
  if (header.magic != telemetry::kMagic ||
      header.version != telemetry::kProtocolVersion || header.size != length) {
    return;
  }
  uint32_t crc = 0;
  std::memcpy(&crc, data + length - 4, 4);
  if (crc != telemetry::crc32(data, length - 4)) return;
  portENTER_CRITICAL(&rxMux);
  const size_t next = (rxHead + 1) % kRxQueueSize;
  if (next == rxTail) {
    ++rxOverflows;
  } else {
    RxItem& item = rxQueue[rxHead];
    std::memcpy(item.mac, info->src_addr, 6);
    item.rssi = info->rx_ctrl != nullptr
                    ? static_cast<int8_t>(info->rx_ctrl->rssi)
                    : 0;
    item.length = static_cast<uint8_t>(length);
    std::memcpy(item.data, data, length);
    rxHead = next;
  }
  portEXIT_CRITICAL(&rxMux);
}

void sendCallback(const esp_now_send_info_t*, esp_now_send_status_t status) {
  if (status != ESP_NOW_SEND_SUCCESS) ++transmitFailures;
}

void beginEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(cfg::kEspNowChannel, WIFI_SECOND_CHAN_NONE);
  espNowReady = esp_now_init() == ESP_OK;
  if (!espNowReady) return;
  esp_now_register_recv_cb(receiveCallback);
  esp_now_register_send_cb(sendCallback);
  esp_now_peer_info_t peer{};
  std::memcpy(peer.peer_addr, kBroadcastMac, 6);
  peer.channel = 0;
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  esp_now_add_peer(&peer);
}

template <typename Packet>
void sendPacket(const uint8_t mac[6], const Packet& packet) {
  if (esp_now_send(mac, reinterpret_cast<const uint8_t*>(&packet),
                   sizeof(packet)) != ESP_OK) {
    ++transmitFailures;
  }
}

// ------------------------------------------- Bridge as the logger end ----

uint32_t beaconSequence = 0;
uint32_t statusSequence = 0;
uint32_t positionSequence = 0;
uint32_t lastBeaconMs = 0;
uint32_t lastLocalStatusMs = 0;
uint32_t lastLocalPositionMs = 0;
uint32_t lastHelloMs = 0;

constexpr uint32_t kZeroClockValidMs = 10UL * 60UL * 1000UL;

bool zeroClockValid(uint32_t nowMs) {
  return zeroInfo.have && (zeroInfo.timeFlags & telemetry::UtcValid) &&
         telemetry::elapsedMs(nowMs, zeroInfo.setMs) <= kZeroClockValidMs;
}

void sendBeacon(uint32_t nowMs) {
  if (!espNowReady ||
      telemetry::elapsedMs(nowMs, lastBeaconMs) < cfg::kBeaconIntervalMs) {
    return;
  }
  lastBeaconMs = nowMs;
  telemetry::DiscoveryPacket packet{};
  telemetry::preparePacket(packet, telemetry::PacketType::Discovery);
  packet.sequence = beaconSequence++;
  packet.uptime_ms = nowMs;
  uint8_t flags = 0;
  if (zeroClockValid(nowMs)) {
    packet.utc_epoch_s = zeroInfo.utcEpochS +
                         telemetry::elapsedMs(nowMs, zeroInfo.setMs) / 1000U;
    flags |= telemetry::UtcValid;
  } else if (gpsUtcFresh() && gpsUtcEpochMs() != 0) {
    packet.utc_epoch_s = static_cast<uint32_t>(gpsUtcEpochMs() / 1000ULL);
    flags |= telemetry::UtcValid;
  }
  if (zeroInfo.have && (zeroInfo.timeFlags & telemetry::UtcOffsetValid)) {
    packet.utc_offset_min = zeroInfo.utcOffsetMin;
    flags |= telemetry::UtcOffsetValid;
  }
  packet.time_flags = static_cast<uint8_t>(
      flags | (static_cast<uint8_t>(kThisBoard) << telemetry::kTimeFlagBoardShift));
  packet.battery_percent =
      zeroInfo.have ? zeroInfo.batteryPercent : telemetry::kBatteryUnknown;
  packet.battery_mv = zeroInfo.have ? zeroInfo.batteryMv : 0;
  telemetry::sealPacket(packet);
  sendPacket(kBroadcastMac, packet);
}

// The accessories drop the link after 5 s without a DialStatus. While the
// Zero is not sending its own, the bridge sends one from its own GPS so the
// DEVICES list stays populated and the Dial shows why logging is missing.
void sendLocalStatus(uint32_t nowMs) {
  if (!espNowReady ||
      telemetry::elapsedMs(nowMs, lastLocalStatusMs) < cfg::kDialStatusIntervalMs) {
    return;
  }
  lastLocalStatusMs = nowMs;
  if (lastZeroDialStatusMs != 0 &&
      telemetry::elapsedMs(nowMs, lastZeroDialStatusMs) <=
          cfg::kZeroStatusTimeoutMs) {
    return;
  }
  telemetry::DialStatusPacket status{};
  telemetry::preparePacket(status, telemetry::PacketType::DialStatus);
  status.sequence = statusSequence++;
  status.uptime_ms = nowMs;
  status.speed_kmh = gpsSpeedFresh() ? static_cast<float>(gps.speed.kmph()) : 0.0f;
  status.fix_valid = gpsFixValid() ? 1 : 0;
  status.satellites = gpsSatellites();
  status.battery_percent =
      zeroInfo.have ? zeroInfo.batteryPercent : telemetry::kBatteryUnknown;
  status.logger_board = static_cast<uint8_t>(kThisBoard);
  snprintf(status.place, sizeof(status.place), "%s",
           zeroLinked(nowMs) ? "ZERO NOT LOGGING" : "ZERO OFFLINE");
  telemetry::sealPacket(status);
  for (auto& device : devices) {
    if (controllerFresh(device, nowMs) && ensurePeer(device)) {
      sendPacket(device.mac, status);
    }
  }
}

// What a black box backs up; from this AtomS3's GPS when the Zero is silent.
void sendLocalPosition(uint32_t nowMs) {
  if (!espNowReady ||
      telemetry::elapsedMs(nowMs, lastLocalPositionMs) <
          cfg::kPositionReportIntervalMs) {
    return;
  }
  lastLocalPositionMs = nowMs;
  if (lastZeroPositionMs != 0 &&
      telemetry::elapsedMs(nowMs, lastZeroPositionMs) <=
          cfg::kZeroStatusTimeoutMs) {
    return;
  }
  bool anyController = false;
  for (const auto& device : devices) anyController |= controllerFresh(device, nowMs);
  if (!anyController) return;
  telemetry::PositionReportPacket report{};
  telemetry::preparePacket(report, telemetry::PacketType::PositionReport);
  report.sequence = positionSequence++;
  if (gpsUtcFresh() && gpsUtcEpochMs() != 0) {
    report.utc_ms = gpsUtcEpochMs();
    report.flags |= telemetry::PositionUtcValid;
  }
  if (gpsFixValid()) report.flags |= telemetry::PositionFix;
  if (gpsPositionFresh()) report.flags |= telemetry::PositionValid;
  report.satellites = gpsSatellites();
  report.logger_board = static_cast<uint8_t>(kThisBoard);
  if (zeroInfo.have && (zeroInfo.timeFlags & telemetry::UtcOffsetValid)) {
    report.utc_offset_min = zeroInfo.utcOffsetMin;
  }
  report.latitude_deg = gps.location.lat();
  report.longitude_deg = gps.location.lng();
  report.altitude_m = gps.altitude.meters();
  report.speed_kmh = gpsSpeedFresh() ? static_cast<float>(gps.speed.kmph()) : 0.0f;
  report.course_deg = gps.course.deg();
  report.hdop = gps.hdop.hdop();
  telemetry::sealPacket(report);
  for (auto& device : devices) {
    if (controllerFresh(device, nowMs) && ensurePeer(device)) {
      sendPacket(device.mac, report);
    }
  }
}

// Without a Zero that handles commands nothing can be saved, so the command
// is refused at once (accepted = 0) rather than left to retry forever. The
// loggers acknowledge heartbeats the same way.
void answerCommandLocally(Device& device,
                          const telemetry::DialCommandPacket& command) {
  telemetry::DialAckPacket ack{};
  telemetry::preparePacket(ack, telemetry::PacketType::DialAck);
  ack.command_sequence = command.sequence;
  ack.accepted = 0;
  telemetry::sealPacket(ack);
  if (ensurePeer(device)) sendPacket(device.mac, ack);
}

void processPacket(const RxItem& item, uint32_t nowMs) {
  ++rxPackets;
  Device& device = findOrAddDevice(item.mac, nowMs);
  device.lastMs = nowMs;
  device.rssi = item.rssi;
  ++device.packets;
  const bool specific = device.kind == Kind::Dial || device.kind == Kind::Echo ||
                        device.kind == Kind::BlackBox;
  const auto type = static_cast<telemetry::PacketType>(item.data[5]);
  switch (type) {
    case telemetry::PacketType::Discovery:
      if (item.length == sizeof(telemetry::DiscoveryPacket)) {
        telemetry::DiscoveryPacket beacon{};
        std::memcpy(&beacon, item.data, sizeof(beacon));
        device.kind = Kind::Logger;
        device.board = telemetry::beaconBoard(beacon.time_flags);
      }
      break;
    case telemetry::PacketType::Telemetry:
    case telemetry::PacketType::RawImuBatch:
      device.kind = Kind::GpsImu;
      break;
    case telemetry::PacketType::DialCommand:
      if (!specific) device.kind = Kind::Controller;
      device.controllerMs = nowMs;
      ensurePeer(device);
      if (item.length == sizeof(telemetry::DialCommandPacket) &&
          !(zeroHandlesCommands && zeroLinked(nowMs))) {
        telemetry::DialCommandPacket command{};
        std::memcpy(&command, item.data, sizeof(command));
        answerCommandLocally(device, command);
      }
      break;
    case telemetry::PacketType::BlackBoxStatus:
      device.kind = Kind::BlackBox;
      break;
    case telemetry::PacketType::TemperatureReport:
      if (item.length == sizeof(telemetry::TemperatureReportPacket)) {
        telemetry::TemperatureReportPacket report{};
        std::memcpy(&report, item.data, sizeof(report));
        const auto source = static_cast<telemetry::ProbeSource>(report.source);
        if (source == telemetry::ProbeSource::Dial) device.kind = Kind::Dial;
        if (source == telemetry::ProbeSource::Echo) device.kind = Kind::Echo;
        if (source == telemetry::ProbeSource::BlackBox) device.kind = Kind::BlackBox;
      }
      break;
    default:
      break;
  }

  // Everything heard is passed on; the Zero decides what to use.
  char mac[13];
  formatMac(item.mac, mac);
  char encoded[((telemetry::kEspNowV1PayloadLimit + 2) / 3) * 4 + 1];
  base64Encode(item.data, item.length, encoded);
  char body[400];
  snprintf(body, sizeof(body), "PJP226,RX,%s,%d,%s", mac, item.rssi, encoded);
  sendToZero(body);
  ++packetsToZero;
}

void drainRadio(uint32_t nowMs) {
  for (int processed = 0; processed < 8; ++processed) {
    RxItem item;
    bool have = false;
    portENTER_CRITICAL(&rxMux);
    if (rxTail != rxHead) {
      item = rxQueue[rxTail];
      rxTail = (rxTail + 1) % kRxQueueSize;
      have = true;
    }
    portEXIT_CRITICAL(&rxMux);
    if (!have) return;
    processPacket(item, nowMs);
  }
}

// ------------------------------------------------ Lines from the Zero ----

// Splits in place at commas; returns the field count.
int splitFields(char* text, char* fields[], int maximum) {
  int count = 0;
  fields[count++] = text;
  for (char* p = text; *p != '\0' && count < maximum; ++p) {
    if (*p == ',') {
      *p = '\0';
      fields[count++] = p + 1;
    }
  }
  return count;
}

void handleZeroTx(const char* macText, const char* encoded, uint32_t nowMs) {
  uint8_t mac[6];
  uint8_t data[telemetry::kEspNowV1PayloadLimit];
  const int length = parseMac(macText, mac)
                         ? base64Decode(encoded, data, sizeof(data))
                         : -1;
  if (length < static_cast<int>(sizeof(telemetry::PacketHeader) + 4)) {
    ++zeroLinesBad;
    return;
  }
  const auto type = static_cast<telemetry::PacketType>(data[5]);
  if (type == telemetry::PacketType::DialStatus) lastZeroDialStatusMs = nowMs;
  if (type == telemetry::PacketType::PositionReport) lastZeroPositionMs = nowMs;
  if (std::memcmp(mac, kBroadcastMac, 6) != 0) {
    Device* device = findDevice(mac);
    if (device == nullptr || !ensurePeer(*device)) {
      ++zeroLinesBad;
      return;
    }
  }
  if (esp_now_send(mac, data, static_cast<size_t>(length)) != ESP_OK) {
    ++transmitFailures;
  }
  ++packetsFromZero;
}

void handleZeroLine(char* line, uint32_t nowMs) {
  ++zeroLinesIn;
  const size_t length = std::strlen(line);
  char* star = std::strrchr(line, '*');
  if (length < 4 || line[0] != '$' || star == nullptr || star[1] == '\0' ||
      star[2] == '\0') {
    ++zeroLinesBad;
    return;
  }
  const long given = std::strtol(star + 1, nullptr, 16);
  if (given != nmeaChecksum(line + 1, static_cast<size_t>(star - line - 1))) {
    ++zeroLinesBad;
    return;
  }
  *star = '\0';
  char* fields[8];
  const int count = splitFields(line + 1, fields, 8);
  if (std::strcmp(fields[0], "PJP226") != 0 || count < 2) {
    ++zeroLinesBad;
    return;
  }
  lastZeroLineMs = nowMs;
  const char* command = fields[1];
  if (std::strcmp(command, "HELLO") == 0) {
    zeroHandlesCommands = count >= 4 && std::strchr(fields[3], 'C') != nullptr;
  } else if (std::strcmp(command, "TIME") == 0 && count >= 7) {
    zeroInfo.have = true;
    zeroInfo.utcEpochS = static_cast<uint32_t>(std::strtoul(fields[2], nullptr, 10));
    zeroInfo.setMs = nowMs;
    zeroInfo.utcOffsetMin = static_cast<int16_t>(std::atoi(fields[3]));
    zeroInfo.timeFlags = static_cast<uint8_t>(std::atoi(fields[4]) & 0x0F);
    zeroInfo.batteryPercent = static_cast<int8_t>(std::atoi(fields[5]));
    zeroInfo.batteryMv = static_cast<uint16_t>(std::atoi(fields[6]));
  } else if (std::strcmp(command, "GPS") == 0 && count >= 3) {
    forwardGps = std::atoi(fields[2]) != 0;
    preferences.putBool("fwd_gps", forwardGps);
  } else if (std::strcmp(command, "TX") == 0 && count >= 4) {
    handleZeroTx(fields[2], fields[3], nowMs);
  } else {
    ++zeroLinesBad;
  }
}

void pollZero(uint32_t nowMs) {
  while (Serial.available() > 0) {
    const int c = Serial.read();
    if (c < 0) break;
    if (c == '\n') {
      zeroLine[zeroLineLength] = '\0';
      if (!zeroLineOverflow && zeroLineLength > 0) {
        handleZeroLine(zeroLine, nowMs);
      } else if (zeroLineOverflow) {
        ++zeroLinesBad;
      }
      zeroLineLength = 0;
      zeroLineOverflow = false;
    } else if (c != '\r') {
      if (zeroLineLength < sizeof(zeroLine) - 1) {
        zeroLine[zeroLineLength++] = static_cast<char>(c);
      } else {
        zeroLineOverflow = true;
      }
    }
  }
}

void sendHello(uint32_t nowMs) {
  if (telemetry::elapsedMs(nowMs, lastHelloMs) < cfg::kBridgeHelloIntervalMs) {
    return;
  }
  lastHelloMs = nowMs;
  char body[120];
  snprintf(body, sizeof(body), "PJP226,BRIDGE,%s,%s,%u,%u,%lu,%d,%d",
           version::kNumber, version::kGit,
           static_cast<unsigned>(cfg::kEspNowChannel),
           static_cast<unsigned>(freshDeviceCount(nowMs)),
           static_cast<unsigned long>(cfg::kGpsBaudCandidates[gpsBaudIndex]),
           gpsNmeaLive(nowMs) ? 1 : 0, forwardGps ? 1 : 0);
  sendToZero(body);
}

void pollGps(uint32_t nowMs) {
  const uint32_t previousChecksums = gps.passedChecksum();
  while (gpsSerial.available() > 0) {
    const char c = static_cast<char>(gpsSerial.read());
    gps.encode(c);
    if (c == '\n') {
      if (forwardGps && !gpsLineOverflow && gpsLineLength > 0 &&
          gpsLine[0] == '$') {
        gpsLine[gpsLineLength++] = '\r';
        gpsLine[gpsLineLength++] = '\n';
        writeToZero(gpsLine, gpsLineLength);
        ++gpsLinesForwarded;
      }
      gpsLineLength = 0;
      gpsLineOverflow = false;
    } else if (c != '\r') {
      if (gpsLineLength < sizeof(gpsLine) - 3) {
        gpsLine[gpsLineLength++] = c;
      } else {
        gpsLineOverflow = true;
      }
    }
  }
  if (gps.passedChecksum() != previousChecksums) lastGpsSentenceMs = nowMs;
  if (lastGpsSentenceMs == 0 &&
      telemetry::elapsedMs(nowMs, gpsBaudStartedMs) >= cfg::kGpsBaudScanIntervalMs) {
    gpsBaudIndex = (gpsBaudIndex + 1U) % cfg::kGpsBaudCandidateCount;
    startGps(cfg::kGpsBaudCandidates[gpsBaudIndex]);
  }
}

// ------------------------------------------------------------- Screen ----

enum class Page : uint8_t { Status, Devices, Gps, Version, About, Count };
Page page = Page::Status;
uint32_t lastDisplayMs = 0;
dino::Game dinoGame;

// Double-size text rows (12 x 16 px, 10 characters per row).
class ScreenRows {
 public:
  ScreenRows(M5Canvas& display, int top, int pitch)
      : display_(display), y_(top), pitch_(pitch) {}

  template <typename... Values>
  void line(uint16_t color, const char* format, Values... values) {
    char text[24]{};
    snprintf(text, sizeof(text), format, values...);
    display_.setTextSize(2);
    display_.setTextColor(color, TFT_BLACK);
    display_.drawString(text, 2, y_);
    y_ += pitch_;
  }

 private:
  M5Canvas& display_;
  int y_;
  int pitch_;
};

void drawTitle(const char* title, uint16_t color) {
  canvas.setTextSize(1);
  canvas.setTextColor(color, TFT_BLACK);
  canvas.setTextDatum(top_left);
  canvas.drawString(title, 2, 1);
  char index[8]{};
  snprintf(index, sizeof(index), "%u/%u", static_cast<unsigned>(page) + 1,
           static_cast<unsigned>(Page::Count));
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  canvas.setTextDatum(top_right);
  canvas.drawString(index, canvas.width() - 2, 1);
  canvas.setTextDatum(top_left);
  canvas.drawFastHLine(0, 10, canvas.width(), color);
}

void drawGpsRow(ScreenRows& rows, uint32_t nowMs) {
  if (!gpsNmeaLive(nowMs)) {
    rows.line(TFT_RED, "NO GPS");
  } else {
    rows.line(gpsPositionFresh() ? TFT_GREEN : TFT_ORANGE, "%s %usat",
              gpsPositionFresh() ? "FIX" : "WAIT",
              static_cast<unsigned>(gpsSatellites()));
  }
}

void drawStatusPage(uint32_t nowMs) {
  drawTitle("ZERO BRIDGE", TFT_CYAN);
  ScreenRows rows(canvas, 14, 19);
  if (zeroLinked(nowMs)) {
    rows.line(TFT_GREEN, "ZERO USB");
  } else {
    rows.line(TFT_RED, "NO ZERO");
  }
  drawGpsRow(rows, nowMs);
  rows.line(forwardGps ? TFT_WHITE : TFT_DARKGREY, "GPS>%s",
            forwardGps ? "ZERO" : "OFF");
  const size_t fresh = freshDeviceCount(nowMs);
  rows.line(fresh > 0 ? TFT_GREEN : TFT_ORANGE, "DEV %u",
            static_cast<unsigned>(fresh));
  rows.line(TFT_WHITE, "ESPNOW ch%u", static_cast<unsigned>(cfg::kEspNowChannel));

  canvas.setTextSize(1);
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  char text[32]{};
  snprintf(text, sizeof(text), "RX %lu TX %lu DROP %lu",
           static_cast<unsigned long>(packetsToZero),
           static_cast<unsigned long>(packetsFromZero),
           static_cast<unsigned long>(linkDrops + rxOverflows));
  canvas.drawString(text, 2, 110);
  canvas.drawString(WiFi.macAddress(), 2, 119);
}

void formatAge(uint32_t ageMs, char* out, size_t size) {
  const uint32_t seconds = ageMs / 1000U;
  if (seconds < 1) snprintf(out, size, "now");
  else if (seconds < 60) snprintf(out, size, "%lus", static_cast<unsigned long>(seconds));
  else snprintf(out, size, "%lum", static_cast<unsigned long>(seconds / 60U));
}

// One row per device, most recently heard first:
// name, last two MAC bytes, RSSI and time since it was last heard.
void drawDevicesPage(uint32_t nowMs) {
  const Device* order[cfg::kMaxDevices];
  size_t count = 0;
  for (const auto& device : devices) {
    if (device.used) order[count++] = &device;
  }
  std::sort(order, order + count, [nowMs](const Device* a, const Device* b) {
    return telemetry::elapsedMs(nowMs, a->lastMs) <
           telemetry::elapsedMs(nowMs, b->lastMs);
  });
  char title[24]{};
  snprintf(title, sizeof(title), "DEVICES %u",
           static_cast<unsigned>(freshDeviceCount(nowMs)));
  drawTitle(title, TFT_CYAN);
  canvas.setTextSize(1);
  if (count == 0) {
    canvas.setTextDatum(middle_center);
    canvas.setTextColor(TFT_ORANGE, TFT_BLACK);
    canvas.drawString("No ESP devices yet", 64, 50);
    canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
    char text[24]{};
    snprintf(text, sizeof(text), "Beacon on ch %u",
             static_cast<unsigned>(cfg::kEspNowChannel));
    canvas.drawString(text, 64, 66);
    canvas.drawString("Accessories scan 1-13", 64, 82);
    canvas.setTextDatum(top_left);
    return;
  }
  int y = 14;
  for (size_t i = 0; i < count && y <= 118; ++i, y += 11) {
    const Device& device = *order[i];
    const uint32_t age = telemetry::elapsedMs(nowMs, device.lastMs);
    canvas.setTextColor(age <= cfg::kDeviceFreshMs  ? TFT_GREEN
                        : age <= cfg::kDeviceAwayMs ? TFT_ORANGE
                                                    : TFT_DARKGREY,
                        TFT_BLACK);
    char ageText[8];
    formatAge(age, ageText, sizeof(ageText));
    char text[32];
    snprintf(text, sizeof(text), "%-9s %02X%02X %4d %3s", deviceName(device),
             device.mac[4], device.mac[5], device.rssi, ageText);
    canvas.drawString(text, 2, y);
  }
}

void drawGpsPage(uint32_t nowMs) {
  char title[24]{};
  snprintf(title, sizeof(title), "GPS %lu baud",
           static_cast<unsigned long>(cfg::kGpsBaudCandidates[gpsBaudIndex]));
  drawTitle(title, TFT_CYAN);
  ScreenRows rows(canvas, 14, 19);
  drawGpsRow(rows, nowMs);
  if (gpsPositionFresh()) {
    rows.line(TFT_WHITE, "%.5f", gps.location.lat());
    rows.line(TFT_WHITE, "%.5f", gps.location.lng());
  } else {
    rows.line(TFT_DARKGREY, "LAT --");
    rows.line(TFT_DARKGREY, "LON --");
  }
  if (gpsSpeedFresh()) {
    rows.line(TFT_GREEN, "%5.1fkm/h", gps.speed.kmph());
  } else {
    rows.line(TFT_DARKGREY, "  --km/h");
  }
  if (gps.hdop.isValid()) {
    rows.line(gps.hdop.hdop() <= 5.0 ? TFT_WHITE : TFT_ORANGE, "HDOP %.1f",
              gps.hdop.hdop());
  } else {
    rows.line(TFT_DARKGREY, "HDOP --");
  }
  canvas.setTextSize(1);
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  char text[32]{};
  snprintf(text, sizeof(text), "%s %lu lines",
           forwardGps ? "TO ZERO" : "NOT SENT",
           static_cast<unsigned long>(gpsLinesForwarded));
  canvas.drawString(text, 2, 119);
}

void drawVersionPage(uint32_t nowMs) {
  drawTitle("FOSSIL RECORD", TFT_CYAN);
  dino::draw(canvas, canvas.width() - dino::kWidth * 2 - 2, 12, 2,
             (nowMs / 4000) % dino::kFrameCount);
  canvas.setTextSize(1);
  canvas.setTextDatum(top_left);
  canvas.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  canvas.drawString("Species", 2, 16);
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.drawString(version::kNumber, 2, 26);
  canvas.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  canvas.drawString("DNA", 2, 50);
  canvas.setTextColor(TFT_YELLOW, TFT_BLACK);
  canvas.drawString(version::kGit, 30, 50);
  canvas.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  canvas.drawString("Hatched", 2, 64);
  canvas.setTextColor(TFT_GREEN, TFT_BLACK);
  canvas.drawString(version::kDate, 2, 76);
  canvas.setTextColor(TFT_DARKGREY, TFT_BLACK);
  canvas.drawString("AtomS3 Zero bridge", 2, 88);
  canvas.setTextDatum(middle_center);
  canvas.setTextColor(TFT_ORANGE, TFT_BLACK);
  canvas.drawString(version::jokeAt(nowMs), canvas.width() / 2, 108);
  canvas.setTextDatum(top_left);
}

// This AtomS3's own GPS speed for the game; unknown counts as parked.
float dinoGameSpeedKmh() {
  return gpsSpeedFresh() ? static_cast<float>(gps.speed.kmph()) : -1.0f;
}

void drawAboutPage(uint32_t nowMs) {
  drawTitle("JP226PRINTS", TFT_CYAN);
  constexpr int kScale = 2;
  constexpr int kGroundY = 16 + dino::kHeight * kScale + 1;
  const int width = canvas.width();
  dinoGame.update(nowMs, dinoGameSpeedKmh(), width / kScale);
  if (dinoGame.takeNewBest()) {
    preferences.putUShort(dino::kBestKey, dinoGame.best());
  }
  dinoGame.draw(canvas, 0, kGroundY, width, kScale, nowMs);
  canvas.setTextSize(1);
  canvas.setTextDatum(middle_center);
  switch (dinoGame.state()) {
    case dino::Game::State::Ready:
      canvas.setTextColor(TFT_YELLOW, TFT_BLACK);
      canvas.drawString("TAP: PLAY HOLD: PAGE", width / 2, 62);
      break;
    case dino::Game::State::Playing:
    case dino::Game::State::Over: {
      char text[32];
      dinoGame.statusText(text, sizeof(text));
      canvas.setTextColor(dinoGame.state() == dino::Game::State::Playing
                              ? TFT_WHITE
                          : dinoGame.beatBest() ? TFT_GREEN
                                                : TFT_RED,
                          TFT_BLACK);
      canvas.drawString(text, width / 2, 62);
      break;
    }
    default:
      break;
  }
  canvas.setTextColor(TFT_WHITE, TFT_BLACK);
  canvas.drawString(dino::kCredit1, width / 2, 84);
  canvas.setFont(&fonts::FreeSansBold9pt7b);
  canvas.setTextColor(TFT_CYAN, TFT_BLACK);
  canvas.drawString(dino::kCredit2, width / 2, 104);
  canvas.setFont(&fonts::Font0);
  canvas.setTextDatum(top_left);
}

void drawScreen(uint32_t nowMs) {
  const uint32_t interval = page == Page::About ? 60 : cfg::kDisplayIntervalMs;
  if (telemetry::elapsedMs(nowMs, lastDisplayMs) < interval) return;
  lastDisplayMs = nowMs;
  if (!canvasReady) return;
  canvas.fillScreen(TFT_BLACK);
  canvas.setTextDatum(top_left);
  switch (page) {
    case Page::Devices: drawDevicesPage(nowMs); break;
    case Page::Gps: drawGpsPage(nowMs); break;
    case Page::Version: drawVersionPage(nowMs); break;
    case Page::About: drawAboutPage(nowMs); break;
    default: drawStatusPage(nowMs); break;
  }
  canvas.pushSprite(0, 0);
}

void nextPage() {
  page = static_cast<Page>((static_cast<uint8_t>(page) + 1U) %
                           static_cast<uint8_t>(Page::Count));
  preferences.putUChar("page", static_cast<uint8_t>(page));
  lastDisplayMs = 0;
}

// Tap: next page (on the game page a tap jumps). Hold 1.5 s on the game page:
// next page. The old hold-to-zero IMU calibration is retired here: the Zero
// has its own BMI270.
void handleButton(uint32_t nowMs) {
  static bool pressOnGame = false;
  static bool holdLatched = false;
  if (M5.BtnA.wasPressed()) {
    pressOnGame = page == Page::About &&
                  dinoGame.state() != dino::Game::State::Auto;
    if (pressOnGame) dinoGame.press(nowMs);
  }
  if (M5.BtnA.pressedFor(1500) && !holdLatched) {
    holdLatched = true;
    if (page == Page::About) nextPage();
  }
  if (M5.BtnA.wasReleased()) {
    if (!holdLatched && !pressOnGame) nextPage();
    holdLatched = false;
  }
}

}  // namespace

void setup() {
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(0);
  Serial.begin(cfg::kZeroLinkBaud);
  auto m5Config = M5.config();
  m5Config.internal_imu = false;
  M5.begin(m5Config);
  M5.Display.setRotation(0);
  M5.Display.setTextWrap(false);
  M5.Display.fillScreen(TFT_BLACK);
  canvas.setColorDepth(16);
  canvasReady = canvas.createSprite(M5.Display.width(),
                                    M5.Display.height()) != nullptr;
  canvas.setTextWrap(false);
  preferences.begin("zerobridge", false);
  forwardGps = preferences.getBool("fwd_gps", true);
  dinoGame.setBest(preferences.getUShort(dino::kBestKey, 0));
  const uint8_t savedPage = preferences.getUChar("page", 0);
  if (savedPage < static_cast<uint8_t>(Page::Count)) {
    page = static_cast<Page>(savedPage);
  }
  startGps(cfg::kGpsBaudCandidates[gpsBaudIndex]);
  beginEspNow();
}

void loop() {
  M5.update();
  const uint32_t nowMs = millis();
  pollGps(nowMs);
  pollZero(nowMs);
  drainRadio(nowMs);
  sendBeacon(nowMs);
  sendLocalStatus(nowMs);
  sendLocalPosition(nowMs);
  sendHello(nowMs);
  forgetOldDevices(nowMs);
  handleButton(nowMs);
  drawScreen(nowMs);
  delay(1);
}
