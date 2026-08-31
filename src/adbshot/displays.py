"""Physical display discovery, so a foldable is captured on the screen you are looking at.

On a single-screen phone `screencap` needs no help. On a Galaxy Z Fold it fails
twice over, and both failures are silent:

  * It prints `[Warning] Multiple displays were found, but no display id was
    specified!` **on stdout**, ahead of the PNG. The payload no longer starts
    with the PNG signature, so capture.py's magic gate rejects a perfectly good
    image as "adb returned no image".
  * It then captures "the first display found", which on this hardware is the
    cover screen -- returning a black 1080x2520 frame while the user is looking
    at the unfolded inner one.

capture.py fixes the first half by tolerating a text preamble. This module
fixes the second: it names the display that is actually powered on, so the
capture can be pinned to it with `screencap -d`.

Three facts, all measured on a Fold 8 Ultra rather than assumed:

  * `-d` wants the 64-bit *physical* id out of `uniqueId="local:<id>"`. The
    small logical ids dumpsys also prints (0, 1) are rejected outright with
    "Failed to take take screenshot. Display Id '0' is not valid."
  * Power state comes from the DisplayDeviceInfo line's `state`, never from
    `committedState`, which lags a fold by a frame: a display that has just
    woken up reports "state ON, committedState OFF".
  * A display that is OFF still captures successfully -- it just hands back an
    all-black image. There is no error to key on, which is exactly why the
    right display has to be chosen up front.

The probe is `dumpsys display` filtered by grep *on the device*: the full dump
is 400 KB and only three of its lines matter. If the device has no grep the
pipe yields nothing, and the resolver retries once unfiltered rather than
quietly guessing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import log

LOG = log.get("displays")

TIMEOUT_SECONDS = 4

# How long "this device has a single display" is trusted before being
# re-checked. Topology is near-static, but an external screen (DeX, HDMI) can
# appear at any time, and a stale "no -d needed" would send the capture back to
# guessing. One probe a minute is invisible next to a ~500 ms capture.
SINGLE_DISPLAY_TTL = 60.0

FILTER = "dumpsys display | grep -E 'mViewports=|DisplayDeviceInfo'"

_UNIQUE_ID = re.compile(r'uniqueId="local:(\d+)"')
_SIZE = re.compile(r'uniqueId="local:\d+",\s*(\d+)\s*x\s*(\d+)')
_STATE = re.compile(r"\bstate\s+([A-Z][A-Z_]*)")
_VIEWPORT = re.compile(r"DisplayViewport\{[^}]*\}")
_VIEWPORT_ID = re.compile(r"uniqueId='local:(\d+)'")
_VIEWPORT_ACTIVE = re.compile(r"isActive=(true|false)")

# Marks the built-in screen the system would fall back to. Used only to break a
# tie, never to override a display that is demonstrably on.
DEFAULT_FLAG = "FLAG_ALLOWED_TO_BE_DEFAULT_DISPLAY"


@dataclass(frozen=True)
class Display:
    physical_id: str
    name: str = ""
    width: int = 0
    height: int = 0
    state: str = "UNKNOWN"
    active: bool = False
    default: bool = False

    @property
    def on(self) -> bool:
        # DOZE is the always-on-display: lit, but showing a clock rather than
        # the session the user means to screenshot. Deliberately not "on".
        return self.state == "ON"

    @property
    def label(self) -> str:
        size = f"{self.width}x{self.height}" if self.width else "?"
        return f"{self.name or self.physical_id} {size} {self.state.lower()}"


def parse(payload: str) -> list[Display]:
    """Displays out of `dumpsys display`, filtered or whole -- both parse the same."""
    active_ids = _parse_viewports(payload)
    out: list[Display] = []
    seen: set[str] = set()

    for line in payload.splitlines():
        if "DisplayDeviceInfo{" not in line:
            continue
        match = _UNIQUE_ID.search(line)
        if not match:
            # Virtual and network displays exist and cannot be passed to -d.
            continue
        physical_id = match.group(1)
        if physical_id in seen:
            continue
        seen.add(physical_id)

        name = ""
        named = re.search(r'DisplayDeviceInfo\{"([^"]*)"', line)
        if named:
            name = named.group(1)

        width = height = 0
        size = _SIZE.search(line)
        if size:
            width, height = int(size.group(1)), int(size.group(2))

        # First `state` on the line is the device's own; `committedState` and
        # the per-display-power-controller states come later and lag it.
        state = "UNKNOWN"
        state_match = _STATE.search(line)
        if state_match:
            state = state_match.group(1)

        out.append(Display(
            physical_id=physical_id,
            name=name,
            width=width,
            height=height,
            state=state,
            active=physical_id in active_ids,
            default=DEFAULT_FLAG in line,
        ))
    return out


def _parse_viewports(payload: str) -> set[str]:
    """Physical ids whose input viewport is live -- where touches are landing."""
    active: set[str] = set()
    for line in payload.splitlines():
        if "mViewports=" not in line:
            continue
        for block in _VIEWPORT.findall(line):
            flag = _VIEWPORT_ACTIVE.search(block)
            ident = _VIEWPORT_ID.search(block)
            if flag and ident and flag.group(1) == "true":
                active.add(ident.group(1))
    return active


def pick(displays: list[Display]) -> Display | None:
    """The display to capture, or None when `-d` should be left off entirely.

    None is not a failure: on a one-screen phone the flag buys nothing, and
    leaving it off keeps the capture identical to what has always shipped.
    """
    if len(displays) < 2:
        return None

    lit = [d for d in displays if d.on]
    if lit:
        # Both screens can be on at once (DeX, an HDMI dongle). The input
        # viewport is the tie-breaker: it follows the screen being touched.
        for d in lit:
            if d.active:
                return d
        for d in lit:
            if d.default:
                return d
        return lit[0]

    # Everything is asleep. The capture will be black whichever we choose, and
    # imaging.looks_blank() is what tells the user why -- but choosing
    # deterministically beats screencap's "first display found".
    for d in displays:
        if d.active:
            return d
    for d in displays:
        if d.default:
            return d
    return displays[0]


class DisplayResolver:
    """Names the live display of a device, without paying for it on every capture.

    A phone with one screen is memoised, so the ~80 ms probe is charged once a
    minute instead of once a capture. A foldable is re-probed every time,
    because which of its screens is live is exactly the thing that changes.
    """

    def __init__(self) -> None:
        self._single: dict[str, float] = {}   # serial -> when it was last seen single
        self._unpinnable: set[str] = set()    # serials whose screencap refuses -d
        self._use_filter = True

    def forget(self, serial: str | None = None) -> None:
        if serial is None:
            self._single.clear()
        else:
            self._single.pop(serial, None)

    def disable(self, serial: str) -> None:
        """Stop pinning this device: its screencap rejected an id we handed it.

        Recorded rather than merely retried, so one OEM build with a different
        `-d` dialect costs a single wasted capture instead of one per shot.
        """
        LOG.info("display pinning disabled for %s", serial or "(default device)")
        self._unpinnable.add(serial)
        self._single.pop(serial, None)

    def resolve(self, serial: str, on_done) -> None:
        """Call `on_done(display_id | None)` on the main loop, exactly once."""
        seen_at = self._single.get(serial)
        cached = serial in self._unpinnable or (
            seen_at is not None and (time.monotonic() - seen_at) < SINGLE_DISPLAY_TTL
        )
        if cached:
            GLib.idle_add(on_done, None, priority=GLib.PRIORITY_DEFAULT)
            return
        self._probe(serial, on_done, filtered=self._use_filter)

    def _probe(self, serial: str, on_done, filtered: bool) -> None:
        argv = ["adb"]
        if serial:
            argv += ["-s", serial]
        argv += ["shell", FILTER if filtered else "dumpsys display"]

        try:
            proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            )
        except GLib.Error as exc:
            # adb missing is the capture's error to report, not ours.
            LOG.debug("could not probe displays: %s", exc.message)
            on_done(None)
            return

        state = {"timeout_id": None, "finished": False}

        def finish(display_id):
            if state["timeout_id"] is not None:
                GLib.Source.remove(state["timeout_id"])
                state["timeout_id"] = None
            if state["finished"]:
                return
            state["finished"] = True
            on_done(display_id)

        def on_timeout():
            state["timeout_id"] = None
            if not state["finished"]:
                proc.force_exit()
                LOG.debug("display probe timed out; capturing without -d")
                finish(None)
            return False

        state["timeout_id"] = GLib.timeout_add_seconds(TIMEOUT_SECONDS, on_timeout)

        def on_finished(source, result):
            try:
                _ok, stdout, _stderr = source.communicate_finish(result)
            except GLib.Error as exc:
                LOG.debug("display probe failed: %s", exc.message)
                finish(None)
                return

            payload = (stdout.get_data() if stdout else b"").decode("utf-8", "replace")
            displays = parse(payload)

            if not displays and filtered and self._use_filter:
                # No grep on the device. Stop filtering for the rest of this
                # session and read the whole dump instead of guessing.
                self._use_filter = False
                LOG.debug("device has no usable grep; re-probing displays unfiltered")
                if state["timeout_id"] is not None:
                    GLib.Source.remove(state["timeout_id"])
                    state["timeout_id"] = None
                state["finished"] = True
                self._probe(serial, on_done, filtered=False)
                return

            if len(displays) < 2:
                self._single[serial] = time.monotonic()
                finish(None)
                return

            self._single.pop(serial, None)
            chosen = pick(displays)
            LOG.info(
                "%d displays: %s -> capturing %s",
                len(displays),
                "; ".join(d.label for d in displays),
                chosen.label if chosen else "(default)",
            )
            finish(chosen.physical_id if chosen else None)

        proc.communicate_async(None, None, on_finished)


def probe_sync(serial: str = "", timeout: float = 6.0) -> list[Display]:
    """Blocking probe for the CLI's --self-check. Never call this on the main loop."""
    import subprocess

    argv = ["adb"]
    if serial:
        argv += ["-s", serial]
    for command in (FILTER, "dumpsys display"):
        try:
            done = subprocess.run(
                argv + ["shell", command],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception:  # noqa: BLE001 - a diagnostic must not raise
            return []
        displays = parse(done.stdout)
        if displays:
            return displays
    return []
