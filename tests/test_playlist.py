"""Playlist files (.m3u8) and the music / video folders."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import library  # noqa: E402
import playlist  # noqa: E402

ENTRIES = [
    {"id": "a1", "title": "First Song", "url": "https://x/a1", "duration": 215, "uploader": "Artist One"},
    {"id": "b2", "title": "Second Song", "url": "https://x/b2", "duration": None, "uploader": "Artist Two"},
    {"id": "c3", "title": "Artist One - Third", "url": "https://x/c3", "duration": 60, "uploader": "Artist One"},
    {"id": "d4", "title": "Fourth", "url": "https://x/d4"},
]


class PlaylistFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def touch(self, name, mtime=None):
        path = self.folder / name
        path.write_bytes(b"x")
        if mtime:
            os.utime(path, (mtime, mtime))
        return path

    def lines(self, path):
        return path.read_text(encoding="utf-8").splitlines()

    def test_lists_the_whole_playlist_in_order(self):
        self.touch("First Song [a1][192kbps].mp3")
        self.touch("Fourth [d4][192kbps].mp3")
        path = playlist.write(self.folder, "My Mix", "https://www.youtube.com/playlist?list=PL1", ENTRIES)
        self.assertEqual(path, self.folder / "My Mix.m3u8")
        lines = self.lines(path)
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(lines[1], "#PLAYLIST:My Mix")
        self.assertEqual(lines[2], "# Source: https://www.youtube.com/playlist?list=PL1")
        self.assertIn("# 4 items in the playlist, 2 in this folder", lines)
        body = lines[lines.index("# 4 items in the playlist, 2 in this folder") + 1:]
        self.assertEqual(body, [
            "#EXTINF:215,Artist One - First Song",           # who uploaded it: playlists mix artists
            "First Song [a1][192kbps].mp3",                   # a playable, relative entry
            "# Not in this folder: Artist Two - Second Song | https://x/b2",
            "# Not in this folder: Artist One - Third | https://x/c3",   # artist not repeated
            "#EXTINF:-1,Fourth",
            "Fourth [d4][192kbps].mp3",
        ])

    def test_running_again_after_the_rest_is_downloaded_completes_the_same_file(self):
        self.touch("First Song [a1][192kbps].mp3")
        playlist.write(self.folder, "My Mix", None, ENTRIES)
        for name in ("Second Song [b2][192kbps].mp3", "Artist One - Third [c3][192kbps].mp3",
                     "Fourth [d4][192kbps].mp3"):
            self.touch(name)
        playlist.write(self.folder, "My Mix", None, ENTRIES)
        text = (self.folder / "My Mix.m3u8").read_text(encoding="utf-8")
        self.assertIn("# 4 items in the playlist, 4 in this folder", text)
        self.assertNotIn("Not in this folder", text)
        self.assertEqual(len(list(self.folder.glob("*.m3u8"))), 1)   # rewritten, not duplicated

    def test_newest_version_is_used_when_two_qualities_exist(self):
        self.touch("First Song [a1][128kbps].mp3", mtime=1_000_000)
        self.touch("First Song [a1][320kbps].mp3", mtime=2_000_000)
        path = playlist.write(self.folder, "Mix", None, ENTRIES[:1])
        self.assertEqual(self.lines(path)[-1], "First Song [a1][320kbps].mp3")

    def test_other_files_in_the_folder_are_not_entries(self):
        self.touch("First Song [a1][192kbps].mp3")
        self.touch("First Song [a1][192kbps].en.srt")
        self.touch("notes.txt")
        path = playlist.write(self.folder, "Mix", None, ENTRIES[:1])
        self.assertEqual([l for l in self.lines(path) if not l.startswith("#")], ["First Song [a1][192kbps].mp3"])

    def test_awkward_titles_stay_one_line_and_the_file_name_is_safe(self):
        entries = [{"id": "z", "title": "Line one\nline two\r\n#EXTINF:1,evil", "url": "https://x/z\nhttps://evil"}]
        path = playlist.write(self.folder, 'Best of: "2026"/<mix>', None, entries)
        self.assertEqual(path.parent, self.folder)
        self.assertNotIn("/", path.name.removesuffix(".m3u8"))
        body = self.lines(path)
        self.assertEqual(len([l for l in body if l.startswith("# Not in this folder")]), 1)
        self.assertEqual([l for l in body if l.startswith("#EXTINF")], [])   # nothing smuggled in

    def test_unicode(self):
        self.touch("Größe – 歌 [u1][192kbps].mp3")
        path = playlist.write(self.folder, "日本語 mix", None, [{"id": "u1", "title": "Größe – 歌"}])
        text = path.read_bytes().decode("utf-8")
        self.assertIn("Größe – 歌 [u1][192kbps].mp3", text)
        self.assertEqual(path.name, "日本語 mix.m3u8")

    def test_nothing_to_write_for_an_empty_playlist(self):
        self.assertIsNone(playlist.write(self.folder, "Empty", None, []))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_playlist_files_are_not_media_in_the_library(self):
        self.touch("First Song [a1][192kbps].mp3")
        playlist.write(self.folder, "Mix", None, ENTRIES)
        self.assertEqual(library.listing(self.folder)["count"], 1)


class FolderTests(unittest.TestCase):
    def test_music_and_video_never_share_a_folder(self):
        root = Path("/lib")
        music = library.destination(root, "Chan", ["playlists", "Mix"], library.kind_for("mp3"))
        video = library.destination(root, "Chan", ["playlists", "Mix"], library.kind_for("mp4"))
        self.assertEqual(music, root / "Chan" / "playlists" / "Mix" / "music")
        self.assertEqual(video, root / "Chan" / "playlists" / "Mix" / "video")


if __name__ == "__main__":
    unittest.main()
