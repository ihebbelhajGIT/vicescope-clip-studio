"""Bind private uploads and interactive publication to the reviewed export."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock, Timeout

from app.youtube import API, UPLOAD, VIDEO_ID, YouTubeError, api_json, request, response_error

CHUNK = 8 * 1024 * 1024


def metadata_for_upload(item):
    metadata = json.loads(item['metadata'])
    title = metadata['title']
    description = metadata['description']
    if metadata['hashtags']:
        description = (description + '\n\n' + ' '.join(metadata['hashtags'])).lstrip('\n')
    if not title.strip() or len(title) > 100 or len(description.encode('utf-8')) > 5000:
        raise YouTubeError('YouTube requires a title of 1–100 characters and a description of at most 5,000 bytes.')
    if any(char in title or char in description for char in '<>'):
        raise YouTubeError('YouTube title and description cannot contain angle brackets.')
    return title, description


def session_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.netloc not in ('www.googleapis.com', 'youtube.googleapis.com')
            or parts.path != '/upload/youtube/v3/videos' or not parts.query or parts.fragment):
        raise YouTubeError('YouTube returned an invalid resumable session URL.')
    return url


class YouTubeJobs:
    def __init__(self, review, account):
        self.review = review
        self.store = review.store
        self.account = account
        self.lock = FileLock(str(self.store.path) + '.youtube.lock', timeout=0)
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS youtube_uploads (
                item_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                output_hash TEXT NOT NULL, metadata_hash TEXT NOT NULL,
                total INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                transfer_started INTEGER NOT NULL DEFAULT 0,
                video_id TEXT UNIQUE, privacy TEXT,
                UNIQUE(channel_id, output_hash))''')

    @contextmanager
    def exclusive(self):
        try:
            with self.lock:
                yield
        except Timeout:
            raise YouTubeError('Another YouTube operation is already running.') from None

    def account_action(self, action):
        with self.exclusive():
            return action()

    def record(self, item_id):
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM youtube_uploads WHERE item_id=?', (item_id,)).fetchone()
            return dict(row) if row else None

    def approved(self, item_id):
        item = self.review.require_private_approval(item_id)
        return item, (item['approved_output_hash'], item['approved_metadata_hash'])

    def bind(self, row, channel_id, hashes):
        if (row['channel_id'], row['output_hash'], row['metadata_hash']) != (channel_id, *hashes):
            raise YouTubeError('The current channel or approval differs from the reserved upload.')

    def upload(self, item_id):
        with self.exclusive(), self.account.session() as session:
            channel = self.account.verify(session)
            item, hashes = self.approved(item_id)
            title, description = metadata_for_upload(item)
            digest = hashlib.sha256()
            with tempfile.TemporaryFile() as snapshot, Path(item['output_path']).open('rb') as source:
                for chunk in iter(lambda: source.read(CHUNK), b''):
                    digest.update(chunk)
                    snapshot.write(chunk)
                size = snapshot.tell()
                if not size or digest.hexdigest() != hashes[0]:
                    raise PermissionError('MP4 changed while preparing the upload.')
                return self.transfer(session, channel, item_id, hashes, title, description, snapshot, size)

    def transfer(self, session, channel, item_id, hashes, title, description, snapshot, size):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.approved(item_id)[1] != hashes:
                raise PermissionError('Review approval changed while preparing the upload.')
            try:
                db.execute('''INSERT INTO youtube_uploads(item_id,channel_id,output_hash,metadata_hash,total)
                    VALUES (?,?,?,?,?) ON CONFLICT(item_id) DO NOTHING''', (item_id, channel[1], *hashes, size))
            except sqlite3.IntegrityError:
                raise YouTubeError('This exact video is already reserved for this YouTube channel.') from None
        row = self.record(item_id)
        self.bind(row, channel[1], hashes)
        if row['video_id']:
            return self.remember_privacy(item_id, self.read_video(session, channel, row['video_id']))
        vault = self.account.vault()
        key = 'upload-' + item_id
        url = vault.get(key)
        if not url:
            if row['transfer_started']:
                raise YouTubeError('The resumable session is missing. Check YouTube Studio before taking further action.')
            response = request(session, 'POST', UPLOAD, params={'uploadType': 'resumable', 'part': 'snippet,status'},
                               headers={'X-Upload-Content-Type': 'video/mp4', 'X-Upload-Content-Length': str(size)},
                               json={'snippet': {'title': title, 'description': description, 'categoryId': '22'},
                                     'status': {'privacyStatus': 'private'}})
            if response.status_code not in (200, 201):
                raise response_error(response)
            url = session_url(response.headers.get('Location', ''))
            vault.put(key, url)
        url = session_url(url)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.approved(item_id)[1] != hashes:
                raise PermissionError('Review approval changed before transfer.')
            db.execute('UPDATE youtube_uploads SET transfer_started=1 WHERE item_id=?', (item_id,))
        response = request(session, 'PUT', url, headers={'Content-Range': f'bytes */{size}'}, data=b'')
        while response.status_code == 308:
            confirmed = response.headers.get('Range')
            match = re.fullmatch(r'bytes=0-([0-9]+)', confirmed or '')
            if confirmed and not match:
                raise YouTubeError('YouTube returned an invalid upload offset.')
            offset = int(match[1]) + 1 if match else 0
            if offset >= size or offset < row['sent']:
                raise YouTubeError('YouTube returned an inconsistent upload offset. Check YouTube Studio.')
            with self.store.connect() as db:
                db.execute('UPDATE youtube_uploads SET sent=? WHERE item_id=?', (offset, item_id))
            if not self.review.approval_matches(item_id, hashes):
                raise PermissionError('Review approval changed during transfer.')
            snapshot.seek(offset)
            chunk = snapshot.read(CHUNK)
            response = request(session, 'PUT', url, data=chunk, headers={
                'Content-Type': 'video/mp4', 'Content-Range': f'bytes {offset}-{offset + len(chunk) - 1}/{size}'})
            row['sent'] = offset
            if response.status_code == 308 and response.headers.get('Range') == confirmed:
                raise YouTubeError('YouTube made no upload progress. Retry the same upload.')
        if response.status_code in (404, 410):
            raise YouTubeError('The resumable session expired. Check YouTube Studio for the video. This job remains reserved.')
        if response.status_code not in (200, 201):
            raise response_error(response)
        try:
            video_id = response.json()['id']
            if not VIDEO_ID.fullmatch(video_id):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise YouTubeError('Upload result is uncertain. Retry the same upload to recover the video ID.') from None
        with self.store.connect() as db:
            db.execute('UPDATE youtube_uploads SET sent=total, video_id=? WHERE item_id=?', (video_id, item_id))
        vault.delete(key)
        return self.remember_privacy(item_id, self.read_video(session, channel, video_id))

    def read_video(self, session, channel, video_id):
        data = api_json(session, 'GET', API + 'videos', params={'part': 'snippet,status', 'id': video_id})
        videos = data.get('items', [])
        if len(videos) != 1 or videos[0].get('id') != video_id or videos[0]['snippet'].get('channelId') != channel[1]:
            raise YouTubeError('The uploaded video is unavailable on the verified ViceScope channel.')
        video = videos[0]
        return {'id': video_id, 'channel': channel, 'title': video['snippet']['title'],
                'description': video['snippet']['description'], 'status': video['status'], 'etag': video.get('etag')}

    def _preview(self, session, item_id):
        channel = self.account.verify(session)
        row = self.record(item_id)
        if not row or not row['video_id'] or row['channel_id'] != channel[1]:
            raise YouTubeError('An uploaded video on the verified ViceScope channel is required.')
        return self.read_video(session, channel, row['video_id'])

    def remember_privacy(self, item_id, video):
        with self.store.connect() as db:
            db.execute('UPDATE youtube_uploads SET privacy=? WHERE item_id=?',
                       (video['status']['privacyStatus'], item_id))
        return video

    def preview(self, item_id):
        with self.exclusive(), self.account.session() as session:
            return self.remember_privacy(item_id, self._preview(session, item_id))

    def publish(self, item_id, confirmed_video):
        if not confirmed_video or confirmed_video['status']['privacyStatus'] != 'private':
            raise PermissionError('Explicit publication confirmation is required for a private video.')
        with self.exclusive(), self.account.session() as session:
            video = self._preview(session, item_id)
            if video != confirmed_video:
                raise YouTubeError('The YouTube video changed. Refresh its details and confirm again.')
            status = video['status']
            if status.get('publishAt') or status.get('uploadStatus') not in ('processed', 'uploaded'):
                raise YouTubeError('Resolve the video processing or schedule status in YouTube Studio first.')
            update = {key: status[key] for key in ('embeddable', 'license', 'publicStatsViewable', 'selfDeclaredMadeForKids') if key in status}
            update['privacyStatus'] = 'public'
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                item, hashes = self.approved(item_id)
                self.bind(self.record(item_id), video['channel'][1], hashes)
                if metadata_for_upload(item) != (video['title'], video['description']):
                    raise PermissionError('YouTube metadata differs from the approved review.')
                headers = {'If-Match': video['etag']} if video['etag'] else {}
                api_json(session, 'PUT', API + 'videos', params={'part': 'status'}, headers=headers,
                         json={'id': video['id'], 'status': update})
            result = self.remember_privacy(item_id, self._preview(session, item_id))
            if result['status']['privacyStatus'] != 'public':
                raise YouTubeError('YouTube has not confirmed public visibility. Check project audit status and YouTube Studio.')
            return result
