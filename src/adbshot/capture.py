"""Runs `adb exec-out screencap -p` without ever blocking the main loop.

The single most important line in this module is the PNG magic gate. `screencap`
can fail with exit status 0, an empty stderr, and a plain-ASCII error message on
stdout -- so keying error handling on the exit status alone writes a corrupt
.png to disk and pushes garbage onto the clipboard. Validating the 8-byte
signature costs a fraction of a microsecond and turns that into a clear message.
"""

from __future__ import annotations

from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import const, log
from .i18n import _

LOG = log.get("capture")

TIMEOUT_SECONDS = 8


class CaptureError(Exception):
    pass


@dataclass
class RawCapture:
    png: bytes
    serial: str


class CaptureEngine:
    def capture(self, serial: str, on_done, on_error) -> None:
        """Start a capture. Callbacks fire on the main loop, exactly once."""
        argv = ["adb"]
        if serial:
            argv += ["-s", serial]
        argv += ["exec-out", "screencap", "-p"]

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

            if not data.startswith(const.PNG_MAGIC):
                on_error(self._explain_payload(data, err, source.get_exit_status()))
                return
            on_done(RawCapture(png=data, serial=serial))

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
        if not text:
            return _("adb returned no image (exit {status}).").format(status=status)
        return _("adb returned no image: {error}").format(error=text)
