import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "server.py"


class KioskServerTests(unittest.TestCase):
    def test_web_service_keeps_privilege_isolation_for_admin_helpers(self):
        service = (MODULE_PATH.parents[1] / "systemd" / "tv-kiosk-web.service").read_text()
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("PrivateTmp=true", service)

        source = MODULE_PATH.read_text()
        self.assertIn("atomic_text_write(ACTION_REQUEST_PATH", source)
        self.assertNotIn('subprocess.run(["sudo", "-n", str(helper), action]', source)
        self.assertNotIn('subprocess.run(["sudo", "-n", "/usr/local/sbin/rahamin-kiosk-network"', source)

    def test_default_playlist_contains_both_weather_pages(self):
        default = json.loads((MODULE_PATH.parents[1] / "config" / "kiosk.json").read_text(encoding="utf-8"))
        weather = {page["name"]: page for page in default["pages"][3:]}
        self.assertEqual(weather["Miami Weather"]["url"], "http://192.168.86.196:8999/miami")
        self.assertEqual(weather["Sicily Weather"]["url"], "http://192.168.86.196:8999/sicily")
        self.assertTrue(all(page["enabled"] for page in weather.values()))
        self.assertEqual(default["rotation_seconds"], 45)

    def test_pi_zero_default_contains_one_baiamonte_page(self):
        default = json.loads((MODULE_PATH.parents[1] / "config" / "kiosk-zero.json").read_text(encoding="utf-8"))
        self.assertEqual(len(default["pages"]), 1)
        self.assertEqual(default["pages"][0]["name"], "Baiamonte TV Dashboard")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = {
            "title": "Test Kiosk",
            "listen_port": 8999,
            "rotation_seconds": 25,
            "transition_seconds": 0.7,
            "zoom_percent": 100,
            "audio_enabled": True,
            "audio_volume": 60,
            "show_status": True,
            "background": "#080706",
            "theme": "rahamin",
            "pages": [
                {"name": "One", "url": "http://example.test/one", "enabled": True},
                {"name": "Two", "url": "http://example.test/two", "enabled": False},
                {"name": "Airport Board", "url": "http://example.test/board", "enabled": True},
                {"name": "Four", "url": "", "enabled": False},
                {"name": "Five", "url": "", "enabled": False},
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

    def test_rotation_page_includes_enabled_pages_only(self):
        status, body = self.get("/tv")
        self.assertEqual(status, 200)
        self.assertIn("example.test/one", body)
        self.assertNotIn("example.test/two", body)
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
        self.assertEqual(json.loads(body), {"status": "ok", "name": "Rahamin Kiosk"})

    def test_default_admin_login_and_change(self):
        self.assertTrue(self.module.check_credentials("admin", "admin"))
        self.module.change_credentials("admin", "captain", "new-secret", "new-secret")
        self.assertFalse(self.module.check_credentials("admin", "admin"))
        self.assertTrue(self.module.check_credentials("captain", "new-secret"))

    def test_admin_page_uses_rahamin_name_and_management_sections(self):
        _token, csrf = self.module.new_session("admin")
        body = self.module.admin_page(self.config, {"username": "admin", "csrf": csrf})
        self.assertIn("Rahamin Kiosk", body)
        self.assertIn("Rotation time", body)
        self.assertIn("Page zoom", body)
        self.assertIn("HDMI audio volume", body)
        self.assertIn("Enable audio through the TV", body)
        self.assertIn("Samsung remote (Anynet+)", body)
        self.assertIn("Fast-forward/Rewind zoom in/out", body)
        self.assertIn("Web port", body)
        self.assertIn("Up to five full-screen pages", body)
        self.assertIn("NETWORK CONFIGURATION", body)
        self.assertIn("Raspberry Pi health", body)
        self.assertIn("Force update now", body)
        self.assertIn("Start display", body)
        self.assertIn("Stop display", body)
        self.assertIn("Reboot Pi", body)
        self.assertIn("Change administrator login", body)

    def test_baiamonte_admin_documents_arrow_map_zoom(self):
        _token, csrf = self.module.new_session("admin")
        original = self.module.KIOSK_VARIANT
        try:
            self.module.KIOSK_VARIANT = "baiamonte"
            body = self.module.admin_page(self.config, {"username": "admin", "csrf": csrf})
        finally:
            self.module.KIOSK_VARIANT = original
        self.assertIn("Left/Right change pages", body)
        self.assertIn("Up/Down zoom only the active ADS-B, AIS, or weather map", body)
        self.assertIn("Play/Pause pauses or resumes rotation", body)
        self.assertIn("Volume and Channel rockers", body)

    def test_rejects_non_http_page_url(self):
        broken = dict(self.config)
        broken["pages"] = [dict(page) for page in self.config["pages"]]
        broken["pages"][0]["url"] = "file:///etc/passwd"
        with self.assertRaises(ValueError):
            self.module.validate_config(broken)

    def test_migrates_three_old_pages_to_five(self):
        old = {**self.config, "pages": self.config["pages"][:3]}
        for page in old["pages"]:
            page.pop("enabled", None)
        migrated = self.module.validate_config(old)
        self.assertEqual(len(migrated["pages"]), 5)
        self.assertTrue(all(page["enabled"] for page in migrated["pages"][:3]))
        self.assertFalse(any(page["enabled"] for page in migrated["pages"][3:]))

    def test_requires_at_least_one_enabled_page(self):
        broken = {**self.config, "pages": [{**page, "enabled": False} for page in self.config["pages"]]}
        with self.assertRaises(ValueError):
            self.module.validate_config(broken)

    def test_accepts_only_supported_zoom_levels(self):
        self.assertEqual(self.module.validate_config({**self.config, "zoom_percent": 125})["zoom_percent"], 125)
        with self.assertRaises(ValueError):
            self.module.validate_config({**self.config, "zoom_percent": 123})

    def test_validates_hdmi_audio_settings(self):
        configured = self.module.validate_config({**self.config, "audio_enabled": False, "audio_volume": 75})
        self.assertFalse(configured["audio_enabled"])
        self.assertEqual(configured["audio_volume"], 75)
        with self.assertRaises(ValueError):
            self.module.validate_config({**self.config, "audio_volume": 101})

    def test_validates_full_network_request(self):
        form = {
            "hostname": "rahamin-kiosk",
            "wifi_enabled": "on",
            "wifi_autoconnect": "on",
            "wifi_ssid": "Home",
            "wifi_password": "",
            "wifi_security": "wpa-psk",
            "wifi_mac_policy": "preserve",
            "ethernet_enabled": "on",
            "wifi_ipv4_mode": "auto",
            "wifi_ipv6_mode": "auto",
            "ethernet_ipv4_mode": "manual",
            "ethernet_ipv4_address": "192.168.86.50/24",
            "ethernet_ipv4_gateway": "192.168.86.1",
            "ethernet_ipv4_dns": "1.1.1.1",
            "ethernet_ipv6_mode": "disabled",
        }
        result = self.module.validate_network_request(form)
        self.assertEqual(result["ethernet_ipv4_address"], "192.168.86.50/24")
        self.assertTrue(result["wifi_enabled"])


if __name__ == "__main__":
    unittest.main()
