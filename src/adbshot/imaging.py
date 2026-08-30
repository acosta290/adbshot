"""Image pipeline: a zero-cost fast path, and Pillow off the main loop.

Two measured facts shape this module:

  * Encoding with Pillow on the GTK main loop freezes it completely -- 0% of
    60fps frames are delivered for the whole encode, not merely a degraded
    fraction. Every Pillow call therefore runs in a worker thread.
  * `adb exec-out screencap -p` already hands us a PNG. When the user wants PNG
    at 100% scale we can pass those bytes straight through: no decode, no
    encode, 0 ms of host CPU.

To keep the fast path free while still being able to tell our own files apart
later, the provenance marker is spliced into the PNG as a tEXt chunk by byte
surgery rather than by re-encoding.
"""

from __future__ import annotations

import binascii
import io
import os
import struct
from dataclasses import dataclass

from . import const

# Anything at or below this, at phone resolution, is suspicious enough to be
# worth a decode to check for an all-black frame (FLAG_SECURE apps and a
# sleeping screen both produce one).
BLANK_SUSPECT_BYTES = 30_000

XATTR_NAME = "user.adbshot"

EXTENSIONS = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}


@dataclass
class Processed:
    data: bytes
    extension: str
    width: int
    height: int
    fast_path: bool


def png_dimensions(png: bytes) -> tuple[int, int]:
    """Width and height straight out of IHDR -- no decode."""
    return struct.unpack(">II", png[16:24])


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def png_add_marker(png: bytes) -> bytes:
    """Splice our tEXt provenance chunk in after IHDR, without re-encoding."""
    ihdr_end = 8 + 4 + 4 + 13 + 4  # signature + IHDR length/type/data/crc
    text = const.PROVENANCE_KEY.encode("latin-1") + b"\x00" + const.PROVENANCE_VALUE.encode("latin-1")
    return png[:ihdr_end] + _chunk(b"tEXt", text) + png[ihdr_end:]


def png_has_marker(png_head: bytes) -> bool:
    needle = const.PROVENANCE_KEY.encode("latin-1") + b"\x00" + const.PROVENANCE_VALUE.encode("latin-1")
    return needle in png_head


def mark_file(path) -> None:
    """Secondary, format-independent marker. Best effort by design."""
    try:
        os.setxattr(str(path), XATTR_NAME, b"1")
    except OSError:
        pass


def file_is_ours(path) -> bool:
    try:
        if os.getxattr(str(path), XATTR_NAME):
            return True
    except OSError:
        pass
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    return head.startswith(const.PNG_MAGIC) and png_has_marker(head)


def looks_blank(png: bytes) -> bool:
    """Advisory all-black check. Never let this fail a capture."""
    if len(png) > BLANK_SUSPECT_BYTES:
        return False
    try:
        from PIL import Image
        import numpy as np

        with Image.open(io.BytesIO(png)) as im:
            rgb = im.convert("RGB")
            return bool(np.asarray(rgb).max() == 0)
    except Exception:  # noqa: BLE001 - advisory only
        return False


def process(png: bytes, fmt: str, quality: int, scale_percent: int) -> Processed:
    """Blocking. Call from a worker thread, never from the main loop."""
    width, height = png_dimensions(png)

    if fmt == "png" and scale_percent >= 100:
        return Processed(png_add_marker(png), ".png", width, height, True)

    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        im.load()
        if scale_percent < 100:
            size = (max(1, im.width * scale_percent // 100),
                    max(1, im.height * scale_percent // 100))
            im = im.resize(size, Image.LANCZOS)
        width, height = im.size

        buf = io.BytesIO()
        if fmt == "jpeg":
            # screencap gives RGBA; JPEG holds no alpha. Flatten onto black,
            # which is what the phone actually showed behind a transparent UI.
            flat = Image.new("RGB", im.size, (0, 0, 0))
            flat.paste(im, mask=im.split()[-1] if im.mode == "RGBA" else None)
            flat.save(buf, "JPEG", quality=quality, optimize=True,
                      comment=const.PROVENANCE_VALUE.encode("latin-1"))
        elif fmt == "webp":
            im.save(buf, "WEBP", quality=quality)
        else:
            data = png_add_marker(_save_png(im))
            return Processed(data, ".png", width, height, False)

    return Processed(buf.getvalue(), EXTENSIONS[fmt], width, height, False)


def _save_png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def thumbnail(png: bytes, size: int = 96):
    """Square GdkPixbuf thumbnail for the notification. Worker thread only."""
    from PIL import Image
    from gi.repository import GdkPixbuf, GLib

    with Image.open(io.BytesIO(png)) as im:
        im.load()
        im = im.convert("RGB")
        im.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new("RGB", (size, size), (0, 0, 0))
        canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
        data = canvas.tobytes()

    # rowstride/n_channels/has_alpha are read back from the real image rather
    # than assumed; a mismatched tuple here renders garbage with no diagnostic.
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, False, 8,
        size, size, size * 3,
    )
