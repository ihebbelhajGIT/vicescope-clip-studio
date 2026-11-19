import os
import subprocess
import time
from pathlib import Path
import pytest
from app.core import require_rights
from app.highlights import analyze_audio
from app.storage import Store
from app.video import tool_path


def test_audio_analysis_requires_rights_and_returns_scores(tmp_path):
    source = tmp_path / 'sample.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=320x180:r=30:d=2', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite')
    with pytest.raises(PermissionError): analyze_audio(store, str(source), [])
    store.save_source(str(source), 'Creator', '', 'Synthetic video', True)
    results = analyze_audio(store, str(source), [{'start': .5, 'end': .6, 'text': 'hello'}])
    assert results and 'audio' in results[0].explanation and 'spoken' in results[0].explanation


def test_export_directory_and_failure_status(tmp_path, monkeypatch):
    from app.video import export_bundle
    import app.video as video
    store = Store(tmp_path / 'db.sqlite')
    target = tmp_path / 'package'
    def failing_render(*args): raise ValueError('render failed')
    monkeypatch.setattr(video, 'render', failing_render)
    with pytest.raises(RuntimeError, match='Export incomplete'):
        export_bundle(store, 'missing', 0, 1, str(target), [], '', '', '')
    assert target.is_dir()
    assert not (target / 'metadata.json').exists()
    with pytest.raises(FileExistsError):
        export_bundle(store, 'missing', 0, 1, str(target), [], '', '', '')


def test_bundle_contains_hashes_and_metadata(tmp_path):
    import json
    from app.video import export_bundle
    from app.storage import file_hash
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite')
    store.save_source(str(source), 'Creator', 'https://example.com', 'Synthetic source', True)
    target = tmp_path / 'bundle'
    result = export_bundle(store, str(source), 0, .5, str(target), [], 'Hook', 'Title', 'Description')
    provenance = json.loads((target / 'provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_sha256'] == file_hash(source)
    assert provenance['output_sha256'] == file_hash(target / 'clip.mp4')
    assert (target / 'metadata.json').exists()
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM completed_packages').fetchone()[0] == 1


def test_metadata_failure_is_not_recorded_as_completed(tmp_path, monkeypatch):
    import app.video as video
    store = Store(tmp_path / 'db.sqlite')
    def fake_render(store, source, start, end, destination, words, hook, title):
        Path(destination).write_bytes(b'partial')
        return {'job_id': 'example'}
    def fail_metadata(*args): raise OSError('metadata unavailable')
    monkeypatch.setattr(video, 'render', fake_render)
    monkeypatch.setattr(video, 'prepare_package', fail_metadata)
    with pytest.raises(RuntimeError, match='Export incomplete'):
        video.export_bundle(store, 'source', 0, 1, str(tmp_path / 'bundle'), [], '', '', '')
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='completed_packages'").fetchone()[0] == 0


def test_blurred_layout_renders(tmp_path):
    from app.video import render, probe
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite'); store.save_source(str(source), 'Creator', '', 'Synthetic', True)
    output = tmp_path / 'blur.mp4'
    render(store, str(source), 0, .5, str(output), [], '', '', 'blur')
    assert any(s.get('width') == 1080 and s.get('height') == 1920 for s in probe(output)['streams'])
