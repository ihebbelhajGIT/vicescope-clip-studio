import json
from types import SimpleNamespace

import pytest

from app.acquisition import authorize, collection_url, discover_collection
from app.automation import Automation
from app.storage import Store


def test_collection_url_validation():
    channel = 'UC' + 'x' * 22
    assert collection_url('youtube_channel', channel).endswith('/channel/' + channel + '/videos')
    assert collection_url('youtube_playlist', 'PLabcdefghijk').endswith('list=PLabcdefghijk')
    with pytest.raises(ValueError): collection_url('youtube_channel', 'https://evil.test/@creator')
    with pytest.raises(ValueError): collection_url('youtube_playlist', 'https://youtube.com/watch?v=abcdefghijk')


def test_discovery_filters_active_and_restricted_and_bounds_request():
    entries = [
        {'id':'abcdefghijk','title':'archive','live_status':'was_live'},
        {'id':'abcdefghijL','live_status':'is_live'},
        {'id':'abcdefghijM','live_status':'is_upcoming'},
        {'id':'abcdefghijN','availability':'private'},
        {'id':'abcdefghijO','has_drm':True},
    ]
    calls = []
    def runner(cmd, **kwargs):
        calls.append(cmd); return SimpleNamespace(stdout=json.dumps({'entries':entries}))
    found = discover_collection('youtube_channel', 'UC'+'x'*22, 5, True, runner)
    assert [x['id'] for x in found] == ['abcdefghijk']
    assert '--flat-playlist' in calls[0] and '--skip-download' in calls[0] and '--no-cookies' in calls[0]
    assert calls[0][-1].endswith('/streams') and calls[0][calls[0].index('--playlist-end')+1] == '5'


@pytest.mark.parametrize('kind,value', [('youtube_channel','UC'+'x'*22), ('youtube_playlist','PLabcdefghijk')])
def test_discovery_requires_exact_grant_and_deduplicates(tmp_path, kind, value):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source(kind, value, 'Creator', 'Documented permission')
    fake = lambda *args: [{'id':'abcdefghijk','title':'A recording','live_status':'was_live'}]
    monitor.scan(discoverer=fake); monitor.scan(discoverer=fake)
    job, = monitor.list_jobs()
    assert job['status'] == 'awaiting_authorization'
    assert monitor.process_next(str(tmp_path), downloader=lambda *args: pytest.fail('downloaded without permission')) is None
    authorize(store, 'https://youtu.be/abcdefghijk', 'Creator', 'Exact permission')
    monitor.scan(discoverer=fake)
    assert monitor.list_jobs()[0]['status'] == 'pending'
    with store.connect() as db: db.execute('UPDATE youtube_permissions SET approved=0')
    with pytest.raises(PermissionError):
        monitor.process_next(str(tmp_path), inspector=lambda *args: pytest.fail('metadata checked after revoke'))
    assert len(monitor.list_jobs()) == 1


def test_retry_is_only_allowed_before_generation(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source('youtube_video','https://youtu.be/abcdefghijk','Creator','Permission')
    monitor.scan()
    with pytest.raises(ValueError):
        monitor.process_next(str(tmp_path), inspector=lambda _: (_ for _ in ()).throw(ValueError('restricted')))
    job, = monitor.list_jobs(); assert job['status'] == 'failed' and job['safe_retry'] == 1
    monitor.retry_job(job['id']); assert monitor.list_jobs()[0]['status'] == 'pending'
    with store.connect() as db: db.execute("UPDATE automation_jobs SET status='interrupted',safe_retry=0")
    with pytest.raises(ValueError, match='may have produced clips'): monitor.retry_job(job['id'])


def test_retryable_interrupted_job_recovers_to_manual_retry(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source('youtube_video','https://youtu.be/abcdefghijk','Creator','Permission'); monitor.scan()
    with store.connect() as db: db.execute("UPDATE automation_jobs SET status='processing',safe_retry=1")
    recovered = Automation(store); job, = recovered.list_jobs()
    assert job['status'] == 'interrupted'
    recovered.retry_job(job['id']); assert recovered.list_jobs()[0]['status'] == 'pending'


def test_collection_transient_retry_respects_configured_backoff(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    now = [100]; monitor.clock = lambda: now[0]
    monitor.add_source('youtube_playlist', 'PLabcdefghijk', 'Creator', 'Evidence')
    authorize(store, 'https://youtu.be/abcdefghijk', 'Creator', 'Exact permission')
    fake = lambda *args: [{'id':'abcdefghijk','live_status':'not_live'}]
    monitor.scan(discoverer=fake)
    def fail(*args): raise RuntimeError('connection timeout')
    monitor.process_next(str(tmp_path), inspector=lambda _: {}, downloader=fail, retry_limit=2, retry_backoff=7)
    assert monitor.list_jobs()[0]['next_retry'] == 107
    now[0] = 106; monitor.scan(discoverer=fake); assert monitor.list_jobs()[0]['status'] == 'retry_wait'
    now[0] = 107; monitor.scan(discoverer=fake); assert monitor.list_jobs()[0]['status'] == 'pending'
    monitor.process_next(str(tmp_path), inspector=lambda _: {}, downloader=fail, retry_limit=2, retry_backoff=7)
    assert monitor.list_jobs()[0]['status'] == 'failed'


def test_disk_monitor_requests_cooperative_stop(tmp_path, monkeypatch):
    import time
    from collections import namedtuple
    import app.automation as module
    usage = namedtuple('usage', 'total used free')
    space = [100]
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: usage(100, 100-space[0], space[0]))
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store, settle_seconds=0)
    folder = tmp_path/'input'; folder.mkdir(); video = folder/'source.mp4'; video.write_bytes(b'approved')
    store.save_source(str(video), 'Creator', '', 'Evidence', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Evidence'); monitor.scan(); monitor.scan()
    def generating(*args, **kwargs):
        space[0] = 1
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if kwargs['cancelled'](): raise InterruptedError('cancelled')
            time.sleep(.05)
        pytest.fail('disk monitor did not request cancellation')
    with pytest.raises(OSError, match='disk space'):
        monitor.process_next(str(tmp_path), min_free_bytes=10, generator=generating)
    job, = monitor.list_jobs(); assert job['status'] == 'failed' and job['safe_retry'] == 0
    with pytest.raises(ValueError): monitor.retry_job(job['id'])


def test_channel_scans_uploads_and_streams_and_deduplicates(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source('youtube_channel', 'UC'+'x'*22, 'Creator', 'Evidence')
    calls = []
    def discover(kind, value, maximum, streams):
        calls.append((maximum, streams))
        return ([{'id':'abcdefghijk','title':'upload'}, {'id':'abcdefghijL','title':'both'}]
                if not streams else [{'id':'abcdefghijL','title':'both'}, {'id':'abcdefghijM','title':'archive','live_status':'was_live'}])
    monitor.scan(maximum=4, streams=True, discoverer=discover)
    assert calls == [(4, False), (4, True)]
    assert len(monitor.list_jobs()) == 3
    assert all(j['status'] == 'awaiting_authorization' for j in monitor.list_jobs())
    assert monitor.list_sources()[0]['new_found'] == 3
    again = Automation(store)
    assert again.list_activity()[0]['event'] == 'discovery_completed'


def test_approval_and_retries_survive_aging_out_of_results(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    now = [100]; monitor.clock = lambda: now[0]
    monitor.add_source('youtube_playlist', 'PLabcdefghijk', 'Creator', 'Evidence')
    found = lambda *args: [{'id':'abcdefghijk'}]
    empty = lambda *args: []
    monitor.scan(discoverer=found)
    authorize(store, 'https://youtu.be/abcdefghijk', 'Creator', 'Permission')
    monitor.scan(discoverer=empty)
    assert monitor.list_jobs()[0]['status'] == 'pending'
    def fail(*args): raise RuntimeError('connection timeout')
    monitor.process_next(str(tmp_path), inspector=lambda _: {}, downloader=fail, retry_backoff=7)
    now[0] = 107; monitor.scan(discoverer=empty)
    assert monitor.list_jobs()[0]['status'] == 'pending'
    with store.connect() as db: db.execute('UPDATE youtube_permissions SET approved=0')
    monitor.scan(discoverer=empty)
    assert monitor.list_jobs()[0]['status'] == 'awaiting_authorization'


def test_source_failure_is_resolved_and_later_failure_reappears(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source('youtube_playlist', 'PLabcdefghijk', 'Creator', 'Evidence')
    def fail(*args): raise RuntimeError('Network unavailable')
    monitor.scan(discoverer=fail); assert monitor.list_jobs()[0]['status'] == 'failed'
    monitor.scan(discoverer=lambda *args: []); assert monitor.list_jobs()[0]['status'] == 'resolved'
    monitor.scan(discoverer=fail); assert monitor.list_jobs()[0]['status'] == 'failed'
    assert [x['event'] for x in monitor.list_activity()] == ['discovery_failed', 'discovery_completed', 'discovery_failed']


def test_reconcile_real_synthetic_export_without_duplicate_review(tmp_path):
    import json
    import subprocess
    from pathlib import Path
    from app.autoclip import generate
    from app.review import ReviewQueue
    from app.video import tool_path
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store, settle_seconds=0)
    folder = tmp_path/'input'; folder.mkdir(); source = folder/'source.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=2', '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    store.save_source(str(source), 'Synthetic creator', '', 'Generated test footage', True)
    monitor.add_source('folder', str(folder), 'Synthetic creator', 'Generated test footage')
    monitor.scan(); monitor.scan(); job = monitor.list_jobs()[0]
    with store.connect() as db:
        db.execute("UPDATE automation_jobs SET status='interrupted',path=?,digest=? WHERE id=?", (str(source),store.source(str(source))['sha256'],job['id']))
    items = generate(store,str(source),str(tmp_path),maximum=1,clip_duration=2,transcriber=lambda _: [{'start':.1,'end':.8,'text':'wow'}],automation_job_id=job['id'],cpu_threads=2)
    queue = ReviewQueue(store); item = queue.get(items[0]); output = Path(item['output_path'])
    with store.connect() as db:
        db.execute('DELETE FROM completed_packages WHERE job_id=?', (item['job_id'],))
        db.execute('DELETE FROM review_items WHERE id=?', (items[0],))
    original_media = output.read_bytes()
    output.write_bytes(original_media + b'tampered')
    with pytest.raises(ValueError,match='uncertain'): monitor.reconcile_job(job['id'])
    output.write_bytes(original_media)
    metadata = output.parent/'metadata.json'; original = metadata.read_bytes()
    content = json.loads(original); content['output_sha256'] = '0'*64
    metadata.write_text(json.dumps(content),encoding='utf-8')
    with pytest.raises(ValueError,match='uncertain'): monitor.reconcile_job(job['id'])
    assert queue.list() == [] and monitor.list_jobs()[0]['status'] == 'interrupted'
    metadata.write_bytes(original)
    assert monitor.reconcile_job(job['id']) == 1
    recovered, = queue.list(); assert recovered['status'] == 'ready' and recovered['approved_output_hash'] is None
    queue.validate(recovered); assert monitor.list_jobs()[0]['status'] == 'completed'
    with pytest.raises(ValueError,match='uncertain'): monitor.reconcile_job(job['id'])
    assert len(queue.list()) == 1


def test_reconcile_incomplete_plan_requires_manual_inspection(tmp_path):
    store = Store(tmp_path/'studio.sqlite'); monitor = Automation(store)
    monitor.add_source('youtube_video','https://youtu.be/abcdefghijk','Creator','Evidence'); monitor.scan()
    job = monitor.list_jobs()[0]
    with store.connect() as db:
        db.execute("UPDATE automation_jobs SET status='interrupted' WHERE id=?", (job['id'],))
        db.execute('INSERT INTO automation_plans VALUES(?,?)', (job['id'],2))
    with pytest.raises(ValueError, match='manual'): monitor.reconcile_job(job['id'])
    assert monitor.list_jobs()[0]['status'] == 'interrupted'
