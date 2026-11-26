#pragma once

#include <Arduino.h>

namespace bridge_config {

// Atomic GPS Base (A134 / A134-V2): GPS TX -> AtomS3 G5, GPS RX <- G6.
constexpr int kGpsRxPin = 5;
constexpr int kGpsTxPin = 6;
constexpr uint32_t kGpsBaudCandidates[] = {9600, 115200};
constexpr size_t kGpsBaudCandidateCount =
    sizeof(kGpsBaudCandidates) / sizeof(kGpsBaudCandidates[0]);
constexpr uint32_t kGpsBaudScanIntervalMs = 5'000;
constexpr uint32_t kGpsFreshMs = 3'000;

// The Zero link is the AtomS3 USB port (USB CDC, /dev/ttyACM0 on the Zero).
// The Zero's Grove UART shares GPIO15 with both Caps, so it is not used.
constexpr uint32_t kZeroLinkBaud = 115200;
constexpr uint32_t kZeroLinkTimeoutMs = 3'000;
// Without a $PJP226,DSTAT/POS from the Zero this long, the bridge answers the
// accessories itself so they stay paired.
constexpr uint32_t kZeroStatusTimeoutMs = 2'000;

// ESP-NOW. The bridge never joins Wi-Fi, so it beacons on one fixed channel;
// accessories scan channels 1-13 until they hear it.
constexpr uint8_t kEspNowChannel = 1;
constexpr uint32_t kBeaconIntervalMs = 1'000;
constexpr uint32_t kDialStatusIntervalMs = 500;
constexpr uint32_t kPositionReportIntervalMs = 1'000;
constexpr uint32_t kBridgeHelloIntervalMs = 1'000;

// DEVICES page: green while heard within kDeviceFreshMs, orange until
// kDeviceAwayMs, then grey; forgotten after kDeviceForgetMs.
constexpr uint32_t kDeviceFreshMs = 5'000;
constexpr uint32_t kDeviceAwayMs = 30'000;
constexpr uint32_t kDeviceForgetMs = 120'000;
constexpr size_t kMaxDevices = 10;

constexpr uint32_t kDisplayIntervalMs = 500;

}  // namespace bridge_config
