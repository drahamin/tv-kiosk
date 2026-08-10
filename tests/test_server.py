import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"


class KioskServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = {
            "title": "Test Kiosk",
            "listen_port": 8999,
            "rotation_seconds": 25,
            "transition_seconds": 0.7,
            "show_status": True,
            "background": "#080706",
            "theme": "baiamonte",
            "pages": [
                {"name": "One", "url": "http://example.test/one"},
                {"name": "Two", "url": "http://example.test/two"},
                {"name": "Board", "url": "http://example.test/board"},
            ],
        }
        config_path = Path(self.tmp.name) / "kiosk.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        os.environ["KIOSK_CONFIG"] = str(config_path)
        os.environ["KIOSK_CREDENTIALS"] = str(Path(self.tmp.name) / "admin.json")
        spec = importlib.util.spec_from_file_location("kiosk_server", MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.config = self.module.load_config()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("KIOSK_CONFIG", None)
        os.environ.pop("KIOSK_CREDENTIALS", None)

    def get(self, path):
        status, _content_type, body = self.module.render_path(path, self.config)
        return status, body

    def test_rotation_page_has_all_three_urls_and_interval(self):
        status, body = self.get("/tv")
        self.assertEqual(status, 200)
        self.assertIn("example.test/one", body)
        self.assertIn("example.test/two", body)
        self.assertIn("example.test/board", body)
        self.assertIn('"rotation_seconds":25', body)

    def test_airport_aliases_show_only_board(self):
        for path in ("/airport-tv", "/tv/airport"):
            status, body = self.get(path)
            self.assertEqual(status, 200)
            self.assertIn("example.test/board", body)
            self.assertNotIn("example.test/one", body)

    def test_health(self):
        status, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_default_admin_login_and_change(self):
        self.assertTrue(self.module.check_credentials("admin", "admin"))
        self.module.change_credentials("admin", "captain", "new-secret", "new-secret")
        self.assertFalse(self.module.check_credentials("admin", "admin"))
        self.assertTrue(self.module.check_credentials("captain", "new-secret"))

    def test_admin_page_uses_baiamonte_theme_and_all_settings(self):
        _token, csrf = self.module.new_session("admin")
        body = self.module.admin_page(self.config, {"username": "admin", "csrf": csrf})
        self.assertIn("BAIAMONTE", body)
        self.assertIn("Rotation time", body)
        self.assertIn("Web port", body)
        self.assertIn("Change administrator login", body)

    def test_rejects_non_http_page_url(self):
        broken = dict(self.config)
        broken["pages"] = [dict(page) for page in self.config["pages"]]
        broken["pages"][0]["url"] = "file:///etc/passwd"
        with self.assertRaises(ValueError):
            self.module.validate_config(broken)


if __name__ == "__main__":
    unittest.main()
