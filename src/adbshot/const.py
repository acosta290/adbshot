"""Frozen identity for adbshot.

Every one of these strings ends up in a place where a typo is silent, not loud:
the D-Bus well-known name, the auto-derived object path, the systemd unit, the
GSettings schema id and its dconf path, the .desktop filename and the
notification "desktop-entry" hint. They are defined once, here, and imported
everywhere else -- never written as a literal.

The id is deliberately dash-free: GApplication rewrites a dash in the app id
into an underscore when deriving the object path, which would silently break
the hotkey's gdbus call.
"""

APP_ID = "io.github.acosta290.AdbShot"
OBJECT_PATH = "/io/github/acosta290/AdbShot"
SCHEMA_ID = "io.github.acosta290.AdbShot"
SCHEMA_PATH = "/io/github/acosta290/AdbShot/"

APP_NAME = "adbshot"
APP_TITLE = "adbshot"
UNIT = "adbshot.service"
DESKTOP_FILE = f"{APP_ID}.desktop"

VERSION = "1.0.0"

# The dconf slot our Super+X binding lives in. gsd-media-keys watches
# `changed::custom-keybindings` so a rebind takes effect live, no logout.
MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEYBINDING_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
CUSTOM_KEYBINDING_PATH = (
    "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/adbshot/"
)

DEFAULT_HOTKEY = "<Super>x"

# PNG signature. Checked before anything else touches capture output, because
# `screencap` can fail with exit status 0 and a plain-ASCII error on stdout.
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Embedded in every PNG we write, so "empty the folder" can tell our own files
# from anything else the user keeps there. NUL-separated, per the tEXt spec.
PROVENANCE_KEY = "Software"
PROVENANCE_VALUE = "adbshot"
