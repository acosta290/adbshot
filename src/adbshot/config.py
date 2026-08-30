"""Settings access that cannot brick the daemon.

Gio.Settings.new() on a missing schema calls g_error() and takes the process
down with exit 133. Under a systemd unit that becomes a crash loop, the unit
lands in `failed`, and a failed unit also kills the D-Bus activation that was
supposed to resurrect us -- so Super+X becomes a permanent silent no-op.

We therefore always look the schema up first and fail with a readable message.
"""

from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Gio, GLib

from . import const


class SchemaMissingError(RuntimeError):
    """The compiled GSettings schema could not be found anywhere."""


def _dev_schema_dir() -> Path | None:
    """build/schemas next to a source checkout, so the app runs from a clone."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "build" / "schemas" / "gschemas.compiled"
        if candidate.exists():
            return candidate.parent
    return None


def load_settings() -> Gio.Settings:
    """Return the app's Gio.Settings, or raise SchemaMissingError."""
    source = Gio.SettingsSchemaSource.get_default()

    # A checkout's own build/schemas wins. Otherwise `make run` would silently
    # use the installed schema, and editing the schema during development would
    # appear to do nothing -- or abort once a new key is referenced.
    dev_dir = _dev_schema_dir()
    if dev_dir is not None:
        dev_source = Gio.SettingsSchemaSource.new_from_directory(
            str(dev_dir), source, False
        )
        schema = dev_source.lookup(const.SCHEMA_ID, False)
        if schema is not None:
            return Gio.Settings.new_full(schema, None, None)

    if source is not None:
        schema = source.lookup(const.SCHEMA_ID, True)
        if schema is not None:
            return Gio.Settings.new_full(schema, None, None)

    raise SchemaMissingError(
        f"GSettings schema {const.SCHEMA_ID} not found.\n"
        f"  Installed?  run: sudo make install\n"
        f"  From source? run: make schemas"
    )


def default_save_dir() -> Path:
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    base = Path(pictures) if pictures else Path.home() / "Pictures"
    return base / const.APP_NAME


def save_dir(settings: Gio.Settings) -> Path:
    configured = settings.get_string("save-directory").strip()
    if not configured:
        return default_save_dir()
    return Path(os.path.expanduser(configured))
