from __future__ import annotations

from gi.repository import Gio, GLib


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


class StatusNotifierTray:
    """Small StatusNotifierItem: left-click reopens the GTK window.

    GNOME requires a StatusNotifier/AppIndicator extension; KDE panels and many
    Wayland panels support this protocol natively. The app remains launcher-activatable
    when no tray watcher is present.
    """

    def __init__(self, activate_callback):
        self.activate_callback = activate_callback
        self.connection = None
        self.registration_id = 0
        self._name_id = 0

    def start(self) -> bool:
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
            interface = node.interfaces[0]
            self.registration_id = self.connection.register_object(
                "/StatusNotifierItem",
                interface,
                self._method_call,
                self._get_property,
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
            self.stop()
            return False

    def _method_call(self, _conn, _sender, _path, _interface, method, _params, invocation):
        if method in {"Activate", "SecondaryActivate", "ContextMenu"}:
            GLib.idle_add(self.activate_callback)
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
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("audio-headset", [], "Discord TTS", "Bot running in background")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/NO_DBUSMENU"),
        }
        return values[name]

    def stop(self) -> None:
        if self.connection and self.registration_id:
            try:
                self.connection.unregister_object(self.registration_id)
            except Exception:
                pass
        if self._name_id:
            try:
                Gio.bus_unown_name(self._name_id)
            except Exception:
                pass
        self.registration_id = 0
        self._name_id = 0
        self.connection = None
