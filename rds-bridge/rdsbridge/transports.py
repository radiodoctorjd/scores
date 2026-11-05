"""Byte transports to the RDS encoders: TCP, UDP and serial."""

from __future__ import annotations

import socket
import time


class TransportError(OSError):
    pass


class TcpTransport:
    def __init__(self, cfg: dict):
        self.host, self.port = cfg["host"], cfg["port"]
        self.timeout = cfg["connect_timeout"]
        self.sock = None

    @property
    def describe(self):
        return f"tcp://{self.host}:{self.port}"

    @property
    def is_open(self):
        return self.sock is not None

    def open(self):
        self.close()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise TransportError(f"cannot connect to {self.host}:{self.port}: {exc}") from exc
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    def write(self, data: bytes):
        if not self.sock:
            raise TransportError("not connected")
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self.close()
            raise TransportError(f"send failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        """Whatever arrives within ``timeout`` seconds (b'' if nothing)."""
        if not self.sock or timeout <= 0:
            return b""
        chunks, deadline = [], time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            except OSError as exc:
                self.close()
                raise TransportError(f"receive failed: {exc}") from exc
            if not chunk:
                self.close()  # peer closed; next write reconnects
                break
            chunks.append(chunk)
            deadline = min(deadline, time.monotonic() + 0.05)  # short tail after data
        if self.sock:
            self.sock.settimeout(self.timeout)
        return b"".join(chunks)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


class UdpTransport:
    def __init__(self, cfg: dict):
        self.host, self.port = cfg["host"], cfg["port"]
        self.sock = None

    @property
    def describe(self):
        return f"udp://{self.host}:{self.port}"

    @property
    def is_open(self):
        return self.sock is not None

    def open(self):
        self.close()
        try:
            info = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_DGRAM)[0]
            self.sock = socket.socket(info[0], socket.SOCK_DGRAM)
            self.sock.connect(info[4])
        except OSError as exc:
            self.sock = None
            raise TransportError(f"cannot open UDP to {self.host}:{self.port}: {exc}") from exc

    def write(self, data: bytes):
        if not self.sock:
            raise TransportError("not open")
        try:
            self.sock.send(data)
        except OSError as exc:
            raise TransportError(f"send failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        if not self.sock or timeout <= 0:
            return b""
        self.sock.settimeout(timeout)
        try:
            return self.sock.recv(4096)
        except (socket.timeout, ConnectionRefusedError):
            return b""
        except OSError:
            return b""

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


class SerialTransport:
    def __init__(self, cfg: dict):
        self.port, self.baudrate = cfg["serial_port"], int(cfg["baudrate"])
        self.ser = None

    @property
    def describe(self):
        return f"serial://{self.port}@{self.baudrate}"

    @property
    def is_open(self):
        return self.ser is not None

    def open(self):
        self.close()
        try:
            import serial  # pyserial, optional dependency
        except ImportError:
            raise TransportError("serial output needs pyserial: pip install pyserial")
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.1, write_timeout=2)
        except Exception as exc:
            raise TransportError(f"cannot open {self.port}: {exc}") from exc

    def write(self, data: bytes):
        if not self.ser:
            raise TransportError("not open")
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as exc:
            self.close()
            raise TransportError(f"serial write failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        if not self.ser or timeout <= 0:
            return b""
        deadline, chunks = time.monotonic() + timeout, []
        while time.monotonic() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                chunks.append(chunk)
                deadline = min(deadline, time.monotonic() + 0.05)
        return b"".join(chunks)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None


def make_transport(cfg: dict):
    return {"tcp": TcpTransport, "udp": UdpTransport, "serial": SerialTransport}[cfg["transport"]](cfg)
