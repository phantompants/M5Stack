#pragma once
#include "Arduino.h"
#include <cstdio>
#include <vector>
constexpr int TFT_BLACK = 0, TFT_WHITE = 1, TFT_CYAN = 2, TFT_GREEN = 3, TFT_ORANGE = 4;
constexpr int top_left = 0;
inline std::vector<std::string> fakeScreenText;
class M5Canvas {
 public:
  const void* getBuffer() const { return &frame_; }
  size_t bufferLength() const { return sizeof(frame_); }
  void startWrite() {}
  void endWrite() {}
  void fillScreen(int) { ++frame_; fakeScreenText.clear(); }
  void setTextDatum(int) {}
  void setTextWrap(bool) {}
  void setTextSize(int) {}
  void setCursor(int, int) {}
  void setTextColor(int, int) {}
  void drawFastHLine(int, int, int, int) {}
  int width() const { return 240; }
  void println(const String& text) { fakeScreenText.emplace_back(text.c_str()); }
  void drawString(const String& text, int, int) { fakeScreenText.emplace_back(text.c_str()); }
  template<class... Args> void printf(const char* fmt, Args... args) {
    char text[256]; std::snprintf(text, sizeof(text), fmt, args...); fakeScreenText.emplace_back(text);
  }
  void pushSprite(int, int) {}
 private:
  uint32_t frame_ = 0;
};
