from __future__ import annotations

import gc
import json
import logging
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from discord_tts.config import VOICE_DIR


logger = logging.getLogger(__name__)

class TTSUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceModel:
    """A locally installed Piper model and the metadata read from its sidecar."""

    model_id: str
    display_name: str
    path: Path
    language_code: str
    language_name: str
    quality: str
    sample_rate: int
    num_speakers: int
    speakers: tuple[tuple[str, int], ...]
    default_speaker_id: int = 0

    def speaker_name(self, speaker_id: int) -> str:
        for name, candidate_id in self.speakers:
            if candidate_id == speaker_id:
                return name
        return f"Speaker {speaker_id}"


class PiperEngine:
    """Piper wrapper with one shared, idle-released ONNX voice cache."""

    _VOICE_IDLE_TIMEOUT_SECONDS = 20.0

    def __init__(self, voice_dir: Path = VOICE_DIR):
        self.voice_dir = voice_dir
        self._voice: Any | None = None
        self._voice_path: str | None = None
        self._lock = threading.RLock()
        self._speech_busy = threading.Event()
        self._release_timer: threading.Timer | None = None
        self._cache_generation = 0

    @staticmethod
    def _language_name(language: dict[str, Any], code: str) -> str:
        name = language.get("name_english") or language.get("name_native")
        if name:
            return str(name)
        family = code.replace("-", "_").split("_", 1)[0].lower()
        return {
            "vi": "Vietnamese",
            "en": "English",
            "de": "German",
            "fr": "French",
            "es": "Spanish",
            "it": "Italian",
            "ja": "Japanese",
            "zh": "Chinese",
            "ko": "Korean",
            "pt": "Portuguese",
            "ru": "Russian",
            "nl": "Dutch",
        }.get(family, code or "Unknown language")

    @staticmethod
    def _fallback_language_code(model_name: str, metadata: dict[str, Any]) -> str:
        language = metadata.get("language")
        if isinstance(language, dict) and language.get("code"):
            return str(language["code"])

        espeak = metadata.get("espeak")
        voice = espeak.get("voice") if isinstance(espeak, dict) else None
        if voice:
            return str(voice).replace("-", "_", 1)

        prefix = model_name.split("-", 1)[0]
        return prefix if prefix else "unknown"

    def _read_model(self, model_path: Path) -> VoiceModel | None:
        metadata_path = model_path.with_suffix(model_path.suffix + ".json")
        if not metadata_path.is_file():
            logger.warning("Skipping Piper model without metadata: %s", model_path)
            return None

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("metadata root must be a JSON object")

            audio = metadata.get("audio") or {}
            if not isinstance(audio, dict):
                audio = {}
            language = metadata.get("language") or {}
            if not isinstance(language, dict):
                language = {}
            language_code = self._fallback_language_code(model_path.stem, metadata)
            language_name = self._language_name(language, language_code)

            quality = audio.get("quality") or metadata.get("quality")
            if not quality:
                suffix = model_path.stem.rsplit("-", 1)
                quality = suffix[-1] if len(suffix) == 2 else "Unknown"

            speaker_map = metadata.get("speaker_id_map") or {}
            if not isinstance(speaker_map, dict):
                speaker_map = {}
            names_by_id: dict[int, str] = {}
            for speaker_name, raw_id in speaker_map.items():
                try:
                    names_by_id[int(raw_id)] = str(speaker_name)
                except (TypeError, ValueError):
                    continue

            try:
                num_speakers = max(1, int(metadata.get("num_speakers", 1)))
            except (TypeError, ValueError):
                num_speakers = max(1, len(names_by_id))
            num_speakers = max(num_speakers, max(names_by_id, default=-1) + 1)
            if num_speakers > 10000:
                raise ValueError(f"unreasonable speaker count: {num_speakers}")

            speakers = tuple(
                (names_by_id.get(speaker_id, f"Speaker {speaker_id}"), speaker_id)
                for speaker_id in range(num_speakers)
            )
            try:
                sample_rate = int(audio.get("sample_rate", 0))
            except (TypeError, ValueError):
                sample_rate = 0

            default_speaker_id = int(metadata.get("default_speaker_id", 0) or 0)
            if not 0 <= default_speaker_id < num_speakers:
                default_speaker_id = 0

            relative_id = model_path.relative_to(self.voice_dir).with_suffix("").as_posix()
            return VoiceModel(
                model_id=relative_id,
                display_name=model_path.stem,
                path=model_path,
                language_code=language_code,
                language_name=language_name,
                quality=str(quality),
                sample_rate=sample_rate,
                num_speakers=num_speakers,
                speakers=speakers,
                default_speaker_id=default_speaker_id,
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Skipping invalid Piper model metadata %s: %s", metadata_path, exc)
            return None

    def list_models(self) -> list[VoiceModel]:
        """Find valid .onnx/.onnx.json pairs, including models in subdirectories."""
        if not self.voice_dir.is_dir():
            return []

        models: list[VoiceModel] = []
        for path in self.voice_dir.rglob("*.onnx"):
            # Split streaming encoder/decoder files are not standalone voices.
            if path.stem.endswith((".encoder", ".decoder")):
                continue
            model = self._read_model(path)
            if model is not None:
                models.append(model)

        return sorted(
            models,
            key=lambda item: (
                item.language_name.casefold(),
                item.display_name.casefold(),
                item.model_id.casefold(),
            ),
        )

    def get_model(self, model_id: str) -> VoiceModel | None:
        models = self.list_models()
        exact = next((item for item in models if item.model_id == model_id), None)
        if exact:
            return exact
        # Existing configs store the bare model filename. Also accept that form
        # when a user places a model inside a subdirectory.
        return next((item for item in models if item.display_name == Path(model_id).name), None)

    def model_id(self, selection: str) -> str:
        model = self.get_model(selection)
        if model:
            return model.model_id
        return selection

    def model_path(self, selection: str) -> Path:
        model = self.get_model(selection)
        if model:
            return model.path
        return self.voice_dir / f"{Path(selection).name}.onnx"

    def language_code(self, selection: str) -> str:
        model = self.get_model(selection)
        if model:
            return model.language_code
        return Path(selection).name.split("-", 1)[0]

    def is_installed(self, selection: str) -> bool:
        return self.get_model(selection) is not None

    def ensure_voice(self, selection: str) -> VoiceModel:
        model = self.get_model(selection)
        if model is None:
            raise TTSUnavailable(
                f"Không tìm thấy model '{selection}'. Hãy chép cặp .onnx và .onnx.json "
                f"vào thư mục: {self.voice_dir}"
            )
        return model

    def set_speech_busy(self, busy: bool) -> None:
        """Prevent new preview synthesis while real Discord TTS is active."""
        if busy:
            self._speech_busy.set()
        else:
            self._speech_busy.clear()

    def _cancel_release_timer_locked(self) -> int:
        self._cache_generation += 1
        timer, self._release_timer = self._release_timer, None
        if timer is not None:
            timer.cancel()
        return self._cache_generation

    def _schedule_voice_release_locked(self) -> None:
        generation = self._cancel_release_timer_locked()
        if self._voice is None:
            return
        timer = threading.Timer(
            self._VOICE_IDLE_TIMEOUT_SECONDS,
            self._release_voice_if_idle,
            args=(generation,),
        )
        timer.daemon = True
        self._release_timer = timer
        timer.start()
        logger.debug(
            "Scheduled Piper model release in %.0f seconds",
            self._VOICE_IDLE_TIMEOUT_SECONDS,
        )

    def _release_voice_if_idle(self, generation: int) -> None:
        with self._lock:
            if generation != self._cache_generation:
                return
            self._release_timer = None
            previous_model = self._voice_path
            self._voice = None
            self._voice_path = None
            if previous_model is not None:
                gc.collect()
                logger.info(
                    "Released idle Piper model after %.0f seconds: %s",
                    self._VOICE_IDLE_TIMEOUT_SECONDS, previous_model,
                )

    def release_voice(self) -> None:
        """Immediately release the cached ONNX voice and its idle timer."""
        with self._lock:
            self._cancel_release_timer_locked()
            previous_model = self._voice_path
            self._voice = None
            self._voice_path = None
            if previous_model is not None:
                gc.collect()
                logger.info("Released cached Piper model: %s", previous_model)

    def synthesize(
        self,
        text: str,
        model_id: str,
        speed: float = 1.0,
        volume: float = 1.0,
        speaker_id: int = 0,
        preview: bool = False,
    ) -> Path:
        text = text.strip()
        if not text:
            raise ValueError("Không có nội dung để đọc.")
        model_info = self.ensure_voice(model_id)

        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:
            raise TTSUnavailable(
                "Chưa cài Piper. Chạy: pip install 'piper-tts>=1.3'"
            ) from exc

        model_path = model_info.path
        model_key = str(model_path)
        with self._lock:
            # A preview that was waiting behind real TTS must not start after
            # the bot has claimed the shared synthesis engine.
            if preview and self._speech_busy.is_set():
                raise TTSUnavailable(
                    "Không thể preview khi bot đang xử lý hoặc phát TTS."
                )

            self._cancel_release_timer_locked()
            try:
                if self._voice is None or self._voice_path != model_key:
                    previous_model = self._voice_path
                    # Drop the previous ONNX session before loading another model.
                    # PiperVoice owns the runtime session and its model allocations.
                    self._voice = None
                    self._voice_path = None
                    if previous_model is not None:
                        gc.collect()
                        logger.info(
                            "Released cached Piper model before switching: %s",
                            previous_model,
                        )

                    voice = PiperVoice.load(model_key)
                    self._voice = voice
                    self._voice_path = model_key
                    logger.info(
                        "Loaded Piper model into the single-model cache: %s", model_key
                    )
                else:
                    voice = self._voice

                output = tempfile.NamedTemporaryFile(
                    prefix="discord-tts-", suffix=".wav", delete=False
                )
                output_path = Path(output.name)
                output.close()
                selected_speaker = None
                if model_info.num_speakers > 1:
                    if not 0 <= int(speaker_id) < model_info.num_speakers:
                        speaker_id = model_info.default_speaker_id
                    selected_speaker = int(speaker_id)

                config = SynthesisConfig(
                    volume=max(0.1, min(2.0, float(volume))),
                    length_scale=1.0 / max(0.5, min(2.0, float(speed))),
                    speaker_id=selected_speaker,
                )
                try:
                    with wave.open(str(output_path), "wb") as wav_file:
                        voice.synthesize_wav(text, wav_file, syn_config=config)
                except Exception:
                    output_path.unlink(missing_ok=True)
                    raise
                return output_path
            finally:
                # Preview and real TTS share the same cache lifetime. The
                # model stays warm briefly, then is evicted after inactivity.
                self._schedule_voice_release_locked()
