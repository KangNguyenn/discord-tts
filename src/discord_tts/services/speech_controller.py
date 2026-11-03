from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

import discord

from discord_tts.services.audio import WavPCMAudio, ensure_opus_loaded
from discord_tts.tts.engine import PiperEngine


logger = logging.getLogger(__name__)


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

    def _voice_playback_diagnostics(
        self, voice_client: discord.VoiceClient, generation: int
    ) -> list[str]:
        """Inspect Discord-side permission and mute state before sending audio."""
        channel = voice_client.channel
        guild = voice_client.guild
        member = guild.me
        if channel is None or member is None:
            logger.warning(
                "Voice diagnostics unavailable: generation=%d channel=%r guild_member=%r",
                generation, channel, member,
            )
            return []

        permissions = channel.permissions_for(member)
        state = member.voice
        flags = {
            "view_channel": permissions.view_channel,
            "connect": permissions.connect,
            "speak": permissions.speak,
            "server_muted": bool(state and state.mute),
            "self_muted": bool(state and state.self_mute),
            "server_deafened": bool(state and state.deaf),
            "self_deafened": bool(state and state.self_deaf),
            "suppressed": bool(state and state.suppress),
        }
        logger.info(
            "Discord voice diagnostics: generation=%d guild=%s channel=%s "
            "endpoint=%s encryption_mode=%s latency=%s average_latency=%s "
            "view_channel=%s connect=%s speak=%s server_muted=%s self_muted=%s "
            "server_deafened=%s self_deafened=%s suppressed=%s",
            generation, guild.id, channel.name,
            voice_client.endpoint, voice_client.mode,
            voice_client.latency, voice_client.average_latency,
            flags["view_channel"], flags["connect"], flags["speak"],
            flags["server_muted"], flags["self_muted"],
            flags["server_deafened"], flags["self_deafened"], flags["suppressed"],
        )

        problems = []
        if not permissions.speak:
            problems.append(
                "Bot không có quyền Speak trong voice channel này. "
                "Hãy cấp quyền Speak cho role của bot."
            )
        if state and state.mute:
            problems.append(
                "Bot đang bị server mute trong Discord. Hãy unmute bot."
            )
        if state and state.self_mute:
            problems.append(
                "Bot đang self-mute trong Discord."
            )
        if state and state.suppress:
            problems.append(
                "Bot đang bị suppress trong Stage channel; cần cấp quyền Speaker."
            )
        return problems

    def submit(self, text: str, language: str, guild_id: int | None, source: str) -> None:
        """Must be called on the Discord asyncio loop."""
        self._generation += 1
        generation = self._generation
        voice_client = self.voice_provider(guild_id)
        logger.info(
            "Speech queued: generation=%d source=%s language=%s guild=%s "
            "voice_connected=%s channel=%s text_length=%d",
            generation, source, language, guild_id,
            bool(voice_client and voice_client.is_connected()),
            getattr(getattr(voice_client, "channel", None), "name", None),
            len(text),
        )
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
                logger.error(
                    "Cannot speak: voice client missing/disconnected (generation=%d guild=%s)",
                    generation, guild_id,
                )
                self.event_callback(
                    "speech_error",
                    {"source": source, "message": "Bot chưa tham gia voice channel."},
                )
                return

            # discord.py cannot transmit PCM AudioSources without native libopus.
            ensure_opus_loaded()
            logger.info("Opus encoder is loaded (generation=%d)", generation)

            speed, volume = self.settings_provider()
            logger.info(
                "Piper synthesis starting: generation=%d model=%s speed=%.2f volume=%.2f",
                generation, self.engine.model_id(language), speed, volume,
            )
            synthesis_started = time.monotonic()
            wav_path = await asyncio.to_thread(
                self.engine.synthesize, text, language, speed, volume
            )
            logger.info(
                "Piper synthesis completed: generation=%d elapsed=%.2fs wav=%s size=%d",
                generation, time.monotonic() - synthesis_started, wav_path,
                wav_path.stat().st_size if wav_path.exists() else -1,
            )
            if generation != self._generation:
                logger.info(
                    "Discarding synthesized audio because it was superseded "
                    "(generation=%d current=%d)", generation, self._generation,
                )
                return

            voice_client = self.voice_provider(guild_id)
            if voice_client is None or not voice_client.is_connected():
                logger.error(
                    "Voice disconnected during synthesis (generation=%d guild=%s)",
                    generation, guild_id,
                )
                self.event_callback(
                    "speech_error",
                    {"source": source, "message": "Bot chưa tham gia voice channel."},
                )
                return

            logger.info(
                "Starting Discord playback: generation=%d connected=%s channel=%s "
                "latency=%s",
                generation, voice_client.is_connected(),
                getattr(getattr(voice_client, "channel", None), "name", None),
                getattr(voice_client, "latency", None),
            )

            voice_problems = self._voice_playback_diagnostics(voice_client, generation)
            if voice_problems:
                message = " ".join(voice_problems)
                logger.error(
                    "Discord voice preflight failed (generation=%d): %s",
                    generation, message,
                )
                self.event_callback("speech_error", {"source": source, "message": message})
                return
            loop = asyncio.get_running_loop()
            finished: asyncio.Future[None] = loop.create_future()

            def after_playback(error: Exception | None) -> None:
                if error is not None:
                    logger.error(
                        "Discord audio player callback reported an error "
                        "(generation=%d): %s",
                        generation, error,
                        exc_info=(type(error), error, error.__traceback__),
                    )
                else:
                    logger.info("Discord audio player callback finished (generation=%d)", generation)

                def resolve() -> None:
                    if finished.done():
                        return
                    if error:
                        finished.set_exception(error)
                    else:
                        finished.set_result(None)
                loop.call_soon_threadsafe(resolve)

            audio = WavPCMAudio(wav_path)
            logger.info(
                "Audio source created: generation=%d sample_rate=%d channels=%d "
                "sample_width=%d",
                generation, audio.sample_rate, audio.channels, audio.sample_width,
            )
            voice_client.play(audio, after=after_playback)
            logger.info("voice_client.play() returned successfully (generation=%d)", generation)
            self.event_callback("speech_started", {"source": source, "text": text})
            try:
                await finished
            except asyncio.CancelledError:
                voice_client.stop()
                raise
            if generation == self._generation:
                self.event_callback("speech_finished", {"source": source, "text": text})
        except asyncio.CancelledError:
            logger.info("Speech task cancelled (generation=%d)", generation)
            raise
        except Exception as exc:
            logger.exception(
                "Speech pipeline failed: generation=%d source=%s language=%s",
                generation, source, language,
            )
            self.event_callback(
                "speech_error", {"source": source, "message": str(exc)}
            )
        finally:
            if wav_path:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    logger.exception("Could not remove temporary WAV file: %s", wav_path)

    def stop(self, guild_id: int | None = None) -> None:
        logger.info("Stop requested for guild=%s", guild_id)
        self._generation += 1
        voice_client = self.voice_provider(guild_id)
        if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
            voice_client.stop()
        self.event_callback("speech_stopped", {})
