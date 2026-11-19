import subprocess
import pytest
from app.autoclip import generate, non_overlapping
from app.highlights import Candidate
from app.review import ReviewQueue
from app.storage import Store
from app.video import tool_path


def test_non_overlapping_selection():
    candidates = [Candidate(0, 10, .9, 'a'), Candidate(5, 15, .8, 'b'), Candidate(10, 20, .7, 'c')]
    assert non_overlapping(candidates, 3) == [candidates[0], candidates[2]]


def test_automatic_synthetic_review_and_approval(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=2', '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'studio.sqlite')
    fake_speech = lambda _: [{'start': .1, 'end': .8, 'text': 'test'}]
    with pytest.raises(PermissionError): generate(store, str(source), str(tmp_path), transcriber=fake_speech)
    store.save_source(str(source), 'Creator', 'https://example.com', 'Generated source', True)
    ids = generate(store, str(source), str(tmp_path), transcriber=fake_speech)
    assert len(ids) == 1
    queue = ReviewQueue(Store(tmp_path / 'studio.sqlite'))
    item = queue.get(ids[0]); assert item['status'] == 'ready'
    queue.decide(ids[0], 'approved_private'); assert queue.is_approved(ids[0])
    queue.edit(ids[0], {'title': 'Revised', 'description': '', 'hashtags': ['#Shorts']})
    assert not queue.is_approved(ids[0])
    queue.decide(ids[0], 'approved_private'); assert queue.is_approved(ids[0])
    queue.decide(ids[0], 'rejected'); assert not queue.is_approved(ids[0])
    with pytest.raises(PermissionError): queue.decide(ids[0], 'approved_private')
    with pytest.raises(ValueError): queue.edit(ids[0], {'title': 'x', 'description': '', 'hashtags': []})


def test_output_change_invalidates_approval(tmp_path):
    from app.video import export_bundle
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'studio.sqlite'); store.save_source(str(source), 'Creator', '', 'Synthetic', True)
    result = export_bundle(store, str(source), 0, .5, str(tmp_path / 'bundle'), [], '', 'Title', '')
    queue = ReviewQueue(store); item = queue.add(result['job_id'], .5, 'audio', {'title':'T', 'description':'', 'hashtags':[]})
    queue.decide(item, 'approved_private'); assert queue.is_approved(item)
    (tmp_path / 'bundle' / 'clip.mp4').write_bytes(b'changed')
    assert not queue.is_approved(item)
    with pytest.raises(PermissionError): queue.decide(item, 'approved_private')
