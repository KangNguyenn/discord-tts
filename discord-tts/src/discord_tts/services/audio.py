from __future__ import annotations

import wave
from pathlib import Path

import discord
import numpy as np


class WavPCMAudio(discord.AudioSource):
    """Read Piper WAV audio and convert it to Discord's 48 kHz stereo PCM format.

    Discord's voice player expects 20 ms of 48 kHz, signed 16-bit, stereo PCM
    per read (3,840 bytes). Piper commonly produces 22.05 kHz mono WAV files.
    Linear interpolation is sufficient for short TTS clips and avoids an FFmpeg
    subprocess in the Flatpak sandbox.
    """

    OUTPUT_RATE = 48_000
    OUTPUT_FRAMES = 960

    def __init__(self, path: Path):
        self._wav = wave.open(str(path), "rb")
        self._channels = self._wav.getnchannels()
        self._sample_width = self._wav.getsampwidth()
        self._sample_rate = self._wav.getframerate()
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
        return stereo.tobytes()

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self._wav.close()
        except Exception:
            pass
