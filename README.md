# Discord TTS

Ứng dụng desktop Linux giúp gửi văn bản thành giọng nói trong Discord bằng Piper TTS. Ứng dụng hỗ trợ chat trực tiếp, Quick Chat và đọc tin nhắn từ các kênh Discord được chọn.

## Tính năng

- Chọn giọng đọc và điều chỉnh tốc độ, âm lượng riêng cho từng khu vực.
- Quick Chat có thể lưu giọng đọc và thiết lập riêng.
- Hỗ trợ nhiều voice Piper được cài trên máy, bao gồm giọng đa người nói.
- Đọc tin nhắn từ những kênh Discord mà bạn cho phép.
- Lưu lịch sử chat và Quick Chat trên máy.

## Cài đặt (Arch Linux / CachyOS)

Cài các gói cần thiết:

```bash
sudo pacman -S --needed git gtk4 python-gobject python-pip opus
```

Tải mã nguồn và cài ứng dụng:

```bash
git clone https://github.com/KangNguyenn/discord-tts.git
cd discord-tts

python -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Khởi chạy:

```bash
discord-tts
```

Nếu lệnh trên chưa có, chạy bằng:

```bash
python -m discord_tts.main
```

## Thiết lập Discord

1. Tạo ứng dụng và bot tại [Discord Developer Portal](https://discord.com/developers/applications).
2. Bật **Message Content Intent** nếu muốn ứng dụng đọc tin nhắn trong kênh.
3. Mời bot vào server với quyền xem kênh, kết nối và nói trong voice channel.
4. Mở Discord TTS, vào **Settings** và nhập Bot Token.
5. Chọn các text channel mà bot được phép đọc.

Ứng dụng không tự đọc mọi kênh. Chỉ những kênh bạn chọn trong Settings mới được theo dõi.

## Thêm voice Piper

Ứng dụng sử dụng voice Piper lưu trên máy và không tự tải model.

1. Mở **Voice Picker** trong ứng dụng.
2. Nhấn biểu tượng thư mục để mở thư mục voice.
3. Chép cả hai tệp của voice vào đó: tệp `.onnx` và tệp `.onnx.json`.
4. Mở lại danh sách model để chọn voice.

Thư mục mặc định:

```text
~/.local/share/discord-tts/voices/
```

Có thể đặt voice trong thư mục con. Ứng dụng sẽ tự tìm các model hợp lệ. Bạn có thể tìm voice tại [Piper Voices](https://huggingface.co/rhasspy/piper-voices). Hãy kiểm tra giấy phép của từng voice trước khi sử dụng hoặc phân phối lại.

Khi cài mới, ứng dụng chọn sẵn voice tiếng Việt. Quick Chat mới sẽ dùng thiết lập hiện tại của Direct Chat làm mặc định; bạn có thể đổi riêng sau đó.

## Dữ liệu và bảo mật

Thiết lập và Bot Token được lưu trong:

```text
~/.config/discord-tts/config.json
```

Lịch sử chat và dữ liệu Quick Chat được lưu trong:

```text
~/.local/share/discord-tts/
```

Bot Token được lưu trên máy dưới dạng văn bản, với quyền truy cập giới hạn cho tài khoản Linux hiện tại. Không chia sẻ tệp cấu hình này.

## Build Flatpak trên Arch Linux / CachyOS

Đảm bảo máy đã cài Flatpak:

```bash
sudo pacman -S --needed flatpak
```

Từ thư mục mã nguồn, chạy:

```bash
./scripts/build-flatpak.sh
```

Bundle được tạo tại `dist/Discord-TTS.flatpak`. Cài cho tài khoản hiện tại, không cần `sudo`:

```bash
flatpak install --user ./dist/Discord-TTS.flatpak
```

Script tự cài `flatpak-builder` bằng pacman nếu máy chưa có và tự gỡ gói đó sau khi build. Nếu công cụ đã được cài từ trước, script giữ nguyên. Script cũng tự tạo `python3-dependencies.json` bằng `flatpak-pip-generator` giống CI; file được tạo tạm và tự xóa sau khi build. Build sử dụng per-user Flatpak installation, tự thêm Flathub nếu cần, và xóa thư mục build/cache/venv tạm sau khi chạy. GNOME SDK chỉ bị gỡ nếu nó được cài mới trong lần build này; GNOME Platform được giữ lại vì ứng dụng cần runtime này để chạy.

Có thể đổi vị trí bundle bằng biến môi trường `OUTPUT_FILE`:

```bash
OUTPUT_FILE="$HOME/Downloads/Discord-TTS.flatpak" ./scripts/build-flatpak.sh
```

## Đóng góp và đóng gói

Dự án hiện hướng tới Linux desktop. GitHub Actions có workflow để build gói Flatpak x86_64; xem tab **Actions** của repository để chạy và tải artifact.
