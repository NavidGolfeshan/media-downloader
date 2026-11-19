# PyInstaller spec. Build with:  pyinstaller --noconfirm --clean MediaDownloader.spec
from PyInstaller.utils.hooks import collect_all

datas = [("static", "static")]
binaries = []
hiddenimports = []

# yt-dlp loads its site extractors dynamically, and ships a JS helper package,
# so PyInstaller needs to be told to take everything.
# webview / clr_loader / pythonnet = the native window (Windows only, skipped if not installed).
for package in ("yt_dlp", "yt_dlp_ejs", "webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["cli"],
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)
# Main app: no console window, opens in its own window; closing the window quits the app.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaDownloader",
    console=False,
)
# Troubleshooting copy: shows a console and opens your browser instead.
exe_console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MediaDownloader-console",
    console=True,
)
coll = COLLECT(exe, exe_console, a.binaries, a.datas, name="MediaDownloader")
