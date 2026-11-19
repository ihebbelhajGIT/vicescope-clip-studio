# Build and package ViceScope

Build on the target OS with Python 3.12 and the target architecture. Install the media tools before running the smoke tests. Neither package includes the FFmpeg or FFprobe command-line executables, Deno, or downloaded Whisper transcription weights. Use the [Windows](windows-install.md) or [macOS](macos-install.md) instructions to install those prerequisites.

## Windows x64 standalone executable

From the checkout in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm packaging/windows.spec
$env:QT_QPA_PLATFORM = 'offscreen'
& .\dist\ViceScopeClipStudio.exe --smoke-test
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item Env:QT_QPA_PLATFORM
& .\dist\ViceScopeClipStudio.exe
```

The one-file executable includes Python and the application dependencies. A console window stays open to support the bundled yt-dlp worker's standard streams.

## macOS arm64 application

From the checkout in Terminal:

```bash
"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/python -c 'import platform; assert platform.machine() == "arm64"'
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m PyInstaller --noconfirm packaging/macos.spec
file 'dist/ViceScope Clip Studio.app/Contents/MacOS/ViceScopeClipStudio'
QT_QPA_PLATFORM=offscreen 'dist/ViceScope Clip Studio.app/Contents/MacOS/ViceScopeClipStudio' --smoke-test
open 'dist/ViceScope Clip Studio.app'
```

The default build is for local use. To create a disk image containing the application bundle:

```bash
mkdir -p build/dmg-root
ditto 'dist/ViceScope Clip Studio.app' 'build/dmg-root/ViceScope Clip Studio.app'
hdiutil create -ov -format UDZO -volname 'ViceScope Clip Studio' -srcfolder build/dmg-root dist/ViceScopeClipStudio-arm64.dmg
```

Distribution to other Macs may require Developer ID signing and notarization. The build does not configure either.

## Smoke tests and component licenses

The native CI matrix runs the full pytest suite and checks the resulting executable or app bundle. The artifact smoke test checks the Qt multimedia backend can initialize, CTranslate2 has a CPU backend, the VAD asset is present, the native credential backend can be selected, FFmpeg provides the render filters/codecs, and the bundled yt-dlp subprocess starts. The full tests cover generated MP4 rendering, review approval, and mocked YouTube operations. Manually play a local H.264/AAC clip on each native OS to verify its audio and video output. Live YouTube verification requires the owner's Google authorization.

The small Silero VAD asset shipped inside faster-whisper is MIT-licensed and is included for voice activity detection. Whisper transcription models are fetched at runtime. The PySide6/Qt Multimedia and PyAV wheels can include FFmpeg shared libraries for decoding. Their licenses and linked codec configuration depend on the exact wheels; review those artifacts before distributing a package. Preserve dependency license notices and meet the applicable Qt, FFmpeg and linked-library redistribution terms. The packaging commands here create local builds. [Third-party notices](../THIRD_PARTY_NOTICES.md)
