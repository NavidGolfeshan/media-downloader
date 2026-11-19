"""The download library: folder layout, "already downloaded?" lookups, moving files in.

Layout (the first parts come from the URL that was looked up):

    <download folder>/<channel>/<section>/<Title> [<id>][<quality>].<ext>
    e.g.  ExampleChannel/videos/Some Video [abc123XYZ00][480p].mp4
          ExampleChannel/playlists/My Mix/Some Track [def456UVW11][192kbps].mp3

The "[id]" and the "[quality]" at the end of each file name are how a file is
recognised later, so the folder itself is the database: move or delete a file
and the library follows. (Files saved by older versions in
.../video/480p/ sub-folders are still recognised.)
"""

import os
import re
import shutil
from pathlib import Path
from typing import Any

MEDIA_EXTS = {".mp3", ".mp4", ".mkv", ".webm"}
TEMP_EXTS = {".part", ".ytdl", ".temp"}
PARTIAL_DIR = ".partial"  # unfinished downloads wait here (so a re-run can resume)
KINDS = {"mp3": "music", "mp4": "video"}

_NAME_TAGS = re.compile(r"\[([^\[\]]+)\](?:\[(\d+(?:kbps|p))\])?$")
_QUALITY = re.compile(r"^(\d+)(kbps|p)$")


def safe_name(value: str, fallback: str = "unknown") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")
    return (value or fallback)[:100]


def kind_for(mode: str) -> str:
    return KINDS[mode]


def quality_label(mode: str, quality: str) -> str:
    return f"{quality}kbps" if mode == "mp3" else f"{quality}p"


def destination(root: Path | str, channel: str, section: list[str]) -> Path:
    parts = [safe_name(channel)] + [safe_name(part, "videos") for part in section]
    return Path(root).joinpath(*parts)


def media_id(filename: str) -> str | None:
    """The yt-dlp id in 'Title [id].ext' or 'Title [id][480p].ext', or None."""
    match = _NAME_TAGS.search(Path(filename).stem)
    return match.group(1) if match else None


def name_quality(filename: str) -> str:
    """The '480p' / '192kbps' tag in 'Title [id][480p].ext', or ''."""
    match = _NAME_TAGS.search(Path(filename).stem)
    return (match.group(2) or "") if match else ""


def describe(kind: str, quality: str | None) -> str:
    """'video 720p' / 'music 192kbps' - what a person reads in a message."""
    return f"{kind} {quality}" if quality else kind


def scan(root: Path | str) -> dict[str, list[dict[str, Any]]]:
    """Index every media file under root by its id."""
    found: dict[str, list[dict[str, Any]]] = {}
    base = Path(root)
    if not base.is_dir():
        return found
    for folder, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        folder_path = Path(folder)
        for name in names:
            path = folder_path / name
            if path.suffix.lower() not in MEDIA_EXTS:
                continue
            item_id = media_id(name)
            if not item_id:
                continue
            kind = "music" if path.suffix.lower() == ".mp3" else "video"
            quality = name_quality(name)
            if (not quality and folder_path.parent.name in KINDS.values()
                    and _QUALITY.match(folder_path.name)):
                quality = folder_path.name  # saved by an older version, in .../video/480p/
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.setdefault(item_id, []).append({
                "kind": kind,
                "quality": quality,
                "label": describe(kind, quality),
                "rel": path.relative_to(base).as_posix(),
                "size": size,
            })
    return found


def lookup(root: Path | str, ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    index = scan(root)
    return {item_id: index[item_id] for item_id in ids if item_id in index}


def listing(root: Path | str) -> dict[str, Any]:
    """Everything in the library grouped by folder, for the Library view."""
    base = Path(root)
    folders: dict[str, list[dict[str, Any]]] = {}
    for records in scan(base).values():
        for record in records:
            folder, _, name = record["rel"].rpartition("/")
            folders.setdefault(folder or ".", []).append({"name": name, "size": record["size"]})
    groups = []
    for folder in sorted(folders, key=str.lower):
        files = sorted(folders[folder], key=lambda f: f["name"].lower())
        groups.append({
            "folder": folder,
            "count": len(files),
            "size": sum(f["size"] for f in files),
            "files": files,
        })
    return {
        "root": str(base),
        "folders": groups,
        "count": sum(g["count"] for g in groups),
        "size": sum(g["size"] for g in groups),
    }


def place(staging: Path, dest: Path, tag: str) -> list[Path]:
    """Move a finished download (media + subtitles) from staging into its library folder,
    adding the quality tag to the names: 'Title [id].mp4' -> 'Title [id][480p].mp4'.

    An older copy of the same item with the same quality is replaced, so re-downloading
    never leaves two files; other qualities are left alone. Returns the final paths.
    """
    dest.mkdir(parents=True, exist_ok=True)
    files = [p for p in staging.iterdir() if p.is_file() and p.suffix.lower() not in TEMP_EXTS]
    ids = {media_id(p.name) for p in files if p.suffix.lower() in MEDIA_EXTS} - {None}

    def tagged(name: str) -> str:
        for item_id in ids:
            if f"[{item_id}]" in name:
                return name.replace(f"[{item_id}]", f"[{item_id}][{tag}]", 1)
        return name

    for old in dest.iterdir():
        if old.is_file() and any(f"[{i}][{tag}]" in old.name for i in ids):
            old.unlink()
    placed = []
    for path in files:
        target = dest / tagged(path.name)
        shutil.move(str(path), str(target))
        placed.append(target)
    shutil.rmtree(staging, ignore_errors=True)
    return placed
