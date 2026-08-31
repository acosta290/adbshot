#!/usr/bin/env python3
"""Exercise the multi-display parser against recorded `dumpsys display` shapes.

Every fixture below is trimmed from a real dump -- a Galaxy Z Fold 8 Ultra for
the two-screen cases, a single-screen phone for the rest. The fields kept are
exactly the ones displays.py reads; the hundreds of bytes of mode tables and
HDR capabilities that sat between them are dropped, because nothing looks at
them and their absence must not change the answer.

Run by `make lint`, so a regression in the picker shows up without a phone
plugged in.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from adbshot import displays  # noqa: E402
from adbshot.capture import split_preamble  # noqa: E402
from adbshot.const import PNG_MAGIC  # noqa: E402

INNER = "4630946722019192211"
COVER = "4630946379103915412"


def viewports(*entries) -> str:
    blocks = ", ".join(
        "DisplayViewport{type=INTERNAL, valid=true, isActive=%s, displayId=%d, "
        "uniqueId='local:%s', physicalPort=%d}" % (active, i, ident, 147 + i)
        for i, (ident, active) in enumerate(entries)
    )
    return "  mViewports=[%s]" % blocks


def device(ident: str, w: int, h: int, state: str, default: bool = True) -> str:
    flags = ", FLAG_ALLOWED_TO_BE_DEFAULT_DISPLAY" if default else ""
    return (
        '  DisplayDeviceInfo{"Pantalla integrada": uniqueId="local:%s", %d x %d, '
        "modeId 1, density 480, touch INTERNAL, rotation 0, type INTERNAL, "
        "address StablePhysical{id=%s, port=147}, state %s, committedState %s, "
        "brightnessMinimum 0.0%s, installOrientation 3}"
        % (ident, w, h, ident, state, state, flags)
    )


FOLDED = "\n".join([
    viewports((COVER, "true"), (INNER, "false")),
    device(INNER, 2256, 2504, "OFF"),
    device(COVER, 1080, 2520, "ON"),
])

UNFOLDED = "\n".join([
    viewports((INNER, "true"), (COVER, "false")),
    device(INNER, 2256, 2504, "ON"),
    device(COVER, 1080, 2520, "OFF"),
])

# Both asleep: whichever is chosen the frame is black, but the choice must be
# the same every time rather than screencap's "first display found".
ASLEEP = "\n".join([
    viewports((COVER, "false"), (INNER, "false")),
    device(INNER, 2256, 2504, "OFF"),
    device(COVER, 1080, 2520, "OFF"),
])

# The unfolded screen is lit while the cover shows its always-on clock. DOZE is
# not "on": it is a clock, not the session the user means to capture.
DOZING_COVER = "\n".join([
    viewports((INNER, "true"), (COVER, "false")),
    device(INNER, 2256, 2504, "ON"),
    device(COVER, 1080, 2520, "DOZE"),
])

# Two screens lit at once (an HDMI dongle, DeX). The input viewport decides.
BOTH_ON = "\n".join([
    viewports((COVER, "true"), (INNER, "false")),
    device(INNER, 2256, 2504, "ON"),
    device(COVER, 1080, 2520, "ON"),
])

ORDINARY_PHONE = "\n".join([
    viewports(("4619827259835644672", "true")),
    device("4619827259835644672", 1080, 2400, "ON"),
])

# A virtual display (screen recording, a cast) has no local: id and cannot be
# passed to -d. It must not be counted as a second screen.
WITH_VIRTUAL = "\n".join([
    viewports(("4619827259835644672", "true")),
    device("4619827259835644672", 1080, 2400, "ON"),
    '  DisplayDeviceInfo{"ScreenRecorder": uniqueId="virtual:android:1", '
    "1080 x 2400, state ON, committedState ON}",
])

failures = []


def expect(name, got, want):
    if got != want:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def chosen(payload):
    display = displays.pick(displays.parse(payload))
    return display.physical_id if display else None


expect("folded picks the cover screen", chosen(FOLDED), COVER)
expect("unfolded picks the inner screen", chosen(UNFOLDED), INNER)
expect("a dozing cover never wins", chosen(DOZING_COVER), INNER)
expect("two lit screens follow the input viewport", chosen(BOTH_ON), COVER)
expect("both asleep resolves deterministically", chosen(ASLEEP), INNER)
expect("one screen needs no -d", chosen(ORDINARY_PHONE), None)
expect("a virtual display is not a second screen", chosen(WITH_VIRTUAL), None)
expect("no output means no pinning", chosen(""), None)
expect("garbage means no pinning", chosen("could not access dumpsys"), None)

parsed = displays.parse(FOLDED)
expect("both screens are parsed", len(parsed), 2)
expect("size is parsed", (parsed[0].width, parsed[0].height), (2256, 2504))
expect("state is the device's, not committedState", parsed[1].state, "ON")
expect("the live viewport is flagged", parsed[1].active, True)

# The other half of the bug: screencap writes its warning on stdout, ahead of
# the PNG, so the payload no longer *starts* with the signature.
WARNING = (
    b"[Warning] Multiple displays were found, but no display id was specified! "
    b"Defaulting to the first display found, however this default is not "
    b'guaranteed to be consistent across captures.\nSee "dumpsys SurfaceFlinger '
    b'--display-id" for valid display IDs.\n'
)
png = PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 40

expect("a clean PNG is untouched", split_preamble(png), (png, b""))
expect("the warning is stripped", split_preamble(WARNING + png), (png, WARNING))
expect("an error with no PNG stays an error", split_preamble(b"error: closed")[0], None)
expect("a PNG buried past the cap is not trusted",
       split_preamble(b"x" * 9000 + png)[0], None)

if failures:
    print("display selection is wrong:")
    for line in failures:
        print("  " + line)
    sys.exit(1)
print("ok: multi-display selection and preamble stripping behave")
