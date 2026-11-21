"""Automatic local-source generation. Network acquisition is deliberately absent."""
from pathlib import Path
import uuid
import json
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


def generate(store, source, output_root, maximum=3, min_score=0.1, transcriber=transcribe, clip_duration=20.0, progress=None, cancelled=None, cpu_threads=0, automation_job_id=None):
    def report(value, message):
        if progress: progress(value, message)
    def check_cancel():
        if cancelled and cancelled(): raise InterruptedError('Generation cancelled; completed clips remain in the review queue')
    if not 1 <= maximum <= 5 or not 0 <= min_score <= 1:
        raise ValueError('Maximum clips must be 1–5 and score must be 0–1')
    check_cancel()
    report(0, 'Checking source permission')
    rights = require_rights(store, source)
    report(10, 'Transcribing source')
    if not isinstance(cpu_threads, int) or cpu_threads < 0: raise ValueError('Invalid CPU thread count')
    words = transcriber(source, cpu_threads=cpu_threads) if cpu_threads and transcriber is transcribe else transcriber(source)
    check_cancel()
    after = require_rights(store, source)
    if rights['sha256'] != after['sha256']: raise PermissionError('Source changed during transcription')
    if not words: raise ValueError('No reliable speech found; review this source manually')
    report(30, 'Analyzing audio and scenes')
    candidates = non_overlapping(analyze_audio(store, source, words, clip_duration, cpu_threads), maximum, min_score)
    check_cancel()
    if not candidates: raise ValueError('No candidates met the minimum score; review this source manually')
    root = Path(output_root).resolve()
    if not root.is_dir(): raise ValueError('Choose an existing output directory')
    queue = ReviewQueue(store)
    results = []
    if automation_job_id:
        with store.connect() as db:
            db.execute('INSERT INTO automation_plans(job_id,expected_count) VALUES(?,?)', (automation_job_id, len(candidates)))
    for index, candidate in enumerate(candidates):
        check_cancel()
        report(40 + int(60 * index / len(candidates)), f'Rendering clip {index + 1} of {len(candidates)}')
        creator = rights['creator'] or 'creator'
        metadata = {'title': f'A moment from {creator}', 'description': f'Credit: {creator}\n{rights["source_url"]}'.strip(), 'hashtags': ['#Shorts']}
        destination = root / ('short-' + uuid.uuid4().hex)
        if automation_job_id:
            with store.connect() as db:
                db.execute('INSERT INTO automation_outputs(job_id,ordinal,directory,score,explanation,metadata) VALUES(?,?,?,?,?,?)',
                           (automation_job_id,index,str(destination),candidate.score,candidate.explanation,json.dumps(metadata)))
        result = export_bundle(store, source, candidate.start, candidate.end, str(destination), words,
                               f'A moment from {creator}', metadata['title'], metadata['description'], mark_complete=False, cpu_threads=cpu_threads)
        item_id = queue.add(result['job_id'], candidate.score, candidate.explanation, metadata, mark_completed=True)
        results.append(item_id)
    report(100, 'Clips ready for review')
    return results


def regenerate(store, item_id, hook, transcriber=transcribe):
    """Re-render the selected time range as a new, unapproved version."""
    import json
    queue = ReviewQueue(store)
    item = queue.get(item_id)
    rights = queue.validate(item)
    source = item['source_path']
    words = transcriber(source)
    after = require_rights(store, source)
    if after['sha256'] != rights['sha256']: raise PermissionError('Source changed during transcription')
    if not words: raise ValueError('No reliable speech found; manual review required')
    metadata = json.loads(item['metadata'])
    destination = Path(item['output_path']).parent.parent / ('short-' + uuid.uuid4().hex)
    result = export_bundle(store, source, item['start'], item['end'], str(destination), words, hook,
                           metadata['title'], metadata['description'], mark_complete=False)
    new_id = queue.add(result['job_id'], item['score'], item['explanation'], metadata, parent_item_id=item_id, mark_completed=True)
    queue.validate(queue.get(new_id))
    queue.supersede_if_unchanged(item)
    return new_id
