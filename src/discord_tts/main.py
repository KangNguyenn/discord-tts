from __future__ import annotations

import logging
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk

from discord_tts.config import APP_ID, AppConfig, HISTORY_DB
from discord_tts.services.tray import StatusNotifierTray
from discord_tts.storage.database import Database
from discord_tts.tts.engine import PiperEngine
from discord_tts.ui.window import MainWindow


class DiscordTTSApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.window: MainWindow | None = None
        self.config = AppConfig.load()
        self.database = Database(HISTORY_DB)
        self.engine = PiperEngine()
        self.tray: StatusNotifierTray | None = None

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(
                self, self.config, self.database, self.engine
            )
            self.add_action(self._make_simple_action("quit", self._quit))
            self.add_action(self._make_simple_action("show", self._present_window))
            self.add_action(
                self._make_simple_action("settings", self._open_settings_from_tray)
            )
            self.tray = StatusNotifierTray(
                self._present_window,
                self._open_settings_from_tray,
                self._quit,
            )
            self.tray.start()
            token = self.config.discord_token.strip()
            if token:
                self.window._start_discord(token)
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

    def _open_settings_from_tray(self):
        if self.window is not None:
            self._present_window()
            self.window._open_settings()
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


def _configure_logging() -> None:
    level_name = os.environ.get("DISCORD_TTS_LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        force=True,
        format="%(asctime)s %(levelname)-8s [%(name)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.captureWarnings(True)

    # Discord's voice handshake and audio player are the main diagnostic targets.
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("discord.voice_client").setLevel(logging.DEBUG)
    logging.getLogger("discord.voice_state").setLevel(logging.DEBUG)
    logging.getLogger("discord.gateway").setLevel(logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

    def log_uncaught_exception(exc_type, exc_value, exc_traceback):
        logging.getLogger("discord_tts").critical(
            "Uncaught exception on the main thread",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def log_thread_exception(args):
        logging.getLogger("discord_tts").critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_uncaught_exception
    threading.excepthook = log_thread_exception


def main() -> int:
    _configure_logging()
    logger = logging.getLogger("discord_tts")
    logger.info("Starting Discord TTS")
    logger.info("Python: %s", sys.version.replace("\n", " "))
    logger.info("Console logging configured (set DISCORD_TTS_LOG_LEVEL to change verbosity)")
    app = DiscordTTSApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
