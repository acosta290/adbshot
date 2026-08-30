"""Emptying the screenshots folder, safely.

The folder is the user's own -- they may well keep other things in it -- so
"empty" never means "delete everything here". Only files we can prove we wrote
are touched, identified by the provenance marker embedded at capture time, and
they go to the Trash rather than being unlinked, so a mistake is recoverable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

from . import imaging, log

LOG = log.get("cleanup")

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class Survey:
    files: list
    total_bytes: int
    skipped: int          # files in the folder that are not ours

    @property
    def count(self) -> int:
        return len(self.files)


def survey(directory: Path, exclude: set | None = None) -> Survey:
    exclude = {str(p) for p in (exclude or set())}
    ours, total, skipped = [], 0, 0
    if not directory.is_dir():
        return Survey([], 0, 0)
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.is_symlink():
            continue
        if entry.suffix.lower() not in SUFFIXES:
            skipped += 1
            continue
        if str(entry) in exclude:
            continue          # a capture still being written is not fair game
        if imaging.file_is_ours(entry):
            ours.append(entry)
            try:
                total += entry.stat().st_size
            except OSError:
                pass
        else:
            skipped += 1
    return Survey(ours, total, skipped)


def empty(files) -> tuple[int, list[str]]:
    """Send each file to the Trash. Never falls back to unlink."""
    trashed, errors = 0, []
    for path in files:
        gfile = Gio.File.new_for_path(str(path))
        try:
            gfile.trash(None)
            trashed += 1
        except GLib.Error as exc:
            LOG.warning("could not trash %s: %s", path, exc.message)
            errors.append(f"{Path(path).name}: {exc.message}")
    return trashed, errors


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
