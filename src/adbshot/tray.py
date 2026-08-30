"""A StatusNotifierItem implemented directly on D-Bus.

libayatana-appindicator would be the obvious choice, except that its typelib
depends on GTK 3, and GTK 3 and GTK 4 cannot coexist in one Python process --
it fails in both import orders. Rather than split the app into two processes to
work around that, we speak the protocol ourselves. It is the same amount of
code, and the whole application stays in one GTK4 process.

Details that matter, all read out of the shell extension that consumes this:

  * `Activate` is deliberately NOT in the published XML. The extension checks
    `supportsActivation` and, when true, waits out a double-click timeout on
    every single left click before opening the menu. Omitting the method makes
    left-click open the menu instantly.
  * Middle click goes through `XAyatanaSecondaryActivate` when we advertise it,
    and only falls back to `SecondaryActivate(x, y)` when we do not.
  * `GetLayout` must recurse: the shell calls it with recursionDepth -1 and
    then asks `GetGroupProperties` for nested ids in one flat list.
  * The shell caches every property and only re-reads the family named by the
    signal it receives, so changing an icon means mutating the property AND
    emitting the matching New* signal.
  * Left click does nothing at all while the menu is empty, so the menu must be
    populated before Status goes Active.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from gi.repository import Gio, GLib

from . import const, log

LOG = log.get("tray")

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="XAyatanaSecondaryActivate">
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


@dataclass
class MenuItem:
    label: str = ""
    icon: str = ""
    action: object = None
    enabled: bool = True
    visible: bool = True
    separator: bool = False
    children: list = field(default_factory=list)
    is_submenu: bool = False
    key: str = ""
    id: int = 0

    def properties(self) -> dict:
        if self.separator:
            return {"type": GLib.Variant("s", "separator")}
        props = {
            "label": GLib.Variant("s", self.label),
            "enabled": GLib.Variant("b", self.enabled),
            "visible": GLib.Variant("b", self.visible),
        }
        if self.icon:
            props["icon-name"] = GLib.Variant("s", self.icon)
        if self.is_submenu or self.children:
            props["children-display"] = GLib.Variant("s", "submenu")
        return props


class StatusNotifierItem:
    """Owns the tray icon and its menu, and keeps them registered."""

    def __init__(self, *, on_about_to_show=None):
        self._conn: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._owner_id = 0
        self._watch_id = 0
        self._item_reg = 0
        self._menu_reg = 0
        self._registered = False
        self._on_about_to_show = on_about_to_show
        self._secondary_action = None

        self._icon_name = "smartphone-symbolic"
        self._attention_icon = "dialog-error-symbolic"
        self._status = "Active"
        self._tooltip = const.APP_TITLE

        self._root = MenuItem(id=0, children=[])
        self._menu_signature = None
        self._ids: dict[str, int] = {}
        self._next_id = 1
        self._by_id: dict[int, MenuItem] = {0: self._root}
        self._revision = 1

        self._sni_info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._menu_info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._item_reg = self._conn.register_object(
            ITEM_PATH, self._sni_info, self._sni_method, self._sni_get_property, None
        )
        self._menu_reg = self._conn.register_object(
            MENU_PATH, self._menu_info, self._menu_method, self._menu_get_property, None
        )
        self._owner_id = Gio.bus_own_name_on_connection(
            self._conn, self._bus_name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        # Registering from the watcher's "appeared" callback rather than once at
        # startup is what makes lock/unlock and extension toggles self-heal:
        # the watcher genuinely goes away while the screen is locked.
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared, self._on_watcher_vanished,
        )
        LOG.info("tray started, bus name %s", self._bus_name)

    def stop(self) -> None:
        for reg, unreg in ((self._item_reg, "object"), (self._menu_reg, "object")):
            if reg and self._conn:
                self._conn.unregister_object(reg)
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
        self._item_reg = self._menu_reg = self._watch_id = self._owner_id = 0

    def _on_watcher_appeared(self, conn, name, owner):
        LOG.info("watcher appeared (%s), registering", owner)
        conn.call(
            WATCHER_NAME, WATCHER_PATH, WATCHER_NAME, "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)), None,
            Gio.DBusCallFlags.NONE, 5000, None, self._on_registered,
        )

    def _on_registered(self, source, result):
        try:
            source.call_finish(result)
            self._registered = True
            LOG.info("registered with the StatusNotifierWatcher")
        except GLib.Error as exc:
            self._registered = False
            LOG.warning("watcher refused registration: %s", exc.message)

    def _on_watcher_vanished(self, conn, name):
        self._registered = False
        LOG.info("watcher vanished (screen locked, or the extension was disabled)")

    # -- public state -----------------------------------------------------

    def set_icon(self, name: str) -> None:
        if name == self._icon_name:
            return
        self._icon_name = name
        self._emit_sni("NewIcon")

    def set_attention(self, on: bool, icon: str = "dialog-error-symbolic") -> None:
        """The shell renders AttentionIconName only while Status is NeedsAttention."""
        self._attention_icon = icon
        new_status = "NeedsAttention" if on else "Active"
        self._emit_sni("NewAttentionIcon")
        if new_status != self._status:
            self._status = new_status
            self._emit_sni("NewStatus", GLib.Variant("(s)", (new_status,)))

    def set_tooltip(self, text: str) -> None:
        if text == self._tooltip:
            return
        self._tooltip = text
        self._emit_sni("NewToolTip")

    def _emit_sni(self, signal: str, params=None) -> None:
        if not self._conn:
            return
        try:
            self._conn.emit_signal(
                None, ITEM_PATH, "org.kde.StatusNotifierItem", signal, params
            )
        except GLib.Error as exc:
            LOG.debug("could not emit %s: %s", signal, exc.message)

    # -- menu -------------------------------------------------------------

    @staticmethod
    def _signature(items: list[MenuItem]) -> tuple:
        return tuple(
            (i.key, i.label, i.icon, i.enabled, i.visible, i.separator, i.is_submenu,
             StatusNotifierItem._signature(i.children))
            for i in items
        )

    def _id_for(self, item: MenuItem, path: str) -> int:
        """A stable id per identity.

        Numbering items with a running counter looks fine until a second phone
        appears: the submenu's children are numbered in between the top-level
        entries, so every id below the device submenu shifts, and an id the
        shell cached a moment earlier now points at a different action --
        'Abrir carpeta' could fire 'Salir'. Keying on identity removes the
        whole class of bug.
        """
        key = item.key or path
        existing = self._ids.get(key)
        if existing is not None:
            return existing
        self._ids[key] = self._next_id
        self._next_id += 1
        return self._ids[key]

    def set_menu(self, items: list[MenuItem], notify: bool = True) -> bool:
        """Install a menu. Returns True only if the content actually changed.

        Bumping the revision and emitting LayoutUpdated unconditionally is what
        made a submenu flash open and shut: the shell calls AboutToShow as the
        popup opens, and a LayoutUpdated arriving at that moment makes it tear
        the menu down and rebuild it. So an unchanged menu is a no-op here, and
        `notify=False` lets AboutToShow refresh the model silently and report
        the change through its own needUpdate return value instead.
        """
        signature = self._signature(items)
        if signature == self._menu_signature:
            return False

        self._by_id = {0: self._root}

        def assign(item_list, prefix):
            for index, item in enumerate(item_list):
                path = f"{prefix}/{index}"
                item.id = self._id_for(item, path)
                self._by_id[item.id] = item
                if item.children:
                    assign(item.children, path)

        assign(items, "")
        self._root.children = items
        self._menu_signature = signature
        self._revision += 1
        if notify and self._conn:
            self._conn.emit_signal(
                None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)),
            )
        return True

    def _layout_tuple(self, item: MenuItem, depth: int) -> tuple:
        """(id, props, children) -- children are boxed, the struct itself is not."""
        if depth == 0:
            children = []
        else:
            child_depth = -1 if depth < 0 else depth - 1
            children = [
                GLib.Variant("(ia{sv}av)", self._layout_tuple(child, child_depth))
                for child in item.children if child.visible
            ]
        return (item.id, item.properties(), children)

    # -- D-Bus plumbing ---------------------------------------------------

    def _sni_get_property(self, conn, sender, path, iface, name):
        return {
            "Category": lambda: GLib.Variant("s", "ApplicationStatus"),
            "Id": lambda: GLib.Variant("s", const.APP_NAME),
            "Title": lambda: GLib.Variant("s", const.APP_TITLE),
            "Status": lambda: GLib.Variant("s", self._status),
            "WindowId": lambda: GLib.Variant("i", 0),
            "IconThemePath": lambda: GLib.Variant("s", ""),
            "Menu": lambda: GLib.Variant("o", MENU_PATH),
            "ItemIsMenu": lambda: GLib.Variant("b", True),
            "IconName": lambda: GLib.Variant("s", self._icon_name),
            "IconPixmap": lambda: GLib.Variant("a(iiay)", []),
            "OverlayIconName": lambda: GLib.Variant("s", ""),
            "OverlayIconPixmap": lambda: GLib.Variant("a(iiay)", []),
            "AttentionIconName": lambda: GLib.Variant("s", self._attention_icon),
            "AttentionIconPixmap": lambda: GLib.Variant("a(iiay)", []),
            "AttentionMovieName": lambda: GLib.Variant("s", ""),
            "ToolTip": lambda: GLib.Variant(
                "(sa(iiay)ss)", ("", [], const.APP_TITLE, self._tooltip)
            ),
        }.get(name, lambda: None)()

    def _sni_method(self, conn, sender, path, iface, method, params, invocation):
        if method in ("XAyatanaSecondaryActivate", "SecondaryActivate"):
            self._fire(self._secondary_action)
            invocation.return_value(None)
        elif method in ("ContextMenu", "Scroll"):
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method
            )

    def set_secondary_action(self, action) -> None:
        self._secondary_action = action

    @staticmethod
    def _fire(action) -> None:
        if action is None:
            return
        # Never run a callback inside the D-Bus dispatch; a slow one would stall
        # the shell waiting on our reply.
        GLib.idle_add(lambda: (action(), False)[1])

    def _menu_get_property(self, conn, sender, path, iface, name):
        return {
            "Version": lambda: GLib.Variant("u", 3),
            "TextDirection": lambda: GLib.Variant("s", "ltr"),
            "Status": lambda: GLib.Variant("s", "normal"),
            "IconThemePath": lambda: GLib.Variant("as", []),
        }.get(name, lambda: None)()

    def _menu_method(self, conn, sender, path, iface, method, params, invocation):
        if method == "GetLayout":
            parent_id, depth, _names = params.unpack()
            item = self._by_id.get(parent_id, self._root)
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout_tuple(item, depth)))
            )
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            out = [
                (i, self._by_id[i].properties())
                for i in ids if i in self._by_id
            ]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            props = self._by_id.get(item_id, self._root).properties()
            value = props.get(name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked":
                item = self._by_id.get(item_id)
                if item is not None and item.enabled:
                    self._fire(item.action)
            invocation.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    item = self._by_id.get(item_id)
                    if item is not None and item.enabled:
                        self._fire(item.action)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            (item_id,) = params.unpack()
            changed = bool(self._on_about_to_show and self._on_about_to_show(item_id))
            invocation.return_value(GLib.Variant("(b)", (changed,)))
        elif method == "AboutToShowGroup":
            (ids,) = params.unpack()
            needs_update = []
            for item_id in ids:
                if self._on_about_to_show and self._on_about_to_show(item_id):
                    needs_update.append(item_id)
            invocation.return_value(GLib.Variant("(aiai)", (needs_update, [])))
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD, method
            )
