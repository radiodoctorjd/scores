"""Smoke-test a built RDSBridge.exe (used by the Windows CI build).

Starts the exe with no config (first run), checks the web screen, then runs a
real config: XML in over UDP -> commands out to a fake TCP encoder.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

EXE = os.path.abspath(sys.argv[1])
APP_DIR = os.path.dirname(EXE)


def wait_http(url, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.read()
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"FAIL: {url} did not answer")


def stop(proc):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


# 1. first run: no config.json -> created from the example, outputs disabled, web screen up
cfg_path = os.path.join(APP_DIR, "config.json")
if os.path.exists(cfg_path):
    os.remove(cfg_path)
proc = subprocess.Popen([EXE], cwd=tempfile.gettempdir())  # cwd elsewhere: must still find its folder
try:
    page = wait_http("http://127.0.0.1:8080/")
    assert b"RDS Bridge" in page, "web page not served"
    status = json.loads(wait_http("http://127.0.0.1:8080/api/status"))
    assert len(status["stations"]) == 2, status
    created = json.load(open(cfg_path, encoding="utf-8"))
    assert all(not o["enabled"] for s in created["stations"] for o in s["outputs"]), "outputs not disabled"
    print("OK first run: config created, web screen served")
finally:
    stop(proc)
for leftover in ("config.json", "config.json.bak", "rdsbridge.log"):
    path = os.path.join(APP_DIR, leftover)
    if os.path.exists(path):
        os.remove(path)  # keep the uploaded folder clean

# 2. real flow with a fake encoder
lines = []
enc = socket.socket()
enc.bind(("127.0.0.1", 0))
enc.listen(1)
enc_port = enc.getsockname()[1]


def encoder():
    conn, _ = enc.accept()
    buf = b""
    while True:
        data = conn.recv(4096)
        if not data:
            return
        buf += data
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            lines.append(line.decode())
            conn.sendall(b"OK\r\n")


threading.Thread(target=encoder, daemon=True).start()
tmp = tempfile.mkdtemp()
cfg = {
    "log_file": "rdsbridge.log",
    "web": {"port": 8091},
    "stations": [{
        "name": "TEST", "input": {"type": "udp", "port": 15123, "bind": "127.0.0.1"},
        "templates": {"rt": ["{title} - {artist}"], "dps": ["{title}"]},
        "outputs": [{"name": "ino", "device": "inovonics730", "host": "127.0.0.1", "port": enc_port}],
    }],
}
cfg_file = os.path.join(tmp, "config.json")
json.dump(cfg, open(cfg_file, "w"))
proc = subprocess.Popen([EXE, "--config", cfg_file])
try:
    wait_http("http://127.0.0.1:8091/api/status")
    xml = open(os.path.join(APP_DIR, "samples", "wideorbit_nowplaying.xml"), "rb").read()
    socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(xml, ("127.0.0.1", 15123))
    end = time.time() + 15
    while time.time() < end and "TEXT=Here Comes The Sun - The Beatles" not in lines:
        time.sleep(0.2)
    assert lines == ["DPS=Here Comes The Sun", "TEXT=Here Comes The Sun - The Beatles"], lines
    assert os.path.exists(os.path.join(tmp, "rdsbridge.log")), "log file not next to config"
    print("OK end to end:", lines)
finally:
    stop(proc)
    shutil.rmtree(tmp, ignore_errors=True)
print("SMOKE TEST PASSED")
