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
sudo pacman -S --needed git gtk4 python-gobject python-pip opus gstreamer gst-plugins-base gst-plugins-good
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

## AppImage (x86_64)

Tải `Discord-TTS-*.AppImage` từ GitHub Releases, sau đó chạy:

```bash
chmod +x Discord-TTS-*.AppImage
./Discord-TTS-*.AppImage
```

AppImage đóng gói Python, GTK4, PyGObject, GStreamer cùng các plugin phát WAV/PulseAudio, Piper/ONNX Runtime, Opus và các Python dependencies. Máy vẫn cần desktop Linux có Wayland hoặc X11; driver đồ họa và tích hợp phiên desktop được sử dụng từ host.

Ở lần chạy đầu, AppImage giải nén runtime vào:

```text
~/.cache/discord-tts/appimage-runtime/
```

Các lần chạy tiếp theo sử dụng runtime đã giải nén. Cấu hình, Bot Token, lịch sử và voice model vẫn nằm trong các thư mục XDG của người dùng như khi cài từ source. Runtime trong cache có thể xóa để giải phóng dung lượng; lần mở tiếp theo sẽ tự giải nén lại.

Nếu máy không hỗ trợ FUSE, thử:

```bash
APPIMAGE_EXTRACT_AND_RUN=1 ./Discord-TTS-*.AppImage
```

### Build AppImage trên Arch Linux / CachyOS

Cần `curl`, `tar` và `bzip2`:

```bash
sudo pacman -S --needed curl tar bzip2
./scripts/build-appimage.sh
```

Script tải Micromamba và appimagetool vào thư mục tạm, tạo runtime đóng gói riêng và tự xóa cache/build tạm sau khi hoàn tất. Không cần sudo để build. File được tạo trong `dist/`, kèm checksum SHA-256.

GitHub Actions cũng build AppImage x86_64 khi chạy thủ công hoặc push tag `v*`; tag sẽ tạo/cập nhật GitHub Release kèm AppImage và checksum.

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
