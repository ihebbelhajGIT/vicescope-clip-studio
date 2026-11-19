from pathlib import Path
import platform
import sys

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise SystemExit("Run this spec with native arm64 Python on macOS")

sys.path.insert(0, SPECPATH)
from desktop_build import desktop_analysis

a = desktop_analysis(Path(SPECPATH).resolve().parent, "keyring.backends.macOS")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ViceScopeClipStudio",
          console=False, target_arch="arm64", upx=False)
files = COLLECT(exe, a.binaries, a.datas, name="ViceScopeClipStudio", upx=False)
bundle = BUNDLE(files, name="ViceScope Clip Studio.app", bundle_identifier="com.vicescope.clipstudio",
                info_plist={"NSHighResolutionCapable": True})
