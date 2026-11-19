import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import core, platform_support
from app.credentials import NativeVault


@pytest.mark.parametrize("platform,env,data,cache", [
    ("win32", {}, "AppData/Local/ViceScopeClipStudio", "AppData/Local/ViceScopeClipStudio/Cache"),
    ("darwin", {}, "Library/Application Support/ViceScopeClipStudio", "Library/Caches/ViceScopeClipStudio"),
    ("linux", {}, ".local/share/ViceScopeClipStudio", ".cache/ViceScopeClipStudio"),
])
def test_native_app_paths(platform, env, data, cache, tmp_path):
    assert platform_support.application_paths(platform, env, tmp_path) == (tmp_path / data, tmp_path / cache)


def test_windows_existing_data_and_explicit_overrides(tmp_path):
    local = tmp_path / "Local"
    data, cache = platform_support.application_paths("win32", {"LOCALAPPDATA": str(local)}, tmp_path)
    assert data == local / "ViceScopeClipStudio"
    env = {"VICESCOPE_DATA_DIR": str(tmp_path / "data"), "VICESCOPE_CACHE_DIR": str(tmp_path / "cache")}
    assert platform_support.application_paths("darwin", env, tmp_path) == (tmp_path / "data", tmp_path / "cache")


@pytest.mark.parametrize("platform,suffix", [("darwin", ""), ("win32", ".exe")])
def test_configured_ffmpeg_pair(tmp_path, monkeypatch, platform, suffix):
    monkeypatch.delenv("VICESCOPE_FFMPEG_BIN", raising=False)
    monkeypatch.setenv("VICESCOPE_DATA_DIR", str(tmp_path))
    directory = tmp_path / "media tools"
    directory.mkdir()
    (tmp_path / "settings.json").write_text(json.dumps({"ffmpeg_bin": str(directory)}), encoding="utf-8-sig")
    for name in ("ffmpeg", "ffprobe"):
        binary = directory / (name + suffix)
        binary.touch(mode=0o755)
        assert platform_support.tool_path(name, platform) == str(binary.resolve())
    monkeypatch.setenv("VICESCOPE_FFMPEG_BIN", str(tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="configured FFmpeg"):
        platform_support.tool_path("ffprobe", platform)


def test_macos_finder_homebrew_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("VICESCOPE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VICESCOPE_FFMPEG_BIN", raising=False)
    monkeypatch.setattr(platform_support.shutil, "which", lambda _: None)
    exist = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda path: path == Path("/opt/homebrew/bin/ffprobe") or exist(path))
    monkeypatch.setattr(platform_support.os, "access", lambda path, _: path == Path("/opt/homebrew/bin/ffprobe"))
    assert platform_support.tool_path("ffprobe", "darwin") == str(Path("/opt/homebrew/bin/ffprobe").resolve())


@pytest.mark.parametrize("platform,module,backend_name", [
    ("win32", "keyring.backends.Windows", "WinVaultKeyring"),
    ("darwin", "keyring.backends.macOS", "Keyring"),
])
def test_native_credentials_keep_existing_identifiers(monkeypatch, platform, module, backend_name):
    backend = Mock()
    factory = Mock(return_value=backend)
    monkeypatch.setitem(sys.modules, module, SimpleNamespace(**{backend_name: factory}))
    vault = NativeVault(platform)
    vault.put("refresh-token", "synthetic")
    vault.get("refresh-token")
    vault.delete("refresh-token")
    service = "ViceScopeClipStudio.YouTube"
    backend.set_password.assert_called_once_with(service, "refresh-token", "synthetic")
    backend.get_password.assert_called_with(service, "refresh-token")
    backend.delete_password.assert_called_once_with(service, "refresh-token")
    if platform == "win32":
        assert backend.persist == "local machine"


def test_credentials_fail_closed_on_unsupported_platform():
    with pytest.raises(RuntimeError, match="No supported native credential store"):
        NativeVault("linux")


@pytest.mark.parametrize("supported,expected", [({"int8", "float32"}, "int8"), ({"float32"}, "float32")])
def test_transcription_uses_cpu_and_app_cache(tmp_path, monkeypatch, supported, expected):
    monkeypatch.setenv("VICESCOPE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("HF_XET_CACHE", raising=False)
    words = [SimpleNamespace(start=0, end=0.1, word=" hello ", probability=.95),
             SimpleNamespace(start=.1, end=.2, word=" discarded", probability=.2)]
    transcribe = Mock(return_value=(iter([SimpleNamespace(words=words)]), None))
    whisper = Mock(return_value=SimpleNamespace(transcribe=transcribe))
    types = Mock(return_value=supported)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_supported_compute_types=types))
    assert core.transcribe("sample.mp4", "tiny.en") == [{"start": 0, "end": .1, "text": "hello", "probability": .95}]
    types.assert_called_once_with("cpu")
    whisper.assert_called_once_with("tiny.en", device="cpu", compute_type=expected,
                                   download_root=str(tmp_path / "cache" / "models"))
    transcribe.assert_called_once_with("sample.mp4", word_timestamps=True, vad_filter=True)


def test_frozen_and_source_ytdlp_command(monkeypatch):
    assert platform_support.ytdlp_command("--version") == [sys.executable, "-m", "yt_dlp", "--version"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert platform_support.ytdlp_command("--version") == [sys.executable, "--yt-dlp-worker", "--version"]


def test_desktop_entrypoint_smoke(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    subprocess.run([sys.executable, "-m", "app.desktop", "--smoke-test"], check=True, timeout=90)


def test_render_and_review_with_unicode_paths(tmp_path):
    from app.review import ReviewQueue
    from app.storage import Store
    from app.video import probe, render

    media = tmp_path / "Sources été"
    media.mkdir()
    source = media / "séquence.mp4"
    subprocess.run([
        platform_support.tool_path("ffmpeg"), "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source),
    ], check=True, timeout=30)
    store = Store(tmp_path / "data" / "studio.sqlite3")
    store.save_source(str(source), "Créateur", "", "Synthetic local fixture", True)
    output = tmp_path / "Export été" / "révision.mp4"
    result = render(store, str(source), 0, .5, str(output), [], "Été", "Révision")
    info = probe(output)
    codecs = {stream["codec_type"]: stream for stream in info["streams"]}
    assert (codecs["video"]["codec_name"], codecs["video"]["width"], codecs["video"]["height"],
            codecs["audio"]["codec_name"]) == ("h264", 1080, 1920, "aac")
    package = {key: result[key] for key in ("job_id", "source_sha256", "output_sha256")}
    (output.parent / "metadata.json").write_text(json.dumps(package), encoding="utf-8")
    queue = ReviewQueue(store)
    item = queue.add(result["job_id"], 1, "Test", {"title": "Révision", "description": "Été", "hashtags": []})
    queue.decide(item, "approved_private")
    assert queue.is_approved(item)
    store.save_source(str(source), "Créateur", "", "Synthetic local fixture", False)
    assert not queue.is_approved(item)
