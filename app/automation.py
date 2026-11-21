"""Persistent, single-worker automation for explicitly authorized sources.

A folder grant does not authorize its contents: every local file must have an
existing approved, hash-matching source record. Interrupted processing is not
retried automatically because it may have produced partial outputs.
"""
from __future__ import annotations

import sqlite3
import uuid
import json
import time
import shutil
import threading
from pathlib import Path
from filelock import FileLock, Timeout
from app.acquisition import acquire, authorize, youtube_id, inspect_completed_video, collection_url, discover_collection
from app.core import require_rights
from app.autoclip import generate

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}


class Automation:
    def __init__(self, store, clock=time.time, settle_seconds=10):
        self.store = store
        self.clock = clock
        self.settle_seconds = settle_seconds
        self.lock = FileLock(str(store.path) + '.automation.lock', timeout=0)
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS automation_sources (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL,
                    creator TEXT NOT NULL, evidence TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(kind,value));
                CREATE TABLE IF NOT EXISTS automation_jobs (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_key TEXT NOT NULL UNIQUE,
                    path TEXT, size INTEGER, mtime_ns INTEGER, digest TEXT, status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS automation_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, job_id TEXT,
                    event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS automation_plans (
                    job_id TEXT PRIMARY KEY, expected_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_outputs (
                    job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, directory TEXT NOT NULL UNIQUE,
                    score REAL NOT NULL, explanation TEXT NOT NULL, metadata TEXT NOT NULL,
                    PRIMARY KEY(job_id,ordinal));
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(automation_jobs)')}
            if 'observed_at' not in columns:
                db.execute('ALTER TABLE automation_jobs ADD COLUMN observed_at REAL NOT NULL DEFAULT 0')
            if 'attempts' not in columns:
                db.execute('ALTER TABLE automation_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
            if 'next_retry' not in columns:
                db.execute('ALTER TABLE automation_jobs ADD COLUMN next_retry REAL')
            source_columns = {row[1] for row in db.execute('PRAGMA table_info(automation_sources)')}
            if 'last_checked' not in source_columns: db.execute("ALTER TABLE automation_sources ADD COLUMN last_checked TEXT")
            if 'new_found' not in source_columns: db.execute("ALTER TABLE automation_sources ADD COLUMN new_found INTEGER NOT NULL DEFAULT 0")
            if 'video_url' not in columns: db.execute("ALTER TABLE automation_jobs ADD COLUMN video_url TEXT")
            if 'safe_retry' not in columns: db.execute("ALTER TABLE automation_jobs ADD COLUMN safe_retry INTEGER NOT NULL DEFAULT 0")
            if 'title' not in columns: db.execute("ALTER TABLE automation_jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if 'auto_approve' not in source_columns:
                db.execute('ALTER TABLE automation_sources ADD COLUMN auto_approve INTEGER NOT NULL DEFAULT 0')
        try:
            with self.lock:
                with store.connect() as db:
                    db.execute("UPDATE automation_jobs SET status='interrupted', error='Processing stopped unexpectedly; inspect partial outputs before retrying', updated_at=CURRENT_TIMESTAMP WHERE status='processing'")
        except Timeout:
            # Another instance may still be processing. Never change its state.
            pass

    def add_source(self, kind, value, creator, evidence, auto_approve=False):
        if kind not in ('folder', 'youtube_video', 'youtube_channel', 'youtube_playlist') or not creator.strip() or not evidence.strip():
            raise ValueError('Select a supported source and provide creator and rights evidence')
        if auto_approve and kind != 'folder': raise ValueError('Folder-wide authorization applies only to local folders')
        if kind == 'folder':
            path = Path(value).expanduser().resolve(strict=True)
            if not path.is_dir(): raise ValueError('Choose an existing local folder')
            value = str(path)
        elif kind in ('youtube_channel', 'youtube_playlist'):
            value = collection_url(kind, value)
        else:
            video_id = youtube_id(value)
            value = 'https://www.youtube.com/watch?v=' + video_id
            with self.store.connect() as db:
                if db.execute('SELECT 1 FROM automation_sources WHERE kind=? AND value=?', (kind, value)).fetchone():
                    raise ValueError('This source is already configured')
            authorize(self.store, value, creator, evidence)
        source_id = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO automation_sources(id,kind,value,creator,evidence,auto_approve) VALUES(?,?,?,?,?,?)', (source_id, kind, value, creator.strip(), evidence.strip(), int(bool(auto_approve))))
        return source_id

    def set_enabled(self, source_id, enabled):
        with self.store.connect() as db:
            result = db.execute('UPDATE automation_sources SET enabled=? WHERE id=?', (int(bool(enabled)), source_id))
            if not result.rowcount: raise ValueError('Source not found')
            if not enabled and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_items'").fetchone():
                db.execute("""UPDATE review_items SET status='ready', approved_output_hash=NULL, approved_metadata_hash=NULL
                    WHERE status='approved_private' AND job_id IN (SELECT j.id FROM clip_jobs j JOIN sources s ON j.source_id=s.id WHERE s.automation_source_id=?)""", (source_id,))

    def save_settings(self, settings):
        with self.store.connect() as db:
            db.execute('INSERT INTO automation_settings(id,value) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (json.dumps(settings),))

    def settings(self):
        with self.store.connect() as db:
            row = db.execute('SELECT value FROM automation_settings WHERE id=1').fetchone()
        if not row: return {}
        try:
            data = json.loads(row[0])
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    def list_sources(self):
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(x) for x in db.execute('SELECT * FROM automation_sources ORDER BY rowid DESC')]

    def list_jobs(self):
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(x) for x in db.execute('SELECT * FROM automation_jobs ORDER BY updated_at DESC, rowid DESC')]

    def list_activity(self, limit=200):
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                'SELECT * FROM automation_activity ORDER BY id DESC LIMIT ?', (limit,))]

    def _activity(self, event, detail='', source_id=None, job_id=None):
        try:
            with self.store.connect() as db:
                db.execute('INSERT INTO automation_activity(source_id,job_id,event,detail) VALUES(?,?,?,?)',
                           (source_id, job_id, event, str(detail)[:500]))
        except sqlite3.Error:
            # Logging is best effort and must never change a completed job's state.
            pass

    def _refresh_video_jobs(self):
        # Previously discovered videos remain actionable after aging out of the first page.
        with self.store.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='youtube_permissions'").fetchone():
                return
            rows = db.execute("""SELECT j.id,j.source_key,j.status,j.next_retry FROM automation_jobs j
                JOIN automation_sources s ON s.id=j.source_id
                WHERE s.enabled=1 AND s.kind IN ('youtube_channel','youtube_playlist')
                AND j.status IN ('pending','awaiting_authorization','retry_wait')""").fetchall()
            for job_id, key, status, retry in rows:
                if not key.startswith('youtube:'): continue
                rights = db.execute('SELECT approved FROM youtube_permissions WHERE video_id=?', (key[8:],)).fetchone()
                allowed = bool(rights and rights[0])
                if status == 'retry_wait' and allowed and retry is not None and retry > self.clock(): continue
                target = 'pending' if allowed else 'awaiting_authorization'
                if target != status:
                    db.execute('UPDATE automation_jobs SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (target, '' if allowed else 'Exact video permission required', job_id))

    def scan(self, maximum=20, streams=False, discoverer=discover_collection):
        self._refresh_video_jobs()
        for source in self.list_sources():
            if not source['enabled']: continue
            if source['kind'] == 'youtube_video':
                self._observe_youtube(source)
            elif source['kind'] == 'folder':
                self._observe_folder(source)
            elif source['kind'] in ('youtube_channel', 'youtube_playlist'):
                self._observe_collection(source, maximum, streams, discoverer)

    def _observe_collection(self, source, maximum, streams, discoverer):
        try:
            entries = discoverer(source['kind'], source['value'], maximum, False)
            if streams and source['kind'] == 'youtube_channel':
                entries += discoverer(source['kind'], source['value'], maximum, True)
        except Exception as exc:
            self._source_error(source, 'Discovery failed: ' + str(exc)[:400])
            self._activity('discovery_failed', str(exc), source['id'])
            return
        new = 0
        with self.store.connect() as db:
            for entry in entries:
                video_id = entry.get('id', '')
                try: youtube_id('https://youtu.be/' + video_id)
                except (ValueError, TypeError): continue
                if entry.get('live_status') in ('is_live', 'is_upcoming', 'post_live'): continue
                url = 'https://www.youtube.com/watch?v=' + video_id
                key = 'youtube:' + video_id
                permitted = db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name="youtube_permissions"').fetchone()
                rights = db.execute('SELECT 1 FROM youtube_permissions WHERE video_id=? AND approved=1', (video_id,)).fetchone() if permitted else None
                result = db.execute("""INSERT OR IGNORE INTO automation_jobs(id,source_id,source_key,video_url,title,status,error)
                    VALUES(?,?,?,?,?,?,?)""", (str(uuid.uuid4()),source['id'],key,url,str(entry.get('title',''))[:300],
                        'pending' if rights else 'awaiting_authorization', '' if rights else 'Approve this exact video in Automation'))
                new += result.rowcount
                db.execute("""UPDATE automation_jobs SET status=?,error=?,updated_at=CURRENT_TIMESTAMP
                    WHERE source_id=? AND source_key=? AND (status IN ('pending','awaiting_authorization') OR (status='retry_wait' AND (next_retry<=? OR ?=0)))""",
                    ('pending' if rights else 'awaiting_authorization', '' if rights else 'Exact video permission required',source['id'],key,self.clock(),int(bool(rights))))
            db.execute('UPDATE automation_sources SET last_checked=CURRENT_TIMESTAMP,new_found=? WHERE id=?', (new,source['id']))
            db.execute("UPDATE automation_jobs SET status='resolved',error='',updated_at=CURRENT_TIMESTAMP WHERE source_key=? AND status='failed'", ('source:' + source['id'],))
        self._activity('discovery_completed', f'{len(entries)} metadata entries checked; {new} new videos', source['id'])

    def _observe_youtube(self, source):
        video_id = youtube_id(source['value'])
        with self.store.connect() as db:
            rights = db.execute('SELECT creator,evidence,approved FROM youtube_permissions WHERE video_id=?', (video_id,)).fetchone()
            authorized = rights == (source['creator'], source['evidence'], 1)
            db.execute('''INSERT INTO automation_jobs(id,source_id,source_key,status,error) VALUES(?,?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET
                    status=CASE WHEN automation_jobs.status IN ('pending','awaiting_authorization') OR (automation_jobs.status='retry_wait' AND (excluded.status='awaiting_authorization' OR automation_jobs.next_retry<=?)) THEN excluded.status ELSE automation_jobs.status END,
                    error=CASE WHEN automation_jobs.status IN ('pending','awaiting_authorization') OR (automation_jobs.status='retry_wait' AND (excluded.status='awaiting_authorization' OR automation_jobs.next_retry<=?)) THEN excluded.error ELSE automation_jobs.error END,
                    updated_at=CURRENT_TIMESTAMP''', (str(uuid.uuid4()), source['id'], 'youtube:' + video_id, 'pending' if authorized else 'awaiting_authorization', '' if authorized else 'Exact video permission is missing or changed',self.clock(),self.clock()))

    def _observe_folder(self, source):
        folder = Path(source['value'])
        if not folder.is_dir():
            self._source_error(source, 'Folder is unavailable')
            return
        try:
            files = list(folder.iterdir())
        except OSError as exc:
            self._source_error(source, f'Cannot scan folder: {exc}')
            return
        for path in files:
            try:
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS: continue
                resolved = path.resolve(strict=True)
                if resolved.parent != folder: continue
                stat = resolved.stat()
            except OSError:
                continue
            # Preserve history when a file at the same path is replaced or grows.
            key = f'file:{str(resolved).casefold()}:{stat.st_size}:{stat.st_mtime_ns}'
            with self.store.connect() as db:
                db.execute("""UPDATE automation_jobs SET status='superseded',
                    error='Source changed since discovery',updated_at=CURRENT_TIMESTAMP
                    WHERE path=? AND source_key!=? AND status IN ('observing','awaiting_authorization','pending')""", (str(resolved), key))
                row = db.execute('SELECT id,size,mtime_ns,status,observed_at FROM automation_jobs WHERE source_key=?', (key,)).fetchone()
                if row is None:
                    db.execute('INSERT INTO automation_jobs(id,source_id,source_key,path,size,mtime_ns,status,observed_at) VALUES(?,?,?,?,?,?,?,?)', (str(uuid.uuid4()),source['id'],key,str(resolved),stat.st_size,stat.st_mtime_ns,'observing',self.clock()))
                    continue
                job_id, old_size, old_mtime, status, observed_at = row
                if status in ('processing','completed','failed','interrupted','duplicate'): continue
                if old_size != stat.st_size or old_mtime != stat.st_mtime_ns:
                    db.execute("UPDATE automation_jobs SET size=?,mtime_ns=?,status='observing',digest=NULL,error='',observed_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (stat.st_size,stat.st_mtime_ns,self.clock(),job_id))
                    continue
                if self.clock() - observed_at < self.settle_seconds:
                    continue
            try:
                try:
                    rights = require_rights(self.store, str(resolved))
                except PermissionError:
                    # Only an explicit folder-wide grant can authorize new files.
                    # Never overwrite a pre-existing per-file rights decision.
                    if not source['auto_approve'] or self.store.source(str(resolved)) is not None:
                        raise
                    self.store.save_source(str(resolved), source['creator'], '', source['evidence'], True,
                                           automation_source_id=source['id'], only_if_new=True)
                    rights = require_rights(self.store, str(resolved))
                # Recheck stat after hashing: a growing file is never queued.
                after = resolved.stat()
                if (after.st_size,after.st_mtime_ns) != (stat.st_size,stat.st_mtime_ns): continue
                digest = rights['sha256']
                with self.store.connect() as db:
                    duplicate = db.execute("SELECT 1 FROM automation_jobs WHERE id!=? AND digest=? AND status IN ('pending','processing','completed','interrupted')", (job_id,digest)).fetchone()
                    db.execute("UPDATE automation_jobs SET digest=?,status=?,error='',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('observing','awaiting_authorization','pending')", (digest, 'duplicate' if duplicate else 'pending',job_id))
            except (PermissionError,OSError,ValueError):
                with self.store.connect() as db:
                    db.execute("UPDATE automation_jobs SET status='awaiting_authorization',digest=NULL,error='Approve this exact file in Clip Editor',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('observing','awaiting_authorization','pending')", (job_id,))

    def _source_error(self, source, error):
        with self.store.connect() as db:
            db.execute('''INSERT INTO automation_jobs(id,source_id,source_key,status,error) VALUES(?,?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET status='failed',error=excluded.error,updated_at=CURRENT_TIMESTAMP''', (str(uuid.uuid4()),source['id'],'source:' + source['id'],'failed',error))

    def retry_job(self, job_id):
        """Retry only when generation never began, avoiding duplicate review clips."""
        try:
            with self.lock:
                with self.store.connect() as db:
                    changed = db.execute("""UPDATE automation_jobs SET status='pending',attempts=0,next_retry=NULL,error='',updated_at=CURRENT_TIMESTAMP
                        WHERE id=? AND status IN ('failed','interrupted') AND safe_retry=1""", (job_id,)).rowcount
                    if not changed:
                        raise ValueError('Cannot safely retry this job: it may have produced clips or has no retryable failure. Inspect its outputs first.')
        except Timeout:
            raise RuntimeError('Wait for the current automation worker to finish') from None

    def reconcile_job(self, job_id):
        """Recover only complete, provenance-checked outputs from a recorded plan."""
        from app.provenance import ClipJob, verify_job
        from app.review import ReviewQueue
        from app.storage import file_hash
        from app.video import probe
        try:
            with self.lock:
                with self.store.connect() as db:
                    db.row_factory = sqlite3.Row
                    job = db.execute("SELECT * FROM automation_jobs WHERE id=? AND status IN ('failed','interrupted')", (job_id,)).fetchone()
                    plan = db.execute('SELECT expected_count FROM automation_plans WHERE job_id=?', (job_id,)).fetchone()
                    rows = db.execute('SELECT * FROM automation_outputs WHERE job_id=? ORDER BY ordinal', (job_id,)).fetchall()
                if not job or not plan or plan[0] < 1 or len(rows) != plan[0]:
                    raise ValueError('Recovery is uncertain: the recorded clip plan is incomplete; inspect partial outputs manually')
                queue = ReviewQueue(self.store)
                recovered = []
                for row in rows:
                    directory = Path(row['directory'])
                    if directory.is_symlink() or not directory.is_dir():
                        raise ValueError('Recovery is uncertain: a planned output folder is missing')
                    try:
                        metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
                        provenance = json.loads((directory / 'provenance.json').read_text(encoding='utf-8'))
                        planned = json.loads(row['metadata'])
                        clip_id = provenance['job_id']
                        with self.store.connect() as db:
                            db.row_factory = sqlite3.Row
                            clip = db.execute('SELECT j.*,e.output_path,e.output_sha256 FROM clip_jobs j JOIN exports e ON j.id=e.job_id WHERE j.id=?', (clip_id,)).fetchone()
                            existing = db.execute('SELECT id FROM review_items WHERE job_id=?', (clip_id,)).fetchone()
                        if not clip or Path(clip['output_path']).resolve() != (directory / 'clip.mp4').resolve():
                            raise ValueError('export record does not match the planned folder')
                        snapshot = ClipJob(clip['id'],clip['source_id'],clip['source_path'],clip['source_sha256'],clip['start'],clip['end'])
                        rights = verify_job(self.store, snapshot)
                        if clip['source_sha256'] != job['digest'] or clip['source_path'] != job['path']:
                            raise ValueError('source snapshot does not match the automation job')
                        if any(provenance.get(k) != v for k,v in (('start',clip['start']),('end',clip['end']),('source_url',rights['source_url']),('creator',rights['creator']))):
                            raise ValueError('source or clip boundaries do not match provenance')
                        digest = file_hash(Path(clip['output_path']))
                        if digest != clip['output_sha256'] or any(provenance.get(k) != v or metadata.get(k) != v for k,v in
                            (('job_id',clip_id),('source_sha256',clip['source_sha256']),('output_sha256',digest))):
                            raise ValueError('output hash or provenance does not match')
                        if any(metadata.get(k) != planned.get(k) for k in ('title','description')):
                            raise ValueError('metadata does not match the recorded plan')
                        streams = probe(Path(clip['output_path'])).get('streams', [])
                        if not any(s.get('codec_type') == 'video' and s.get('codec_name') == 'h264' and s.get('width') == 1080 and s.get('height') == 1920 for s in streams) or not any(s.get('codec_type') == 'audio' and s.get('codec_name') == 'aac' for s in streams):
                            raise ValueError('output streams are not a valid vertical H.264/AAC clip')
                        if existing: queue.validate(queue.get(existing['id']))
                        recovered.append((row,clip_id,existing['id'] if existing else None,planned))
                    except Exception as exc:
                        raise ValueError(f'Recovery is uncertain; inspect {directory}: {exc}') from exc
                for row,clip_id,item_id,planned in recovered:
                    if item_id is None:
                        item_id = queue.add(clip_id,row['score'],row['explanation'],planned,mark_completed=True)
                    else:
                        with self.store.connect() as db:
                            db.execute('CREATE TABLE IF NOT EXISTS completed_packages (job_id TEXT PRIMARY KEY, directory TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                            db.execute('INSERT OR IGNORE INTO completed_packages(job_id,directory) VALUES(?,?)', (clip_id,row['directory']))
                with self.store.connect() as db:
                    db.execute("UPDATE automation_jobs SET status='completed',error='',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('failed','interrupted')", (job_id,))
                self._activity('reconciled', f'{len(recovered)} existing clips verified', job['source_id'], job_id)
                return len(recovered)
        except Timeout:
            raise RuntimeError('Wait for the current automation worker to finish') from None

    def process_next(self, output_dir, maximum=3, clip_duration=20, min_score=.1, cancelled=None, progress=None, generator=generate, downloader=acquire, cpu_threads=0, min_free_bytes=0, inspector=inspect_completed_video, retry_limit=3, retry_backoff=30):
        try:
            with self.lock:
                return self._process_next(output_dir, maximum, clip_duration, min_score, cancelled, progress, generator, downloader, cpu_threads, min_free_bytes, inspector, retry_limit, retry_backoff)
        except Timeout:
            raise RuntimeError('Another automation worker is already running') from None

    def _process_next(self, output_dir, maximum, clip_duration, min_score, cancelled, progress, generator, downloader, cpu_threads, min_free_bytes, inspector, retry_limit, retry_backoff):
        root = Path(output_dir).expanduser().resolve(strict=True)
        if not root.is_dir(): raise ValueError('Choose an existing output folder')
        if not isinstance(cpu_threads, int) or cpu_threads < 0 or min_free_bytes < 0 or not 1 <= retry_limit <= 10 or not 1 <= retry_backoff <= 3600:
            raise ValueError('Invalid resource limits')
        if shutil.disk_usage(root).free < min_free_bytes:
            raise OSError('Output drive has less free space than the configured minimum')
        if cancelled and cancelled(): raise InterruptedError('Automation stopped')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT j.*,s.kind,s.value,s.creator,s.evidence,s.enabled FROM automation_jobs j JOIN automation_sources s ON s.id=j.source_id WHERE j.status='pending' AND s.enabled=1 ORDER BY j.rowid LIMIT 1").fetchone()
            if row is None: return None
            job = dict(row)
            db.execute("UPDATE automation_jobs SET status='processing',safe_retry=1,error='',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (job['id'],))
        self._activity('processing_started', job['source_key'], job['source_id'], job['id'])
        disk_low = threading.Event()
        disk_monitor_stop = threading.Event()
        def monitor_disk():
            while not disk_monitor_stop.is_set():
                try:
                    if shutil.disk_usage(root).free < min_free_bytes: disk_low.set(); return
                except OSError:
                    disk_low.set(); return
                disk_monitor_stop.wait(1)
        disk_thread = threading.Thread(target=monitor_disk, daemon=True) if min_free_bytes else None
        if disk_thread: disk_thread.start()
        def cancelled_or_disabled():
            if disk_low.is_set() or (cancelled and cancelled()): return True
            with self.store.connect() as db:
                current = db.execute('SELECT enabled FROM automation_sources WHERE id=?', (job['source_id'],)).fetchone()
            return not current or not current[0]
        try:
            if cancelled_or_disabled(): raise InterruptedError('Automation stopped or source disabled')
            if job['kind'] in ('youtube_video', 'youtube_channel', 'youtube_playlist'):
                video_url = job['video_url'] or job['value']
                # Recheck exact-ID rights before even requesting metadata.
                with self.store.connect() as db:
                    permission = db.execute('SELECT creator,evidence,approved FROM youtube_permissions WHERE video_id=?', (youtube_id(video_url),)).fetchone()
                if not permission or not permission[2] or (job['kind'] == 'youtube_video' and permission != (job['creator'], job['evidence'], 1)):
                    raise PermissionError('Exact video permission was changed or revoked')
                try:
                    inspector(video_url)
                    if cancelled_or_disabled(): raise InterruptedError('Automation stopped or source disabled')
                    # acquire rechecks authorization before downloading.
                    path = downloader(self.store,video_url,str(root))
                except RuntimeError as exc:
                    message = str(exc)
                    transient = any(term in message.lower() for term in ('timed out', 'timeout', 'network', 'connection', 'temporarily unavailable', 'http error 429', 'http error 502', 'http error 503', 'http error 504'))
                    if not transient: raise
                    attempts = job['attempts'] + 1
                    status = 'retry_wait' if attempts < retry_limit else 'failed'
                    delay = retry_backoff * (2 ** (attempts - 1))
                    with self.store.connect() as db:
                        db.execute("UPDATE automation_jobs SET status=?,attempts=?,next_retry=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, attempts, self.clock() + delay if status == 'retry_wait' else None, 'Temporary download failure; retry scheduled' if status == 'retry_wait' else f'Download failed after {retry_limit} attempts',job['id']))
                    self._activity(status, 'Temporary YouTube acquisition failure', job['source_id'], job['id'])
                    return None
            else:
                path = job['path']
            rights = require_rights(self.store,path)
            if job['digest'] and rights['sha256'] != job['digest']:
                raise PermissionError('Approved file changed after discovery')
            if cancelled_or_disabled(): raise InterruptedError('Automation stopped or source disabled')
            if shutil.disk_usage(root).free < min_free_bytes:
                raise OSError('Output drive has less free space than the configured minimum')
            kwargs = {'cpu_threads': cpu_threads} if cpu_threads else {}
            if generator is generate:
                kwargs['automation_job_id'] = job['id']
            with self.store.connect() as db:
                db.execute('UPDATE automation_jobs SET safe_retry=0,path=?,digest=? WHERE id=?', (path, rights['sha256'], job['id']))
            items = generator(self.store,path,str(root),maximum=maximum,clip_duration=clip_duration,min_score=min_score,cancelled=cancelled_or_disabled,progress=progress,**kwargs)
            if disk_low.is_set(): raise OSError('Free disk space fell below the configured minimum during processing')
            if not items: raise ValueError('No clips were generated')
            with self.store.connect() as db:
                db.execute("UPDATE automation_jobs SET path=?,digest=?,status='completed',error='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (path,rights['sha256'],job['id']))
            self._activity('completed', f'{len(items)} clips ready for review', job['source_id'], job['id'])
            return items
        except Exception as exc:
            if disk_low.is_set(): exc = OSError('Free disk space fell below the configured minimum during processing')
            with self.store.connect() as db:
                db.execute("UPDATE automation_jobs SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", ('interrupted' if isinstance(exc,InterruptedError) else 'failed',str(exc)[:500],job['id']))
            self._activity('interrupted' if isinstance(exc, InterruptedError) else 'failed', str(exc), job['source_id'], job['id'])
            raise exc
        finally:
            disk_monitor_stop.set()
            if disk_thread: disk_thread.join(timeout=2)
