from __future__ import annotations

import ctypes.util
import logging
import tempfile
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
    Linear interpolation is sufficient for short TTS clips and avoids an FFmpeg
    subprocess in the Flatpak sandbox.
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
        self._read_count = 0
        self._logged_eof = False
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
            if not self._logged_eof:
                logger.info(
                    "WAV PCM stream reached EOF after %d packets (path=%s)",
                    self._read_count, self._path,
                )
                self._logged_eof = True
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
        self._read_count += 1
        if self._read_count <= 2:
            pcm_float = pcm.astype(np.float32)
            rms = float(np.sqrt(np.mean(np.square(pcm_float))))
            logger.debug(
                "Generated PCM packet %d: %d bytes (expected 3840), "
                "sample_min=%d sample_max=%d rms=%.1f",
                self._read_count, len(packet),
                int(pcm.min()), int(pcm.max()), rms,
            )
        return packet

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        try:
            self._wav.close()
            logger.debug("Closed WAV source after %d PCM packets", self._read_count)
        except Exception:
            logger.exception("Failed to close WAV source")


def convert_wav_to_discord_pcm_wav(path: Path) -> Path:
    """Convert a Piper WAV into the exact PCM packets used by Discord playback.

    The returned WAV can be played locally to isolate resampling/formatting from
    Discord's voice transport and Opus encryption.
    """
    source = WavPCMAudio(path)
    output_file = tempfile.NamedTemporaryFile(
        prefix="discord-tts-pcm-preview-", suffix=".wav", delete=False
    )
    output_path = Path(output_file.name)
    output_file.close()

    packet_count = 0
    sample_count = 0
    sum_squares = 0.0
    peak = 0
    sample_min = 32767
    sample_max = -32768
    try:
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(2)
            wav_file.setsampwidth(2)
            wav_file.setframerate(WavPCMAudio.OUTPUT_RATE)
            while True:
                packet = source.read()
                if not packet:
                    break
                wav_file.writeframesraw(packet)
                samples = np.frombuffer(packet, dtype="<i2").astype(np.float64)
                if samples.size:
                    packet_count += 1
                    sample_count += int(samples.size)
                    sum_squares += float(np.dot(samples, samples))
                    peak = max(peak, int(np.max(np.abs(samples))))
                    sample_min = min(sample_min, int(samples.min()))
                    sample_max = max(sample_max, int(samples.max()))

        if packet_count == 0:
            raise ValueError("WAV không chứa dữ liệu âm thanh PCM.")
        rms = float(np.sqrt(sum_squares / sample_count)) if sample_count else 0.0
        logger.info(
            "Discord PCM conversion summary: path=%s packets=%d duration=%.3fs "
            "sample_min=%d sample_max=%d peak=%d rms=%.1f",
            output_path, packet_count,
            (sample_count / 2) / WavPCMAudio.OUTPUT_RATE,
            sample_min, sample_max, peak, rms,
        )
        return output_path
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        source.cleanup()
