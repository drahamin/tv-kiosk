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
            "rotation_seconds": 25,
            "pages": [
                {"name": "One", "url": "http://example.test/one"},
                {"name": "Two", "url": "http://example.test/two"},
                {"name": "Board", "url": "http://example.test/board"},
            ],
        }
        config_path = Path(self.tmp.name) / "kiosk.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        os.environ["KIOSK_CONFIG"] = str(config_path)
        spec = importlib.util.spec_from_file_location("kiosk_server", MODULE_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.config = self.module.load_config()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("KIOSK_CONFIG", None)

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


if __name__ == "__main__":
    unittest.main()
