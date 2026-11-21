"""Acquisition of specifically authorized YouTube videos."""
from __future__ import annotations

import re
import json
import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from app.platform_support import process_options, ytdlp_command
from app.video import probe, tool_path
from app.core import require_rights


def youtube_id(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        if parts.scheme != 'https' or parts.username or parts.password or parts.port:
            raise ValueError()
        host = (parts.hostname or '').lower()
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if parts.path == '/watch':
                values = parse_qs(parts.query).get('v', [])
                value = values[0] if len(values) == 1 else ''
            elif parts.path.startswith(('/shorts/', '/live/')):
                value = parts.path.split('/')[2]
            else: value = ''
        elif host == 'youtu.be':
            value = parts.path.removeprefix('/')
        else: value = ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value): raise ValueError()
        return value
    except ValueError:
        raise ValueError('Enter a valid HTTPS YouTube video URL') from None


def authorize(store, url: str, creator: str, evidence: str):
    video_id = youtube_id(url)
    if not creator.strip() or not evidence.strip():
        raise ValueError('Creator and permission evidence are required')
    with store.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS youtube_permissions (
            video_id TEXT PRIMARY KEY, creator TEXT NOT NULL, evidence TEXT NOT NULL, approved INTEGER NOT NULL)''')
        previous = db.execute('SELECT creator,evidence,approved FROM youtube_permissions WHERE video_id=?', (video_id,)).fetchone()
        if previous and previous != (creator.strip(), evidence.strip(), 1):
            source_url = 'https://www.youtube.com/watch?v=' + video_id
            has_review = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_items'").fetchone()
            if has_review:
                db.execute("""UPDATE review_items SET status='ready', approved_output_hash=NULL, approved_metadata_hash=NULL
                    WHERE status='approved_private' AND job_id IN (SELECT j.id FROM clip_jobs j JOIN sources s ON j.source_id=s.id WHERE s.source_url=?)""", (source_url,))
            db.execute('UPDATE sources SET approved=0 WHERE source_url=?', (source_url,))
        db.execute('INSERT INTO youtube_permissions VALUES(?,?,?,1) ON CONFLICT(video_id) DO UPDATE SET creator=excluded.creator,evidence=excluded.evidence,approved=1', (video_id, creator.strip(), evidence.strip()))
    return video_id


def inspect_completed_video(url: str, runner=subprocess.run):
    """Read public metadata only for an already authorized exact video."""
    video_id = youtube_id(url)
    canonical_url = 'https://www.youtube.com/watch?v=' + video_id
    cmd = ytdlp_command('--ignore-config', '--no-cookies', '--no-playlist', '--skip-download', '--dump-single-json', canonical_url)
    try:
        result = runner(cmd, capture_output=True, check=True, timeout=90, **process_options(bundled_child=True))
        info = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('YouTube metadata request timed out') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('YouTube metadata is unavailable; check access and connection') from exc
    except (ValueError, TypeError) as exc:
        raise RuntimeError('YouTube returned invalid video metadata') from exc
    if not isinstance(info, dict) or info.get('id') != video_id:
        raise ValueError('YouTube metadata does not match the authorized video')
    if info.get('has_drm') or info.get('availability') in ('private', 'needs_auth', 'premium_only', 'subscriber_only'):
        raise PermissionError('Video is restricted or requires authentication')
    if info.get('is_live') or info.get('live_status') not in ('not_live', 'was_live'):
        raise ValueError('Video is live, upcoming, or its completed status is unknown')
    return {'video_id': video_id, 'live_status': info['live_status']}


def acquire(store, url: str, root: str, runner=subprocess.run):
    video_id = youtube_id(url)
    with store.connect() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='youtube_permissions'").fetchone()
        rights = db.execute('SELECT creator,evidence FROM youtube_permissions WHERE video_id=? AND approved=1', (video_id,)).fetchone() if exists else None
    if not rights: raise PermissionError('Explicit permission for this exact video is required before download')
    root_path = Path(root).resolve(strict=True)
    if not root_path.is_dir(): raise ValueError('Choose an existing download folder')
    canonical_url = 'https://www.youtube.com/watch?v=' + video_id
    with store.connect() as db:
        existing = db.execute('SELECT path FROM sources WHERE source_url=? AND creator=? AND permission_evidence=? AND approved=1', (canonical_url, *rights)).fetchall()
    for (path,) in existing:
        try:
            require_rights(store, path)
            return path
        except (OSError, PermissionError):
            pass
    directory = root_path / ('source-' + uuid.uuid4().hex)
    directory.mkdir()
    cmd = ytdlp_command('--ignore-config', '--no-cookies', '--no-playlist', '--no-cache-dir', '--no-progress', '--no-write-info-json', '--ffmpeg-location', tool_path('ffmpeg'), '-f', 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b', '--merge-output-format', 'mp4', '-o', str(directory / 'source.%(ext)s'), '--print', 'after_move:filepath', canonical_url)
    try:
        result = runner(cmd, capture_output=True, check=True, timeout=7200, **process_options(bundled_child=True))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or '').strip()[-600:]
        raise RuntimeError('YouTube import failed. The video may be unavailable, restricted, or unsupported. ' + detail) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('YouTube import timed out. Check the connection and try again.') from exc
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines: raise RuntimeError('Download did not report a completed file')
    media = Path(lines[-1]).resolve(strict=True)
    if media.parent != directory or not media.is_file(): raise RuntimeError('Unexpected download output path')
    probe(media)
    with store.connect() as db:
        current = db.execute('SELECT creator,evidence FROM youtube_permissions WHERE video_id=? AND approved=1', (video_id,)).fetchone()
    if current != rights: raise PermissionError('Video permission changed during download')
    store.save_source(str(media), rights[0], canonical_url, rights[1], True, youtube_video_id=video_id)
    return str(media)


def collection_url(kind: str, value: str) -> str:
    """Validate a public YouTube collection locator without following arbitrary URLs."""
    value = value.strip()
    if kind == 'youtube_channel' and re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value):
        return 'https://www.youtube.com/channel/' + value + '/videos'
    if kind == 'youtube_playlist' and re.fullmatch(r'[A-Za-z0-9_-]{10,100}', value):
        return 'https://www.youtube.com/playlist?list=' + value
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.username or parts.password or parts.port or (parts.hostname or '').lower() not in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        raise ValueError('Enter a valid HTTPS YouTube channel or playlist URL')
    if kind == 'youtube_channel':
        match = re.fullmatch(r'/(channel/UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9_.-]{3,100})(?:/(?:videos|streams))?/?', parts.path)
        if not match: raise ValueError('Enter a YouTube channel ID or channel URL')
        return 'https://www.youtube.com/' + match.group(1) + '/videos'
    if kind == 'youtube_playlist' and parts.path == '/playlist':
        ids = parse_qs(parts.query).get('list', [])
        if len(ids) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{10,100}', ids[0]):
            return 'https://www.youtube.com/playlist?list=' + ids[0]
    raise ValueError('Enter a YouTube playlist ID or playlist URL')


def discover_collection(kind, value, maximum=20, streams=False, runner=subprocess.run):
    """Read bounded public metadata; this does not grant reuse permission."""
    if not 1 <= maximum <= 100: raise ValueError('Discovery limit must be between 1 and 100')
    url = collection_url(kind, value)
    if kind == 'youtube_channel' and streams: url = url.removesuffix('/videos') + '/streams'
    cmd = ytdlp_command('--ignore-config', '--no-cookies', '--flat-playlist', '--skip-download', '--playlist-end', str(maximum), '--dump-single-json', url)
    try:
        result = runner(cmd, capture_output=True, check=True, timeout=120, **process_options(bundled_child=True))
        data = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('YouTube discovery timed out') from exc
    except (subprocess.CalledProcessError, ValueError, TypeError) as exc:
        raise RuntimeError('YouTube discovery failed or returned invalid metadata') from exc
    if not isinstance(data, dict) or not isinstance(data.get('entries'), list):
        raise RuntimeError('YouTube discovery returned invalid entries')
    found = []
    for item in data['entries'][:maximum]:
        if not isinstance(item, dict) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(item.get('id', ''))): continue
        if item.get('is_live') or item.get('live_status') in ('is_live', 'is_upcoming', 'post_live'): continue
        if item.get('has_drm') or item.get('availability') in ('private', 'needs_auth', 'premium_only', 'subscriber_only'): continue
        found.append({'id': item['id'], 'title': str(item.get('title') or '')[:300], 'live_status': item.get('live_status')})
    return found
