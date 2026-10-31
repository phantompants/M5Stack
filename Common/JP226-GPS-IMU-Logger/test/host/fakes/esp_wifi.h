#pragma once
inline unsigned fakeScanStops = 0;
inline int esp_wifi_scan_stop() { ++fakeScanStops; return 0; }
