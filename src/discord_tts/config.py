from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_ID = "io.github.kang.discordtts"
APP_NAME = "Discord TTS"
SCHEMA_VERSION = 2
DEFAULT_VOICE_MODEL = "vi_VN-vais1000-medium"


def _xdg_dir(variable: str, fallback: str) -> Path:
    return Path(os.environ.get(variable, Path.home() / fallback)) / "discord-tts"


CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", ".config")
DATA_DIR = _xdg_dir("XDG_DATA_HOME", ".local/share")
CACHE_DIR = _xdg_dir("XDG_CACHE_HOME", ".cache")
VOICE_DIR = DATA_DIR / "voices"
HISTORY_DB = DATA_DIR / "history.sqlite3"


@dataclass
class VoiceProfile:
    model_id: str = DEFAULT_VOICE_MODEL
    speaker_id: int = 0
    speed: float = 1.0
    volume: float = 1.0

    @classmethod
    def from_dict(cls, values: object) -> "VoiceProfile":
        if not isinstance(values, dict):
            return cls()
        try:
            return cls(
                model_id=str(values.get("model_id", DEFAULT_VOICE_MODEL)),
                speaker_id=max(0, int(values.get("speaker_id", 0))),
                speed=max(0.5, min(2.0, float(values.get("speed", 1.0)))),
                volume=max(0.1, min(2.0, float(values.get("volume", 1.0)))),
            )
        except (TypeError, ValueError):
            return cls()

    def copy(self) -> "VoiceProfile":
        return VoiceProfile(
            model_id=self.model_id,
            speaker_id=self.speaker_id,
            speed=self.speed,
            volume=self.volume,
        )


@dataclass
class AppConfig:
    guild_id: int | None = None
    voice_channel_id: int | None = None
    read_text_channel_ids: list[int] = field(default_factory=list)
    discord_token: str = ""
    direct_chat: VoiceProfile = field(default_factory=VoiceProfile)
    discord_reader: VoiceProfile = field(default_factory=VoiceProfile)
    discord_status: str = "online"
    discord_activity_type: str = "listening"
    discord_activity_name: str = "Discord TTS"
    discord_streaming_url: str = ""
    auto_join_user_id: int | None = None
    hide_on_close: bool = True

    @classmethod
    def load(cls) -> "AppConfig":
        path = CONFIG_DIR / "config.json"
        if not path.exists():
            return cls()

        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            values = None

        # This release intentionally starts with a fresh schema. Keep the old
        # file as a backup, but never import or migrate any of its values.
        if not isinstance(values, dict) or values.get("schema_version") != SCHEMA_VERSION:
            backup = path.with_name("config.legacy.json")
            if backup.exists():
                backup = path.with_name(f"config.legacy.{os.getpid()}.json")
            try:
                os.replace(path, backup)
            except OSError:
                pass
            return cls()

        try:
            return cls(
                guild_id=int(values["guild_id"]) if values.get("guild_id") is not None else None,
                voice_channel_id=(
                    int(values["voice_channel_id"])
                    if values.get("voice_channel_id") is not None else None
                ),
                read_text_channel_ids=[
                    int(item) for item in values.get("read_text_channel_ids", [])
                ],
                discord_token=str(values.get("discord_token", "")),
                direct_chat=VoiceProfile.from_dict(values.get("direct_chat")),
                discord_reader=VoiceProfile.from_dict(values.get("discord_reader")),
                discord_status=str(values.get("discord_status", "online")),
                discord_activity_type=str(values.get("discord_activity_type", "listening")),
                discord_activity_name=str(values.get("discord_activity_name", "Discord TTS")),
                discord_streaming_url=str(values.get("discord_streaming_url", "")),
                auto_join_user_id=(
                    int(values["auto_join_user_id"])
                    if values.get("auto_join_user_id") is not None else None
                ),
                hide_on_close=bool(values.get("hide_on_close", True)),
            )
        except (TypeError, ValueError):
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / "config.json"
        values = asdict(self)
        values["schema_version"] = SCHEMA_VERSION
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
