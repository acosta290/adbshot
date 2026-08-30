"""Device tracking over a persistent socket to the adb server.

`host:track-devices-l` pushes a full device list every time anything changes,
so there is no polling, and its lines already carry `model:` -- which means the
tray menu never has to pay for a `getprop` round trip.

Two parser traps, both measured rather than assumed:

  * `adb devices -l` separates with SPACE PADDING, not a tab. Splitting on '\t'
    works for plain `adb devices` and silently fails here.
  * The state can be two words ("no permissions"), and that line continues with
    a parenthetical and a URL. Taking split()[1] as the state is wrong, and a
    naive key:value scan of the rest swallows "https://..." as a property.
"""

from __future__ import annotations

import enum
import re
import socket
import threading
import time
from dataclasses import dataclass, field

from gi.repository import GLib

from . import log
from .i18n import _

LOG = log.get("devices")

ADB_HOST = "127.0.0.1"
ADB_PORT = 5037

# Longest first, so "no permissions" is matched before any single word.
STATES = [
    "no permissions",
    "unauthorized",
    "authorizing",
    "connecting",
    "bootloader",
    "recovery",
    "sideload",
    "rescue",
    "offline",
    "device",
    "host",
    "unknown",
]

# Only these keys are real; anything else on the line (a URL, a parenthetical)
# is noise from the "no permissions" message.
KNOWN_PROPS = {"usb", "product", "model", "device", "transport_id"}


class Transport(enum.Enum):
    USB = "usb"
    NETWORK = "network"
    EMULATOR = "emulator"


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    props: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.state == "device"

    @property
    def transport(self) -> Transport:
        # Not `':' in serial` -- that is only true of the manual `adb connect`
        # form, and misses the mDNS form the phone's own Wireless debugging
        # screen produces. Absence of a usb: field is the reliable signal.
        if self.serial.startswith("emulator-"):
            return Transport.EMULATOR
        if "usb" not in self.props:
            return Transport.NETWORK
        return Transport.USB

    @property
    def model(self) -> str:
        raw = self.props.get("model") or self.props.get("device") or ""
        return raw.replace("_", " ") or self.serial

    @property
    def label(self) -> str:
        if self.transport is Transport.NETWORK:
            return f"{self.model} (Wi-Fi)"
        return self.model

    def status_text(self) -> str:
        return {
            "device": _("ready"),
            "unauthorized": _("unauthorised — accept the debugging prompt on the phone"),
            "no permissions": _("no permissions — check your udev rules"),
            "offline": _("offline"),
            "authorizing": _("authorising…"),
            "connecting": _("connecting…"),
            "recovery": _("in recovery"),
            "bootloader": _("in the bootloader"),
            "sideload": _("in sideload"),
            "rescue": _("in rescue mode"),
        }.get(self.state, self.state)


def parse_line(line: str) -> Device | None:
    line = line.strip()
    if not line or line.startswith("List of devices"):
        return None
    # Serial is the first whitespace-delimited token; the rest is padded text.
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    serial, rest = parts[0], parts[1].strip()

    state = None
    for candidate in STATES:
        if rest == candidate or rest.startswith(candidate + " "):
            state = candidate
            rest = rest[len(candidate):].strip()
            break
    if state is None:
        state = rest.split()[0] if rest else "unknown"
        rest = ""

    props = {}
    for token in rest.split():
        key, sep, value = token.partition(":")
        if sep and key in KNOWN_PROPS:
            props[key] = value
    return Device(serial=serial, state=state, props=props)


def parse_device_list(payload: str) -> list[Device]:
    out = []
    for line in payload.splitlines():
        dev = parse_line(line)
        if dev is not None:
            out.append(dev)
    return out


class DeviceTracker:
    """Keeps a live device list, calling `on_change(list[Device])` on the main loop."""

    def __init__(self, on_change):
        self._on_change = on_change
        self._devices: list[Device] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def devices(self) -> list[Device]:
        with self._lock:
            return list(self._devices)

    def ready_devices(self) -> list[Device]:
        return [d for d in self.devices if d.ready]

    def find(self, serial: str) -> Device | None:
        for d in self.devices:
            if d.serial == serial:
                return d
        return None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="adb-track", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- worker thread ----------------------------------------------------

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                self._track()
                backoff = 0.5
            except Exception as exc:  # noqa: BLE001 - any failure means "retry"
                LOG.debug("tracker disconnected: %s", exc)
                self._publish([])
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 10.0)

    def _track(self) -> None:
        sock = socket.create_connection((ADB_HOST, ADB_PORT), timeout=5)
        try:
            sock.settimeout(None)
            self._send(sock, "host:track-devices-l")
            if self._read_exactly(sock, 4) != b"OKAY":
                raise RuntimeError("adb server refused track-devices-l")
            LOG.info("tracking devices via adb server")
            while not self._stop.is_set():
                header = self._read_exactly(sock, 4)
                length = int(header.decode("ascii"), 16)
                payload = self._read_exactly(sock, length).decode("utf-8", "replace") if length else ""
                self._publish(parse_device_list(payload))
        finally:
            sock.close()

    @staticmethod
    def _send(sock: socket.socket, command: str) -> None:
        data = command.encode("utf-8")
        sock.sendall(f"{len(data):04x}".encode("ascii") + data)

    @staticmethod
    def _read_exactly(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("adb server closed the connection")
            buf += chunk
        return buf

    def _publish(self, devices: list[Device]) -> None:
        with self._lock:
            if devices == self._devices:
                return
            self._devices = devices
        LOG.info("devices: %s", ", ".join(f"{d.serial}={d.state}" for d in devices) or "(none)")
        GLib.idle_add(self._on_change, list(devices), priority=GLib.PRIORITY_DEFAULT)
