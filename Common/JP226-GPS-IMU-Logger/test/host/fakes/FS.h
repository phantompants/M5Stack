#pragma once
#include "Arduino.h"
#define FILE_READ "r"
class File {
 public:
  explicit operator bool() const { return false; }
  bool available() const { return false; }
  String readStringUntil(char) { return String(); }
  void close() {}
};
namespace fs { class FS { public: File open(const char*, const char*) { return File{}; } }; }
