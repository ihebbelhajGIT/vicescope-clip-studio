import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.acquisition import acquire, authorize, youtube_id
from app.review import ReviewQueue
from app.storage import Store
from app.video import export_bundle, tool_path


@pytest.mark.parametrize('url', ['http://youtube.com/watch?v=abcdefghijk', 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://youtube.com/watch?v=abcdefghijk&v=zzzzzzzzzzz', 'https://youtu.be/too-short', 'https://user@youtube.com/watch?v=abcdefghijk'])
def test_invalid_urls_rejected(url):
    with pytest.raises(ValueError): youtube_id(url)


def test_exact_video_authorization_before_acquisition(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    url = 'https://www.youtube.com/watch?v=abcdefghijk'
    calls = []
    def runner(cmd, **kwargs):
        calls.append(cmd)
        output = Path(cmd[cmd.index('-o')+1].replace('%(ext)s', 'mp4'))
        subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(output)], check=True)
        return SimpleNamespace(stdout=str(output)+'\n')
    with pytest.raises(PermissionError): acquire(store, url, str(tmp_path), runner)
    assert not calls
    authorize(store, url, 'Creator', 'Written permission')
    with pytest.raises(PermissionError): acquire(store, 'https://youtu.be/zzzzzzzzzzz', str(tmp_path), runner)
    result = acquire(store, url, str(tmp_path), runner)
    assert store.source(result)['approved'] == 1
    assert calls[0][-1] == url


def test_metadata_synchronized_and_tampering_blocks_approval(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite'); store.save_source(str(source), 'Creator', '', 'Synthetic', True)
    result = export_bundle(store, str(source), 0, .5, str(tmp_path / 'bundle'), [], '', 'Title', '')
    queue = ReviewQueue(store)
    item = queue.add(result['job_id'], .5, 'audio', {'title': 'First', 'description': '', 'hashtags': ['#Shorts']})
    queue.edit(item, {'title': 'Edited', 'description': 'New', 'hashtags': ['#Test']})
    path = tmp_path / 'bundle' / 'metadata.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert (data['title'], data['description'], data['hashtags']) == ('Edited', 'New', ['#Test'])
    queue.decide(item, 'approved_private'); assert queue.is_approved(item)
    data['title'] = 'Changed externally'; path.write_text(json.dumps(data), encoding='utf-8')
    assert not queue.is_approved(item)
    with pytest.raises(PermissionError): queue.decide(item, 'approved_private')


def test_regeneration_creates_new_unapproved_version(tmp_path):
    from app.autoclip import generate, regenerate
    source = tmp_path / 'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite'); store.save_source(str(source), 'Creator', '', 'Synthetic', True)
    speech = lambda _: [{'start': .1, 'end': .8, 'text': 'wow'}]
    old = generate(store, str(source), str(tmp_path), transcriber=speech)[0]
    queue = ReviewQueue(store); queue.decide(old, 'approved_private'); assert queue.is_approved(old)
    new = regenerate(store, old, 'New hook', transcriber=speech)
    queue = ReviewQueue(store)
    assert queue.get(old)['status'] == 'superseded' and not queue.is_approved(old)
    assert queue.get(new)['parent_item_id'] == old and queue.get(new)['status'] == 'ready'
    with pytest.raises(PermissionError): queue.decide(old, 'approved_private')


def test_reactions_and_scene_changes_affect_scores():
    from app.highlights import rank_candidates
    words = [{'start': 1, 'text': 'no'}, {'start': 1.1, 'text': 'way'}]
    result = rank_candidates(20, [(1, .5)], [2], words, window=20)
    assert 'reaction phrases' in result[0].explanation
    assert 'scene changes' in result[0].explanation


def test_scene_change_detected_in_generated_video(tmp_path):
    from app.highlights import analyze_audio
    source = tmp_path / 'scenes.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=black:s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'color=white:s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=2', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store = Store(tmp_path / 'db.sqlite'); store.save_source(str(source), 'Creator', '', 'Synthetic', True)
    assert 'scene changes' in analyze_audio(store, str(source), [])[0].explanation
