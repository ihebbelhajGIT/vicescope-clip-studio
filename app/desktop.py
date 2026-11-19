"""Entry point for native packages and their isolated yt-dlp worker."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.platform_support import process_options, tool_path, ytdlp_command


def smoke_test():
    from ctranslate2 import get_supported_compute_types
    from faster_whisper import WhisperModel
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication, QWidget

    if sys.platform in ("darwin", "win32"):
        from app.credentials import NativeVault
        NativeVault()  # Select and import the real backend without writing credentials.
    if not get_supported_compute_types("cpu"):
        raise RuntimeError("CTranslate2 has no supported CPU compute types")
    vad = Path(sys.modules[WhisperModel.__module__].__file__).parent / "assets"
    if not any(vad.glob("silero_vad*.onnx")):
        raise RuntimeError("faster-whisper's packaged voice activity detector is missing")
    for name in ("ffmpeg", "ffprobe"):
        subprocess.run([tool_path(name), "-version"], check=True, timeout=30,
                       capture_output=True, **process_options())
    for option, required in (("-filters", {"subtitles", "boxblur"}), ("-encoders", {"libx264", "aac"})):
        result = subprocess.run([tool_path("ffmpeg"), "-hide_banner", option], check=True, timeout=30,
                                capture_output=True, **process_options())
        missing = required.difference(result.stdout.split())
        if missing:
            raise RuntimeError("FFmpeg is missing: " + ", ".join(sorted(missing)))
    subprocess.run(ytdlp_command("--ignore-config", "--version"), check=True, timeout=60,
                   capture_output=True, **process_options(bundled_child=True))
    app = QApplication([])
    widget = QWidget()
    player = QMediaPlayer(widget)
    app.processEvents()
    del player, widget
    return 0


def main():
    if sys.argv[1:2] == ["--yt-dlp-worker"]:
        import yt_dlp
        yt_dlp.main(sys.argv[2:])
        return 0
    if sys.argv[1:2] == ["--smoke-test"]:
        return smoke_test()
    from app.main import main as open_studio
    return open_studio()


if __name__ == "__main__":
    sys.exit(main())
