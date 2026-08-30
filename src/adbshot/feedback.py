"""Shutter sound, animated tray icon and one reusable notification.

Deliberately maps zero windows. A GTK window shown from here would take the
keyboard focus, which is both jarring and -- when the experimental auto-paste
is on -- actively wrong, because it would move the target the paste lands in.

Every channel is independently switchable, individually wrapped, and none of
them is ever awaited on the capture path: feedback must never be able to fail
a capture that otherwise succeeded.
"""

from __future__ import annotations

from pathlib import Path

from gi.repository import Gio, GLib

from . import const, log
from .i18n import _

LOG = log.get("feedback")

# 120 ms/frame. The measured floor on this shell is 100 ms (below it, frames
# are dropped rather than shown), so this leaves a little headroom.
FRAME_MS = 120
IDLE_ICON = "smartphone-symbolic"
BUSY_FRAMES = ("camera-photo-symbolic", "smartphone-symbolic")
ERROR_ICON = "dialog-error-symbolic"

SHUTTER_SOUND = "camera-shutter"


class Feedback:
    def __init__(self, settings, tray):
        self._settings = settings
        self._tray = tray
        self._anim_id = 0
        self._frame = 0
        self._notification = None
        self._notify_ready = False
        self._sound = None
        self._sound_settings = None
        self._init_sound()
        self._init_notify()

    # -- setup ------------------------------------------------------------

    def _init_sound(self) -> None:
        try:
            import gi
            gi.require_version("GSound", "1.0")
            from gi.repository import GSound

            self._sound = GSound.Context()
            self._sound.init()
            # libcanberra does not honour the desktop's own event-sounds switch,
            # so we check it ourselves rather than ignoring the user's setting.
            source = Gio.SettingsSchemaSource.get_default()
            if source and source.lookup("org.gnome.desktop.sound", True):
                self._sound_settings = Gio.Settings.new("org.gnome.desktop.sound")
        except Exception as exc:  # noqa: BLE001
            LOG.debug("sound unavailable: %s", exc)
            self._sound = None

    def _init_notify(self) -> None:
        try:
            import gi
            gi.require_version("Notify", "0.7")
            from gi.repository import Notify

            Notify.init(const.APP_TITLE)
            self._notify_ready = True
        except Exception as exc:  # noqa: BLE001
            LOG.debug("notifications unavailable: %s", exc)

    # -- channels ---------------------------------------------------------

    def _sound_allowed(self) -> bool:
        if not self._sound or not self._settings.get_boolean("sound-enabled"):
            return False
        if self._sound_settings and not self._sound_settings.get_boolean("event-sounds"):
            return False
        return True

    def _play_shutter(self) -> None:
        if not self._sound_allowed():
            return
        try:
            self._sound.play_simple({"event.id": SHUTTER_SOUND}, None)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("shutter sound failed: %s", exc)

    def _start_animation(self) -> None:
        self._stop_animation()
        self._frame = 0

        def tick():
            self._frame = (self._frame + 1) % len(BUSY_FRAMES)
            self._tray.set_icon(BUSY_FRAMES[self._frame])
            return True

        self._tray.set_icon(BUSY_FRAMES[0])
        self._anim_id = GLib.timeout_add(FRAME_MS, tick)

    def _stop_animation(self) -> None:
        if self._anim_id:
            GLib.Source.remove(self._anim_id)
            self._anim_id = 0

    def _notify(self, summary, body, *, urgent=False, pixbuf=None, path=None) -> None:
        if not self._notify_ready or not self._settings.get_boolean("notification-enabled"):
            return
        try:
            from gi.repository import Notify

            if self._notification is None:
                self._notification = Notify.Notification.new(summary, body, None)
            else:
                # Reusing one Notification keeps one id, so ten captures in a
                # row update a single banner instead of stacking ten.
                self._notification.clear_actions()
                self._notification.update(summary, body, None)

            note = self._notification
            note.set_hint("desktop-entry", GLib.Variant("s", const.APP_ID))
            note.set_urgency(Notify.Urgency.CRITICAL if urgent else Notify.Urgency.NORMAL)
            if pixbuf is not None:
                note.set_image_from_pixbuf(pixbuf)
            else:
                note.set_hint("image-path", GLib.Variant("s", ERROR_ICON if urgent else IDLE_ICON))
            if path:
                # A NORMAL banner arrives collapsed, so its buttons are not one
                # click away; "default" is what activating the banner itself hits.
                note.add_action("default", _("Open"), lambda *_a: _open(path))
                note.add_action("open-folder", _("Open folder"),
                                lambda *_a: _open(str(Path(path).parent)))
            note.show()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("notification failed: %s", exc)

    # -- capture lifecycle ------------------------------------------------

    def capture_started(self, device_label: str = "") -> None:
        self._play_shutter()
        self._start_animation()
        self._tray.set_tooltip(
            _("Capturing {device}…").format(device=device_label)
            if device_label else _("Capturing…")
        )

    def capture_succeeded(self, *, path, width, height, size_bytes, pixbuf=None,
                          copied: bool, pasted: bool, saved: bool) -> None:
        self._stop_animation()
        self._tray.set_attention(False)
        self._tray.set_icon(IDLE_ICON)
        self._tray.set_tooltip(const.APP_TITLE)

        bits = [f"{width}×{height}", _human(size_bytes)]
        if copied:
            bits.append(_("pasted") if pasted else _("on the clipboard"))
        summary = _("Capture ready")
        body = " · ".join(bits)
        if saved and path:
            body += f"\n{Path(path).name}"
        self._notify(summary, body, pixbuf=pixbuf, path=path if saved else None)

    def capture_failed(self, message: str) -> None:
        self._stop_animation()
        self._tray.set_icon(ERROR_ICON)
        self._tray.set_attention(True, ERROR_ICON)
        self._tray.set_tooltip(message)
        LOG.error("capture failed: %s", message)
        # CRITICAL punches through Do Not Disturb; a failed capture that says
        # nothing at all is the one outcome we never want.
        self._notify(_("Could not capture"), message, urgent=True)

    def clear_error(self) -> None:
        self._tray.set_attention(False)
        self._tray.set_icon(IDLE_ICON)
        self._tray.set_tooltip(const.APP_TITLE)

    def shutdown(self) -> None:
        self._stop_animation()


def _open(path: str) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(str(path), None), None)
    except GLib.Error as exc:
        LOG.warning("could not open %s: %s", path, exc.message)


def _human(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
