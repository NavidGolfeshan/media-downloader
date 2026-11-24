"""Full error details, kept for later: <download folder>/.media-downloader/errors.log

The page only has room for a short version of an error; everything yt-dlp said
(and the Python traceback, when there is one) is appended here, one block per
error, so it can be read afterwards. Writing to the log never raises.
"""

import logging
import re
import threading
import traceback
from datetime import datetime
from pathlib import Path

import library

logger = logging.getLogger(__name__)

LOG_NAME = "errors.log"
MAX_BYTES = 2_000_000  # past this the log moves to errors.log.1 and a new one starts
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_lock = threading.Lock()


def clean(text: object) -> str:
    """The message without colour codes or surrounding blank space."""
    return _ANSI.sub("", str(text)).strip()


def log_path(root: Path | str) -> Path:
    return library.data_dir(root) / LOG_NAME


def write(
    root: Path | str,
    what: str,
    error: object,
    fields: dict[str, object] | None = None,
    exc: BaseException | None = None,
) -> None:
    """Append one block: when, what happened, the details given, the full error."""
    try:
        lines = [f"==== {datetime.now():%Y-%m-%d %H:%M:%S} | {what} ===="]
        for label, value in (fields or {}).items():
            if value:
                lines.append(f"{label}: {value}")
        lines += ["--- Error ---", clean(error) or "(no message)"]
        if exc is not None and exc.__traceback__ is not None:
            trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            lines += ["--- Traceback ---", clean(trace)]
        text = "\n".join(lines) + "\n\n"

        path = log_path(root)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                path.replace(path.with_name(LOG_NAME + ".1"))
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text)
    except Exception:  # noqa: BLE001 - a broken log must never break a download
        logger.warning("Could not write the error log", exc_info=True)
