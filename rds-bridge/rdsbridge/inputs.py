"""Inputs that receive WideOrbit now-playing XML.

* file - WideOrbit writes an XML file (Event Driver, export type XML); we poll it
* udp  - one XML document per datagram
* tcp  - WideOrbit connects and sends XML (one or more documents per connection)
* http - WideOrbit (or anything else) POSTs XML to http://this-pc:port/path

Several stations can share one input; each station keeps only the events that
match its own ``match`` rule.
"""

from __future__ import annotations

import hashlib
import os
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import describe_input
from .wideorbit import extract_documents


class Input:
    def __init__(self, cfg: dict, monitor):
        self.cfg = cfg
        self.monitor = monitor
        self.listeners = []  # callables(data: bytes, source: str)
        self._stop = threading.Event()
        self._threads = []
        self.state = "starting"
        self.last_error = ""
        self.docs = 0

    @property
    def describe(self):
        return describe_input(self.cfg)

    def dispatch(self, data: bytes, source: str):
        self.docs += 1
        self.monitor.log("in", "", source, f"{len(data)} bytes received", data)
        for listener in list(self.listeners):
            try:
                listener(data, source)
            except Exception as exc:
                self.monitor.log("error", "", source, f"station failed to handle document: {exc}")

    def _spawn(self, target, *args, name=""):
        t = threading.Thread(target=target, args=args, daemon=True, name=name or f"in:{self.describe}")
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()

    def join(self):
        for t in self._threads:
            t.join(timeout=3)

    def status(self):
        return {"input": self.describe, "state": self.state, "last_error": self.last_error, "docs": self.docs}


class FileInput(Input):
    def start(self):
        self._spawn(self._run)

    def _run(self):
        path, interval = self.cfg["path"], self.cfg["poll_interval"]
        last_sig, last_digest, pending_sig, missing_logged = None, None, None, False
        self.state = "watching"
        while not self._stop.is_set():
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
                missing_logged = False
                if sig != last_sig:
                    with open(path, "rb") as fh:
                        data = fh.read()
                    digest = hashlib.sha1(data).digest()
                    if digest == last_digest:
                        last_sig = sig
                    elif self._complete(data) or sig == pending_sig:
                        # complete document, or unchanged for a full poll (let the parser report it)
                        last_digest, last_sig, pending_sig = digest, sig, None
                        if data.strip(b"\x00 \t\r\n"):
                            self.dispatch(data, f"file {os.path.basename(path)}")
                    else:
                        pending_sig = sig  # WideOrbit may still be writing; look again next poll
                self.state, self.last_error = "watching", ""
            except FileNotFoundError:
                if not missing_logged:
                    self.monitor.log("info", "", f"file {path}", "waiting for file to appear")
                    missing_logged = True
                self.state = "waiting for file"
            except OSError as exc:  # locked by the writer, network share hiccup...
                self.state, self.last_error = "error", str(exc)
            self._stop.wait(interval)

    @staticmethod
    def _complete(data: bytes) -> bool:
        docs, rest = extract_documents(data)
        return bool(docs) and not rest.strip(b"\x00 \t\r\n")


class UdpInput(Input):
    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.cfg["bind"], self.cfg["port"]))
        self.sock.settimeout(1.0)
        self.state = "listening"
        self._spawn(self._run)

    def _run(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                if self._stop.is_set():
                    break
                self.last_error = str(exc)
                continue
            if data.strip():
                self.dispatch(data, f"udp from {addr[0]}:{addr[1]}")
        self.sock.close()


class TcpInput(Input):
    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.cfg["bind"], self.cfg["port"]))
        self.sock.listen(8)
        self.sock.settimeout(1.0)
        self.state = "listening"
        self._spawn(self._accept)

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, addr = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            self._spawn(self._client, conn, addr, name=f"in:tcp:{addr[0]}")
        self.sock.close()

    def _client(self, conn, addr):
        source = f"tcp from {addr[0]}:{addr[1]}"
        buf = b""
        conn.settimeout(1.0)
        with conn:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                docs, buf = extract_documents(buf)
                for doc in docs:
                    self.dispatch(doc, source)
                if len(buf) > 1_000_000:
                    self.monitor.log("error", "", source, "over 1 MB without a complete XML document; discarding")
                    buf = b""
        if buf.strip(b"\x00 \t\r\n"):
            self.dispatch(buf, source)


class HttpInput(Input):
    def start(self):
        inp = self

        class Handler(BaseHTTPRequestHandler):
            def _path_ok(self):
                want = inp.cfg["path"].rstrip("/") or "/"
                got = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
                return want == "/" or got == want

            def do_POST(self):
                if not self._path_ok():
                    return self._reply(404, b"not found")
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length) if length else b""
                if not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
                    # form post: xml=<...> or data=<...>
                    form = urllib.parse.parse_qs(data.decode("utf-8", "replace"))
                    data = (form.get("xml") or form.get("data") or [""])[0].encode("utf-8")
                if not data.strip():
                    return self._reply(400, b"empty body")
                inp.dispatch(data, f"http from {self.client_address[0]}")
                self._reply(200, b"OK")

            def do_GET(self):
                if not self._path_ok():
                    return self._reply(404, b"not found")
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                xml = (qs.get("xml") or [""])[0]
                if not xml:
                    return self._reply(200, b"RDS Bridge HTTP input: POST now-playing XML here")
                inp.dispatch(xml.encode("utf-8"), f"http from {self.client_address[0]}")
                self._reply(200, b"OK")

            def _reply(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((self.cfg["bind"], self.cfg["port"]), Handler)
        self.server.daemon_threads = True
        self.state = "listening"
        self._spawn(self.server.serve_forever)

    def stop(self):
        super().stop()
        if getattr(self, "server", None):
            self.server.shutdown()
            self.server.server_close()


INPUT_CLASSES = {"file": FileInput, "udp": UdpInput, "tcp": TcpInput, "http": HttpInput}


def make_input(cfg: dict, monitor) -> Input:
    return INPUT_CLASSES[cfg["type"]](cfg, monitor)
