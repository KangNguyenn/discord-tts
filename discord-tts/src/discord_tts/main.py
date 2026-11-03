from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from discord_tts.config import APP_ID, APP_NAME, AppConfig, HISTORY_DB
from discord_tts.services.tray import StatusNotifierTray
from discord_tts.storage.database import Database
from discord_tts.storage.secrets import SecretStore, SecretStoreError
from discord_tts.tts.engine import PiperEngine
from discord_tts.ui.window import MainWindow


class DiscordTTSApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window: MainWindow | None = None
        self.config = AppConfig.load()
        self.database = Database(HISTORY_DB)
        self.engine = PiperEngine()
        self.secrets: SecretStore | None = None
        self.tray: StatusNotifierTray | None = None

    def do_activate(self):
        if self.window is None:
            try:
                self.secrets = SecretStore()
            except SecretStoreError:
                # Window still opens so the user can read setup guidance in the UI.
                self.secrets = None
            self.window = MainWindow(
                self, self.config, self.database, self.engine,
                self.secrets if self.secrets else _UnavailableSecretStore(),
            )
            self.tray = StatusNotifierTray(self._present_window)
            self.tray.start()
            self.add_action(self._make_simple_action("quit", self._quit))
            self.add_action(self._make_simple_action("show", self._present_window))
            self.add_action(self._make_simple_action("settings", self.window._open_settings))
            if self.secrets:
                try:
                    token = self.secrets.get_token()
                except SecretStoreError as exc:
                    self.window.handle_service_event("connection_error", {"message": str(exc)})
                    token = None
                if token:
                    self.window._start_discord(token)
                elif self.secrets:
                    self.window._show_token_dialog()
            else:
                self.window.handle_service_event(
                    "connection_error",
                    {"message": "Thiếu Secret Service. Cài GNOME Keyring/libsecret để lưu Bot Token an toàn."},
                )
        self._present_window()

    def _make_simple_action(self, name, callback):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda *_: callback())
        return action

    def _present_window(self):
        if self.window:
            self.window.set_visible(True)
            self.window.present()
        return False

    def _quit(self):
        if self.window and self.window.discord:
            self.window.discord.shutdown()
        if self.tray:
            self.tray.stop()
        self.quit()

    def do_shutdown(self):
        if self.window and self.window.discord:
            self.window.discord.shutdown()
        if self.tray:
            self.tray.stop()
        Gtk.Application.do_shutdown(self)


class _UnavailableSecretStore:
    def get_token(self):
        return None

    def set_token(self, token):
        raise SecretStoreError("Secret Service không khả dụng.")


def main() -> int:
    app = DiscordTTSApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
