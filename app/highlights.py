"""Rank precomputed highlight signals; timestamps are in seconds.

Signals are supplied by analysis adapters, not inferred from metadata. Missing
signals do not contribute to a score. Results are suggestions for human review.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    score: float
    explanation: str


def rank_candidates(duration: float, audio: Sequence[tuple[float, float]],
                    scenes: Sequence[float], words: Sequence[dict],
                    window: float = 20.0, limit: int = 5) -> list[Candidate]:
    if not math.isfinite(duration) or not math.isfinite(window) or duration <= 0 or window <= 0 or limit < 1:
        raise ValueError("Duration, window and limit must be positive")
    if not audio and not scenes and not words:
        return []
    candidates = []
    step = window / 2
    start = 0.0
    while start < duration:
        end = min(start + window, duration)
        if end - start < min(3.0, duration):
            break
        levels = [max(0.0, min(1.0, float(v))) for t, v in audio if start <= t < end and math.isfinite(float(v))]
        cuts = sum(1 for t in scenes if start <= t < end)
        speech = sum(1 for w in words if start <= float(w["start"]) < end and w.get("text", "").strip())
        components = []
        score = 0.0
        if levels:
            score += max(levels) * 0.4
            components.append("audio activity")
        if cuts:
            score += min(cuts / 4, 1) * 0.3
            components.append(f"{cuts} scene changes")
        if speech:
            score += min(speech / 30, 1) * 0.3
            components.append(f"{speech} spoken words")
        if components:
            candidates.append(Candidate(round(start, 3), round(end, 3), round(score, 3), ", ".join(components)))
        start += step
    candidates.sort(key=lambda item: (-item.score, item.start))
    return candidates[:limit]


def analyze_audio(store, source: str, words: list[dict]):
    """Extract one RMS measurement per second and rank review candidates."""
    import re
    import subprocess
    from pathlib import Path
    from app.core import require_rights
    from app.video import probe, tool_path

    before = require_rights(store, source)
    duration = float(probe(Path(source))["format"]["duration"])
    cmd = [tool_path("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(Path(source).resolve()), "-vn", "-af", "aresample=16000,asetnsamples=n=16000:p=0,astats=metadata=1:reset=1,ametadata=print:file=-", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=3600)
    after = require_rights(store, source)
    if before["sha256"] != after["sha256"]:
        raise PermissionError("Source changed during analysis")
    levels = []
    current_time = None
    for line in result.stdout.splitlines():
        match = re.search(r"pts_time:([\d.]+)", line)
        if match: current_time = float(match.group(1))
        if line.startswith("lavfi.astats.Overall.RMS_level=") and current_time is not None:
            level = float(line.split("=", 1)[1])
            if math.isfinite(level): levels.append((current_time, max(0.0, min(1.0, (level + 60.0) / 60.0))))
    return rank_candidates(duration, levels, [], words, window=min(20.0, duration))
