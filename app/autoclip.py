"""Automatic local-source generation. Network acquisition is deliberately absent."""
from pathlib import Path
import uuid
from app.core import require_rights, transcribe
from app.highlights import analyze_audio
from app.review import ReviewQueue
from app.video import export_bundle


def non_overlapping(candidates, maximum=3, min_score=0.1):
    selected = []
    for item in candidates:
        if item.score < min_score or item.end <= item.start: continue
        if any(max(0, min(item.end, other.end) - max(item.start, other.start)) > 0 for other in selected): continue
        selected.append(item)
        if len(selected) >= maximum: break
    return selected


def generate(store, source, output_root, maximum=3, min_score=0.1, transcriber=transcribe):
    if not 1 <= maximum <= 5 or not 0 <= min_score <= 1:
        raise ValueError('Maximum clips must be 1–5 and score must be 0–1')
    rights = require_rights(store, source)
    words = transcriber(source)
    after = require_rights(store, source)
    if rights['sha256'] != after['sha256']: raise PermissionError('Source changed during transcription')
    if not words: raise ValueError('No reliable speech found; review this source manually')
    candidates = non_overlapping(analyze_audio(store, source, words), maximum, min_score)
    if not candidates: raise ValueError('No candidates met the minimum score; review this source manually')
    root = Path(output_root).resolve()
    if not root.is_dir(): raise ValueError('Choose an existing output directory')
    queue = ReviewQueue(store)
    results = []
    for candidate in candidates:
        creator = rights['creator'] or 'creator'
        metadata = {'title': f'A moment from {creator}', 'description': f'Credit: {creator}\n{rights["source_url"]}'.strip(), 'hashtags': ['#Shorts']}
        destination = root / ('short-' + uuid.uuid4().hex)
        result = export_bundle(store, source, candidate.start, candidate.end, str(destination), words,
                               f'A moment from {creator}', metadata['title'], metadata['description'])
        item_id = queue.add(result['job_id'], candidate.score, candidate.explanation, metadata)
        results.append(item_id)
    return results


def regenerate(store, item_id, hook, transcriber=transcribe):
    """Re-render the selected time range as a new, unapproved version."""
    import json
    queue = ReviewQueue(store)
    item = queue.get(item_id)
    rights = queue.validate(item)
    queue.supersede(item_id)
    source = item['source_path']
    words = transcriber(source)
    after = require_rights(store, source)
    if after['sha256'] != rights['sha256']: raise PermissionError('Source changed during transcription')
    if not words: raise ValueError('No reliable speech found; manual review required')
    metadata = json.loads(item['metadata'])
    destination = Path(item['output_path']).parent.parent / ('short-' + uuid.uuid4().hex)
    result = export_bundle(store, source, item['start'], item['end'], str(destination), words, hook,
                           metadata['title'], metadata['description'])
    return queue.add(result['job_id'], item['score'], item['explanation'], metadata, parent_item_id=item_id)
