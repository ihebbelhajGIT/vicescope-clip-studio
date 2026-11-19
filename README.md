# ViceScope Clip Studio

This is an early, incomplete local desktop foundation, not a finished clipping application. It does not require Claude. Install Python 3.11 or newer and FFmpeg separately, then run `powershell -ExecutionPolicy Bypass -File scripts/install.ps1`. If the Python launcher has a different version, create `.venv` with your Python executable and install `requirements.txt`. Launch with `.\.venv\Scripts\python.exe -m app.main` and run tests with `.\.venv\Scripts\python.exe -m pytest`.

Search uses yt-dlp metadata; search results are not verified gameplay or authorization. Save a campaign brief, choose a local source, enter permission evidence, and explicitly approve the exact source. The Highlight Finder can request transcription using faster-whisper; its model may download on first use. The metadata draft action is disabled until exported clips have verifiable source provenance. Review the source, rights, MP4 and generated JSON before any manual publishing. No publishing is performed by the app. Data and logs live under `%LOCALAPPDATA%\ViceScopeClipStudio`.

Implemented in source: local SQLite source/campaign records, YouTube search adapter, transcription adapter, rights gate and disabled metadata draft, background workers, and a basic Qt interface. Not yet implemented: actual highlight selection, video playback, cutting, subtitles, video rendering, source-to-output provenance, reliable live discovery, cancellation, queue, analytics, packaging as an executable, or complete campaign rules. Metadata packaging accepts a manually selected MP4 and cannot prove it derives from the approved source; verify it manually. The desktop application has not launched in the current environment because PySide6 is unavailable; FFmpeg is also missing. Six dependency-free unit tests pass; see docs/development.md. Do not use it as a production publishing workflow.

For a future executable, after implementing and testing the missing features, install PyInstaller in the virtual environment and evaluate `pyinstaller --windowed --name ViceScopeClipStudio app/main.py`; Qt plugins, model files, FFmpeg and startup imports require further packaging work and validation.


Source approvals are bound to a SHA-256 hash. Replacing a source requires renewed approval; old records are revoked on migration. See docs/development.md for environment and verification status.


A standalone candidate-ranking component and persisted source/segment snapshots are present but are not connected to the UI or an export workflow.
