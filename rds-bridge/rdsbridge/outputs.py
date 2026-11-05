"""Encoder outputs: render text, build commands/frames, deliver them.

Each output has its own worker thread, so a slow or offline encoder never
delays the others.  Only the newest update is kept while an encoder is down
(no backlog of stale song titles is replayed when it comes back).
"""

from __future__ import annotations

import threading
import time

from . import templates as tpl
from . import uecp
from .transports import TransportError, make_transport


def render_fields(station_cfg: dict, output_cfg: dict, event: dict | None, mode: str,
                  override: dict | None = None) -> dict:
    """The named text fields (rt, dps, ps, ...) this output should carry."""
    templates = dict(station_cfg["templates"])
    templates.update(output_cfg.get("templates") or {})
    defaults = dict(station_cfg["default"])
    defaults.update(output_cfg.get("default") or {})
    ctx = {k: tpl.clean(v, False) for k, v in (event or {}).items()}
    ctx.setdefault("station_name", station_cfg["name"])
    ascii_only = output_cfg.get("ascii_only", True)
    out = {}
    for name in list(templates) + [d for d in defaults if d not in templates]:
        value = tpl.render_first(templates.get(name, []), ctx) if mode == "song" else ""
        if not value:
            value = tpl.render(defaults.get(name, ""), ctx)
        if override and override.get(name) is not None and str(override[name]).strip():
            value = str(override[name])
        value = tpl.clean(value, ascii_only)
        if name in output_cfg.get("uppercase", []):
            value = value.upper()
        out[name] = tpl.truncate(value, output_cfg.get("max_lengths", {}).get(name))
    return out


def build_messages(output_cfg: dict, fields: dict, event: dict | None, ab_flag: int = 0) -> list:
    """List of (description, bytes) to send, in order."""
    if output_cfg["protocol"] == "uecp":
        u = output_cfg["uecp"]
        msgs = []
        if u.get("ps_field") and fields.get(u["ps_field"]):
            text = fields[u["ps_field"]]
            frame = uecp.frame(uecp.ps_message(text, u["dsn"], u["psn"]), u["site"], u["encoder"])
            msgs.append((f"UECP PS  '{text[:8]}'", frame))
        if u.get("rt_field") and u["rt_field"] in fields:
            text = fields[u["rt_field"]]
            if text or not output_cfg.get("skip_empty", True):
                msg = uecp.rt_message(text, u["dsn"], u["psn"], u["rt_transmissions"], ab_flag,
                                      u["rt_buffer_config"])
                msgs.append((f"UECP RT  '{text}'", uecp.frame(msg, u["site"], u["encoder"])))
        return msgs
    return [(cmd, encode_command(output_cfg, cmd)) for cmd in render_commands(output_cfg, fields, event)]


def render_commands(output_cfg: dict, fields: dict, event: dict | None, which: str = "commands") -> list:
    ctx = {k: tpl.clean(v, output_cfg.get("ascii_only", True)) for k, v in (event or {}).items()}
    ctx.update(fields)
    cmds = []
    for template in output_cfg[which]:
        used = tpl.fields_in(template)
        if output_cfg.get("skip_empty", True) and any(not str(ctx.get(f, "")).strip() for f in used):
            continue
        cmds.append(tpl.render(template, ctx))
    return cmds


def encode_command(output_cfg: dict, cmd: str) -> bytes:
    return (cmd + output_cfg["terminator"]).encode(output_cfg["encoding"], errors="replace")


class Output:
    def __init__(self, station_name: str, cfg: dict, monitor):
        self.station = station_name
        self.cfg = cfg
        self.name = cfg["name"]
        self.monitor = monitor
        self.transport = make_transport(cfg)
        self._cv = threading.Condition()
        self._pending = None
        self._stop = threading.Event()
        self._thread = None
        self._last_sent = None
        self._last_rt = None
        self._ab = 0
        self.state = "idle"  # idle | ok | error | disabled
        self.last_error = ""
        self.last_sent_at = None
        self.last_fields = {}
        self.last_messages = []
        self.last_response = ""
        self.sent_count = 0
        self.error_count = 0
        if not cfg["enabled"]:
            self.state = "disabled"

    @property
    def endpoint(self):
        return f"{self.name} ({self.transport.describe})"

    def start(self):
        if not self.cfg["enabled"]:
            return
        self._thread = threading.Thread(target=self._run, name=f"out:{self.station}:{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=5)
        self.transport.close()

    def submit(self, fields: dict, event: dict | None, force: bool = False):
        if not self.cfg["enabled"]:
            return
        with self._cv:
            prev_force = self._pending[2] if self._pending else False
            self._pending = (fields, event, force or prev_force)
            self._cv.notify()

    def _run(self):
        backoff = 0.0
        while not self._stop.is_set():
            with self._cv:
                while self._pending is None and not self._stop.is_set():
                    self._cv.wait(1.0)
                if self._stop.is_set():
                    break
                fields, event, force = self._pending
                self._pending = None
            try:
                self._deliver(fields, event, force)
                backoff = 0.0
            except Exception as exc:  # keep the worker alive no matter what
                self.state = "error"
                self.last_error = str(exc)
                self.error_count += 1
                self.monitor.log("error", self.station, self.endpoint, str(exc))
                self.transport.close()
                with self._cv:
                    if self._pending is None:
                        self._pending = (fields, event, True)
                backoff = min(30.0, max(1.0, backoff * 2))
                self._stop.wait(backoff)

    def _deliver(self, fields: dict, event: dict | None, force: bool):
        rt = fields.get((self.cfg["uecp"].get("rt_field") or "rt"))
        ab = self._ab ^ 1 if (rt != self._last_rt and self._last_rt is not None) else self._ab
        messages = build_messages(self.cfg, fields, event, ab)
        payload = [m[1] for m in messages]
        if not force and payload == self._last_sent:
            return
        if not messages:
            self.monitor.log("skip", self.station, self.endpoint, "nothing to send (all fields empty)")
            return
        persistent = self.cfg["persistent"]
        try:
            self._send_all(messages)
        except TransportError:
            if not persistent:
                raise
            # a persistent connection may have gone stale: reconnect once and retry
            self.transport.close()
            self._send_all(messages)
        finally:
            if not persistent:
                self.transport.close()
        self._ab, self._last_rt = ab, rt
        self._last_sent = payload
        self.last_fields = dict(fields)
        self.last_messages = [m[0] for m in messages]
        self.last_sent_at = time.time()
        self.sent_count += 1
        self.state, self.last_error = "ok", ""

    def _send_all(self, messages):
        if not self.transport.is_open:
            self.transport.open()
            self.monitor.log("info", self.station, self.endpoint, "connected")
            for cmd in render_commands(self.cfg, {}, None, "init_commands"):
                self._send_one(cmd, encode_command(self.cfg, cmd))
        for i, (label, data) in enumerate(messages):
            if i and self.cfg["command_delay"] > 0:
                time.sleep(self.cfg["command_delay"])
            self._send_one(label, data)

    def _send_one(self, label: str, data: bytes):
        self.transport.write(data)
        detail = uecp.hexdump(data) if self.cfg["protocol"] == "uecp" else repr(data)[1:]
        self.monitor.log("out", self.station, self.endpoint, label, detail)
        if self.cfg["read_response"]:
            resp = self.transport.read(self.cfg["response_timeout"])
            if resp:
                text = resp.decode("latin-1").strip() or uecp.hexdump(resp)
                self.last_response = text
                self.monitor.log("resp", self.station, self.endpoint, text[:120], repr(resp)[1:])

    def status(self) -> dict:
        return {
            "name": self.name, "device": self.cfg["device"], "protocol": self.cfg["protocol"],
            "endpoint": self.transport.describe, "enabled": self.cfg["enabled"], "state": self.state,
            "connected": self.transport.is_open, "last_error": self.last_error,
            "last_sent_at": self.last_sent_at, "last_fields": self.last_fields,
            "last_messages": self.last_messages, "last_response": self.last_response,
            "sent_count": self.sent_count, "error_count": self.error_count,
        }
