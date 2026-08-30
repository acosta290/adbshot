"""Auto-paste: synthesising a paste shortcut through Mutter's RemoteDesktop API.

Which shortcut to send is configurable, because "paste an image" is not
universally Ctrl+V. Claude Code's terminal UI, for instance, takes
Ctrl+Super+V for an image paste, and other apps have their own conventions.

The injection goes through NotifyKeyboardKeysym rather than
NotifyKeyboardKeycode: a keysym is exactly what Gtk.accelerator_parse() hands
back, so any combination the user records in Preferences maps straight through
without a keycode table and without caring about the keyboard layout.

Two honest caveats, both verified rather than assumed:

  * Every injection lights GNOME's "screen is being shared" panel indicator,
    and the shell holds it visible for a minimum of five seconds
    (MIN_SHARED_INDICATOR_VISIBLE_TIME_US) however brief our session is.
  * org.gnome.Mutter.RemoteDesktop is a compositor-internal interface, not a
    stable public API.

What it is not is a privilege problem: no root, no udev rule, no group change,
no extra package, no portal consent dialog. XTEST is not an alternative -- it
delivers nothing to native Wayland windows.

Deliberately absent: a "release every modifier first" burst to clear the Super
still held from Super+X. A release cannot clear a modifier physically held on
another device, and as a session's first key event it is rejected outright,
which would abort the paste every time. Waiting is the only real mitigation,
and the ~0.7 s capture already provides most of it.
"""

from __future__ import annotations

import gi
from gi.repository import Gio, GLib

from . import log
from .i18n import _

LOG = log.get("paste")

BUS = "org.gnome.Mutter.RemoteDesktop"
PATH = "/org/gnome/Mutter/RemoteDesktop"
IFACE = "org.gnome.Mutter.RemoteDesktop"
SESSION_IFACE = "org.gnome.Mutter.RemoteDesktop.Session"

INHIBITED = "inhibited"

DEFAULT_SHORTCUT = "<Control>v"

# X11 keysyms for the modifier keys we may need to hold down.
KEYSYM_SHIFT = 0xFFE1
KEYSYM_CONTROL = 0xFFE3
KEYSYM_ALT = 0xFFE9
KEYSYM_SUPER = 0xFFEB
KEYSYM_META = 0xFFE7
KEYSYM_HYPER = 0xFFED


class PasteUnavailable(Exception):
    """Auto-paste cannot run right now. `retryable` distinguishes why."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# -- shortcut handling ----------------------------------------------------

def parse_shortcut(accelerator: str) -> list[tuple[int, bool]]:
    """An accelerator string -> the (keysym, pressed) sequence to inject.

    Modifiers go down first and come up last, in mirror order, so nothing is
    ever left stuck if the sequence is interrupted partway.
    """
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    parsed = Gtk.accelerator_parse(accelerator or DEFAULT_SHORTCUT)
    ok, keyval, mods = parsed if len(parsed) == 3 else (parsed[0] != 0, *parsed)
    if not ok or not keyval:
        raise PasteUnavailable(_("Invalid paste shortcut: {shortcut}").format(shortcut=accelerator))

    modifiers = []
    if mods & Gdk.ModifierType.CONTROL_MASK:
        modifiers.append(KEYSYM_CONTROL)
    if mods & Gdk.ModifierType.ALT_MASK:
        modifiers.append(KEYSYM_ALT)
    if mods & Gdk.ModifierType.SHIFT_MASK:
        modifiers.append(KEYSYM_SHIFT)
    if mods & Gdk.ModifierType.SUPER_MASK:
        modifiers.append(KEYSYM_SUPER)
    if mods & Gdk.ModifierType.META_MASK:
        modifiers.append(KEYSYM_META)
    if mods & Gdk.ModifierType.HYPER_MASK:
        modifiers.append(KEYSYM_HYPER)

    sequence = [(m, True) for m in modifiers]
    sequence.append((keyval, True))
    sequence.append((keyval, False))
    sequence.extend((m, False) for m in reversed(modifiers))
    return sequence


def describe_shortcut(accelerator: str) -> str:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    parsed = Gtk.accelerator_parse(accelerator or DEFAULT_SHORTCUT)
    ok, keyval, mods = parsed if len(parsed) == 3 else (parsed[0] != 0, *parsed)
    if not ok:
        return accelerator
    return Gtk.accelerator_get_label(keyval, mods)


# -- capability probe -----------------------------------------------------

def probe() -> tuple[bool, str]:
    """(available, reason). A locked screen is 'unknown', never 'unavailable'.

    Runs on a throwaway private bus connection and never calls Start(): the
    session dies with the connection, so nothing leaks and -- crucially -- the
    screen-sharing indicator never lights up just for a capability check.
    """
    conn = None
    try:
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        conn = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        conn.call_sync(BUS, PATH, IFACE, "CreateSession", None, None,
                       Gio.DBusCallFlags.NONE, 3000, None)
        return True, _("available")
    except GLib.Error as exc:
        message = exc.message or ""
        if INHIBITED in message.lower():
            # Caching this as "unavailable" would silently disable auto-paste
            # forever for a daemon that happened to start on a locked screen.
            return False, _("unknown (screen locked)")
        return False, message
    finally:
        if conn is not None:
            try:
                conn.close_sync(None)
            except GLib.Error:
                pass


# -- injection ------------------------------------------------------------

def paste(accelerator: str = DEFAULT_SHORTCUT) -> None:
    """Inject the configured shortcut. Blocking but short; worker thread only.

    Everything in here raises PasteUnavailable and nothing else. A raw
    GLib.Error escaping would be caught by the caller's generic handler and
    reported as "the capture failed" -- even though by that point the
    screenshot is already on disk and already on the clipboard.
    """
    sequence = parse_shortcut(accelerator)

    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = conn.call_sync(BUS, PATH, IFACE, "CreateSession", None, None,
                                Gio.DBusCallFlags.NONE, 5000, None)
        session_path = result.unpack()[0]
    except GLib.Error as exc:
        raise _unavailable(exc) from exc

    try:
        try:
            conn.call_sync(BUS, session_path, SESSION_IFACE, "Start", None, None,
                           Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as exc:
            raise _unavailable(exc) from exc

        # Each key is wrapped on its own: one rejected event must never leave a
        # modifier stuck in the down state on the user's real keyboard.
        rejected = 0
        for keysym, pressed in sequence:
            if not _key(conn, session_path, keysym, pressed):
                rejected += 1
        if rejected == len(sequence):
            raise PasteUnavailable(
                _("Could not paste: the compositor rejected every key.")
            )
    finally:
        # Unconditional: this is the shared session bus, not a throwaway
        # connection like probe() uses, so a session left behind here would
        # live for as long as the daemon does.
        try:
            conn.call_sync(BUS, session_path, SESSION_IFACE, "Stop", None, None,
                           Gio.DBusCallFlags.NONE, 3000, None)
        except GLib.Error as exc:
            LOG.debug("session stop failed: %s", exc.message)


def _unavailable(exc: GLib.Error) -> PasteUnavailable:
    message = exc.message or ""
    if INHIBITED in message.lower():
        return PasteUnavailable(
            _("Could not paste: the session is locked."), retryable=True
        )
    return PasteUnavailable(_("Could not paste: {error}").format(error=message))


def _key(conn, session_path: str, keysym: int, pressed: bool) -> bool:
    try:
        conn.call_sync(
            BUS, session_path, SESSION_IFACE, "NotifyKeyboardKeysym",
            GLib.Variant("(ub)", (keysym, pressed)), None,
            Gio.DBusCallFlags.NONE, 3000, None,
        )
        return True
    except GLib.Error as exc:
        LOG.warning("keysym 0x%x %s rejected: %s", keysym,
                    "down" if pressed else "up", exc.message)
        return False
