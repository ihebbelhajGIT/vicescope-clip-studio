from __future__ import annotations

import hashlib
from contextlib import contextmanager
import sqlite3
from pathlib import Path


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as media:
        for chunk in iter(lambda: media.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                    creator TEXT NOT NULL, source_url TEXT NOT NULL,
                    permission_evidence TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS clip_jobs (
                    id TEXT PRIMARY KEY, source_id INTEGER NOT NULL, source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exports (
                    job_id TEXT PRIMARY KEY, output_path TEXT NOT NULL, output_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, brief TEXT NOT NULL
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(sources)")}
            if "sha256" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN sha256 TEXT")
                db.execute("UPDATE sources SET approved=0")
            if "youtube_video_id" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN youtube_video_id TEXT")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='youtube_permissions'").fetchone():
                    db.execute("""UPDATE sources SET youtube_video_id=substr(source_url,33)
                        WHERE source_url='https://www.youtube.com/watch?v=' || substr(source_url,33)
                        AND EXISTS (SELECT 1 FROM youtube_permissions WHERE video_id=substr(source_url,33))""")
            if "automation_source_id" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN automation_source_id TEXT")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_source(self, path: str, creator: str, source_url: str, evidence: str, approved: bool, youtube_video_id: str | None = None, automation_source_id: str | None = None, only_if_new: bool = False):
        resolved = Path(path).expanduser().resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("Select a local video file")
        if approved and not evidence.strip():
            raise ValueError("Permission evidence is required for approval")
        digest = file_hash(resolved)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if automation_source_id:
                grant = db.execute('SELECT kind,value,creator,evidence,enabled,auto_approve FROM automation_sources WHERE id=?', (automation_source_id,)).fetchone()
                if (not grant or grant[0] != 'folder' or not grant[4] or not grant[5]
                        or resolved.parent != Path(grant[1]) or grant[2:4] != (creator.strip(), evidence.strip())
                        or source_url or not approved or youtube_video_id):
                    raise PermissionError('The folder authorization is missing, disabled, or does not cover this file')
            old = db.execute("SELECT id,creator,source_url,permission_evidence,approved,sha256,automation_source_id FROM sources WHERE path=?", (str(resolved),)).fetchone()
            if only_if_new and old: raise ValueError('Source already has an explicit rights record')
            current = (creator.strip(), source_url.strip(), evidence.strip(), int(approved), digest, automation_source_id)
            if old and old[1:] != current:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_items'").fetchone()
                if exists:
                    db.execute("""UPDATE review_items SET status='ready', approved_output_hash=NULL, approved_metadata_hash=NULL
                        WHERE status='approved_private' AND job_id IN (SELECT id FROM clip_jobs WHERE source_id=?)""", (old[0],))
            db.execute("INSERT INTO sources(path,creator,source_url,permission_evidence,approved,sha256,youtube_video_id,automation_source_id) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET creator=excluded.creator,source_url=excluded.source_url,permission_evidence=excluded.permission_evidence,approved=excluded.approved,sha256=excluded.sha256,youtube_video_id=COALESCE(excluded.youtube_video_id,sources.youtube_video_id),automation_source_id=excluded.automation_source_id", (str(resolved), creator.strip(), source_url.strip(), evidence.strip(), int(approved), digest, youtube_video_id, automation_source_id))

    def source(self, path: str):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM sources WHERE path=?", (str(Path(path).expanduser().resolve()),)).fetchone()
            return dict(row) if row else None

    def add_campaign(self, name: str, brief: str):
        if not name.strip() or not brief.strip():
            raise ValueError("Campaign name and brief are required")
        with self.connect() as db:
            db.execute("INSERT INTO campaigns(name,brief) VALUES(?,?)", (name.strip(), brief.strip()))

    def campaigns(self):
        with self.connect() as db:
            return db.execute("SELECT name, brief FROM campaigns ORDER BY id DESC").fetchall()

    def counts(self):
        with self.connect() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("sources", "campaigns")}
