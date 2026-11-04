from __future__ import annotations

import ctypes.util
import logging
import wave
from pathlib import Path

import discord
import numpy as np


logger = logging.getLogger(__name__)


def ensure_opus_loaded() -> None:
    """Load libopus explicitly before sending PCM audio through discord.py."""
    if discord.opus.is_loaded():
        logger.debug("discord.py reports Opus already loaded")
        return

    candidates = []
    discovered = ctypes.util.find_library("opus")
    if discovered:
        candidates.append(discovered)
    # Common Linux SONAMEs; keep these as fallbacks for minimal environments.
    candidates.extend(("libopus.so.0", "libopus.so"))

    errors = []
    for library in dict.fromkeys(candidates):
        try:
            logger.debug("Trying to load Opus library: %s", library)
            discord.opus.load_opus(library)
            logger.info("Loaded native Opus library: %s", library)
            return
        except Exception as exc:
            logger.warning("Could not load Opus library %s: %s", library, exc)
            errors.append(f"{library}: {exc}")

    details = "; ".join(errors) if errors else "ctypes.util.find_library('opus') không tìm thấy thư viện."
    raise RuntimeError(
        "Không tìm thấy hoặc không tải được libopus nên Discord không thể phát PCM audio. "
        "Trên CachyOS/Arch, chạy: sudo pacman -S --needed opus. "
        f"Chi tiết: {details}"
    )


class WavPCMAudio(discord.AudioSource):
    """Read Piper WAV audio and convert it to Discord's 48 kHz stereo PCM format.

    Discord's voice player expects 20 ms of 48 kHz, signed 16-bit, stereo PCM
    per read (3,840 bytes). Piper commonly produces 22.05 kHz mono WAV files.
    Linear interpolation is sufficient for short TTS clips and avoids spawning
    an FFmpeg subprocess for every playback.
    """

    OUTPUT_RATE = 48_000
    OUTPUT_FRAMES = 960

    def __init__(self, path: Path):
        self._path = str(path)
        self._wav = wave.open(self._path, "rb")
        self._channels = self._wav.getnchannels()
        self._sample_width = self._wav.getsampwidth()
        self._sample_rate = self._wav.getframerate()
        self.channels = self._channels
        self.sample_width = self._sample_width
        self.sample_rate = self._sample_rate
        logger.info(
            "Opening WAV for Discord playback: path=%s channels=%d sample_width=%d "
            "sample_rate=%d frames=%d duration=%.3fs",
            path, self._channels, self._sample_width, self._sample_rate,
            self._wav.getnframes(),
            self._wav.getnframes() / self._sample_rate if self._sample_rate else 0,
        )
        if self._sample_width not in (1, 2, 4):
            self._wav.close()
            raise ValueError(f"WAV sample width không được hỗ trợ: {self._sample_width}")
        if self._channels < 1 or self._sample_rate < 1:
            self._wav.close()
            raise ValueError("WAV có định dạng không hợp lệ.")
        self._input_frames = max(1, round(self._sample_rate / 50))

    def read(self) -> bytes:
        raw = self._wav.readframes(self._input_frames)
        if not raw:
            return b""

        if self._sample_width == 1:
            samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) << 8
        elif self._sample_width == 2:
            samples = np.frombuffer(raw, dtype="<i2")
        else:
            samples = (np.frombuffer(raw, dtype="<i4") >> 16).astype(np.int16)

        if self._channels > 1:
            samples = samples[: (len(samples) // self._channels) * self._channels]
            samples = samples.reshape(-1, self._channels).astype(np.float32).mean(axis=1)
        else:
            samples = samples.astype(np.float32)

        if samples.size == 0:
            return b""

        # Pad the final partial 20 ms packet; the next read will return EOF.
        if samples.size < self._input_frames:
            samples = np.pad(samples, (0, self._input_frames - samples.size))

        source_x = np.arange(samples.size, dtype=np.float64)
        target_x = np.linspace(0, samples.size - 1, self.OUTPUT_FRAMES)
        resampled = np.interp(target_x, source_x, samples)
        pcm = np.clip(resampled, -32768, 32767).astype("<i2")
        stereo = np.repeat(pcm[:, np.newaxis], 2, axis=1)
        packet = stereo.tobytes()
        return packet

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self._wav.close()
            logger.debug("Closed WAV source: %s", self._path)
        except Exception:
            logger.exception("Failed to close WAV source")
