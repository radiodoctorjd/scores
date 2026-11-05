"""Command line entry point:  python -m rdsbridge --config config.json"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

from . import __version__
from . import config as config_mod
from .monitor import Monitor


def app_dir() -> str:
    """Folder that holds config.json: next to RDSBridge.exe, or the project folder."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _windows_console_setup():
    """Set the window title and turn off QuickEdit.

    With QuickEdit on, a stray click in the console window pauses all output,
    which freezes the program until someone presses a key.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetConsoleTitleW("RDS Bridge")
        handle = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            k32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)  # -QUICK_EDIT, +EXTENDED_FLAGS
    except Exception:
        pass


def _first_run_config(path: str) -> bool:
    """Create config.json from config.example.json with every encoder output disabled."""
    import json
    example = os.path.join(app_dir(), "config.example.json")
    if not os.path.exists(example):
        return False
    with open(example, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    for station in raw.get("stations", []):
        for out in station.get("outputs", []):
            out["enabled"] = False
    config_mod.save(path, raw)
    return True


def _setup_logging(level: str, log_file: str | None):
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    if sys.stdout is not None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    if log_file:
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def _test_xml(cfg: dict, path: str, station_name: str | None) -> int:
    from .station import Station, build_context, is_music, parse_events, select_event

    with open(path, "rb") as fh:
        data = fh.read()
    stations = [s for s in cfg["stations"] if not station_name or s["name"] == station_name]
    if not stations:
        print(f"No station named {station_name!r}")
        return 2
    for st_cfg in stations:
        print(f"=== Station {st_cfg['name']} ===")
        try:
            events = parse_events(st_cfg, data)
        except Exception as exc:
            print(f"  XML could not be parsed: {exc}")
            return 1
        from .station import accepts
        mine = [e for e in events if accepts(st_cfg, e)]
        print(f"  {len(events)} event(s) in document, {len(mine)} match this station")
        cur, nxt = select_event(st_cfg, mine)
        if not cur:
            continue
        music, reason = is_music(st_cfg, cur)
        print(f"  Selected event: {'MUSIC' if music else 'NOT MUSIC'} ({reason})")
        for k, v in sorted(cur.items()):
            if v:
                print(f"    {k:<18} {v}")
        station = Station(st_cfg, Monitor())
        station.event, station.mode = build_context(cur, nxt), ("song" if music else "default")
        for row in station.preview():
            print(f"  -> {row['name']} ({row['device']}, {row['protocol']})")
            mode = "song" if music else "default"
            res = row[mode]
            if "error" in res:
                print(f"     ERROR: {res['error']}")
                continue
            for m in res["messages"] or ["(nothing would be sent)"]:
                print("     " + m.replace("\n", "\n     "))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rdsbridge", description="WideOrbit now-playing to RDS encoders (Audemat FMB80, Inovonics 730)")
    ap.add_argument("-c", "--config", help="config file (default: config.json next to the program)")
    ap.add_argument("--check", action="store_true", help="validate the config and exit")
    ap.add_argument("--test-xml", metavar="FILE", help="parse a WideOrbit XML file and print what would be sent (sends nothing)")
    ap.add_argument("--station", help="with --test-xml: only this station")
    ap.add_argument("--log-level", help="DEBUG, INFO, WARNING (overrides config)")
    ap.add_argument("--no-web", action="store_true", help="do not start the web debug screen")
    ap.add_argument("--open", action="store_true", help="open the web screen in a browser after starting")
    ap.add_argument("--version", action="version", version=f"rdsbridge {__version__}")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # never crash on odd characters in a console
    args.config = os.path.abspath(args.config or os.path.join(app_dir(), "config.json"))

    created = False
    if not os.path.exists(args.config) and not (args.check or args.test_xml):
        created = _first_run_config(args.config)
        if created:
            print(f"First run: created {args.config} from config.example.json.")
            print("All encoder outputs are disabled until you edit them in the web screen (Settings tab).")
    try:
        raw = config_mod.load(args.config)
        cfg = config_mod.resolve(raw)
    except config_mod.ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.check:
        print(f"{args.config}: OK ({len(cfg['stations'])} station(s), "
              f"{sum(len(s['outputs']) for s in cfg['stations'])} output(s))")
        return 0
    if args.test_xml:
        return _test_xml(cfg, args.test_xml, args.station)

    _windows_console_setup()
    log_file = cfg.get("log_file")
    if log_file and not os.path.isabs(log_file):
        log_file = os.path.join(os.path.dirname(args.config), log_file)
    _setup_logging(args.log_level or cfg["log_level"], log_file)
    log = logging.getLogger("rdsbridge")
    from .engine import App

    monitor = Monitor(cfg["monitor_size"])
    app = App(raw, args.config, monitor)
    app.start()
    log.info("RDS Bridge %s started with %d station(s)", __version__, len(cfg["stations"]))

    server = None
    if cfg["web"]["enabled"] and not args.no_web:
        from .web import make_server, serve_in_background
        try:
            server = make_server(app, cfg["web"])
            serve_in_background(server)
            host = cfg["web"]["bind"]
            shown = "localhost" if host in ("0.0.0.0", "127.0.0.1", "") else host
            url = f"http://{shown}:{cfg['web']['port']}/"
            log.info("Debug / customize screen: %s", url)
            if args.open or created:
                import webbrowser
                webbrowser.open(url)
        except OSError as exc:
            log.error("Could not start the web screen on port %s: %s (is RDS Bridge already running?)",
                      cfg["web"]["port"], exc)
    log.info("Config: %s   (press Ctrl+C to stop)", args.config)

    done = threading.Event()

    def _quit(*_):
        done.set()

    signal.signal(signal.SIGINT, _quit)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _quit)
    if hasattr(signal, "SIGBREAK"):  # Ctrl+Break on Windows
        signal.signal(signal.SIGBREAK, _quit)
    try:
        while not done.wait(0.5):
            pass
    finally:
        log.info("Shutting down")
        if server:
            server.shutdown()
        app.stop()
    return 0
