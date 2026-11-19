# Development status

2026-11-19: `.venv` uses Python 3.12.10 and initially contained only pip. Installing `requirements.txt` failed because the configured package source returned no PySide6 distribution. The pytest suite cannot run without pytest, and the desktop launch fails because PySide6 is missing. FFmpeg and FFprobe were not found on PATH. Git 2.39.0 is available; GitHub CLI reports its configured `ihebbelhajGIT` login is invalid and network access to GitHub is blocked. The user confirmed that username as the intended owner. No repository or commit was created.

Six standard-library unit tests pass via `.venv\Scripts\python.exe -m unittest discover -s tests -p '*_unittest.py' -v`. They cover candidate ranking and a persisted source/segment snapshot; they do not test media analysis, rendering, or the UI. The existing pytest tests were not run. Source records store SHA-256 hashes; existing approvals are revoked on migration. The job record binds source ID and hash to selected times, but is not wired into a media workflow and cannot establish output provenance. Publishing packages remain disabled.

In a normal PowerShell terminal with network access, from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.main
gh auth login --hostname github.com
gh auth status
ffmpeg -version
ffprobe -version
```

Install FFmpeg separately from a trusted distributor and make both binaries available on PATH if those final checks fail. No automatic publishing is implemented.
