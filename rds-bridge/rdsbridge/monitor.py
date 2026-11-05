"""In-memory traffic log that feeds the debug screen."""

from __future__ import annotations

import collections
import logging
import threading
import time

log = logging.getLogger("rdsbridge")

# kinds: in (raw data received), parsed (fields), out (sent to encoder),
# resp (encoder reply), skip (ignored on purpose), info, error
_LEVELS = {"error": logging.ERROR, "out": logging.INFO, "info": logging.INFO,
           "parsed": logging.INFO, "in": logging.DEBUG, "resp": logging.DEBUG,
           "skip": logging.DEBUG}
MAX_DATA = 20000


class Monitor:
    def __init__(self, size: int = 2000):
        self._entries = collections.deque(maxlen=max(100, int(size)))
        self._lock = threading.Lock()
        self._next_id = 1

    def log(self, kind: str, station: str = "", endpoint: str = "", summary: str = "", data=None) -> None:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if isinstance(data, str) and len(data) > MAX_DATA:
            data = data[:MAX_DATA] + f"\n... ({len(data) - MAX_DATA} more characters)"
        with self._lock:
            entry = {"id": self._next_id, "ts": time.time(), "kind": kind, "station": station,
                     "endpoint": endpoint, "summary": summary, "data": data}
            self._next_id += 1
            self._entries.append(entry)
        prefix = " ".join(p for p in (f"[{station}]" if station else "", endpoint) if p)
        log.log(_LEVELS.get(kind, logging.INFO), "%-6s %s %s", kind.upper(), prefix, summary)

    def since(self, last_id: int = 0, limit: int = 500) -> list:
        with self._lock:
            items = [e for e in self._entries if e["id"] > last_id]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
