from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


DATABASE_SCHEMA_VERSION = 2
DEFAULT_MODEL = "vi_VN-vais1000-medium"


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._prepare_fresh_database()
        self._initialize()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _prepare_fresh_database(self) -> None:
        if not self.path.exists():
            return
        try:
            with sqlite3.connect(self.path) as db:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error:
            version = -1
        if version == DATABASE_SCHEMA_VERSION:
            return

        # Do not migrate old history or Quick Chat settings. Preserve the old
        # database as a local backup and create an empty database for this schema.
        backup = self.path.with_name(f"{self.path.stem}.legacy{self.path.suffix}")
        if backup.exists():
            backup = self.path.with_name(
                f"{self.path.stem}.legacy.{time.time_ns()}{self.path.suffix}"
            )
        os.replace(self.path, backup)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    source TEXT NOT NULL,
                    language TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'sent'
                );

                CREATE TABLE IF NOT EXISTS quick_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    speaker_id INTEGER NOT NULL DEFAULT 0,
                    speed REAL NOT NULL DEFAULT 1.0,
                    volume REAL NOT NULL DEFAULT 1.0,
                    position INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            db.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            count = db.execute("SELECT COUNT(*) FROM quick_messages").fetchone()[0]
            if count == 0:
                defaults = [
                    ("Xin chào", "Xin chào mọi người!", DEFAULT_MODEL, 0, 1.0, 1.0, 0),
                    ("Cảm ơn", "Cảm ơn bạn nhé!", DEFAULT_MODEL, 0, 1.0, 1.0, 1),
                    ("Hello", "Xin chào mọi người!", DEFAULT_MODEL, 0, 1.0, 1.0, 2),
                    ("BRB", "Mình quay lại ngay nhé!", DEFAULT_MODEL, 0, 1.0, 1.0, 3),
                ]
                db.executemany(
                    "INSERT INTO quick_messages"
                    "(title, text, model_id, speaker_id, speed, volume, position) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    defaults,
                )

    def add_history(
        self, direction: str, source: str, language: str, text: str, status: str = "sent"
    ) -> int:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO history(created_at, direction, source, language, text, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (created_at, direction, source, language, text, status),
            )
            return int(cursor.lastrowid)

    def recent_history(self, limit: int = 80) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(
                db.execute(
                    "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()[::-1]
            )

    def quick_messages(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(
                db.execute(
                    "SELECT * FROM quick_messages ORDER BY position, id"
                ).fetchall()
            )

    def add_quick_message(
        self, title: str, text: str, model_id: str, speaker_id: int,
        speed: float, volume: float,
    ) -> int:
        with self._connect() as db:
            position = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM quick_messages"
            ).fetchone()[0]
            cursor = db.execute(
                "INSERT INTO quick_messages"
                "(title, text, model_id, speaker_id, speed, volume, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, text, model_id, speaker_id, speed, volume, position),
            )
            return int(cursor.lastrowid)

    def update_quick_message(
        self, quick_id: int, title: str, text: str, model_id: str,
        speaker_id: int, speed: float, volume: float,
    ) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE quick_messages SET title = ?, text = ?, model_id = ?, "
                "speaker_id = ?, speed = ?, volume = ? WHERE id = ?",
                (title, text, model_id, speaker_id, speed, volume, quick_id),
            )

    def delete_quick_message(self, quick_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM quick_messages WHERE id = ?", (quick_id,))
