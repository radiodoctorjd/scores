"""Configuration loading, defaults, device presets and validation.

The *raw* config is exactly what is stored in config.json (and what the web UI
edits). ``resolve()`` turns it into a fully populated config with defaults and
device presets applied; the rest of the program only ever sees resolved config.
"""

from __future__ import annotations

import copy
import json
import os
import string


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Device presets.  Anything here can be overridden per output in config.json.
#
# NOTE: verify the ASCII command names against the manual for your firmware.
# They are only defaults; every output can define its own "commands" list.
# ---------------------------------------------------------------------------
PRESETS = {
    "fmb80": {
        # Audemat / WorldCast FMB80. The encoder's TCP or UDP port must be set to
        # the ASCII protocol in its configuration (e.g. udp1.protocol=ascii).
        # Alternatively set "protocol": "uecp" and set the port to UECP.
        "protocol": "ascii",
        "terminator": "\r\n",
        "commands": ["RT1={rt}", "DPS1={dps}"],
        "max_lengths": {"rt": 64, "dps": 128, "ps": 8},
    },
    "inovonics730": {
        # Inovonics 730 (also 720/713 style ASCII command set).
        "protocol": "ascii",
        "terminator": "\r",
        "commands": ["DPS={dps}", "TEXT={rt}"],
        "max_lengths": {"rt": 64, "dps": 128, "ps": 8},
    },
    "generic": {},
}

STATION_DEFAULTS = {
    "enabled": True,
    # Only accept events whose fields match, e.g. {"station": "WAAA"}.  Lets
    # several stations share one input (one UDP port, one HTTP endpoint...).
    "match": {},
    # Decide which events are music.  null = every event that has a title.
    "filter": None,
    "require_fields": ["title"],
    # What to do for events that are not music: "default" sends the default
    # (idle) text, "ignore" leaves the last song on air.
    "non_music_action": "default",
    # Text templates.  Each field is a list; the first template whose fields
    # are all non-empty wins.
    "templates": {
        "rt": ["{title} - {artist}", "{title}"],
        "dps": ["{title} - {artist}", "{title}"],
        "ps": [],
    },
    # Text sent when no song is playing.  May also contain {fields}.
    "default": {"rt": "", "dps": "", "ps": ""},
    "revert_after_duration": False,
    "revert_grace": 5,
    "duration_units": "auto",  # auto | ms | s
    "refresh_interval": 0,  # seconds; resend current text periodically (0 = off)
    "event_path": None,  # ElementTree path to event elements, auto-detect if null
    "event_select": "first",  # first | last | <index>
    "field_map": {},  # extra aliases: {"title": ["songname"]}
}

OUTPUT_DEFAULTS = {
    "enabled": True,
    "device": "generic",
    "protocol": "ascii",  # ascii | uecp
    "transport": "tcp",  # tcp | udp | serial
    "host": None,
    "port": None,
    "serial_port": None,
    "baudrate": 9600,
    "persistent": True,
    "connect_timeout": 5.0,
    "read_response": None,  # None = yes for tcp/serial, no for udp
    "response_timeout": 0.3,
    "terminator": "\r\n",
    "encoding": "latin-1",
    "commands": [],
    "init_commands": [],  # sent after every (re)connect, e.g. a login
    "command_delay": 0.1,
    "skip_empty": True,  # skip a command if a field it uses is empty
    "templates": {},  # per-output template overrides
    "default": {},  # per-output default-text overrides
    "max_lengths": {"rt": 64},
    "uppercase": [],  # field names to force to upper case
    "ascii_only": True,
    "uecp": {
        "site": 0,
        "encoder": 0,
        "dsn": 0,
        "psn": 0,
        "rt_field": "rt",
        "ps_field": "ps",
        "rt_transmissions": 0,
        "rt_buffer_config": 0,
    },
}

INPUT_TYPES = ("file", "udp", "tcp", "http")

WEB_DEFAULTS = {
    "enabled": True,
    "bind": "127.0.0.1",
    "port": 8080,
    "username": None,
    "password": None,
}

TOP_DEFAULTS = {
    "log_level": "INFO",
    "log_file": None,
    "monitor_size": 2000,
}


def load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}")


def save(path: str, raw: dict) -> None:
    """Write the raw config atomically, keeping the previous file as .bak."""
    text = json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    if os.path.exists(path):
        try:
            os.replace(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def check_template(template: str, where: str) -> None:
    try:
        list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ConfigError(f"{where}: bad template {template!r}: {exc}")


def _norm_templates(templates: dict, where: str) -> dict:
    out = {}
    for name, value in (templates or {}).items():
        items = [str(t) for t in _as_list(value)]
        for t in items:
            check_template(t, f"{where}.{name}")
        out[str(name).lower()] = items
    return out


def _norm_defaults(defaults: dict, where: str) -> dict:
    out = {}
    for name, value in (defaults or {}).items():
        value = "" if value is None else str(value)
        check_template(value, f"{where}.{name}")
        out[str(name).lower()] = value
    return out


def resolve_output(raw: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    device = str(raw.get("device", "generic")).lower()
    if device not in PRESETS:
        raise ConfigError(f"{where}: unknown device {device!r} (choose from {', '.join(PRESETS)})")
    cfg = _merge(_merge(OUTPUT_DEFAULTS, PRESETS[device]), raw)
    cfg["device"] = device
    cfg["name"] = str(raw.get("name") or f"{device} {cfg.get('host') or cfg.get('serial_port') or ''}".strip())

    cfg["protocol"] = str(cfg["protocol"]).lower()
    if cfg["protocol"] not in ("ascii", "uecp"):
        raise ConfigError(f"{where}: protocol must be 'ascii' or 'uecp'")
    cfg["transport"] = str(cfg["transport"]).lower()
    if cfg["transport"] in ("tcp", "udp"):
        if not cfg.get("host"):
            raise ConfigError(f"{where}: 'host' is required for {cfg['transport']}")
        if not cfg.get("port"):
            raise ConfigError(f"{where}: 'port' is required for {cfg['transport']}")
        cfg["port"] = int(cfg["port"])
    elif cfg["transport"] == "serial":
        if not cfg.get("serial_port"):
            raise ConfigError(f"{where}: 'serial_port' is required for serial (e.g. COM3 or /dev/ttyUSB0)")
    else:
        raise ConfigError(f"{where}: transport must be tcp, udp or serial")
    if cfg["read_response"] is None:
        cfg["read_response"] = cfg["transport"] != "udp"

    cfg["commands"] = [str(c) for c in _as_list(cfg["commands"])]
    cfg["init_commands"] = [str(c) for c in _as_list(cfg["init_commands"])]
    for c in cfg["commands"] + cfg["init_commands"]:
        check_template(c, f"{where}.commands")
    if cfg["protocol"] == "ascii" and not cfg["commands"]:
        raise ConfigError(f"{where}: no 'commands' defined for the ascii protocol")
    try:
        "x".encode(cfg["encoding"])
    except LookupError:
        raise ConfigError(f"{where}: unknown encoding {cfg['encoding']!r}")

    cfg["templates"] = _norm_templates(cfg["templates"], f"{where}.templates")
    cfg["default"] = _norm_defaults(cfg["default"], f"{where}.default")
    cfg["max_lengths"] = {str(k).lower(): int(v) for k, v in (cfg["max_lengths"] or {}).items() if v}
    cfg["uppercase"] = [str(u).lower() for u in _as_list(cfg["uppercase"])]
    for key in ("connect_timeout", "response_timeout", "command_delay"):
        cfg[key] = float(cfg[key])
    u = cfg["uecp"]
    for key in ("site", "encoder", "dsn", "psn", "rt_transmissions", "rt_buffer_config"):
        u[key] = int(u.get(key) or 0)
    for key in ("rt_field", "ps_field"):
        u[key] = (str(u[key]).lower() if u.get(key) else None)
    return cfg


def resolve_input(raw: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    cfg = dict(raw)
    cfg["type"] = str(cfg.get("type", "")).lower()
    if cfg["type"] not in INPUT_TYPES:
        raise ConfigError(f"{where}: type must be one of {', '.join(INPUT_TYPES)}")
    if cfg["type"] == "file":
        if not cfg.get("path"):
            raise ConfigError(f"{where}: 'path' is required for a file input")
        cfg["poll_interval"] = float(cfg.get("poll_interval", 1.0))
    else:
        if not cfg.get("port"):
            raise ConfigError(f"{where}: 'port' is required for a {cfg['type']} input")
        cfg["port"] = int(cfg["port"])
        cfg["bind"] = cfg.get("bind") or "0.0.0.0"
        if cfg["type"] == "http":
            cfg["path"] = cfg.get("path") or "/"
    return cfg


def resolve_station(raw: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: must be an object")
    if not raw.get("name"):
        raise ConfigError(f"{where}: 'name' is required")
    # templates/default are replaced as a whole when given, not merged
    cfg = _merge({k: v for k, v in STATION_DEFAULTS.items() if k not in ("templates", "default")},
                 {k: v for k, v in raw.items() if k not in ("templates", "default", "outputs", "input")})
    where = f"station '{raw['name']}'"
    cfg["name"] = str(raw["name"])
    cfg["templates"] = _norm_templates(raw.get("templates", STATION_DEFAULTS["templates"]), f"{where}.templates")
    cfg["default"] = _norm_defaults(raw.get("default", STATION_DEFAULTS["default"]), f"{where}.default")
    if "input" not in raw:
        raise ConfigError(f"{where}: 'input' is required")
    cfg["input"] = resolve_input(raw["input"], f"{where}.input")

    flt = cfg.get("filter")
    if flt is not None:
        if not isinstance(flt, dict) or not flt.get("field"):
            raise ConfigError(f"{where}.filter: needs a 'field' (e.g. \"media_type\") and 'include' and/or 'exclude' lists")
        flt["field"] = str(flt["field"]).lower()
        flt["include"] = [str(v) for v in _as_list(flt.get("include"))]
        flt["exclude"] = [str(v) for v in _as_list(flt.get("exclude"))]
    cfg["require_fields"] = [str(f).lower() for f in _as_list(cfg.get("require_fields"))]
    cfg["match"] = {str(k).lower(): [str(x) for x in _as_list(v)] for k, v in (cfg.get("match") or {}).items()}
    if cfg["non_music_action"] not in ("default", "ignore"):
        raise ConfigError(f"{where}: non_music_action must be 'default' or 'ignore'")
    if cfg["duration_units"] not in ("auto", "ms", "s"):
        raise ConfigError(f"{where}: duration_units must be auto, ms or s")
    cfg["field_map"] = {str(k).lower(): [str(a).lower() for a in _as_list(v)] for k, v in (cfg.get("field_map") or {}).items()}
    cfg["revert_grace"] = float(cfg["revert_grace"])
    cfg["refresh_interval"] = float(cfg["refresh_interval"] or 0)

    outputs = raw.get("outputs") or []
    if not isinstance(outputs, list):
        raise ConfigError(f"{where}.outputs: must be a list")
    cfg["outputs"] = [resolve_output(o, f"{where}.outputs[{i}]") for i, o in enumerate(outputs)]
    names = [o["name"] for o in cfg["outputs"]]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"{where}: duplicate output names: {', '.join(sorted(dupes))}")
    return cfg


def resolve(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    cfg = _merge(TOP_DEFAULTS, {k: v for k, v in raw.items() if k not in ("stations", "web")})
    cfg["web"] = _merge(WEB_DEFAULTS, raw.get("web") or {})
    stations = raw.get("stations")
    if not isinstance(stations, list) or not stations:
        raise ConfigError("config needs a non-empty 'stations' list")
    cfg["stations"] = [resolve_station(s, f"stations[{i}]") for i, s in enumerate(stations)]
    names = [s["name"] for s in cfg["stations"]]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"duplicate station names: {', '.join(sorted(dupes))}")

    # Stations sharing a network input must agree on its settings.
    seen = {}
    for st in cfg["stations"]:
        if not st["enabled"]:
            continue
        key = input_key(st["input"])
        if key in seen and seen[key][1] != st["input"]:
            raise ConfigError(f"stations '{seen[key][0]}' and '{st['name']}' share the same input "
                              f"({describe_input(st['input'])}) but configure it differently")
        seen.setdefault(key, (st["name"], st["input"]))
    return cfg


def describe_input(inp: dict) -> str:
    if inp["type"] == "file":
        return f"file {inp['path']}"
    if inp["type"] == "http":
        return f"http {inp['bind']}:{inp['port']}{inp['path']}"
    return f"{inp['type']} {inp['bind']}:{inp['port']}"


def input_key(inp: dict) -> tuple:
    if inp["type"] == "file":
        return ("file", os.path.normcase(os.path.abspath(inp["path"])))
    if inp["type"] == "http":
        return ("http", inp["bind"], inp["port"])
    return (inp["type"], inp["bind"], inp["port"])
