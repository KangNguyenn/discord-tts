from __future__ import annotations

from datetime import datetime
from pathlib import Path
import logging
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from discord_tts.config import APP_NAME, AppConfig, VoiceProfile, VOICE_DIR
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

        self.set_size_request(560, 600)
        self._load_css()
        self._build_ui()
        self._load_history()
        self._load_quick_chat()
        # Set the initial geometry after all content is populated. Long
        # content is constrained by the scroll areas below, not by the window.
        self.set_default_size(760, 760)
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
        self.status_text.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_text.set_wrap(True)
        self.status_text.set_wrap_mode(Pango.WrapMode.CHAR)
        if hasattr(self.status_text, "set_natural_wrap_mode"):
            self.status_text.set_natural_wrap_mode(Pango.WrapMode.CHAR)
        self.status_text.set_lines(1)
        self.status_text.set_max_width_chars(40)
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
        self.clear_history_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.clear_history_button.add_css_class("flat")
        self.clear_history_button.set_tooltip_text("Xóa toàn bộ lịch sử Conversation")
        self.clear_history_button.connect("clicked", self._confirm_clear_history)
        chat_header.append(self.clear_history_button)
        root.append(chat_header)

        self.chat_list = Gtk.ListBox()
        self.chat_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chat_list.add_css_class("chat-list")
        self.chat_scroll = Gtk.ScrolledWindow()
        self.chat_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Keep chat history inside its viewport instead of propagating the
        # natural height of every message to the main window.
        self.chat_scroll.set_propagate_natural_height(False)
        self.chat_scroll.set_propagate_natural_width(False)
        self.chat_scroll.set_hexpand(True)
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
        self.quick_box.set_min_children_per_line(1)
        self.quick_box.set_row_spacing(5)
        self.quick_box.set_column_spacing(5)
        # Keep each shortcut at its natural one-line size. Homogeneous FlowBox
        # cells can otherwise stretch short rows to fill the whole viewport.
        self.quick_box.set_homogeneous(False)
        self.quick_box.set_hexpand(True)

        # Reserve a stable-height area for shortcuts. Extra rows scroll here
        # rather than increasing the main window's natural height.
        self.quick_scroll = Gtk.ScrolledWindow()
        self.quick_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.quick_scroll.set_propagate_natural_height(False)
        self.quick_scroll.set_propagate_natural_width(False)
        self.quick_scroll.set_min_content_height(88)
        self.quick_scroll.set_max_content_height(88)
        self.quick_scroll.set_hexpand(True)
        self.quick_scroll.set_vexpand(False)
        self.quick_scroll.set_child(self.quick_box)
        self.quick_scroll.add_css_class("quick-chat-scroll")
        root.append(self.quick_scroll)

        # Chat composer: Enter sends; Shift+Enter inserts a newline.
        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        composer.add_css_class("composer")
        root.append(composer)
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        lang_label = Gtk.Label(label="Voice")
        lang_label.add_css_class("dim-label")
        top_row.append(lang_label)
        self.app_voice_button = self._make_voice_button(
            self.config.direct_chat.model_id
        )
        self.app_voice_button.set_tooltip_text("Chọn model, speaker, speed và volume cho Direct Chat")
        self.app_voice_button.connect(
            "clicked",
            lambda button: self._open_voice_picker(
                button, self.config.direct_chat,
                lambda profile: self._save_profile(self.config.direct_chat, profile),
            ),
        )
        top_row.append(self.app_voice_button)
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
        input_scroll.set_max_content_height(66)
        input_scroll.set_propagate_natural_height(False)
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
        self.send_button = Gtk.Button.new_from_icon_name("mail-send-symbolic")
        self.send_button.add_css_class("suggested-action")
        self.send_button.add_css_class("circular")
        self.send_button.set_valign(Gtk.Align.END)
        self.send_button.set_tooltip_text("Send and speak (Enter)")
        self.send_button.set_sensitive(False)
        self.send_button.connect("clicked", self._send_message)
        text_row.append(self.send_button)
        composer.append(text_row)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.footer_status = Gtk.Label(label="Voice model được lưu offline · Enter để gửi · Shift+Enter xuống dòng")
        self.footer_status.add_css_class("dim-label")
        self.footer_status.set_xalign(0)
        self.footer_status.set_hexpand(True)
        self.footer_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.footer_status.set_wrap(True)
        self.footer_status.set_wrap_mode(Pango.WrapMode.CHAR)
        if hasattr(self.footer_status, "set_natural_wrap_mode"):
            self.footer_status.set_natural_wrap_mode(Pango.WrapMode.CHAR)
        self.footer_status.set_lines(1)
        self.footer_status.set_max_width_chars(64)
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

    def _send_message(self, *_args) -> None:
        text = self._input_text().strip()
        if not text:
            return
        profile = self.config.direct_chat
        model_id = self.engine.model_id(profile.model_id)
        language = self.engine.language_code(model_id)
        self._append_bubble(text, "outgoing", language, "You")
        self.database.add_history("outgoing", "app", language, text)
        self.input_view.get_buffer().set_text("")
        if not self.discord:
            self._append_bubble(
                "Discord bot chưa được kết nối.", "system", language, "System"
            )
            return
        self.discord.speak(
            text, model_id, profile.speaker_id, profile.speed, profile.volume, "app"
        )
        self.input_view.grab_focus()


    def _append_bubble(self, text: str, direction: str, language: str, author: str, timestamp: str | None = None) -> None:
        row = Gtk.ListBoxRow()
        row.add_css_class("chat-row")
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        line.set_hexpand(True)
        line.set_halign(Gtk.Align.FILL)
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bubble.add_css_class("chat-bubble")
        bubble.add_css_class(direction)
        bubble.set_hexpand(False)
        bubble.set_halign(
            Gtk.Align.END if direction == "outgoing" else Gtk.Align.START
        )
        message = Gtk.Label(label=text)
        message.set_wrap(True)
        message.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # WORD_CHAR wraps during allocation, but GTK may still measure a
        # long unbroken token as one word for the natural width request.
        # CHAR natural wrapping keeps that measurement bounded as well.
        if hasattr(message, "set_natural_wrap_mode"):
            message.set_natural_wrap_mode(Pango.WrapMode.CHAR)
        message.set_xalign(0)
        message.set_selectable(True)
        message.set_max_width_chars(48)
        bubble.append(message)
        try:
            time = datetime.fromisoformat(timestamp).astimezone().strftime("%H:%M") if timestamp else datetime.now().strftime("%H:%M")
        except (TypeError, ValueError):
            time = datetime.now().strftime("%H:%M")
        language_tag = (language or "unknown").replace("-", "_").split("_", 1)[0].upper()
        meta = Gtk.Label(label=f"{author} · {language_tag} · {time}")
        meta.set_xalign(1 if direction == "outgoing" else 0)
        meta.set_ellipsize(Pango.EllipsizeMode.END)
        meta.set_wrap(True)
        meta.set_wrap_mode(Pango.WrapMode.CHAR)
        if hasattr(meta, "set_natural_wrap_mode"):
            meta.set_natural_wrap_mode(Pango.WrapMode.CHAR)
        meta.set_lines(1)
        meta.set_max_width_chars(32)
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

    def _confirm_clear_history(self, *_args) -> None:
        dialog = Gtk.Dialog(
            title="Xóa lịch sử Conversation", transient_for=self, modal=True
        )
        dialog.add_button("Hủy", Gtk.ResponseType.CANCEL)
        dialog.add_button("Xóa lịch sử", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)
        message = Gtk.Label(
            label="Xóa toàn bộ lịch sử Conversation?\\nHành động này không thể hoàn tác."
        )
        message.set_wrap(True)
        message.set_xalign(0)
        content.append(message)

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK:
                self.database.clear_history()
                child = self.chat_list.get_first_child()
                while child is not None:
                    next_child = child.get_next_sibling()
                    self.chat_list.remove(child)
                    child = next_child
                self.footer_status.set_text("Đã xóa lịch sử Conversation.")
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _load_quick_chat(self) -> None:
        child = self.quick_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.quick_box.remove(child)
            child = next_child

        for row in self.database.quick_messages():
            item = dict(row)
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
            chip.add_css_class("quick-chip")
            chip.set_size_request(286, 40)
            chip.set_hexpand(False)
            chip.set_vexpand(False)
            chip.set_halign(Gtk.Align.START)
            chip.set_valign(Gtk.Align.CENTER)

            item_button = Gtk.Button()
            item_button.add_css_class("quick-button")
            item_button.set_size_request(112, 34)
            item_button.set_hexpand(False)
            item_button.set_vexpand(False)
            item_button.set_halign(Gtk.Align.START)
            item_button.set_valign(Gtk.Align.CENTER)
            item_title = Gtk.Label(label=item["title"])
            item_title.set_ellipsize(Pango.EllipsizeMode.END)
            item_title.set_wrap(False)
            item_title.set_lines(1)
            item_title.set_width_chars(12)
            item_title.set_max_width_chars(12)
            item_button.set_child(item_title)
            item_button.set_tooltip_text(f'{item["title"]}\n{item["text"]}')
            item_button.connect("clicked", self._on_quick_clicked, item)
            chip.append(item_button)

            model = self.engine.get_model(item["model_id"])
            model_name = model.display_name if model else f"Missing: {item['model_id']}"
            speaker_name = (
                model.speaker_name(int(item["speaker_id"]))
                if model and model.num_speakers > 1 else "Speaker 0"
            )
            profile_label = Gtk.Label(
                label=f"{model_name} · {speaker_name} · "
                      f"{float(item['speed']):.2f}× · {float(item['volume']):.2f}×"
            )
            profile_label.add_css_class("dim-label")
            profile_label.set_ellipsize(Pango.EllipsizeMode.END)
            profile_label.set_wrap(False)
            profile_label.set_lines(1)
            profile_label.set_valign(Gtk.Align.CENTER)
            profile_label.set_vexpand(False)
            profile_label.set_width_chars(12)
            profile_label.set_max_width_chars(12)
            profile_label.set_tooltip_text(
                f"{model_name} · {speaker_name} · speed {item['speed']} · volume {item['volume']}"
            )
            chip.append(profile_label)

            edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
            edit_button.add_css_class("flat")
            edit_button.set_size_request(34, 34)
            edit_button.set_vexpand(False)
            edit_button.set_valign(Gtk.Align.CENTER)
            edit_button.set_tooltip_text(f"Sửa Quick Chat: {item['title']}")
            edit_button.connect("clicked", self._edit_quick_chat, item)
            chip.append(edit_button)

            delete_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
            delete_button.add_css_class("flat")
            delete_button.set_size_request(34, 34)
            delete_button.set_vexpand(False)
            delete_button.set_valign(Gtk.Align.CENTER)
            delete_button.set_tooltip_text(f"Xóa Quick Chat: {item['title']}")
            delete_button.connect(
                "clicked", self._delete_quick_chat, item["id"], item["title"]
            )
            chip.append(delete_button)
            self.quick_box.append(chip)

    def _on_quick_clicked(self, _button, item: dict) -> None:
        text = item["text"].strip()
        if not text:
            return
        model_id = self.engine.model_id(item["model_id"])
        language = self.engine.language_code(model_id)
        profile = VoiceProfile(
            model_id=model_id,
            speaker_id=int(item["speaker_id"]),
            speed=float(item["speed"]),
            volume=float(item["volume"]),
        )
        self._append_bubble(text, "outgoing", language, item["title"])
        self.database.add_history("outgoing", "quick", language, text)
        if not self.discord:
            self._append_bubble(
                "Discord bot chưa được kết nối.", "system", language, "System"
            )
            return
        # Quick Chat speaks with its own profile and never changes Direct Chat.
        self.discord.speak(
            text, profile.model_id, profile.speaker_id,
            profile.speed, profile.volume, "quick",
        )

    def _delete_quick_chat(self, _button, quick_id: int, title: str) -> None:
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
        self._open_quick_chat_dialog()

    def _edit_quick_chat(self, _button, item: dict) -> None:
        self._open_quick_chat_dialog(item)

    def _open_quick_chat_dialog(self, item: dict | None = None) -> None:
        editing = item is not None
        dialog = Gtk.Dialog(
            title="Chỉnh sửa Quick Chat" if editing else "Tạo Quick Chat",
            transient_for=self,
            modal=True,
        )
        dialog.add_button("Hủy", Gtk.ResponseType.CANCEL)
        dialog.add_button("Lưu thay đổi" if editing else "Tạo Quick Chat", Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(9)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(14)
        content.set_margin_end(14)

        title_label = Gtk.Label(label="Tên Quick Chat")
        title_label.set_xalign(0)
        title_entry = Gtk.Entry(placeholder_text="Ví dụ: Hello")
        title_entry.set_text(item["title"] if item else "")
        content.append(title_label)
        content.append(title_entry)

        text_label = Gtk.Label(label="Nội dung nói")
        text_label.set_xalign(0)
        text_view = Gtk.TextView()
        text_view.add_css_class("quick-chat-editor")
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_size_request(-1, 96)
        if item:
            text_view.get_buffer().set_text(item["text"])
        text_scroll = Gtk.ScrolledWindow()
        text_scroll.set_min_content_height(96)
        text_scroll.set_child(text_view)
        content.append(text_label)
        content.append(text_scroll)

        profile = (
            VoiceProfile(
                model_id=item["model_id"],
                speaker_id=int(item["speaker_id"]),
                speed=float(item["speed"]),
                volume=float(item["volume"]),
            )
            if item else self.config.direct_chat.copy()
        )
        model_label = Gtk.Label(label="Voice Model")
        model_label.set_xalign(0)
        model_button = self._make_voice_button(profile.model_id)
        model_button.set_hexpand(True)
        model_button.set_tooltip_text("Chọn model, speaker, speed và volume cho Quick Chat")
        model_button.connect(
            "clicked",
            lambda button: self._open_voice_picker(
                button, profile,
                lambda updated: self._copy_profile_to_button(profile, updated, model_button),
            ),
        )
        content.append(model_label)
        content.append(model_button)
        profile_note = Gtk.Label(
            label="Mặc định sao chép cấu hình Direct Chat tại thời điểm tạo. "
                  "Sau khi lưu, profile này hoạt động độc lập."
        )
        profile_note.set_wrap(True)
        profile_note.set_xalign(0)
        profile_note.add_css_class("dim-label")
        content.append(profile_note)

        def on_response(_dialog, response):
            if response == Gtk.ResponseType.OK:
                title = title_entry.get_text().strip()
                start, end = text_view.get_buffer().get_bounds()
                text = text_view.get_buffer().get_text(start, end, True).strip()
                if not title or not text:
                    self._append_bubble(
                        "Tên và nội dung Quick Chat không được để trống.",
                        "system", "vi", "System",
                    )
                else:
                    if editing:
                        self.database.update_quick_message(
                            int(item["id"]), title, text, profile.model_id,
                            profile.speaker_id, profile.speed, profile.volume,
                        )
                    else:
                        self.database.add_quick_message(
                            title, text, profile.model_id,
                            profile.speaker_id, profile.speed, profile.volume,
                        )
                    self._load_quick_chat()
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _copy_profile_to_button(
        self, target: VoiceProfile, updated: VoiceProfile, button: Gtk.Button
    ) -> None:
        target.model_id = updated.model_id
        target.speaker_id = updated.speaker_id
        target.speed = updated.speed
        target.volume = updated.volume
        self._set_voice_button_label(button, target.model_id)
        button.set_tooltip_text(self._profile_tooltip(target))

    def _profile_tooltip(self, profile: VoiceProfile) -> str:
        model = self.engine.get_model(profile.model_id)
        if not model:
            return f"Missing model: {profile.model_id}"
        speaker = model.speaker_name(profile.speaker_id)
        return (
            f"{model.display_name} · {speaker} · "
            f"speed {profile.speed:.2f}× · volume {profile.volume:.2f}×"
        )

    def _save_profile(self, target: VoiceProfile, updated: VoiceProfile) -> None:
        target.model_id = updated.model_id
        target.speaker_id = updated.speaker_id
        target.speed = updated.speed
        target.volume = updated.volume
        self.config.save()
        if target is self.config.direct_chat and hasattr(self, "app_voice_button"):
            self._set_voice_button_label(
                self.app_voice_button, self.config.direct_chat.model_id
            )
            self.app_voice_button.set_tooltip_text(
                self._profile_tooltip(self.config.direct_chat)
            )

    def _save_profile_to_button(
        self, target: VoiceProfile, updated: VoiceProfile, button: Gtk.Button
    ) -> None:
        self._copy_profile_to_button(target, updated, button)
        self.config.save()
        button.set_tooltip_text(self._profile_tooltip(target))


    @staticmethod
    def _display_text(text: str, limit: int = 40) -> str:
        value = str(text or "")
        if len(value) <= limit:
            return value
        return value[:limit - 1].rstrip() + "…"

    def _set_status(self, text: str, state: str = "neutral") -> None:
        self.status_text.set_text(self._display_text(text, 40))
        self.status_text.set_tooltip_text(text)
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
            log_message = str(payload.get("message", ""))
            self.footer_status.set_text(self._display_text(log_message, 64))
            self.footer_status.set_tooltip_text(log_message)

    def _update_guilds(self, guilds: list[dict], selected_id: int | None) -> None:
        self._loading_dropdowns = True
        self.guilds = {item["id"]: item for item in guilds}
        names = [self._display_text(item["name"], 36) for item in guilds] or ["Bot chưa có server"]
        self.guild_dropdown.set_model(Gtk.StringList.new(names))
        ids = list(self.guilds.keys())
        if selected_id in ids:
            self.guild_dropdown.set_selected(ids.index(selected_id))
        elif ids:
            self.guild_dropdown.set_selected(0)
            selected_id = ids[0]
        self._loading_dropdowns = False
        selected_guild = self.guilds.get(selected_id) if selected_id is not None else None
        self.guild_dropdown.set_tooltip_text(
            selected_guild["name"] if selected_guild else "Discord server"
        )
        self._update_voice_channels(selected_id)

    def _update_voice_channels(self, guild_id: int | None) -> None:
        item = self.guilds.get(guild_id) if guild_id else None
        self.voice_channels = item["voice_channels"] if item else []
        names = [self._display_text(channel["name"], 36) for channel in self.voice_channels] or ["Không có voice channel"]
        self.channel_dropdown.set_model(Gtk.StringList.new(names))
        selected = next(
            (index for index, channel in enumerate(self.voice_channels)
             if channel["id"] == self.config.voice_channel_id),
            0,
        )
        self.channel_dropdown.set_selected(selected)
        selected_channel = (
            self.voice_channels[selected]
            if selected < len(self.voice_channels) else None
        )
        self.channel_dropdown.set_tooltip_text(
            selected_channel["name"] if selected_channel else "Voice channel"
        )

    def _on_guild_selected(self, dropdown, _pspec) -> None:
        if self._loading_dropdowns:
            return
        ids = list(self.guilds.keys())
        index = dropdown.get_selected()
        if index >= len(ids):
            return
        guild_id = ids[index]
        self.guild_dropdown.set_tooltip_text(self.guilds[guild_id]["name"])
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
            self._open_settings()
            return
        self._start_discord(token)

    def _start_discord(self, token: str) -> None:
        if self.discord and self.discord.is_running:
            return
        if not self.discord:
            self.discord = DiscordService(
                self.config, self.engine, self._post_service_event
            )
        self.connect_button.set_sensitive(False)
        self._set_status("Đang kết nối Discord…", "warning")
        self.discord.start(token)

    def _post_service_event(self, event: str, payload: dict) -> None:
        GLib.idle_add(self.handle_service_event, event, payload)


    def _open_settings(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Settings", transient_for=self, modal=True)
        dialog.set_default_size(650, 720)
        dialog.add_button("Hủy", Gtk.ResponseType.CANCEL)
        dialog.add_button("Lưu cài đặt", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(11)
        content.set_margin_top(14)
        content.set_margin_bottom(14)
        content.set_margin_start(16)
        content.set_margin_end(16)

        token_section = Gtk.Label(label="Discord Bot")
        token_section.add_css_class("heading")
        token_section.set_xalign(0)
        content.append(token_section)
        token_note = Gtk.Label(
            label="Bot Token được lưu cục bộ trong config.json với quyền truy cập chỉ dành cho tài khoản hiện tại."
        )
        token_note.set_wrap(True)
        token_note.set_xalign(0)
        token_note.add_css_class("dim-label")
        content.append(token_note)

        token_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        token_entry = Gtk.Entry()
        token_entry.set_placeholder_text("Discord Bot Token")
        token_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        token_entry.set_visibility(False)
        token_entry.set_hexpand(True)
        token_entry.set_text(self.config.discord_token)
        token_row.append(token_entry)
        show_token = Gtk.ToggleButton(label="Hiện")
        show_token.connect(
            "toggled",
            lambda button: (
                token_entry.set_visibility(button.get_active()),
                button.set_label("Ẩn" if button.get_active() else "Hiện"),
            ),
        )
        token_row.append(show_token)
        content.append(token_row)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        auto_join_section = Gtk.Label(label="Tự động tham gia")
        auto_join_section.add_css_class("heading")
        auto_join_section.set_xalign(0)
        content.append(auto_join_section)

        auto_join_note = Gtk.Label(
            label="Khi bot kết nối, ứng dụng sẽ tìm voice channel mà User ID này "
                  "đang ở và tự tham gia. Để trống để tắt tính năng."
        )
        auto_join_note.set_wrap(True)
        auto_join_note.set_xalign(0)
        auto_join_note.add_css_class("dim-label")
        content.append(auto_join_note)

        auto_join_user_id_label = Gtk.Label(label="Discord User ID")
        auto_join_user_id_label.set_xalign(0)
        content.append(auto_join_user_id_label)
        auto_join_user_id_entry = Gtk.Entry(
            placeholder_text="Ví dụ: 123456789012345678"
        )
        auto_join_user_id_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        auto_join_user_id_entry.set_text(
            str(self.config.auto_join_user_id)
            if self.config.auto_join_user_id is not None else ""
        )
        content.append(auto_join_user_id_entry)

        auto_join_error_label = Gtk.Label()
        auto_join_error_label.add_css_class("error")
        auto_join_error_label.set_xalign(0)
        auto_join_error_label.set_visible(False)
        content.append(auto_join_error_label)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        activity_section = Gtk.Label(label="Discord Activity")
        activity_section.add_css_class("heading")
        activity_section.set_xalign(0)
        content.append(activity_section)

        status_values = ["online", "idle", "dnd", "invisible"]
        status_labels = ["Online", "Idle", "Do Not Disturb", "Invisible"]
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_label = Gtk.Label(label="Bot status")
        status_label.set_xalign(0)
        status_label.set_hexpand(True)
        status_dropdown = Gtk.DropDown.new_from_strings(status_labels)
        status_dropdown.set_selected(
            status_values.index(self.config.discord_status)
            if self.config.discord_status in status_values else 0
        )
        status_row.append(status_label)
        status_row.append(status_dropdown)
        content.append(status_row)

        activity_values = ["playing", "listening", "watching", "competing", "streaming"]
        activity_labels = ["Playing", "Listening", "Watching", "Competing", "Streaming"]
        type_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        type_label = Gtk.Label(label="Activity type")
        type_label.set_xalign(0)
        type_label.set_hexpand(True)
        activity_dropdown = Gtk.DropDown.new_from_strings(activity_labels)
        activity_dropdown.set_selected(
            activity_values.index(self.config.discord_activity_type)
            if self.config.discord_activity_type in activity_values else 1
        )
        type_row.append(type_label)
        type_row.append(activity_dropdown)
        content.append(type_row)

        name_label = Gtk.Label(label="Activity name")
        name_label.set_xalign(0)
        activity_name_entry = Gtk.Entry(placeholder_text="Ví dụ: to messages")
        activity_name_entry.set_text(self.config.discord_activity_name)
        content.append(name_label)
        content.append(activity_name_entry)

        stream_url_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        stream_url_label = Gtk.Label(label="Streaming URL")
        stream_url_label.set_xalign(0)
        stream_url_entry = Gtk.Entry(placeholder_text="https://twitch.tv/...")
        stream_url_entry.set_text(self.config.discord_streaming_url)
        stream_url_row.append(stream_url_label)
        stream_url_row.append(stream_url_entry)
        stream_url_row.set_visible(self.config.discord_activity_type == "streaming")
        content.append(stream_url_row)

        def on_activity_type_changed(dropdown, _pspec):
            stream_url_row.set_visible(
                dropdown.get_selected() == activity_values.index("streaming")
            )
        activity_dropdown.connect("notify::selected", on_activity_type_changed)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        reader_section = Gtk.Label(label="Discord Message Reader")
        reader_section.add_css_class("heading")
        reader_section.set_xalign(0)
        content.append(reader_section)
        reader_note = Gtk.Label(
            label="Chọn voice profile và các text channel mà bot được phép đọc."
        )
        reader_note.set_wrap(True)
        reader_note.set_xalign(0)
        reader_note.add_css_class("dim-label")
        content.append(reader_note)

        reader_voice_button = self._make_voice_button(
            self.config.discord_reader.model_id
        )
        reader_voice_button.set_hexpand(True)
        reader_voice_button.set_tooltip_text(
            self._profile_tooltip(self.config.discord_reader)
        )
        reader_voice_button.connect(
            "clicked",
            lambda button: self._open_voice_picker(
                button, self.config.discord_reader,
                lambda updated: self._save_profile_to_button(
                    self.config.discord_reader, updated, reader_voice_button
                ),
            ),
        )
        content.append(reader_voice_button)

        channels_label = Gtk.Label(label="Allowed text channels")
        channels_label.set_xalign(0)
        content.append(channels_label)
        channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        channel_scroll = Gtk.ScrolledWindow()
        channel_scroll.set_min_content_height(130)
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
            empty = Gtk.Label(label="Kết nối bot và chọn server trước.")
            empty.add_css_class("dim-label")
            channel_box.append(empty)
        content.append(channel_scroll)

        def save_settings(_dialog, response):
            if response != Gtk.ResponseType.OK:
                dialog.destroy()
                return

            raw_auto_join_user_id = auto_join_user_id_entry.get_text().strip()
            if raw_auto_join_user_id and (
                not raw_auto_join_user_id.isascii()
                or not raw_auto_join_user_id.isdigit()
                or len(raw_auto_join_user_id) > 20
                or not 0 < int(raw_auto_join_user_id) < 2**64
            ):
                auto_join_error_label.set_text(
                    "User ID phải là dãy số Discord hợp lệ, không kèm ký tự khác."
                )
                auto_join_error_label.set_visible(True)
                auto_join_user_id_entry.add_css_class("error")
                return

            auto_join_user_id = (
                int(raw_auto_join_user_id) if raw_auto_join_user_id else None
            )
            auto_join_error_label.set_visible(False)
            auto_join_user_id_entry.remove_css_class("error")

            old_token = self.config.discord_token
            old_auto_join_user_id = self.config.auto_join_user_id
            new_token = token_entry.get_text().strip()
            self.config.discord_token = new_token
            self.config.auto_join_user_id = auto_join_user_id
            self.config.discord_status = status_values[
                min(status_dropdown.get_selected(), len(status_values) - 1)
            ]
            self.config.discord_activity_type = activity_values[
                min(activity_dropdown.get_selected(), len(activity_values) - 1)
            ]
            self.config.discord_activity_name = activity_name_entry.get_text().strip()
            self.config.discord_streaming_url = stream_url_entry.get_text().strip()
            selected_channels = (
                [
                    channel_id for channel_id, check in self._settings_checks.items()
                    if check.get_active()
                ]
                if self._settings_checks else list(self.config.read_text_channel_ids)
            )
            self.config.read_text_channel_ids = selected_channels
            self.config.save()

            if self.discord:
                self.discord.set_read_channels(selected_channels)

            token_changed = old_token != new_token
            if token_changed:
                old_service = self.discord
                if old_service:
                    old_service.shutdown()
                self.discord = None
                self.connect_button.set_sensitive(True)
                if new_token:
                    def wait_and_restart():
                        if old_service and old_service.thread:
                            old_service.thread.join(timeout=12)
                        GLib.idle_add(self._start_discord, new_token)
                    threading.Thread(
                        target=wait_and_restart,
                        name="discord-restart",
                        daemon=True,
                    ).start()
            elif self.discord and self.discord.is_running:
                self.discord.update_presence()
                if old_auto_join_user_id != auto_join_user_id:
                    self.discord.auto_join_configured_user()
            elif new_token:
                self._start_discord(new_token)

            dialog.destroy()

        dialog.connect("response", save_settings)
        dialog.present()


    def _voice_button_label(self, model_id: str) -> str:
        model = self.engine.get_model(model_id)
        if model:
            return model.display_name
        return f"Missing model: {Path(model_id).name}" if model_id else "Chọn model"

    def _make_voice_button(self, model_id: str) -> Gtk.Button:
        button = Gtk.Button()
        contents = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=self._voice_button_label(model_id))
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.CHAR)
        if hasattr(label, "set_natural_wrap_mode"):
            label.set_natural_wrap_mode(Pango.WrapMode.CHAR)
        label.set_lines(1)
        label.set_width_chars(18)
        label.set_max_width_chars(24)
        contents.append(label)
        contents.append(Gtk.Image.new_from_icon_name("pan-down-symbolic"))
        button.set_child(contents)
        return button

    def _set_voice_button_label(self, button: Gtk.Button, model_id: str) -> None:
        contents = button.get_child()
        if isinstance(contents, Gtk.Box):
            label = contents.get_first_child()
            if isinstance(label, Gtk.Label):
                label.set_text(self._voice_button_label(model_id))

    def _open_voice_directory(self, *_args) -> None:
        try:
            VOICE_DIR.mkdir(parents=True, exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(VOICE_DIR.resolve().as_uri(), None)
        except Exception as exc:
            logger.exception("Could not open Piper model directory")
            self._append_bubble(
                f"Không mở được thư mục model: {exc}", "system", "vi", "System"
            )

    def _open_voice_picker(
        self, _button: Gtk.Button, profile: VoiceProfile, on_save
    ) -> None:
        models = self.engine.list_models()
        draft = profile.copy()
        dialog = Gtk.Dialog(title="Chọn Voice Model", transient_for=self, modal=True)
        dialog.set_default_size(700, 650)
        header = Gtk.HeaderBar()
        title = Gtk.Label(label="Voice Models")
        title.add_css_class("title")
        header.set_title_widget(title)
        folder_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        folder_button.set_tooltip_text("Mở thư mục model Piper")
        folder_button.connect("clicked", self._open_voice_directory)
        header.pack_end(folder_button)
        dialog.set_titlebar(header)
        dialog.add_button("Hủy", Gtk.ResponseType.CANCEL)
        use_button = dialog.add_button("Lưu cấu hình", Gtk.ResponseType.OK)
        use_button.set_sensitive(bool(models))

        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(14)
        content.set_margin_end(14)

        note = Gtk.Label(
            label="Chọn model, speaker và tinh chỉnh tốc độ/âm lượng. "
                  "Nút Play chỉ phát thử trên máy."
        )
        note.set_wrap(True)
        note.set_xalign(0)
        note.add_css_class("dim-label")
        content.append(note)

        speed_row, speed_scale = self._make_scale_row(
            "Speed", 0.5, 2.0, draft.speed, "×"
        )
        volume_row, volume_scale = self._make_scale_row(
            "Volume", 0.1, 2.0, draft.volume, "×"
        )
        content.append(speed_row)
        content.append(speed_scale)
        content.append(volume_row)
        content.append(volume_scale)

        selected_model = {"id": ""}
        speaker_states: dict[str, dict[str, int]] = {}
        speaker_rows: dict[str, Gtk.Widget] = {}
        preview_status = Gtk.Label(label="Nhấn Play để nghe thử model.")
        preview_status.set_wrap(True)
        preview_status.set_xalign(0)
        preview_status.add_css_class("dim-label")
        preview_stop_button = Gtk.Button.new_from_icon_name(
            "media-playback-stop-symbolic"
        )
        preview_stop_button.set_tooltip_text("Dừng preview")
        preview_stop_button.set_sensitive(False)

        if not models:
            empty = Gtk.Label(
                label="Chưa tìm thấy model hợp lệ. Hãy chép cặp .onnx và "
                      ".onnx.json vào thư mục voices."
            )
            empty.set_wrap(True)
            empty.set_xalign(0)
            empty.set_margin_top(18)
            empty.set_margin_bottom(18)
            content.append(empty)
        else:
            current_info = self.engine.get_model(draft.model_id)
            initial_id = current_info.model_id if current_info else models[0].model_id
            selected_model["id"] = initial_id
            rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            models_by_id = {model.model_id: model for model in models}
            first_radio: Gtk.CheckButton | None = None

            for model in models:
                saved_speaker = (
                    draft.speaker_id if model.model_id == initial_id
                    else model.default_speaker_id
                )
                if not 0 <= int(saved_speaker) < model.num_speakers:
                    saved_speaker = model.default_speaker_id
                speaker_state = {"speaker_id": int(saved_speaker)}
                speaker_states[model.model_id] = speaker_state

                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                card.add_css_class("top-card")
                card.set_margin_start(2)
                card.set_margin_end(2)
                card.set_margin_top(2)
                card.set_margin_bottom(2)

                top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                radio = Gtk.CheckButton()
                if first_radio is not None:
                    radio.set_group(first_radio)
                else:
                    first_radio = radio
                radio.set_active(model.model_id == initial_id)

                details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                details.set_hexpand(True)
                radio.set_hexpand(True)
                radio.set_child(details)
                radio.set_cursor_from_name("pointer")
                radio.set_tooltip_text(f"Chọn model: {model.display_name}")
                name = Gtk.Label(label=model.display_name)
                name.set_xalign(0)
                name.add_css_class("heading")
                details.append(name)

                quality = model.quality.replace("_", " ").title()
                language = Gtk.Label(label=f"{model.language_name} · {quality}")
                language.set_xalign(0)
                language.add_css_class("dim-label")
                details.append(language)

                stats = f"{model.sample_rate:,} Hz" if model.sample_rate else "Sample rate: —"
                stats += f" · {model.num_speakers} speaker(s)"
                metadata = Gtk.Label(label=stats)
                metadata.set_xalign(0)
                metadata.add_css_class("dim-label")
                details.append(metadata)

                play_button = Gtk.Button.new_from_icon_name(
                    "media-playback-start-symbolic"
                )
                play_button.set_tooltip_text("Nghe thử model này")
                play_button.connect(
                    "clicked",
                    lambda button, selected=model, state=speaker_state:
                        self._preview_model(
                            button, selected, state, preview_status,
                            preview_stop_button, speed_scale.get_value(),
                            volume_scale.get_value(),
                        ),
                )
                top_row.append(radio)
                top_row.append(play_button)
                card.append(top_row)

                if model.num_speakers > 1:
                    speaker_row = Gtk.Box(
                        orientation=Gtk.Orientation.HORIZONTAL, spacing=8
                    )
                    speaker_row.set_margin_start(36)
                    speaker_label = Gtk.Label(label="Speaker")
                    speaker_label.add_css_class("dim-label")
                    speaker_row.append(speaker_label)
                    choices = [name for name, _speaker_id in model.speakers]
                    dropdown = Gtk.DropDown.new_from_strings(choices)
                    dropdown.set_hexpand(True)
                    selected_index = next(
                        (
                            index for index, (_speaker_name, speaker_id)
                            in enumerate(model.speakers)
                            if speaker_id == speaker_state["speaker_id"]
                        ),
                        0,
                    )
                    dropdown.set_selected(selected_index)
                    dropdown.connect(
                        "notify::selected",
                        lambda widget, _pspec, m=model, state=speaker_state:
                            state.update(
                                speaker_id=m.speakers[widget.get_selected()][1]
                                if widget.get_selected() < len(m.speakers)
                                else m.default_speaker_id
                            ),
                    )
                    speaker_row.set_visible(model.model_id == initial_id)
                    speaker_rows[model.model_id] = speaker_row
                    card.append(speaker_row)

                def on_radio_toggled(radio_button, model_key=model.model_id):
                    if not radio_button.get_active():
                        return
                    selected_model["id"] = model_key
                    for key, row in speaker_rows.items():
                        row.set_visible(key == model_key)

                radio.connect("toggled", on_radio_toggled)
                rows_box.append(card)
                rows_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_min_content_height(230)
            scroll.set_vexpand(True)
            scroll.set_child(rows_box)
            content.append(scroll)

        preview_controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        preview_status.set_hexpand(True)
        preview_controls.append(preview_status)
        preview_controls.append(preview_stop_button)
        content.append(preview_controls)
        preview_stop_button.connect(
            "clicked", self._stop_tts_preview_from_picker, preview_status
        )

        def on_response(_dialog, response):
            self._stop_tts_preview()
            if response == Gtk.ResponseType.OK and selected_model["id"]:
                model = self.engine.get_model(selected_model["id"])
                if model:
                    draft.model_id = model.model_id
                    draft.speaker_id = speaker_states[model.model_id]["speaker_id"]
                    draft.speed = speed_scale.get_value()
                    draft.volume = volume_scale.get_value()
                    on_save(draft)
                    self.footer_status.set_text(
                        f"Đã lưu voice profile: {model.display_name}"
                    )
            dialog.destroy()

        dialog.connect("response", on_response)
        dialog.present()

    def _make_scale_row(
        self, label: str, minimum: float, maximum: float,
        value: float, suffix: str,
    ):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=label)
        title.set_xalign(0)
        title.set_hexpand(True)
        value_label = Gtk.Label(label=f"{value:.2f}{suffix}")
        row.append(title)
        row.append(value_label)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, minimum, maximum, 0.05
        )
        scale.set_value(value)
        scale.set_draw_value(False)
        scale.add_mark(1.0, Gtk.PositionType.BOTTOM, "1×")

        # Snap to the normal 1× level when the thumb gets within half a step.
        # Keeping the threshold below one full step lets keyboard arrows move
        # away from 1× without getting stuck on the snap point.
        snap_tolerance = scale.get_adjustment().get_step_increment() / 2.0

        def on_value_changed(widget):
            current = widget.get_value()
            if current != 1.0 and abs(current - 1.0) <= snap_tolerance + 1e-9:
                widget.set_value(1.0)
                current = widget.get_value()
            value_label.set_text(f"{current:.2f}{suffix}")

        scale.connect("value-changed", on_value_changed)
        return row, scale

    def _stop_tts_preview_from_picker(
        self, _button, status_label: Gtk.Label
    ) -> None:
        self._stop_tts_preview()
        status_label.set_text("Đã dừng nghe thử.")


    def _preview_text_for_model(self, model) -> str:
        family = model.language_code.lower().replace("-", "_").split("_", 1)[0]
        samples = {
            "vi": "Xin chào! Đây là câu nghe thử cho giọng đọc đang được chọn.",
            "en": "Hello! This is a preview of the selected voice.",
            "de": "Hallo! Dies ist eine kurze Vorschau der ausgewählten Stimme.",
            "fr": "Bonjour ! Ceci est un aperçu de la voix sélectionnée.",
            "es": "¡Hola! Esta es una prueba de la voz seleccionada.",
            "it": "Ciao! Questa è un'anteprima della voce selezionata.",
            "ja": "こんにちは。選択した音声のサンプルです。",
            "zh": "你好，这是所选语音的试听。",
            "ko": "안녕하세요. 선택한 음성의 미리 듣기입니다.",
            "pt": "Olá! Esta é uma prévia da voz selecionada.",
            "ru": "Здравствуйте! Это пример выбранного голоса.",
        }
        return samples.get(family, "Hello! This is a preview of the selected voice.")

    def _preview_model(
        self,
        button: Gtk.Button,
        model,
        speaker_state: dict,
        status_label: Gtk.Label,
        stop_button: Gtk.Button,
        speed: float | None = None,
        volume: float | None = None,
    ) -> None:
        speaker_id = int(speaker_state.get("speaker_id", model.default_speaker_id))
        text = self._preview_text_for_model(model)

        # Preview is local-only and reuses the same Piper/WAV/GTK playback path.
        self._stop_tts_preview()
        generation = self._preview_generation
        speed = 1.0 if speed is None else speed
        volume = 1.0 if volume is None else volume
        self._preview_button = button
        self._preview_stop_button = stop_button
        self._preview_status = status_label
        stop_button.set_sensitive(True)
        button.set_sensitive(False)
        speaker_label = (
            f" · {model.speaker_name(speaker_id)}" if model.num_speakers > 1 else ""
        )
        status_label.set_text(
            f"Đang tổng hợp {model.display_name}{speaker_label}…"
        )

        def worker() -> None:
            wav_path: Path | None = None
            try:
                wav_path = self.engine.synthesize(
                    text, model.model_id, speed, volume, speaker_id
                )
                logger.info(
                    "TTS preview synthesized: generation=%d model=%s speaker_id=%d "
                    "wav=%s size=%d",
                    generation, model.model_id, speaker_id, wav_path,
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

    def _notify(self, title: str, body: str) -> None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        self.app.send_notification(None, notification)

    def _on_close_request(self, *_args):
        # The application stays alive; StatusNotifierItem and launcher can reopen it.
        self.set_visible(False)
        return True
