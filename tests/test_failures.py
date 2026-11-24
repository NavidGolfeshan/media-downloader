"""failed.json, errors.log and the subtitle helpers."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import downloader  # noqa: E402
import errorlog  # noqa: E402
import failures  # noqa: E402
import library  # noqa: E402


class FailuresTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def add(self, id="a", url=None, folder="Chan/videos", title="A", error="boom"):
        return failures.record(self.root, folder, id=id, title=title,
                               url=url or f"https://x/{id}", error=error, fmt="music 192kbps")

    def read(self):
        return json.loads(failures.file_path(self.root).read_text(encoding="utf-8"))

    def test_structure_follows_the_folders(self):
        self.add("a", folder="Chan/videos")
        self.add("b", folder="Chan/playlists/My Mix")
        data = self.read()
        self.assertEqual(data["version"], 1)
        self.assertEqual(sorted(data["folders"]), ["Chan/playlists/My Mix", "Chan/videos"])
        entry = data["folders"]["Chan/videos"][0]
        self.assertEqual(set(entry), {"id", "title", "url", "error", "format", "failed_at"})
        self.assertEqual((entry["id"], entry["title"], entry["url"]), ("a", "A", "https://x/a"))

    def test_an_item_is_written_only_once(self):
        self.assertTrue(self.add("a"))
        self.assertFalse(self.add("a"))                                   # same id, same folder
        self.assertFalse(self.add("a", folder="Chan/playlists/Other"))    # same id, other folder
        self.assertFalse(self.add("a", url="https://x/other-address"))    # same id, other url
        self.assertEqual(sum(len(v) for v in self.read()["folders"].values()), 1)

    def test_item_without_id_is_matched_by_url(self):
        self.assertTrue(self.add(id=None, url="https://x/no-id"))
        self.assertFalse(self.add(id=None, url="https://x/no-id"))
        self.assertTrue(self.add(id=None, url="https://x/another"))

    def test_concurrent_writers_never_duplicate_or_corrupt(self):
        def worker(n):
            for i in range(10):
                self.add(f"id{i}", title=f"T{i}", error=f"from thread {n}")
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        ids = [e["id"] for v in self.read()["folders"].values() for e in v]
        self.assertEqual(sorted(ids), sorted(f"id{i}" for i in range(10)))

    def test_success_removes_the_record_and_empty_folders(self):
        self.add("a")
        self.add("b", folder="Chan/playlists/P")
        self.assertTrue(failures.clear(self.root, id="a", url="https://x/a"))
        self.assertFalse(failures.clear(self.root, id="a", url="https://x/a"))
        self.assertEqual(list(self.read()["folders"]), ["Chan/playlists/P"])

    def test_lookup_by_id(self):
        self.add("a", error="ERROR: [youtube] a: not available in your country\nmore text")
        found = failures.lookup(self.root, ["a", "zzz"])
        self.assertEqual(list(found), ["a"])
        self.assertEqual(found["a"]["error"], "[youtube] a: not available in your country")
        self.assertEqual(found["a"]["folder"], "Chan/videos")
        self.assertEqual(failures.lookup(Path(self.tmp.name) / "nothing-here", ["a"]), {})

    def test_broken_file_is_kept_aside_not_overwritten(self):
        path = failures.file_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_text("{ this is not json", encoding="utf-8")
        self.assertTrue(self.add("a"))
        self.assertEqual(path.with_name("failed.json.corrupt").read_text(encoding="utf-8"), "{ this is not json")
        self.assertEqual(self.read()["folders"]["Chan/videos"][0]["id"], "a")

    def test_note_failure_logs_everything_and_lists_the_item(self):
        long_error = "first line\n" + "Andorra, " * 300
        failures.note_failure(self.root, self.root / "Chan" / "videos", id="a", title="A",
                              url="https://x/a", error=long_error, fmt="video 720p")
        failures.note_failure(self.root, self.root / "Chan" / "videos", id="a", title="A",
                              url="https://x/a", error=long_error, fmt="video 720p")
        self.assertEqual(sum(len(v) for v in self.read()["folders"].values()), 1)  # listed once
        log = errorlog.log_path(self.root).read_text(encoding="utf-8")
        self.assertEqual(log.count("Download failed"), 2)                           # logged every time
        self.assertTrue(("Andorra, " * 299) in log)                                 # not shortened
        self.assertIn("URL: https://x/a", log)

    def test_remember_false_only_logs(self):
        failures.note_failure(self.root, self.root, id="a", title="A", url="https://x/a",
                              error="x", fmt="subtitles [en]", what="Subtitle download failed", remember=False)
        self.assertFalse(failures.file_path(self.root).exists())
        self.assertIn("Subtitle download failed", errorlog.log_path(self.root).read_text(encoding="utf-8"))

    def test_hidden_folder_is_not_part_of_the_library(self):
        self.add("a")
        errorlog.write(self.root, "x", "y")
        self.assertEqual(library.scan(self.root), {})
        self.assertEqual(library.listing(self.root)["count"], 0)


class ErrorLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_block_has_time_details_error_and_traceback(self):
        try:
            raise ValueError("\x1b[0;31mERROR:\x1b[0m something broke")
        except ValueError as exc:
            errorlog.write(self.root, "Job failed", exc, {"Title": "T", "Empty": ""}, exc)
        log = errorlog.log_path(self.root).read_text(encoding="utf-8")
        self.assertIn("| Job failed ====", log)
        self.assertIn("Title: T", log)
        self.assertNotIn("Empty:", log)
        self.assertIn("ERROR: something broke", log)      # colour codes removed
        self.assertNotIn("\x1b", log)
        self.assertIn("--- Traceback ---", log)
        self.assertIn("ValueError", log)

    def test_entries_are_appended(self):
        errorlog.write(self.root, "one", "first")
        errorlog.write(self.root, "two", "second")
        log = errorlog.log_path(self.root).read_text(encoding="utf-8")
        self.assertLess(log.index("first"), log.index("second"))

    def test_big_log_rolls_over(self):
        path = errorlog.log_path(self.root)
        path.parent.mkdir(parents=True)
        path.write_text("x" * (errorlog.MAX_BYTES + 1), encoding="utf-8")
        errorlog.write(self.root, "new", "fresh")
        self.assertTrue(path.with_name("errors.log.1").exists())
        self.assertNotIn("xxxx", path.read_text(encoding="utf-8"))

    def test_never_raises(self):
        blocker = self.root / "blocker"
        blocker.write_text("a file where a folder should be")
        with self.assertLogs("errorlog", "WARNING"):  # it only warns; it must not raise
            errorlog.write(blocker, "x", "y")  # cannot create blocker/.media-downloader


class SubtitleFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_suffix(self):
        self.assertEqual(library.subtitle_suffix("Title [abc].en.srt", "abc"), ".en.srt")
        self.assertEqual(library.subtitle_suffix("Mr. Smith [abc].pt-BR.srt", "abc"), ".pt-BR.srt")
        self.assertEqual(library.subtitle_suffix("Mr. Smith.en-orig.srt", None), ".en-orig.srt")

    def test_subtitles_are_named_after_each_video_and_staging_is_removed(self):
        folder = self.base / "lib" / "Chan" / "videos"
        folder.mkdir(parents=True)
        v480, v720 = folder / "Song [a][480p].mp4", folder / "Song [a][720p].mp4"
        v480.write_bytes(b"1"); v720.write_bytes(b"1")
        (folder / "Song [a][480p].en.srt").write_text("old")
        staging = self.base / "stage"
        staging.mkdir()
        (staging / "Song [a].en.srt").write_text("new")
        placed = library.place_subtitles(staging, [v480, v720], "a")
        self.assertEqual(sorted(p.name for p in placed), ["Song [a][480p].en.srt", "Song [a][720p].en.srt"])
        self.assertEqual((folder / "Song [a][480p].en.srt").read_text(), "new")  # replaced the old one
        self.assertFalse(staging.exists())

    def test_video_files_only_returns_videos(self):
        index = {"a": [{"kind": "music", "rel": "C/Song [a][192kbps].mp3"},
                       {"kind": "video", "rel": "C/Song [a][720p].mp4"}]}
        self.assertEqual(library.video_files(index, "a", "/lib"), [Path("/lib/C/Song [a][720p].mp4")])
        self.assertEqual(library.video_files(index, "zzz", "/lib"), [])


class DownloadSubtitlesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "stage"

    def fake_ydl(self, write_srt=True, error=None):
        seen = {}
        folder = self.folder

        class FakeYDL:
            def __init__(self, options):
                seen["options"] = options
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, url, download=True):
                seen["url"] = url
                if error:
                    raise error
                if write_srt:
                    (folder / "Song [a].en.srt").write_text("1")
        return FakeYDL, seen

    def test_asks_only_for_the_subtitle(self):
        fake, seen = self.fake_ydl()
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", fake):
            files = downloader.download_subtitles("https://x/a", str(self.folder), {"lang": "en", "auto": False})
        self.assertEqual([f.name for f in files], ["Song [a].en.srt"])
        options = seen["options"]
        self.assertTrue(options["skip_download"])
        self.assertTrue(options["noplaylist"])
        self.assertTrue(options["writesubtitles"])
        self.assertFalse(options["writeautomaticsub"])
        self.assertEqual(options["subtitleslangs"], ["en"])
        self.assertEqual(options["postprocessors"][0]["key"], "FFmpegSubtitlesConvertor")

    def test_automatic_captions(self):
        fake, seen = self.fake_ydl()
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", fake):
            downloader.download_subtitles("https://x/a", str(self.folder), {"lang": "en-orig", "auto": True})
        self.assertTrue(seen["options"]["writeautomaticsub"])
        self.assertFalse(seen["options"]["writesubtitles"])

    def test_no_subtitle_in_that_language(self):
        fake, _ = self.fake_ydl(write_srt=False)
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", fake):
            with self.assertRaisesRegex(RuntimeError, "no subtitles in the language 'fr'"):
                downloader.download_subtitles("https://x/a", str(self.folder), {"lang": "fr"})

    def test_yt_dlp_error_gets_the_usual_hint(self):
        fake, _ = self.fake_ydl(error=Exception("ERROR: Private video"))
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", fake):
            with self.assertRaisesRegex(RuntimeError, "unavailable, private or region-locked"):
                downloader.download_subtitles("https://x/a", str(self.folder), {"lang": "en"})

    def test_cancelled_before_starting(self):
        event = threading.Event(); event.set()
        with self.assertRaises(downloader.Cancelled):
            downloader.download_subtitles("https://x/a", str(self.folder), {"lang": "en"}, cancel_event=event)


if __name__ == "__main__":
    unittest.main()
