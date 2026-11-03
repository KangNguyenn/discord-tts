from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import struct
import threading
from typing import Callable

import discord
from discord import app_commands
from discord.ext import commands

from discord_tts.config import AppConfig
from discord_tts.services.speech_controller import SpeechController
from discord_tts.storage.database import Database
from discord_tts.tts.engine import PiperEngine


logger = logging.getLogger(__name__)


class DiscordService:
    """Owns discord.py on a dedicated asyncio thread; GTK never blocks on Discord."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        engine: PiperEngine,
        event_callback: Callable[[str, dict], None],
    ):
        self.config = config
        self.database = database
        self.engine = engine
        self.event_callback = event_callback
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot: commands.Bot | None = None
        self.speech: SpeechController | None = None
        self.thread: threading.Thread | None = None
        self._token: str | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._guilds: dict[int, discord.Guild] = {}

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
        self._ready.clear()
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
            self._ready.clear()
            if self._stopping.is_set():
                self.event_callback("disconnected", {"message": "Bot đã dừng."})

    async def _run_bot(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.messages = True
        intents.message_content = True

        bot = commands.Bot(command_prefix="!", intents=intents)
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
            lambda: (self.config.tts_speed, self.config.tts_volume),
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
            try:
                await bot.tree.sync()
            except Exception as exc:
                self.event_callback("log", {"message": f"Không sync được slash commands: {exc}"})

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
            is_allowed_channel = (
                message.guild.id == self.config.guild_id
                and message.channel.id in self.config.read_text_channel_ids
            )
            if is_allowed_channel and content and not content.startswith(("!", "/")):
                self.event_callback(
                    "discord_message",
                    {
                        "text": content,
                        "language": self.config.discord_incoming_language,
                        "author": message.author.display_name,
                        "channel": getattr(message.channel, "name", "Discord"),
                        "guild_id": message.guild.id,
                    },
                )
                if self.speech:
                    self.speech.submit(
                        content,
                        self.config.discord_incoming_language,
                        message.guild.id,
                        "discord",
                    )

            # Overriding on_message disables the default command dispatcher.
            await bot.process_commands(message)

        @bot.tree.command(name="join", description="Cho bot tham gia voice channel của bạn")
        async def join(interaction: discord.Interaction) -> None:
            if not interaction.guild:
                await interaction.response.send_message("Lệnh này chỉ dùng trong server.", ephemeral=True)
                return
            member = interaction.user
            voice_state = getattr(member, "voice", None)
            channel = voice_state.channel if voice_state else None
            if channel is None:
                await interaction.response.send_message("Bạn cần vào voice channel trước.", ephemeral=True)
                return
            try:
                await self._join_channel(channel)
                self.config.guild_id = interaction.guild.id
                self.config.voice_channel_id = channel.id
                self.config.save()
                self._publish_guilds()
                await interaction.response.send_message(f"Đã vào **{channel.name}**.", ephemeral=True)
            except Exception as exc:
                await interaction.response.send_message(f"Không thể join: {exc}", ephemeral=True)

        @bot.tree.command(name="leave", description="Cho bot rời voice channel")
        async def leave(interaction: discord.Interaction) -> None:
            if interaction.guild and interaction.guild.voice_client:
                await interaction.guild.voice_client.disconnect(force=True)
                await interaction.response.send_message("Đã rời voice channel.", ephemeral=True)
                self.event_callback("voice_left", {})
            else:
                await interaction.response.send_message("Bot chưa ở trong voice channel.", ephemeral=True)

        @bot.tree.command(name="stop", description="Dừng câu TTS đang phát")
        async def stop(interaction: discord.Interaction) -> None:
            if self.speech:
                self.speech.stop(interaction.guild.id if interaction.guild else None)
            await interaction.response.send_message("Đã dừng TTS.", ephemeral=True)

        @bot.tree.command(name="tts", description="Đọc một câu bằng TTS")
        @app_commands.describe(text="Nội dung cần đọc", language="Ngôn ngữ của câu")
        @app_commands.choices(
            language=[
                app_commands.Choice(name="Tiếng Việt", value="vi"),
                app_commands.Choice(name="English", value="en"),
            ]
        )
        async def tts(
            interaction: discord.Interaction,
            text: str,
            language: app_commands.Choice[str],
        ) -> None:
            if not interaction.guild:
                await interaction.response.send_message("Lệnh này chỉ dùng trong server.", ephemeral=True)
                return
            if self.speech:
                self.speech.submit(text, language.value, interaction.guild.id, "slash")
            await interaction.response.send_message("Đã nhận câu TTS.", ephemeral=True)

        @bot.tree.command(name="quick", description="Đọc một Quick Chat theo tên")
        async def quick(interaction: discord.Interaction, name: str) -> None:
            item = self.database.find_quick_message(name)
            if item is None:
                await interaction.response.send_message("Không tìm thấy Quick Chat đó.", ephemeral=True)
                return
            if not interaction.guild:
                await interaction.response.send_message("Lệnh này chỉ dùng trong server.", ephemeral=True)
                return
            if self.speech:
                self.speech.submit(item["text"], item["language"], interaction.guild.id, "quick")
            await interaction.response.send_message(f"Đang đọc **{item['title']}**.", ephemeral=True)

        # Prefix aliases are convenient for servers where slash commands are unavailable.
        @bot.command(name="tts")
        async def prefix_tts(ctx: commands.Context, language: str, *, text: str) -> None:
            lang = "en" if language.lower() in {"en", "english"} else "vi"
            if ctx.guild and self.speech:
                self.speech.submit(text, lang, ctx.guild.id, "prefix")

        self._ready.set()
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

    def _instrument_voice_dave(self, voice_client: discord.VoiceClient) -> None:
        """Log DAVE negotiation without exposing MLS payloads or voice tokens."""
        websocket = getattr(voice_client, "ws", None)
        connection = getattr(voice_client, "_connection", None)
        if websocket is None or connection is None:
            logger.warning("Cannot instrument DAVE: voice websocket or connection is missing")
            return
        if getattr(websocket, "_discord_tts_dave_instrumented", False):
            return

        def log_state(stage: str, opcode: int | None = None) -> None:
            session = getattr(connection, "dave_session", None)
            try:
                can_encrypt = connection.can_encrypt
            except Exception:
                can_encrypt = "unavailable"
            logger.info(
                "Voice DAVE state: stage=%s opcode=%s protocol_version=%s "
                "session_exists=%s session_ready=%s can_encrypt=%s pending_transitions=%s",
                stage, opcode, getattr(connection, "dave_protocol_version", None),
                session is not None, getattr(session, "ready", None),
                can_encrypt, getattr(connection, "dave_pending_transitions", None),
            )

        original_received_message = websocket.received_message
        original_received_binary_message = websocket.received_binary_message
        original_send_binary = websocket.send_binary

        async def observed_received_message(message: dict) -> None:
            opcode = message.get("op")
            if opcode in (4, 21, 22, 24):
                logger.info(
                    "Voice DAVE gateway event received: opcode=%s data_keys=%s",
                    opcode,
                    sorted(message.get("d", {}).keys()) if isinstance(message.get("d"), dict) else [],
                )
                log_state("before-json-event", opcode)
            try:
                await original_received_message(message)
            except Exception:
                logger.exception("Voice DAVE JSON event handler failed: opcode=%s", opcode)
                raise
            if opcode in (4, 21, 22, 24):
                log_state("after-json-event", opcode)

        async def observed_received_binary_message(message: bytes) -> None:
            opcode = message[2] if len(message) >= 3 else None
            if opcode in (25, 27, 29, 30, 31):
                logger.info(
                    "Voice DAVE binary event received: opcode=%s bytes=%d",
                    opcode, len(message),
                )
                log_state("before-binary-event", opcode)
            try:
                await original_received_binary_message(message)
            except Exception:
                logger.exception("Voice DAVE binary event handler failed: opcode=%s", opcode)
                raise
            if opcode in (25, 27, 29, 30, 31):
                log_state("after-binary-event", opcode)

        async def observed_send_binary(opcode: int, payload: bytes) -> None:
            if opcode in (26, 28, 31):
                logger.info(
                    "Voice DAVE binary frame sent: opcode=%s payload_bytes=%d",
                    opcode, len(payload),
                )
            await original_send_binary(opcode, payload)

        websocket.received_message = observed_received_message
        websocket.received_binary_message = observed_received_binary_message
        websocket.send_binary = observed_send_binary
        websocket._discord_tts_dave_instrumented = True
        logger.info(
            "Installed voice DAVE diagnostics: channel=%s endpoint=%s",
            getattr(voice_client.channel, "name", None), voice_client.endpoint,
        )
        log_state("instrumentation-installed")

    def _instrument_voice_udp(self, voice_client: discord.VoiceClient) -> None:
        """Log whether RTP audio packets are accepted by the local UDP socket."""
        connection = getattr(voice_client, "_connection", None)
        if connection is None:
            logger.warning("Cannot instrument voice UDP: voice connection state is missing")
            return
        if getattr(connection, "_discord_tts_udp_instrumented", False):
            return

        original_send_packet = connection.send_packet
        original_get_voice_packet = voice_client._get_voice_packet
        packet_diagnostics = {"count": 0, "opus_decoder": None}

        def observed_get_voice_packet(opus_data: bytes) -> bytes:
            packet_diagnostics["count"] += 1
            packet_number = packet_diagnostics["count"]
            dave_session = getattr(connection, "dave_session", None)
            dave_protocol_version = getattr(connection, "dave_protocol_version", None)
            dave_ready = getattr(dave_session, "ready", None)
            try:
                dave_can_encrypt = connection.can_encrypt
            except Exception:
                dave_can_encrypt = "unavailable"

            if packet_number <= 3:
                try:
                    decoder = packet_diagnostics["opus_decoder"]
                    if decoder is None:
                        decoder = discord.opus.Decoder()
                        packet_diagnostics["opus_decoder"] = decoder
                    opus_frames = discord.opus.Decoder.packet_get_nb_frames(opus_data)
                    opus_samples_per_frame = discord.opus.Decoder.packet_get_samples_per_frame(opus_data)
                    decoded_pcm = decoder.decode(opus_data)
                    decoded_sample_count = len(decoded_pcm) // 2
                    decoded_rms = (
                        math.sqrt(
                            sum(sample * sample for (sample,) in struct.iter_unpack("<h", decoded_pcm))
                            / decoded_sample_count
                        )
                        if decoded_sample_count else 0.0
                    )
                    logger.info(
                        "Voice Opus self-check: packet=%d opus_bytes=%d opus_head=%s "
                        "frames=%d samples_per_frame=%d decoded_bytes=%d decoded_rms=%.1f",
                        packet_number, len(opus_data), opus_data[:8].hex(),
                        opus_frames, opus_samples_per_frame, len(decoded_pcm), decoded_rms,
                    )
                except Exception:
                    logger.exception(
                        "Voice Opus self-check failed: packet=%d opus_bytes=%d",
                        packet_number, len(opus_data),
                    )

            packet = original_get_voice_packet(opus_data)
            if packet_number <= 3:
                try:
                    rtp_sequence = struct.unpack(">H", packet[2:4])[0]
                    rtp_timestamp = struct.unpack(">I", packet[4:8])[0]
                    rtp_ssrc = struct.unpack(">I", packet[8:12])[0]
                    logger.info(
                        "Voice RTP packet prepared: packet=%d bytes=%d version=%d "
                        "payload_type=%d sequence=%d timestamp=%d ssrc=%d "
                        "dave_protocol_version=%s dave_session=%s dave_ready=%s "
                        "dave_can_encrypt=%s mode=%s",
                        packet_number, len(packet), packet[0] >> 6, packet[1] & 0x7F,
                        rtp_sequence, rtp_timestamp, rtp_ssrc,
                        dave_protocol_version, dave_session is not None, dave_ready,
                        dave_can_encrypt, voice_client.mode,
                    )
                except Exception:
                    logger.exception("Could not inspect prepared voice RTP packet")
            return packet

        voice_client._get_voice_packet = observed_get_voice_packet

        stats = {
            "sent_packets": 0,
            "sent_bytes": 0,
            "dropped_packets": 0,
            "last_error": None,
        }

        def observed_send_packet(packet: bytes) -> None:
            try:
                original_send_packet(packet)
            except OSError as exc:
                stats["dropped_packets"] += 1
                stats["last_error"] = repr(exc)
                logger.error(
                    "Voice UDP packet send failed: packet=%d bytes=%d error=%r",
                    stats["sent_packets"] + stats["dropped_packets"],
                    len(packet), exc,
                )
                raise

            stats["sent_packets"] += 1
            stats["sent_bytes"] += len(packet)
            if stats["sent_packets"] <= 2:
                try:
                    peer = connection.socket.getpeername()
                except OSError as exc:
                    peer = f"unavailable: {exc}"
                try:
                    local = connection.socket.getsockname()
                except OSError as exc:
                    local = f"unavailable: {exc}"
                logger.info(
                    "Voice UDP packet accepted by local socket: packet=%d bytes=%d "
                    "local=%s peer=%s",
                    stats["sent_packets"], len(packet), local, peer,
                )
            elif stats["sent_packets"] % 50 == 0:
                logger.debug(
                    "Voice UDP progress: sent_packets=%d sent_bytes=%d dropped_packets=%d",
                    stats["sent_packets"], stats["sent_bytes"], stats["dropped_packets"],
                )

        connection.send_packet = observed_send_packet
        connection._discord_tts_udp_stats = stats
        connection._discord_tts_udp_instrumented = True
        self._instrument_voice_dave(voice_client)
        logger.info(
            "Installed voice UDP diagnostics: channel=%s endpoint=%s mode=%s",
            getattr(voice_client.channel, "name", None),
            voice_client.endpoint, voice_client.mode,
        )

    async def _join_channel(self, channel: discord.VoiceChannel | discord.StageChannel) -> discord.VoiceClient:
        logger.info(
            "Joining voice channel: guild=%s (%s), channel=%s (%s)",
            channel.guild.name, channel.guild.id, channel.name, channel.id,
        )
        existing = channel.guild.voice_client
        if existing and existing.is_connected():
            if existing.channel and existing.channel.id == channel.id:
                self._instrument_voice_udp(existing)
                return existing
            await existing.move_to(channel)
            self._instrument_voice_udp(existing)
            return existing
        voice_client = await channel.connect()
        self._instrument_voice_udp(voice_client)
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

    def leave_voice(self) -> None:
        self._submit(self._leave_voice())

    async def _leave_voice(self) -> None:
        voice = self._voice_client(self.config.guild_id)
        logger.info("Leaving voice: voice_client=%r", voice)
        if voice and voice.is_connected():
            await voice.disconnect(force=True)
        self.event_callback("voice_left", {})

    def speak(self, text: str, language: str, source: str = "app") -> None:
        self._submit(self._speak(text, language, source))

    async def _speak(self, text: str, language: str, source: str) -> None:
        logger.info(
            "Speech requested: source=%s language=%s guild_id=%s text_length=%d",
            source, language, self.config.guild_id, len(text),
        )
        if not self.speech:
            self.event_callback("speech_error", {"source": source, "message": "Discord bot chưa sẵn sàng."})
            return
        self.speech.submit(text, language, self.config.guild_id, source)

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

    def set_read_channels(self, channel_ids: list[int], language: str) -> None:
        self.config.read_text_channel_ids = channel_ids
        self.config.discord_incoming_language = language
        self.config.save()

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
