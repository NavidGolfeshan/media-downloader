"""Terminal interface for the media downloader (no browser needed).

Examples:
    python cli.py "https://youtu.be/VIDEO_ID"
    python cli.py URL -f mp4 -q 1080 --subs en        # with English subtitles (.srt)
    python cli.py PLAYLIST_URL --list                 # just show the videos
    python cli.py PLAYLIST_URL -s 1,3,5-10 --zip      # pick some, bundle as ZIP
    python cli.py URL --proxy none --cookies-file cookies.txt -o /content/out
"""

import argparse
import dataclasses
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import downloader
import library
from config import _browser_cookies, _proxy, settings
from formatting import format_duration, format_eta, format_size, format_speed
from jobs import safe_filename, staging_dir
from selection import parse_track_selection

MP3_QUALITIES = {"128", "192", "320"}
DEFAULT_QUALITY = {"mp3": "192", "mp4": "720"}


# ---------------------------------------------------------------- output ----

def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def in_colab() -> bool:
    return any(key.startswith("COLAB_") for key in os.environ)


def truncate(text: str, width: int) -> str:
    text = " ".join(text.split())
    if width < 4:
        return ""
    return text if len(text) <= width else text[: width - 3] + "..."


class Progress:
    """One self-updating status line (or, with --plain, a line every few seconds)."""

    def __init__(self, plain: bool) -> None:
        self.plain = plain
        self.interval = 5.0 if plain else 0.1
        self.last_draw = 0.0
        self.dirty = False  # a \r line is on screen and needs clearing

    def _width(self) -> int:
        return max(40, shutil.get_terminal_size((100, 24)).columns - 1)

    def update(self, prefix: str, title: str, stage: str, percent: float,
               stats: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self.last_draw < self.interval:
            return
        self.last_draw = now

        if stage == "processing":
            info = "converting / merging..."
        else:
            done, total = stats.get("downloaded") or 0, stats.get("total")
            size = f"{format_size(done)} / {format_size(total)}" if total else format_size(done)
            info = (f"{percent:5.1f}%  {size}  {format_speed(stats.get('speed'))}"
                    f"  ETA {format_eta(stats.get('eta'))}")
        head = f"{prefix} {info}  "
        line = head + truncate(title, self._width() - len(head))

        if self.plain:
            print(line, flush=True)
        else:
            sys.stdout.write("\r" + line.ljust(self._width()))
            sys.stdout.flush()
            self.dirty = True

    def clear(self) -> None:
        if self.dirty:
            sys.stdout.write("\r" + " " * self._width() + "\r")
            sys.stdout.flush()
            self.dirty = False
        self.last_draw = 0.0


def ask(prompt: str) -> str | None:
    try:
        return input(prompt).strip()
    except EOFError:
        return None


# ----------------------------------------------------------------- steps ----

def choose_media(url: str) -> dict[str, Any] | None:
    """Look the URL up; if it is a channel, let the person open one section."""
    for _ in range(4):  # channel -> section -> playlist; never loop forever
        print(f"Looking up {url} ...", flush=True)
        try:
            media = downloader.inspect_media(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not look that up: {str(exc)[:600]}", file=sys.stderr)
            return None
        if media["items"] or not media.get("groups"):
            return media

        groups = media["groups"]
        print(f"\n{media['title']} contains {len(groups)} playlist(s)/section(s):")
        for number, group in enumerate(groups, 1):
            print(f"  {number:>3}. {group['title']}\n       {group['url']}")
        if not interactive():
            print("\nRun again with one of those URLs.", file=sys.stderr)
            return None
        answer = ask("\nOpen which one? (number, empty to skip): ")
        if not answer or not answer.isdigit() or not 1 <= int(answer) <= len(groups):
            return None
        url = groups[int(answer) - 1]["url"]
    return None


def choose_items(media: dict[str, Any], select: str | None,
                 list_only: bool = False) -> list[dict[str, str]] | None:
    items = media["items"]
    if not media["is_playlist"]:
        return items

    known = [i["duration"] for i in items if i.get("duration")]
    total_length = format_duration(sum(known))
    print(f"\n{media['title']}  ({len(items)} videos"
          + (f", {total_length} total" if known and len(known) == len(items) else "") + ")")
    for number, item in enumerate(items, 1):
        length = format_duration(item.get("duration"))
        print(f"  {number:>4}. {truncate(item['title'], 80)}" + (f"  [{length}]" if length else ""))

    if list_only:
        return []
    text = select
    if text is None and interactive():
        text = ask("\nWhich ones? (all, or e.g. 1,3,5-10): ") or "all"
    text = text or "all"
    try:
        indices = parse_track_selection(text, len(items))
    except ValueError as exc:
        print(f"  Selection problem: {exc}", file=sys.stderr)
        return None
    return [items[i] for i in indices]


def download_items(items: list[dict[str, str]], mode: str, quality: str,
                   root: Path, dest: Path, subtitle: dict[str, Any] | None,
                   zip_it: bool, again: bool, plain: bool):
    """Download each item into its library folder.

    Returns (finished_files, failures, network_bytes). Items already in the library
    with the same format and quality are skipped unless `again` is set.
    """
    progress = Progress(plain)
    finished: list[Path] = []
    failures: list[tuple[str, str]] = []
    network_bytes = 0
    total = len(items)
    have = library.lookup(root, [i["id"] for i in items if i.get("id")])
    kind = library.kind_for(mode)

    for index, item in enumerate(items, 1):
        prefix = f"[{index}/{total}]"
        title = item["title"]
        records = have.get(item.get("id") or "", [])
        same = [r for r in records if r["kind"] == kind and r["quality"] == library.quality_label(mode, quality)]
        if same and not again:
            print(f"{prefix} SKIP    {truncate(title, 70)}  (already have {same[0]['label']}: {same[0]['rel']})")
            continue
        if records and not same:
            labels = ", ".join(sorted({r["label"] for r in records}))
            print(f"{prefix} NOTE    {truncate(title, 70)}  (you also have: {labels})")

        last: dict[str, Any] = {}
        started = time.monotonic()

        def on_progress(stage: str, percent: float, **stats: Any) -> None:
            if stage == "downloading":
                last.update(stats)
            progress.update(prefix, title, stage, percent, last)

        staging = staging_dir(root, item["url"], mode, quality, subtitle)
        try:
            path = downloader.download_one(
                item["url"], str(staging), mode, quality,
                on_progress=on_progress, subtitle=subtitle,
            )
            if not zip_it:
                moved = library.place(staging, dest, library.quality_label(mode, quality))
                path = next(p for p in moved if p.suffix.lower() in library.MEDIA_EXTS)
        except KeyboardInterrupt:
            progress.clear()
            raise
        except Exception as exc:  # noqa: BLE001
            progress.clear()
            shutil.rmtree(staging, ignore_errors=True)
            failures.append((title, str(exc)))
            print(f"{prefix} FAILED  {truncate(title, 70)}\n         {str(exc)[:500]}")
            continue

        progress.clear()
        elapsed = max(time.monotonic() - started, 0.001)
        got = int(last.get("downloaded") or 0)
        network_bytes += got
        finished.append(path)
        extra = f" +{len([m for m in moved if m.suffix == '.srt'])} subtitle(s)" if not zip_it and subtitle else ""
        print(f"{prefix} OK      {truncate(path.name, 70)}  "
              f"{format_size(path.stat().st_size)}{extra}"
              f"  (downloaded {format_size(got)} in {format_eta(elapsed)}, "
              f"avg {format_speed(got / elapsed)})")

    return finished, failures, network_bytes


def make_zip(files: list[Path], title: str, mode: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{safe_filename(title)}_{mode}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=3) as bundle:
        for path in files:
            bundle.write(path, arcname=path.name)
    return archive


# ------------------------------------------------------------------ main ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download audio/video with yt-dlp from the terminal.")
    parser.add_argument("urls", nargs="*", help="video, playlist or channel URL(s)")
    parser.add_argument("-f", "--format", choices=("mp3", "mp4"), default="mp3")
    parser.add_argument("-q", "--quality",
                        help="mp3: 128/192/320 kbps (default 192); mp4: max height, e.g. 720")
    parser.add_argument("-s", "--select", metavar="SEL",
                        help='playlist items: "all" or numbers/ranges like 1,3,5-10')
    parser.add_argument("-o", "--output", metavar="DIR",
                        help="download folder (default: the one set in the web UI / DOWNLOAD_DIR). "
                             "Files go in CHANNEL/SECTION/ as 'Title [id][480p].mp4'")
    parser.add_argument("--zip", action="store_true",
                        help="bundle a playlist into one ZIP file instead of keeping the files")
    parser.add_argument("--again", action="store_true",
                        help="download items even if the same format and quality are already in the library")
    parser.add_argument("--subs", metavar="LANG",
                        help="mp4 only: also save subtitles in this language as .srt (e.g. en)")
    parser.add_argument("--auto-subs", action="store_true",
                        help="with --subs: use the auto-generated captions")
    parser.add_argument("--list", action="store_true",
                        help="only list what is at the URL, download nothing")
    parser.add_argument("--plain", action="store_true",
                        help="print a progress line every few seconds instead of "
                             "redrawing one line (better for log files)")
    parser.add_argument("--proxy", metavar="VALUE",
                        help='override PROXY from .env ("none" turns it off)')
    parser.add_argument("--cookies-file", metavar="PATH",
                        help="Netscape-format cookies file (overrides .env)")
    parser.add_argument("--cookies-from-browser", metavar="BROWSER",
                        help='override COOKIES_FROM_BROWSER ("none" turns it off)')
    return parser


def apply_overrides(args: argparse.Namespace) -> None:
    changes: dict[str, Any] = {}
    if args.proxy is not None:
        changes["proxy"] = _proxy(args.proxy)
    if args.cookies_file:
        changes["cookie_file"] = args.cookies_file
        changes["cookies_from_browser"] = ()
    elif args.cookies_from_browser is not None:
        changes["cookies_from_browser"] = _browser_cookies(args.cookies_from_browser)
    downloader.settings = dataclasses.replace(settings, **changes)
    downloader.settings.validate()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # titles may hold characters the console can't show
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    quality = args.quality or DEFAULT_QUALITY[args.format]
    if args.format == "mp3" and quality not in MP3_QUALITIES:
        parser.error("mp3 quality must be 128, 192 or 320")
    if args.format == "mp4" and not (quality.isdigit() and 100 <= int(quality) <= 8640):
        parser.error("mp4 quality must be a height such as 720")

    try:
        apply_overrides(args)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    urls = list(args.urls)
    if not urls:
        if not interactive():
            parser.error("give at least one URL")
        url = ask("URL: ")
        if not url:
            return 1
        urls = [url]
    for url in urls:
        if not url.lower().startswith(("http://", "https://")):
            parser.error(f"not a valid http(s) URL: {url}")

    root = Path(args.output or settings.download_dir).expanduser().resolve()
    subtitle = {"lang": args.subs, "auto": args.auto_subs} if args.subs and args.format == "mp4" else None
    plain = args.plain or not (interactive() or in_colab())
    exit_code = 0

    try:
        for url in urls:
            media = choose_media(url)
            if media is None:
                exit_code = 1
                continue
            items = choose_items(media, args.select, list_only=args.list)
            if items is None:
                exit_code = 1
                continue
            if args.list:
                if not media["is_playlist"]:
                    print(media["title"])
                continue
            if not items:
                continue

            dest = library.destination(root, media["channel"], media["section"])
            dest.mkdir(parents=True, exist_ok=True)
            print(f"\nDownloading {len(items)} item(s) as {args.format.upper()} "
                  f"({quality}{' kbps' if args.format == 'mp3' else 'p max'}) "
                  f"-> {dest}\n")
            began = time.monotonic()
            zip_it = args.zip and media["is_playlist"]
            files, failures, network = download_items(
                items, args.format, quality, root, dest, subtitle, zip_it, args.again, plain)
            count = len(files)

            if zip_it and files:
                archive = make_zip(files, media["title"], args.format, dest)
                for item in items:
                    shutil.rmtree(staging_dir(root, item["url"], args.format, quality, subtitle),
                                  ignore_errors=True)
                files = [archive]

            elapsed = max(time.monotonic() - began, 0.001)
            saved = sum(path.stat().st_size for path in files)
            print(f"\nDone: {count} of {len(items)} downloaded, "
                  f"{format_size(saved)} saved, {format_size(network)} fetched in "
                  f"{format_eta(elapsed)} (avg {format_speed(network / elapsed)}).")
            for path in files:
                print(f"  {path}")
            if failures:
                exit_code = 1
    except KeyboardInterrupt:
        print("\nCancelled. Partial downloads stay in "
              f"{root / library.PARTIAL_DIR} so a re-run can resume them.", file=sys.stderr)
        return 130

    partial = root / library.PARTIAL_DIR
    if partial.is_dir() and not any(partial.iterdir()):
        partial.rmdir()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
