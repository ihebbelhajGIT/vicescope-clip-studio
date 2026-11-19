import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from app import youtube
from app.acquisition import authorize
from app.provenance import create_job
from app.review import ReviewQueue
from app.storage import Store, file_hash
from app.youtube import API, UPLOAD, YouTubeAccount, YouTubeError
from app.youtube_jobs import YouTubeJobs

CHANNEL = 'UC' + 'a' * 22
VIDEO = 'abcdefghijk'
URL = UPLOAD + '?upload_id=private-session'
METADATA = {'title': 'A clip', 'description': 'Credit', 'hashtags': ['#Shorts']}


class Vault(dict):
    put = dict.__setitem__

    def delete(self, key):
        self.pop(key, None)


def response(status=200, body=None, **headers):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    result._content = json.dumps(body).encode()
    return result


def video(privacy='private', **snippet):
    return {'items': [{'id': VIDEO, 'snippet': {
        'channelId': CHANNEL, 'title': 'A clip', 'description': 'Credit\n\n#Shorts', **snippet},
        'status': {'privacyStatus': privacy, 'uploadStatus': 'processed', 'embeddable': False}}]}


@pytest.fixture
def account(tmp_path):
    path = tmp_path / 'youtube-client.json'
    path.write_text(json.dumps({'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'mock',
                                               'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                                               'token_uri': 'https://oauth2.googleapis.com/token'}}))
    vault = Vault()
    return YouTubeAccount(path, CHANNEL, lambda: vault)


def make_item(queue, directory, content=b'fake MP4', authorized_youtube=False):
    directory.mkdir()
    source = directory / 'source.mp4'
    source.write_bytes(b'source')
    link = 'https://www.youtube.com/watch?v=' + VIDEO if authorized_youtube else ''
    if link:
        authorize(queue.store, link, 'Creator', 'Permission')
    queue.store.save_source(str(source), 'Creator', link, 'Permission', True, VIDEO if link else None)
    output = directory / 'clip.mp4'
    output.write_bytes(content)
    job = create_job(queue.store, str(source), 0, 1)
    output_hash = file_hash(output)
    (directory / 'metadata.json').write_text(json.dumps({
        'job_id': job.id, 'source_sha256': job.source_sha256, 'output_sha256': output_hash,
    }))
    with queue.store.connect() as db:
        db.execute('INSERT INTO exports VALUES (?,?,?)', (job.id, str(output), output_hash))
    return queue.add(job.id, 1, 'Test', METADATA)


@pytest.fixture
def setup(tmp_path, account, monkeypatch):
    queue = ReviewQueue(Store(tmp_path / 'studio.sqlite3'))
    item = make_item(queue, tmp_path / 'package', authorized_youtube=True)
    session = Mock(spec=requests.Session)
    from contextlib import nullcontext
    monkeypatch.setattr(account, 'session', lambda: nullcontext(session))
    return YouTubeJobs(queue, account), item, session


def channel_response(channel=CHANNEL):
    return response(body={'items': [{'id': channel, 'snippet': {'title': 'ViceScope'}}]})


@pytest.mark.parametrize('target', [
    '/?code=x', '/?state=wrong&code=x', '/?state=s&state=s&code=x', '/else?state=s&code=x',
    'https://evil.example/?state=s&code=x', '/?state=s&code=x&code=y', '/?state=s&code=',
])
def test_callback_rejects_invalid_input(target):
    with pytest.raises(ValueError):
        youtube.callback_code(target, 's')


def test_callback_success_and_decline():
    assert youtube.callback_code('/?state=s&code=a%2Bb', 's') == 'a+b'
    with pytest.raises(YouTubeError, match='declined'):
        youtube.callback_code('/?state=s&error=access_denied', 's')


def test_refresh_and_revoke(account, monkeypatch):
    account.vault().put('refresh-token', 'mock-refresh')
    refresh = Mock(return_value=(True, {'access_token': 'mock-access', 'expires_in': 3600}, False))
    monkeypatch.setattr('google.oauth2._client._token_endpoint_request_no_throw', refresh)
    transport = Mock(return_value=channel_response())
    monkeypatch.setattr(requests.Session, 'request', transport)
    assert account.channel() == ('ViceScope', CHANNEL)
    assert refresh.call_args.args[2]['refresh_token'] == 'mock-refresh'
    assert transport.call_args.kwargs['headers']['authorization'] == 'Bearer mock-access'
    revoke = Mock(return_value=response())
    monkeypatch.setattr(youtube.requests, 'post', revoke)
    account.disconnect()
    assert revoke.call_args.kwargs['data'] == {'token': 'mock-refresh'}
    assert not account.vault()
    with pytest.raises(YouTubeError, match='Connect YouTube'), account.session():
        pytest.fail('A disconnected account opened a session')


def test_expired_credentials(account, monkeypatch):
    account.vault().put('refresh-token', 'mock-refresh')
    monkeypatch.setattr('google.oauth2._client._token_endpoint_request_no_throw',
                        Mock(side_effect=youtube.GoogleAuthError('invalid_grant')))
    with pytest.raises(YouTubeError, match='Reconnect'):
        account.channel()


def test_failed_revocation_preserves_token(account, monkeypatch):
    account.vault().put('refresh-token', 'mock-refresh')
    monkeypatch.setattr(youtube.requests, 'post', Mock(return_value=response(503)))
    with pytest.raises(YouTubeError, match='503'):
        account.disconnect()
    assert account.vault()['refresh-token'] == 'mock-refresh'


def test_wrong_channel_does_not_upload(setup):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.return_value = channel_response('UC' + 'b' * 22)
    with pytest.raises(YouTubeError, match='does not match'):
        jobs.upload(item)
    assert session.request.call_count == 1
    assert jobs.record(item) is None


@pytest.mark.parametrize('change', ['unapproved', 'mp4', 'metadata', 'permission', 'source', 'edit'])
def test_requires_current_approval(setup, change):
    jobs, item, session = setup
    if change != 'unapproved':
        jobs.review.decide(item, 'approved_private')
    row = jobs.review.get(item)
    if change == 'mp4':
        Path(row['output_path']).write_bytes(b'changed')
    if change == 'metadata':
        path = Path(row['output_path']).with_name('metadata.json')
        path.write_text(json.dumps({**json.loads(path.read_text()), 'title': 'Changed'}))
    if change == 'permission':
        with jobs.store.connect() as db:
            db.execute('UPDATE youtube_permissions SET approved=0')
    if change == 'source':
        Path(row['source_path']).write_bytes(b'changed')
    if change == 'edit':
        jobs.review.edit(item, {**METADATA, 'title': 'Changed'})
    session.request.return_value = channel_response()
    with pytest.raises(PermissionError):
        jobs.upload(item)
    assert session.request.call_count == 1
    assert jobs.record(item) is None


def upload_responses():
    return [channel_response(), response(Location=URL), response(308),
            response(201, {'id': VIDEO}), response(body=video())]


def test_private_upload_and_duplicate_prevention(setup, tmp_path):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = upload_responses()
    assert jobs.upload(item)['status']['privacyStatus'] == 'private'
    calls = session.request.call_args_list
    assert calls[1].args == ('POST', UPLOAD)
    assert calls[1].kwargs['json']['status'] == {'privacyStatus': 'private'}
    assert calls[3].kwargs['data'] == b'fake MP4'
    assert jobs.record(item)['sent'] == 8
    assert jobs.record(item)['video_id'] == VIDEO
    assert jobs.record(item)['privacy'] == 'private'
    session.request.side_effect = [channel_response(), response(body=video())]
    assert jobs.upload(item)['id'] == VIDEO
    duplicate = make_item(jobs.review, tmp_path / 'duplicate')
    jobs.review.decide(duplicate, 'approved_private')
    session.request.side_effect = [channel_response()]
    with pytest.raises(YouTubeError, match='already reserved'):
        jobs.upload(duplicate)


@pytest.mark.parametrize('resume_status', [308, 201, 404])
def test_interrupted_upload_resumes_or_stays_reserved(setup, resume_status):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = [channel_response(), response(Location=URL), response(308),
                                   requests.ConnectionError('completion unknown')]
    with pytest.raises(YouTubeError, match='interrupted'):
        jobs.upload(item)
    assert jobs.account.vault()['upload-' + item] == URL
    session.request.reset_mock()
    resumed = {308: [response(308, Range='bytes=0-3'), response(201, {'id': VIDEO})],
               201: [response(201, {'id': VIDEO})], 404: [response(404)]}
    session.request.side_effect = [channel_response(), *resumed[resume_status], response(body=video())]
    if resume_status == 404:
        with pytest.raises(YouTubeError, match='remains reserved'):
            jobs.upload(item)
    else:
        assert jobs.upload(item)['id'] == VIDEO
    calls = session.request.call_args_list
    assert calls[1].kwargs['headers']['Content-Range'] == 'bytes */8'
    assert all(call.args[0] != 'POST' for call in calls)
    if resume_status == 308:
        assert calls[2].kwargs['data'] == b' MP4'
        assert calls[2].kwargs['headers']['Content-Range'] == 'bytes 4-7/8'


@pytest.mark.parametrize('status,reason,message', [(403, 'quotaExceeded', 'quota'), (429, 'rateLimitExceeded', 'rate limiting'), (503, 'backendError', '503')])
def test_api_failure(setup, status, reason, message):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = [channel_response(), response(status, {'error': {'errors': [{'reason': reason}]}})]
    with pytest.raises(YouTubeError, match=message):
        jobs.upload(item)
    assert jobs.record(item)['transfer_started'] == 0


@pytest.mark.parametrize('change', ['no confirmation', 'invalidated', 'changed remote', 'wrong channel', 'confirmed'])
def test_publication_requires_current_confirmed_video_and_approval(setup, change):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = upload_responses()
    private = jobs.upload(item)
    session.request.reset_mock()
    if change == 'no confirmation':
        with pytest.raises(PermissionError, match='confirmation'):
            jobs.publish(item, None)
        assert not session.request.called
        return
    if change == 'invalidated':
        jobs.review.decide(item, 'rejected')
    session.request.side_effect = [channel_response('UC' + 'b' * 22 if change == 'wrong channel' else CHANNEL),
                                   response(body=video(title='Edited') if change == 'changed remote' else video()),
                                   response(body={}), channel_response(), response(body=video('public'))]
    if change == 'confirmed':
        assert jobs.publish(item, private)['status']['privacyStatus'] == 'public'
        update = session.request.call_args_list[2]
        assert update.args == ('PUT', API + 'videos')
        assert update.kwargs['json']['status'] == {'privacyStatus': 'public', 'embeddable': False}
        assert jobs.record(item)['privacy'] == 'public'
    else:
        with pytest.raises((PermissionError, YouTubeError)):
            jobs.publish(item, private)
        assert all(call.args[0] == 'GET' for call in session.request.call_args_list)


def test_oauth_desktop_loopback_uses_pkce(account, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import parse_qs, urlencode, urlsplit

    exchange = Mock()
    monkeypatch.setattr(youtube.InstalledAppFlow, 'fetch_token', exchange)
    monkeypatch.setattr(youtube.InstalledAppFlow, 'credentials', property(lambda _: credentials))
    monkeypatch.setattr(requests.Session, 'request', Mock(return_value=channel_response()))
    # Keep the callback's loopback transport independent of the mocked Internet requests.
    from urllib.request import urlopen

    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        assert query['code_challenge_method'] == ['S256']
        redirect = query['redirect_uri'][0]
        assert urlsplit(redirect).hostname == '127.0.0.1'

        def complete():
            query_string = urlencode({'state': query['state'][0], 'code': 'mock-code'})
            with urlopen(redirect + '?' + query_string, timeout=5) as reply:
                assert reply.status == 200
        callbacks.append(pool.submit(complete))
        return True

    # Authorization verification uses an actual Google AuthorizedSession with a fake token.
    from google.oauth2.credentials import Credentials
    credentials = Credentials('mock-access', refresh_token='mock-refresh')
    callbacks = []
    with ThreadPoolExecutor() as pool:
        monkeypatch.setattr(youtube.webbrowser, 'open', open_browser)
        assert account.connect() == ('ViceScope', CHANNEL)
        callbacks[0].result()
    exchange.assert_called_once_with(code='mock-code', timeout=30)
    assert account.vault() == {'refresh-token': 'mock-refresh'}


def test_offscreen_review_actions(tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget
    from app.main import Window

    monkeypatch.setenv('VICESCOPE_DATA_DIR', str(tmp_path / 'app-data'))
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])

    class PreviewStub(QWidget):
        selection_start = Signal(float)
        selection_end = Signal(float)
        position_changed = Signal(int)

        def __init__(self):
            super().__init__()
            self.player = Mock(positionChanged=self.position_changed)

        def load(self, _path):
            pass

    monkeypatch.setattr('app.main.VideoPreview', PreviewStub)
    window = Window()
    details = {'id': VIDEO, 'channel': ('ViceScope', CHANNEL), 'title': 'A clip',
               'description': 'Credit\n\n#Shorts', 'status': {'privacyStatus': 'private'}}
    try:
        panel = window.youtube_panel
        item = make_item(window.review_queue, tmp_path / 'gui-package')
        window.refresh_review()
        window.review_list.setCurrentRow(0)
        assert not panel.upload_button.isEnabled()
        approve = next(b for b in window.findChildren(QPushButton) if b.text() == 'Approve private upload')
        approve.click()
        window.review_list.setCurrentRow(0)
        assert panel.upload_button.isEnabled()

        connect = Mock(return_value=details['channel'])
        monkeypatch.setattr(window.youtube_jobs.account, 'connect', connect)
        disconnect = Mock()
        monkeypatch.setattr(window.youtube_jobs.account, 'disconnect', disconnect)

        def wait_for_worker():
            for _ in range(250):
                QTest.qWait(10)
                if not window.threads:
                    return
            pytest.fail('YouTube GUI worker did not finish')

        accounts = {button.text(): button for button in panel.findChildren(QPushButton)}
        accounts['Connect YouTube'].click()
        wait_for_worker()
        connect.assert_called_once()
        assert CHANNEL in panel.account_status.text()

        def upload(item_id):
            with window.store.connect() as db:
                db.execute('''INSERT INTO youtube_uploads(item_id, channel_id, output_hash, metadata_hash,
                    total, sent, video_id, privacy) VALUES(?,?,?,?,1,1,?,?)''',
                           (item_id, CHANNEL, 'media', 'metadata', VIDEO, 'private'))
            return details

        upload_action = Mock(side_effect=upload)
        monkeypatch.setattr(window.youtube_jobs, 'upload', upload_action)
        publish_action = Mock(return_value={**details, 'status': {'privacyStatus': 'public'}})
        monkeypatch.setattr(window.youtube_jobs, 'publish', publish_action)
        panel.upload_button.click()
        wait_for_worker()
        upload_action.assert_called_once_with(item)
        assert panel.description.toPlainText() == details['description']
        assert panel.link_button.isEnabled()
        assert panel.publish_button.isEnabled()
        publish_action.assert_not_called()

        def press_dialog(label):
            dialog = app.activeModalWidget()
            assert isinstance(dialog, QMessageBox)
            button = next(b for b in dialog.buttons() if b.text() == label)
            button.click()

        QTimer.singleShot(0, lambda: press_dialog('Cancel'))
        panel.publish_button.click()
        publish_action.assert_not_called()
        QTimer.singleShot(0, lambda: press_dialog('Publish Publicly'))
        panel.publish_button.click()
        wait_for_worker()
        publish_action.assert_called_once_with(item, details)
        assert 'public' in panel.video_status.text()
        assert not panel.publish_button.isEnabled()
        accounts['Disconnect YouTube'].click()
        wait_for_worker()
        disconnect.assert_called_once()
        assert 'disconnected' in panel.account_status.text()
    finally:
        window.close()


def test_session_url_cannot_redirect_credentials(setup):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = [channel_response(), response(Location='https://www.googleapis.com.evil.example/?upload_id=x')]
    with pytest.raises(YouTubeError, match='invalid resumable session URL'):
        jobs.upload(item)
    assert session.request.call_count == 2
    assert not jobs.account.vault()


def test_missing_session_does_not_initiate_again(setup):
    jobs, item, session = setup
    jobs.review.decide(item, 'approved_private')
    session.request.side_effect = [channel_response(), response(Location=URL), requests.ConnectionError()]
    with pytest.raises(YouTubeError, match='interrupted'):
        jobs.upload(item)
    jobs.account.vault().clear()
    session.request.reset_mock(side_effect=True)
    session.request.return_value = channel_response()
    with pytest.raises(YouTubeError, match='session is missing'):
        jobs.upload(item)
    assert session.request.call_count == 1


def test_other_instance_cannot_start_concurrent_upload(setup):
    jobs, item, session = setup
    second_instance = YouTubeJobs(jobs.review, jobs.account)
    with jobs.exclusive(), pytest.raises(YouTubeError, match='already running'):
        second_instance.upload(item)
    assert not session.request.called
