"""Registering Super+X as a GNOME custom keybinding.

The app cannot grab the accelerator itself: org.gnome.Shell.GrabAccelerator is
gated by a sender allowlist containing exactly org.gnome.Settings and
org.gnome.SettingsDaemon.MediaKeys, so gsd-media-keys' custom-keybinding
mechanism is the only sanctioned route.

The command it runs is `gdbus`, not our own CLI. A C client costs ~2 ms against
a live GApplication; forwarding through a second Python process costs ~40-50 ms
and, worse, makes the hotkey-spawned process the primary -- producing a daemon
running outside systemd while systemctl reports the unit inactive. gdbus also
takes a --timeout, so a wedged trigger self-clears instead of lingering.

gsd-media-keys watches changed::custom-keybindings, so a rebind takes effect
immediately with no logout.
"""

from __future__ import annotations

from gi.repository import Gio, GLib

from . import const, log
from .i18n import _

LOG = log.get("hotkey")

TRIGGER_COMMAND = (
    "/usr/bin/gdbus call --session --timeout 10 "
    f"--dest {const.APP_ID} --object-path {const.OBJECT_PATH} "
    "--method org.freedesktop.Application.ActivateAction capture [] {}"
)


def _media_keys() -> Gio.Settings:
    return Gio.Settings.new(const.MEDIA_KEYS_SCHEMA)


def _binding(path: str) -> Gio.Settings:
    return Gio.Settings.new_with_path(const.CUSTOM_KEYBINDING_SCHEMA, path)


def is_installed() -> bool:
    paths = _media_keys().get_strv("custom-keybindings")
    return const.CUSTOM_KEYBINDING_PATH in paths


def current_binding() -> str:
    if not is_installed():
        return ""
    return _binding(const.CUSTOM_KEYBINDING_PATH).get_string("binding")


def install(accelerator: str = const.DEFAULT_HOTKEY) -> None:
    """Idempotent. Values are written before the path is listed, so gsd never
    sees a listed-but-empty binding."""
    entry = _binding(const.CUSTOM_KEYBINDING_PATH)
    entry.set_string("name", _("adbshot: capture the phone screen"))
    entry.set_string("command", TRIGGER_COMMAND)
    entry.set_string("binding", accelerator)
    Gio.Settings.sync()

    media = _media_keys()
    paths = media.get_strv("custom-keybindings")
    if const.CUSTOM_KEYBINDING_PATH not in paths:
        paths.append(const.CUSTOM_KEYBINDING_PATH)
        media.set_strv("custom-keybindings", paths)
    Gio.Settings.sync()
    LOG.info("hotkey %s installed", accelerator)


def uninstall() -> None:
    media = _media_keys()
    paths = [p for p in media.get_strv("custom-keybindings")
             if p != const.CUSTOM_KEYBINDING_PATH]
    media.set_strv("custom-keybindings", paths)
    entry = _binding(const.CUSTOM_KEYBINDING_PATH)
    for key in ("name", "command", "binding"):
        entry.reset(key)
    Gio.Settings.sync()
    LOG.info("hotkey removed")


# -- conflict detection ---------------------------------------------------

# Schemas whose string/strv values are accelerators worth checking.
_SKIP_PREFIXES = ("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding",)


def find_conflicts(accelerator: str) -> list[tuple[str, str]]:
    """Every other setting that binds the same accelerator. ~12 ms."""
    if not accelerator:
        return []
    wanted = _normalise(accelerator)
    hits = []
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return []
    ids, relocatable = source.list_schemas(True)
    for schema_id in ids:
        if schema_id.startswith(_SKIP_PREFIXES):
            continue
        if not any(k in schema_id for k in ("keybindings", "media-keys", "wm.keybindings", "shell")):
            continue
        try:
            settings = Gio.Settings.new(schema_id)
        except Exception:  # noqa: BLE001
            continue
        for key in settings.list_keys():
            try:
                value = settings.get_value(key)
            except Exception:  # noqa: BLE001
                continue
            kind = value.get_type_string()
            values = []
            if kind == "s":
                values = [value.get_string()]
            elif kind == "as":
                values = list(value.get_strv())
            for candidate in values:
                if candidate and _normalise(candidate) == wanted:
                    hits.append((schema_id, key))
                    break
    # Our own entry is not a conflict.
    return hits


def _normalise(accel: str) -> str:
    ok, keyval, mods = Gtk_accelerator_parse(accel)
    if not ok:
        return accel.lower().replace(" ", "")
    return f"{mods}:{keyval}"


def Gtk_accelerator_parse(accel: str):
    from gi.repository import Gtk

    result = Gtk.accelerator_parse(accel)
    # GTK4 returns (ok, keyval, mods)
    if len(result) == 3:
        return result
    keyval, mods = result
    return (keyval != 0, keyval, mods)
