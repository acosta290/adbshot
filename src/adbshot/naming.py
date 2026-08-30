"""Filename templating with collision handling."""

from __future__ import annotations

import datetime
import re
from pathlib import Path

TOKENS = ("modelo", "serie", "fecha", "hora", "ancho", "alto")

_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


def slugify(text: str) -> str:
    return _UNSAFE.sub("_", text.strip()).strip("_") or "captura"


def render(template: str, *, model: str, serial: str, width: int, height: int,
           now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    values = {
        "modelo": slugify(model),
        "serie": slugify(serial),
        "fecha": now.strftime("%Y-%m-%d"),
        "hora": now.strftime("%H-%M-%S"),
        "ancho": str(width),
        "alto": str(height),
    }
    out = template
    for token, value in values.items():
        out = out.replace("{" + token + "}", value)
    # An unknown {token} would otherwise survive into the filename verbatim.
    out = re.sub(r"\{[^}]*\}", "", out)
    return slugify(out)


def unique_path(directory: Path, stem: str, extension: str) -> Path:
    candidate = directory / f"{stem}{extension}"
    if not candidate.exists():
        return candidate
    for n in range(2, 1000):
        candidate = directory / f"{stem}_{n}{extension}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"no free filename for {stem}{extension} in {directory}")
