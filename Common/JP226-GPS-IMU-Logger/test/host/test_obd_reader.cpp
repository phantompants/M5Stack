#include "ObdReader.h"
#include <WiFi.h>
#include <algorithm>
#include <cassert>
#include <cstdio>

namespace {
std::string header;
std::string receiveFilter;
std::string goodReply(const std::string& command) {
  if (command == "ATZ\r") { header.clear(); receiveFilter = "7E8"; return "ELM327 v1.5\r>"; }
  if (command.rfind("ATCRA", 0) == 0) { receiveFilter = command.substr(5, 3); return "OK\r>"; }
  if (command == "ATAR\r") { receiveFilter = "7E8"; return "OK\r>"; }
  if (command.rfind("ATSH", 0) == 0) { header = command.substr(4, 3); return "OK\r>"; }
  if (command.rfind("AT", 0) == 0) return "OK\r>";
  if (command.rfind("01", 0) == 0) {
    assert(header == "7E0");
    if (receiveFilter != "7E8") return "NO DATA\r>";
    const std::string pid = command.substr(2, 2);
    if (pid == "00" || pid == "20" || pid == "40") return "41" + pid + "FFFFFFFF\r>";
    if (pid == "0C") return "410C1AF8\r>";
    if (pid == "01") return "410181000000\r>";
    if (pid == "42") return "414236B0\r>";
    return "41" + pid + "50\r>";
  }
  if (command == "03\r") { assert(header == "7E0"); return "43010299\r>"; }
  if (command == "07\r") { assert(header == "7E0"); return "4700\r>"; }
  if (command.rfind("22", 0) == 0) {
    assert(header == "726");
    if (receiveFilter != "72E") return "NO DATA\r>";
    const auto did = command.substr(2, 4);
    if (did == "280F") return "62280F12345678\r>";
    if (did == "2810") return "62281023456789\r>";
    if (did == "2811") return "6228113456789A\r>";
    if (did == "2812") return "622812456789AB\r>";
    if (did == "280B") return "00C\r0:62280B123456\r1:7802BC4006AA\r>";
    return "62" + did + "02BC\r>";
  }
  assert(false && "Unexpected vehicle service; only read commands are permitted");
  return "";
}
void tick(ObdReader& reader, uint32_t duration) {
  const auto stop = fakeMillis + duration;
  while (fakeMillis < stop) { reader.update(fakeMillis); fakeMillis += 25; }
}
void reset() { fake::reset(); fakeMillis = 5000; fakeWifiStatus = WL_CONNECTED; fake::reply = goodReply; }

void test_complete_session_and_pause() {
  reset();
  ObdReader reader;
  tick(reader, 12'000);
  auto s = reader.snapshot();
  assert(s.connected && s.ecuResponding);
  assert(std::fabs(s.engine[obd::Rpm] - 1726) < 0.01);
  assert(s.stored.valid && s.stored.count == 1 && s.stored.codes[0] == 0x0299);
  assert(s.pending.valid && s.pending.count == 0);
  assert(std::fabs(s.tyrePsi[0] - 35) < 0.01 && std::fabs(s.tyreC[0] - 24) < 0.01);
  const size_t before = fake::commands.size();
  reader.setPaused(true);
  tick(reader, 5000);
  assert(!reader.snapshot().connected && fake::commands.size() == before);
  reader.setPaused(false);
  tick(reader, 5000);
  assert(reader.snapshot().ecuResponding);
  reader.requestFaultScan();
  const auto priorScans = std::count(fake::commands.begin(), fake::commands.end(), "03\r");
  tick(reader, 5000);
  assert(std::count(fake::commands.begin(), fake::commands.end(), "03\r") > priorScans);
  fakeWifiStatus = 0;
  tick(reader, 500);
  assert(!reader.snapshot().connected && !reader.snapshot().stored.valid);
}

void test_rejected_header_never_sends_body_requests() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "ATSH726\r") return std::string("?\r>");
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 15'000);
  for (const auto& cmd : fake::commands) assert(cmd.rfind("22", 0) != 0);
  assert(fake::connects == 1 && reader.snapshot().ecuResponding);
  assert(std::string(reader.tpmsStatusText()) == "BCM TX HEADER REJECTED");
}

void test_timeout_resynchronises_and_incomplete_codes_are_unknown() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "03\r") return std::string("43020299\r>"); // count says 2, only one supplied
    if (command == "07\r") return std::string("NO DATA\r>");
    if (command == "ATSH726\r") return std::string(); // missing prompt
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 6500);
  assert(!reader.snapshot().stored.valid && !reader.snapshot().pending.valid);
  tick(reader, 5000);
  for (const auto& cmd : fake::commands) assert(cmd.rfind("22", 0) != 0);
  assert(!reader.snapshot().connected);
}

void test_supported_pid_filter_and_response_overflow() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "0100\r") return std::string("410000180000\r>"); // only RPM and speed
    if (command == "03\r") return std::string(1200, 'A') + "\r>";
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 4000);
  for (const auto& cmd : fake::commands) {
    if (cmd.rfind("01", 0) == 0) assert(cmd == "0100\r" || cmd == "010C\r" || cmd == "010D\r");
  }
  assert(!reader.snapshot().connected);
  assert(std::string(reader.lastError()) == "ELM response too long");
}

void test_explicit_rx_filters_pressure_priority_and_engine_restore() {
  reset();
  ObdReader reader;
  tick(reader, 12'000);
  assert(std::find(fake::commands.begin(), fake::commands.end(), "ATCRA72E\r") != fake::commands.end());
  assert(std::find(fake::commands.begin(), fake::commands.end(), "ATCRA7E8\r") != fake::commands.end());
  std::vector<std::string> ford;
  for (const auto& command : fake::commands)
    if (command.rfind("22", 0) == 0) ford.push_back(command);
  assert(ford.size() >= 4);
  assert(ford[0] == "222813\r" && ford[1] == "222814\r");
  assert(ford[2] == "222816\r" && ford[3] == "222815\r");
  assert(reader.snapshot().ecuResponding && std::isfinite(reader.snapshot().engine[obd::Rpm]));
  assert(std::isfinite(reader.snapshot().tyrePsi[0]));
  assert(reader.tpmsDiagnostic(0).result == ObdReader::TpmsResult::Ok);
  assert(std::string(reader.tpmsDiagnostic(0).reply) == "62281302BC");
}

void test_unsupported_rx_filter_retains_engine_and_retry() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command.rfind("ATCRA", 0) == 0) return std::string("?\r>");
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 12'000);
  for (const auto& command : fake::commands) assert(command.rfind("22", 0) != 0);
  assert(fake::connects == 1 && reader.snapshot().ecuResponding);
  assert(std::find(fake::commands.begin(), fake::commands.end(), "ATAR\r") != fake::commands.end());
  assert(std::string(reader.tpmsStatusText()) == "BCM RX FILTER REJECTED");
  assert(reader.tpmsDiagnostic(0).result == ObdReader::TpmsResult::SetupFailed);
  fake::reply = goodReply;
  reader.requestTpmsScan();
  tick(reader, 6000);
  assert(std::isfinite(reader.snapshot().tyrePsi[0]));
}

void test_pressure_diagnostics_survive_successful_engine_polls() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "222813\r") return std::string("7F2231\r>");
    if (command == "222814\r") return std::string("NO DATA\r>");
    if (command == "222816\r") return std::string("622816FFFF\r>");
    if (command == "222815\r") return std::string("6228150Z00\r>");
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 12'000);
  assert(reader.snapshot().ecuResponding);
  assert(reader.tpmsDiagnostic(0).result == ObdReader::TpmsResult::Rejected);
  assert(reader.tpmsDiagnostic(0).negativeResponse == 0x31);
  assert(std::string(reader.tpmsDiagnostic(0).reply) == "7F2231");
  assert(reader.tpmsDiagnostic(1).result == ObdReader::TpmsResult::NoData);
  assert(reader.tpmsDiagnostic(2).result == ObdReader::TpmsResult::Unavailable);
  assert(reader.tpmsDiagnostic(3).result == ObdReader::TpmsResult::Malformed);
  for (float value : reader.snapshot().tyrePsi) assert(std::isnan(value));
}

void test_response_pending_waits_for_the_same_did() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "222813\r") return std::string("7F2278\r>");
    return goodReply(command);
  };
  ObdReader reader;
  const uint32_t deadline = fakeMillis + 8000;
  while (fakeMillis < deadline &&
         std::find(fake::commands.begin(), fake::commands.end(), "222813\r") == fake::commands.end())
    tick(reader, 25);
  assert(fakeMillis < deadline);
  tick(reader, 25);
  assert(reader.tpmsDiagnostic(0).result == ObdReader::TpmsResult::Pending);
  const auto before = fake::commands.size();
  tick(reader, 500);
  assert(fake::commands.size() == before);
  for (char c : std::string("62281302BC\r>")) fake::receive.push_back(c);
  tick(reader, 25);
  assert(reader.tpmsDiagnostic(0).result == ObdReader::TpmsResult::Ok);
  assert(std::fabs(reader.snapshot().tyrePsi[0] - 35) < 0.01);
}

void test_optional_query_timeout_does_not_starve_pressures() {
  reset();
  fake::reply = [](const std::string& command) {
    if (command == "22280F\r") return std::string();
    return goodReply(command);
  };
  ObdReader reader;
  tick(reader, 24'000);
  assert(std::count(fake::commands.begin(), fake::commands.end(), "22280F\r") == 1);
  assert(fake::connects == 2);
  assert(reader.snapshot().ecuResponding && std::isfinite(reader.snapshot().tyrePsi[0]));
}

}

int main() {
  test_complete_session_and_pause();
  test_rejected_header_never_sends_body_requests();
  test_timeout_resynchronises_and_incomplete_codes_are_unknown();
  test_supported_pid_filter_and_response_overflow();
  test_explicit_rx_filters_pressure_priority_and_engine_restore();
  test_unsupported_rx_filter_retains_engine_and_retry();
  test_pressure_diagnostics_survive_successful_engine_polls();
  test_response_pending_waits_for_the_same_did();
  test_optional_query_timeout_does_not_starve_pressures();
  std::puts("OBD reader: ELM sessions, BCM receive filters, engine recovery, pressure priority, negative/pending replies, optional-query timeouts, overflow and pause tests passed");
}
