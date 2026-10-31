#pragma once
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <string>

inline uint32_t fakeMillis = 0;
inline uint32_t millis() { return fakeMillis; }
class String {
 public:
  String(const char* s = "") : s_(s) {}
  explicit String(std::string s) : s_(std::move(s)) {}
  bool isEmpty() const { return s_.empty(); }
  bool startsWith(const char* prefix) const { return s_.rfind(prefix, 0) == 0; }
  int indexOf(char c) const { const auto i = s_.find(c); return i == std::string::npos ? -1 : int(i); }
  String substring(size_t start, size_t end = std::string::npos) const { return String(s_.substr(start, end == std::string::npos ? end : end - start)); }
  void trim() {
    const auto first = s_.find_first_not_of(" \r\n\t");
    s_ = first == std::string::npos ? "" : s_.substr(first, s_.find_last_not_of(" \r\n\t") - first + 1);
  }
  void toLowerCase() { for (char& c : s_) c = char(std::tolower(static_cast<unsigned char>(c))); }
  size_t length() const { return s_.size(); }
  const char* c_str() const { return s_.c_str(); }
  void toCharArray(char* out, size_t cap) const { if (cap) { std::strncpy(out, s_.c_str(), cap - 1); out[cap - 1] = 0; } }
  bool operator==(const char* other) const { return s_ == other; }
  bool operator==(const String& other) const { return s_ == other.s_; }
  String operator+(const String& other) const { return String(s_ + other.s_); }
  String& operator+=(char c) { s_ += c; return *this; }
  void setCharAt(size_t i, char c) { s_.at(i) = c; }
  void remove(size_t i) { s_.erase(i); }
 private:
  std::string s_;
};
inline String operator+(const char* lhs, const String& rhs) { return String(lhs) + rhs; }
struct FakeSerial { template<class... Args> void printf(const char*, Args...) {} };
inline FakeSerial Serial;
