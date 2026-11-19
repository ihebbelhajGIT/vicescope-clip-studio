from pathlib import Path
import platform
import sys

if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
    raise SystemExit("Run this spec with x64 Python on Windows")

sys.path.insert(0, SPECPATH)
from desktop_build import desktop_analysis

a = desktop_analysis(Path(SPECPATH).resolve().parent, "keyring.backends.Windows")
pyz = PYZ(a.pure)
# A console bootloader keeps stdio available when the executable runs its yt-dlp worker.
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="ViceScopeClipStudio", console=True, upx=False)
