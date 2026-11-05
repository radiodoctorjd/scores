"""Web debug / customization screen and its JSON API."""

from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .config import ConfigError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def make_server(app, web_cfg: dict) -> ThreadingHTTPServer:
    user, password = web_cfg.get("username"), web_cfg.get("password")
    token = None
    if password:
        token = "Basic " + base64.b64encode(f"{user or ''}:{password}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"RDSBridge/{__version__}"

        # -- helpers -----------------------------------------------------
        def _authorized(self):
            if not token:
                return True
            if hmac.compare_digest(self.headers.get("Authorization", ""), token):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="RDS Bridge"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode("utf-8"))

        def _error(self, msg, code=400):
            self._json({"error": msg}, code)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def log_message(self, *args):
            pass

        # -- routes ------------------------------------------------------
        def do_GET(self):
            if not self._authorized():
                return
            url = urllib.parse.urlsplit(self.path)
            qs = urllib.parse.parse_qs(url.query)
            if url.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if url.path == "/api/status":
                st = app.engine.status()
                st.update(version=__version__, app_started_at=app.started_at, config_path=app.config_path)
                return self._json(st)
            if url.path == "/api/log":
                since = int((qs.get("since") or ["0"])[0] or 0)
                return self._json({"entries": app.monitor.since(since)})
            if url.path == "/api/config":
                return self._json(app.engine.raw)
            self._error("not found", 404)

        def do_POST(self):
            if not self._authorized():
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                body = self._body()
            except (ValueError, UnicodeDecodeError) as exc:
                return self._error(f"bad JSON: {exc}")
            try:
                if path == "/api/config":
                    app.apply_config(body.get("config"), save=body.get("save", True))
                    return self._json({"ok": True})
                if path == "/api/validate":
                    from .config import resolve
                    resolve(body.get("config"))
                    return self._json({"ok": True})
                if path == "/api/preview":
                    return self._json(app.engine.preview(body["station"], body.get("station_config"), body.get("xml")))
                if path == "/api/inject":
                    app.engine.inject(body["station"], body.get("xml", ""))
                    return self._json({"ok": True})
                if path == "/api/override":
                    st = app.engine.station(body["station"])
                    if body.get("clear"):
                        st.clear_override()
                    else:
                        st.set_override(body.get("fields") or {}, body.get("hold", False), body.get("minutes"))
                    return self._json({"ok": True})
                if path == "/api/resend":
                    app.engine.station(body["station"]).publish(force=True)
                    return self._json({"ok": True})
                if path == "/api/log/clear":
                    app.monitor.clear()
                    return self._json({"ok": True})
            except ConfigError as exc:
                return self._error(str(exc))
            except KeyError as exc:
                return self._error(f"missing or unknown: {exc}")
            except Exception as exc:
                return self._error(f"{type(exc).__name__}: {exc}", 500)
            self._error("not found", 404)

    server = ThreadingHTTPServer((web_cfg["bind"], int(web_cfg["port"])), Handler)
    server.daemon_threads = True
    return server


def serve_in_background(server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    t.start()
    return t
