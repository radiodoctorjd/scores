"""One station: turns incoming now-playing documents into encoder updates."""

from __future__ import annotations

import fnmatch
import threading
import time

from . import wideorbit
from .config import describe_input
from .outputs import Output, build_messages, render_fields


def _matches(value: str, patterns: list) -> bool:
    value = (value or "").strip().upper()
    return any(fnmatch.fnmatchcase(value, p.strip().upper()) for p in patterns)


def accepts(cfg: dict, event: dict) -> bool:
    """Does the event belong to this station (``match`` setting)?"""
    return all(_matches(event.get(field, ""), pats) for field, pats in cfg["match"].items())


def is_music(cfg: dict, event: dict):
    """(bool, reason)"""
    for f in cfg["require_fields"]:
        if not str(event.get(f, "")).strip():
            return False, f"no {f}"
    flt = cfg.get("filter")
    if flt:
        value = event.get(flt["field"], "")
        if flt["include"] and not _matches(value, flt["include"]):
            return False, f"{flt['field']}={value!r} not in include list"
        if flt["exclude"] and _matches(value, flt["exclude"]):
            return False, f"{flt['field']}={value!r} is excluded"
    return True, "music"


def parse_events(cfg: dict, data: bytes) -> list:
    return wideorbit.parse(data, cfg.get("event_path"), cfg.get("field_map"), cfg["duration_units"])


def select_event(cfg: dict, events: list):
    """(current, next) from the station's events."""
    if not events:
        return None, None
    sel = cfg.get("event_select", "first")
    if sel == "last":
        idx = len(events) - 1
    elif sel == "first":
        idx = 0
    else:
        idx = max(0, min(len(events) - 1, int(sel)))
    nxt = events[idx + 1] if idx + 1 < len(events) else None
    return events[idx], nxt


def build_context(event: dict, nxt: dict | None) -> dict:
    ctx = dict(event)
    for k, v in (nxt or {}).items():
        ctx[f"next_{k}"] = v
    return ctx


def _identity(event: dict) -> tuple:
    return tuple(event.get(k, "") for k in ("title", "artist", "cart", "air_time"))


class Station:
    def __init__(self, cfg: dict, monitor):
        self.cfg = cfg
        self.name = cfg["name"]
        self.monitor = monitor
        self.outputs = [Output(self.name, o, monitor) for o in cfg["outputs"]]
        self.lock = threading.RLock()
        self.event = None  # context of the current event (with next_* fields)
        self.mode = "default"  # song | default
        self.mode_reason = "waiting for first event"
        self.started_at = None
        self.duration = None
        self.reverted = False
        self.last_doc_at = None
        self.last_publish_at = None
        self.override = None  # {"fields": {...}, "hold": bool, "expires_at": float|None}
        self.docs_received = 0

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        for out in self.outputs:
            out.start()

    def stop(self):
        for out in self.outputs:
            out.stop()

    def restore(self, state: dict):
        """Carry state over a live config reload."""
        with self.lock:
            for key in ("event", "mode", "mode_reason", "started_at", "duration", "reverted",
                        "last_doc_at", "override", "docs_received"):
                setattr(self, key, state.get(key))
        if self.event is not None or self.override:
            self.publish()

    def snapshot(self) -> dict:
        with self.lock:
            return {k: getattr(self, k) for k in ("event", "mode", "mode_reason", "started_at", "duration",
                                                  "reverted", "last_doc_at", "override", "docs_received")}

    # -- incoming data -----------------------------------------------------
    def handle_document(self, data: bytes, source: str, events: list | None = None):
        """``events`` may be pre-parsed when an input is shared between stations."""
        if events is None:
            try:
                events = parse_events(self.cfg, data)
            except Exception as exc:
                self.monitor.log("error", self.name, source, f"could not parse XML: {exc}", data)
                return
        mine = [e for e in events if accepts(self.cfg, e)]
        if not mine:
            if self.cfg["match"]:
                self.monitor.log("skip", self.name, source, "no event matches this station's 'match' rule")
            return
        event, nxt = select_event(self.cfg, mine)
        music, reason = is_music(self.cfg, event)
        summary = " | ".join(x for x in (event.get("title"), event.get("artist"),
                                          event.get("media_type") or event.get("category")) if x)
        shown = {k: v for k, v in event.items() if v}
        self.monitor.log("parsed", self.name, source, f"{'MUSIC' if music else 'NOT MUSIC'} ({reason}): {summary}",
                         "\n".join(f"{k} = {v}" for k, v in sorted(shown.items())))
        with self.lock:
            self.docs_received += 1
            self.last_doc_at = time.time()
            if not music and self.cfg["non_music_action"] == "ignore":
                self.monitor.log("skip", self.name, source, "not music; keeping current text (non_music_action=ignore)")
                return
            new_event = self.event is None or _identity(self.event) != _identity(event) or self.mode != (
                "song" if music else "default")
            self.event = build_context(event, nxt)
            self.mode = "song" if music else "default"
            self.mode_reason = reason
            if new_event:
                self.started_at = time.time()
                self.duration = wideorbit.parse_duration(event.get("duration"), self.cfg["duration_units"])
                self.reverted = False
                if self.override and not self.override.get("hold"):
                    self.override = None
        self.publish()

    # -- publishing ----------------------------------------------------------
    def fields_for(self, output: Output) -> dict:
        with self.lock:
            ov = self.override["fields"] if self.override else None
            return render_fields(self.cfg, output.cfg, self.event, self.mode, ov)

    def publish(self, force: bool = False):
        with self.lock:
            self.last_publish_at = time.time()
            event = self.event
        for out in self.outputs:
            out.submit(self.fields_for(out), event, force)

    def set_override(self, fields: dict, hold: bool = False, minutes: float | None = None):
        with self.lock:
            self.override = {"fields": {k.lower(): v for k, v in fields.items() if v is not None},
                             "hold": bool(hold),
                             "expires_at": time.time() + float(minutes) * 60 if minutes else None}
        self.monitor.log("info", self.name, "override", f"manual text set ({'held' if hold else 'until next event'})",
                         "\n".join(f"{k} = {v}" for k, v in fields.items()))
        self.publish(force=True)

    def clear_override(self):
        with self.lock:
            had = self.override is not None
            self.override = None
        if had:
            self.monitor.log("info", self.name, "override", "manual text cleared")
            self.publish(force=True)

    def tick(self, now: float):
        """Called about once a second by the engine."""
        republish, force = False, False
        with self.lock:
            if self.override and self.override.get("expires_at") and now >= self.override["expires_at"]:
                self.override = None
                self.monitor.log("info", self.name, "override", "manual text expired")
                republish = True
            if (self.mode == "song" and self.cfg["revert_after_duration"] and self.duration
                    and not self.reverted and self.started_at
                    and now >= self.started_at + self.duration + self.cfg["revert_grace"]):
                self.reverted = True
                self.mode, self.mode_reason = "default", "song duration elapsed"
                self.monitor.log("info", self.name, "timer", "song ended (duration elapsed); sending default text")
                republish = True
            refresh = self.cfg["refresh_interval"]
            if refresh and self.last_publish_at and now - self.last_publish_at >= refresh:
                republish, force = True, True
        if republish:
            self.publish(force=force)

    # -- previews / status -------------------------------------------------
    def preview(self, station_cfg: dict | None = None, event: dict | None = None) -> list:
        """What every output would send for the current (or a given) event, without sending."""
        cfg = station_cfg or self.cfg
        with self.lock:
            ev = event if event is not None else self.event
            ov = self.override["fields"] if self.override else None
        results = []
        for out_cfg in cfg["outputs"]:
            row = {"name": out_cfg["name"], "device": out_cfg["device"], "protocol": out_cfg["protocol"]}
            for mode in ("song", "default"):
                try:
                    fields = render_fields(cfg, out_cfg, ev, mode, ov if mode == "song" else None)
                    msgs = build_messages(out_cfg, fields, ev)
                    if out_cfg["protocol"] == "uecp":
                        from .uecp import hexdump
                        lines = [f"{label}\n    {hexdump(data)}" for label, data in msgs]
                    else:
                        lines = [label for label, _ in msgs]
                    row[mode] = {"fields": fields, "messages": lines}
                except Exception as exc:
                    row[mode] = {"error": str(exc)}
            results.append(row)
        return results

    def status(self) -> dict:
        with self.lock:
            ev = self.event or {}
            return {
                "name": self.name, "enabled": self.cfg["enabled"], "input": describe_input(self.cfg["input"]),
                "mode": "override" if self.override else self.mode, "mode_reason": self.mode_reason,
                "override": self.override, "event": {k: v for k, v in ev.items() if not k.startswith("next_")},
                "next_event": {k[5:]: v for k, v in ev.items() if k.startswith("next_")},
                "started_at": self.started_at, "duration": self.duration, "last_doc_at": self.last_doc_at,
                "docs_received": self.docs_received,
                "outputs": [o.status() for o in self.outputs],
            }
