"""Runs `adb exec-out screencap -p` without ever blocking the main loop.

The single most important line in this module is the PNG magic gate. `screencap`
can fail with exit status 0, an empty stderr, and a plain-ASCII error message on
stdout -- so keying error handling on the exit status alone writes a corrupt
.png to disk and pushes garbage onto the clipboard. Validating the 8-byte
signature costs a fraction of a microsecond and turns that into a clear message.

That gate has one blind spot, and a foldable walks straight into it: on a phone
with more than one screen, `screencap` writes a multi-line `[Warning] Multiple
displays were found...` to stdout and *then* writes a valid PNG behind it. The
image is fine; only its offset moved. So the gate now locates the signature
instead of merely testing for it, and rejects the payload only when there is no
PNG in it at all.

Which screen that PNG shows is displays.py's problem: without `-d`, screencap
takes "the first display found", which on a Fold is the cover screen even while
the inner one is the live one.
"""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import const, log
from .displays import DisplayResolver
from .i18n import _

LOG = log.get("capture")

TIMEOUT_SECONDS = 8

# How far into stdout the PNG signature may sit before the payload is treated
# as an error rather than a warned-about image. screencap's own warning is
# ~350 bytes; anything past this is not a preamble, it is noise.
MAX_PREAMBLE_BYTES = 8192


class CaptureError(Exception):
    pass


@dataclass
class RawCapture:
    png: bytes
    serial: str
    display: str = ""


def split_preamble(data: bytes) -> tuple[bytes | None, bytes]:
    """(png, preamble). png is None when stdout holds no image at all."""
    if data.startswith(const.PNG_MAGIC):
        return data, b""
    offset = data.find(const.PNG_MAGIC, 0, MAX_PREAMBLE_BYTES + len(const.PNG_MAGIC))
    if offset > 0:
        return data[offset:], data[:offset]
    return None, data


class CaptureEngine:
    def __init__(self) -> None:
        self.displays = DisplayResolver()

    def capture(self, serial: str, on_done, on_error, display_id: str | None = None) -> None:
        """Start a capture. Callbacks fire on the main loop, exactly once.

        Pass display_id to pin the capture to a screen; leave it None to let
        the resolver find the one that is powered on.
        """
        if display_id is not None:
            self._shoot(serial, display_id, on_done, on_error)
            return

        def chosen(resolved):
            self._shoot(serial, resolved, on_done, on_error)
            return False

        self.displays.resolve(serial, chosen)

    # -- one screencap invocation -----------------------------------------

    def _shoot(self, serial: str, display_id: str | None, on_done, on_error,
               retried: bool = False) -> None:
        argv = ["adb"]
        if serial:
            argv += ["-s", serial]
        argv += ["exec-out", "screencap", "-p"]
        if display_id:
            argv += ["-d", display_id]

        try:
            proc = Gio.Subprocess.new(
                argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            )
        except GLib.Error as exc:
            on_error(self._explain_spawn(exc))
            return

        state = {"timeout_id": None, "finished": False}

        def on_timeout():
            # Returning False already destroys the source; clearing the id here
            # is what stops on_finished from removing it a second time.
            state["timeout_id"] = None
            if not state["finished"]:
                state["finished"] = True
                proc.force_exit()
                on_error(_(
                    "The phone did not respond within {seconds} s. Check the "
                    "cable and that USB debugging is still authorised."
                ).format(seconds=TIMEOUT_SECONDS))
            return False

        state["timeout_id"] = GLib.timeout_add_seconds(TIMEOUT_SECONDS, on_timeout)

        def on_finished(source, result):
            if state["timeout_id"] is not None:
                GLib.Source.remove(state["timeout_id"])
                state["timeout_id"] = None
            if state["finished"]:
                return
            state["finished"] = True
            try:
                ok, stdout, stderr = source.communicate_finish(result)
            except GLib.Error as exc:
                on_error(_("Could not run adb: {error}").format(error=exc.message))
                return

            data = stdout.get_data() if stdout else b""
            err = (stderr.get_data() if stderr else b"").decode("utf-8", "replace").strip()

            png, preamble = split_preamble(data)
            if png is None:
                if display_id and not retried:
                    # The id came from dumpsys and screencap still refused it
                    # (an OEM build, a display unplugged between the two
                    # calls). One unpinned retry beats a failed capture.
                    LOG.warning("screencap rejected display %s; retrying unpinned", display_id)
                    self.displays.disable(serial)
                    self._shoot(serial, None, on_done, on_error, retried=True)
                    return
                on_error(self._explain_payload(data, err, source.get_exit_status()))
                return

            if preamble:
                LOG.debug("dropped %d bytes of screencap chatter: %s",
                          len(preamble), preamble[:200].decode("utf-8", "replace").strip())
            on_done(RawCapture(png=png, serial=serial, display=display_id or ""))

        proc.communicate_async(None, None, on_finished)

    @staticmethod
    def _explain_spawn(exc: GLib.Error) -> str:
        if "No such file" in (exc.message or ""):
            return _("adb was not found. Install it with: sudo apt install adb")
        return _("Could not launch adb: {error}").format(error=exc.message)

    @staticmethod
    def _explain_payload(data: bytes, stderr_text: str, status: int) -> str:
        text = (stderr_text or data[:200].decode("utf-8", "replace")).strip()
        low = text.lower()
        if "no devices" in low or "device not found" in low:
            return _("No phone is connected.")
        if "unauthorized" in low:
            return _("The phone has not authorised USB debugging. Accept the prompt on its screen.")
        if "offline" in low:
            return _("The phone is offline. Plug it in again.")
        if "permission" in low:
            return _("adb has no permission for this device. Check your udev rules.")
        if "is not valid" in low or "display id" in low:
            return _("The phone rejected the chosen screen. Unlock it and try again.")
        if not text:
            return _("adb returned no image (exit {status}).").format(status=status)
        return _("adb returned no image: {error}").format(error=text)
