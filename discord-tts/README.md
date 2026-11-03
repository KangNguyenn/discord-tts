# Discord TTS (GTK4)

A local-first Linux desktop app for controlling a Discord TTS bot. The UI is written with GTK4/PyGObject; the bot runs locally and uses offline Piper voices.

## Current MVP

- GTK4 chat-style composer: `Enter` sends and speaks; `Shift+Enter` adds a newline.
- Each app message has an explicit Vietnamese/English selector.
- New speech interrupts the current playback (one shared speech controller).
- Discord `/join`, `/leave`, `/tts`, `/stop`, `/quick` commands, plus `!tts vi|en <text>`.
- Optional Discord text-channel reader. It is **off by default**; choose allowed channels in Settings.
- Quick Chat buttons and SQLite history.
- Discord token stored through Linux Secret Service, not in JSON config.
- Piper model downloader for Vietnamese and English.
- Close hides the window; the app attempts to register a StatusNotifierItem. KDE panels and many Wayland panels support it natively. GNOME needs a tray/AppIndicator extension. Launching the app again should reactivate the existing window.

## Important setup notes

This is an initial source MVP, not yet a signed/release-ready Flatpak. Discord voice requires discord.py 2.7.1+ with its voice extras (PyNaCl and DAVE support) and the Opus shared library. Discord completed its all-call DAVE migration in March 2026, so the project intentionally requires a DAVE-capable discord.py release. WAV-to-PCM conversion is handled in-app, so no FFmpeg subprocess is needed. Bot permissions must include Connect, Speak, View Channels, Send Messages (for commands), and application-command access. Enable **Message Content Intent** in Discord Developer Portal only if you want to read normal text-channel messages.

The Discord bot must be invited to exactly the server you intend to use. The app lets you choose one server if it is in more than one. For safety, normal text messages are not read until you explicitly allow one or more text channels in Settings.

### CachyOS / Arch Linux

Install system GTK/GI, the Opus shared library and a Secret Service provider:

```bash
sudo pacman -S --needed gtk4 python-gobject python-pip opus libsecret gnome-keyring
```

Create an environment that can see the system PyGObject bindings, then install Python dependencies:

```bash
python -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Start a Secret Service provider (for example GNOME Keyring) in your desktop session, then run:

```bash
python -m discord_tts.main
```

Or install the project into the venv and use `discord-tts`.

### Voice models

Open Settings and press **Tải voice VI** and **Tải voice EN**. These download the model and its JSON config into:

```text
~/.local/share/discord-tts/voices/
```

The initial model IDs are `vi_VN-vais1000-medium` and `en_US-lessac-medium`. Models are separate downloads and are not bundled into the app. Check each model card's license and attribution terms before redistribution.

## Configuration and data

- Config: `~/.config/discord-tts/config.json` (no bot token)
- TTS models / SQLite history: `~/.local/share/discord-tts/`
- Temporary WAV files: system temporary directory; removed after playback
- Bot Token: Linux Secret Service item `io.github.kang.discordtts`

## Build Flatpak without installing build tools locally

The repository includes a GitHub Actions workflow at `.github/workflows/build-flatpak.yml`. It builds the app in the cloud using the GNOME 50 Flatpak build image. The workflow also generates the pinned Python dependency source manifest on the CI runner, so you do not need `flatpak`, `flatpak-builder`, the GNOME SDK, Python build tools, or the Flatpak SDK on your own computer.

1. Create a GitHub repository and upload/push this project source.
2. Open the repository's **Actions** tab.
3. Select **Build Flatpak** in the workflow list.
4. Click **Run workflow** and choose the branch containing the source.
5. When the run succeeds, open the run and download the `Discord-TTS-x86_64` artifact. It contains `Discord-TTS.flatpak`.

The workflow also runs automatically when you push a version tag such as `v0.1.0`. The build currently targets x86_64.

**Important:** this removes the need for local build tools, not the Flatpak runtime requirement. To install and run the resulting bundle, the target Linux system must have the Flatpak application installed; Flatpak may also download the GNOME 50 runtime on first install. If you do not want Flatpak itself installed on the target machine, an AppImage would be the suitable distribution format instead.

The workflow has been added as build configuration but still needs its first real GitHub Actions run to validate dependency resolution and the finished bundle.

## Architecture

```text
GTK4 main thread
  ├── Conversation bubbles + composer
  ├── Quick Chat + Settings
  └── GLib idle callbacks
          │
          ▼
Discord asyncio thread (discord.py)
  ├── Gateway / slash commands / voice
  └── SpeechController (interrupts previous generation)
          │
          ▼
Piper worker thread -> WAV -> 48 kHz stereo PCM -> Discord Opus encoder

SQLite: history + quick messages
Secret Service: Discord Bot Token
```

## Known MVP limitations

- Discord messages use a configurable default language because the sender cannot choose a language for another person's message. App chat and `/tts` have an explicit language choice.
- The Settings UI currently exposes speed and volume. The MVP uses one default model per language. A model picker, pitch-shifting and per-user voice profiles are planned, not implemented yet.
- System-tray support is StatusNotifierItem-based. A tray watcher/extension is required; close-to-hide and launcher reactivation are fallbacks.
- Slash commands are synced globally. Discord may take time to make new global commands visible.
- This project has not yet been runtime-tested against a live Discord server in this build environment.
