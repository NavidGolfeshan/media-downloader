"""Tests for the terminal interface and the speed/size reporting (yt-dlp mocked)."""

import io
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli  # noqa: E402
import downloader  # noqa: E402
import formatting  # noqa: E402


def fake_download_one(url, output_dir, mode, quality, on_progress=None, cancel_event=None, subtitle=None):
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if "bad" in url:
        raise RuntimeError("boom")
    if on_progress:
        on_progress("downloading", 50.0, downloaded=500, total=1000, speed=250.0, eta=2.0)
        on_progress("processing", 100.0, speed=None, eta=None)
    vid = url.rsplit("/", 1)[-1]
    path = folder / f"{vid} [{vid}].{mode}"
    path.write_bytes(b"x" * 1000)
    if subtitle:
        (folder / f"{vid} [{vid}].{subtitle['lang']}.srt").write_text("1\n")
    return path


def fake_download_one_ok(url, output_dir, mode, quality, on_progress=None, cancel_event=None, subtitle=None):
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"ok [{url.rsplit('/', 1)[-1]}].{mode}"
    path.write_bytes(b"x")
    return path


PLAYLIST = {"is_playlist": True, "title": "My List", "groups": [], "channel": "Chan",
            "section": ["videos"], "items": [
    {"url": "https://x/a", "title": "A", "id": "a", "duration": 65},
    {"url": "https://x/b", "title": "B", "id": "b", "duration": 3600},
    {"url": "https://x/c", "title": "C", "id": "c", "duration": 125}]}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"
        for patcher in (
            mock.patch.object(downloader, "download_one", fake_download_one),
            mock.patch.object(downloader, "inspect_media", return_value=PLAYLIST),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["--plain", "-o", str(self.out), *args])
        return code, buffer.getvalue()

    def test_downloads_selection_and_reports_speed_and_size(self):
        code, text = self.run_cli("https://p", "-s", "1,3")
        self.assertEqual(code, 0)
        folder = self.out / "Chan" / "videos" / "music"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a [a][192kbps].mp3", "c [c][192kbps].mp3"])
        self.assertIn("1 KB", text)  # file size shown
        self.assertIn("250 B/s", text)                       # speed shown while downloading
        self.assertIn("avg", text)
        self.assertFalse((self.out / ".partial").exists())

    def test_zip(self):
        code, _ = self.run_cli("https://p", "-s", "all", "--zip")
        self.assertEqual(code, 0)
        archive = next(self.out.rglob("*.zip"))
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(sorted(z.namelist()), ["a [a].mp3", "b [b].mp3", "c [c].mp3"])
        self.assertEqual([p.name for p in archive.parent.iterdir()], [archive.name])

    def test_failure_gives_nonzero_exit_and_keeps_going(self):
        bad = dict(PLAYLIST, items=[{"url": "https://x/bad", "title": "B", "id": "bad"},
                                    {"url": "https://x/a", "title": "A", "id": "a"}])
        with mock.patch.object(downloader, "inspect_media", return_value=bad):
            code, text = self.run_cli("https://p", "-s", "all")
        self.assertEqual(code, 1)
        self.assertIn("FAILED", text)
        self.assertTrue((self.out / "Chan" / "videos" / "music" / "a [a][192kbps].mp3").exists())

    def test_failures_are_remembered_once_and_cleared_on_success(self):
        import json
        import failures
        bad = dict(PLAYLIST, items=[{"url": "https://x/bad", "title": "B", "id": "bad"}])
        with mock.patch.object(downloader, "inspect_media", return_value=bad):
            self.run_cli("https://p", "-s", "all")
            self.run_cli("https://p", "-s", "all")  # fails again: still one record
        data = json.loads(failures.file_path(self.out).read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for v in data["folders"].values() for e in v], ["bad"])
        self.assertEqual(list(data["folders"]), ["Chan/videos/music"])
        self.assertIn("boom", (self.out / ".media-downloader" / "errors.log").read_text(encoding="utf-8"))
        works = dict(PLAYLIST, items=[{"url": "https://x/bad", "title": "B", "id": "bad"}])
        with mock.patch.object(downloader, "inspect_media", return_value=works), \
             mock.patch.object(downloader, "download_one", fake_download_one_ok):
            self.run_cli("https://p", "-s", "all")
        data = json.loads(failures.file_path(self.out).read_text(encoding="utf-8"))
        self.assertEqual(data["folders"], {})

    def test_playlist_file_for_playlists_only(self):
        mix = dict(PLAYLIST, title="Road Trip", section=["playlists", "Road Trip"])
        with mock.patch.object(downloader, "inspect_media", return_value=mix):
            code, text = self.run_cli("https://p", "-s", "1,3")
        self.assertEqual(code, 0)
        folder = self.out / "Chan" / "playlists" / "Road Trip" / "music"
        self.assertIn("Playlist file:", text)
        lines = (folder / "Road Trip.m3u8").read_text(encoding="utf-8").splitlines()
        self.assertIn("# 3 items in the playlist, 2 in this folder", lines)
        self.assertIn("# Not in this folder: B | https://x/b", lines)   # B was not selected
        self.run_cli("https://p", "-s", "1")  # a channel tab (section 'videos') gets none
        self.assertEqual(len(list(self.out.rglob("*.m3u8"))), 1)

    def test_mp4_goes_to_the_video_folder(self):
        self.run_cli("https://p", "-s", "1", "-f", "mp4", "-q", "720")
        self.assertTrue((self.out / "Chan" / "videos" / "video" / "a [a][720p].mp4").exists())
        self.assertFalse((self.out / "Chan" / "videos" / "music").exists())

    def test_list_downloads_nothing(self):
        code, text = self.run_cli("https://p", "--list")
        self.assertEqual(code, 0)
        self.assertIn("3 videos, 1:03:10 total", text)
        self.assertIn("[1:05]", text)
        self.assertFalse(self.out.exists())

    def test_skips_what_is_already_downloaded_unless_again(self):
        self.run_cli("https://p", "-s", "1")
        code, text = self.run_cli("https://p", "-s", "1,2")
        self.assertIn("SKIP", text)
        self.assertEqual(text.count(" OK "), 1)  # only b was downloaded
        code, text = self.run_cli("https://p", "-s", "1", "--again")
        self.assertIn(" OK ", text)

    def test_other_quality_is_downloaded_with_a_note(self):
        self.run_cli("https://p", "-s", "1")
        _, text = self.run_cli("https://p", "-s", "1", "-q", "320")
        self.assertIn("you also have: music 192kbps", text)
        self.assertTrue((self.out / "Chan" / "videos" / "music" / "a [a][320kbps].mp3").exists())

    def test_subtitles_flag(self):
        self.run_cli("https://p", "-s", "1", "-f", "mp4", "-q", "720", "--subs", "en")
        folder = self.out / "Chan" / "videos" / "video"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["a [a][720p].en.srt", "a [a][720p].mp4"])

    def test_bad_selection(self):
        code, _ = self.run_cli("https://p", "-s", "9")
        self.assertEqual(code, 1)

    def test_rejects_bad_arguments(self):
        for args in (["https://p", "-q", "999"], ["https://p", "-f", "mp4", "-q", "x"], ["nope"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr", io.StringIO()):
                cli.main(args)

    def test_overrides_proxy_and_cookies(self):
        original = downloader.settings
        self.addCleanup(setattr, downloader, "settings", original)
        self.run_cli("https://p", "--list", "--proxy", "none", "--cookies-from-browser", "none")
        self.assertEqual(downloader.settings.proxy, "")
        self.assertEqual(downloader.settings.cookies_from_browser, ())


class ProgressMeterTests(unittest.TestCase):
    def test_video_plus_audio_is_one_progress(self):
        meter = downloader.ProgressMeter()
        info = {"requested_formats": [{"filesize": 800}, {"filesize": 200}]}
        pct, stats = meter.update({"status": "downloading", "filename": "v.f1", "downloaded_bytes": 400,
                                   "total_bytes": 800, "speed": 100.0, "info_dict": info})
        self.assertEqual(pct, 40.0)
        self.assertEqual(stats["total"], 1000)
        self.assertEqual(stats["eta"], 6.0)
        meter.update({"status": "finished", "filename": "v.f1", "downloaded_bytes": 800, "total_bytes": 800,
                      "info_dict": info})
        pct, stats = meter.update({"status": "downloading", "filename": "a.f2", "downloaded_bytes": 100,
                                   "total_bytes": 200, "speed": 50.0, "info_dict": info})
        self.assertEqual(pct, 90.0)
        self.assertEqual(stats["downloaded"], 900)

    def test_percent_never_goes_backwards_and_caps_at_100(self):
        meter = downloader.ProgressMeter()
        meter.update({"status": "downloading", "filename": "a", "downloaded_bytes": 90, "total_bytes": 100})
        pct, _ = meter.update({"status": "downloading", "filename": "b", "downloaded_bytes": 0, "total_bytes": 1000})
        self.assertGreaterEqual(pct, 8.0)
        pct, _ = meter.update({"status": "downloading", "filename": "b", "downloaded_bytes": 5000, "total_bytes": 1000})
        self.assertLessEqual(pct, 100.0)

    def test_unknown_total(self):
        meter = downloader.ProgressMeter()
        pct, stats = meter.update({"status": "downloading", "filename": "a", "downloaded_bytes": 10, "speed": 5.0})
        self.assertEqual((pct, stats["total"], stats["eta"]), (0.0, None, None))


class FormattingTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(formatting.format_size(1_500_000), "1.5 MB")
        self.assertEqual(formatting.format_size(2_500_000_000), "2.50 GB")
        self.assertEqual(formatting.format_size(None), "?")
        self.assertEqual(formatting.format_speed(3_400_000), "3.4 MB/s")
        self.assertEqual(formatting.format_eta(65), "01:05")
        self.assertEqual(formatting.format_eta(3725), "1:02:05")
        self.assertEqual(formatting.format_eta(None), "--:--")
        self.assertEqual(formatting.format_duration(225), "3:45")
        self.assertEqual(formatting.format_duration(3725), "1:02:05")
        self.assertEqual(formatting.format_duration(None), "")


if __name__ == "__main__":
    unittest.main()
