from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Callable

import discord
from discord import app_commands

from discord_tts.config import AppConfig
from discord_tts.services.speech_controller import SpeechController
from discord_tts.tts.engine import PiperEngine


logger = logging.getLogger(__name__)


class DiscordService:
    """Owns discord.py on a dedicated asyncio thread; GTK never blocks on Discord."""

    def __init__(
        self,
        config: AppConfig,
        engine: PiperEngine,
        event_callback: Callable[[str, dict], None],
    ):
        self.config = config
        self.engine = engine
        self.event_callback = event_callback
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot: discord.Client | None = None
        self.speech: SpeechController | None = None
        self.thread: threading.Thread | None = None
        self._token: str | None = None
        self._stopping = threading.Event()
        self._guilds: dict[int, discord.Guild] = {}
        self._auto_join_attempted_user_id: int | None = None

    @property
    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self._stopping.is_set()

    def start(self, token: str) -> None:
        if self.is_running:
            logger.warning("Ignoring start request: Discord bot is already running")
            return
        logger.info("Starting Discord bot worker thread")
        self._token = token
        self._stopping.clear()
        self.thread = threading.Thread(
            target=self._thread_main, name="discord-asyncio", daemon=True
        )
        self.thread.start()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop

        def handle_asyncio_exception(_loop, context):
            error = context.get("exception")
            if error:
                logger.error(
                    "Unhandled exception in Discord asyncio loop: %s",
                    context.get("message", "no message"),
                    exc_info=(type(error), error, error.__traceback__),
                )
            else:
                logger.error("Unhandled asyncio loop error: %s", context)

        loop.set_exception_handler(handle_asyncio_exception)
        logger.info("Discord asyncio loop started")
        try:
            loop.run_until_complete(self._run_bot())
        except Exception as exc:
            logger.exception("Discord bot task terminated with an exception")
            self.event_callback("connection_error", {"message": str(exc)})
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            logger.info("Discord asyncio loop closed")
            self.loop = None
            self.bot = None
            self.speech = None
            if self._stopping.is_set():
                self.event_callback("disconnected", {"message": "Bot đã dừng."})

    async def _run_bot(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.messages = True
        intents.message_content = True

        initial_status, initial_activity = self._presence_values()
        bot = discord.Client(
            intents=intents, status=initial_status, activity=initial_activity
        )
        command_tree = app_commands.CommandTree(bot)
        self.bot = bot
        logger.info(
            "Created Discord client (voice intent=%s, guilds intent=%s)",
            intents.voice_states, intents.guilds,
        )
        self.speech = SpeechController(
            asyncio.get_running_loop(),
            self._voice_client,
            self.event_callback,
            self.engine,
        )

        @bot.event
        async def on_ready() -> None:
            logger.info(
                "Discord READY: user=%s id=%s guild_count=%d",
                bot.user, getattr(bot.user, "id", None), len(bot.guilds),
            )
            logger.info("Native Opus currently loaded: %s", discord.opus.is_loaded())
            if bot.user:
                self.event_callback(
                    "connected",
                    {"username": str(bot.user), "user_id": bot.user.id},
                )
            self._guilds = {guild.id: guild for guild in bot.guilds}
            self._publish_guilds()
            await self._auto_join_configured_user()
            # The previous app registered global slash commands. Syncing an
            # empty command tree removes those stale commands from Discord.
            try:
                command_tree.clear_commands(guild=None)
                await command_tree.sync()
            except Exception as exc:
                logger.warning("Could not clear previously registered Discord commands: %s", exc)

        @bot.event
        async def on_disconnect() -> None:
            logger.warning("Discord gateway disconnected; discord.py will attempt reconnect")
            self.event_callback("reconnecting", {"message": "Mất kết nối Discord, đang thử kết nối lại…"})

        @bot.event
        async def on_resumed() -> None:
            logger.info("Discord gateway session resumed")
            self.event_callback("connected", {"username": str(bot.user or "Bot")})
            self._publish_guilds()

        @bot.event
        async def on_guild_join(guild: discord.Guild) -> None:
            self._guilds[guild.id] = guild
            self._publish_guilds()

        @bot.event
        async def on_guild_remove(guild: discord.Guild) -> None:
            self._guilds.pop(guild.id, None)
            if self.config.guild_id == guild.id:
                self.config.guild_id = None
                self.config.voice_channel_id = None
                self.config.save()
            self._publish_guilds()

        @bot.event
        async def on_message(message: discord.Message) -> None:
            if message.author.bot or message.guild is None:
                return

            content = (message.content or "").strip()
            profile = self.config.discord_reader
            is_allowed_channel = (
                message.guild.id == self.config.guild_id
                and message.channel.id in self.config.read_text_channel_ids
            )
            if is_allowed_channel and content:
                model_id = self.engine.model_id(profile.model_id)
                language = self.engine.language_code(model_id)
                self.event_callback(
                    "discord_message",
                    {
                        "text": content,
                        "language": language,
                        "model_id": model_id,
                        "author": message.author.display_name,
                        "channel": getattr(message.channel, "name", "Discord"),
                        "guild_id": message.guild.id,
                    },
                )
                if self.speech:
                    self.speech.submit(
                        content,
                        model_id,
                        message.guild.id,
                        "discord",
                        profile.speaker_id,
                        profile.speed,
                        profile.volume,
                    )

        try:
            await bot.start(self._token, reconnect=True)
        finally:
            await bot.close()

    def _publish_guilds(self) -> None:
        guilds = []
        for guild in self._guilds.values():
            voice_channels = [
                {"id": channel.id, "name": channel.name}
                for channel in guild.voice_channels
            ]
            text_channels = [
                {"id": channel.id, "name": channel.name}
                for channel in guild.text_channels
            ]
            guilds.append(
                {
                    "id": guild.id,
                    "name": guild.name,
                    "voice_channels": voice_channels,
                    "text_channels": text_channels,
                }
            )
        guilds.sort(key=lambda item: item["name"].casefold())
        if self.config.guild_id not in self._guilds and guilds:
            self.config.guild_id = guilds[0]["id"]
            self.config.voice_channel_id = None
            self.config.save()
        self.event_callback("guilds_updated", {"guilds": guilds, "selected_id": self.config.guild_id})

    def _voice_client(self, guild_id: int | None) -> discord.VoiceClient | None:
        if not self.bot:
            return None
        guild = self.bot.get_guild(guild_id or self.config.guild_id or 0)
        return guild.voice_client if guild else None

    async def _join_channel(self, channel: discord.VoiceChannel | discord.StageChannel) -> discord.VoiceClient:
        logger.info(
            "Joining voice channel: guild=%s (%s), channel=%s (%s)",
            channel.guild.name, channel.guild.id, channel.name, channel.id,
        )
        existing = channel.guild.voice_client
        if existing and existing.is_connected():
            if existing.channel and existing.channel.id == channel.id:
                return existing
            await existing.move_to(channel)
            return existing
        voice_client = await channel.connect()
        logger.info(
            "Voice connect completed: connected=%s channel=%s voice_client=%r",
            voice_client.is_connected(), getattr(voice_client.channel, "name", None), voice_client,
        )
        return voice_client

    def join_selected_channel(self, guild_id: int, channel_id: int) -> None:
        self._submit(self._join_selected_channel(guild_id, channel_id))

    async def _join_selected_channel(self, guild_id: int, channel_id: int) -> None:
        try:
            guild = self.bot.get_guild(guild_id) if self.bot else None
            channel = guild.get_channel(channel_id) if guild else None
            if channel is None or not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                raise RuntimeError("Không tìm thấy voice channel đã chọn.")
            voice_client = await self._join_channel(channel)
            logger.info(
                "Voice joined successfully: connected=%s playing=%s channel=%s",
                voice_client.is_connected(), voice_client.is_playing(),
                getattr(voice_client.channel, "name", None),
            )
            self.config.guild_id = guild_id
            self.config.voice_channel_id = channel_id
            self.config.save()
            self.event_callback("voice_joined", {"channel": channel.name, "guild_id": guild_id})
        except Exception as exc:
            logger.exception("Failed to join selected voice channel")
            self.event_callback("voice_error", {"message": str(exc)})

    def auto_join_configured_user(self) -> None:
        """Re-check the configured user's current voice channel immediately."""
        self._submit(self._auto_join_configured_user(force=True))

    async def _auto_join_configured_user(self, force: bool = False) -> None:
        if not self.bot or not self.bot.is_ready():
            return

        user_id = self.config.auto_join_user_id
        if user_id is None:
            self._auto_join_attempted_user_id = None
            return
        if not force and self._auto_join_attempted_user_id == user_id:
            return
        self._auto_join_attempted_user_id = user_id

        target_channel = None
        for guild in self.bot.guilds:
            for channel in guild.channels:
                if not isinstance(
                    channel, (discord.VoiceChannel, discord.StageChannel)
                ):
                    continue
                if user_id in channel.voice_states:
                    target_channel = channel
                    break
            if target_channel is not None:
                break

        if target_channel is None:
            logger.info(
                "Auto-join skipped: user_id=%s is not in a voice channel "
                "in any server shared with the bot",
                user_id,
            )
            return

        try:
            voice_client = await self._join_channel(target_channel)
            logger.info(
                "Auto-joined configured user: user_id=%s guild=%s channel=%s "
                "connected=%s",
                user_id, target_channel.guild.id, target_channel.id,
                voice_client.is_connected(),
            )
            self.config.guild_id = target_channel.guild.id
            self.config.voice_channel_id = target_channel.id
            self.config.save()
            self.event_callback(
                "voice_joined",
                {
                    "channel": target_channel.name,
                    "guild_id": target_channel.guild.id,
                    "auto_join": True,
                },
            )
        except Exception as exc:
            logger.exception(
                "Could not auto-join voice channel for user_id=%s", user_id
            )
            self.event_callback(
                "voice_error",
                {"message": f"Không thể tự động tham gia voice channel: {exc}"},
            )

    def leave_voice(self) -> None:
        self._submit(self._leave_voice())

    async def _leave_voice(self) -> None:
        voice = self._voice_client(self.config.guild_id)
        logger.info("Leaving voice: voice_client=%r", voice)
        if voice and voice.is_connected():
            await voice.disconnect(force=True)
        self.event_callback("voice_left", {})

    def speak(
        self, text: str, model_id: str, speaker_id: int = 0,
        speed: float = 1.0, volume: float = 1.0, source: str = "app",
    ) -> None:
        self._submit(self._speak(text, model_id, speaker_id, speed, volume, source))

    async def _speak(
        self, text: str, model_id: str, speaker_id: int,
        speed: float, volume: float, source: str,
    ) -> None:
        logger.info(
            "Speech requested: source=%s model=%s speaker_id=%d speed=%.2f volume=%.2f guild_id=%s text_length=%d",
            source, model_id, speaker_id, speed, volume, self.config.guild_id, len(text),
        )
        if not self.speech:
            self.event_callback("speech_error", {"source": source, "message": "Discord bot chưa sẵn sàng."})
            return
        self.speech.submit(
            text, model_id, self.config.guild_id, source, speaker_id, speed, volume
        )

    def stop_speech(self) -> None:
        self._submit(self._stop_speech())

    async def _stop_speech(self) -> None:
        if self.speech:
            self.speech.stop(self.config.guild_id)

    def set_guild(self, guild_id: int) -> None:
        self.config.guild_id = guild_id
        self.config.voice_channel_id = None
        self.config.save()
        self._publish_guilds_threadsafe()

    def set_read_channels(self, channel_ids: list[int]) -> None:
        self.config.read_text_channel_ids = channel_ids
        self.config.save()

    def update_presence(self) -> None:
        if self.loop and self.is_running:
            self._submit(self._apply_presence())

    def _presence_values(self):
        statuses = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        activity_types = {
            "playing": discord.ActivityType.playing,
            "listening": discord.ActivityType.listening,
            "watching": discord.ActivityType.watching,
            "competing": discord.ActivityType.competing,
        }
        status = statuses.get(self.config.discord_status, discord.Status.online)
        activity_name = self.config.discord_activity_name.strip() or "Discord TTS"
        activity_kind = self.config.discord_activity_type
        activity = None
        if activity_kind == "streaming":
            stream_url = self.config.discord_streaming_url.strip()
            if stream_url:
                activity = discord.Streaming(name=activity_name, url=stream_url)
        else:
            activity_type = activity_types.get(
                activity_kind, discord.ActivityType.listening
            )
            activity = discord.Activity(type=activity_type, name=activity_name)
        return status, activity

    async def _apply_presence(self) -> None:
        if not self.bot or not self.bot.is_ready():
            return
        status, activity = self._presence_values()
        await self.bot.change_presence(status=status, activity=activity)

    def _publish_guilds_threadsafe(self) -> None:
        if not self.loop:
            return
        self.loop.call_soon_threadsafe(self._publish_guilds)

    def _submit(self, coroutine) -> None:
        if self.loop and self.is_running:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

            def log_future_result(completed):
                try:
                    completed.result()
                except concurrent.futures.CancelledError:
                    logger.debug("Submitted Discord coroutine was cancelled")
                except Exception:
                    logger.exception("Submitted Discord coroutine failed")

            future.add_done_callback(log_future_result)
        else:
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            self.event_callback("connection_error", {"message": "Discord bot chưa kết nối."})

    def shutdown(self) -> None:
        if not self.loop or not self.is_running:
            logger.debug("Discord shutdown requested while bot is not running")
            return
        logger.info("Shutting down Discord bot")
        self._stopping.set()
        if self.bot:
            asyncio.run_coroutine_threadsafe(self.bot.close(), self.loop)
