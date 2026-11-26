#pragma once

// LovyanGFX setup for the ESP32-2432S028R "Cheap Yellow Display".
//
// Display: ILI9341 or ST7789 (boards ship with either) on HSPI (SCK 14,
// MOSI 13, MISO 12, CS 15, DC 2), backlight on G21. begin() reads the
// panel's ID and picks the right driver. Touch: XPT2046 on its own pins
// (SCK 25, MOSI 32, MISO 39, CS 33, IRQ 36), driven on VSPI.

#define LGFX_USE_V1
#include <LovyanGFX.hpp>

namespace cyd {

constexpr int kRedLedPin = 4;    // RGB LED, active low
constexpr int kGreenLedPin = 16;
constexpr int kBlueLedPin = 17;

class Display : public lgfx::LGFX_Device {
 public:
  Display() {
    {
      auto cfg = bus_.config();
      cfg.spi_host = HSPI_HOST;
      cfg.spi_mode = 0;
      cfg.freq_write = 27000000;
      cfg.freq_read = 16000000;
      cfg.spi_3wire = false;
      cfg.use_lock = true;
      cfg.dma_channel = SPI_DMA_CH_AUTO;
      cfg.pin_sclk = 14;
      cfg.pin_mosi = 13;
      cfg.pin_miso = 12;
      cfg.pin_dc = 2;
      bus_.config(cfg);
      panel_.setBus(&bus_);
      st7789_.setBus(&bus_);
    }
    for (lgfx::Panel_LCD* panel :
         {static_cast<lgfx::Panel_LCD*>(&panel_),
          static_cast<lgfx::Panel_LCD*>(&st7789_)}) {
      auto cfg = panel->config();
      cfg.pin_cs = 15;
      cfg.pin_rst = -1;
      cfg.pin_busy = -1;
      cfg.panel_width = 240;
      cfg.panel_height = 320;
      cfg.offset_rotation = 0;
      cfg.readable = true;
      cfg.invert = false;
      cfg.rgb_order = false;
      cfg.dlen_16bit = false;
      cfg.bus_shared = false;
      panel->config(cfg);
    }
    {
      auto cfg = light_.config();
      cfg.pin_bl = 21;
      cfg.invert = false;
      cfg.freq = 44100;
      cfg.pwm_channel = 7;
      // Attached in begin() to the chosen panel only: a second init() would
      // try to claim another PWM timer for the same pin.
      light_.config(cfg);
    }
    {
      auto cfg = touch_.config();
      cfg.x_min = 300;
      cfg.x_max = 3900;
      cfg.y_min = 200;
      cfg.y_max = 3700;
      cfg.pin_int = 36;
      cfg.bus_shared = false;
      cfg.offset_rotation = 0;
      cfg.spi_host = VSPI_HOST;
      cfg.freq = 1000000;
      cfg.pin_sclk = 25;
      cfg.pin_mosi = 32;
      cfg.pin_miso = 39;
      cfg.pin_cs = 33;
      touch_.config(cfg);
      panel_.setTouch(&touch_);
      st7789_.setTouch(&touch_);
    }
    setPanel(&panel_);
  }

  // Raw read of a panel register, first byte in the low bits (byte 0 is
  // the dummy clock cycle).
  uint32_t readRegister(uint8_t command) {
    startWrite();
    const uint32_t value = panel()->readCommand(command, 0, 4);
    endWrite();
    return value;
  }

  // Starts the display with the driver that matches the panel. RDDID (0x04)
  // on an ST7789 reads 85 85 52; an ILI9341 reads 00 93 41 from 0xD3.
  uint32_t begin() {
    init();
    rddid_ = readRegister(0x04);
    id4_ = readRegister(0xD3);
    // Only a panel that answers 0xD3 with 93 41 is an ILI9341. The ST7789
    // boards seen so far read back noise (MISO is unreliable on them), so
    // anything else gets the ST7789 driver.
    const bool ili9341 = (id4_ >> 16) == 0x4193;
    if (!ili9341) {
      setPanel(&st7789_);
      init();
      isSt7789_ = true;
    }
    panel()->setLight(&light_);
    light_.init(200);
    return rddid_;
  }

  uint32_t id4() const { return id4_; }

  bool isSt7789() const { return isSt7789_; }

 private:
  bool isSt7789_ = false;
  uint32_t rddid_ = 0;
  uint32_t id4_ = 0;
  lgfx::Panel_ST7789 st7789_;
  lgfx::Panel_ILI9341 panel_;
  lgfx::Bus_SPI bus_;
  lgfx::Light_PWM light_;
  lgfx::Touch_XPT2046 touch_;
};

}  // namespace cyd
