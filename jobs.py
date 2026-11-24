"""Background download jobs: state, progress, and saving into the library.

Finished files are never deleted by this program. Jobs only live in memory (the
list on the page); `job_ttl_minutes` is how long a finished job stays listed.
"""

import hashlib
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import downloader
import errorlog
import failures
import library
import playlist
from formatting import format_size
from library import safe_name as safe_filename  # noqa: F401  (kept for older imports)

logger = logging.getLogger(__name__)

FINISHED_STATES = {"done", "failed", "cancelled"}


def staging_dir(root: Path, url: str, mode: str, quality: str, subtitle: dict | None) -> Path:
    """Where one item downloads before it is moved into the library.

    The name is fixed by the item and options, so an interrupted download resumes."""
    key = f"{url}|{mode}|{quality}|{(subtitle or {}).get('lang', '')}"
    return root / library.PARTIAL_DIR / hashlib.sha1(key.encode()).hexdigest()[:16]


class Job:
    def __init__(
        self,
        job_id: str,
        title: str,
        is_playlist: bool,
        items: list[dict[str, str]],
        mode: str,
        quality: str,
        root: Path,
        destination: Path,
        subtitle: dict[str, Any] | None,
        source_url: str | None = None,
        channel: str | None = None,
        section: list[str] | None = None,
        playlist_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self.id = job_id
        self.title = title
        self.is_playlist = is_playlist
        self.mode = mode
        self.quality = quality
        self.root = root
        self.destination = destination
        self.subtitle = subtitle
        self.source_url = source_url  # the page that was looked up, so the person can go back to it
        self.channel = channel
        self.section = section or []
        self.playlist_items = playlist_items  # the whole playlist (not just what was selected)
        self.playlist_file: str | None = None
        self.had_failures = False     # something was written to the error log
        self.created = time.time()
        self.finished_at: float | None = None

        self.state = "queued"  # queued | running | done | failed | cancelled
        self.message = "Waiting to start…"
        self.error: str | None = None
        self.percent = 0.0
        self.speed: float | None = None  # bytes/second of the item being downloaded
        self.eta: float | None = None    # seconds left for that item
        self.tracks: list[dict[str, Any]] = [
            {"title": item["title"], "url": item["url"], "id": item.get("id"),
             "status": "pending", "percent": 0.0, "error": None,
             "downloaded": 0,     # bytes fetched over the network so far
             "total": None,       # expected bytes, when the site says
             "speed": None, "eta": None,
             "size": None,        # final file size once the item is saved
             "subtitles": 0}      # subtitle files saved next to it
            for item in items
        ]
        self.files: list[dict[str, Any]] = []  # {"name", "size"} for saved media files

        self.cancel_event = threading.Event()
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "id": self.id,
                "title": self.title,
                "is_playlist": self.is_playlist,
                "mode": self.mode,
                "quality": self.quality,
                "folder": str(self.destination),
                "subtitle": (self.subtitle or {}).get("lang"),
                "source_url": self.source_url,
                "channel": self.channel,
                "playlist_file": self.playlist_file,
                "error_log": str(errorlog.log_path(self.root)) if self.had_failures else None,
                "failed_file": (str(failures.file_path(self.root))
                                if self.had_failures and self.mode != "subs" else None),
                "state": self.state,
                "message": self.message,
                "error": self.error,
                "percent": round(self.percent, 1),
                "speed": self.speed,
                "eta": self.eta,
                "downloaded_bytes": sum(t["downloaded"] for t in self.tracks),
                "tracks": [
                    {key: track[key] for key in (
                        "title", "status", "percent", "error",
                        "downloaded", "total", "speed", "eta", "size", "subtitles",
                    )}
                    for track in self.tracks
                ],
                "files": [dict(f) for f in self.files],
            }


class JobManager:
    """In-memory job registry. The list is lost on restart; downloaded files are not."""

    def __init__(self, root: str, ttl_minutes: int, max_concurrent: int) -> None:
        self.root = Path(root)
        self.ttl_seconds = ttl_minutes * 60
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent
        self._executor = self._new_executor(max_concurrent)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _new_executor(workers: int) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=workers, thread_name_prefix="download")

    # ---- settings changed from the page --------------------------------

    def configure(self, root: str, ttl_minutes: int, max_concurrent: int) -> None:
        """Apply new settings. Jobs already created keep the folder they started with."""
        self.root = Path(root)
        self.ttl_seconds = ttl_minutes * 60
        if max_concurrent != self._max_concurrent:
            old = self._executor
            self._max_concurrent = max_concurrent
            self._executor = self._new_executor(max_concurrent)
            old.shutdown(wait=False)  # queued and running jobs finish on the old pool

    # ---- registry -------------------------------------------------------

    def create(
        self,
        title: str,
        is_playlist: bool,
        items: list[dict[str, str]],
        mode: str,
        quality: str,
        channel: str = "downloads",
        section: list[str] | None = None,
        subtitle: dict[str, Any] | None = None,
        source_url: str | None = None,
        playlist_items: list[dict[str, Any]] | None = None,
    ) -> Job:
        self.cleanup_expired()
        root = self.root
        section = section or ["videos"]
        destination = library.destination(root, channel, section, library.kind_for(mode))
        job = Job(uuid.uuid4().hex[:12], title, is_playlist, items, mode, quality,
                  root, destination, subtitle, source_url, channel, section, playlist_items)
        return self._submit(job)

    def create_subtitles(
        self,
        title: str,
        items: list[dict[str, str]],
        subtitle: dict[str, Any],
        channel: str | None = None,
        source_url: str | None = None,
    ) -> Job:
        """A job that downloads only subtitles, for videos already in the library."""
        self.cleanup_expired()
        job = Job(uuid.uuid4().hex[:12], title, len(items) > 1, items, "subs",
                  str(subtitle["lang"]), self.root, self.root, subtitle, source_url, channel)
        return self._submit(job)

    def _submit(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        job.cancel_event.set()
        with job.lock:
            if job.state == "queued":
                job.state = "cancelled"
                job.message = "Cancelled."
                job.finished_at = time.time()
        return True

    def delete(self, job_id: str) -> bool:
        """Remove a finished job from the list. Its files stay where they were saved."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.state not in FINISHED_STATES:
                job.cancel_event.set()
                return False
            del self._jobs[job_id]
        return True

    def cleanup_expired(self) -> None:
        now = time.time()
        with self._lock:
            for job in [j for j in self._jobs.values()
                        if j.finished_at and now - j.finished_at > self.ttl_seconds]:
                del self._jobs[job.id]

    # ---- worker ---------------------------------------------------------

    def _run(self, job: Job) -> None:
        with job.lock:
            if job.state == "cancelled":  # cancelled while still queued
                return
            job.state = "running"
            job.message = "Starting…"

        try:
            self._download_all(job)
        except downloader.Cancelled:
            self._finish(job, "cancelled", "Cancelled.")
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            logger.exception("Job %s failed", job.id)
            job.error = str(exc)[:900]
            job.had_failures = True
            errorlog.write(job.root, "Job failed", exc,
                           {"Job": job.title, "Format": library.describe(job.mode, job.quality)}, exc)
            self._finish(job, "failed", "The job failed.")

    def _finish(self, job: Job, state: str, message: str) -> None:
        with job.lock:
            job.state = state
            job.message = message
            job.finished_at = time.time()
            job.speed = job.eta = None
            if state == "done":
                job.percent = 100.0
            if state == "cancelled":
                for track in job.tracks:
                    if track["status"] in {"pending", "downloading", "processing"}:
                        track["status"] = "cancelled"

    def _download_all(self, job: Job) -> None:
        if job.mode == "subs":
            self._subtitles_all(job)
            return
        total = len(job.tracks)
        saved = 0
        fmt = library.describe(library.kind_for(job.mode), library.quality_label(job.mode, job.quality))

        for index, track in enumerate(job.tracks):
            if job.cancel_event.is_set():
                raise downloader.Cancelled()

            with job.lock:
                track["status"] = "downloading"
                job.message = f"Downloading {index + 1}/{total}: {track['title'][:120]}"

            def on_progress(stage: str, percent: float, i=index, t=track, **stats) -> None:
                with job.lock:
                    t["status"] = "processing" if stage == "processing" else "downloading"
                    t["percent"] = percent
                    for key in ("downloaded", "total", "speed", "eta"):
                        if key in stats:
                            t[key] = stats[key]
                    job.speed = t["speed"]
                    job.eta = t["eta"]
                    job.percent = (i + percent / 100.0) / total * 100.0

            staging = staging_dir(job.root, track["url"], job.mode, job.quality, job.subtitle)
            try:
                media_path = downloader.download_one(
                    track["url"],
                    str(staging),
                    job.mode,
                    job.quality,
                    on_progress=on_progress,
                    cancel_event=job.cancel_event,
                    subtitle=job.subtitle,
                )
                placed = library.place(staging, job.destination, library.quality_label(job.mode, job.quality))
            except downloader.Cancelled:
                raise  # the partial download stays in staging so it can resume
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to download %s: %s", track["title"], exc)
                shutil.rmtree(staging, ignore_errors=True)
                # The whole error goes to the log; the page gets a shortened copy.
                failures.note_failure(
                    job.root, job.destination, id=track.get("id"), title=track["title"],
                    url=track["url"], error=exc, fmt=fmt, exc=exc)
                with job.lock:
                    job.had_failures = True
                    track["status"] = "failed"
                    track["error"] = str(exc)[:600]
                    track["speed"] = track["eta"] = None
                    job.percent = (index + 1) / total * 100.0
                continue

            final = next(p for p in placed if p.suffix.lower() in library.MEDIA_EXTS)
            failures.note_success(job.root, id=track.get("id"), url=track["url"])
            with job.lock:
                track["status"] = "done"
                track["percent"] = 100.0
                track["speed"] = track["eta"] = None
                track["size"] = final.stat().st_size
                track["subtitles"] = sum(1 for p in placed if p.suffix.lower() == ".srt")
                job.files.append({"name": final.name, "size": track["size"]})
                job.speed = job.eta = None
                job.percent = (index + 1) / total * 100.0
            saved += 1

        if not saved:
            first_errors = [t["error"] for t in job.tracks if t.get("error")][:3]
            job.error = "No files were downloaded successfully."
            if first_errors:
                job.error += "\n\n" + "\n".join(first_errors)
            self._finish(job, "failed", "No files downloaded successfully.")
            return

        self._write_playlist(job)
        failed = sum(1 for t in job.tracks if t["status"] == "failed")
        size = sum(f["size"] for f in job.files)
        summary = f"Saved {saved} file{'s' if saved != 1 else ''} ({format_size(size)})"
        summary += f", {failed} failed." if failed else "."
        self._finish(job, "done", summary)

    def _write_playlist(self, job: Job) -> None:
        """Save 'Playlist name.m3u8' next to the tracks of a playlist (see playlist.py)."""
        if not job.playlist_items or job.section[:1] != ["playlists"] or not job.destination.is_dir():
            return
        try:
            path = playlist.write(job.destination, job.title, job.source_url, job.playlist_items)
        except Exception as exc:  # noqa: BLE001 - the tracks are saved; this file is a bonus
            logger.warning("Could not write the playlist file: %s", exc)
            errorlog.write(job.root, "Could not write the playlist file", exc,
                           {"Playlist": job.title, "Folder": str(job.destination)}, exc)
            return
        if path is not None:
            with job.lock:
                job.playlist_file = str(path)

    def _subtitles_all(self, job: Job) -> None:
        """Download just the subtitles and save them next to the videos already in the library."""
        total = len(job.tracks)
        saved = 0
        lang = str(job.subtitle["lang"])
        index = library.scan(job.root)

        for position, track in enumerate(job.tracks):
            if job.cancel_event.is_set():
                raise downloader.Cancelled()
            with job.lock:
                track["status"] = "downloading"
                job.message = f"Subtitles {position + 1}/{total}: {track['title'][:120]}"

            videos = library.video_files(index, track.get("id") or "", job.root)
            staging = staging_dir(job.root, track["url"], "subs", lang, job.subtitle)
            try:
                if not videos:
                    raise RuntimeError(
                        "This video isn't in the library, so there is no video file to put the subtitles next to.")
                downloader.download_subtitles(
                    track["url"], str(staging), job.subtitle, cancel_event=job.cancel_event)
                placed = library.place_subtitles(staging, videos, track.get("id"))
            except downloader.Cancelled:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to get subtitles for %s: %s", track["title"], exc)
                shutil.rmtree(staging, ignore_errors=True)
                # The video itself is fine, so this is logged but not added to failed.json.
                failures.note_failure(
                    job.root, job.root, id=track.get("id"), title=track["title"], url=track["url"],
                    error=exc, fmt=f"subtitles [{lang}]", exc=exc,
                    what="Subtitle download failed", remember=False)
                with job.lock:
                    job.had_failures = True
                    track["status"] = "failed"
                    track["error"] = str(exc)[:600]
                    job.percent = (position + 1) / total * 100.0
                continue

            with job.lock:
                track["status"] = "done"
                track["percent"] = 100.0
                track["subtitles"] = len(placed)
                for path in placed:
                    job.files.append({"name": path.name, "size": path.stat().st_size})
                job.percent = (position + 1) / total * 100.0
            saved += 1

        if not saved:
            first_errors = [t["error"] for t in job.tracks if t.get("error")][:3]
            job.error = "No subtitles were downloaded."
            if first_errors:
                job.error += "\n\n" + "\n".join(first_errors)
            self._finish(job, "failed", "No subtitles downloaded.")
            return

        failed = sum(1 for t in job.tracks if t["status"] == "failed")
        summary = f"Saved subtitles for {saved} video{'s' if saved != 1 else ''}"
        summary += f", {failed} failed." if failed else "."
        self._finish(job, "done", summary)
