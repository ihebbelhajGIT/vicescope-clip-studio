"""Desktop paths and process launch policy shared by source and frozen builds."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "ViceScopeClipStudio"


def application_paths(platform=None, environ=None, home=None):
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    if platform == "win32":
        local = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        data, cache = local / APP_NAME, local / APP_NAME / "Cache"
    elif platform == "darwin":
        data = home / "Library" / "Application Support" / APP_NAME
        cache = home / "Library" / "Caches" / APP_NAME
    else:
        data = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / APP_NAME
        cache = Path(environ.get("XDG_CACHE_HOME") or home / ".cache") / APP_NAME
    if override := environ.get("VICESCOPE_DATA_DIR"):
        data = Path(override).expanduser()
    if override := environ.get("VICESCOPE_CACHE_DIR"):
        cache = Path(override).expanduser()
    return data, cache


def settings():
    data, _ = application_paths()
    try:
        config = json.loads((data / "settings.json").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read settings.json in {data}. Check the file permissions and JSON format.") from exc
    if not isinstance(config, dict) or any(
        key in config and not isinstance(config[key], str)
        for key in ("ffmpeg_bin", "youtube_channel_id")
    ):
        raise ValueError("settings.json must contain an object with string settings.")
    return config


def tool_path(name: str, platform=None) -> str:
    if name not in ("ffmpeg", "ffprobe"):
        raise ValueError("Unknown media tool")
    platform = sys.platform if platform is None else platform
    executable = name + ".exe" if platform == "win32" else name
    configured = os.environ.get("VICESCOPE_FFMPEG_BIN") or settings().get("ffmpeg_bin")
    if configured:
        directory = Path(configured).expanduser()
        candidate = directory / executable
        if directory.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        raise FileNotFoundError(f"{executable} is unavailable in the configured FFmpeg bin folder: {directory}")
    if platform == "win32" and not getattr(sys, "frozen", False):
        legacy = Path(__file__).resolve().parents[1] / ".venv" / "ffmpeg-dist"
        for candidate in sorted(legacy.glob(f"*/bin/{executable}")):
            if candidate.is_file():
                return str(candidate.resolve())
    if platform == "darwin":
        # Homebrew's standard ffmpeg formula can omit libass. Prefer ffmpeg-full.
        for prefix in (Path("/opt/homebrew"), Path("/usr/local")):
            candidate = prefix / "opt" / "ffmpeg-full" / "bin" / executable
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    if found := shutil.which(executable):
        return str(Path(found).resolve())
    if platform == "darwin":
        for directory in (Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")):
            candidate = directory / executable
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
    raise FileNotFoundError(f"{executable} was not found. Install FFmpeg and FFprobe or configure their bin folder.")


def process_options(*, bundled_child=False):
    options = {"encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    env = os.environ.copy()
    if sys.platform == "darwin":
        # Finder starts apps with a minimal PATH. yt-dlp may need Homebrew's Deno.
        env["PATH"] = os.pathsep.join([env.get("PATH") or os.defpath, "/opt/homebrew/bin", "/usr/local/bin"])
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux") and not bundled_child:
        # PyInstaller's library paths belong to the app, not an external FFmpeg.
        original = env.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    options["env"] = env
    return options


def ytdlp_command(*args):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--yt-dlp-worker", *args]
    return [sys.executable, "-m", "yt_dlp", *args]
