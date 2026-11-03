from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from discord_tts.config import APP_NAME, AppConfig, DATA_DIR, HISTORY_DB
from discord_tts.services.discord_service import DiscordService
from discord_tts.storage.database import Database
from discord_tts.tts.engine import PiperEngine


logger = logging.getLogger(__name__)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, config: AppConfig, database: Database,
                 engine: PiperEngine):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.config = config
        self.database = database
        self.engine = engine
        self.discord: DiscordService | None = None
        self.guilds: dict[int, dict] = {}
        self.voice_channels: list[dict] = []
        self._loading_dropdowns = False
        self._settings_checks: dict[int, Gtk.CheckButton] = {}
        self._preview_generation = 0
        self._preview_media: Gtk.MediaFile | None = None
        self._preview_path: Path | None = None
        self._preview_button: Gtk.Button | None = None
        self._preview_stop_button: Gtk.Button | None = None
        self._preview_status: Gtk.Label | None = None

        self.set_default_size(760, 760)
        self.set_size_request(560, 600)
        self._load_css()
        self._build_ui()
        self._load_history()
        self._load_quick_chat()
        self.connect("close-request", self._on_close_request)

    def set_discord_service(self, service: DiscordService) -> None:
        self.discord = service

    def _load_css(self) -> None:
        css_path = Path(__file__).with_name("app.css")
        if not css_path.exists():
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _build_ui(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(12)
        root.set_margin_bottom(14)
        root.set_margin_start(16)
        root.set_margin_end(16)
        self.set_child(root)

        header = Gtk.HeaderBar()
        title = Gtk.Label(label="Discord TTS")
        title.add_css_class("title")
        header.set_title_widget(title)
        settings_button = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        settings_button.set_tooltip_text("Settings")
        settings_button.connect("clicked", self._open_settings)
        header.pack_end(settings_button)
        token_button = Gtk.Button.new_from_icon_name("dialog-password-symbolic")
        token_button.set_tooltip_text("Thay Bot Token")
        token_button.connect("clicked", self._change_token)
        header.pack_end(token_button)
        menu_model = Gio.Menu.new()
        menu_model.append("Settings", "app.settings")
        menu_model.append("Quit", "app.quit")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu_model)
        menu_button.set_tooltip_text("Application menu")
        header.pack_end(menu_button)
        self.set_titlebar(header)

        # Connection controls.
        connection_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        connection_card.add_css_class("top-card")
        root.append(connection_card)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_dot = Gtk.Box()
        self.status_dot.add_css_class("status-dot")
        self.status_text = Gtk.Label(label="Chưa kết nối Discord")
        self.status_text.set_xalign(0)
        self.status_text.set_hexpand(True)
        status_row.append(self.status_dot)
        status_row.append(self.status_text)
        self.connection_hint = Gtk.Label(label="Bot chạy trên máy này · TTS offline")
        self.connection_hint.add_css_class("dim-label")
        self.connection_hint.set_xalign(1)
        status_row.append(self.connection_hint)
        connection_card.append(status_row)

        selectors = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.guild_dropdown = Gtk.DropDown.new_from_strings(["Chưa có server"])
        self.guild_dropdown.set_hexpand(True)
        self.guild_dropdown.set_tooltip_text("Discord server")
        self.guild_dropdown.connect("notify::selected", self._on_guild_selected)
        self.channel_dropdown = Gtk.DropDown.new_from_strings(["Chọn voice channel"])
        self.channel_dropdown.set_hexpand(True)
        self.channel_dropdown.set_tooltip_text("Voice channel")
        selectors.append(self.guild_dropdown)
        selectors.append(self.channel_dropdown)
        connection_card.append(selectors)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.join_button = Gtk.Button(label="Join voice")
        self.join_button.add_css_class("suggested-action")
        self.join_button.set_icon_name("audio-volume-high-symbolic")
        self.join_button.connect("clicked", self._join_voice)
        self.leave_button = Gtk.Button(label="Leave")
        self.leave_button.set_icon_name("call-stop-symbolic")
        self.leave_button.connect("clicked", self._leave_voice)
        self.leave_button.set_sensitive(False)
        self.connect_button = Gtk.Button(label="Connect bot")
        self.connect_button.set_icon_name("network-connect-symbolic")
        self.connect_button.connect("clicked", self._connect_bot)
        actions.append(self.join_button)
        actions.append(self.leave_button)
        actions.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        actions.append(self.connect_button)
        connection_card.append(actions)

        # Chat history area.
        chat_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chat_title = Gtk.Label(label="Conversation")
        chat_title.add_css_class("heading")
        chat_title.set_xalign(0)
        chat_title.set_hexpand(True)
        chat_header.append(chat_title)
        self.speaking_label = Gtk.Label(label="")
        self.speaking_label.add_css_class("dim-label")
        chat_header.append(self.speaking_label)
        root.append(chat_header)

        self.chat_list = Gtk.ListBox()
        self.chat_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_list.add_css_class("chat-list")
        self.chat_scroll = Gtk.ScrolledWindow()
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_scroll.set_vexpand(True)
        self.chat_scroll.set_child(self.chat_list)
        self.chat_scroll.add_css_class("top-card")
        root.append(self.chat_scroll)

        # Quick Chat shortcuts.
        quick_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        quick_label = Gtk.Label(label="Quick Chat")
        quick_label.add_css_class("heading")
        quick_label.set_xalign(0)
        quick_label.set_hexpand(True)
        quick_header.append(quick_label)
        add_quick = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_quick.set_tooltip_text("Thêm Quick Chat")
        add_quick.connect("clicked", self._add_quick_chat)
        quick_header.append(add_quick)
        root.append(quick_header)
        self.quick_box = Gtk.FlowBox()
        self.quick_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.quick_box.set_max_children_per_line(4)
        self.quick_box.set_min_children_per_line(2)
        self.quick_box.set_row_spacing(5)
        self.quick_box.set_column_spacing(5)
        self.quick_box.set_homogeneous(False)
        root.append(self.quick_box)

        # Chat composer: Enter sends; Shift+Enter inserts a newline.
        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        composer.add_css_class("composer")
        root.append(composer)
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        lang_label = Gtk.Label(label="Language")
        lang_label.add_css_class("dim-label")
        top_row.append(lang_label)
        self.language_dropdown = Gtk.DropDown.new_from_strings(["Tiếng Việt", "English"])
        self.language_dropdown.set_selected(0)
        self.language_dropdown.set_tooltip_text("Chọn ngôn ngữ cho câu sắp gửi")
        top_row.append(self.language_dropdown)
        top_row.append(Gtk.Label(label="· Enter để gửi · Shift+Enter xuống dòng"))
        top_row.get_last_child().add_css_class("dim-label")
        composer.append(top_row)

        text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.input_view = Gtk.TextView()
        self.input_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.input_view.set_accepts_tab(False)
        self.input_view.set_size_request(-1, 66)
        self.input_view.set_left_margin(5)
        self.input_view.set_right_margin(5)
        self.input_view.set_top_margin(5)
        self.input_view.set_bottom_margin(5)
        self.input_view.set_vexpand(False)
        self.input_view.get_buffer().connect("changed", self._on_input_changed)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_input_key)
        self.input_view.add_controller(key_controller)
        input_scroll = Gtk.ScrolledWindow()
        input_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        input_scroll.set_min_content_height(66)
        input_scroll.set_max_content_height(138)
        input_scroll.set_child(self.input_view)
        input_scroll.set_hexpand(True)
        input_overlay = Gtk.Overlay()
        input_overlay.set_child(input_scroll)
        self.input_placeholder = Gtk.Label(label="Nhập câu muốn nói…  (Enter để gửi)")
        self.input_placeholder.set_xalign(0)
        self.input_placeholder.set_yalign(0)
        self.input_placeholder.add_css_class("dim-label")
        self.input_placeholder.set_margin_start(12)
        self.input_placeholder.set_margin_top(12)
        self.input_placeholder.set_can_target(False)
        input_overlay.add_overlay(self.input_placeholder)
        text_row.append(input_overlay)
        self.send_button = Gtk.Button.new_from_icon_name("paper-plane-symbolic")
        self.send_button.add_css_class("suggested-action")
        self.send_button.add_css_class("circular")
        self.send_button.set_valign(Gtk.Align.END)
        self.send_button.set_tooltip_text("Send and speak (Enter)")
        self.send_button.set_sensitive(False)
        self.send_button.connect("clicked", self._send_message)
        text_row.append(self.send_button)
        composer.append(text_row)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.footer_status = Gtk.Label(label="Chọn ngôn ngữ trước mỗi câu · Voice model được lưu offline")
        self.footer_status.add_css_class("dim-label")
        self.footer_status.set_xalign(0)
        self.footer_status.set_hexpand(True)
        footer.append(self.footer_status)
        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.set_icon_name("media-playback-stop-symbolic")
        self.stop_button.connect("clicked", lambda *_: self.discord.stop_speech() if self.discord else None)
        footer.append(self.stop_button)
        root.append(footer)

    def _on_input_changed(self, _buffer) -> None:
        text = self._input_text().strip()
        self.send_button.set_sensitive(bool(text))
        self.input_placeholder.set_visible(not bool(text))

    def _input_text(self) -> str:
        buffer = self.input_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, True)

    def _on_input_key(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                return False
            self._send_message()
            return True
        return False

    def _selected_language(self) -> str:
        return "en" if self.language_dropdown.get_selected() == 1 else "vi"

    def _send_message(self, *_args) -> None:
        text = self._input_text().strip()
        if not text:
            return
        language = self._selected_language()
        self._append_bubble(text, "outgoing", language, "You")
        self.database.add_history("outgoing", "app", language, text)
        self.input_view.get_buffer().set_text("")
        if not self.discord:
            self._append_bubble("Discord bot chưa được cấu hình.", "system", language, "System")
            return
        self.discord.speak(text, language, "app")
        self.input_view.grab_focus()

    def _append_bubble(self, text: str, direction: str, language: str, author: str, timestamp: str | None = None) -> None:
        row = Gtk.ListBoxRow()
        row.add_css_class("chat-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        line.set_halign(Gtk.Align.END if direction == "outgoing" else Gtk.Align.START)
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.add_css_class("chat-bubble")
        bubble.add_css_class(direction)
        message = Gtk.Label(label=text)
        message.set_wrap(True)
        message.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        message.set_xalign(0)
        message.set_selectable(True)
        message.set_max_width_chars(64)
        bubble.append(message)
        try:
            time = datetime.fromisoformat(timestamp).astimezone().strftime("%H:%M") if timestamp else datetime.now().strftime("%H:%M")
        except (TypeError, ValueError):
            time = datetime.now().strftime("%H:%M")
        language_tag = "EN" if language == "en" else "VI"
        meta = Gtk.Label(label=f"{author} · {language_tag} · {time}")
        meta.set_xalign(1 if direction == "outgoing" else 0)
        meta.add_css_class("chat-meta")
        bubble.append(meta)
        line.append(bubble)
        row.set_child(line)
        self.chat_list.append(row)
        GLib.idle_add(self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self):
        adjustment = self.chat_scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return GLib.SOURCE_REMOVE

    def _load_history(self) -> None:
        for item in self.database.recent_history(70):
            direction = item["direction"] if item["direction"] in {"incoming", "outgoing", "system"} else "outgoing"
            author = "You" if direction == "outgoing" else item["source"].title()
            self._append_bubble(item["text"], direction, item["language"], author, item["created_at"])

    def _load_quick_chat(self) -> None:
        child = self.quick_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.quick_box.remove(child)
            child = next_child
        for item in self.database.quick_messages():
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
            chip.add_css_class("quick-chip")

            speak_button = Gtk.Button(label=item["title"])
            speak_button.add_css_class("quick-button")
            speak_button.set_tooltip_text(item["text"])
            speak_button.connect(
                "clicked", self._on_quick_clicked, item["text"], item["language"]
            )
            chip.append(speak_button)

            delete_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            delete_button.add_css_class("flat")
            delete_button.set_tooltip_text(f"Xóa Quick Chat: {item['title']}")
            delete_button.connect(
                "clicked", self._delete_quick_chat, item["id"], item["title"]
            )
            chip.append(delete_button)
            self.quick_box.append(chip)

    def _on_quick_clicked(self, _button, text: str, language: str) -> None:
        self.language_dropdown.set_selected(1 if language == "en" else 0)
        self.input_view.get_buffer().set_text(text)
        self._send_message()

    def _delete_quick_chat(self, _button, quick_id: int, title: str) -> None:
        # Gtk.MessageDialog.set_secondary_text() is not available in GTK4.
        dialog = Gtk.Dialog(title="Xóa Quick Chat", transient_for=self, modal=True)
        dialog.add_button("Hủy", Gtk.ResponseType.CANCEL)
        dialog.add_button("Xóa", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        message = Gtk.Label(
            label=f"Xóa Quick Chat “{title}”?\nShortcut này sẽ bị xóa khỏi danh sách."
        )
        message.set_wrap(True)
        message.set_xalign(0)
        content.append(message)

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK:
                self.database.delete_quick_message(quick_id)
                self._load_quick_chat()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _add_quick_chat(self, *_args) -> None:
        dialog = Gtk.Dialog(title="New Quick Chat", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        title_entry = Gtk.Entry(placeholder_text="Tên nút, ví dụ: Hello")
        text_entry = Gtk.Entry(placeholder_text="Câu sẽ được đọc")
        language = Gtk.DropDown.new_from_strings(["Tiếng Việt", "English"])
        content.append(title_entry)
        content.append(text_entry)
        content.append(language)

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK and title_entry.get_text().strip() and text_entry.get_text().strip():
                lang = "en" if language.get_selected() == 1 else "vi"
                self.database.add_quick_message(title_entry.get_text().strip(), text_entry.get_text().strip(), lang)
                self._load_quick_chat()
            dialog.destroy()
        dialog.connect("response", on_response)
        dialog.present()

    def _set_status(self, text: str, state: str = "neutral") -> None:
        self.status_text.set_text(text)
        self.status_dot.remove_css_class("connected")
        self.status_dot.remove_css_class("warning")
        self.status_dot.remove_css_class("error")
        if state != "neutral":
            self.status_dot.add_css_class(state)

    def handle_service_event(self, event: str, payload: dict) -> None:
        if event == "connected":
            self._set_status(f"Connected as {payload.get('username', 'Bot')}", "connected")
            self.connect_button.set_sensitive(False)
            self._notify("Discord đã kết nối", "Bot đang hoạt động.")
        elif event == "reconnecting":
            self._set_status("Đang kết nối lại…", "warning")
            self._notify("Mất kết nối Discord", payload.get("message", "Đang thử kết nối lại."))
        elif event == "disconnected":
            self._set_status("Đã ngắt kết nối", "neutral")
            self.connect_button.set_sensitive(True)
        elif event == "connection_error":
            self._set_status("Lỗi kết nối Discord", "error")
            self.connect_button.set_sensitive(True)
            self._append_bubble(payload.get("message", "Unknown error"), "system", "vi", "System")
        elif event == "guilds_updated":
            self._update_guilds(payload.get("guilds", []), payload.get("selected_id"))
        elif event == "voice_joined":
            self._set_status(f"Đang ở voice: {payload.get('channel', '')}", "connected")
            self.leave_button.set_sensitive(True)
        elif event == "voice_left":
            self.leave_button.set_sensitive(False)
            self._set_status("Đã rời voice channel", "connected")
        elif event == "voice_error":
            self._append_bubble(payload.get("message", "Voice error"), "system", "vi", "System")
        elif event == "discord_message":
            text = payload.get("text", "")
            language = payload.get("language", "vi")
            author = payload.get("author", "Discord")
            self._append_bubble(text, "incoming", language, author)
            self.database.add_history("incoming", "discord", language, text)
        elif event == "speech_started":
            self.speaking_label.set_text("Đang nói…")
        elif event in {"speech_finished", "speech_stopped", "speech_interrupted"}:
            self.speaking_label.set_text("")
        elif event == "speech_error":
            self.speaking_label.set_text("")
            self._append_bubble(payload.get("message", "TTS error"), "system", "vi", "TTS")
        elif event == "log":
            self.footer_status.set_text(payload.get("message", ""))

    def _update_guilds(self, guilds: list[dict], selected_id: int | None) -> None:
        self._loading_dropdowns = True
        self.guilds = {item["id"]: item for item in guilds}
        names = [item["name"] for item in guilds] or ["Bot chưa có server"]
        self.guild_dropdown.set_model(Gtk.StringList.new(names))
        ids = list(self.guilds.keys())
        if selected_id in ids:
            self.guild_dropdown.set_selected(ids.index(selected_id))
        elif ids:
            self.guild_dropdown.set_selected(0)
            selected_id = ids[0]
        self._loading_dropdowns = False
        self._update_voice_channels(selected_id)

    def _update_voice_channels(self, guild_id: int | None) -> None:
        item = self.guilds.get(guild_id) if guild_id else None
        self.voice_channels = item["voice_channels"] if item else []
        names = [channel["name"] for channel in self.voice_channels] or ["Không có voice channel"]
        self.channel_dropdown.set_model(Gtk.StringList.new(names))
        selected = next(
            (index for index, channel in enumerate(self.voice_channels)
             if channel["id"] == self.config.voice_channel_id),
            0,
        )
        self.channel_dropdown.set_selected(selected)

    def _on_guild_selected(self, dropdown, _pspec) -> None:
        if self._loading_dropdowns:
            return
        ids = list(self.guilds.keys())
        index = dropdown.get_selected()
        if index >= len(ids):
            return
        guild_id = ids[index]
        if self.discord:
            self.discord.set_guild(guild_id)
        self.config.guild_id = guild_id
        self.config.voice_channel_id = None
        self.config.save()
        self._update_voice_channels(guild_id)

    def _join_voice(self, *_args) -> None:
        if not self.discord:
            self._append_bubble("Hãy kết nối bot Discord trước.", "system", "vi", "System")
            return
        guild_ids = list(self.guilds.keys())
        guild_index = self.guild_dropdown.get_selected()
        channel_index = self.channel_dropdown.get_selected()
        if guild_index >= len(guild_ids) or channel_index >= len(self.voice_channels):
            self._append_bubble("Chọn server và voice channel hợp lệ.", "system", "vi", "System")
            return
        self.discord.join_selected_channel(guild_ids[guild_index], self.voice_channels[channel_index]["id"])

    def _leave_voice(self, *_args) -> None:
        if self.discord:
            self.discord.leave_voice()

    def _connect_bot(self, *_args) -> None:
        token = self.config.discord_token.strip()
        if not token:
            self._show_token_dialog()
            return
        self._start_discord(token)

    def _start_discord(self, token: str) -> None:
        if self.discord and self.discord.is_running:
            return
        if not self.discord:
            self.discord = DiscordService(self.config, self.database, self.engine, self._post_service_event)
        self.connect_button.set_sensitive(False)
        self._set_status("Đang kết nối Discord…", "warning")
        self.discord.start(token)

    def _post_service_event(self, event: str, payload: dict) -> None:
        GLib.idle_add(self.handle_service_event, event, payload)

    def _show_token_dialog(self) -> None:
        dialog = Gtk.Dialog(title="Thiết lập Discord Bot", transient_for=self, modal=True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save and connect", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(9)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        explanation = Gtk.Label(
            label=(
                "Dán Bot Token từ Discord Developer Portal. Token được lưu cục bộ "
                "trong config.json riêng của ứng dụng."
            )
        )
        explanation.set_wrap(True)
        explanation.set_xalign(0)
        # Gtk.PasswordEntry is not available in all GTK4 Python bindings
        # with the Gtk.Entry placeholder API. Use Gtk.Entry with password
        # visibility disabled, plus an explicit show/hide control.
        token_entry = Gtk.Entry()
        token_entry.set_placeholder_text("Discord Bot Token")
        token_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        token_entry.set_visibility(False)
        token_entry.set_hexpand(True)
        token_entry.set_text(self.config.discord_token)

        token_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        token_row.append(token_entry)
        visibility_button = Gtk.ToggleButton(label="Hiện")
        visibility_button.set_tooltip_text("Hiện hoặc ẩn Bot Token")

        def on_token_visibility_toggled(button):
            visible = button.get_active()
            token_entry.set_visibility(visible)
            button.set_label("Ẩn" if visible else "Hiện")

        visibility_button.connect("toggled", on_token_visibility_toggled)
        token_row.append(visibility_button)

        content.append(explanation)
        content.append(token_row)

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK:
                new_token = token_entry.get_text().strip()
                if not new_token:
                    self._append_bubble(
                        "Bot Token không được để trống.", "system", "vi", "System"
                    )
                    dialog.destroy()
                    return
                self.config.discord_token = new_token
                self.config.save()
                if self.discord and self.discord.is_running:
                    self.discord.shutdown()
                    self.discord = None
                self._start_discord(new_token)
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _change_token(self, *_args) -> None:
        self._show_token_dialog()

    def _open_settings(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Settings", transient_for=self, modal=True)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        content.set_size_request(480, 640)

        section = Gtk.Label(label="Discord message reader")
        section.add_css_class("heading")
        section.set_xalign(0)
        content.append(section)
        note = Gtk.Label(label="Chọn text channel mà bot được phép đọc. Mặc định không đọc channel nào.")
        note.set_wrap(True)
        note.set_xalign(0)
        note.add_css_class("dim-label")
        content.append(note)

        language_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        language_row.append(Gtk.Label(label="Ngôn ngữ mặc định cho tin nhắn Discord"))
        lang = Gtk.DropDown.new_from_strings(["Tiếng Việt", "English"])
        lang.set_selected(1 if self.config.discord_incoming_language == "en" else 0)
        lang.set_hexpand(True)
        language_row.append(lang)
        content.append(language_row)

        channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        channel_scroll = Gtk.ScrolledWindow()
        channel_scroll.set_min_content_height(150)
        channel_scroll.set_child(channel_box)
        self._settings_checks = {}
        guild = self.guilds.get(self.config.guild_id) if self.config.guild_id else None
        if guild:
            for channel in guild["text_channels"]:
                check = Gtk.CheckButton(label=f"#{channel['name']}")
                check.set_active(channel["id"] in self.config.read_text_channel_ids)
                self._settings_checks[channel["id"]] = check
                channel_box.append(check)
        else:
            channel_box.append(Gtk.Label(label="Kết nối bot và chọn server trước."))
        content.append(channel_scroll)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        tuning = Gtk.Label(label="Voice tuning")
        tuning.add_css_class("heading")
        tuning.set_xalign(0)
        content.append(tuning)
        speed_row, speed_scale = self._make_scale_row("Speed", 0.5, 2.0, self.config.tts_speed, "×")
        volume_row, volume_scale = self._make_scale_row("Volume", 0.1, 2.0, self.config.tts_volume, "×")
        content.append(speed_row)
        content.append(speed_scale)
        content.append(volume_row)
        content.append(volume_scale)
        info = Gtk.Label(label="Pitch và lựa chọn voice/model sẽ được mở rộng ở phần Voice settings. Model Piper cần tải một lần và sau đó chạy offline.")
        info.set_wrap(True)
        info.set_xalign(0)
        info.add_css_class("dim-label")
        content.append(info)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        preview_section = Gtk.Label(label="TTS preview")
        preview_section.add_css_class("heading")
        preview_section.set_xalign(0)
        content.append(preview_section)
        preview_note = Gtk.Label(
            label="Tạo giọng nói bằng Piper rồi phát trực tiếp trên máy. "
                  "Bước này không gửi âm thanh tới Discord."
        )
        preview_note.set_wrap(True)
        preview_note.set_xalign(0)
        preview_note.add_css_class("dim-label")
        content.append(preview_note)

        preview_text = Gtk.Entry()
        preview_text.set_placeholder_text("Nhập câu để nghe thử…")
        preview_text.set_text("Xin chào! Đây là giọng đọc thử.")
        content.append(preview_text)

        preview_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        preview_language = Gtk.DropDown.new_from_strings(["Tiếng Việt", "English"])
        preview_language.set_selected(0)
        preview_language.set_hexpand(True)
        preview_button = Gtk.Button(label="Nghe Piper")
        preview_button.set_icon_name("audio-volume-high-symbolic")
        preview_button.add_css_class("suggested-action")
        preview_stop_button = Gtk.Button(label="Dừng")
        preview_stop_button.set_icon_name("media-playback-stop-symbolic")
        preview_stop_button.set_sensitive(False)
        preview_controls.append(preview_language)
        preview_controls.append(preview_button)
        preview_controls.append(preview_stop_button)
        content.append(preview_controls)

        preview_status = Gtk.Label(label="Chọn ngôn ngữ và nhấn Nghe thử.")
        preview_status.set_wrap(True)
        preview_status.set_xalign(0)
        preview_status.add_css_class("dim-label")
        content.append(preview_status)

        preview_button.connect(
            "clicked", self._preview_tts,
            preview_text, preview_language, speed_scale, volume_scale,
            preview_status, preview_stop_button,
        )
        preview_stop_button.connect(
            "clicked", self._stop_tts_preview_from_settings, preview_status,
        )

        download_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        download_vi = Gtk.Button(label="Tải voice VI")
        download_en = Gtk.Button(label="Tải voice EN")
        download_status = Gtk.Label(label="")
        download_status.set_wrap(True)
        download_status.add_css_class("dim-label")
        download_vi.connect("clicked", self._download_voice, "vi", download_status)
        download_en.connect("clicked", self._download_voice, "en", download_status)
        download_box.append(download_vi)
        download_box.append(download_en)
        content.append(download_box)
        content.append(download_status)

        def save_settings(_dialog, _response):
            self._stop_tts_preview()
            selected_channels = [channel_id for channel_id, check in self._settings_checks.items() if check.get_active()]
            self.config.discord_incoming_language = "en" if lang.get_selected() == 1 else "vi"
            self.config.read_text_channel_ids = selected_channels
            self.config.tts_speed = speed_scale.get_value()
            self.config.tts_volume = volume_scale.get_value()
            self.config.save()
            if self.discord:
                self.discord.set_read_channels(selected_channels, self.config.discord_incoming_language)
            dialog.destroy()
        dialog.connect("response", save_settings)
        dialog.present()

    def _make_scale_row(self, label: str, minimum: float, maximum: float, value: float, suffix: str):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=label)
        title.set_xalign(0)
        title.set_hexpand(True)
        value_label = Gtk.Label(label=f"{value:.2f}{suffix}")
        row.append(title)
        row.append(value_label)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, 0.05)
        scale.set_value(value)
        scale.set_draw_value(False)
        scale.connect("value-changed", lambda widget: value_label.set_text(f"{widget.get_value():.2f}{suffix}"))
        return row, scale

    def _preview_tts(
        self,
        button: Gtk.Button,
        text_entry: Gtk.Entry,
        language_dropdown: Gtk.DropDown,
        speed_scale: Gtk.Scale,
        volume_scale: Gtk.Scale,
        status_label: Gtk.Label,
        stop_button: Gtk.Button,
    ) -> None:
        text = text_entry.get_text().strip()
        if not text:
            status_label.set_text("Hãy nhập nội dung cần nghe thử.")
            return

        # A preview only synthesizes and plays a local WAV; it never contacts Discord.
        self._stop_tts_preview()
        generation = self._preview_generation
        language = "en" if language_dropdown.get_selected() == 1 else "vi"
        speed = speed_scale.get_value()
        volume = volume_scale.get_value()
        self._preview_button = button
        self._preview_stop_button = stop_button
        self._preview_status = status_label
        stop_button.set_sensitive(True)
        self._preview_status.set_text(
            f"Đang tổng hợp bằng model {self.engine.model_id(language)}…"
        )
        button.set_sensitive(False)

        def worker() -> None:
            wav_path: Path | None = None
            try:
                wav_path = self.engine.synthesize(text, language, speed, volume)
                logger.info(
                    "TTS preview synthesized: generation=%d language=%s model=%s "
                    "wav=%s size=%d",
                    generation, language, self.engine.model_id(language), wav_path,
                    wav_path.stat().st_size if wav_path.exists() else -1,
                )
                GLib.idle_add(self._start_tts_preview, generation, wav_path)
            except Exception as exc:
                logger.exception("TTS preview synthesis failed (generation=%d)", generation)
                if wav_path:
                    wav_path.unlink(missing_ok=True)
                GLib.idle_add(self._tts_preview_failed, generation, str(exc))

        threading.Thread(target=worker, name="tts-preview", daemon=True).start()

    def _start_tts_preview(self, generation: int, wav_path: Path) -> bool:
        if generation != self._preview_generation or self._preview_button is None:
            wav_path.unlink(missing_ok=True)
            return GLib.SOURCE_REMOVE

        try:
            media = Gtk.MediaFile.new_for_filename(str(wav_path))
            media.set_loop(False)
            media.connect("notify::prepared", self._on_tts_preview_prepared, generation)
            media.connect("notify::error", self._on_tts_preview_error, generation)
            media.connect("notify::ended", self._on_tts_preview_ended, generation)
            self._preview_media = media
            self._preview_path = wav_path
            media.play()
            self._preview_status.set_text(
                "Đã tạo WAV. Đang phát trực tiếp qua thiết bị âm thanh của máy…"
            )
            logger.info(
                "TTS preview playback started: generation=%d path=%s",
                generation, wav_path,
            )
        except Exception as exc:
            logger.exception("Could not start local TTS preview (generation=%d)", generation)
            self._finish_tts_preview(generation, f"Không phát được preview: {exc}")
        return GLib.SOURCE_REMOVE

    def _on_tts_preview_prepared(self, media, _pspec, generation: int) -> None:
        if generation != self._preview_generation or media is not self._preview_media:
            return
        error = media.get_error()
        if error is not None:
            self._finish_tts_preview(generation, f"Lỗi audio backend: {error}")
        elif not media.has_audio():
            self._finish_tts_preview(
                generation, "File WAV đã tạo nhưng GTK không nhận diện được track âm thanh."
            )
        else:
            self._preview_status.set_text(
                "Model đã tạo WAV có audio; đang thử phát qua âm thanh hệ thống."
            )

    def _on_tts_preview_error(self, media, _pspec, generation: int) -> None:
        if generation != self._preview_generation or media is not self._preview_media:
            return
        error = media.get_error()
        if error is not None:
            logger.error("Local TTS preview playback failed: %s", error)
            self._finish_tts_preview(generation, f"Không phát được preview: {error}")

    def _on_tts_preview_ended(self, media, _pspec, generation: int) -> None:
        if (
            generation == self._preview_generation
            and media is self._preview_media
            and media.get_ended()
        ):
            self._finish_tts_preview(generation, "Nghe thử hoàn tất.")

    def _finish_tts_preview(self, generation: int, message: str) -> None:
        if generation != self._preview_generation:
            return
        # Invalidate callbacks before clearing the stream, which can emit notifications.
        self._preview_generation += 1
        media, self._preview_media = self._preview_media, None
        path, self._preview_path = self._preview_path, None
        button, self._preview_button = self._preview_button, None
        stop_button, self._preview_stop_button = self._preview_stop_button, None
        if media is not None:
            try:
                media.pause()
                media.clear()
            except Exception:
                logger.exception("Failed to release local TTS preview media")
        if path is not None:
            path.unlink(missing_ok=True)
        if button is not None:
            button.set_sensitive(True)
        if stop_button is not None:
            stop_button.set_sensitive(False)
        if self._preview_status is not None:
            self._preview_status.set_text(message)
        self._preview_status = None

    def _tts_preview_failed(self, generation: int, message: str) -> bool:
        if generation != self._preview_generation:
            return GLib.SOURCE_REMOVE
        self._preview_generation += 1
        button, self._preview_button = self._preview_button, None
        stop_button, self._preview_stop_button = self._preview_stop_button, None
        if button is not None:
            button.set_sensitive(True)
        if stop_button is not None:
            stop_button.set_sensitive(False)
        if self._preview_status is not None:
            self._preview_status.set_text(f"Lỗi tổng hợp TTS: {message}")
        self._preview_status = None
        return GLib.SOURCE_REMOVE

    def _stop_tts_preview(self) -> None:
        self._preview_generation += 1
        media, self._preview_media = self._preview_media, None
        path, self._preview_path = self._preview_path, None
        button, self._preview_button = self._preview_button, None
        stop_button, self._preview_stop_button = self._preview_stop_button, None
        if media is not None:
            try:
                media.pause()
                media.clear()
            except Exception:
                logger.exception("Failed to stop local TTS preview")
        if path is not None:
            path.unlink(missing_ok=True)
        if button is not None:
            button.set_sensitive(True)
        if stop_button is not None:
            stop_button.set_sensitive(False)
        self._preview_status = None

    def _stop_tts_preview_from_settings(self, _button, status_label: Gtk.Label) -> None:
        self._stop_tts_preview()
        status_label.set_text("Đã dừng nghe thử.")

    def _download_voice(self, _button, language: str, status_label: Gtk.Label) -> None:
        status_label.set_text(f"Đang tải model {language.upper()}…")

        def worker():
            try:
                self.engine.download_voice(language)
                message = f"Đã cài voice {language.upper()}. Có thể sử dụng offline."
                ok = True
            except Exception as exc:
                message = str(exc)
                ok = False
            GLib.idle_add(status_label.set_text, message)
            if ok:
                GLib.idle_add(self.footer_status.set_text, message)
        threading.Thread(target=worker, daemon=True).start()

    def _notify(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.app.send_notification(None, notification)

    def _on_close_request(self, *_args):
        # The application stays alive; StatusNotifierItem and launcher can reopen it.
        self.set_visible(False)
        return True
