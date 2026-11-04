#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENVIRONMENT_FILE="$PROJECT_ROOT/packaging/appimage/environment.yml"
APP_VERSION="$(awk -F'"' '/^version = / { print $2; exit }' "$PROJECT_ROOT/pyproject.toml")"
APP_BUILD_ID="${APPIMAGE_BUILD_ID:-$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || printf 'local')}"
APP_KEY="$APP_VERSION-$APP_BUILD_ID"
OUTPUT_FILE="${APPIMAGE_OUTPUT:-$PROJECT_ROOT/dist/Discord-TTS-$APP_VERSION-x86_64.AppImage}"

WORK_DIR=""
MAMBA_BIN=""
APPIMAGETOOL_BIN=""

log() {
    printf '\033[1;34m[build-appimage]\033[0m %s\n' "$*"
}

die() {
    printf '\033[1;31m[build-appimage] ERROR:\033[0m %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status="$1"
    trap - EXIT
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
    exit "$status"
}
trap 'cleanup $?' EXIT

[[ "$(uname -m)" == "x86_64" ]] ||
    die "Hiện chỉ hỗ trợ build AppImage x86_64."

[[ -n "$APP_VERSION" ]] || die "Không đọc được version từ pyproject.toml."
[[ -f "$ENVIRONMENT_FILE" ]] || die "Không tìm thấy $ENVIRONMENT_FILE."

for command in curl tar bzip2; do
    command -v "$command" >/dev/null 2>&1 ||
        die "Thiếu '$command'. Trên Arch/CachyOS: sudo pacman -S --needed curl tar bzip2"
done

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/discord-tts-appimage.XXXXXX")"
ENV_DIR="$WORK_DIR/runtime"
APPDIR="$WORK_DIR/Discord-TTS.AppDir"
RUNTIME_ARCHIVE="$APPDIR/usr/share/discord-tts/runtime.tar.gz"

if command -v micromamba >/dev/null 2>&1; then
    MAMBA_BIN="$(command -v micromamba)"
else
    log "Tải Micromamba vào thư mục tạm..."
    curl --fail --location --silent --show-error \
        https://micro.mamba.pm/api/micromamba/linux-64/latest \
        --output "$WORK_DIR/micromamba.tar.bz2"
    tar -xjf "$WORK_DIR/micromamba.tar.bz2" -C "$WORK_DIR" bin/micromamba
    MAMBA_BIN="$WORK_DIR/bin/micromamba"
fi

if [[ -n "${APPIMAGETOOL:-}" ]]; then
    APPIMAGETOOL_BIN="$APPIMAGETOOL"
else
    log "Tải appimagetool chính thức..."
    APPIMAGETOOL_BIN="$WORK_DIR/appimagetool-x86_64.AppImage"
    curl --fail --location --silent --show-error \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
        --output "$APPIMAGETOOL_BIN"
    chmod +x "$APPIMAGETOOL_BIN"
fi

log "Tạo runtime Python 3.11 + GTK4 + PyGObject..."
export MAMBA_ROOT_PREFIX="$WORK_DIR/mamba-root"
export MAMBA_NO_BANNER=1
"$MAMBA_BIN" create --yes --prefix "$ENV_DIR" --file "$ENVIRONMENT_FILE"

log "Cài Discord TTS và Python dependencies..."
"$ENV_DIR/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "$PROJECT_ROOT"
"$ENV_DIR/bin/python" -m pip check

log "Kiểm tra imports và GTK4 introspection..."
"$ENV_DIR/bin/python" - <<'PY'
import ctypes.util
import discord
import gi
import numpy
import onnxruntime
import piper

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gst, Gtk

Gst.init(None)
required_factories = ("playbin", "wavparse", "autoaudiosink", "pulsesink")
missing = [
    name for name in required_factories
    if Gst.ElementFactory.find(name) is None
]
if missing:
    raise RuntimeError(f"Missing GStreamer plugins: {', '.join(missing)}")

print(f"GTK: {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
print(f"GStreamer: {Gst.version_string()} (playbin, WAV parser and PulseAudio sink available)")
print(f"NumPy: {numpy.__version__}")
print(f"ONNX Runtime: {onnxruntime.__version__}")
print(f"discord.py: {discord.__version__}")
print(f"Opus found by host lookup: {ctypes.util.find_library('opus') or 'runtime fallback will be used'}")
PY

log "Chuẩn bị GTK/GIO caches..."
if [[ -d "$ENV_DIR/share/glib-2.0/schemas" && -x "$ENV_DIR/bin/glib-compile-schemas" ]]; then
    "$ENV_DIR/bin/glib-compile-schemas" "$ENV_DIR/share/glib-2.0/schemas"
fi
if [[ -d "$ENV_DIR/lib/gio/modules" && -x "$ENV_DIR/bin/gio-querymodules" ]]; then
    "$ENV_DIR/bin/gio-querymodules" "$ENV_DIR/lib/gio/modules"
fi
if [[ -x "$ENV_DIR/bin/gdk-pixbuf-query-loaders" ]]; then
    "$ENV_DIR/bin/gdk-pixbuf-query-loaders" --update-cache
fi

log "Đóng gói runtime có thể di chuyển..."
mkdir -p -- "$(dirname -- "$RUNTIME_ARCHIVE")"
"$ENV_DIR/bin/conda-pack" --prefix "$ENV_DIR" --format tar.gz --output "$RUNTIME_ARCHIVE"
chmod 0644 "$RUNTIME_ARCHIVE"

log "Tạo AppDir..."
mkdir -p "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/128x128/apps"
cp "$PROJECT_ROOT/packaging/appimage/discord-tts.desktop" "$APPDIR/discord-tts.desktop"
cp "$PROJECT_ROOT/packaging/appimage/discord-tts.png" "$APPDIR/discord-tts.png"
cp "$PROJECT_ROOT/packaging/appimage/discord-tts.desktop" "$APPDIR/usr/share/applications/discord-tts.desktop"
cp "$PROJECT_ROOT/packaging/appimage/discord-tts.png" "$APPDIR/usr/share/icons/hicolor/128x128/apps/discord-tts.png"
cp "$PROJECT_ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
printf '%s\n' "$APP_KEY" > "$APPDIR/usr/share/discord-tts/runtime-version"

if [[ "$OUTPUT_FILE" != /* ]]; then
    OUTPUT_FILE="$PROJECT_ROOT/$OUTPUT_FILE"
fi
mkdir -p -- "$(dirname -- "$OUTPUT_FILE")"

log "Tạo AppImage..."
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL_BIN" "$APPDIR" "$OUTPUT_FILE"
chmod +x "$OUTPUT_FILE"

log "Kiểm thử AppImage và lần giải nén runtime đầu tiên..."
APPIMAGE_EXTRACT_AND_RUN=1 DISCORD_TTS_APPIMAGE_SMOKE_TEST=1 "$OUTPUT_FILE"

sha256sum "$OUTPUT_FILE" > "$OUTPUT_FILE.sha256"

log "Hoàn tất:"
log "  AppImage: $OUTPUT_FILE"
log "  SHA-256:  $OUTPUT_FILE.sha256"
log "Runtime sẽ được giải nén lần đầu vào ~/.cache/discord-tts/appimage-runtime/$APP_KEY"
