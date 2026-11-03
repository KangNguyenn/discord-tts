from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from discord_tts.config import VOICE_DIR

VOICE_IDS = {
    "vi": "vi_VN-vais1000-medium",
    "en": "en_US-lessac-medium",
}


class TTSUnavailable(RuntimeError):
    pass


class PiperEngine:
    """Piper wrapper. Model objects are cached so each sentence does not reload ONNX."""

    def __init__(self, voice_dir: Path = VOICE_DIR):
        self.voice_dir = voice_dir
        self._voices = {}
        self._lock = threading.RLock()

    def model_id(self, language: str) -> str:
        return VOICE_IDS.get(language, VOICE_IDS["vi"])

    def model_path(self, language: str) -> Path:
        return self.voice_dir / f"{self.model_id(language)}.onnx"

    def is_installed(self, language: str) -> bool:
        model = self.model_path(language)
        return model.is_file() and model.with_suffix(model.suffix + ".json").is_file()

    def download_voice(self, language: str) -> None:
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        voice = self.model_id(language)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "piper.download_voices",
                voice,
                "--data-dir",
                str(self.voice_dir),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not self.is_installed(language):
            details = result.stderr.strip() or result.stdout.strip()
            raise TTSUnavailable(
                f"Không tải được voice {voice}. Kiểm tra Internet và thử lại. {details}"
            )

    def ensure_voice(self, language: str) -> None:
        if not self.is_installed(language):
            raise TTSUnavailable(
                f"Chưa cài voice {self.model_id(language)}. Mở Settings → Voice models để tải."
            )

    def synthesize(
        self, text: str, language: str, speed: float = 1.0, volume: float = 1.0
    ) -> Path:
        text = text.strip()
        if not text:
            raise ValueError("Không có nội dung để đọc.")
        self.ensure_voice(language)

        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:
            raise TTSUnavailable(
                "Chưa cài Piper. Chạy: pip install 'piper-tts>=1.3'"
            ) from exc

        model = self.model_path(language)
        with self._lock:
            voice = self._voices.get(str(model))
            if voice is None:
                voice = PiperVoice.load(str(model))
                self._voices[str(model)] = voice

            output = tempfile.NamedTemporaryFile(prefix="discord-tts-", suffix=".wav", delete=False)
            output_path = Path(output.name)
            output.close()
            config = SynthesisConfig(
                volume=max(0.1, min(2.0, float(volume))),
                length_scale=1.0 / max(0.5, min(2.0, float(speed))),
            )
            try:
                with wave.open(str(output_path), "wb") as wav_file:
                    voice.synthesize_wav(text, wav_file, syn_config=config)
            except Exception:
                output_path.unlink(missing_ok=True)
                raise
            return output_path
