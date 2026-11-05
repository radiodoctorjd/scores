import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request

from rdsbridge import config as config_mod
from rdsbridge.engine import App
from rdsbridge.monitor import Monitor
from rdsbridge.outputs import build_messages, render_fields


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeEncoder:
    """TCP server that records lines and answers OK, like an RDS encoder."""

    def __init__(self, terminator=b"\r"):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.term = terminator
        self.lines = []
        self.connections = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        buf = b""
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while self.term in buf:
                    line, buf = buf.split(self.term, 1)
                    self.lines.append(line.decode("latin-1").strip("\r\n"))
                    conn.sendall(b"OK\r\n")

    def wait_line(self, line, timeout=5):
        end = time.time() + timeout
        while time.time() < end and line not in self.lines:
            time.sleep(0.02)
        return self.lines

    def wait_for(self, n, timeout=5):
        end = time.time() + timeout
        while time.time() < end and len(self.lines) < n:
            time.sleep(0.02)
        return self.lines

    def close(self):
        self.sock.close()


def song(title, artist="", media="SONG", station="WAAA"):
    return (f"<nowplaying><station>{station}</station><title>{title}</title><artist>{artist}</artist>"
            f"<media_type>{media}</media_type><duration>00:03:00</duration></nowplaying>").encode()


class Rendering(unittest.TestCase):
    def setUp(self):
        self.station = config_mod.resolve_station({
            "name": "S", "input": {"type": "udp", "port": 1},
            "templates": {"rt": ["{title} - {artist}", "{title}"], "dps": ["{title!u}"]},
            "default": {"rt": "Station slogan", "dps": "WAAA"},
            "outputs": [{"device": "inovonics730", "host": "x", "port": 1, "max_lengths": {"rt": 10}}],
        }, "s")
        self.out = self.station["outputs"][0]

    def test_song_and_default(self):
        f = render_fields(self.station, self.out, {"title": "Song", "artist": "Band"}, "song")
        self.assertEqual(f, {"rt": "Song", "dps": "SONG"})
        f = render_fields(self.station, self.out, {"title": "Spot"}, "default")
        self.assertEqual(f, {"rt": "Station", "dps": "WAAA"})

    def test_override(self):
        f = render_fields(self.station, self.out, {"title": "Song"}, "song", {"dps": "Live!"})
        self.assertEqual(f["dps"], "Live!")

    def test_commands_skip_empty(self):
        msgs = build_messages(self.out, {"rt": "Hello", "dps": ""}, {})
        self.assertEqual([m[0] for m in msgs], ["TEXT=Hello"])
        self.assertEqual(msgs[0][1], b"TEXT=Hello\r")

    def test_no_newline_injection(self):
        f = render_fields(self.station, self.out, {"title": "A\r\nTEXT=evil"}, "song")
        self.assertNotIn("\n", f["rt"])
        self.assertNotIn("\r", f["rt"])


class ConfigValidation(unittest.TestCase):
    def test_example_config_is_valid(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cfg = config_mod.resolve(config_mod.load(os.path.join(here, "config.example.json")))
        self.assertEqual(len(cfg["stations"]), 2)

    def test_errors(self):
        base = {"name": "S", "input": {"type": "udp", "port": 1}, "outputs": []}
        with self.assertRaises(config_mod.ConfigError):
            config_mod.resolve({"stations": [dict(base, outputs=[{"device": "nope"}])]})
        with self.assertRaises(config_mod.ConfigError):
            config_mod.resolve({"stations": [dict(base, outputs=[{"device": "fmb80", "host": "h"}])]})
        with self.assertRaises(config_mod.ConfigError):
            config_mod.resolve({"stations": [dict(base, templates={"rt": "{title"})]})
        with self.assertRaises(config_mod.ConfigError):
            config_mod.resolve({"stations": [base, base]})


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.ino = FakeEncoder(b"\r")
        self.fmb = FakeEncoder(b"\r\n")
        self.udp_port = free_port(socket.SOCK_DGRAM)
        self.http_port = free_port()
        self.tmp = tempfile.TemporaryDirectory()
        self.xml_path = os.path.join(self.tmp.name, "np.xml")
        self.raw = {
            "stations": [
                {"name": "WAAA", "input": {"type": "udp", "port": self.udp_port, "bind": "127.0.0.1"},
                 "match": {"station": "WAAA"},
                 "filter": {"field": "media_type", "include": ["SONG"]},
                 "templates": {"rt": ["{title} - {artist}", "{title}"], "dps": ["{title}"]},
                 "default": {"rt": "WAAA slogan", "dps": "WAAA"},
                 "outputs": [
                     {"name": "ino", "device": "inovonics730", "host": "127.0.0.1", "port": self.ino.port,
                      "command_delay": 0},
                     {"name": "fmb", "device": "fmb80", "host": "127.0.0.1", "port": self.fmb.port,
                      "command_delay": 0, "persistent": False},
                 ]},
                {"name": "WBBB", "input": {"type": "udp", "port": self.udp_port, "bind": "127.0.0.1"},
                 "match": {"station": "WBBB"},
                 "templates": {"rt": ["B: {title}"]}, "default": {"rt": "B default"},
                 "outputs": [{"name": "b", "device": "inovonics730", "host": "127.0.0.1", "port": self.fmb.port,
                              "terminator": "\r\n", "command_delay": 0}]},
                {"name": "WCCC", "input": {"type": "file", "path": self.xml_path, "poll_interval": 0.1},
                 "templates": {"rt": ["C: {title}"]},
                 "outputs": [{"name": "c", "device": "inovonics730", "host": "127.0.0.1", "port": self.ino.port,
                              "command_delay": 0}]},
                {"name": "WDDD", "input": {"type": "http", "port": self.http_port, "bind": "127.0.0.1"},
                 "templates": {"rt": ["D: {title}"]},
                 "outputs": [{"name": "d", "device": "inovonics730", "host": "127.0.0.1", "port": self.ino.port,
                              "command_delay": 0}]},
            ]
        }
        self.cfg_path = os.path.join(self.tmp.name, "config.json")
        config_mod.save(self.cfg_path, self.raw)
        self.monitor = Monitor()
        self.app = App(self.raw, self.cfg_path, self.monitor)
        self.app.start()
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def tearDown(self):
        self.app.stop()
        self.ino.close()
        self.fmb.close()
        self.udp.close()
        self.tmp.cleanup()

    def send(self, data):
        self.udp.sendto(data, ("127.0.0.1", self.udp_port))

    def test_flow(self):
        self.send(song("Hello", "Adele"))
        self.ino.wait_for(2)
        self.assertEqual(self.ino.lines[:2], ["DPS=Hello", "TEXT=Hello - Adele"])
        self.fmb.wait_for(2)
        self.assertEqual(self.fmb.lines[:2], ["RT1=Hello - Adele", "DPS1=Hello"])

        # same song again: nothing new is sent
        self.send(song("Hello", "Adele"))
        time.sleep(0.4)
        self.assertEqual(len(self.ino.lines), 2)

        # spot -> default text
        self.send(song("Car ad", media="SPOT"))
        self.ino.wait_for(4)
        self.assertEqual(self.ino.lines[2:4], ["DPS=WAAA", "TEXT=WAAA slogan"])

        # second station on the same UDP port
        self.send(song("Other", station="WBBB"))
        self.assertIn("TEXT=B: Other", self.fmb.wait_line("TEXT=B: Other"))
        self.assertNotIn("TEXT=B: Other", self.ino.lines)

        # file input
        with open(self.xml_path, "wb") as fh:
            fh.write(song("From file"))
        self.assertIn("TEXT=C: From file", self.ino.wait_line("TEXT=C: From file"))

        # http input
        req = urllib.request.Request(f"http://127.0.0.1:{self.http_port}/", data=song("Via http"), method="POST")
        self.assertEqual(urllib.request.urlopen(req, timeout=5).read(), b"OK")
        self.assertIn("TEXT=D: Via http", self.ino.wait_line("TEXT=D: Via http"))

        kinds = {e["kind"] for e in self.monitor.since(0)}
        self.assertTrue({"in", "parsed", "out", "resp"} <= kinds)

    def test_override_and_reload(self):
        st = self.app.engine.station("WAAA")
        self.send(song("Hello", "Adele"))
        self.ino.wait_for(2)
        st.set_override({"rt": "Live from the fair"}, hold=True)
        self.assertIn("TEXT=Live from the fair", self.ino.wait_line("TEXT=Live from the fair"))
        self.send(song("Next song", "X"))
        self.assertIn("DPS=Next song", self.ino.wait_line("DPS=Next song"))  # dps follows the song...
        self.assertNotIn("TEXT=Next song - X", self.ino.lines)  # ...rt stays held
        # reload with a new rt template, state carries over
        raw = json.loads(json.dumps(self.raw))
        raw["stations"][0]["templates"]["rt"] = ["NEW {title}"]
        self.app.apply_config(raw)
        self.app.engine.station("WAAA").clear_override()
        end = time.time() + 5
        while time.time() < end and "TEXT=NEW Next song" not in self.ino.lines:
            time.sleep(0.05)
        self.assertIn("TEXT=NEW Next song", self.ino.lines)
        self.assertEqual(config_mod.load(self.cfg_path)["stations"][0]["templates"]["rt"], ["NEW {title}"])

    def test_encoder_offline_then_back(self):
        port = free_port()
        raw = json.loads(json.dumps(self.raw))
        raw["stations"][0]["outputs"][0]["port"] = port
        self.app.apply_config(raw, save=False)
        self.send(song("Queued", "Q"))
        time.sleep(0.5)
        out = self.app.engine.station("WAAA").outputs[0]
        self.assertEqual(out.state, "error")
        # bring a server up on that port; the worker retries and delivers
        late = FakeEncoder.__new__(FakeEncoder)
        late.sock = socket.socket()
        late.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        late.sock.bind(("127.0.0.1", port))
        late.sock.listen(5)
        late.port, late.term, late.lines, late.connections = port, b"\r", [], 0
        threading.Thread(target=late._run, daemon=True).start()
        try:
            self.assertIn("TEXT=Queued - Q", late.wait_line("TEXT=Queued - Q", timeout=8))
        finally:
            late.close()


class WebApi(unittest.TestCase):
    def test_api(self):
        from rdsbridge.web import make_server, serve_in_background
        enc = FakeEncoder()
        raw = {"stations": [{"name": "S", "input": {"type": "udp", "port": free_port(socket.SOCK_DGRAM), "bind": "127.0.0.1"},
                             "outputs": [{"name": "o", "device": "inovonics730", "host": "127.0.0.1", "port": enc.port}]}]}
        app = App(raw, None, Monitor())
        app.start()
        port = free_port()
        server = make_server(app, {"bind": "127.0.0.1", "port": port, "username": None, "password": None})
        serve_in_background(server)
        base = f"http://127.0.0.1:{port}"

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()

        try:
            code, body = call("/")
            self.assertEqual(code, 200)
            self.assertIn(b"RDS Bridge", body)
            code, body = call("/api/inject", {"station": "S", "xml": "<np><title>T</title><artist>A</artist></np>"})
            self.assertEqual(code, 200)
            self.assertIn("TEXT=T - A", enc.wait_line("TEXT=T - A"))
            code, body = call("/api/status")
            st = json.loads(body)["stations"][0]
            self.assertEqual(st["event"]["title"], "T")
            code, body = call("/api/preview", {"station": "S", "station_config": dict(raw["stations"][0], templates={"rt": ["X {title}"]})})
            prev = json.loads(body)["outputs"][0]["song"]["messages"]
            self.assertEqual(prev, ["TEXT=X T"])
            code, body = call("/api/config", {"config": {"stations": []}})
            self.assertEqual(code, 400)
            code, body = call("/api/log")
            self.assertTrue(json.loads(body)["entries"])
        finally:
            server.shutdown()
            app.stop()
            enc.close()


if __name__ == "__main__":
    unittest.main()
