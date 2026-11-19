"""Shared PyInstaller analysis for native desktop builds."""
from PyInstaller.building.build_main import Analysis
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata


def desktop_analysis(root, credential_backend=None):
    datas = collect_data_files("faster_whisper", includes=["assets/silero_vad*.onnx"])
    datas += collect_data_files("yt_dlp_ejs")
    datas += copy_metadata("yt-dlp", recursive=True)
    datas += copy_metadata("faster-whisper", recursive=True)
    binaries = collect_dynamic_libs("ctranslate2")
    imports = collect_submodules("yt_dlp")
    if credential_backend:
        imports.append(credential_backend)
    for path in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        datas.append((str(root / path), "."))
    return Analysis(
        [str(root / "app" / "desktop.py")],
        pathex=[str(root)],
        binaries=binaries,
        datas=datas,
        hiddenimports=imports,
        excludes=["torch", "tensorflow", "IPython", "tkinter"],
        noarchive=False,
    )
