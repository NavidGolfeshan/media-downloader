# Media Downloader (web UI)

A small local web app, with a terminal mode for when you don't want a browser. Paste a video or playlist URL, pick the tracks, format and quality, and the files are saved straight into your download folder, organised by channel. Downloads use `yt-dlp`. No Telegram, no bot tokens.

> Use this project only for content you own, have permission to download, or are otherwise legally allowed to save. Respect each platform's terms and applicable copyright law.

## Download (Windows)

Grab `MediaDownloader-windows.zip` from the [Releases](../../releases) page, unzip the **whole folder**, and run `MediaDownloader.exe`. No Python needed. The app is not code-signed, so Windows SmartScreen may warn you the first time ("More info" > "Run anyway").

## Features

- Browser UI: look up a URL, choose options, watch progress, and the files land in your download folder. There is no temporary storage and no timer that deletes anything.
- **Organised library**: every file goes to `CHANNEL/SECTION/music/` (MP3) or `CHANNEL/SECTION/video/` (MP4), so the music and the video version of the same thing never share a folder. The id and quality are at the end of the name, for example
  `ExampleChannel/videos/video/Some Video [abc123XYZ00][480p].mp4` or `ExampleChannel/playlists/My Mix/music/Some Track [def456UVW11][192kbps].mp3`.
  Files downloaded with an earlier version stay where they are; they are still recognised as downloaded.
  The channel name is the `@handle` from the URL. Subtitles sit beside the video with the same name (`... [480p].en.srt`).
- **Playlist file**: when you download from a playlist (for example one that mixes many artists), a standard `Playlist name.m3u8` is saved in the same `music/` or `video/` folder as the tracks, so you can tell what the playlist was. It lists the whole playlist in order as `Artist - Title`, with the playlist's link at the top. Tracks in the folder are normal entries (open it in VLC or any player); tracks not downloaded, or that failed, are comment lines with their link. It is rewritten after each run, so downloading the rest later completes the same file. Channel tabs such as Videos or Shorts do not get one.
- **Already downloaded?** Each item in a list shows one coloured tag per version you already have, with the folder it is in (hover for the full path): a green **♪ music 192kbps** and an orange **▶ video 720p**, so the two can't be mixed up. The tag that matches what you have selected now is outlined. Select all / a range such as `1-10` / Download then asks before downloading things again:
  - *same quality*: "you are trying to download some items again";
  - *different quality*: "...you have the 'video 720p' version of this";
  - *different format* (music vs video): "...you have the 'music 192kbps' version of this".
  **Yes** keeps them, **No** unselects them, **Skip this message** keeps them and stops asking for the current list. Closing the box (Esc) does nothing.
- Re-downloading the same version replaces the old file; you never end up with two copies.
- **Library** button: everything you have, grouped by folder, with sizes and the full path.
- **Subtitles** (MP4): if the video has subtitles, tick the box, pick a language and they are saved as `.srt` next to the video. Uploaded subtitles are offered, plus the auto-generated captions in the video's own language.
- **Settings** button: download folder, proxy, how many downloads run at once, and how long finished jobs stay on the page. No file editing, effective immediately, and remembered in `user_settings.json`.
- The length of each video/track is shown in the list, and the Download button shows the total length of what you selected.
- Live download speed, bytes downloaded, ETA and final file size, per item and for the whole job.
- Terminal mode (`cli.py`): the same downloads and the same folders without a browser (see below).
- Channels and playlist lists: paste a channel URL (or its Playlists tab) and pick a section such as Videos, Shorts or a playlist; it opens and shows the videos inside, with a Back button.
- Tick tracks individually, or type `all`, numbers, or ranges such as `1,3,5-10` into the selection box.
- MP3 quality: 128, 192 or 320 kbps. MP4 maximum resolution, based on what the first selected video offers.
- Per-track progress, a Cancel button, and failed tracks reported without stopping the rest.
- A running job survives a browser refresh (the page reconnects to it).
- **Back to the list**: when a download finishes, "Back to the list" returns to the playlist or channel section you picked from (same list, nothing typed again, your format and quality kept, selection cleared) with the "downloaded" and "failed" tags refreshed. After a page reload it looks the same address up again. "New link" starts from an empty page instead.
- **Failed items**: an item that fails is marked in red ("Failed before ...") every time you look the list up again, and is written to `failed.json` (see below). Downloading it successfully later removes the mark.
- **Full errors**: the page shows a short version of an error; the complete text (everything yt-dlp said, plus the Python traceback) is appended to `errors.log` with the time, title, URL and format.
- **Subtitles only**: select videos you already downloaded and a "Subtitles only" box appears. Pick a language and only the subtitle is fetched, saved as `.srt` next to each downloaded video file (`Title [id][720p].en.srt`). A video without that language is reported as failed in that job; it is not added to `failed.json`, because the video itself is fine.

### Where the failure records are kept

Both files live in a hidden-style folder inside your download folder, `.media-downloader/` (its path is shown at the bottom of the page and in the job card). It is not part of the library listing.

`failed.json` follows the library's folder layout. An item is written only once (matched by its id, or by its URL when it has no id), whatever format or playlist it failed in:

```json
{
  "version": 1,
  "folders": {
    "ExampleChannel/playlists/My Mix/music": [
      {"id": "def456UVW11", "title": "Some Track", "url": "https://...",
       "error": "short reason", "format": "music 192kbps", "failed_at": "2026-11-19T15:02:11"}
    ]
  }
}
```

`errors.log` keeps every failure (also repeated ones), and rolls over to `errors.log.1` after about 2 MB. The terminal mode (`cli.py`) writes to the same two files.
- Optional cookie-file support for sites where you are legitimately signed in.

## Project files

```text
media-downloader/
├── main.py          # Flask app and API routes
├── config.py        # settings from .env
├── downloader.py    # yt-dlp inspection + download
├── cli.py           # terminal interface (no browser needed)
├── library.py       # folder layout, "already downloaded?" lookups, saving files
├── jobs.py          # background jobs and progress
├── formatting.py    # sizes, speeds, ETAs as text
├── selection.py     # "1,3,5-10" parser
├── static/          # index.html, style.css, app.js
├── tests/
├── requirements.txt
└── .env.example
```

## Requirements

- Python 3.10 or newer
- FFmpeg on your `PATH` (needed for MP3 conversion and merging video + audio)

## Setup

```bash
python -m venv .venv
# Windows PowerShell:  .\.venv\Scripts\Activate.ps1
# Linux / macOS:       source .venv/bin/activate
pip install -r requirements.txt
```

Install FFmpeg and check it with `ffmpeg -version`
(Ubuntu/Debian: `sudo apt install -y ffmpeg`).

Optionally copy the settings template (every value has a default, so this is only needed if you want to change something):

```bash
cp .env.example .env        # Windows: Copy-Item .env.example .env
```

## Packaging as a Windows app (no Python needed on the other PC)

1. Copy the real `ffmpeg.exe`, `ffprobe.exe` and `node.exe` into `bin/` (see `bin/README.txt`).
2. Double-click `build.bat` (needs Python 3.10+ on the build machine only).
3. Copy the whole `dist\MediaDownloader` folder to any Windows PC and run `MediaDownloader.exe`.

`MediaDownloader.exe` opens in its own window with no console, and closing the window quits the app (a log is written to
`%APPDATA%\MediaDownloader\app.log`). `MediaDownloader-console.exe` is a troubleshooting copy with a console that opens your browser.
The packaged app saves settings in `%APPDATA%\MediaDownloader\` and downloads to
`Downloads\MediaDownloader` until you change it in Settings. A `.env` is not bundled; put one next to the exe if you want one.
Rebuild now and then, because yt-dlp needs updating when sites change.

## Run

```bash
python main.py
```

Open <http://127.0.0.1:8000> in your browser.

## Terminal mode (no browser)

```bash
python cli.py "https://youtu.be/VIDEO_ID"                 # MP3 at 192 kbps -> your download folder
python cli.py URL -f mp4 -q 1080 --subs en                 # MP4, up to 1080p, English subtitles
python cli.py PLAYLIST_URL --list                          # show the videos, download nothing
python cli.py PLAYLIST_URL -s 1,3,5-10                    # pick some
python cli.py URL -o /content/drive/MyDrive/music          # choose the output folder
```

While it runs you get one live line per item: percentage, bytes done / total, speed and ETA. When an item finishes it prints the saved file size and the average speed, and a summary line closes the run.

| Option | Meaning |
| --- | --- |
| `-f mp3\|mp4` | Format (default `mp3`). |
| `-q` | MP3: 128 / 192 / 320 kbps. MP4: maximum height such as 720. |
| `-s SEL` | Playlist items: `all` or numbers/ranges like `1,3,5-10`. Without it, a real terminal asks; otherwise everything is downloaded. |
| `-o DIR` | Download folder (default: the one saved from the web UI, else `DOWNLOAD_DIR`). Files go in `CHANNEL/SECTION/music/` or `.../video/`, named like `Title [id][480p].mp4`, exactly like the UI; playlists also get their `.m3u8`. |
| `--again` | Download even if the same format and quality are already in the library (otherwise they are skipped, and other versions are mentioned). |
| `--subs LANG` / `--auto-subs` | MP4: also save subtitles in that language as `.srt` (`--auto-subs` for auto-generated ones). |
| `--zip` | Bundle a playlist into one ZIP instead of keeping the separate files. |
| `--list` | Only list what is at the URL. |
| `--plain` | A progress line every few seconds instead of one redrawn line (use for log files). |
| `--proxy`, `--cookies-file`, `--cookies-from-browser` | Override `PROXY`, `COOKIE_FILE`, `COOKIES_FROM_BROWSER` from `.env`. `none` turns one off. |

Channel URLs list their sections/playlists; pick one interactively or re-run with one of the printed URLs. Press Ctrl+C to stop; partial downloads stay in `DOWNLOAD_FOLDER/.partial` and a re-run resumes them. The exit code is 0 when everything downloaded, 1 if anything failed.


## How to use the web UI

1. Paste a URL and press **Look up**.
2. If you see a list of playlists or sections (channel URLs), click one to open it; use **Back** to return.
3. For a list of videos, choose the ones you want (checkboxes, **Select all**, or the number box). Badges show what you already have.
4. Choose **MP3** or **MP4**, then quality (and subtitles for MP4). The line under the options shows exactly which folder the files will go to.
5. Press **Download**. Files are saved as they finish; nothing to click afterwards.

## Settings

Open **Settings** in the page to change the download folder, the proxy, the number of simultaneous downloads and how long finished jobs stay listed. Values saved there win over `.env`; delete `user_settings.json` to go back to the `.env` values.

## Defaults (`.env`)

These are the starting values; the Settings button overrides them.

| Variable | Default | Meaning |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Address the UI listens on. |
| `PORT` | `8000` | Port the UI listens on. |
| `PROXY` | `None` | Proxy for all downloads: `None`, a port like `10808` (SOCKS5 on 127.0.0.1), `host:port`, or a full URL like `http://127.0.0.1:10809`. |
| `COOKIES_FROM_BROWSER` | empty | Read YouTube login cookies from this browser (`firefox`, `chrome`, `edge`…). |
| `COOKIE_FILE` | empty | Path to your own exported cookies file. |
| `MAX_PLAYLIST_ITEMS` | `0` | Cap playlist length; `0` means no cap. |
| `DOWNLOAD_DIR` | `downloads` | The download folder (your library). |
| `JOB_TTL_MINUTES` | `60` | How long a finished job stays in the list on the page. Files are never deleted. |
| `MAX_CONCURRENT_JOBS` | `1` | How many jobs download at once. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |

## Security notes

- The UI has **no login**, and anyone who can open it can change the download folder and proxy. Keep `HOST=127.0.0.1` so only your own computer can reach it. If you set `0.0.0.0` (for example to use it from your phone), anyone on that network can use it, so put it behind a reverse proxy with authentication.
- Never commit `.env` or cookie files; `.gitignore` excludes them.
- Treat cookie files like passwords.

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests mock yt-dlp, so they run without network access or FFmpeg.

## Troubleshooting

- **`ModuleNotFoundError`**: activate the virtual environment and run `pip install -r requirements.txt`.
- **MP3 conversion or MP4 merging fails**: check `ffmpeg -version`.
- **"Requested format is not available" on YouTube**: install [Deno](https://deno.com) (or Node.js 20+), run `python -m pip install -U "yt-dlp[default]"`, and restart. The program enables Deno, Node and Bun automatically when they are on your PATH.
- **Region**: the line under the title shows the country websites see for your connection. When `PROXY` is set it is the proxy's exit country, which is what YouTube uses for region locks.
- **A site rejects the request**: update yt-dlp with `python -m pip install --upgrade yt-dlp`. Some sites need cookies or block automated access.
- **"Can't reach the downloader"** in the page: `python main.py` has stopped; start it again.
- **A job disappears after a restart**: the job *list* is kept in memory only. Your downloaded files stay in the download folder and are still recognised (badges, Library). Unfinished downloads wait in `.partial` inside it and resume if you start them again.
- **The badge doesn't show for something I downloaded**: items are recognised by the `[id][quality]` at the end of the file name. Don't rename that part; moving the file to another folder or renaming the title is fine. Files saved by an earlier version in `video/480p/` sub-folders are still recognised.

## Limitations

- Downloads run one track after another within a job; there is no durable queue or automatic retry.
- Subtitle languages are checked on the first selected video; a video without that language simply gets no subtitle.
- The `[720p]` in the file name is the maximum you asked for; if a video only has 480p the file is still tagged `[720p]`.
- Progress is per track. For video it combines the video and audio streams into one figure; sizes are exact only when the site reports them, otherwise estimates.
- The MP4 resolution list comes from the first selected video; other tracks may have fewer options, in which case yt-dlp picks the best available at or below your choice.

## Privacy

The app runs on your computer and has no accounts or analytics. The only requests it makes are to the sites you download from and, for the region line under the title, to `ipwho.is` / `ipinfo.io` to see which country your connection appears from.

## License

MIT, see [LICENSE](LICENSE). Third-party components (yt-dlp, FFmpeg, Node.js, Flask and others) keep their own licenses, see [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt).
