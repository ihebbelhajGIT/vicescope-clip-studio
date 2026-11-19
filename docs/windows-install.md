# Install ViceScope on Windows 10/11 x64

Use Windows 10 version 1809 or later or Windows 11, with 64-bit Python 3.12. From PowerShell, install Python and a full FFmpeg distribution. Deno supports current yt-dlp YouTube challenges.

```powershell
winget install --exact --id Python.Python.3.12
winget install --exact --id Gyan.FFmpeg
winget install --exact --id DenoLand.Deno
```

Open a new PowerShell session and launch from your checkout.

```powershell
Set-Location 'D:\vicescope-clip-studio'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ffmpeg -version
ffprobe -version
deno --version
.\.venv\Scripts\python.exe -m app.desktop
```

If your media tools are in a custom folder, set `$env:VICESCOPE_FFMPEG_BIN = 'C:\path\to\ffmpeg\bin'` before launch. The folder must contain both executables. The Settings page displays the paths ViceScope resolved. Check that FFmpeg includes the `subtitles` filter and the `libx264` encoder.

For the local packaged executable, the PowerShell launcher uses `.vicescope-runtime\temp` and `.vicescope-runtime\cache` under this checkout and restores the caller's environment variables on exit. It does not change the app data directory or require an activated virtual environment:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\launch-vicescope.ps1
```

By default it uses `.venv\ffmpeg-dist\ffmpeg-9.0.2-essentials_build\bin`. If your FFmpeg is elsewhere, append `-FFmpegBin 'D:\path\to\ffmpeg\bin'`. Run with `-SmokeTest` to check the packaged dependencies without opening the full interface. The new cache location may download the Whisper model again on first transcription. This project-local runtime folder is ignored by Git and should be retained if you want to keep cached models.

ViceScope retains its existing database, OAuth client file, and logs in `%LOCALAPPDATA%\ViceScopeClipStudio`. Whisper downloads are cached in its `Cache\models` subfolder. YouTube credentials and active resumable sessions use Windows Credential Manager. Follow the [YouTube setup guide](youtube-setup.md) to configure the OAuth client and channel. See [Build and package](build-and-package.md) to create a standalone executable.
