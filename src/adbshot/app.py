"""The daemon: one process, one capture state machine.

Super+X, the tray's "Capture now", middle-click and the scriptable one-shot
mode all funnel into a single capture() so that debouncing actually holds --
four independent guards would let one trigger bypass another's.

hold() is called unconditionally and is not optional: without it a GApplication
started with --gapplication-service exits after ten idle seconds with status 0,
which Restart=on-failure does not recover. The tray would vanish ten seconds
after every login while systemd reported success.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import clipboard, config, const, feedback as fb, hotkey, i18n, imaging, log, naming, paste  # noqa: E402
from .i18n import _  # noqa: E402
from .capture import CaptureEngine  # noqa: E402
from .devices import DeviceTracker  # noqa: E402
from .tray import MenuItem, StatusNotifierItem  # noqa: E402

LOG = log.get("app")

DEBOUNCE_SECONDS = 0.4


class AdbShotApp(Adw.Application):
    def __init__(self, settings=None):
        super().__init__(
            application_id=const.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.settings = settings
        self.tracker = None
        self.tray = None
        self.feedback = None
        self.engine = CaptureEngine()
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="adbshot-io"
        )
        self._busy = False
        self._last_trigger = 0.0
        # D-Bus activation delivers the capture action within milliseconds of
        # startup, while the tracker's first device list takes ~100 ms to
        # arrive. Without this, the very first Super+X after a cold start fails
        # with "no phone connected" and the capture is simply lost.
        self._tracker_seeded = False
        self._pending_capture = False
        self._pending_timeout = 0
        self._prefs = None
        self._chooser = None
        self._paste_warned = False

    # -- lifecycle --------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        if self.settings is None:
            self.settings = config.load_settings()

        for name, handler in (
            ("capture", lambda *_a: self.capture()),
            ("settings", lambda *_a: self.open_preferences()),
            ("choose-device", lambda *_a: self.open_device_chooser()),
            ("open-folder", lambda *_a: self.open_folder()),
            ("open-last", lambda *_a: self.open_last()),
            ("quit", lambda *_a: self.quit()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        i18n.set_language(self.settings.get_string("language"))
        self.settings.connect("changed::language", self._on_language_changed)

        self.tray = StatusNotifierItem(on_about_to_show=self._on_about_to_show)
        self.tray.start()
        self.tray.set_secondary_action(self.capture)
        self.feedback = fb.Feedback(self.settings, self.tray)

        self.tracker = DeviceTracker(self._on_devices_changed)
        self.tracker.start()
        self._rebuild_menu()

        self.settings.connect("changed::hotkey", lambda *_a: self._sync_hotkey())
        self._first_run()

        # Never lets the service idle out. Must come after the tray exists.
        self.hold()
        LOG.info("%s %s ready", const.APP_TITLE, const.VERSION)

    def do_activate(self):
        # Launching the app from the menu should show something useful rather
        # than silently doing nothing.
        self.open_preferences()

    def do_shutdown(self):
        if self.feedback:
            self.feedback.shutdown()
        if self.tracker:
            self.tracker.stop()
        if self.tray:
            self.tray.stop()
        self._pool.shutdown(wait=False)
        Adw.Application.do_shutdown(self)

    def _on_language_changed(self, *_args) -> None:
        i18n.set_language(self.settings.get_string("language"))
        # The tray menu is rebuilt from source strings every time, so it picks
        # the new language up at once. An open Preferences window keeps the
        # language it was built with until it is reopened.
        self.tray._menu_signature = None
        self._rebuild_menu()

    def _first_run(self) -> None:
        try:
            config.save_dir(self.settings).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            LOG.warning("could not create the save folder: %s", exc)
        # Self-healing: if the shortcut was never installed, or the user removed
        # it in GNOME Settings, put it back.
        if not hotkey.is_installed():
            try:
                hotkey.install(self.settings.get_string("hotkey"))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("could not install the hotkey: %s", exc)

    def _sync_hotkey(self) -> None:
        try:
            hotkey.install(self.settings.get_string("hotkey"))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("could not update the hotkey: %s", exc)

    # -- devices ----------------------------------------------------------

    def _on_devices_changed(self, devices) -> None:
        self._tracker_seeded = True
        self._rebuild_menu()
        if self._chooser is not None:
            self._chooser.refresh(devices)
        if self._pending_capture:
            self._release_pending()
        return False

    def _release_pending(self) -> None:
        if self._pending_timeout:
            GLib.Source.remove(self._pending_timeout)
            self._pending_timeout = 0
        if not self._pending_capture:
            return
        self._pending_capture = False
        self._last_trigger = 0.0          # this attempt must not debounce itself
        self.capture()

    def resolve_device(self):
        """(device, needs_chooser). Never opens UI itself."""
        ready = self.tracker.ready_devices() if self.tracker else []
        if not ready:
            return None, False
        preferred = self.settings.get_string("preferred-device")
        if not self.settings.get_boolean("always-ask-device"):
            if preferred:
                for dev in ready:
                    if dev.serial == preferred:
                        return dev, False
            if len(ready) == 1:
                return ready[0], False
        elif len(ready) == 1 and preferred and ready[0].serial == preferred:
            return ready[0], False
        if len(ready) == 1:
            return ready[0], False
        return None, True

    # -- the one capture path ---------------------------------------------

    def capture(self, *_args) -> None:
        now = time.monotonic()
        if self._busy or (now - self._last_trigger) < DEBOUNCE_SECONDS:
            LOG.debug("capture ignored (busy or debounced)")
            return
        self._last_trigger = now

        if not self._tracker_seeded:
            LOG.debug("tracker still warming up; deferring the capture")
            self._pending_capture = True
            if not self._pending_timeout:
                # If adb never answers, fall through and report it properly
                # rather than waiting forever.
                self._pending_timeout = GLib.timeout_add_seconds(
                    3, lambda: (setattr(self, "_tracker_seeded", True),
                                self._release_pending(), False)[2]
                )
            return

        device, needs_chooser = self.resolve_device()
        if needs_chooser:
            # Kept off the fast path on purpose: a dialog takes focus, which
            # would move the target an auto-paste lands in.
            self.open_device_chooser()
            return
        if device is None:
            self.feedback.capture_failed(
                _("No phone is connected. Plug it in over USB and accept the debugging prompt.")
            )
            return

        self.capture_device(device)

    def capture_device(self, device) -> None:
        """Capture a device already chosen -- skips resolution, keeps the guard."""
        if self._busy:
            return
        self._busy = True
        self._last_trigger = time.monotonic()
        self.feedback.capture_started(device.label)
        self.engine.capture(
            device.serial,
            lambda raw: self._on_raw(raw, device),
            self._on_capture_error,
        )

    def _on_capture_error(self, message: str) -> None:
        self._busy = False
        self.feedback.capture_failed(message)

    def _on_raw(self, raw, device) -> None:
        fmt = self.settings.get_string("image-format")
        quality = self.settings.get_int("quality")
        scale = self.settings.get_int("scale-percent")
        save = self.settings.get_boolean("save-enabled")
        mode = self.settings.get_string("clipboard-mode")
        template = self.settings.get_string("filename-template")
        directory = config.save_dir(self.settings)
        settle = self.settings.get_int("paste-settle-ms") / 1000.0
        shortcut = self.settings.get_string("paste-shortcut")

        future = self._pool.submit(
            self._process_blocking, raw, device, fmt, quality, scale,
            save, mode, template, directory, settle, shortcut,
        )
        future.add_done_callback(
            lambda f: GLib.idle_add(self._on_processed, f, priority=GLib.PRIORITY_DEFAULT)
        )

    def _process_blocking(self, raw, device, fmt, quality, scale,
                          save, mode, template, directory, settle, shortcut) -> dict:
        """Worker thread. Pillow on the main loop freezes it completely."""
        out = imaging.process(raw.png, fmt, quality, scale)
        result = {
            "width": out.width, "height": out.height, "bytes": len(out.data),
            "path": None, "copied": False, "pasted": False, "saved": False,
            "blank": imaging.looks_blank(raw.png), "pixbuf": None, "warning": None,
        }

        if save:
            directory.mkdir(parents=True, exist_ok=True)
            stem = naming.render(
                template, model=device.model, serial=device.serial,
                width=out.width, height=out.height,
            )
            path = naming.unique_path(directory, stem, out.extension)
            path.write_bytes(out.data)
            imaging.mark_file(path)
            result["path"] = str(path)
            result["saved"] = True

        if mode != "off":
            mime = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}[fmt]
            try:
                clipboard.copy_image(out.data, mime)
                result["copied"] = True
            except clipboard.ClipboardError as exc:
                result["warning"] = str(exc)

        if mode == "copy-paste" and result["copied"]:
            time.sleep(settle)
            try:
                paste.paste(shortcut)
                result["pasted"] = True
            except Exception as exc:  # noqa: BLE001
                # The screenshot is already saved and already on the clipboard;
                # a paste hiccup is a warning, never a failed capture.
                LOG.warning("auto-paste failed: %s", exc)
                result["warning"] = str(exc)

        try:
            result["pixbuf"] = imaging.thumbnail(raw.png)
        except Exception:  # noqa: BLE001 - a thumbnail is never worth failing over
            pass
        return result

    def _on_processed(self, future) -> bool:
        self._busy = False
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            LOG.exception("processing failed")
            self.feedback.capture_failed(_("Could not process the capture: {error}").format(error=exc))
            return False

        if result["path"]:
            self.settings.set_string("last-capture", result["path"])

        self._rebuild_menu()

        if result["blank"]:
            self.feedback.capture_failed(_(
                "The capture came out black. The screen is off, or the "
                "foreground app blocks screenshots (FLAG_SECURE)."
            ))
            return False

        if result["warning"] and not result["copied"]:
            self.feedback.capture_failed(result["warning"])
            return False

        if result["warning"] and not self._paste_warned:
            self._paste_warned = True
            LOG.warning("%s", result["warning"])

        self.feedback.capture_succeeded(
            path=result["path"], width=result["width"], height=result["height"],
            size_bytes=result["bytes"], pixbuf=result["pixbuf"],
            copied=result["copied"], pasted=result["pasted"], saved=result["saved"],
        )
        return False

    # -- menu -------------------------------------------------------------

    def _on_about_to_show(self, item_id: int) -> bool:
        # Refresh the model without signalling. Returning True is the protocol's
        # own way to say "re-read the layout before you show it" -- emitting
        # LayoutUpdated here instead would close the popup as it opens.
        return self._rebuild_menu(notify=False)

    def _rebuild_menu(self, notify: bool = True) -> bool:
        if self.tray is None:
            return False
        devices = self.tracker.devices if self.tracker else []
        ready = [d for d in devices if d.ready]
        preferred = self.settings.get_string("preferred-device")

        if ready:
            active, _needs_chooser = self.resolve_device()
            label = active.label if active else _("Choose a device")
        elif devices:
            label = devices[0].status_text()
        else:
            label = "Sin dispositivos"

        device_children = []
        for dev in devices:
            mark = "● " if dev.serial == preferred else "   "
            suffix = "" if dev.ready else f"  ({dev.status_text()})"
            device_children.append(MenuItem(
                key=f"device:{dev.serial}",
                label=f"{mark}{dev.label}{suffix}",
                enabled=dev.ready,
                action=(lambda s=dev.serial: self._set_preferred(s)),
            ))
        if not devices:
            device_children.append(MenuItem(key="device:none", label=_("No devices"), enabled=False))
        device_children.append(MenuItem(key="device:sep", separator=True))
        device_children.append(MenuItem(
            key="device:forget",
            label=_("Forget the remembered device"),
            icon="edit-clear-symbolic",
            enabled=bool(preferred),
            action=lambda: self._set_preferred(""),
        ))

        last = self.settings.get_string("last-capture")
        has_last = bool(last) and Path(last).exists()

        return self.tray.set_menu([
            MenuItem(key="prefs", label=_("Preferences…"), icon="preferences-system-symbolic",
                     action=self.open_preferences),
            MenuItem(key="sep1", separator=True),
            MenuItem(key="capture", label=_("Capture now"), icon="camera-photo-symbolic",
                     enabled=bool(ready), action=self.capture),
            MenuItem(key="device", label=_("Device: {name}").format(name=label), icon="smartphone-symbolic",
                     is_submenu=True, children=device_children),
            MenuItem(key="sep2", separator=True),
            MenuItem(key="open-last", label=_("Open last capture"), icon="image-x-generic-symbolic",
                     enabled=has_last, action=self.open_last),
            MenuItem(key="open-folder", label=_("Open folder"), icon="folder-pictures-symbolic",
                     action=self.open_folder),
            MenuItem(key="sep3", separator=True),
            MenuItem(key="quit", label=_("Quit"), icon="application-exit-symbolic",
                     action=self.quit),
        ], notify=notify)

    def _set_preferred(self, serial: str) -> None:
        self.settings.set_string("preferred-device", serial)
        self._rebuild_menu()

    # -- windows ----------------------------------------------------------

    def open_preferences(self, *_args) -> None:
        from .prefs import PreferencesDialog

        if self._prefs is not None and self._prefs.get_root() is not None:
            self._prefs.present(None)
            return
        self._prefs = PreferencesDialog(self)
        self._prefs.present(None)

    def open_device_chooser(self, *_args) -> None:
        from .chooser import DeviceChooser

        if self._chooser is not None and self._chooser.get_root() is not None:
            self._chooser.present(None)
            return
        self._chooser = DeviceChooser(self)
        self._chooser.present(None)

    def open_folder(self, *_args) -> None:
        directory = config.save_dir(self.settings)
        directory.mkdir(parents=True, exist_ok=True)
        fb._open(str(directory))

    def open_last(self, *_args) -> None:
        last = self.settings.get_string("last-capture")
        if last and Path(last).exists():
            fb._open(last)
        else:
            self.open_folder()
