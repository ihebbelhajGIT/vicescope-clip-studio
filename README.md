# ViceScope Clip Studio

An experimental Windows desktop application for source-rights tracking and local video clipping. It does not upload or publish videos. Python 3.11+, FFmpeg and FFprobe are required; install dependencies with `.venv\Scripts\python.exe -m pip install -r requirements.txt` and launch with `.venv\Scripts\python.exe -m app.main`.

Choose a local source in Clip Editor, enter permission evidence, explicitly approve and save it. Set start and end times, hook, title and description. Highlight Finder can transcribe the authorized source; the first run may download a model. Render creates a new 1080×1920 H.264/AAC MP4 and a JSON draft beside it. FFmpeg and FFprobe are detected from `.venv/ffmpeg-dist/*/bin` or PATH. Settings displays the detected paths. No media or models are included in this repository.

The draft records hashes and attribution; retain the original source and evidence. Review the clip and rights manually before publishing. An MIT software license does not grant rights to footage.

Run tests with `.venv\Scripts\python.exe -m pytest -q`. Synthetic integration tests cover generated source, approval, rendering, probe inspection and metadata. This is an experimental workflow: automatic candidate detection, video preview, subtitle editing, presets, detailed progress, export history, and an output copy inside a package directory are not implemented. Transcript and real-world media quality have not been verified. The metadata draft points to the MP4 instead of copying it. Existing general-purpose package preparation remains disabled.

See `docs/architecture.md` for the earlier audit and `THIRD_PARTY_NOTICES.md` for attribution. The FFmpeg binaries and model weights are separately distributed components with their own licenses.
