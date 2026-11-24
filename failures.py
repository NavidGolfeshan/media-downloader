"""Items that failed to download, remembered in <download folder>/.media-downloader/failed.json

The file follows the library's folder layout. Each folder (relative to the
download folder) lists the items that failed there:

    {
      "version": 1,
      "folders": {
        "ExampleChannel/playlists/My Mix": [
          {"id": "def456UVW11", "title": "Some Track", "url": "https://...",
           "error": "short reason", "format": "music 192kbps",
           "failed_at": "2026-11-19T15:02:11"}
        ]
      }
    }

An item is written once: if its id (or its URL, when it has no id) is already in
the file, nothing is added. When the item is downloaded successfully later, its
record is removed. The page uses the file to mark failed items in red.
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import errorlog
import library

logger = logging.getLogger(__name__)

FILE_NAME = "failed.json"
_lock = threading.Lock()


def file_path(root: Path | str) -> Path:
    return library.data_dir(root) / FILE_NAME


def short_error(error: object) -> str:
    """The first line of an error, which is the part a person needs to read."""
    for line in errorlog.clean(error).splitlines():
        line = line.strip()
        if line:
            return line.removeprefix("ERROR:").strip()[:300]
    return "Unknown error"


def folder_key(root: Path | str, destination: Path | str) -> str:
    try:
        return Path(destination).relative_to(root).as_posix() or "."
    except ValueError:
        return str(destination)


def _empty() -> dict[str, Any]:
    return {"version": 1, "folders": {}}


def _load(root: Path | str) -> dict[str, Any]:
    path = file_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except OSError:
        return _empty()
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("folders"), dict):
        # Unreadable: keep the broken file for inspection instead of overwriting it.
        try:
            path.replace(path.with_name(path.name + ".corrupt"))
        except OSError:
            pass
        return _empty()
    return data


def _save(root: Path | str, data: dict[str, Any]) -> None:
    path = file_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _matches(entry: dict[str, Any], item_id: str | None, url: str | None) -> bool:
    return bool((item_id and entry.get("id") == item_id) or (url and entry.get("url") == url))


def record(
    root: Path | str,
    folder: str,
    *,
    id: str | None,
    title: str,
    url: str,
    error: object,
    fmt: str = "",
) -> bool:
    """Add the item unless it is already listed (in any folder). True if it was added."""
    with _lock:
        data = _load(root)
        for entries in data["folders"].values():
            if any(_matches(e, id, url) for e in entries):
                return False
        data["folders"].setdefault(folder, []).append({
            "id": id or "",
            "title": title,
            "url": url,
            "error": short_error(error),
            "format": fmt,
            "failed_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save(root, data)
    return True


def clear(root: Path | str, *, id: str | None, url: str | None) -> bool:
    """Forget the item (it downloaded fine). True if something was removed."""
    if not file_path(root).exists():
        return False
    with _lock:
        data = _load(root)
        removed = False
        for folder in list(data["folders"]):
            kept = [e for e in data["folders"][folder] if not _matches(e, id, url)]
            if len(kept) != len(data["folders"][folder]):
                removed = True
                if kept:
                    data["folders"][folder] = kept
                else:
                    del data["folders"][folder]
        if removed:
            _save(root, data)
    return removed


def lookup(root: Path | str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The failure record of each id that has one, with the folder it failed in."""
    if not file_path(root).exists():
        return {}
    wanted = set(ids)
    found: dict[str, dict[str, Any]] = {}
    with _lock:
        data = _load(root)
    for folder, entries in data["folders"].items():
        for entry in entries:
            if entry.get("id") in wanted:
                found[entry["id"]] = {**entry, "folder": folder}
    return found


def note_failure(
    root: Path | str,
    destination: Path | str,
    *,
    id: str | None,
    title: str,
    url: str,
    error: object,
    fmt: str,
    exc: BaseException | None = None,
    what: str = "Download failed",
    remember: bool = True,
) -> None:
    """Save the full error to the log and (unless remember is False) list the item as failed."""
    errorlog.write(
        root, what, error,
        {"Title": title, "URL": url, "Id": id, "Format": fmt, "Folder": str(destination)}, exc,
    )
    if not remember:
        return
    try:
        record(root, folder_key(root, destination), id=id, title=title, url=url, error=error, fmt=fmt)
    except Exception:  # noqa: BLE001 - never let bookkeeping break a download
        logger.warning("Could not update %s", FILE_NAME, exc_info=True)


def note_success(root: Path | str, *, id: str | None, url: str | None) -> None:
    try:
        clear(root, id=id, url=url)
    except Exception:  # noqa: BLE001
        logger.warning("Could not update %s", FILE_NAME, exc_info=True)
