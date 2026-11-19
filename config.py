"""Configuration loaded from environment variables or a local .env file."""

from dataclasses import dataclass, fields, replace
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

import paths

paths.add_bin_to_path()
load_dotenv(paths.APP_DIR / ".env")


def _integer(name: str, default: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _proxy(raw: str) -> str:
    """Normalise the PROXY setting. Returns "" when no proxy should be used.

    Accepts: None / empty (no proxy), a bare port like 10808 or host:port
    (treated as SOCKS5), or a full URL such as http://127.0.0.1:10809.
    """
    value = raw.strip()
    if value.lower() in ("", "none", "off", "false", "no", "0"):
        return ""
    if value.isdigit():
        return f"socks5h://127.0.0.1:{value}"
    if "://" not in value:
        return f"socks5h://{value}"
    scheme = value.split("://", 1)[0].lower()
    if scheme == "socks5":  # let the proxy resolve DNS too
        value = "socks5h://" + value.split("://", 1)[1]
    return value


def _browser_cookies(raw: str) -> tuple:
    """Parse COOKIES_FROM_BROWSER into the tuple yt-dlp expects.

    Same syntax as yt-dlp's --cookies-from-browser:
        BROWSER[+KEYRING][:PROFILE][::CONTAINER]
    e.g. "firefox", "firefox:abc123.default-release", "chrome:Profile 1".
    Returns () when unset.
    """
    value = raw.strip()
    if value.lower() in ("", "none", "off", "false", "no", "0"):
        return ()
    match = re.fullmatch(
        r"(?P<browser>[^+:]+)(?:\s*\+\s*(?P<keyring>[^:]+))?"
        r"(?:\s*:\s*(?P<profile>.+?))?(?:\s*::\s*(?P<container>.+))?",
        value,
    )
    if not match:
        raise ValueError(f"COOKIES_FROM_BROWSER is not valid: {raw!r}")
    browser, keyring, profile, container = match.group(
        "browser", "keyring", "profile", "container"
    )
    return (browser.strip().lower(), profile, keyring, container)


def cookies_text(value: tuple) -> str:
    """Inverse of _browser_cookies: turn the tuple back into BROWSER[+KEYRING][:PROFILE][::CONTAINER]."""
    if not value:
        return ""
    browser, profile, keyring, container = value
    text = browser
    if keyring:
        text += f"+{keyring}"
    if profile:
        text += f":{profile}"
    if container:
        text += f"::{container}"
    return text


@dataclass
class Settings:
    """Settings. Values saved from the web page (user_settings.json) win over .env."""

    host: str = os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"
    port: int = _integer("PORT", 8000)
    cookie_file: str = os.getenv("COOKIE_FILE", "").strip()
    cookies_from_browser: tuple = _browser_cookies(os.getenv("COOKIES_FROM_BROWSER", ""))
    proxy: str = _proxy(os.getenv("PROXY", "None"))
    max_playlist_items: int = _integer("MAX_PLAYLIST_ITEMS", 0)
    download_dir: str = os.getenv("DOWNLOAD_DIR", "").strip() or paths.DEFAULT_DOWNLOAD_DIR
    job_ttl_minutes: int = _integer("JOB_TTL_MINUTES", 60)
    max_concurrent_jobs: int = _integer("MAX_CONCURRENT_JOBS", 1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip()

    def validate(self) -> None:
        if self.max_playlist_items < 0:
            raise RuntimeError("MAX_PLAYLIST_ITEMS must be 0 or greater.")
        if self.job_ttl_minutes < 1:
            raise RuntimeError("JOB_TTL_MINUTES must be 1 or greater.")
        if self.max_concurrent_jobs < 1:
            raise RuntimeError("MAX_CONCURRENT_JOBS must be 1 or greater.")
        if not 1 <= self.port <= 65535:
            raise RuntimeError("PORT must be between 1 and 65535.")
        if self.proxy and self.proxy.split("://", 1)[0].lower() not in (
            "http", "https", "socks4", "socks4a", "socks5", "socks5h",
        ):
            raise RuntimeError(
                "PROXY must be None, a port (10808), host:port, or a URL "
                "starting with http://, https://, socks4://, or socks5://."
            )
        if self.cookie_file and not os.path.isfile(self.cookie_file):
            raise RuntimeError(
                f"COOKIE_FILE was set but the file does not exist: {self.cookie_file}"
            )


def _supported_browsers() -> set:
    try:
        from yt_dlp.cookies import SUPPORTED_BROWSERS
        return set(SUPPORTED_BROWSERS)
    except Exception:  # pragma: no cover - only if yt-dlp changes its internals
        return {"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}


# Settings the web page may change, and the file they are saved in.
EDITABLE = ("download_dir", "proxy", "cookies_from_browser", "cookie_file",
            "max_concurrent_jobs", "job_ttl_minutes")


def settings_file() -> Path:
    return Path(os.getenv("SETTINGS_FILE") or paths.DATA_DIR / "user_settings.json")


def load_saved(current: Settings) -> Settings:
    """Apply user_settings.json on top of the .env values (ignored if unreadable)."""
    try:
        saved = json.loads(settings_file().read_text(encoding="utf-8"))
        changes = {key: saved[key] for key in EDITABLE if key in saved}
        if "cookies_from_browser" in changes:
            changes["cookies_from_browser"] = _browser_cookies(str(changes["cookies_from_browser"]))
        candidate = replace(current, **changes)
        candidate.validate()
        return candidate
    except (OSError, ValueError, TypeError, RuntimeError):
        return current


def save_user_settings(current: Settings) -> None:
    data = {key: getattr(current, key) for key in EDITABLE}
    data["cookies_from_browser"] = cookies_text(current.cookies_from_browser)
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def apply(changes: dict) -> None:
    """Validate and apply changes to the live settings object, then save them.

    Raises ValueError/RuntimeError (and changes nothing) if a value is invalid.
    """
    clean = {}
    if "download_dir" in changes:
        folder = str(changes["download_dir"]).strip()
        if not folder:
            raise ValueError("Download folder cannot be empty.")
        path = Path(folder).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            raise ValueError(f"Can't write to that folder: {exc}") from exc
        clean["download_dir"] = str(path)
    if "proxy" in changes:
        clean["proxy"] = _proxy(str(changes["proxy"]))
    if "cookies_from_browser" in changes:
        parsed = _browser_cookies(str(changes["cookies_from_browser"]))
        if parsed and parsed[0] not in _supported_browsers():
            raise ValueError(
                f"Unknown browser '{parsed[0]}'. Use one of: "
                + ", ".join(sorted(_supported_browsers())) + "."
            )
        clean["cookies_from_browser"] = parsed
    if "cookie_file" in changes:
        clean["cookie_file"] = str(changes["cookie_file"]).strip().strip('"').strip()
    for key in ("max_concurrent_jobs", "job_ttl_minutes"):
        if key in changes:
            try:
                clean[key] = int(str(changes[key]).strip())
            except ValueError as exc:
                raise ValueError(f"{key} must be a whole number.") from exc
    candidate = replace(settings, **clean)
    candidate.validate()
    for name, value in clean.items():
        setattr(settings, name, value)
    save_user_settings(settings)


settings = load_saved(Settings())
