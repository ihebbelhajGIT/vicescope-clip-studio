"""Persistent human review decisions tied to exact media and metadata."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from app.provenance import ClipJob, verify_job
from app.storage import file_hash


def canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def metadata_hash(data):
    return hashlib.sha256(canonical(data).encode('utf-8')).hexdigest()


class ReviewQueue:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS review_items (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, score REAL NOT NULL,
                explanation TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL,
                approved_output_hash TEXT, approved_metadata_hash TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def add(self, job_id, score, explanation, metadata):
        item_id = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute('INSERT INTO review_items(id,job_id,score,explanation,metadata,status) VALUES(?,?,?,?,?,?)',
                       (item_id, job_id, score, explanation, canonical(metadata), 'ready'))
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
        return rights

    def edit(self, item_id, metadata):
        if not isinstance(metadata, dict) or not isinstance(metadata.get('title'), str) or not isinstance(metadata.get('description'), str) or not isinstance(metadata.get('hashtags'), list) or not all(isinstance(x, str) for x in metadata['hashtags']):
            raise ValueError('Metadata requires a title, description and list of hashtags')
        with self.store.connect() as db:
            cursor = db.execute("UPDATE review_items SET metadata=?, status='ready', approved_output_hash=NULL, approved_metadata_hash=NULL WHERE id=? AND status != 'rejected'", (canonical(metadata), item_id))
            if not cursor.rowcount: raise ValueError('Review item not found or rejected; generate a new version')

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
                if item['status'] == 'rejected': raise PermissionError('Rejected version cannot be approved; generate a new version')
                output_hash, meta_hash = item['output_sha256'], metadata_hash(json.loads(item['metadata']))
            else:
                if item is None: raise ValueError('Review item not found')
                output_hash, meta_hash = None, None
            db.execute('UPDATE review_items SET status=?, approved_output_hash=?, approved_metadata_hash=? WHERE id=?', (decision, output_hash, meta_hash, item_id))

    def is_approved(self, item_id):
        item = self.get(item_id)
        if not item or item['status'] != 'approved_private': return False
        try:
            self.validate(item)
            return item['approved_output_hash'] == item['output_sha256'] and item['approved_metadata_hash'] == metadata_hash(json.loads(item['metadata']))
        except (OSError, PermissionError, ValueError):
            return False
