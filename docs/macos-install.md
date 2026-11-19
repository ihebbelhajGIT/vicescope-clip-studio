# Install ViceScope on Apple Silicon macOS

These commands use native arm64 Python 3.12 and work with a checkout under your home directory. If you already have a checkout, use that directory. Install [Homebrew](https://brew.sh/) first if needed, then run in Terminal.

```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
brew install python@3.12 ffmpeg-full deno
git clone https://github.com/ihebbelhajGIT/vicescope-clip-studio.git ~/vicescope-clip-studio
cd ~/vicescope-clip-studio
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/python -c 'import platform; assert platform.machine() == "arm64"'
.venv/bin/python -m pip install -r requirements.txt
"$(brew --prefix ffmpeg-full)/bin/ffmpeg" -version
"$(brew --prefix ffmpeg-full)/bin/ffprobe" -version
deno --version
.venv/bin/python -m app.desktop
```

Use a checkout that contains the cross-platform commit. On Mac, ViceScope looks for the full Homebrew FFmpeg build even when Finder launches the app with a minimal PATH. That build provides libass for caption rendering and libx264 for vertical MP4 export. If you installed FFmpeg elsewhere, use an absolute `ffmpeg_bin` setting or set `VICESCOPE_FFMPEG_BIN` before launch. The Settings page shows which executables were found.

The application stores its database, log, and settings under `~/Library/Application Support/ViceScopeClipStudio`. Whisper models download on demand to `~/Library/Caches/ViceScopeClipStudio/models` and use the native CTranslate2 CPU backend. Clip and source files remain in the folders you select.

YouTube refresh tokens and resumable upload sessions are stored in the macOS login Keychain. macOS may prompt for access when you first connect or after rebuilding the app. Set up your OAuth client and the expected channel using the [YouTube setup guide](youtube-setup.md). Use `settings.json` for Finder launches. See [Build and package](build-and-package.md) for a native `.app` and optional disk image.
