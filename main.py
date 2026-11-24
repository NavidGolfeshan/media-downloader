"""Local web UI for the media downloader (Flask)."""

import logging
import os
import re
import socket
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.serving import make_server

import config
import downloader
import errorlog
import failures
import library
import paths
from config import settings
from jobs import JobManager
from selection import parse_track_selection

paths.log_to_file_if_no_console()
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

STATIC_DIR = paths.RESOURCE_DIR / "static"
URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)
MP3_QUALITIES = {"128", "192", "320"}
MAX_ITEMS_PER_JOB = 5000

app = Flask(__name__, static_folder=None)
jobs: JobManager | None = None


def get_jobs() -> JobManager:
    global jobs
    if jobs is None:
        jobs = JobManager(
            settings.download_dir,
            settings.job_ttl_minutes,
            settings.max_concurrent_jobs,
        )
    return jobs


def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/static/<path:name>")
def static_files(name: str):
    return send_from_directory(STATIC_DIR, name)


def settings_payload() -> dict:
    return {
        "download_dir": str(Path(settings.download_dir).expanduser().resolve()),
        "proxy": settings.proxy,
        "cookies_from_browser": config.cookies_text(settings.cookies_from_browser),
        "cookie_file": settings.cookie_file,
        "max_concurrent_jobs": settings.max_concurrent_jobs,
        "job_ttl_minutes": settings.job_ttl_minutes,
        "max_playlist_items": settings.max_playlist_items,
        "error_log": str(errorlog.log_path(settings.download_dir).expanduser().resolve()),
        "failed_file": str(failures.file_path(settings.download_dir).expanduser().resolve()),
    }


@app.get("/api/config")
def api_config():
    return jsonify(settings_payload())


@app.post("/api/settings")
def api_settings():
    data = request.get_json(silent=True) or {}
    try:
        config.apply({k: v for k, v in data.items() if k in config.EDITABLE})
    except (ValueError, RuntimeError) as exc:
        return error(str(exc))
    get_jobs().configure(
        settings.download_dir, settings.job_ttl_minutes, settings.max_concurrent_jobs)
    return jsonify(settings_payload())


@app.post("/api/check")
def api_check():
    """Which of these items (by id) are already in the download folder, and which failed before?"""
    ids = (request.get_json(silent=True) or {}).get("ids")
    if not isinstance(ids, list):
        return error("ids must be a list.")
    ids = [str(i) for i in ids[:MAX_ITEMS_PER_JOB] if i]
    return jsonify({
        "found": library.lookup(settings.download_dir, ids),
        "failed": failures.lookup(settings.download_dir, ids),
    })


@app.get("/api/library")
def api_library():
    return jsonify(library.listing(settings.download_dir))


@app.get("/api/region")
def api_region():
    try:
        return jsonify(downloader.lookup_region())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Region lookup failed: %s", exc)
        return error(str(exc)[:400], 502)


@app.post("/api/inspect")
def api_inspect():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    if not URL_PATTERN.match(url):
        return error("Please enter a valid URL beginning with http:// or https://.")
    hint = str(data.get("channel") or "").strip() or None
    try:
        result = downloader.inspect_media(url, hint)
        result["source_url"] = url
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to inspect URL")
        errorlog.write(settings.download_dir, "Look-up failed", exc, {"URL": url}, exc)
        return error(
            f"Couldn't inspect that URL: {str(exc)[:700]}\n"
            "Check the URL, update yt-dlp, and verify that you have permission "
            "to access/download the content.",
            502,
        )


@app.post("/api/parse-selection")
def api_parse_selection():
    data = request.get_json(silent=True) or {}
    try:
        total = int(data.get("total", 0))
        indices = parse_track_selection(str(data.get("text", "")), total)
    except ValueError as exc:
        return error(str(exc))
    return jsonify({"indices": indices})


@app.get("/api/formats")
def api_formats():
    url = request.args.get("url", "").strip()
    if not URL_PATTERN.match(url):
        return error("A valid http(s) URL is required.")
    try:
        info, heights = downloader.inspect_video_formats(url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not inspect video formats")
        errorlog.write(settings.download_dir, "Format check failed", exc, {"URL": url}, exc)
        return error(f"Couldn't inspect video formats: {str(exc)[:700]}", 502)
    return jsonify({
        "heights": downloader.choose_heights(heights),
        "subtitles": downloader.list_subtitles(info),
    })


@app.post("/api/jobs")
def api_create_job():
    data = request.get_json(silent=True) or {}

    mode = data.get("mode")
    if mode not in ("mp3", "mp4"):
        return error("Format must be mp3 or mp4.")

    quality = str(data.get("quality", ""))
    if mode == "mp3" and quality not in MP3_QUALITIES:
        return error("MP3 quality must be 128, 192 or 320.")
    if mode == "mp4" and not (quality.isdigit() and 100 <= int(quality) <= 8640):
        return error("MP4 quality must be a resolution such as 720.")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return error("Select at least one item to download.")
    if len(raw_items) > MAX_ITEMS_PER_JOB:
        return error(f"Too many items (max {MAX_ITEMS_PER_JOB}).")

    items = []
    for raw in raw_items:
        url = str(raw.get("url", "")).strip() if isinstance(raw, dict) else ""
        if not URL_PATTERN.match(url):
            return error("Every item needs a valid http(s) URL.")
        items.append({
            "url": url,
            "title": str(raw.get("title") or "Untitled")[:300],
            "id": str(raw.get("id") or "")[:200] or None,
        })

    section = data.get("section")
    if not isinstance(section, list) or not all(isinstance(p, str) for p in section):
        section = ["videos"]

    subtitle = None
    raw_sub = data.get("subtitle")
    if mode == "mp4" and isinstance(raw_sub, dict) and str(raw_sub.get("lang") or "").strip():
        subtitle = {"lang": str(raw_sub["lang"]).strip()[:40], "auto": bool(raw_sub.get("auto"))}

    job = get_jobs().create(
        source_url=str(data.get("source_url") or "")[:2000] or None,
        playlist_items=clean_playlist(data.get("playlist")),
        title=str(data.get("title") or "download")[:300],
        is_playlist=bool(data.get("is_playlist")),
        items=items,
        mode=mode,
        quality=quality,
        channel=str(data.get("channel") or "downloads")[:200],
        section=section[:4],
        subtitle=subtitle,
    )
    return jsonify(job.snapshot()), 201


def clean_playlist(raw: Any) -> list[dict[str, Any]] | None:
    """The playlist the page was showing (every item, selected or not), trimmed to what the
    playlist file needs. Bad entries are skipped: the file is optional."""
    if not isinstance(raw, list):
        return None
    entries = []
    for entry in raw[:MAX_ITEMS_PER_JOB]:
        if not isinstance(entry, dict):
            continue
        duration = entry.get("duration")
        entries.append({
            "id": str(entry.get("id") or "")[:200],
            "title": str(entry.get("title") or "Untitled")[:300],
            "url": str(entry.get("url") or "")[:2000],
            "uploader": str(entry.get("uploader") or "")[:200],
            "duration": duration if isinstance(duration, int) and duration > 0 else None,
        })
    return entries or None


@app.post("/api/subtitles")
def api_create_subtitle_job():
    """Download only the subtitles of videos that are already in the library."""
    data = request.get_json(silent=True) or {}

    raw_sub = data.get("subtitle")
    lang = str(raw_sub.get("lang") or "").strip()[:40] if isinstance(raw_sub, dict) else ""
    if not lang:
        return error("Choose a subtitle language.")
    subtitle = {"lang": lang, "auto": bool(raw_sub.get("auto"))}

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return error("Select at least one video.")
    if len(raw_items) > MAX_ITEMS_PER_JOB:
        return error(f"Too many items (max {MAX_ITEMS_PER_JOB}).")
    items = []
    for raw in raw_items:
        url = str(raw.get("url", "")).strip() if isinstance(raw, dict) else ""
        item_id = str(raw.get("id") or "")[:200] if isinstance(raw, dict) else ""
        if not URL_PATTERN.match(url) or not item_id:
            return error("Every item needs a valid http(s) URL and an id.")
        items.append({"url": url, "title": str(raw.get("title") or "Untitled")[:300], "id": item_id})

    job = get_jobs().create_subtitles(
        title=str(data.get("title") or "subtitles")[:300],
        items=items,
        subtitle=subtitle,
        channel=str(data.get("channel") or "")[:200] or None,
        source_url=str(data.get("source_url") or "")[:2000] or None,
    )
    return jsonify(job.snapshot()), 201


@app.get("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = get_jobs().get(job_id)
    if not job:
        return error("This job no longer exists (it may have expired).", 404)
    return jsonify(job.snapshot())


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel(job_id: str):
    if not get_jobs().cancel(job_id):
        return error("This job no longer exists.", 404)
    return jsonify({"ok": True})


@app.delete("/api/jobs/<job_id>")
def api_delete(job_id: str):
    if not get_jobs().delete(job_id):
        return error("Job not found or still running.", 404)
    return jsonify({"ok": True})


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _make_server():
    """Bind the configured port, or any free port if it is taken, so the app always starts."""
    port = settings.port
    if not _port_is_free(settings.host, port):
        logger.warning("Port %s is busy, using a free one instead.", port)
        port = 0
    return make_server(settings.host, port, app, threaded=True)


def _wait_until_listening(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)


def run_in_window(server, url: str) -> bool:
    """Show the UI in its own window. Closing the window stops the app.

    Returns False (nothing started) if the window library is unavailable.
    """
    try:
        import webview
    except ImportError:
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _wait_until_listening(server.server_port)
    webview.create_window("Media Downloader", url, width=1150, height=850, min_size=(700, 500))
    webview.start()
    # The window was closed: stop everything, including any download still running.
    logger.info("Window closed, quitting.")
    logging.shutdown()
    os._exit(0)


def main() -> None:
    settings.validate()
    get_jobs()
    logger.info("Downloads are saved to %s", Path(settings.download_dir).resolve())
    server = _make_server()
    url = f"http://127.0.0.1:{server.server_port}"

    if paths.FROZEN and not paths.CONSOLE_BUILD and run_in_window(server, url):
        return

    logger.info("Open %s in your browser.", url)
    if paths.FROZEN:
        logger.info("Close this window to quit.")
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
