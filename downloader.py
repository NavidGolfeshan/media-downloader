"""yt-dlp inspection and download helpers."""

import json
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Any, Callable

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.utils import DownloadCancelled

from config import settings

ProgressCallback = Callable[..., None]  # (stage, percent, **stats)

PREFERRED_HEIGHTS = [360, 480, 720, 1080, 1440, 2160]


class Cancelled(Exception):
    """Raised when the user cancels a running download."""


class ProgressMeter:
    """Combine yt-dlp's per-stream progress dicts into one figure for the whole item.

    A video download is really two streams (video + audio) plus a merge, and
    yt-dlp reports each stream on its own. This adds them up so the percentage,
    byte counts, speed and ETA describe the item the person actually asked for.
    """

    def __init__(self) -> None:
        self.streams: dict[str, tuple[int, int]] = {}  # stream -> (downloaded, total)
        self.expected = 0
        self.speed: float | None = None
        self.last_percent = 0.0

    def update(self, data: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        key = str(data.get("filename") or data.get("tmpfilename") or "")
        done = int(data.get("downloaded_bytes") or 0)
        total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
        if data.get("status") == "finished":
            total = done = int(data.get("total_bytes") or done)
        self.streams[key] = (done, total)

        # Size of every stream we will need, when the site announces it up front.
        info = data.get("info_dict") or {}
        sizes = [
            fmt.get("filesize") or fmt.get("filesize_approx")
            for fmt in (info.get("requested_formats") or [info])
        ]
        if sizes and all(sizes):
            self.expected = int(sum(sizes))

        downloaded = sum(d for d, _ in self.streams.values())
        total_bytes = max(self.expected, sum(t for _, t in self.streams.values()))

        if data.get("speed"):
            self.speed = float(data["speed"])
        eta = None
        if self.speed and total_bytes > downloaded:
            eta = (total_bytes - downloaded) / self.speed

        percent = min(100.0, downloaded * 100.0 / total_bytes) if total_bytes else 0.0
        self.last_percent = max(self.last_percent, percent)  # never jump backwards
        return self.last_percent, {
            "downloaded": downloaded,
            "total": total_bytes or None,
            "speed": self.speed,
            "eta": eta,
        }


def js_runtimes() -> dict[str, dict[str, str]]:
    """Find JavaScript runtimes on PATH so yt-dlp can solve YouTube's challenge.

    yt-dlp only enables Deno by default; Node.js and Bun must be switched on
    explicitly, so a machine with just Node installed would otherwise fail with
    "Requested format is not available".
    """
    found = {}
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            found[name] = {"path": path}
    return found


def common_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": True,
    }
    if settings.proxy:
        options["proxy"] = settings.proxy
    runtimes = js_runtimes()
    if runtimes:
        options["js_runtimes"] = runtimes
    if settings.cookie_file:
        options["cookiefile"] = settings.cookie_file
    elif settings.cookies_from_browser:
        options["cookiesfrombrowser"] = settings.cookies_from_browser
    return options


def normalize_entry_url(entry: dict[str, Any]) -> str | None:
    """Return a usable URL for a flat yt-dlp playlist entry."""
    value = entry.get("webpage_url") or entry.get("url")
    if not value:
        return None
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    # Flat YouTube entries commonly expose just a video ID.
    extractor = (entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    if "youtube" in extractor or re.fullmatch(r"[\w-]{11}", str(value)):
        return f"https://www.youtube.com/watch?v={value}"
    return None


def inspect_url(url: str) -> dict[str, Any]:
    options = common_options()
    options.update({
        "extract_flat": "in_playlist",
        "noplaylist": False,
    })
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("yt-dlp returned no information.")
    return info


# ie_key values / URL shapes that mean "this entry is a list of videos, not a video".
PLAYLIST_URL_PATTERN = re.compile(
    r"youtube\.com/(playlist\?|@[^/]+|channel/|c/|user/)|youtube\.com/.+/(videos|shorts|streams|playlists|releases)/?$",
    re.IGNORECASE,
)


def is_playlist_entry(entry: dict[str, Any]) -> bool:
    """True if a flat entry is itself a playlist / channel tab rather than a video."""
    if entry.get("_type") == "playlist":
        return True
    extractor = (entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    if extractor.endswith("tab") or "playlist" in extractor or "channel" in extractor:
        return True
    value = entry.get("webpage_url") or entry.get("url") or ""
    return bool(PLAYLIST_URL_PATTERN.search(str(value)))


def playlist_entry_url(entry: dict[str, Any]) -> str | None:
    value = entry.get("webpage_url") or entry.get("url")
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    entry_id = entry.get("id")
    if entry_id and "youtube" in (entry.get("ie_key") or entry.get("extractor_key") or "").lower():
        return f"https://www.youtube.com/playlist?list={entry_id}"
    return None


SECTION_NAMES = {"videos", "shorts", "streams", "playlists", "releases", "live"}


def channel_name(url: str, info: dict[str, Any] | None = None) -> str:
    """Folder name for whoever owns the page: the @handle when there is one."""
    info = info or {}
    match = re.search(r"/(@[^/?#]+)", unquote(url))
    if match:
        return match.group(1).lstrip("@")
    for key in ("uploader_id", "channel_id"):
        value = str(info.get(key) or "")
        if value.startswith("@"):
            return value.lstrip("@")
    for key in ("uploader_url", "channel_url", "playlist_uploader_id"):
        match = re.search(r"/(@[^/?#]+)", unquote(str(info.get(key) or "")))
        if match:
            return match.group(1).lstrip("@")
    for key in ("channel", "uploader", "playlist_uploader"):
        if info.get(key):
            return str(info[key])
    return urlparse(url).netloc.removeprefix("www.") or "unknown"


def section_parts(url: str, info: dict[str, Any], is_playlist: bool) -> list[str]:
    """Folder parts for the kind of page: ['videos'], ['shorts'], ['playlists', 'My list']."""
    if not is_playlist:
        return ["videos"]
    path = urlparse(url).path.rstrip("/").lower()
    last = path.rsplit("/", 1)[-1]
    if "youtube" in urlparse(url).netloc and last in SECTION_NAMES and last != "playlists":
        return [last]
    return ["playlists", str(info.get("title") or "playlist")]


def _duration(value: Any) -> int | None:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


# A bare channel address (no /videos, /shorts, ... on the end).
BARE_CHANNEL_PATTERN = re.compile(
    r"^(https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+))/?$",
    re.IGNORECASE,
)
# yt-dlp lists only Videos, Shorts and Live for a bare channel address, so these
# two tabs have to be looked for separately.
EXTRA_CHANNEL_TABS = (("playlists", "Playlists"), ("releases", "Releases"))


def probe_tab(url: str, label: str) -> dict[str, str] | None:
    """Return {"url", "title"} if the channel tab at `url` exists and has something in it."""
    options = common_options()
    options.update({"extract_flat": "in_playlist", "playlist_items": "1"})
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        entries = [entry for entry in (info or {}).get("entries") or [] if entry]
    except Exception:  # noqa: BLE001 - a missing tab is normal, not an error
        return None
    if not entries:
        return None
    return {"url": url, "title": str((info or {}).get("title") or label)}


def extra_channel_groups(url: str) -> list[dict[str, str]]:
    """The Playlists / Releases tabs of a channel, when the address is a bare channel."""
    match = BARE_CHANNEL_PATTERN.match(url.strip())
    if not match:
        return []
    groups = []
    for tab, label in EXTRA_CHANNEL_TABS:
        group = probe_tab(f"{match.group(1)}/{tab}", label)
        if group:
            groups.append(group)
    return groups


def inspect_media(url: str, channel_hint: str | None = None) -> dict[str, Any]:
    """Inspect a URL and return a UI-friendly description.

    Returns {"is_playlist": bool, "title": str, "channel": str, "section": [str],
    "items": [{"url", "title", "id"}]}. channel/section decide the library folder;
    channel_hint keeps the name stable when opening a playlist from a channel page.
    For a list, it also has "groups": [{"url", "title"}], the playlists or
    channel sections (Videos, Shorts, Playlists...) found inside it, which the UI
    lets the person open one level deeper. The playlist cap (MAX_PLAYLIST_ITEMS)
    applies to the videos.
    """
    info = inspect_url(url)
    raw_entries = info.get("entries")

    if raw_entries is None:
        title = str(info.get("title") or "video")
        return {
            "is_playlist": False,
            "title": title,
            "channel": channel_hint or channel_name(url, info),
            "section": section_parts(url, info, False),
            "items": [{"url": url, "title": title, "id": str(info.get("id") or "") or None,
                       "duration": _duration(info.get("duration"))}],
        }

    items = []
    groups = []
    seen_groups = set()
    for entry in raw_entries:
        if not entry:
            continue
        if is_playlist_entry(entry):
            group_url = playlist_entry_url(entry)
            if group_url and group_url not in seen_groups:
                seen_groups.add(group_url)
                groups.append({
                    "url": group_url,
                    "title": str(entry.get("title") or "Untitled"),
                })
            continue
        entry_url = normalize_entry_url(entry)
        if not entry_url:
            continue
        item = {
            "url": entry_url,
            "title": str(entry.get("title") or "Untitled"),
            "id": str(entry.get("id") or "") or None,
            "duration": _duration(entry.get("duration")),
        }
        uploader = str(entry.get("uploader") or entry.get("channel") or "").strip()
        if uploader:
            item["uploader"] = uploader  # playlists can mix artists; the playlist file shows who is who
        items.append(item)

    for group in extra_channel_groups(url):
        if group["url"] not in seen_groups:
            seen_groups.add(group["url"])
            groups.append(group)

    if settings.max_playlist_items > 0:
        items = items[:settings.max_playlist_items]
    if not items and not groups:
        raise RuntimeError("No videos or playlists were found at this address.")

    return {
        "is_playlist": True,
        "title": str(info.get("title") or "playlist"),
        "channel": channel_hint or channel_name(url, info),
        "section": section_parts(url, info, True),
        "items": items,
        "groups": groups,
    }


def inspect_video_formats(url: str) -> tuple[dict[str, Any], list[int]]:
    options = common_options()
    options.update({
        "noplaylist": True,
        "extract_flat": False,
    })
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("yt-dlp returned no video information.")

    heights = sorted({
        int(fmt["height"])
        for fmt in info.get("formats", [])
        if fmt.get("height")
        and fmt.get("vcodec") not in (None, "none")
    })
    return info, heights


def list_subtitles(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Subtitle tracks a video offers: every uploaded one, plus the auto-generated
    captions in the video's own language (the full auto-translate list is huge)."""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    result = [
        {"lang": lang, "name": str((tracks[0].get("name") if tracks else "") or lang), "auto": False}
        for lang, tracks in manual.items()
    ]
    automatic = info.get("automatic_captions") or {}
    original = info.get("language")
    picks = [k for k in automatic if k.endswith("-orig")] or (
        [original] if original in automatic else [])
    for lang in picks:
        if lang in manual:
            continue
        tracks = automatic[lang]
        name = str((tracks[0].get("name") if tracks else "") or lang)
        result.append({"lang": lang, "name": f"{name} (auto-generated)", "auto": True})
    return result


def choose_heights(heights: list[int]) -> list[int]:
    """Pick the resolutions to offer in the UI."""
    available = [height for height in PREFERRED_HEIGHTS if height in heights]
    if not available:
        available = sorted(set(heights))[-6:] if heights else [720]
    return available


def lookup_region() -> dict[str, Any]:
    """Return the public IP and country that websites (including YouTube) see.

    Goes through the configured proxy, so it reports the proxy's exit
    location when PROXY is set, and your own connection otherwise.
    """
    options = {"quiet": True, "no_warnings": True, "socket_timeout": 10}
    if settings.proxy:
        options["proxy"] = settings.proxy

    errors = []
    with yt_dlp.YoutubeDL(options) as ydl:
        for url in ("https://ipwho.is/", "https://ipinfo.io/json"):
            try:
                with ydl.urlopen(Request(url, headers={"Accept": "application/json"})) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}: {exc}")
                continue
            if data.get("success") is False:
                errors.append(f"{url}: {data.get('message', 'lookup failed')}")
                continue
            code = data.get("country_code") or data.get("country") or ""
            name = data.get("country") if data.get("country_code") else ""
            return {
                "ip": data.get("ip", ""),
                "country": name or code,
                "country_code": code,
                "region": data.get("region", ""),
                "city": data.get("city", ""),
                "via_proxy": bool(settings.proxy),
            }
    raise RuntimeError("Could not determine region. " + "; ".join(errors)[:300])


def explain_error(message: str) -> str:
    """Append a plain-language hint for common YouTube failures."""
    message = re.sub(r"\x1b\[[0-9;]*m", "", message)
    text = message.lower()
    hint = ""
    if "sign in" in text or "confirm you" in text or "cookies" in text:
        if settings.cookie_file or settings.cookies_from_browser:
            hint = (
                "Cookies are configured but YouTube still rejected them. Make sure you are "
                "logged in to YouTube in that browser (open youtube.com and check), that it is "
                "the right Firefox profile (Settings: firefox:PROFILE_NAME), and that the "
                "browser is closed or not locking its cookie file. If the cookies were "
                "exported earlier, they may have been rotated: log in again or re-export."
            )
        else:
            hint = "YouTube wants a signed-in session: open Settings and set \"Browser cookies\" to firefox (or chrome, edge…), or give a cookies file."
    elif (
        "requested format is not available" in text
        or "javascript runtime" in text
        or "js runtime" in text
        or "n challenge" in text
        or "signature" in text
    ):
        hint = (
            "YouTube gave yt-dlp no downloadable formats. This video is usually fine; the "
            "usual cause is a missing JavaScript runtime or solver. Install Deno "
            "(or Node.js 20+), run: pip install -U \"yt-dlp[default]\", then restart this "
            "program. Check the Region line at the top of the page too: some videos are "
            "blocked in the proxy's country."
        )
    elif "ffmpeg" in text or "ffprobe" in text:
        hint = "FFmpeg was not found or failed: check that `ffmpeg -version` works in the same terminal."
    elif "http error 403" in text or "unable to extract" in text or "nsig" in text:
        hint = "yt-dlp is probably out of date: run pip install -U yt-dlp and restart."
    elif "video unavailable" in text or "private video" in text or "not available in your country" in text or "uploader has not made this video available" in text:
        hint = "This video is unavailable, private or region-locked."
    return f"{message}\n{hint}" if hint else message


def subtitle_options(subtitle: dict[str, Any]) -> dict[str, Any]:
    """yt-dlp options that save one subtitle language as an .srt file."""
    return {
        "writesubtitles": not subtitle.get("auto"),
        "writeautomaticsub": bool(subtitle.get("auto")),
        "subtitleslangs": [str(subtitle["lang"])],
        "postprocessors": [
            {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"},
        ],
    }


def download_one(
    url: str,
    output_dir: str,
    mode: str,
    quality: str,
    on_progress: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    subtitle: dict[str, Any] | None = None,
) -> Path:
    """Download exactly one item into a dedicated directory and return its media file.

    on_progress(stage, percent, **stats) is called with stage "downloading" or
    "processing". While downloading, stats holds downloaded (bytes so far),
    total (bytes, or None if unknown), speed (bytes/second) and eta (seconds).
    subtitle, for mp4 only, is {"lang": "en", "auto": False}: the subtitle is saved
    next to the video as .srt. If cancel_event is set, the download aborts and
    Cancelled is raised.
    """
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")

    meter = ProgressMeter()

    def progress_hook(data: dict[str, Any]) -> None:
        check_cancel()
        if on_progress is None:
            return
        if data.get("status") in ("downloading", "finished"):
            percent, stats = meter.update(data)
            on_progress("downloading", percent, **stats)

    def postprocessor_hook(data: dict[str, Any]) -> None:
        check_cancel()
        if on_progress is not None and data.get("status") == "started":
            on_progress("processing", 100.0, speed=None, eta=None)

    options = common_options()
    options.update({
        "noplaylist": True,
        "ignoreerrors": False,
        "outtmpl": str(folder / "%(title).160B [%(id)s].%(ext)s"),
        "overwrites": True,
        "continuedl": True,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    })

    if mode == "mp3":
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(quality),
            }],
        })
        allowed = {".mp3"}
    elif mode == "mp4":
        height = int(quality)
        options.update({
            "format": (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
            ),
            "merge_output_format": "mp4",
        })
        if subtitle and subtitle.get("lang"):
            options.update(subtitle_options(subtitle))
        allowed = {".mp4", ".mkv", ".webm"}
    else:
        raise ValueError(f"Unsupported format: {mode}")

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(url, download=True)
    except DownloadCancelled as exc:
        raise Cancelled() from exc
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(explain_error(str(exc).removeprefix("ERROR: "))) from exc

    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()
    if not result:
        raise RuntimeError("yt-dlp could not download this item.")

    candidates = [
        path for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in allowed
    ]
    if not candidates:
        raise RuntimeError("Download completed but no output media file was found.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def download_subtitles(
    url: str,
    output_dir: str,
    subtitle: dict[str, Any],
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    """Download only the subtitle of one video, as .srt files in output_dir.

    Raises RuntimeError when the video has no subtitle in that language."""
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled()
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)

    options = common_options()
    options.update({
        "noplaylist": True,
        "ignoreerrors": False,
        "skip_download": True,
        "overwrites": True,
        "windowsfilenames": True,
        "outtmpl": str(folder / "%(title).160B [%(id)s].%(ext)s"),
    })
    options.update(subtitle_options(subtitle))
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)
    except DownloadCancelled as exc:
        raise Cancelled() from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(explain_error(str(exc))) from exc

    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".srt")
    if not files:
        raise RuntimeError(
            f"This video has no subtitles in the language '{subtitle['lang']}'.")
    return files
