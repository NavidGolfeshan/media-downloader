"""End-to-end tests of the web API with the yt-dlp layer mocked out."""

import dataclasses
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import downloader  # noqa: E402
import jobs as jobs_module  # noqa: E402
import main  # noqa: E402


def fake_download_one(url, output_dir, mode, quality, on_progress=None, cancel_event=None, subtitle=None):
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if "bad" in url:
        raise RuntimeError("boom")
    if on_progress:
        on_progress("downloading", 50.0, downloaded=500, total=1000, speed=250.0, eta=2.0)
        on_progress("processing", 100.0, speed=None, eta=None)
    vid = url.rsplit("/", 1)[-1]
    path = folder / f"Song {vid} [{vid}].{mode}"
    path.write_bytes(b"x" * 1000)
    if subtitle:
        (folder / f"Song {vid} [{vid}].{subtitle['lang']}.srt").write_text("1\n")
    return path


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "lib"
        main.jobs = jobs_module.JobManager(str(self.root), ttl_minutes=60, max_concurrent=1)
        self.client = main.app.test_client()
        self.saved_settings = dataclasses.replace(config.settings)
        config.settings.download_dir = str(self.root)
        patcher = mock.patch.object(downloader, "download_one", fake_download_one)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: [setattr(config.settings, k, getattr(self.saved_settings, k))
                                 for k in config.EDITABLE])

    def wait(self, job_id, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            data = self.client.get(f"/api/jobs/{job_id}").get_json()
            if data["state"] in ("done", "failed", "cancelled"):
                return data
            time.sleep(0.05)
        self.fail("job did not finish")

    def create(self, **overrides):
        body = {
            "title": "My list", "is_playlist": True, "mode": "mp3", "quality": "192",
            "channel": "Chan", "section": ["videos"],
            "items": [{"url": "https://x/a", "title": "A", "id": "a"},
                      {"url": "https://x/b", "title": "B", "id": "b"}],
        }
        body.update(overrides)
        return self.client.post("/api/jobs", json=body)

    def test_index_served(self):
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)

    def test_parse_selection_endpoint(self):
        ok = self.client.post("/api/parse-selection", json={"text": "1,3-4", "total": 5})
        self.assertEqual(ok.get_json()["indices"], [0, 2, 3])
        bad = self.client.post("/api/parse-selection", json={"text": "9", "total": 5})
        self.assertEqual(bad.status_code, 400)

    def test_inspect_rejects_non_url(self):
        self.assertEqual(self.client.post("/api/inspect", json={"url": "nope"}).status_code, 400)

    def test_files_land_in_library_layout_without_track_folders(self):
        data = self.wait(self.create().get_json()["id"])
        self.assertEqual(data["state"], "done")
        folder = self.root / "Chan" / "videos"
        self.assertEqual(data["folder"], str(folder))
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["Song a [a][192kbps].mp3", "Song b [b][192kbps].mp3"])
        self.assertFalse((self.root / ".partial").exists() and any((self.root / ".partial").iterdir()))
        self.assertEqual(sorted(f["name"] for f in data["files"]),
                         ["Song a [a][192kbps].mp3", "Song b [b][192kbps].mp3"])

    def test_video_folder_and_subtitles(self):
        data = self.wait(self.create(mode="mp4", quality="720", section=["playlists", "Best of"],
                                     subtitle={"lang": "en", "auto": False}).get_json()["id"])
        folder = self.root / "Chan" / "playlists" / "Best of"
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["Song a [a][720p].en.srt", "Song a [a][720p].mp4",
                          "Song b [b][720p].en.srt", "Song b [b][720p].mp4"])
        self.assertEqual(data["tracks"][0]["subtitles"], 1)
        self.assertEqual(data["subtitle"], "en")

    def test_subtitle_ignored_for_mp3(self):
        data = self.wait(self.create(subtitle={"lang": "en"}).get_json()["id"])
        self.assertIsNone(data["subtitle"])

    def test_check_finds_what_is_downloaded(self):
        self.wait(self.create().get_json()["id"])
        found = self.client.post("/api/check", json={"ids": ["a", "zzz"]}).get_json()["found"]
        self.assertEqual(list(found), ["a"])
        self.assertEqual(found["a"][0]["kind"], "music")
        self.assertEqual(found["a"][0]["quality"], "192kbps")
        self.assertEqual(found["a"][0]["rel"], "Chan/videos/Song a [a][192kbps].mp3")

    def test_redownload_replaces_instead_of_duplicating(self):
        self.wait(self.create().get_json()["id"])
        self.wait(self.create().get_json()["id"])
        folder = self.root / "Chan" / "videos"
        self.assertEqual(len(list(folder.iterdir())), 2)

    def test_other_quality_sits_next_to_it(self):
        self.wait(self.create().get_json()["id"])
        self.wait(self.create(quality="320").get_json()["id"])
        names = sorted(p.name for p in (self.root / "Chan" / "videos").iterdir())
        self.assertEqual(names, ["Song a [a][192kbps].mp3", "Song a [a][320kbps].mp3",
                                 "Song b [b][192kbps].mp3", "Song b [b][320kbps].mp3"])
        found = self.client.post("/api/check", json={"ids": ["a"]}).get_json()["found"]["a"]
        self.assertEqual(sorted(r["label"] for r in found), ["music 192kbps", "music 320kbps"])

    def test_library_listing(self):
        self.wait(self.create().get_json()["id"])
        lib = self.client.get("/api/library").get_json()
        self.assertEqual(lib["count"], 2)
        self.assertEqual(lib["folders"][0]["folder"], "Chan/videos")

    def test_partial_failure_still_done(self):
        items = [{"url": "https://x/a", "title": "A", "id": "a"},
                 {"url": "https://x/bad", "title": "B", "id": "bad"}]
        data = self.wait(self.create(items=items).get_json()["id"])
        self.assertEqual(data["state"], "done")
        self.assertEqual([t["status"] for t in data["tracks"]], ["done", "failed"])

    def test_all_failed(self):
        items = [{"url": "https://x/bad", "title": "B"}]
        data = self.wait(self.create(items=items, is_playlist=False).get_json()["id"])
        self.assertEqual(data["state"], "failed")
        self.assertIn("No files", data["error"])

    def test_validation(self):
        self.assertEqual(self.create(mode="flac").status_code, 400)
        self.assertEqual(self.create(quality="999").status_code, 400)
        self.assertEqual(self.create(mode="mp4", quality="abc").status_code, 400)
        self.assertEqual(self.create(items=[]).status_code, 400)
        self.assertEqual(self.create(items=[{"url": "file:///etc/passwd"}]).status_code, 400)

    def test_hostile_folder_names_stay_inside_library(self):
        data = self.wait(self.create(channel="../../evil", section=["..", "a/b"]).get_json()["id"])
        folder = Path(data["folder"]).resolve()
        self.assertTrue(str(folder).startswith(str(self.root.resolve())))

    def test_deleting_a_job_keeps_the_files(self):
        data = self.wait(self.create().get_json()["id"])
        self.assertEqual(self.client.delete(f"/api/jobs/{data['id']}").status_code, 200)
        self.assertTrue(Path(data["folder"]).exists())
        self.assertEqual(self.client.get(f"/api/jobs/{data['id']}").status_code, 404)

    def test_cancel(self):
        def slow(url, output_dir, mode, quality, on_progress=None, cancel_event=None, subtitle=None):
            for _ in range(100):
                if cancel_event.is_set():
                    raise downloader.Cancelled()
                time.sleep(0.02)
            raise AssertionError("was not cancelled")

        with mock.patch.object(downloader, "download_one", slow):
            job_id = self.create().get_json()["id"]
            time.sleep(0.1)
            self.assertEqual(self.client.post(f"/api/jobs/{job_id}/cancel").status_code, 200)
            data = self.wait(job_id)
        self.assertEqual(data["state"], "cancelled")
        self.assertNotIn("downloading", [t["status"] for t in data["tracks"]])

    def test_expired_job_records_are_dropped_but_files_stay(self):
        data = self.wait(self.create().get_json()["id"])
        manager = main.jobs
        manager.get(data["id"]).finished_at = time.time() - 7200
        manager.cleanup_expired()
        self.assertIsNone(manager.get(data["id"]))
        self.assertTrue(Path(data["folder"]).exists())

    def test_startup_never_deletes_the_library(self):
        self.wait(self.create().get_json()["id"])
        jobs_module.JobManager(str(self.root), 60, 1)  # what a restart does
        self.assertEqual(self.client.get("/api/library").get_json()["count"], 2)

    def test_snapshot_reports_speed_and_sizes(self):
        seen = []

        def reporting(url, output_dir, mode, quality, on_progress=None, cancel_event=None, subtitle=None):
            on_progress("downloading", 50.0, downloaded=500, total=1000, speed=250.0, eta=2.0)
            seen.append(next(iter(main.jobs._jobs.values())).snapshot())  # no race with job_id
            return fake_download_one(url, output_dir, mode, quality)

        with mock.patch.object(downloader, "download_one", reporting):
            job_id = self.create(is_playlist=False, items=[{"url": "https://x/a", "title": "A", "id": "a"}]
                                 ).get_json()["id"]
            data = self.wait(job_id)
        live = seen[0]
        self.assertEqual(live["speed"], 250.0)
        self.assertEqual(live["downloaded_bytes"], 500)
        self.assertEqual(live["tracks"][0]["total"], 1000)
        self.assertEqual(data["tracks"][0]["size"], 1000)
        self.assertIsNone(data["speed"])
        self.assertIn("1 KB", data["message"])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = dataclasses.replace(config.settings)
        patcher = mock.patch.dict("os.environ", {"SETTINGS_FILE": str(Path(self.tmp.name) / "s.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: [setattr(config.settings, k, getattr(self.original, k))
                                 for k in config.EDITABLE])
        main.jobs = jobs_module.JobManager(str(Path(self.tmp.name) / "lib"), 60, 1)
        self.client = main.app.test_client()

    def test_change_everything_from_the_page(self):
        target = Path(self.tmp.name) / "new place"
        data = self.client.post("/api/settings", json={
            "download_dir": str(target), "proxy": "10809", "max_concurrent_jobs": "3",
            "job_ttl_minutes": "5"}).get_json()
        self.assertEqual(data["proxy"], "socks5h://127.0.0.1:10809")
        self.assertEqual(data["max_concurrent_jobs"], 3)
        self.assertEqual(data["job_ttl_minutes"], 5)
        self.assertTrue(target.is_dir())
        self.assertEqual(main.jobs.root, target.resolve())
        self.assertEqual(downloader.settings.proxy, "socks5h://127.0.0.1:10809")
        # saved, so it survives a restart
        reloaded = config.load_saved(dataclasses.replace(self.original))
        self.assertEqual(reloaded.download_dir, str(target.resolve()))
        self.assertEqual(reloaded.proxy, "socks5h://127.0.0.1:10809")

    def test_cookies_from_browser_can_be_set_and_cleared(self):
        data = self.client.post("/api/settings", json={"cookies_from_browser": " Firefox:my.profile "}).get_json()
        self.assertEqual(data["cookies_from_browser"], "firefox:my.profile")
        self.assertEqual(config.settings.cookies_from_browser, ("firefox", "my.profile", None, None))
        # saved as text and read back as the tuple yt-dlp expects
        reloaded = config.load_saved(dataclasses.replace(self.original))
        self.assertEqual(reloaded.cookies_from_browser, ("firefox", "my.profile", None, None))
        data = self.client.post("/api/settings", json={"cookies_from_browser": ""}).get_json()
        self.assertEqual(data["cookies_from_browser"], "")
        self.assertEqual(config.settings.cookies_from_browser, ())

    def test_cookie_file_is_checked(self):
        missing = Path(self.tmp.name) / "nope.txt"
        self.assertEqual(self.client.post("/api/settings", json={"cookie_file": str(missing)}).status_code, 400)
        real = Path(self.tmp.name) / "cookies.txt"
        real.write_text("# Netscape HTTP Cookie File\n")
        data = self.client.post("/api/settings", json={"cookie_file": f'"{real}"'}).get_json()
        self.assertEqual(data["cookie_file"], str(real))
        self.client.post("/api/settings", json={"cookie_file": ""})
        self.assertEqual(config.settings.cookie_file, "")

    def test_unknown_browser_is_rejected(self):
        before = config.settings.cookies_from_browser
        response = self.client.post("/api/settings", json={"cookies_from_browser": "netscape"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unknown browser", response.get_json()["error"])
        self.assertEqual(config.settings.cookies_from_browser, before)

    def test_proxy_can_be_turned_off(self):
        self.client.post("/api/settings", json={"proxy": ""})
        self.assertEqual(config.settings.proxy, "")

    def test_bad_values_change_nothing(self):
        before = config.settings.proxy
        for body in ({"max_concurrent_jobs": "0"}, {"job_ttl_minutes": "abc"},
                     {"proxy": "ftp://x"}, {"download_dir": ""}):
            self.assertEqual(self.client.post("/api/settings", json=body).status_code, 400)
        self.assertEqual(config.settings.proxy, before)


class RegionAndHintTests(unittest.TestCase):
    def test_region_endpoint(self):
        fake = {"ip": "1.2.3.4", "country": "Germany", "country_code": "DE",
                "region": "Saxony", "city": "Falkenstein", "via_proxy": False}
        with mock.patch.object(downloader, "lookup_region", return_value=fake):
            data = main.app.test_client().get("/api/region").get_json()
        self.assertEqual(data["country_code"], "DE")

    def test_region_endpoint_failure(self):
        with mock.patch.object(downloader, "lookup_region", side_effect=RuntimeError("nope")):
            response = main.app.test_client().get("/api/region")
        self.assertEqual(response.status_code, 502)

    def test_format_error_gets_js_runtime_hint_not_region_hint(self):
        text = downloader.explain_error("[youtube] x: Requested format is not available.")
        self.assertIn("JavaScript runtime", text)
        self.assertNotIn("region-locked", text)

    def test_js_runtimes_only_lists_installed(self):
        with mock.patch.object(downloader.shutil, "which",
                               side_effect=lambda n: "/usr/bin/node" if n == "node" else None):
            self.assertEqual(downloader.js_runtimes(), {"node": {"path": "/usr/bin/node"}})


class DownloaderHelperTests(unittest.TestCase):
    def test_normalize_entry_url(self):
        self.assertEqual(downloader.normalize_entry_url({"url": "https://a/b"}), "https://a/b")
        self.assertEqual(
            downloader.normalize_entry_url({"url": "abc123XYZ00", "ie_key": "Youtube"}),
            "https://www.youtube.com/watch?v=abc123XYZ00",
        )
        self.assertIsNone(downloader.normalize_entry_url({"url": "weird"}))
        self.assertIsNone(downloader.normalize_entry_url({}))

    def test_choose_heights(self):
        self.assertEqual(downloader.choose_heights([144, 360, 720, 1080, 1081]), [360, 720, 1080])
        self.assertEqual(downloader.choose_heights([]), [720])
        self.assertEqual(downloader.choose_heights([100, 200]), [100, 200])

    def test_inspect_media_playlist_cap(self):
        info = {"title": "P", "entries": [
            {"url": "https://a/1", "title": "1"}, None, {"url": "https://a/2", "title": "2"},
            {"url": "https://a/3"},
        ]}
        with mock.patch.object(downloader, "inspect_url", return_value=info):
            full = downloader.inspect_media("https://p")
            self.assertEqual(len(full["items"]), 3)
            self.assertEqual(full["items"][2]["title"], "Untitled")
        capped_settings = dataclasses.replace(downloader.settings, max_playlist_items=2)
        with mock.patch.object(downloader, "inspect_url", return_value=info), \
             mock.patch.object(downloader, "settings", capped_settings):
            self.assertEqual(len(downloader.inspect_media("https://p")["items"]), 2)

    def test_inspect_media_channel_sections(self):
        info = {"title": "Chan", "entries": [
            {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@c/videos", "title": "Chan - Videos"},
            {"_type": "playlist", "webpage_url": "https://www.youtube.com/@c/playlists", "title": "Chan - Playlists"},
            {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@c/videos", "title": "dup"},
        ]}
        with mock.patch.object(downloader, "inspect_url", return_value=info), \
             mock.patch.object(downloader, "probe_tab", return_value=None):
            result = downloader.inspect_media("https://www.youtube.com/@c")
        self.assertEqual(result["items"], [])
        self.assertEqual([g["url"] for g in result["groups"]], [
            "https://www.youtube.com/@c/videos", "https://www.youtube.com/@c/playlists"])

    def test_bare_channel_gets_playlists_and_releases(self):
        # yt-dlp lists only Videos/Shorts for a bare channel address.
        info = {"title": "Chan", "entries": [
            {"url": "abc123XYZ00", "ie_key": "Youtube", "title": "A video", "id": "abc123XYZ00"},
            {"_type": "url", "ie_key": "YoutubeTab", "url": "https://www.youtube.com/@c/shorts", "title": "Chan - Shorts"},
        ]}
        asked = []

        def probe(url, label):
            asked.append(url)
            if url.endswith("/releases"):
                return None  # this channel has no Releases tab
            return {"url": url, "title": "Chan - " + label}

        with mock.patch.object(downloader, "inspect_url", return_value=info), \
             mock.patch.object(downloader, "probe_tab", side_effect=probe):
            result = downloader.inspect_media("https://www.youtube.com/@c/")
        self.assertEqual(asked, ["https://www.youtube.com/@c/playlists", "https://www.youtube.com/@c/releases"])
        self.assertEqual([g["url"] for g in result["groups"]], [
            "https://www.youtube.com/@c/shorts", "https://www.youtube.com/@c/playlists"])
        self.assertEqual(len(result["items"]), 1)

    def test_channel_with_only_playlists_is_not_empty(self):
        with mock.patch.object(downloader, "inspect_url", return_value={"title": "Chan", "entries": []}), \
             mock.patch.object(downloader, "probe_tab",
                               side_effect=lambda url, label: {"url": url, "title": label}):
            result = downloader.inspect_media("https://www.youtube.com/channel/UC123")
        self.assertEqual([g["title"] for g in result["groups"]], ["Playlists", "Releases"])

    def test_tabs_are_not_probed_for_other_addresses(self):
        info = {"title": "x", "entries": [{"url": "https://a/1", "id": "1"}]}
        with mock.patch.object(downloader, "inspect_url", return_value=info), \
             mock.patch.object(downloader, "probe_tab") as probe:
            for url in ("https://www.youtube.com/@c/videos", "https://www.youtube.com/@c/playlists",
                        "https://www.youtube.com/playlist?list=PL1", "https://vimeo.com/user1"):
                downloader.inspect_media(url)
        probe.assert_not_called()

    def test_probe_tab_handles_missing_tab(self):
        class Boom:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, *a, **k): raise RuntimeError("This channel does not have a releases tab")
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", Boom):
            self.assertIsNone(downloader.probe_tab("https://www.youtube.com/@c/releases", "Releases"))

    def test_inspect_media_playlists_tab_and_mixed(self):
        info = {"title": "Playlists", "entries": [
            {"ie_key": "YoutubeTab", "url": "https://www.youtube.com/playlist?list=PL1", "title": "One"},
            {"ie_key": "YoutubeTab", "id": "PL2", "title": "Two"},
            {"ie_key": "Youtube", "url": "abc123XYZ00", "title": "A video"},
        ]}
        with mock.patch.object(downloader, "inspect_url", return_value=info):
            result = downloader.inspect_media("https://www.youtube.com/@c/playlists")
        self.assertEqual([g["url"] for g in result["groups"]], [
            "https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/playlist?list=PL2"])
        self.assertEqual(len(result["items"]), 1)

    def test_inspect_media_empty_raises(self):
        with mock.patch.object(downloader, "inspect_url", return_value={"title": "x", "entries": [None]}):
            with self.assertRaises(RuntimeError):
                downloader.inspect_media("https://p")

    def test_inspect_media_single(self):
        info = {"title": "V", "id": "vid1", "uploader_id": "@ExampleChannel", "duration": 225}
        with mock.patch.object(downloader, "inspect_url", return_value=info):
            result = downloader.inspect_media("https://www.youtube.com/watch?v=vid1")
        self.assertEqual(result, {"is_playlist": False, "title": "V", "channel": "ExampleChannel",
                                  "section": ["videos"],
                                  "items": [{"url": "https://www.youtube.com/watch?v=vid1",
                                             "title": "V", "id": "vid1", "duration": 225}]})

    def test_durations_on_items(self):
        info = {"title": "P", "entries": [{"url": "https://a/1", "id": "1", "duration": 185.0},
                                          {"url": "https://a/2", "id": "2"},
                                          {"url": "https://a/3", "id": "3", "duration": 0}]}
        with mock.patch.object(downloader, "inspect_url", return_value=info):
            items = downloader.inspect_media("https://p")["items"]
        self.assertEqual([i["duration"] for i in items], [185, None, None])

    def test_channel_and_section_naming(self):
        info = {"title": "Example - Videos", "entries": [{"url": "https://a/1", "title": "1", "id": "1"}]}
        with mock.patch.object(downloader, "inspect_url", return_value=info):
            r = downloader.inspect_media("https://www.youtube.com/@ExampleChannel/videos")
            self.assertEqual((r["channel"], r["section"]), ("ExampleChannel", ["videos"]))
            self.assertEqual(r["items"][0]["id"], "1")
            r = downloader.inspect_media("https://www.youtube.com/@ExampleChannel/shorts")
            self.assertEqual(r["section"], ["shorts"])
            r = downloader.inspect_media("https://www.youtube.com/playlist?list=PL1", "ExampleChannel")
            self.assertEqual((r["channel"], r["section"]), ("ExampleChannel", ["playlists", "Example - Videos"]))
        self.assertEqual(downloader.channel_name("https://vimeo.com/x", {"uploader": "Ann"}), "Ann")
        self.assertEqual(downloader.channel_name("https://www.example.com/x", {}), "example.com")

    def test_list_subtitles(self):
        info = {"language": "en",
                "subtitles": {"en": [{"name": "English"}], "de": [{"name": "German"}], "live_chat": [{}]},
                "automatic_captions": {"en": [{"name": "English"}], "en-orig": [{"name": "English (Original)"}],
                                       "fr": [{"name": "French"}]}}
        subs = downloader.list_subtitles(info)
        self.assertEqual([(s["lang"], s["auto"]) for s in subs],
                         [("en", False), ("de", False), ("en-orig", True)])
        self.assertEqual(downloader.list_subtitles({}), [])
        only_auto = {"language": "en", "automatic_captions": {"en": [{"name": "English"}], "fr": [{}]}}
        self.assertEqual([s["lang"] for s in downloader.list_subtitles(only_auto)], ["en"])

    def test_subtitle_options(self):
        manual = downloader.subtitle_options({"lang": "de", "auto": False})
        self.assertTrue(manual["writesubtitles"])
        self.assertFalse(manual["writeautomaticsub"])
        self.assertEqual(manual["subtitleslangs"], ["de"])
        self.assertEqual(manual["postprocessors"][0]["format"], "srt")
        auto = downloader.subtitle_options({"lang": "en-orig", "auto": True})
        self.assertTrue(auto["writeautomaticsub"])
        self.assertFalse(auto["writesubtitles"])

    def test_formats_endpoint_includes_subtitles(self):
        info = {"language": "en", "subtitles": {"en": [{"name": "English"}]}, "formats": [
            {"height": 720, "vcodec": "avc1"}]}
        with mock.patch.object(downloader, "inspect_video_formats", return_value=(info, [720])):
            data = main.app.test_client().get("/api/formats?url=https://x/v").get_json()
        self.assertEqual(data["heights"], [720])
        self.assertEqual(data["subtitles"][0]["lang"], "en")


if __name__ == "__main__":
    unittest.main()
