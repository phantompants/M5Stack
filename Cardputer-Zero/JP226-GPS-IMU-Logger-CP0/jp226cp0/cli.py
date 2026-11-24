"""Command line entry point.

  jp226_logger.py                      live: serial GPS -> /dev/fb0 + keys
  jp226_logger.py --headless           live, no display/keys (prints status)
  jp226_logger.py --nmea-file F --out D   replay a recorded NMEA file into CSVs
  jp226_logger.py --screenshot DIR     render every page to 320x170 PNGs
"""

import argparse
import os
import shutil
import signal
import sys
import tempfile
import time
from typing import List, Optional

from . import APP_TITLE, __version__, hardware, keys, ui
from .app import LoggerApp, replay_lines
from .clock import Clock
from .settings import default_data_dir, load_settings

DEFAULT_REPLAY_DIR = "replay_out"


def _bundled_demo() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "sample_data", "demo_drive.nmea")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jp226_logger.py",
        description="{} {} for the M5Stack CardputerZero".format(APP_TITLE, __version__))
    p.add_argument("--headless", action="store_true",
                   help="no framebuffer or keyboard; log and print status")
    p.add_argument("--nmea-file", metavar="PATH",
                   help="replay this NMEA file instead of a serial GPS "
                        "(virtual clock from the RMC times)")
    p.add_argument("--out", metavar="DIR",
                   help="data directory (default: settings data_dir, or "
                        "./{} in replay mode)".format(DEFAULT_REPLAY_DIR))
    p.add_argument("--screenshot", metavar="DIR",
                   help="render each page to a 320x170 PNG in DIR (needs "
                        "Pillow); replays --nmea-file or the bundled demo")
    p.add_argument("--speed", type=float, default=0.0, metavar="X",
                   help="replay pacing: 0 = as fast as possible (default), "
                        "1 = real time, 10 = ten times faster")
    p.add_argument("--replay-seconds", type=float, metavar="N",
                   help="stop the replay N GPS seconds after the first RMC "
                        "(e.g. to screenshot mid-drive)")
    p.add_argument("--config", metavar="FILE", help="settings file")
    p.add_argument("--gps-device", metavar="DEV", help="serial device (default: auto)")
    p.add_argument("--gps-baud", type=int, metavar="BAUD", help="baud (default: auto)")
    p.add_argument("--clock-source", choices=("auto", "gps", "system"))
    p.add_argument("--version", action="version", version=__version__)
    return p


def _read_lines(path: str) -> List[str]:
    with open(path, "r", encoding="ascii", errors="ignore") as handle:
        return handle.read().splitlines()


def run_replay_mode(args, settings) -> int:
    path = args.nmea_file or _bundled_demo()
    if not os.path.isfile(path):
        print("NMEA file not found: {}".format(path), file=sys.stderr)
        return 2
    temp: Optional[str] = None
    if args.out:
        out = args.out
    elif args.screenshot:
        temp = tempfile.mkdtemp(prefix="jp226cp0_")
        out = temp
    else:
        out = DEFAULT_REPLAY_DIR
    # Replays must not use the host's wall clock: only GPS time from the file.
    app = LoggerApp(settings, out, clock=Clock("gps"))
    try:
        app.source_info = {"device": os.path.basename(path), "baud": 0,
                           "status": "REPLAY FILE"}
        final_ms = replay_lines(app, _read_lines(path), args.speed,
                                max_seconds=args.replay_seconds)
        if args.screenshot:
            app.create_waypoint(final_ms)    # so the WAYPOINT page has content
        model = app.ui_model(final_ms)
        print("replayed {} NMEA sentences ({} bad) over {:.0f} s; state {}; "
              "{} rows -> {}".format(app.gps.sentences_ok, app.gps.sentences_bad,
                                     final_ms / 1000.0, app.machine.mode,
                                     app.telemetry.rows_written, out))
        if args.screenshot:
            if not ui.pillow_available():
                print("Pillow is not installed: cannot render screenshots "
                      "(pip install pillow)", file=sys.stderr)
                return 4
            for p in ui.save_screenshots(model, args.screenshot):
                print("wrote", p)
    finally:
        app.close()
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    return 0


def run_live(args, settings) -> int:
    from .gps_source import SerialGpsReader

    data_dir = args.out or settings.data_dir or default_data_dir()
    app = LoggerApp(settings, data_dir)
    t0 = time.monotonic()

    def now_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    reader = SerialGpsReader(now_ms, settings.gps_device, settings.gps_baud)
    reader.start()

    screen = None
    key_reader = None
    if not args.headless:
        try:
            if not ui.pillow_available():
                raise RuntimeError("Pillow is not installed")
            from .framebuffer import Framebuffer
            screen = Framebuffer(hardware.FRAMEBUFFER_DEVICE)
        except Exception as exc:  # ImportError on non-Linux, OSError, ...
            print("no framebuffer ({}); continuing headless".format(exc),
                  file=sys.stderr)
        try:
            key_reader = keys.EvdevKeys()
        except Exception as exc:
            print("no keyboard input ({})".format(exc), file=sys.stderr)

    running = {"on": True}

    def stop(_signum, _frame):
        running["on"] = False

    signal.signal(signal.SIGINT, stop)
    try:
        signal.signal(signal.SIGTERM, stop)
    except (AttributeError, ValueError):
        pass

    page = 0
    last_draw = -10_000
    last_print = 0
    try:
        while running["on"]:
            now = now_ms()
            while True:
                try:
                    stamp, line = reader.lines.get_nowait()
                except Exception:
                    break
                app.feed_line(line, stamp)
            app.source_info = {"device": reader.device, "baud": reader.baud,
                               "status": reader.status}
            app.tick(now)

            if key_reader is not None:
                for name in key_reader.poll():
                    app.last_key = name
                    shortcut_page = keys.shortcut_page_for(name)
                    if shortcut_page is not None:
                        page = ui.PAGES.index(shortcut_page)
                        if shortcut_page == "WAYPOINT":
                            app.create_waypoint(now)
                        elif shortcut_page == "OBD SCAN":
                            app.start_obd_scan()
                        continue
                    action = keys.action_for(name)
                    if action == keys.NEXT_PAGE:
                        page = (page + 1) % len(ui.PAGES)
                    elif action == keys.PREV_PAGE:
                        page = (page - 1) % len(ui.PAGES)
                    elif (page == ui.SETTINGS_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_PREV):
                        app.select_vehicle_setting(-1)
                    elif (page == ui.SETTINGS_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_NEXT):
                        app.select_vehicle_setting(1)
                    elif (page == ui.SETTINGS_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_DEC):
                        app.adjust_vehicle_setting(-1)
                    elif (page == ui.SETTINGS_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_INC):
                        app.adjust_vehicle_setting(1)
                    elif (page == ui.OBD_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_PREV):
                        app.select_obd_option(-1)
                    elif (page == ui.OBD_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_NEXT):
                        app.select_obd_option(1)
                    elif (page == ui.OBD_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_DEC):
                        app.adjust_obd_option(-1)
                    elif (page == ui.OBD_PAGE_INDEX and
                          name in hardware.KEYS_SETTING_INC):
                        app.adjust_obd_option(1)
                    elif (page == ui.DINO_PAGE_INDEX and
                          name in hardware.KEYS_DINO_JUMP):
                        app.press_dino(now)
                    elif (page == ui.DINO_PAGE_INDEX and
                          name in hardware.KEYS_DINO_TOGGLE):
                        app.toggle_dino_character()
                    elif action == keys.WAYPOINT:
                        if page == ui.SETTINGS_PAGE_INDEX:
                            app.store.put(vehicle_setup=app.vehicle)
                        elif page == ui.DINO_PAGE_INDEX:
                            app.press_dino(now)
                        elif page == ui.OBD_PAGE_INDEX:
                            app.start_obd_scan()
                        else:
                            app.create_waypoint(now)
                    elif action == keys.QUIT:
                        running["on"] = False
            draw_interval = 33 if page == ui.DINO_PAGE_INDEX else 100
            if screen is not None and now - last_draw >= draw_interval:
                last_draw = now
                screen.show(ui.render_page(page, app.ui_model(now)))
            if args.headless and now - last_print >= 10_000:
                last_print = now
                m = app.ui_model(now)
                print("{} fix={} sats={} speed={} rows={} gps={}".format(
                    m["log_state"], int(m["fix_valid"]), m["satellites"],
                    m["speed_kmh"], m["rows_written"], m["gps_status"]),
                      flush=True)
            time.sleep(0.01)
    finally:
        reader.stop()
        app.close()
        if key_reader is not None:
            key_reader.close()
        if screen is not None:
            screen.close()
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    if args.gps_device:
        settings.gps_device = args.gps_device
    if args.gps_baud:
        settings.gps_baud = args.gps_baud
    if args.clock_source:
        settings.clock_source = args.clock_source
    for warning in settings.warnings:
        print("settings: " + warning, file=sys.stderr)
    if args.nmea_file or args.screenshot:
        return run_replay_mode(args, settings)
    return run_live(args, settings)
