from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from app.storage import file_hash


def discover(query: str, limit: int = 20):
    query = query.strip()
    if not query or not 1 <= limit <= 50:
        raise ValueError("Enter a search query and a limit from 1 to 50")
    cmd = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--flat-playlist", "--dump-single-json", f"ytsearch{limit}:{query}"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, check=True)
    entries = json.loads(result.stdout).get("entries") or []
    return [{"title": e.get("title") or "Unknown", "creator": e.get("channel") or e.get("uploader") or "Unknown", "url": e.get("webpage_url") or ("https://www.youtube.com/watch?v=" + e["id"] if e.get("id") else ""), "views": e.get("view_count"), "duration": e.get("duration"), "live_status": e.get("live_status") or "unknown", "footage": "Unverified", "authorization": "Not established"} for e in entries if e]


def transcribe(path: str, model: str = "base"):
    from faster_whisper import WhisperModel
    segments, _ = WhisperModel(model, device="cpu", compute_type="int8").transcribe(path, word_timestamps=True, vad_filter=True)
    return [{"start": w.start, "end": w.end, "text": w.word.strip(), "probability": w.probability} for segment in segments for w in (segment.words or []) if w.probability >= 0.5]


def require_rights(store, source: str):
    record = store.source(source)
    if not record or not record["approved"] or not record["permission_evidence"].strip():
        raise PermissionError("Publication export requires explicit approval and evidence for this exact source")
    try:
        matches = bool(record.get("sha256")) and file_hash(Path(source)) == record["sha256"]
    except OSError as exc:
        raise PermissionError("Approved source is unavailable") from exc
    if not matches:
        raise PermissionError("Source content changed since approval; review rights again")
    return record


def write_package(store, source: str, clip: str, destination: str, title: str, description: str):
    require_rights(store, source)
    raise NotImplementedError("Publishing packages are disabled until exported clips have verified source provenance")
