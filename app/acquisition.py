"""Acquisition of specifically authorized YouTube videos."""
from __future__ import annotations

import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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
    cmd = [sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-cookies', '--no-playlist', '--no-cache-dir', '--no-progress', '--no-write-info-json', '--ffmpeg-location', tool_path('ffmpeg'), '-f', 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b', '--merge-output-format', 'mp4', '-o', str(directory / 'source.%(ext)s'), '--print', 'after_move:filepath', canonical_url]
    try:
        result = runner(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, timeout=7200)
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
