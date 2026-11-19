import subprocess
from pathlib import Path
import pytest
from app.storage import Store
from app.video import probe, render, subtitles_ass, tool_path


def test_subtitles_are_relative_to_selection():
    ass = subtitles_ass([{"start": 12, "end": 13, "text": "Hello"}], 10, 15, "Hook", "Creator")
    assert "0:00:02.00,0:00:03.00" in ass


def test_synthetic_render_and_rights(tmp_path):
    source = tmp_path / "synthetic.mp4"
    subprocess.run([tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    store = Store(tmp_path / "db.sqlite")
    target = tmp_path / "vertical.mp4"
    with pytest.raises(PermissionError):
        render(store, str(source), 0, 1, str(target), [], "Hook", "Title")
    store.save_source(str(source), "Creator", "", "Generated test footage", True)
    result = render(store, str(source), 0, 1, str(target), [{"start": .1, "end": .8, "text": "Hello"}], "Hook", "Title")
    streams = probe(target)["streams"]
    assert result["output_sha256"]
    assert any(s.get("width") == 1080 and s.get("height") == 1920 for s in streams)
    assert any(s.get("codec_name") == "aac" for s in streams)


def test_package_checks_output_and_source(tmp_path):
    from app.video import prepare_package
    source = tmp_path / "synthetic.mp4"
    subprocess.run([tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=160x90:r=30:d=1", "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    store = Store(tmp_path / "db.sqlite")
    store.save_source(str(source), "Creator", "", "Permission", True)
    result = render(store, str(source), 0, .5, str(tmp_path / "clip.mp4"), [], "Hook", "Title")
    metadata = prepare_package(store, result, str(tmp_path / "metadata.json"))
    assert '"creator_credit": "Creator"' in metadata.read_text(encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"modified")
    with pytest.raises(PermissionError):
        prepare_package(store, result, str(tmp_path / "other.json"))
