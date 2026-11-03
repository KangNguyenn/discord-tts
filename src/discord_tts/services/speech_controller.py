from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

import discord

from discord_tts.services.audio import WavPCMAudio, ensure_opus_loaded
from discord_tts.tts.engine import PiperEngine


class SpeechController:
    """One playback pipeline shared by app chat, slash commands and Discord messages."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        voice_provider: Callable[[int | None], discord.VoiceClient | None],
        event_callback: Callable[[str, dict], None],
        engine: PiperEngine,
        settings_provider: Callable[[], tuple[float, float]],
    ):
        self.loop = loop
        self.voice_provider = voice_provider
        self.event_callback = event_callback
        self.engine = engine
        self.settings_provider = settings_provider
        self._generation = 0
        self._tasks: set[asyncio.Task] = set()

    def submit(self, text: str, language: str, guild_id: int | None, source: str) -> None:
        """Must be called on the Discord asyncio loop."""
        self._generation += 1
        generation = self._generation
        voice_client = self.voice_provider(guild_id)
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
            self.event_callback("speech_interrupted", {"source": source})
        task = self.loop.create_task(
            self._speak(generation, text, language, guild_id, source)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _speak(
        self,
        generation: int,
        text: str,
        language: str,
        guild_id: int | None,
        source: str,
    ) -> None:
        wav_path: Path | None = None
        try:
            voice_client = self.voice_provider(guild_id)
            if voice_client is None or not voice_client.is_connected():
                self.event_callback(
                    "speech_error",
                    {"source": source, "message": "Bot chưa tham gia voice channel."},
                )
                return

            # discord.py cannot transmit PCM AudioSources without native libopus.
            ensure_opus_loaded()

            speed, volume = self.settings_provider()
            wav_path = await asyncio.to_thread(
                self.engine.synthesize, text, language, speed, volume
            )
            if generation != self._generation:
                return

            voice_client = self.voice_provider(guild_id)
            if voice_client is None or not voice_client.is_connected():
                self.event_callback(
                    "speech_error",
                    {"source": source, "message": "Bot chưa tham gia voice channel."},
                )
                return

            loop = asyncio.get_running_loop()
            finished: asyncio.Future[None] = loop.create_future()

            def after_playback(error: Exception | None) -> None:
                def resolve() -> None:
                    if finished.done():
                        return
                    if error:
                        finished.set_exception(error)
                    else:
                        finished.set_result(None)
                loop.call_soon_threadsafe(resolve)

            audio = WavPCMAudio(wav_path)
            voice_client.play(audio, after=after_playback)
            self.event_callback("speech_started", {"source": source, "text": text})
            try:
                await finished
            except asyncio.CancelledError:
                voice_client.stop()
                raise
            if generation == self._generation:
                self.event_callback("speech_finished", {"source": source, "text": text})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.event_callback(
                "speech_error", {"source": source, "message": str(exc)}
            )
        finally:
            if wav_path:
                wav_path.unlink(missing_ok=True)

    def stop(self, guild_id: int | None = None) -> None:
        self._generation += 1
        voice_client = self.voice_provider(guild_id)
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
        self.event_callback("speech_stopped", {})
