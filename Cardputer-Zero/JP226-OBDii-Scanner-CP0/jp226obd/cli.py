"""CLI & CardputerZero framebuffer/evdev launcher for JP226 OBD-II Scanner."""

import argparse
import os
import signal
import sys
import tempfile
import shutil
import time
from typing import List, Optional

from . import APP_TITLE, __version__, elm327, hardware, keys, scanner, ui
from .app import ObdScannerApp
from .fake_elm import DEMO_PROFILES


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jp226_obd_scanner.py",
        description="{} {} for M5Stack CardputerZero".format(APP_TITLE, __version__),
    )
    p.add_argument("--headless", action="store_true",
                   help="run one scan without framebuffer/keys and print summary")
    p.add_argument("--demo", action="store_true",
                   help="use simulated OBD-II vehicle (no adapter)")
    p.add_argument("--demo-profile", choices=DEMO_PROFILES, default="ranger",
                   help="simulated vehicle profile (default: ranger)")
    p.add_argument("--link", choices=scanner.LINK_CHOICES,
                   help="adapter link (usb, wifi, ble, demo)")
    p.add_argument("--device", help="USB serial device path")
    p.add_argument("--baud", help="USB serial baud (0 = auto)")
    p.add_argument("--host", help="Wi-Fi ELM327 host (default: 192.168.0.10)")
    p.add_argument("--port", help="Wi-Fi ELM327 TCP port (default: 35000)")
    p.add_argument("--ble-name", help="BLE adapter name substring or MAC")
    p.add_argument("--protocol", choices=tuple(scanner.PROTOCOL_LABELS),
                   help="ELM327 protocol (0 = AUTO)")
    p.add_argument("--scan-mode", choices=scanner.SCAN_MODES,
                   help="quick, ev_probe, or full")
    p.add_argument("--out", metavar="DIR", help="output directory")
    p.add_argument("--config", metavar="FILE", help="config file path")
    p.add_argument("--screenshot", metavar="DIR",
                   help="run demo scan and save 320x170 PNGs to DIR")
    p.add_argument("--usb-list", action="store_true",
                   help="list detected USB serial ports and exit")
    p.add_argument("--ble-scan", action="store_true",
                   help="scan nearby BLE adapters and exit")
    p.add_argument("--web", action="store_true",
                   help="serve saved OBD logs/profiles over local Wi-Fi HTTP")
    p.add_argument("--web-port", type=int, default=8090,
                   help="Wi-Fi HTTP server port (default: 8090)")
    p.add_argument("--version", action="version", version=__version__)
    return p


def _apply_args(app: ObdScannerApp, args) -> None:
    if args.demo:
        app.link = "demo"
    elif args.link:
        app.link = args.link
    if args.demo_profile:
        app.demo_profile = args.demo_profile
    if args.protocol:
        app.protocol = args.protocol
    if args.scan_mode:
        app.scan_mode = args.scan_mode


def _override_cfg(app: ObdScannerApp, args) -> dict:
    cfg = app.build_config()
    for field in ("device", "baud", "host", "port", "ble_name", "protocol", "scan_mode"):
        val = getattr(args, field, None)
        if val is not None:
            cfg[field] = str(val)
    if args.demo:
        cfg["link"] = "demo"
        cfg["demo_profile"] = args.demo_profile
    elif args.link:
        cfg["link"] = args.link
    return cfg


def run_screenshot_mode(args) -> int:
    if not ui.pillow_available():
        print("Pillow is not installed: cannot render screenshots", file=sys.stderr)
        return 4
    temp = tempfile.mkdtemp(prefix="jp226obd_")
    app = ObdScannerApp(data_dir=args.out or temp, config_path=args.config)
    try:
        app.link = "demo"
        app.demo_profile = args.demo_profile
        app.scan_mode = args.scan_mode or "ev_probe"
        cfg = _override_cfg(app, args)
        cfg["link"] = "demo"
        app.run_scan_sync(cfg)
        app.start_web()
        model = app.ui_model(1200)
        # Populate live values on the LIVE page screenshot from the scanned PIDs.
        model["live"] = {
            "values": {
                k: v.get("value")
                for k, v in (app.profile.get("standard_pids") or {}).items()
                if isinstance(v, dict)
            },
            "hz": model["ev_fit"].get("effective_hz", 18.0),
            "latency_ms": model["ev_fit"].get("avg_latency_ms") or 18.0,
            "samples": 12,
            "error": "",
        }
        # Map PID hex keys to short names expected by _page_live
        short_vals = {}
        for item in (app.profile.get("standard_pids") or {}).values():
            if isinstance(item, dict) and item.get("short"):
                short_vals[item["short"]] = item.get("value")
        model["live"]["values"] = short_vals
        for p in ui.save_screenshots(model, args.screenshot):
            print("wrote", p)
    finally:
        app.close()
        if not args.out:
            shutil.rmtree(temp, ignore_errors=True)
    return 0


def run_headless_scan(args) -> int:
    app = ObdScannerApp(data_dir=args.out, config_path=args.config)
    try:
        _apply_args(app, args)
        cfg = _override_cfg(app, args)
        profile = app.run_scan_sync(cfg)
        if not profile:
            print("Scan failed: {}".format(app.error or app.status), file=sys.stderr)
            return 2
        lf = profile["logger_fit"]
        ev = profile["ev_sound_fit"]
        print("Status: {} | Link: {} | Protocol: {}".format(
            app.status, app.link_name, profile["adapter"]["protocol"]))
        print("Logger fit: {}/{} core metrics | TPMS: {}".format(
            lf["supported_count"], lf["total_count"],
            "YES" if lf["tpms_ready"] else "NO"))
        print("EV Sound fit: {} ({}/{} channels, {} Hz, multi-PID: {})".format(
            ev["grade"], ev["available_channels"], ev["total_channels"],
            ev["effective_hz"], "YES" if ev["multi_pid_supported"] else "NO"))
        print("Profile: {}".format(os.path.join(app.data_dir, app.profile_file)))
        return 0 if app.status == "COMPLETE" else 1
    finally:
        app.close()


def _run_page_action(app: ObdScannerApp, page_action: str, page: int,
                     now_ms: int = 0) -> int:
    if page_action == keys.CYCLE_LINK:
        app.cycle_connect_row(0, 1)
    elif page_action == keys.CYCLE_TARGET:
        app.cycle_connect_row(1, 1)
    elif page_action == keys.CYCLE_PROTOCOL:
        app.cycle_connect_row(2, 1)
    elif page_action == keys.CYCLE_MODE:
        app.cycle_connect_row(3, 1)
    elif page_action == keys.TOGGLE_SCAN:
        app.start_or_stop_scan()
        if page == ui.CONNECT_PAGE_INDEX and app.is_scanning():
            return ui.SCAN_PAGE_INDEX
    elif page_action == keys.TOGGLE_LIVE:
        app.toggle_live()
    elif page_action == keys.GO_LIVE:
        if not app.live_active and not app.is_scanning():
            app.toggle_live()
        return ui.LIVE_PAGE_INDEX
    elif page_action == keys.GO_MODULES:
        return ui.PAGES.index("MODULES")
    elif page_action == keys.EV_SCAN:
        app.scan_mode = "ev_probe"
        app.store.put(scan_mode=app.scan_mode)
        app.start_or_stop_scan()
        return ui.SCAN_PAGE_INDEX
    elif page_action == keys.FULL_SCAN:
        app.scan_mode = "full"
        app.store.put(scan_mode=app.scan_mode)
        app.start_or_stop_scan()
        return ui.SCAN_PAGE_INDEX
    elif page_action == keys.SCROLL_UP:
        app.scroll_modules(-1)
    elif page_action == keys.SCROLL_DOWN:
        app.scroll_modules(1)
    elif page_action == keys.REFRESH_DEVS:
        app.refresh_devices()
    elif page_action == keys.TOGGLE_WEB:
        app.toggle_web()
    elif page_action == keys.CYCLE_WEB_PORT:
        app.cycle_web_port()
    elif page_action == keys.GO_SHARE:
        return ui.PAGES.index("SHARE")
    elif page_action == keys.GO_DINO:
        return ui.DINO_PAGE_INDEX
    elif page_action == keys.DINO_JUMP:
        app.press_dino(now_ms)
    elif page_action == keys.DINO_TOGGLE:
        app.toggle_dino_character()
    return page


def run_web_only(args) -> int:
    app = ObdScannerApp(data_dir=args.out, config_path=args.config)
    app.web_port = args.web_port
    app.start_web()
    if not app.web.running:
        print("Failed to start web server: {}".format(app.web.error), file=sys.stderr)
        app.close()
        return 2
    ip_url, local_url = app.web.urls()
    print("Serving OBD logs from {}".format(app.data_dir))
    print("Open in iPhone Safari: {}  or  {}".format(ip_url, local_url))
    running = {"on": True}

    def _stop(_signum, _frame):
        running["on"] = False

    signal.signal(signal.SIGINT, _stop)
    try:
        while running["on"]:
            time.sleep(0.5)
    finally:
        app.close()
    return 0


def run_live_ui(args) -> int:
    app = ObdScannerApp(data_dir=args.out, config_path=args.config)
    _apply_args(app, args)
    if args.web_port:
        app.web_port = args.web_port
    app.start_web()

    screen = None
    key_reader = None
    try:
        if not ui.pillow_available():
            raise RuntimeError("Pillow is not installed")
        from .framebuffer import Framebuffer
        screen = Framebuffer(hardware.FRAMEBUFFER_DEVICE)
    except Exception as exc:
        print("no framebuffer ({}); running headless scan".format(exc), file=sys.stderr)
        app.close()
        return run_headless_scan(args)

    try:
        key_reader = keys.EvdevKeys()
    except Exception as exc:
        print("no keyboard input ({})".format(exc), file=sys.stderr)

    running = {"on": True}

    def _stop(_signum, _frame):
        running["on"] = False

    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass

    t0 = time.monotonic()
    page = 0
    last_draw = -10_000
    try:
        while running["on"]:
            now_ms = int((time.monotonic() - t0) * 1000)
            app.tick_dino(now_ms)
            if key_reader is not None:
                for name in key_reader.poll():
                    app.last_key = name
                    page_act = keys.page_action_for(ui.PAGES[page], name)
                    if page_act is not None:
                        page = _run_page_action(app, page_act, page, now_ms)
                        continue
                    act = keys.action_for(name)
                    if act == keys.NEXT_PAGE:
                        page = (page + 1) % len(ui.PAGES)
                    elif act == keys.PREV_PAGE:
                        page = (page - 1) % len(ui.PAGES)
                    elif act == keys.UP:
                        if page == ui.CONNECT_PAGE_INDEX:
                            app.select_connect_row(-1)
                        elif page == ui.MODULES_PAGE_INDEX:
                            app.scroll_modules(-1)
                    elif act == keys.DOWN:
                        if page == ui.CONNECT_PAGE_INDEX:
                            app.select_connect_row(1)
                        elif page == ui.MODULES_PAGE_INDEX:
                            app.scroll_modules(1)
                    elif act == keys.DEC and page == ui.CONNECT_PAGE_INDEX:
                        app.adjust_connect_row(-1)
                    elif act == keys.INC and page == ui.CONNECT_PAGE_INDEX:
                        app.adjust_connect_row(1)
                    elif act == keys.REFRESH:
                        app.refresh_devices()
                    elif page == ui.DINO_PAGE_INDEX and name in hardware.KEYS_DINO_JUMP:
                        app.press_dino(now_ms)
                    elif page == ui.DINO_PAGE_INDEX and name in hardware.KEYS_DINO_TOGGLE:
                        app.toggle_dino_character()
                    elif act == keys.ACTION:
                        if page == ui.LIVE_PAGE_INDEX:
                            app.toggle_live()
                        elif page == ui.DINO_PAGE_INDEX:
                            app.press_dino(now_ms)
                        else:
                            app.start_or_stop_scan()
                            if page == ui.CONNECT_PAGE_INDEX and app.is_scanning():
                                page = ui.SCAN_PAGE_INDEX
                    elif act == keys.QUIT:
                        running["on"] = False

            draw_interval = 33 if page in (ui.DINO_PAGE_INDEX, ui.FOSSIL_PAGE_INDEX) else 100
            if now_ms - last_draw >= draw_interval:
                last_draw = now_ms
                screen.show(ui.render_page(page, app.ui_model(now_ms)))
            time.sleep(0.01)
    finally:
        app.close()
        if key_reader is not None:
            key_reader.close()
        if screen is not None:
            screen.close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.usb_list:
        ports = elm327.find_usb_devices()
        print("\n".join(ports) if ports else "No USB serial adapters found.")
        return 0
    if args.ble_scan:
        try:
            devices = elm327.discover_ble_adapters(scan_s=5.0)
        except elm327.LinkError as exc:
            print("BLE scan failed: {}".format(exc), file=sys.stderr)
            return 2
        for name, addr in devices:
            print("{}  {}".format(addr, name))
        if not devices:
            print("No BLE devices discovered.")
        return 0
    if args.screenshot:
        return run_screenshot_mode(args)
    if args.web:
        return run_web_only(args)
    if args.headless:
        return run_headless_scan(args)
    return run_live_ui(args)
