"""Builds stations, inputs and outputs from config; supports live reload."""

from __future__ import annotations

import copy
import logging
import threading
import time

from . import config as config_mod
from .inputs import make_input
from .station import Station, parse_events

log = logging.getLogger("rdsbridge")


class Engine:
    def __init__(self, raw_config: dict, monitor):
        self.raw = copy.deepcopy(raw_config)
        self.cfg = config_mod.resolve(raw_config)
        self.monitor = monitor
        self.stations = {}
        self.inputs = {}
        self._stop = threading.Event()
        self._ticker = None
        self.started_at = None

    def start(self, previous_state: dict | None = None):
        for st_cfg in self.cfg["stations"]:
            station = Station(st_cfg, self.monitor)
            self.stations[station.name] = station
            if not st_cfg["enabled"]:
                continue
            key = config_mod.input_key(st_cfg["input"])
            if key not in self.inputs:
                self.inputs[key] = make_input(st_cfg["input"], self.monitor)
            self.inputs[key].listeners.append(station.handle_document)
            station.start()
            if previous_state and station.name in previous_state:
                station.restore(previous_state[station.name])
        for inp in self.inputs.values():
            try:
                inp.start()
                self.monitor.log("info", "", inp.describe, "input started")
            except OSError as exc:
                inp.state, inp.last_error = "error", str(exc)
                self.monitor.log("error", "", inp.describe, f"cannot start input: {exc}")
        self.started_at = time.time()
        self._ticker = threading.Thread(target=self._tick, name="ticker", daemon=True)
        self._ticker.start()

    def stop(self):
        self._stop.set()
        for inp in self.inputs.values():
            inp.stop()
        for inp in self.inputs.values():
            inp.join()
        for st in self.stations.values():
            st.stop()

    def snapshot(self) -> dict:
        return {name: st.snapshot() for name, st in self.stations.items()}

    def _tick(self):
        while not self._stop.wait(1.0):
            now = time.time()
            for st in self.stations.values():
                if st.cfg["enabled"]:
                    try:
                        st.tick(now)
                    except Exception as exc:
                        log.exception("tick failed for %s: %s", st.name, exc)

    def station(self, name: str) -> Station:
        if name not in self.stations:
            raise KeyError(f"no station named {name!r}")
        return self.stations[name]

    def inject(self, name: str, xml: str):
        """Feed XML to one station as if it came from WideOrbit (testing)."""
        data = xml.encode("utf-8")
        self.monitor.log("in", name, "test injection", f"{len(data)} bytes (manual test)", data)
        self.station(name).handle_document(data, "test injection")

    def preview(self, name: str, raw_station: dict | None = None, xml: str | None = None) -> dict:
        station = self.station(name)
        st_cfg = config_mod.resolve_station(raw_station, "station") if raw_station else station.cfg
        event = None
        if xml:
            from .station import build_context, select_event
            events = parse_events(st_cfg, xml.encode("utf-8"))
            cur, nxt = select_event(st_cfg, events)
            event = build_context(cur, nxt) if cur else None
        return {"event": event if event is not None else station.event, "outputs": station.preview(st_cfg, event)}

    def status(self) -> dict:
        return {
            "started_at": self.started_at,
            "stations": [st.status() for st in self.stations.values()],
            "inputs": [i.status() for i in self.inputs.values()],
        }


class App:
    """Holds the running engine; the web UI talks to this."""

    def __init__(self, raw_config: dict, config_path: str | None, monitor):
        self.config_path = config_path
        self.monitor = monitor
        self.lock = threading.RLock()
        self.engine = Engine(raw_config, monitor)
        self.started_at = time.time()

    def start(self):
        self.engine.start()

    def stop(self):
        self.engine.stop()

    def apply_config(self, raw: dict, save: bool = True):
        """Validate, save and hot-reload. Raises ConfigError on bad config."""
        config_mod.resolve(raw)  # validate before touching anything
        with self.lock:
            state = self.engine.snapshot()
            old_raw = self.engine.raw
            self.engine.stop()
            try:
                new = Engine(raw, self.monitor)
                new.start(state)
            except Exception as exc:
                self.monitor.log("error", "", "config", f"reload failed, restoring previous config: {exc}")
                new = Engine(old_raw, self.monitor)
                new.start(state)
                self.engine = new
                raise
            self.engine = new
            if save and self.config_path:
                config_mod.save(self.config_path, raw)
        self.monitor.log("info", "", "config", "configuration applied" + (" and saved" if save else ""))
