#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="io.github.kang.discordtts"
APP_BRANCH="stable"
GNOME_SDK="org.gnome.Sdk"
GNOME_SDK_BRANCH="50"
FLATHUB_URL="https://dl.flathub.org/repo/flathub.flatpakrepo"

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$PROJECT_ROOT/packaging/flatpak/io.github.kang.discordtts.yml"
OUTPUT_FILE="${OUTPUT_FILE:-$PROJECT_ROOT/dist/Discord-TTS.flatpak}"
GENERATED_DEPENDENCIES="$PROJECT_ROOT/packaging/flatpak/python3-dependencies.json"

builder_installed_by_script=0
sdk_was_installed=1
dependencies_created_by_script=0
WORK_DIR=""

log() {
    printf '\033[1;34m[build-flatpak]\033[0m %s\n' "$*"
}

die() {
    printf '\033[1;31m[build-flatpak] ERROR:\033[0m %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status="$1"
    trap - EXIT

    # Remove the generated manifest only if it did not exist before this run.
    if (( dependencies_created_by_script == 1 )); then
        rm -f -- "$GENERATED_DEPENDENCIES"
    fi

    # Build sources, generator venv, and Flatpak Builder cache are temporary.
    if [[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi

    # Remove the SDK only when this script had to install it.
    # Keep org.gnome.Platform: the finished application needs that runtime.
    if (( sdk_was_installed == 0 )); then
        if flatpak list --user --runtime --columns=application,branch 2>/dev/null |
            awk -v app="$GNOME_SDK" -v branch="$GNOME_SDK_BRANCH" \
                '$1 == app && $2 == branch { found = 1 } END { exit !found }'; then
            log "Gỡ GNOME SDK vừa cài cho lần build này..."
            if ! flatpak uninstall --user --noninteractive \
                "$GNOME_SDK//$GNOME_SDK_BRANCH"; then
                printf '[build-flatpak] Cảnh báo: không tự gỡ được GNOME SDK.\n' >&2
            fi
        fi
    fi

    # Remove flatpak-builder only if this script installed it.
    if (( builder_installed_by_script == 1 )); then
        log "Gỡ gói flatpak-builder vừa cài..."
        if ! sudo pacman -Rns --noconfirm flatpak-builder; then
            printf '[build-flatpak] Cảnh báo: không tự gỡ được flatpak-builder.\n' >&2
        fi
    fi

    exit "$status"
}
trap 'cleanup $?' EXIT

command -v flatpak >/dev/null 2>&1 ||
    die "Thiếu Flatpak. Cài bằng: sudo pacman -S --needed flatpak"

for command in python3 curl; do
    command -v "$command" >/dev/null 2>&1 ||
        die "Thiếu '$command'. Cài gói python và curl bằng pacman."
done

if ! command -v flatpak-builder >/dev/null 2>&1; then
    command -v pacman >/dev/null 2>&1 ||
        die "Không tìm thấy pacman để cài flatpak-builder."
    log "Cài flatpak-builder tạm thời..."
    sudo pacman -S --needed flatpak-builder
    builder_installed_by_script=1
fi

[[ -f "$MANIFEST" ]] || die "Không tìm thấy manifest: $MANIFEST"

# Use the per-user Flatpak installation so runtime/SDK setup does not need sudo.
if ! flatpak remote-list --user --columns=name | grep -Fxq 'flathub'; then
    log "Thêm Flathub remote cho tài khoản hiện tại..."
    flatpak remote-add --user --if-not-exists flathub "$FLATHUB_URL"
fi

if flatpak list --user --runtime --columns=application,branch 2>/dev/null |
    awk -v app="$GNOME_SDK" -v branch="$GNOME_SDK_BRANCH" \
        '$1 == app && $2 == branch { found = 1 } END { exit !found }'; then
    sdk_was_installed=1
else
    sdk_was_installed=0
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/discord-tts-flatpak.XXXXXX")"
BUILD_DIR="$WORK_DIR/build"
REPO_DIR="$WORK_DIR/repo"
STATE_DIR="$WORK_DIR/state"
DEPENDENCY_VENV="$WORK_DIR/pip-generator-venv"
GENERATOR_SCRIPT="$WORK_DIR/flatpak-pip-generator.py"

# The manifest includes this generated file. CI creates it before building;
# local builds must generate it too.
if [[ ! -f "$GENERATED_DEPENDENCIES" ]]; then
    log "Tạo Python dependency manifest..."
    python3 -m venv "$DEPENDENCY_VENV"
    "$DEPENDENCY_VENV/bin/pip" install --disable-pip-version-check \
        'requirements-parser>=0.11,<1' 'packaging>=23'

    curl --fail --location --silent --show-error \
        https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py \
        --output "$GENERATOR_SCRIPT"

    (
        cd "$PROJECT_ROOT/packaging/flatpak"
        "$DEPENDENCY_VENV/bin/python" "$GENERATOR_SCRIPT" \
            --runtime=org.gnome.Sdk//50 \
            --requirements-file=requirements.txt \
            --output=python3-dependencies \
            --prefer-wheels=numpy,onnxruntime,piper-phonemize,PyNaCl,cffi,cryptography,davey,piper-tts \
            --wheel-arches=x86_64
    )
    dependencies_created_by_script=1
fi

mkdir -p -- "$(dirname -- "$OUTPUT_FILE")"

log "Build $APP_ID..."
flatpak-builder \
    --user \
    --install-deps-from=flathub \
    --force-clean \
    --default-branch="$APP_BRANCH" \
    --state-dir="$STATE_DIR" \
    --repo="$REPO_DIR" \
    "$BUILD_DIR" \
    "$MANIFEST"

log "Đóng gói Flatpak bundle..."
flatpak build-bundle \
    --runtime-repo="$FLATHUB_URL" \
    "$REPO_DIR" \
    "$OUTPUT_FILE" \
    "$APP_ID" \
    "$APP_BRANCH"

log "Hoàn tất: $OUTPUT_FILE"
