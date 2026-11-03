from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_ID = "io.github.kang.discordtts"
APP_NAME = "Discord TTS"


def _xdg_dir(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, Path.home() / fallback)) / "discord-tts"


CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", ".config")
DATA_DIR = _xdg_dir("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg_dir("XDG_CACHE_HOME", ".cache")
VOICE_DIR = DATA_DIR / "voices"
HISTORY_DB = DATA_DIR / "history.sqlite3"


@dataclass
class AppConfig:
    guild_id: int | None = None
    voice_channel_id: int | None = None
    read_text_channel_ids: list[int] = field(default_factory=list)
    discord_incoming_language: str = "vi"
    tts_speed: float = 1.0
    tts_volume: float = 1.0
    # Language-specific model IDs. Users can change these later in Settings.
    voice_vi: str = "vi_VN-vais1000-medium"
    voice_en: str = "en_US-lessac-medium"
    hide_on_close: bool = True

    @classmethod
    def load(cls) -> "AppConfig":
        path = CONFIG_DIR / "config.json"
        if not path.exists():
            return cls()
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            allowed = {item.name for item in cls.__dataclass_fields__.values()}
            return cls(**{key: value for key, value in values.items() if key in allowed})
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "config.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
