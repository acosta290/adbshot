"""Device chooser, shown only when the choice is genuinely ambiguous.

Never opened from the hotkey fast path: presenting it takes keyboard focus,
which would move the window an auto-paste lands in.
"""

from __future__ import annotations

from gi.repository import Adw, Gtk

from . import log
from .i18n import _

LOG = log.get("chooser")


class DeviceChooser(Adw.Dialog):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.set_title(_("Choose a device"))
        self.set_content_width(420)

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_top(12)
        self._list.set_margin_bottom(12)
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)

        self._remember = Adw.SwitchRow(
            title=_("Remember this choice"),
            subtitle=_("Later captures will go to this phone without asking"),
            active=True,
        )
        remember_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        remember_box.add_css_class("boxed-list")
        remember_box.append(self._remember)
        remember_box.set_margin_start(12)
        remember_box.set_margin_end(12)
        remember_box.set_margin_bottom(12)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        content.append(header)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=380)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.append(self._list)
        inner.append(remember_box)
        scroller.set_child(inner)
        content.append(scroller)
        self.set_child(content)

        self.refresh(app.tracker.devices if app.tracker else [])

    def refresh(self, devices) -> None:
        child = self._list.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt

        if not devices:
            row = Adw.ActionRow(
                title=_("No devices"),
                subtitle=_("Plug the phone in over USB and accept the debugging prompt"),
            )
            row.set_activatable(False)
            self._list.append(row)
            return

        for dev in devices:
            row = Adw.ActionRow(title=dev.label, subtitle=f"{dev.serial} · {dev.status_text()}")
            row.set_activatable(dev.ready)
            icon = Gtk.Image.new_from_icon_name(
                "smartphone-symbolic" if dev.ready else "dialog-warning-symbolic"
            )
            row.add_prefix(icon)
            if dev.ready:
                button = Gtk.Button(label=_("Capture"), valign=Gtk.Align.CENTER)
                button.add_css_class("suggested-action")
                button.connect("clicked", lambda _btn, d=dev: self._pick(d))
                row.add_suffix(button)
            self._list.append(row)

    def _pick(self, device) -> None:
        if self._remember.get_active():
            self.app.settings.set_string("preferred-device", device.serial)
        self.close()
        self.app.capture_device(device)
