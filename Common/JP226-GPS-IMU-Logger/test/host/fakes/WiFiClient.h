#pragma once
#include <deque>
#include <functional>
#include <string>
#include <vector>

namespace fake {
inline bool connected = false;
inline unsigned connects = 0;
inline std::deque<char> receive;
inline std::vector<std::string> commands;
inline std::function<std::string(const std::string&)> reply;
inline void reset() { connected = false; connects = 0; receive.clear(); commands.clear(); }
}
class WiFiClient {
 public:
  void stop() { fake::connected = false; fake::receive.clear(); }
  bool connected() const { return fake::connected; }
  void setTimeout(unsigned) {}
  bool connect(const char*, uint16_t, int) { ++fake::connects; fake::connected = true; return true; }
  int available() const { return int(fake::receive.size()); }
  int read() { char c = fake::receive.front(); fake::receive.pop_front(); return c; }
  size_t print(const char* text) {
    const std::string command(text);
    fake::commands.push_back(command);
    const std::string answer = fake::reply ? fake::reply(command) : "";
    for (char c : answer) fake::receive.push_back(c);
    return command.size();
  }
};
