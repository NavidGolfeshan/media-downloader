"""A playlist file (.m3u8) saved next to the downloaded tracks, so you can tell what the playlist was.

It lives in the same folder as the files (CHANNEL/playlists/NAME/music or .../video) and lists
every item of the playlist in its order, not only the ones downloaded in this run:

  * items that are in the folder are normal entries, so a media player can open the file;
  * the others are comment lines with their title and link, so the file also says what is missing.

It is rewritten after every run from the whole playlist and the folder's current contents, so
downloading the rest of a playlist later completes the same file.
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import library


def _one_line(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def label(entry: dict[str, Any]) -> str:
    """'Artist - Title' when the playlist knows who uploaded the item (playlists mix artists)."""
    title = _one_line(entry.get("title")) or "Untitled"
    artist = _one_line(entry.get("uploader"))
    if artist and not title.lower().startswith(artist.lower()):
        return f"{artist} - {title}"
    return title


def _newest_by_id(folder: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in library.MEDIA_EXTS:
            continue
        item_id = library.media_id(path.name)
        if item_id and (item_id not in found or path.stat().st_mtime >= found[item_id].stat().st_mtime):
            found[item_id] = path
    return found


def write(
    folder: Path,
    title: str,
    source_url: str | None,
    entries: list[dict[str, Any]],
) -> Path | None:
    """(Re)write '<title>.m3u8' in folder. Returns its path, or None for an empty playlist."""
    if not entries:
        return None
    here = _newest_by_id(folder)
    present = sum(1 for entry in entries if entry.get("id") in here)

    lines = ["#EXTM3U", f"#PLAYLIST:{_one_line(title)}"]
    if source_url:
        lines.append(f"# Source: {_one_line(source_url)}")
    lines.append(f"# Written by Media Downloader on {datetime.now():%Y-%m-%d %H:%M}")
    lines.append(f"# {len(entries)} items in the playlist, {present} in this folder")
    for entry in entries:
        name = label(entry)
        path = here.get(entry.get("id"))
        if path is not None:
            lines.append(f"#EXTINF:{entry.get('duration') or -1},{name}")
            lines.append(path.name)
        else:
            lines.append(f"# Not in this folder: {name} | {_one_line(entry.get('url'))}")

    target = folder / (library.safe_name(title, "playlist") + ".m3u8")
    temp = target.with_name(target.name + ".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    os.replace(temp, target)
    return target
