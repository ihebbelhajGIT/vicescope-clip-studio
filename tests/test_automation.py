from pathlib import Path
import pytest
from app.automation import Automation
from app.storage import Store


def engine(tmp_path):
    store = Store(tmp_path / 'studio.sqlite')
    return store, Automation(store, settle_seconds=0)


def test_local_folder_requires_stability_and_exact_file_permission(tmp_path):
    store, monitor = engine(tmp_path)
    now = [100.0]
    monitor.clock = lambda: now[0]
    monitor.settle_seconds = 10
    folder = tmp_path / 'incoming'; folder.mkdir()
    video = folder / 'clip.mp4'; video.write_bytes(b'first')
    monitor.add_source('folder', str(folder), 'Creator', 'Folder permission')
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'observing'
    video.write_bytes(b'file still growing')
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'observing'
    now[0] += 9
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'observing'
    now[0] += 1
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'awaiting_authorization'
    calls = []
    assert monitor.process_next(str(tmp_path), generator=lambda *a, **k: calls.append(a)) is None
    assert not calls
    store.save_source(str(video), 'Creator', '', 'Exact video approval', True)
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'pending'
    result = monitor.process_next(str(tmp_path), generator=lambda *a, **k: ['unapproved-review-id'])
    assert result == ['unapproved-review-id'] and monitor.list_jobs()[0]['status'] == 'completed'
    monitor.scan(); assert monitor.process_next(str(tmp_path), generator=lambda *a, **k: calls.append(a)) is None
    assert not calls


def test_revoked_permission_cannot_start_processing(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    video = folder / 'clip.mp4'; video.write_bytes(b'video')
    store.save_source(str(video), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'pending'
    store.save_source(str(video), 'Creator', '', 'Permission', False)
    called = []
    with pytest.raises(PermissionError): monitor.process_next(str(tmp_path), generator=lambda *a, **k: called.append(a))
    assert not called and monitor.list_jobs()[0]['status'] == 'failed'


def test_identical_local_content_is_not_processed_twice(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    for name in ('one.mp4', 'two.mp4'):
        path = folder / name; path.write_bytes(b'same content')
        store.save_source(str(path), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    assert sorted(j['status'] for j in monitor.list_jobs()) == ['duplicate', 'pending']
    assert len(monitor.process_next(str(tmp_path), generator=lambda *a, **k: ['review'])) == 1
    assert monitor.process_next(str(tmp_path), generator=lambda *a, **k: pytest.fail('duplicate processed')) is None


def test_youtube_permission_revoked_before_scan_never_downloads(tmp_path):
    store, monitor = engine(tmp_path)
    monitor.add_source('youtube_video', 'https://youtu.be/abcdefghijk', 'Creator', 'Permission')
    with store.connect() as db: db.execute('UPDATE youtube_permissions SET approved=0')
    monitor.scan(); assert monitor.list_jobs()[0]['status'] == 'awaiting_authorization'
    assert monitor.process_next(str(tmp_path), downloader=lambda *a: pytest.fail('unauthorized download')) is None


def test_interrupted_jobs_are_not_retried_automatically(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    video = folder / 'clip.mp4'; video.write_bytes(b'video')
    store.save_source(str(video), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    with pytest.raises(InterruptedError):
        monitor.process_next(str(tmp_path), generator=lambda *a, **k: (_ for _ in ()).throw(InterruptedError('stop')))
    assert monitor.list_jobs()[0]['status'] == 'interrupted'
    monitor.scan(); assert monitor.process_next(str(tmp_path), generator=lambda *a, **k: pytest.fail('retried')) is None
    assert Automation(Store(tmp_path / 'studio.sqlite')).list_jobs()[0]['status'] == 'interrupted'


def test_settings_and_source_disable_persist(tmp_path):
    store, monitor = engine(tmp_path)
    source = monitor.add_source('folder', str(tmp_path), 'Creator', 'Permission')
    monitor.set_enabled(source, False); monitor.save_settings({'interval': 30, 'generate': False})
    again = Automation(Store(tmp_path / 'studio.sqlite'))
    assert not again.list_sources()[0]['enabled']
    assert again.settings() == {'interval': 30, 'generate': False}
    again.scan(); assert again.list_jobs() == []


def test_restart_marks_processing_interrupted_and_active_lock_is_respected(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    video = folder / 'clip.mp4'; video.write_bytes(b'video')
    store.save_source(str(video), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    with store.connect() as db: db.execute("UPDATE automation_jobs SET status='processing'")
    with monitor.lock:
        other = Automation(store, settle_seconds=0)
        assert other.list_jobs()[0]['status'] == 'processing'
        with pytest.raises(RuntimeError, match='already running'): other.process_next(str(tmp_path))
    recovered = Automation(store, settle_seconds=0)
    assert recovered.list_jobs()[0]['status'] == 'interrupted'


def test_synthetic_automatic_render_enters_review_unapproved(tmp_path):
    import subprocess
    from app.autoclip import generate
    from app.review import ReviewQueue
    from app.video import tool_path
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    video = folder / 'synthetic.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=2', '-f', 'lavfi', '-i', 'sine=duration=2', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)], check=True)
    store.save_source(str(video), 'Synthetic creator', '', 'Generated media', True)
    monitor.add_source('folder', str(folder), 'Synthetic creator', 'Generated media')
    monitor.scan(); monitor.scan()
    def run_generator(store, source, output, **options):
        return generate(store, source, output, transcriber=lambda _: [{'start': .1, 'end': .8, 'text': 'wow'}], **options)
    results = monitor.process_next(str(tmp_path), maximum=1, clip_duration=2, generator=run_generator, cpu_threads=2)
    review = ReviewQueue(store); item = review.get(results[0])
    assert item['status'] == 'ready' and not review.is_approved(item['id'])
    review.validate(item)
    assert Path(item['output_path']).is_file() and monitor.list_jobs()[0]['status'] == 'completed'


def test_transient_download_retries_are_bounded_and_backed_off(tmp_path):
    store, monitor = engine(tmp_path)
    now = [100.0]; monitor.clock = lambda: now[0]
    monitor.add_source('youtube_video', 'https://youtu.be/abcdefghijk', 'Creator', 'Permission')
    calls = []
    def fail_download(*args):
        calls.append(args)
        raise RuntimeError('YouTube import timed out')
    monitor.scan(); monitor.process_next(str(tmp_path), downloader=fail_download, inspector=lambda _: {'live_status':'not_live'})
    job = monitor.list_jobs()[0]
    assert job['status'] == 'retry_wait' and job['attempts'] == 1 and job['next_retry'] == 130
    now[0] = 129; monitor.scan(); assert monitor.process_next(str(tmp_path), downloader=fail_download, inspector=lambda _: {'live_status':'not_live'}) is None
    assert len(calls) == 1
    now[0] = 130; monitor.scan(); monitor.process_next(str(tmp_path), downloader=fail_download, inspector=lambda _: {'live_status':'not_live'})
    assert monitor.list_jobs()[0]['next_retry'] == 190
    now[0] = 190; monitor.scan(); monitor.process_next(str(tmp_path), downloader=fail_download, inspector=lambda _: {'live_status':'not_live'})
    assert monitor.list_jobs()[0]['status'] == 'failed' and len(calls) == 3
    now[0] = 1000; monitor.scan(); assert monitor.process_next(str(tmp_path), downloader=fail_download, inspector=lambda _: {'live_status':'not_live'}) is None
    assert len(calls) == 3


def test_replaced_file_preserves_history_and_requires_new_permission(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    source = folder / 'clip.mp4'; source.write_bytes(b'first version')
    store.save_source(str(source), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    monitor.process_next(str(tmp_path), generator=lambda *a, **k: ['old-review'])
    source.write_bytes(b'second version with different size')
    monitor.scan(); monitor.scan()
    assert sorted(item['status'] for item in monitor.list_jobs()) == ['awaiting_authorization', 'completed']
    store.save_source(str(source), 'Creator', '', 'New permission', True)
    monitor.scan()
    assert sorted(item['status'] for item in monitor.list_jobs()) == ['completed', 'pending']


def test_disk_preflight_leaves_pending_job_untouched(tmp_path, monkeypatch):
    from collections import namedtuple
    import app.automation as module
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    source = folder / 'clip.mp4'; source.write_bytes(b'approved')
    store.save_source(str(source), 'Creator', '', 'Permission', True)
    monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: usage(100, 99, 1))
    with pytest.raises(OSError, match='free space'):
        monitor.process_next(str(tmp_path), min_free_bytes=10, generator=lambda *a, **k: pytest.fail('processed'))
    assert monitor.list_jobs()[0]['status'] == 'pending'


def test_disabling_source_is_seen_by_active_generation(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    source = folder / 'clip.mp4'; source.write_bytes(b'approved')
    store.save_source(str(source), 'Creator', '', 'Permission', True)
    source_id = monitor.add_source('folder', str(folder), 'Creator', 'Permission')
    monitor.scan(); monitor.scan()
    def generator(*args, **kwargs):
        monitor.set_enabled(source_id, False)
        assert kwargs['cancelled']()
        raise InterruptedError('source disabled')
    with pytest.raises(InterruptedError): monitor.process_next(str(tmp_path), generator=generator)
    assert monitor.list_jobs()[0]['status'] == 'interrupted'


def test_explicit_folder_scope_authorizes_new_file_but_not_a_denied_record(tmp_path):
    from app.core import require_rights
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir()
    allowed = folder / 'new.mp4'; allowed.write_bytes(b'new file')
    denied = folder / 'denied.mp4'; denied.write_bytes(b'denied')
    store.save_source(str(denied), 'Creator', '', 'Revoked', False)
    source_id = monitor.add_source('folder', str(folder), 'Creator', 'Folder-wide permission', auto_approve=True)
    monitor.scan(); monitor.scan()
    assert store.source(str(allowed))['automation_source_id'] == source_id
    assert require_rights(store, str(allowed))['approved'] == 1
    assert store.source(str(denied))['approved'] == 0
    assert sorted(row['status'] for row in monitor.list_jobs()) == ['awaiting_authorization', 'pending']
    monitor.set_enabled(source_id, False)
    with pytest.raises(PermissionError, match='Folder permission'): require_rights(store, str(allowed))
    monitor.set_enabled(source_id, True)
    assert require_rights(store, str(allowed))['approved'] == 1


def test_disabling_folder_grant_invalidates_review_approval(tmp_path):
    import subprocess
    from app.review import ReviewQueue
    from app.video import export_bundle, tool_path
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir(); source = folder / 'clip.mp4'
    subprocess.run([tool_path('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=s=160x90:r=30:d=1', '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True)
    source_id = monitor.add_source('folder', str(folder), 'Creator', 'Folder-wide permission', auto_approve=True)
    monitor.scan(); monitor.scan()
    result = export_bundle(store, str(source), 0, .5, str(tmp_path/'bundle'), [], '', 'Title', '')
    review = ReviewQueue(store); item = review.add(result['job_id'], .5, 'audio', {'title':'Title','description':'','hashtags':[]})
    review.decide(item, 'approved_private'); assert review.is_approved(item)
    monitor.set_enabled(source_id, False); assert not review.is_approved(item)
    monitor.set_enabled(source_id, True); assert not review.is_approved(item) and review.get(item)['status']=='ready'


def test_disabled_folder_scope_cannot_create_file_permission(tmp_path):
    store, monitor = engine(tmp_path)
    folder = tmp_path / 'incoming'; folder.mkdir(); source = folder / 'clip.mp4'; source.write_bytes(b'video')
    source_id = monitor.add_source('folder', str(folder), 'Creator', 'Permission', auto_approve=True)
    monitor.set_enabled(source_id, False)
    with pytest.raises(PermissionError):
        store.save_source(str(source), 'Creator', '', 'Permission', True, automation_source_id=source_id, only_if_new=True)
    assert store.source(str(source)) is None


@pytest.mark.parametrize('status,allowed', [('not_live', True), ('was_live', True), ('is_live', False), ('is_upcoming', False), ('post_live', False), (None, False)])
def test_video_metadata_rejects_live_and_unknown_status(status, allowed):
    import json
    from types import SimpleNamespace
    from app.acquisition import inspect_completed_video
    calls = []
    def runner(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=json.dumps({'id':'abcdefghijk', 'live_status':status, 'is_live':False}))
    if allowed:
        assert inspect_completed_video('https://youtu.be/abcdefghijk', runner)['live_status'] == status
    else:
        with pytest.raises(ValueError): inspect_completed_video('https://youtu.be/abcdefghijk', runner)
    assert '--skip-download' in calls[0] and '--no-cookies' in calls[0]


def test_revoked_video_permission_blocks_metadata_and_download(tmp_path):
    store, monitor = engine(tmp_path)
    monitor.add_source('youtube_video', 'https://youtu.be/abcdefghijk', 'Creator', 'Permission')
    monitor.scan()
    with store.connect() as db: db.execute('UPDATE youtube_permissions SET approved=0')
    with pytest.raises(PermissionError):
        monitor.process_next(str(tmp_path), inspector=lambda _: pytest.fail('metadata fetched'), downloader=lambda *a: pytest.fail('downloaded'))
