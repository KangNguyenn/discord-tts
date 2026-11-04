from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib


logger = logging.getLogger(__name__)


_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
  </interface>
</node>
"""


_DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
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
      <arg name="errors" type="a(is)" direction="out"/>
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
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""


class StatusNotifierTray:
    """StatusNotifierItem with a standard DBusMenu context menu.

    GNOME requires a StatusNotifier/AppIndicator extension; KDE panels and many
    Wayland panels support this protocol natively. The app remains launcher-activatable
    when no tray watcher is present.
    """

    _MENU_CALLBACKS = {
        1: "open",
        2: "settings",
        4: "quit",
    }

    def __init__(
        self,
        activate_callback: Callable[[], object],
        settings_callback: Callable[[], object],
        quit_callback: Callable[[], object],
    ):
        self._callbacks = {
            "open": activate_callback,
            "settings": settings_callback,
            "quit": quit_callback,
        }
        self.connection = None
        self.registration_id = 0
        self.menu_registration_id = 0
        self._name_id = 0

    def start(self) -> bool:
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

            sni_node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
            self.registration_id = self.connection.register_object(
                "/StatusNotifierItem",
                sni_node.interfaces[0],
                self._method_call,
                self._get_property,
                None,
            )

            menu_node = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_XML)
            self.menu_registration_id = self.connection.register_object(
                "/Menu",
                menu_node.interfaces[0],
                self._menu_method_call,
                self._get_menu_property,
                None,
            )

            self.connection.call_sync(
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", ("/StatusNotifierItem",)),
                None,
                Gio.DBusCallFlags.NONE,
                1500,
                None,
            )
            return True
        except Exception:
            logger.debug("Could not register StatusNotifierItem", exc_info=True)
            self.stop()
            return False

    def _schedule_callback(self, callback: Callable[[], object]) -> None:
        GLib.idle_add(self._run_callback, callback)

    @staticmethod
    def _run_callback(callback: Callable[[], object]) -> bool:
        try:
            callback()
        except Exception:
            logger.exception("Tray menu action failed")
        return GLib.SOURCE_REMOVE

    def _method_call(
        self, _conn, _sender, _path, _interface, method, _params, invocation
    ):
        if method in {"Activate", "SecondaryActivate"}:
            self._schedule_callback(self._callbacks["open"])
        # ContextMenu is handled through the exported DBusMenu object.
        invocation.return_value(None)

    def _get_property(self, _conn, _sender, _path, _interface, name):
        values = {
            "Category": GLib.Variant("s", "Communications"),
            "Id": GLib.Variant("s", "discord-tts"),
            "Title": GLib.Variant("s", "Discord TTS"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "audio-headset"),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)",
                ("audio-headset", [], "Discord TTS", "Bot running in background"),
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/Menu"),
        }
        return values[name]

    @staticmethod
    def _filter_properties(properties: dict, requested: list[str]) -> dict:
        if not requested or "all" in requested:
            return properties
        return {
            name: value for name, value in properties.items()
            if name in requested
        }

    def _menu_item_properties(self, item_id: int, requested: list[str] | None = None):
        labels = {
            1: "Mở",
            2: "Cài đặt",
            4: "Tắt",
        }
        if item_id == 0:
            properties = {}
        elif item_id == 3:
            properties = {
                "type": GLib.Variant("s", "separator"),
                "visible": GLib.Variant("b", True),
            }
        elif item_id in labels:
            properties = {
                "label": GLib.Variant("s", labels[item_id]),
                "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True),
            }
        else:
            return None
        return self._filter_properties(properties, requested or [])

    def _layout_node(self, item_id: int, depth: int, requested: list[str]):
        properties = self._menu_item_properties(item_id, requested)
        if properties is None:
            properties = {}

        children = []
        if item_id == 0 and depth != 0:
            child_depth = depth - 1 if depth > 0 else -1
            for child_id in (1, 2, 3, 4):
                child = self._layout_node(child_id, child_depth, requested)
                children.append(GLib.Variant("(ia{sv}av)", child))
        return (item_id, properties, children)

    def _menu_method_call(
        self, _conn, _sender, _path, _interface, method, params, invocation
    ):
        args = params.unpack()

        if method == "GetLayout":
            parent_id, depth, requested = args
            if parent_id != 0 and self._menu_item_properties(parent_id) is None:
                invocation.return_dbus_error(
                    "com.canonical.dbusmenu.Error", "Unknown menu item."
                )
                return
            layout = self._layout_node(parent_id, depth, requested)
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (1, layout))
            )
            return

        if method == "GetGroupProperties":
            item_ids, requested = args
            rows = []
            for item_id in item_ids:
                properties = self._menu_item_properties(item_id, requested)
                if properties is not None:
                    rows.append((item_id, properties))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
            return

        if method == "GetProperty":
            item_id, name = args
            properties = self._menu_item_properties(item_id)
            value = properties.get(name) if properties else None
            if value is None:
                invocation.return_dbus_error(
                    "com.canonical.dbusmenu.Error", "Unknown menu property."
                )
                return
            invocation.return_value(GLib.Variant("(v)", (value,)))
            return

        if method == "Event":
            item_id, event_name, _data, _timestamp = args
            self._dispatch_menu_event(item_id, event_name)
            invocation.return_value(None)
            return

        if method == "EventGroup":
            errors = []
            for item_id, event_name, _data, _timestamp in args[0]:
                if not self._dispatch_menu_event(item_id, event_name):
                    errors.append((item_id, event_name))
            invocation.return_value(GLib.Variant("(a(is))", (errors,)))
            return

        if method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        if method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return

        invocation.return_dbus_error(
            "com.canonical.dbusmenu.Error", f"Unsupported menu method: {method}"
        )

    def _dispatch_menu_event(self, item_id: int, event_name: str) -> bool:
        callback_name = self._MENU_CALLBACKS.get(item_id)
        if callback_name is None or event_name != "clicked":
            return False
        self._schedule_callback(self._callbacks[callback_name])
        return True

    def _get_menu_property(self, _conn, _sender, _path, _interface, name):
        values = {
            "Version": GLib.Variant("u", 4),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values[name]

    def stop(self) -> None:
        if self.connection:
            for registration_id in (
                self.menu_registration_id,
                self.registration_id,
            ):
                if registration_id:
                    try:
                        self.connection.unregister_object(registration_id)
                    except Exception:
                        logger.debug("Could not unregister tray DBus object", exc_info=True)
        if self._name_id:
            try:
                Gio.bus_unown_name(self._name_id)
            except Exception:
                pass
        self.registration_id = 0
        self.menu_registration_id = 0
        self._name_id = 0
        self.connection = None
