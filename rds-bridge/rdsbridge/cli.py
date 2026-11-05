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


def _setup_logging(level: str, log_file: str | None):
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
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
    ap.add_argument("-c", "--config", default="config.json", help="config file (default: config.json)")
    ap.add_argument("--check", action="store_true", help="validate the config and exit")
    ap.add_argument("--test-xml", metavar="FILE", help="parse a WideOrbit XML file and print what would be sent (sends nothing)")
    ap.add_argument("--station", help="with --test-xml: only this station")
    ap.add_argument("--log-level", help="DEBUG, INFO, WARNING (overrides config)")
    ap.add_argument("--no-web", action="store_true", help="do not start the web debug screen")
    ap.add_argument("--version", action="version", version=f"rdsbridge {__version__}")
    args = ap.parse_args(argv)

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

    _setup_logging(args.log_level or cfg["log_level"], cfg.get("log_file"))
    log = logging.getLogger("rdsbridge")
    from .engine import App

    monitor = Monitor(cfg["monitor_size"])
    app = App(raw, os.path.abspath(args.config), monitor)
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
            log.info("Debug / customize screen: http://%s:%s/", shown, cfg["web"]["port"])
        except OSError as exc:
            log.error("Could not start the web screen on port %s: %s", cfg["web"]["port"], exc)

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
