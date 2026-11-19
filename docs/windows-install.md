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

ViceScope retains its existing database, OAuth client file, and logs in `%LOCALAPPDATA%\ViceScopeClipStudio`. Whisper downloads are cached in its `Cache\models` subfolder. YouTube credentials and active resumable sessions use Windows Credential Manager. Follow the [YouTube setup guide](youtube-setup.md) to configure the OAuth client and channel. See [Build and package](build-and-package.md) to create a standalone executable.
