# Discord TTS (GTK4)

A local-first Linux desktop app for controlling a Discord TTS bot. The UI is written with GTK4/PyGObject; the bot runs locally and uses offline Piper voices.

## Current MVP

- GTK4 chat-style composer: `Enter` sends and speaks; `Shift+Enter` adds a newline.
- App chat uses a selectable local Piper model and speaker.
- New speech interrupts the current playback.
- Discord `/join`, `/leave`, `/tts`, `/stop`, and `/quick` commands.
- Optional Discord text-channel reader. It is off by default; choose allowed channels and its local voice model in Settings.
- Quick Chat buttons, delete controls, and SQLite history.
- Bot Token can be entered or changed in the app; it is stored in the local config file with owner-only permissions (`0600`). No Keyring or Secret Service is required.
- Local Piper model discovery from .onnx/.onnx.json pairs; the shared picker previews models and supports speaker selection.
- Close hides the window; the app attempts to register a StatusNotifierItem. KDE panels and many Wayland panels support it natively. GNOME needs a tray/AppIndicator extension. Launching the app again should reactivate the existing window.

## Important setup notes

This is an initial source MVP, not yet a signed/release-ready Flatpak. Discord voice requires discord.py 2.7.1+ with its voice extras (PyNaCl and DAVE support) and the Opus shared library. WAV-to-PCM conversion is handled in-app, so no FFmpeg subprocess is needed. Bot permissions must include Connect, Speak, View Channels, Send Messages (for commands), and application-command access. Enable Message Content Intent in Discord Developer Portal only if you want to read normal text-channel messages.

The Discord bot must be invited to the server you intend to use. For safety, normal text messages are not read until you explicitly allow one or more text channels in Settings.

### CachyOS / Arch Linux

Install GTK/GI, the Opus shared library and Python tooling:

```bash
sudo pacman -S --needed gtk4 python-gobject python-pip opus
```

Create an environment that can see the system PyGObject bindings, then install Python dependencies:

```bash
python -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Run:

```bash
python -m discord_tts.main
```

Or use the `discord-tts` command after installing the project into the environment.

### Voice models

The app uses local models only; it does not download models or fetch an online catalog. Open the model picker and press the folder icon in its title bar to open:

```text
~/.local/share/discord-tts/voices/
```

Copy both files for each Piper voice into that folder:

```text
voice-name.onnx
voice-name.onnx.json
```

Subdirectories are supported. The app scans recursively and lists models with a matching JSON sidecar. Language, quality, sample rate, and speaker information are read from local metadata. Multi-speaker models expose a speaker dropdown in the picker.

Piper models can be found in the [Piper voice repository](https://huggingface.co/rhasspy/piper-voices). Check each model card's license and attribution terms before using or redistributing a model.

## Configuration and data

- Config, including Bot Token: `~/.config/discord-tts/config.json`
- Config file permissions: owner read/write only (`0600`)
- TTS models / SQLite history: `~/.local/share/discord-tts/`
- Temporary WAV files: system temporary directory; removed after playback

The token is stored in plaintext inside the local config file, protected by filesystem permissions. Anyone with access to your Linux account can still read it. Do not share this file or include it in backups/repositories without protecting it.

## Build Flatpak without installing build tools locally

The repository includes a GitHub Actions workflow at `.github/workflows/main.yml`. It builds the app in the cloud using the GNOME 50 Flatpak build image. You do not need `flatpak`, `flatpak-builder`, the GNOME SDK, Python build tools, or the Flatpak SDK on your own computer.

1. Push this project source to the GitHub repository.
2. Open the repository's **Actions** tab.
3. Select **Build Flatpak**.
4. Click **Run workflow** and choose the branch containing the source.
5. When the run succeeds, download the `Discord-TTS-x86_64` artifact. It contains `Discord-TTS.flatpak`.

The workflow currently targets x86_64. The target Linux system still needs Flatpak installed to install and run the bundle. Flatpak may also download the GNOME 50 runtime on first install. If you do not want Flatpak on the target system, AppImage would be the alternative.

## Architecture

```text
GTK4 main thread
  ├── Conversation bubbles + model-selecting composer
  ├── Shared local voice-model picker
  ├── Quick Chat + Settings
  └── GLib idle callbacks
          |
          v
Discord asyncio thread (discord.py)
  ├── Gateway / slash commands / voice
  └── SpeechController (interrupts previous generation)
          |
          v
Piper worker thread -> WAV -> 48 kHz stereo PCM -> Discord Opus encoder

SQLite: history + quick messages
JSON config: per-context model/speaker settings + Discord Bot Token
```

## Known MVP limitations

- Discord message reading uses one configured model/speaker because the sender cannot choose a voice for another person's message. App chat can choose any installed model. The legacy `/tts`, `!tts`, and Quick Chat language options continue to map Vietnamese/English to their configured default model IDs.
- The Settings UI exposes speed and volume. Pitch-shifting and per-user voice profiles are not implemented yet.
- System-tray support is StatusNotifierItem-based. A tray watcher/extension is required; close-to-hide and launcher reactivation are fallbacks.
- Slash commands are synced globally. Discord may take time to make new global commands visible.
- This project has not yet been runtime-tested against a live Discord server in this build environment.
