"""Immutable snapshots of reviewed source and selected segment.

These records do not by themselves authorize an output or enable publication.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from app.core import require_rights


@dataclass(frozen=True)
class ClipJob:
    id: str
    source_id: int
    source_path: str
    source_sha256: str
    start: float
    end: float


def create_job(store, source: str, start: float, end: float) -> ClipJob:
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("Select a valid start and end time")
    record = require_rights(store, source)
    job = ClipJob(str(uuid.uuid4()), record["id"], record["path"], record["sha256"], start, end)
    with store.connect() as db:
        db.execute("INSERT INTO clip_jobs(id, source_id, source_path, source_sha256, start, end) VALUES(?,?,?,?,?,?)", (job.id, job.source_id, job.source_path, job.source_sha256, job.start, job.end))
    return job


def verify_job(store, job: ClipJob):
    record = require_rights(store, job.source_path)
    if record["id"] != job.source_id or record["sha256"] != job.source_sha256:
        raise PermissionError("Source approval no longer matches the job snapshot")
    return record
