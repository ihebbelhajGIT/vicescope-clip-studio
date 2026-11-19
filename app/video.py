"""Local, rights-gated video rendering and output verification."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.provenance import create_job, verify_job
from app.storage import file_hash


def tool_path(name: str) -> str:
    configured = Path(__file__).resolve().parent.parent / ".venv" / "ffmpeg-dist"
    found = list(configured.glob(f"*/bin/{name}.exe")) if configured.exists() else []
    executable = str(found[0]) if found else shutil.which(name)
    if not executable:
        raise FileNotFoundError(f"{name} was not found. Install FFmpeg and FFprobe or add them to PATH.")
    return str(Path(executable).resolve())


def probe(path: Path) -> dict:
    result = subprocess.run([tool_path("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def stamp(value: float) -> str:
    value = max(0, value)
    centis = round(value * 100)
    return f"{centis // 360000}:{centis // 6000 % 60:02}:{centis // 100 % 60:02}.{centis % 100:02}"


def ass_text(text: str) -> str:
    return text.replace("\\", " ").replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")


def subtitles_ass(words: list[dict], start: float, end: float, hook: str, credit: str) -> str:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,100,100,380,1
Style: Hook,Arial,66,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,8,100,100,220,1
Style: Credit,Arial,40,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,100,100,210,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    def add(s, e, style, text):
        if text.strip() and e > s:
            lines.append(f"Dialogue: 0,{stamp(s)},{stamp(e)},{style},,0,0,0,,{ass_text(text)}")
    add(0, min(4, end - start), "Hook", hook)
    add(0, end - start, "Credit", credit)
    for word in words:
        s, e = float(word["start"]), float(word["end"])
        if math.isfinite(s) and math.isfinite(e):
            add(max(0, s-start), min(end-start, e-start), "Caption", str(word.get("text", "")))
    return header + "\n".join(lines) + "\n"


def render(store, source: str, start: float, end: float, destination: str, words: list[dict], hook: str, title: str):
    job = create_job(store, source, start, end)
    rights = verify_job(store, job)
    target = Path(destination).resolve()
    if target.exists():
        raise FileExistsError("Choose a new output filename")
    info = probe(Path(source))
    duration = float(info["format"]["duration"])
    if end > duration + 0.05:
        raise ValueError("Clip end exceeds source duration")
    if not any(s.get("codec_type") == "audio" for s in info["streams"]):
        raise ValueError("Source needs an audio stream")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vicescope-") as tmp:
        directory = Path(tmp)
        (directory / "captions.ass").write_text(subtitles_ass(words, start, end, hook, rights["creator"]), encoding="utf-8")
        intermediate = directory / "render.mp4"
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,subtitles=captions.ass"
        cmd = [tool_path("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", str(start), "-i", str(Path(source).resolve()), "-t", str(end-start), "-vf", vf, "-r", "30", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(intermediate)]
        subprocess.run(cmd, cwd=directory, capture_output=True, text=True, check=True, timeout=3600)
        verify_job(store, job)
        output = probe(intermediate)
        video = next((s for s in output["streams"] if s.get("codec_type") == "video"), {})
        audio = next((s for s in output["streams"] if s.get("codec_type") == "audio"), {})
        if (video.get("codec_name"), video.get("width"), video.get("height"), audio.get("codec_name")) != ("h264", 1080, 1920, "aac"):
            raise ValueError("Rendered file failed codec or resolution verification")
        if abs(float(output["format"]["duration"]) - (end-start)) > 0.25:
            raise ValueError("Rendered duration differs from selected segment")
        digest = file_hash(intermediate)
        # Exclusive creation avoids overwriting existing user files.
        with target.open("xb") as out, intermediate.open("rb") as inp:
            shutil.copyfileobj(inp, out)
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS exports (job_id TEXT PRIMARY KEY, output_path TEXT NOT NULL, output_sha256 TEXT NOT NULL)")
        db.execute("INSERT INTO exports VALUES(?,?,?)", (job.id, str(target), digest))
    return {"job_id": job.id, "source_sha256": job.source_sha256, "output_sha256": digest, "output_path": str(target), "title": title, "creator": rights["creator"], "source_url": rights["source_url"]}


def prepare_package(store, result: dict, destination: str, description: str = "") -> Path:
    """Create metadata only for an intact, recorded export and approved source."""
    from app.provenance import ClipJob
    with store.connect() as db:
        db.row_factory = __import__("sqlite3").Row
        row = db.execute("SELECT j.*, e.output_path, e.output_sha256 FROM clip_jobs j JOIN exports e ON e.job_id=j.id WHERE j.id=?", (result["job_id"],)).fetchone()
    if row is None:
        raise PermissionError("No recorded export for this job")
    job = ClipJob(row["id"], row["source_id"], row["source_path"], row["source_sha256"], row["start"], row["end"])
    rights = verify_job(store, job)
    output = Path(row["output_path"])
    if file_hash(output) != row["output_sha256"]:
        raise PermissionError("Export was modified after rendering")
    data = {"job_id": job.id, "source_sha256": job.source_sha256, "output_sha256": row["output_sha256"], "exported_mp4": str(output), "title": result["title"], "description": description, "hashtags": ["#GTA6", "#Shorts"], "creator_credit": rights["creator"], "source_url": rights["source_url"], "rights_status": "user approved; evidence recorded locally", "status": "draft; not published"}
    path = Path(destination)
    with path.open("x", encoding="utf-8") as out:
        json.dump(data, out, ensure_ascii=False, indent=2)
    return path
