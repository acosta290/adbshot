"""Writing an image to the Wayland clipboard.

Two things here are counter-intuitive and both were measured:

  * GTK's own Gdk.Clipboard does NOT work from a windowless daemon. With no
    window there is no input serial, so the set_selection goes out with serial
    0 and Mutter answers cancelled(). wl-copy is the correct writer.
  * wl-copy must be spawned with stdout AND stderr going to DEVNULL. It forks a
    background process to hold the selection, and that holder inherits the
    write end of any pipe we create -- so communicate() waits on a pipe that is
    never closed and hangs forever.

The holder also inherits our cgroup, which is why the systemd unit sets
KillMode=process: otherwise `systemctl --user restart` silently empties the
clipboard the app just filled.
"""

from __future__ import annotations

import subprocess

from . import log
from .i18n import _

LOG = log.get("clipboard")

WRITE_TIMEOUT = 3.0


class ClipboardError(Exception):
    pass


def copy_image(data: bytes, mime: str = "image/png") -> None:
    """Blocking, but bounded. Call from a worker thread."""
    try:
        proc = subprocess.Popen(
            ["wl-copy", "--type", mime],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise ClipboardError(
            _("wl-copy was not found. Install it with: sudo apt install wl-clipboard")
        ) from exc

    try:
        proc.communicate(data, timeout=WRITE_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        # A locked screen makes wl-copy block; the clipboard is often set
        # anyway, so confirm before calling it a failure.
        if not _clipboard_offers(mime):
            raise ClipboardError(
                _("Could not write to the clipboard (is the screen locked?).")
            ) from None
        LOG.warning("wl-copy timed out but the selection is present")
        return

    if proc.returncode not in (0, None):
        raise ClipboardError(_("wl-copy exited with status {status}").format(status=proc.returncode))


def _clipboard_offers(mime: str) -> bool:
    try:
        out = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, timeout=2.0, text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return mime in out.stdout
