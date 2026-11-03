from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

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
                    language TEXT NOT NULL DEFAULT 'vi',
                    position INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            count = db.execute("SELECT COUNT(*) FROM quick_messages").fetchone()[0]
            if count == 0:
                db.executemany(
                    "INSERT INTO quick_messages(title, text, language, position) VALUES (?, ?, ?, ?)",
                    [
                        ("Xin chào", "Xin chào mọi người!", "vi", 0),
                        ("Cảm ơn", "Cảm ơn bạn nhé!", "vi", 1),
                        ("Hello", "Hello everyone!", "en", 2),
                        ("BRB", "Be right back!", "en", 3),
                    ],
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

    def add_quick_message(self, title: str, text: str, language: str) -> None:
        with self._connect() as db:
            position = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM quick_messages"
            ).fetchone()[0]
            db.execute(
                "INSERT INTO quick_messages(title, text, language, position) VALUES (?, ?, ?, ?)",
                (title, text, language, position),
            )

    def find_quick_message(self, title: str):
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM quick_messages WHERE lower(title) = lower(?) LIMIT 1",
                (title,),
            ).fetchone()
