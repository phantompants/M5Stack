#pragma once
#include "M5GFX.h"
struct FakeKeys {
  bool esc = false, tab = false, del = false, backspace = false;
  bool enter = false, up = false, down = false;
  std::vector<char> word;
};
struct FakeKeyboard {
  FakeKeys keys;
  char key = 0;
  bool changed = false;
  bool isChange() { const bool prior = changed; changed = false; return prior; }
  bool isPressed() const { return key != 0 || keys.enter || keys.esc; }
  bool isKeyPressed(char c) const { return key == c; }
  FakeKeys& keysState() { return keys; }
};
struct FakeCardputer { FakeKeyboard Keyboard; };
inline FakeCardputer M5Cardputer;
