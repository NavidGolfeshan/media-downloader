"""Where things live, both when run as `python main.py` and as a packaged .exe."""

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# MediaDownloader-console.exe is the troubleshooting build: console window, opens the browser.
CONSOLE_BUILD = FROZEN and Path(sys.executable).stem.lower().endswith("console")

if FROZEN:
    # Packaged with PyInstaller.
    APP_DIR = Path(sys.executable).resolve().parent             # folder with the .exe
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))      # bundled static/ etc.
    DATA_DIR = Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader"
    DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "MediaDownloader")
else:
    APP_DIR = RESOURCE_DIR = DATA_DIR = Path(__file__).resolve().parent
    DEFAULT_DOWNLOAD_DIR = "downloads"


def add_bin_to_path() -> None:
    """Put ./bin (ffmpeg, ffprobe, node) first on PATH, so nothing has to be installed."""
    bin_dir = APP_DIR / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def log_to_file_if_no_console() -> None:
    """A windowed .exe has no console (sys.stdout is None), so write the log to a file instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DATA_DIR / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log
