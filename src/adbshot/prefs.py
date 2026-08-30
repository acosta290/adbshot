"""Preferences.

Note on binding: Gio.Settings.bind() only carries the int and bool keys here.
Both enum keys (image-format, clipboard-mode) need hand-written two-way glue --
binding a ComboRow's selected index straight to an enum key silently does the
wrong thing.
"""

from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from . import cleanup, config, const, hotkey, i18n, log, naming, paste
from .i18n import _

LOG = log.get("prefs")

# Labels are resolved when the window is built, not at import time, so a
# language change applies the next time Preferences is opened.
def _formats():
    return [
        ("png", "PNG", _("Lossless. Best for text and interface screenshots.")),
        ("jpeg", "JPEG", _("Lossy. Much smaller files.")),
        ("webp", "WebP", _("Lossy, and more efficient than JPEG.")),
    ]


def _clipboard_modes():
    return [
        ("copy", _("Copy to the clipboard")),
        ("copy-paste", _("Copy and paste automatically (experimental)")),
        ("off", _("Do not use the clipboard")),
    ]


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = app.settings
        self._guard = False
        self.set_title(_("adbshot Preferences"))
        self.set_search_enabled(True)

        self.add(self._page_capture())
        self.add(self._page_storage())
        self.add(self._page_behaviour())

    # -- helpers ----------------------------------------------------------

    def _toast(self, text: str) -> None:
        self.add_toast(Adw.Toast.new(text))

    def _combo(self, title, subtitle, key, options):
        """Two-way glue between an enum GSettings key and an Adw.ComboRow."""
        values = [v for v, *_ in options]
        model = Gtk.StringList.new([label for _value, label, *_rest in options])
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=model)

        def to_ui(*_a):
            current = self.settings.get_string(key)
            if current in values:
                self._guard = True
                row.set_selected(values.index(current))
                self._guard = False

        def to_settings(*_a):
            if self._guard:
                return
            index = row.get_selected()
            if 0 <= index < len(values):
                self.settings.set_string(key, values[index])

        to_ui()
        row.connect("notify::selected", to_settings)
        self.settings.connect(f"changed::{key}", to_ui)
        return row

    # -- pages ------------------------------------------------------------

    def _page_capture(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=_("Capture"), icon_name="camera-photo-symbolic")

        group = Adw.PreferencesGroup(title=_("Image"))
        fmt_row = self._combo(
            _("Format"), _("The format each capture is saved and copied in"),
            "image-format", _formats(),
        )
        group.add(fmt_row)

        quality = Adw.SpinRow.new_with_range(1, 100, 1)
        quality.set_title(_("Quality"))
        quality.set_subtitle(_("Only applies to lossy formats"))
        self.settings.bind("quality", quality, "value", Gio.SettingsBindFlags.DEFAULT)
        group.add(quality)

        scale = Adw.SpinRow.new_with_range(10, 100, 5)
        scale.set_title(_("Output scale"))
        scale.set_subtitle(_("Percentage of the phone's own resolution"))
        self.settings.bind("scale-percent", scale, "value", Gio.SettingsBindFlags.DEFAULT)
        group.add(scale)

        def sync_quality(*_a):
            lossy = self.settings.get_string("image-format") != "png"
            # Kept visible rather than hidden: a row that vanishes reflows the
            # whole dialog and hides the explanation of why it is unavailable.
            quality.set_sensitive(lossy)
            quality.set_subtitle(
                _("Only applies to lossy formats") if lossy
                else _("PNG is lossless, so quality does not apply")
            )

        sync_quality()
        self.settings.connect("changed::image-format", sync_quality)
        page.add(group)

        device = Adw.PreferencesGroup(
            title=_("Device"),
            description=_("With several phones connected the remembered one is used; "
                          "the chooser appears only when it is needed."),
        )
        ask = Adw.SwitchRow(title=_("Always ask which device to use"))
        self.settings.bind("always-ask-device", ask, "active", Gio.SettingsBindFlags.DEFAULT)
        device.add(ask)

        current = Adw.ActionRow(title=_("Remembered device"))
        forget = Gtk.Button(label=_("Forget"), valign=Gtk.Align.CENTER)
        forget.connect("clicked", lambda *_a: self.settings.set_string("preferred-device", ""))
        current.add_suffix(forget)

        def sync_device(*_a):
            serial = self.settings.get_string("preferred-device")
            dev = self.app.tracker.find(serial) if (serial and self.app.tracker) else None
            current.set_subtitle(f"{dev.label} · {serial}" if dev else (serial or _("None")))
            forget.set_sensitive(bool(serial))

        sync_device()
        self.settings.connect("changed::preferred-device", sync_device)
        device.add(current)

        choose = Adw.ActionRow(title=_("Choose a device now"))
        pick = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER)
        pick.connect("clicked", lambda *_a: self.app.open_device_chooser())
        choose.add_suffix(pick)
        device.add(choose)
        page.add(device)
        return page

    def _page_storage(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=_("Files"), icon_name="folder-pictures-symbolic")

        group = Adw.PreferencesGroup(title=_("Saving"))
        save = Adw.SwitchRow(
            title=_("Save every capture to disk"),
            subtitle=_("Turn this off and the capture only reaches the clipboard"),
        )
        self.settings.bind("save-enabled", save, "active", Gio.SettingsBindFlags.DEFAULT)
        group.add(save)

        folder = Adw.ActionRow(title=_("Destination folder"))
        choose = Gtk.Button(label=_("Change…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._choose_folder)
        folder.add_suffix(choose)

        def sync_folder(*_a):
            folder.set_subtitle(str(config.save_dir(self.settings)))

        sync_folder()
        self.settings.connect("changed::save-directory", sync_folder)
        group.add(folder)

        template = Adw.EntryRow(title=_("Filename template"))
        template.set_text(self.settings.get_string("filename-template"))
        template.connect(
            "changed",
            lambda row: self.settings.set_string("filename-template", row.get_text()),
        )
        group.add(template)

        hint = Adw.ActionRow(
            title=_("Available tokens"),
            subtitle="  ".join("{" + t + "}" for t in naming.TOKENS),
        )
        hint.set_activatable(False)
        group.add(hint)
        page.add(group)

        danger = Adw.PreferencesGroup(
            title=_("Empty the folder"),
            description=_("Only captures created by adbshot are removed, and they go "
                          "to the Trash. Anything else you keep in that folder is "
                          "left alone."),
        )
        self._empty_row = Adw.ActionRow(title=_("adbshot captures in the folder"))
        empty_button = Gtk.Button(label=_("Empty…"), valign=Gtk.Align.CENTER)
        empty_button.add_css_class("destructive-action")
        empty_button.connect("clicked", self._confirm_empty)
        self._empty_row.add_suffix(empty_button)
        self._empty_button = empty_button
        danger.add(self._empty_row)
        page.add(danger)
        self._refresh_survey()
        return page

    def _page_behaviour(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title=_("Behaviour"), icon_name="preferences-system-symbolic")

        general = Adw.PreferencesGroup(title=_("Language"))
        general.add(self._combo(
            _("Interface language"),
            _("Takes effect immediately for the tray menu; reopen this window "
              "to apply it here."),
            "language", i18n.LANGUAGES,
        ))
        page.add(general)

        clip = Adw.PreferencesGroup(title=_("Clipboard"))
        clip.add(self._combo(
            _("What to do after a capture"),
            _("Auto-paste is experimental: it lights GNOME's screen-sharing "
              "indicator for 5 s on every capture."),
            "clipboard-mode", _clipboard_modes(),
        ))

        self._paste_row = Adw.ActionRow(
            title=_("Shortcut sent when pasting"),
            subtitle=_("Ctrl+V works almost everywhere. Claude's CLI pastes images "
                       "with Ctrl+Super+V."),
        )
        change_paste = Gtk.Button(label=_("Change…"), valign=Gtk.Align.CENTER)
        change_paste.connect("clicked", self._change_paste_shortcut)
        test_paste = Gtk.Button(label=_("Test in 3 s"), valign=Gtk.Align.CENTER)
        test_paste.set_tooltip_text(
            _("Focus the window you want to paste into; the shortcut will be sent there.")
        )
        test_paste.connect("clicked", self._test_paste_shortcut)
        self._paste_row.add_suffix(test_paste)
        self._paste_row.add_suffix(change_paste)
        clip.add(self._paste_row)
        self._refresh_paste_shortcut()

        status = Adw.ActionRow(title=_("Auto-paste status"))
        check = Gtk.Button(label=_("Check"), valign=Gtk.Align.CENTER)

        def probe(*_a):
            ok, why = paste.probe()
            status.set_subtitle((_("Available") if ok else _("Not available")) + f" — {why}")

        check.connect("clicked", probe)
        status.add_suffix(check)
        status.set_subtitle(_("Press \u201cCheck\u201d"))
        clip.add(status)
        page.add(clip)

        keys = Adw.PreferencesGroup(
            title=_("Global shortcut"),
            description=_("gsd-media-keys applies the change immediately, with no "
                          "need to log out."),
        )
        self._hotkey_row = Adw.ActionRow(title=_("Shortcut to capture"))
        change = Gtk.Button(label=_("Change…"), valign=Gtk.Align.CENTER)
        change.connect("clicked", self._change_hotkey)
        self._hotkey_row.add_suffix(change)
        keys.add(self._hotkey_row)
        self._refresh_hotkey()
        page.add(keys)

        alerts = Adw.PreferencesGroup(title=_("Alerts"))
        sound = Adw.SwitchRow(title=_("Shutter sound when capturing"))
        self.settings.bind("sound-enabled", sound, "active", Gio.SettingsBindFlags.DEFAULT)
        alerts.add(sound)
        note = Adw.SwitchRow(
            title=_("Notification when finished"),
            subtitle=_("With a thumbnail of the capture. Errors always notify."),
        )
        self.settings.bind("notification-enabled", note, "active", Gio.SettingsBindFlags.DEFAULT)
        alerts.add(note)
        page.add(alerts)
        return page

    # -- actions ----------------------------------------------------------

    def _choose_folder(self, *_a) -> None:
        dialog = Gtk.FileDialog(title=_("Folder for captures"))
        dialog.set_initial_folder(
            Gio.File.new_for_path(str(config.save_dir(self.settings)))
        )

        def done(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder:
                self.settings.set_string("save-directory", folder.get_path())
                self._refresh_survey()

        dialog.select_folder(self.get_root(), None, done)

    def _refresh_survey(self) -> None:
        directory = config.save_dir(self.settings)
        result = cleanup.survey(directory)
        self._survey = result
        if result.count:
            subtitle = _("{count} file(s) · {size}").format(
                count=result.count, size=cleanup.human_size(result.total_bytes))
            if result.skipped:
                subtitle += _(" · {count} other file(s) will be kept").format(count=result.skipped)
            self._empty_row.set_subtitle(subtitle)
        else:
            self._empty_row.set_subtitle(_("No adbshot captures to remove"))
        self._empty_button.set_sensitive(bool(result.count))

    def _confirm_empty(self, *_a) -> None:
        self._refresh_survey()
        result = self._survey
        if not result.count:
            self._toast(_("Nothing to empty"))
            return
        body = _("{count} capture(s) ({size}) from\n{folder}\nwill be moved to the Trash.").format(
            count=result.count, size=cleanup.human_size(result.total_bytes),
            folder=config.save_dir(self.settings))
        if result.skipped:
            body += _("\n\nThe other {count} file(s) in the folder will not be touched.").format(
                count=result.skipped)
        dialog = Adw.AlertDialog(heading=_("Empty the captures?"), body=body)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("empty", _("Empty"))
        dialog.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(source, result_obj):
            if source.choose_finish(result_obj) != "empty":
                return
            trashed, errors = cleanup.empty(self._survey.files)
            self._refresh_survey()
            if errors:
                self._toast(_("{trashed} to the Trash, {failed} failed").format(
                    trashed=trashed, failed=len(errors)))
            else:
                self._toast(_("{count} capture(s) moved to the Trash").format(count=trashed))

        dialog.choose(self, None, answered)

    def _refresh_paste_shortcut(self) -> None:
        accel = self.settings.get_string("paste-shortcut")
        self._paste_row.set_title(_("Shortcut sent when pasting: {shortcut}").format(
            shortcut=paste.describe_shortcut(accel)))

    def _change_paste_shortcut(self, *_a) -> None:
        def done(accel):
            self.settings.set_string("paste-shortcut", accel)
            self._refresh_paste_shortcut()
            self._toast(_("Pasting will use {shortcut}").format(shortcut=paste.describe_shortcut(accel)))

        self._record_accel(
            _("Press the paste shortcut"),
            _("Press the shortcut the target app uses to paste images.\n"
              "Ctrl+V is the usual one; Ctrl+Super+V for Claude's CLI.\nEsc cancels."),
            done,
        )

    def _test_paste_shortcut(self, *_a) -> None:
        accel = self.settings.get_string("paste-shortcut")
        self._toast(_("Sending {shortcut} in 3 s — focus the target window").format(
            shortcut=paste.describe_shortcut(accel)))

        def fire():
            try:
                paste.paste(accel)
            except Exception as exc:  # noqa: BLE001
                # A silent test button is worse than no test button: the user
                # cannot tell "it worked but the target ignored it" from
                # "it never ran".
                LOG.warning("test paste failed: %s", exc)
                self._toast(_("Could not send: {error}").format(error=exc))
            else:
                self._toast(_("{shortcut} sent").format(shortcut=paste.describe_shortcut(accel)))
            return False

        GLib.timeout_add_seconds(3, fire)

    def _refresh_hotkey(self) -> None:
        accel = self.settings.get_string("hotkey")
        label = Gtk.accelerator_get_label(*Gtk.accelerator_parse(accel)[1:]) or accel
        installed = hotkey.is_installed()
        conflicts = hotkey.find_conflicts(accel)
        parts = [label]
        if not installed:
            parts.append(_("not registered"))
        if conflicts:
            parts.append(_("conflicts with {keys}").format(
                keys=", ".join(k for _schema, k in conflicts)))
        self._hotkey_row.set_subtitle(" · ".join(parts))

    def _change_hotkey(self, *_a) -> None:
        def done(accel):
            self.settings.set_string("hotkey", accel)
            self._refresh_hotkey()
            self._toast(_("Capture shortcut changed to {shortcut}").format(
                shortcut=paste.describe_shortcut(accel)))

        self._record_accel(
            _("Press the shortcut"),
            _("Press the shortcut you want to use for capturing.\nEsc cancels."),
            done,
        )

    def _record_accel(self, heading: str, body: str, on_done) -> None:
        """Shared key recorder. Requires a modifier: a bare key is unusable as
        a global shortcut and would be a trap to record."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancelar")
        dialog.set_close_response("cancel")

        controller = Gtk.EventControllerKey()

        def on_key(_ctrl, keyval, _code, state):
            from gi.repository import Gdk

            mods = state & Gtk.accelerator_get_default_mod_mask()
            if keyval == Gdk.KEY_Escape:
                dialog.close()
                return True
            if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Shift_L,
                          Gdk.KEY_Shift_R, Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
                          Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                return True
            if not mods:
                return True
            dialog.close()
            on_done(Gtk.accelerator_name(keyval, mods))
            return True

        controller.connect("key-pressed", on_key)
        dialog.add_controller(controller)
        dialog.choose(self, None, lambda *_a: None)
