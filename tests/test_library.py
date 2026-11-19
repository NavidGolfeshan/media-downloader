import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import library  # noqa: E402


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def touch(self, rel, size=10):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def test_destination_layout(self):
        self.assertEqual(library.destination(self.root, "ExampleChannel", ["videos"]),
                         self.root / "ExampleChannel" / "videos")
        self.assertEqual(library.destination(self.root, "ExampleChannel", ["playlists", "Best of"]),
                         self.root / "ExampleChannel" / "playlists" / "Best of")

    def test_names_cannot_escape_the_library(self):
        dest = library.destination(self.root, "../..", ["..", "a/b", "c:d"])
        self.assertTrue(dest.resolve().is_relative_to(self.root.resolve()))

    def test_media_id(self):
        self.assertEqual(library.media_id("Some [x] title [abc-_1].mp3"), "abc-_1")
        self.assertEqual(library.media_id("Some Video [abc123XYZ00][480p].mp4"), "abc123XYZ00")
        self.assertEqual(library.name_quality("Some Video [abc123XYZ00][480p].mp4"), "480p")
        self.assertEqual(library.name_quality("Song [a][192kbps].mp3"), "192kbps")
        self.assertEqual(library.name_quality("Song [a].mp3"), "")
        self.assertEqual(library.media_id("Song [abc].en.srt"), None)  # subtitles are not media
        self.assertIsNone(library.media_id("no id.mp3"))

    def test_scan_reads_kind_and_quality_from_file_names(self):
        self.touch("C/videos/A [a1][1080p].mp4")
        self.touch("C/videos/A [a1][192kbps].mp3")
        self.touch("C/videos/A [a1][1080p].en.srt")
        found = library.scan(self.root)["a1"]
        self.assertEqual(sorted(r["label"] for r in found), ["music 192kbps", "video 1080p"])

    def test_scan_still_understands_the_older_nested_layout(self):
        self.touch("C/videos/video/480p/Some Video [abc123XYZ00].mp4")
        record = library.scan(self.root)["abc123XYZ00"][0]
        self.assertEqual(record["label"], "video 480p")

    def test_scan_handles_files_outside_the_layout_and_ignores_partials(self):
        self.touch("old/Loose [l1].mp3")
        self.touch(".partial/abc/Half [h1].mp4")
        self.touch("C/videos/Other.mp3")  # no id: can't be matched
        index = library.scan(self.root)
        self.assertEqual(index["l1"][0]["kind"], "music")
        self.assertEqual(index["l1"][0]["quality"], "")
        self.assertEqual(index["l1"][0]["label"], "music")
        self.assertNotIn("h1", index)
        self.assertEqual(len(index), 1)

    def test_place_adds_quality_and_replaces_only_the_same_quality(self):
        dest = self.root / "C" / "videos"
        self.touch("C/videos/Old title [z9][480p].mp4")
        self.touch("C/videos/Old title [z9][480p].en.srt")
        self.touch("C/videos/Old title [z9][720p].mp4")
        staging = self.root / ".partial" / "k"
        self.touch(".partial/k/New title [z9].mp4")
        self.touch(".partial/k/New title [z9].en.srt")
        self.touch(".partial/k/leftover [z9].mp4.part")
        placed = library.place(staging, dest, "480p")
        self.assertEqual(sorted(p.name for p in dest.iterdir()),
                         ["New title [z9][480p].en.srt", "New title [z9][480p].mp4", "Old title [z9][720p].mp4"])
        self.assertEqual(len(placed), 2)
        self.assertFalse(staging.exists())

    def test_listing_groups_by_folder(self):
        self.touch("C/videos/A [a][192kbps].mp3", 5)
        self.touch("C/videos/B [b][192kbps].mp3", 7)
        self.touch("C/shorts/S [s][720p].mp4", 3)
        lib = library.listing(self.root)
        self.assertEqual((lib["count"], lib["size"]), (3, 15))
        self.assertEqual([g["folder"] for g in lib["folders"]],
                         ["C/shorts", "C/videos"])


if __name__ == "__main__":
    unittest.main()
