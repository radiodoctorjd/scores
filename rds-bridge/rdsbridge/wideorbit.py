"""Parse WideOrbit (WO Automation for Radio) now-playing XML.

WideOrbit's XML export is configurable, so instead of hard-coding one schema
this parser:

* accepts a single event (``<nowplaying><title>..</title>..</nowplaying>``) or
  several events inside a container (``<events><event>..</event>..</events>``),
* flattens every leaf element and attribute into a lower-case field dict, so
  any field WideOrbit sends can be used in a template as ``{field_name}``,
* adds canonical fields (title, artist, album, cart, category, media_type,
  duration, station) from common aliases, and
* tolerates the usual real-world problems: UTF-8 BOM, Windows-1252 bytes in
  a file declared as UTF-8, bare ``&`` characters and stray control chars.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

ALIASES = {
    "title": ["title", "song", "songtitle", "song_title", "cut_title", "name"],
    "artist": ["artist", "performer", "artist_name", "artists"],
    "album": ["album", "album_title", "cd"],
    "cart": ["cart", "cart_number", "cartnumber", "cut", "cut_id", "media_id", "id"],
    "category": ["category", "cat", "group"],
    "media_type": ["media_type", "mediatype", "type", "event_type"],
    "duration": ["duration", "length", "len", "runtime", "total_length"],
    "station": ["station", "station_name", "callsign", "call_letters", "service"],
    "air_time": ["air_time", "airtime", "start_time", "starttime"],
}

_BARE_AMP = re.compile(rb"&(?!(?:#[0-9]+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9._-]*);)")
_BAD_CTRL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")


class ParseError(ValueError):
    pass


def parse_xml(data: bytes) -> ET.Element:
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = data.strip(b"\x00 \t\r\n")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if not data:
        raise ParseError("empty document")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        first = exc
    fixed = _BAD_CTRL.sub(b"", _BARE_AMP.sub(b"&amp;", data))
    try:
        return ET.fromstring(fixed)
    except ET.ParseError:
        pass
    # Probably Windows-1252 text in a document declared (or assumed) UTF-8.
    try:
        text = fixed.decode("utf-8")
    except UnicodeDecodeError:
        text = fixed.decode("cp1252", errors="replace")
    try:
        return ET.fromstring(_XML_DECL.sub("", text))
    except ET.ParseError:
        raise ParseError(f"invalid XML: {first}")


def _local(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def flatten(elem: ET.Element) -> dict:
    """All attributes and leaf element texts below ``elem`` as {name: text}."""
    fields = {}
    for key, value in elem.attrib.items():
        fields[_local(key)] = (value or "").strip()
    for child in elem.iter():
        if child is elem:
            continue
        name = _local(child.tag)
        if len(child) == 0:
            fields.setdefault(name, (child.text or "").strip())
        for key, value in child.attrib.items():
            fields.setdefault(f"{name}_{_local(key)}", (value or "").strip())
    return fields


def _looks_like_event(elem: ET.Element, title_names: set) -> bool:
    if any(_local(k) in title_names for k in elem.attrib):
        return True
    return any(len(c) == 0 and _local(c.tag) in title_names for c in elem)


def find_events(root: ET.Element, event_path: str | None = None, title_names=None) -> list:
    if event_path:
        found = root.findall(event_path)
        if not found and _local(root.tag) == event_path.strip("./").lower():
            found = [root]
        return found
    title_names = set(title_names or ALIASES["title"]) | set(ALIASES["artist"])
    level = [root]
    while level:
        hits = [e for e in level if _looks_like_event(e, title_names)]
        if hits:
            return hits
        level = [c for e in level for c in e if len(c) > 0 or c.attrib]
    return [root]


def parse_duration(value, units: str = "auto"):
    """Seconds as float, from '3:45', '00:03:45.500', '225', '225000' (ms)."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    try:
        if ":" in v:
            seconds = 0.0
            for part in v.split(":"):
                seconds = seconds * 60 + float(part)
        else:
            seconds = float(v)
            if units == "ms" or (units == "auto" and seconds >= 10000):
                seconds /= 1000.0
    except ValueError:
        return None
    return seconds if 0 < seconds < 86400 else None


def normalize(fields: dict, field_map: dict | None = None, duration_units: str = "auto") -> dict:
    out = dict(fields)
    aliases = {k: list(v) for k, v in ALIASES.items()}
    for key, extra in (field_map or {}).items():
        aliases[key] = list(extra) + aliases.get(key, [])
    for canon, names in aliases.items():
        user_mapped = bool(field_map and canon in field_map)
        if out.get(canon) and not user_mapped:
            continue
        for name in names:
            if out.get(name):
                out[canon] = out[name]
                break
        else:
            out.setdefault(canon, "")
    secs = parse_duration(out.get("duration"), duration_units)
    out["duration_seconds"] = "" if secs is None else f"{secs:.0f}"
    return out


def parse(data: bytes, event_path=None, field_map=None, duration_units="auto") -> list:
    """Parse a document into a list of normalized event field dicts."""
    root = parse_xml(data)
    title_names = set(ALIASES["title"]) | set((field_map or {}).get("title", []))
    events = find_events(root, event_path, title_names)
    return [normalize(flatten(e), field_map, duration_units) for e in events]


_START_TAG = re.compile(rb"<([A-Za-z_][\w.\-:]*)[\s>/]")


def extract_documents(buf: bytes):
    """Split a byte stream (e.g. a TCP connection) into complete XML documents.

    Returns (documents, remainder).
    """
    docs = []
    while True:
        m = _START_TAG.search(buf)
        if not m:
            break
        name = m.group(1)
        start = buf.rfind(b"<?xml", 0, m.start())
        start = m.start() if start < 0 else start
        # self-closing root: <nowplaying/>
        gt = buf.find(b">", m.start())
        if gt >= 0 and buf[gt - 1:gt] == b"/":
            end = gt + 1
        else:
            close = re.search(rb"</" + re.escape(name) + rb"\s*>", buf[m.end():])
            if not close:
                break
            end = m.end() + close.end()
        docs.append(buf[start:end])
        buf = buf[end:]
    return docs, buf
