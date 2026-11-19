"""Persistent human review decisions tied to exact media and metadata."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import os
import tempfile
from pathlib import Path
from app.provenance import ClipJob, verify_job
from app.storage import file_hash


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def metadata_hash(data):
    return hashlib.sha256(canonical(data).encode('utf-8')).hexdigest()


def sync_package(path, metadata):
    """Write review fields atomically; preserve provenance fields in the package."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data.update(metadata)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.metadata-', suffix='.tmp', delete=False) as tmp:
            temp_name = tmp.name
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name): os.unlink(temp_name)


class ReviewQueue:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS review_items (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, score REAL NOT NULL,
                explanation TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL,
                approved_output_hash TEXT, approved_metadata_hash TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(review_items)')}
            if 'parent_item_id' not in columns:
                db.execute('ALTER TABLE review_items ADD COLUMN parent_item_id TEXT')

    def add(self, job_id, score, explanation, metadata, parent_item_id=None, mark_completed=False):
        item_id = str(uuid.uuid4())
        with self.store.connect() as db:
            row = db.execute('SELECT output_path FROM exports WHERE job_id=?', (job_id,)).fetchone()
            if not row: raise ValueError('Export not found')
            sync_package(Path(row[0]).parent / 'metadata.json', metadata)
            db.execute('INSERT INTO review_items(id,job_id,score,explanation,metadata,status,parent_item_id) VALUES(?,?,?,?,?,?,?)',
                       (item_id, job_id, score, explanation, canonical(metadata), 'ready', parent_item_id))
            if mark_completed:
                db.row_factory = sqlite3.Row
                row = db.execute('''SELECT r.*, e.output_path, e.output_sha256, j.source_id, j.source_path,
                    j.source_sha256, j.start, j.end FROM review_items r JOIN exports e ON e.job_id=r.job_id
                    JOIN clip_jobs j ON j.id=r.job_id WHERE r.id=?''', (item_id,)).fetchone()
                item = dict(row)
                self.validate(item)
                directory = Path(item['output_path']).parent
                provenance = json.loads((directory / 'provenance.json').read_text(encoding='utf-8'))
                if any(provenance.get(k) != item[v] for k, v in (('job_id', 'job_id'), ('source_sha256', 'source_sha256'), ('output_sha256', 'output_sha256'))):
                    raise PermissionError('Export provenance does not match the review record')
                db.execute('CREATE TABLE IF NOT EXISTS completed_packages (job_id TEXT PRIMARY KEY, directory TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
                db.execute('INSERT INTO completed_packages(job_id,directory) VALUES(?,?)', (job_id, str(directory)))
        return item_id

    def list(self):
        with self.store.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('''SELECT r.*, e.output_path, e.output_sha256,
                j.source_id, j.source_path, j.source_sha256, j.start, j.end
                FROM review_items r JOIN exports e ON e.job_id=r.job_id
                JOIN clip_jobs j ON j.id=r.job_id ORDER BY r.created_at DESC, r.rowid DESC''')]

    def get(self, item_id):
        return next((row for row in self.list() if row['id'] == item_id), None)

    def validate(self, item):
        if item is None:
            raise ValueError('Review item not found')
        job = ClipJob(item['job_id'], item['source_id'], item['source_path'], item['source_sha256'], item['start'], item['end'])
        rights = verify_job(self.store, job)
        if file_hash(Path(item['output_path'])) != item['output_sha256']:
            raise PermissionError('Rendered video changed since export')
        try:
            package = json.loads((Path(item['output_path']).parent / 'metadata.json').read_text(encoding='utf-8'))
            metadata = json.loads(item['metadata'])
            if any(package.get(key) != metadata.get(key) for key in ('title', 'description', 'hashtags')):
                raise PermissionError('Package metadata differs from the review record')
            if package.get('output_sha256') != item['output_sha256'] or package.get('source_sha256') != item['source_sha256'] or package.get('job_id') != item['job_id']:
                raise PermissionError('Package provenance does not match the review record')
        except (OSError, json.JSONDecodeError) as exc:
            raise PermissionError('Package metadata is missing or invalid') from exc
        return rights

    def edit(self, item_id, metadata):
        if not isinstance(metadata, dict) or not isinstance(metadata.get('title'), str) or not isinstance(metadata.get('description'), str) or not isinstance(metadata.get('hashtags'), list) or not all(isinstance(x, str) for x in metadata['hashtags']):
            raise ValueError('Metadata requires a title, description and list of hashtags')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT e.output_path FROM review_items r JOIN exports e ON e.job_id=r.job_id WHERE r.id=? AND r.status NOT IN ('rejected','superseded')", (item_id,)).fetchone()
            if not row: raise ValueError('Review item not found or rejected; generate a new version')
            sync_package(Path(row[0]).parent / 'metadata.json', metadata)
            db.execute("UPDATE review_items SET metadata=?, status='ready', approved_output_hash=NULL, approved_metadata_hash=NULL WHERE id=?", (canonical(metadata), item_id))

    def decide(self, item_id, decision):
        if decision not in ('approved_private', 'rejected'):
            raise ValueError('Invalid decision')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.row_factory = sqlite3.Row
            row = db.execute('''SELECT r.*, e.output_path, e.output_sha256, j.source_id, j.source_path,
                j.source_sha256, j.start, j.end FROM review_items r JOIN exports e ON e.job_id=r.job_id
                JOIN clip_jobs j ON j.id=r.job_id WHERE r.id=?''', (item_id,)).fetchone()
            item = dict(row) if row else None
            if decision == 'approved_private':
                self.validate(item)
                if item['status'] in ('rejected', 'superseded'): raise PermissionError('Rejected or superseded version cannot be approved')
                output_hash, meta_hash = item['output_sha256'], metadata_hash(json.loads(item['metadata']))
            else:
                if item is None: raise ValueError('Review item not found')
                output_hash, meta_hash = None, None
            db.execute('UPDATE review_items SET status=?, approved_output_hash=?, approved_metadata_hash=? WHERE id=?', (decision, output_hash, meta_hash, item_id))

    def supersede(self, item_id):
        with self.store.connect() as db:
            changed = db.execute("UPDATE review_items SET status='superseded', approved_output_hash=NULL, approved_metadata_hash=NULL WHERE id=? AND status != 'superseded'", (item_id,))
            if not changed.rowcount: raise ValueError('Review item not found or already superseded')

    def supersede_if_unchanged(self, original):
        with self.store.connect() as db:
            changed = db.execute("""UPDATE review_items SET status='superseded', approved_output_hash=NULL,
                approved_metadata_hash=NULL WHERE id=? AND status=? AND metadata=? AND
                approved_output_hash IS ? AND approved_metadata_hash IS ?""",
                (original['id'], original['status'], original['metadata'],
                 original['approved_output_hash'], original['approved_metadata_hash']))
            if not changed.rowcount:
                raise ValueError('Original review changed during regeneration; review both versions')

    def is_approved(self, item_id):
        item = self.get(item_id)
        if not item or item['status'] != 'approved_private': return False
        try:
            self.validate(item)
            return item['approved_output_hash'] == item['output_sha256'] and item['approved_metadata_hash'] == metadata_hash(json.loads(item['metadata']))
        except (OSError, PermissionError, ValueError):
            return False
