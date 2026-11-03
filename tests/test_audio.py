import wave
from pathlib import Path

import numpy as np

from discord_tts.services.audio import WavPCMAudio


def test_piper_wav_is_converted_to_discord_pcm(tmp_path: Path):
    path = tmp_path / "sample.wav"
    samples = (np.sin(np.linspace(0, 8 * np.pi, 441)) * 12000).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(samples.tobytes())

    audio = WavPCMAudio(path)
    packet = audio.read()
    assert len(packet) == 960 * 2 * 2
    assert audio.read() == b""
    audio.cleanup()
