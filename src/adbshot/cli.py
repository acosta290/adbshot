"""Command line entry point.

Everything that must NOT reach an already-running instance is handled here,
before app.run() is ever called. GApplication forwards unknown arguments to the
running primary, so `--install-hotkey` or `--self-check` would otherwise return
success having done nothing at all -- and pop a Preferences window open in the
middle of `make install`.

Only the bare invocation, --gapplication-service, `capture` and `--settings`
are allowed through, and the latter two go over D-Bus on purpose: that path
also cold-starts the daemon through systemd if it is not running.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import const

USAGE = f"""{const.APP_TITLE} {const.VERSION} — screenshot an adb-connected phone

Usage:
  adbshot                      run the tray daemon (this is what systemd starts)
  adbshot capture              tell the running daemon to capture now
  adbshot --settings           open Preferences
  adbshot --out FILE           one-shot capture straight to FILE, no GUI
  adbshot --self-check         verify the installation and print a report
  adbshot --install-hotkey     register the global shortcut for this user
  adbshot --uninstall-hotkey   remove it
  adbshot --version            print the version

One-shot options (with --out):
  --device SERIAL   capture this device instead of the preferred one
  --format FMT      png | jpeg | webp        (default: the configured format)
  --quality N       1-100                    (default: the configured quality)
  --scale N         10-100 percent           (default: the configured scale)
  --clipboard       also copy to the clipboard (off by default in one-shot mode)
"""

def _apply_language() -> None:
    """Honour the configured language even on the paths that never build a UI."""
    from . import i18n

    try:
        from . import config
        i18n.set_language(config.load_settings().get_string("language"))
    except Exception:  # noqa: BLE001 - a missing schema must not break --help
        i18n.set_language(i18n.DEFAULT)


LOCAL_FLAGS = {
    "--version", "-V", "--help", "-h", "--self-check",
    "--install-hotkey", "--uninstall-hotkey", "--out",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if any(a in ("--help", "-h") for a in argv):
        print(USAGE)
        return 0
    if any(a in ("--version", "-V") for a in argv):
        print(f"{const.APP_TITLE} {const.VERSION}")
        return 0

    from . import log
    log.setup(verbose="--verbose" in argv)
    _apply_language()

    if "--self-check" in argv:
        return self_check()
    if "--install-hotkey" in argv:
        return _hotkey(install=True)
    if "--uninstall-hotkey" in argv:
        return _hotkey(install=False)
    if "--out" in argv:
        return one_shot(argv)

    if argv and argv[0] == "capture":
        return _activate("capture")
    if "--settings" in argv or (argv and argv[0] == "settings"):
        return _activate("settings")

    # Load the settings HERE, not in do_startup. PyGObject swallows an
    # exception raised inside a vfunc: it prints the traceback and the process
    # exits 0, so systemd would report a healthy unit while the daemon does
    # nothing at all.
    from . import config
    from .app import AdbShotApp

    try:
        settings = config.load_settings()
    except config.SchemaMissingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return AdbShotApp(settings).run([sys.argv[0]] + argv)


# -- talking to the running daemon ----------------------------------------

def _activate(action: str) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib

    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        conn.call_sync(
            const.APP_ID, const.OBJECT_PATH, "org.freedesktop.Application",
            "ActivateAction",
            GLib.Variant("(sava{sv})", (action, [], {})),
            None, Gio.DBusCallFlags.NONE, 10000, None,
        )
        return 0
    except GLib.Error as exc:
        print(f"Could not reach the daemon: {exc.message}", file=sys.stderr)
        print("Start it with: systemctl --user start adbshot", file=sys.stderr)
        return 1


def _hotkey(install: bool) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    from . import config, hotkey

    try:
        if install:
            settings = config.load_settings()
            hotkey.install(settings.get_string("hotkey"))
            print(f"Shortcut registered: {settings.get_string('hotkey')}")
        else:
            hotkey.uninstall()
            print("Shortcut removed")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


# -- scriptable one-shot ---------------------------------------------------

def one_shot(argv: list[str]) -> int:
    """A complete capture with no GUI and no running daemon required."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib

    from . import clipboard, config, imaging
    from .capture import CaptureEngine
    from .devices import DeviceTracker

    opts = _parse(argv)
    out = Path(opts["out"]).expanduser()

    try:
        settings = config.load_settings()
        fmt = opts.get("format") or settings.get_string("image-format")
        quality = int(opts.get("quality") or settings.get_int("quality"))
        scale = int(opts.get("scale") or settings.get_int("scale-percent"))
        serial = opts.get("device") or settings.get_string("preferred-device")
    except Exception:  # noqa: BLE001 - one-shot must work without an install
        fmt = opts.get("format") or "png"
        quality = int(opts.get("quality") or 90)
        scale = int(opts.get("scale") or 100)
        serial = opts.get("device") or ""

    if fmt not in ("png", "jpeg", "webp"):
        print(f"error: unknown format: {fmt}", file=sys.stderr)
        return 2

    loop = GLib.MainLoop()
    status = {"code": 1}

    def done(raw):
        try:
            processed = imaging.process(raw.png, fmt, quality, scale)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(processed.data)
            imaging.mark_file(out)
            if "clipboard" in opts:
                mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}[fmt]
                clipboard.copy_image(processed.data, mime)
            print(f"{out}  {processed.width}x{processed.height}  {len(processed.data)} bytes")
            status["code"] = 0
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}", file=sys.stderr)
        loop.quit()

    def failed(message):
        print(f"error: {message}", file=sys.stderr)
        loop.quit()

    if not serial:
        # Resolve a single connected device without standing up the whole app.
        found = {"serial": None, "done": False}

        def on_change(devices):
            ready = [d for d in devices if d.ready]
            if not found["done"]:
                found["done"] = True
                found["serial"] = ready[0].serial if len(ready) >= 1 else None
                loop.quit()
            return False

        tracker = DeviceTracker(on_change)
        tracker.start()
        GLib.timeout_add_seconds(5, loop.quit)
        loop.run()
        tracker.stop()
        serial = found["serial"] or ""
        loop = GLib.MainLoop()

    CaptureEngine().capture(serial, done, failed)
    GLib.timeout_add_seconds(20, loop.quit)
    loop.run()
    return status["code"]


def _parse(argv: list[str]) -> dict:
    opts = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--clipboard":
            opts["clipboard"] = True
        elif arg.startswith("--") and i + 1 < len(argv):
            opts[arg[2:]] = argv[i + 1]
            i += 1
        i += 1
    return opts


# -- self check ------------------------------------------------------------

def self_check() -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib

    rows, failures = [], 0

    def check(label, ok, detail="", fatal=True):
        nonlocal failures
        if not ok and fatal:
            failures += 1
        rows.append((("PASS" if ok else ("FAIL" if fatal else "WARN")), label, detail))

    adb = _which("adb")
    check("adb found", bool(adb), adb or "install it with: sudo apt install adb")
    if adb:
        out = _run([adb, "devices", "-l"])
        from .devices import parse_device_list
        devices = parse_device_list(out)
        ready = [d for d in devices if d.ready]
        check("device ready", bool(ready),
              ", ".join(f"{d.label} ({d.serial})" for d in ready) or
              (", ".join(f"{d.serial}: {d.status_text()}" for d in devices) or "none connected"),
              fatal=False)

    check("wl-copy found", bool(_which("wl-copy")),
          _which("wl-copy") or "install it with: sudo apt install wl-clipboard")
    check("gdbus found", Path("/usr/bin/gdbus").exists(), "/usr/bin/gdbus")

    try:
        from . import config
        settings = config.load_settings()
        check("GSettings schema", True, const.SCHEMA_ID)
        check("destination folder", True, str(config.save_dir(settings)))
    except Exception as exc:  # noqa: BLE001
        check("GSettings schema", False, str(exc).splitlines()[0])
        settings = None

    from . import hotkey
    installed = hotkey.is_installed()
    binding = hotkey.current_binding()
    check("global shortcut registered", installed,
          binding or "register it with: adbshot --install-hotkey", fatal=False)
    if binding:
        conflicts = hotkey.find_conflicts(binding)
        check("shortcut has no conflicts", not conflicts,
              ", ".join(f"{s}:{k}" for s, k in conflicts) or "none", fatal=False)

    unit = _run(["systemctl", "--user", "is-active", const.UNIT]).strip()
    check("service active", unit == "active",
          unit or "start it with: systemctl --user start adbshot", fatal=False)
    if _run(["systemctl", "--user", "is-failed", const.UNIT]).strip() == "failed":
        check("service in failed state", False,
              "clear it with: systemctl --user reset-failed adbshot", fatal=False)

    try:
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = conn.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", (const.APP_ID,)), None,
            Gio.DBusCallFlags.NONE, 3000, None)
        check("daemon on the bus", res.unpack()[0], const.APP_ID, fatal=False)
        res = conn.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)), None,
            Gio.DBusCallFlags.NONE, 3000, None)
        check("system tray available", res.unpack()[0],
              "needs an AppIndicator extension enabled and the session unlocked",
              fatal=False)
    except GLib.Error as exc:
        check("D-Bus session", False, exc.message)

    width = max(len(r[1]) for r in rows)
    print(f"\n  {const.APP_TITLE} {const.VERSION} — self check\n")
    for state, label, detail in rows:
        mark = {"PASS": "✓", "FAIL": "✗", "WARN": "!"}[state]
        print(f"  {mark} {label.ljust(width)}   {detail}")
    print()
    if failures:
        print(f"  {failures} required check(s) failed.\n")
    return 1 if failures else 0


def _which(name: str) -> str:
    from shutil import which
    return which(name) or ""


def _run(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return ""
