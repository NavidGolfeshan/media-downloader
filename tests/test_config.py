import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import _browser_cookies, _proxy, cookies_text  # noqa: E402


class ProxyTests(unittest.TestCase):
    def test_disabled(self):
        for value in ("", "None", "none", " off ", "0"):
            self.assertEqual(_proxy(value), "")

    def test_bare_port_is_socks5(self):
        self.assertEqual(_proxy("10808"), "socks5h://127.0.0.1:10808")

    def test_host_port(self):
        self.assertEqual(_proxy("192.168.1.5:1080"), "socks5h://192.168.1.5:1080")

    def test_full_urls(self):
        self.assertEqual(_proxy("http://127.0.0.1:10809"), "http://127.0.0.1:10809")
        self.assertEqual(_proxy("socks5://127.0.0.1:10808"), "socks5h://127.0.0.1:10808")


class BrowserCookieTests(unittest.TestCase):
    def test_unset(self):
        self.assertEqual(_browser_cookies(""), ())
        self.assertEqual(_browser_cookies("none"), ())

    def test_plain_browser(self):
        self.assertEqual(_browser_cookies(" Firefox "), ("firefox", None, None, None))

    def test_profile_keeps_case(self):
        self.assertEqual(
            _browser_cookies("firefox:Abc123.default-release"),
            ("firefox", "Abc123.default-release", None, None),
        )

    def test_windows_profile_path(self):
        self.assertEqual(
            _browser_cookies(r"firefox:C:\Users\Me\AppData\Roaming\Mozilla\Firefox\Profiles\x.default"),
            ("firefox", r"C:\Users\Me\AppData\Roaming\Mozilla\Firefox\Profiles\x.default", None, None),
        )


if __name__ == "__main__":
    unittest.main()


class CookiesTextTests(unittest.TestCase):
    def test_round_trip(self):
        for text in ("firefox", "firefox:abc.default", "chrome+gnomekeyring:Profile 1", "firefox:p::Work"):
            self.assertEqual(cookies_text(_browser_cookies(text)), text)
        self.assertEqual(cookies_text(()), "")
