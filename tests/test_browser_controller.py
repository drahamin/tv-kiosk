import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "browser-controller.py"
spec = importlib.util.spec_from_file_location("browser_controller", MODULE_PATH)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class BrowserControllerTests(unittest.TestCase):
    def test_remote_signals_select_previous_and_next_pages(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("signal.SIGUSR1, request_page(1)", source)
        self.assertIn("signal.SIGUSR2, request_page(-1)", source)
        self.assertIn("Remote selected page", source)

    def test_chromium_launch_forces_fullscreen(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller, "display_size", return_value=(1920, 1080)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            self.assertIs(controller.launch_chromium(125), fake)
        command = popen.call_args.args[0]
        self.assertIn("--kiosk", command)
        self.assertIn("--start-fullscreen", command)
        self.assertIn("--start-maximized", command)
        self.assertIn("--window-position=0,0", command)
        self.assertIn("--window-size=1920,1080", command)
        self.assertIn("--force-device-scale-factor=1.25", command)
        self.assertIn("--autoplay-policy=no-user-gesture-required", command)
        self.assertIn("--disk-cache-size=268435456", command)
        self.assertIn("--renderer-process-limit=3", command)
        self.assertNotIn("--incognito", command)
        self.assertTrue(command[-1].endswith("/session/boot.html?profile=multi"))
        self.assertNotIn("--mute-audio", command)

    def test_chromium_can_disable_audio_without_extra_processes(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller, "display_size", return_value=(1920, 1080)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            controller.launch_chromium(100, False)
        self.assertIn("--mute-audio", popen.call_args.args[0])

    def test_pi_zero_uses_one_renderer_and_smaller_cache(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller, "display_size", return_value=(1920, 1080)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            controller.launch_chromium(100, True, "zero")
        command = popen.call_args.args[0]
        self.assertIn("--renderer-process-limit=1", command)
        self.assertIn("--disk-cache-size=134217728", command)
        self.assertIn("--enable-low-end-device-mode", command)
        self.assertIn("--process-per-site", command)
        self.assertIn("--js-flags=--max-old-space-size=192", command)
        self.assertTrue(command[-1].endswith("/session/boot.html?profile=zero"))

    def test_pi_zero_uses_armv6_compatible_fullscreen_cog(self):
        fake = MagicMock()
        with patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            self.assertIs(controller.launch_cog("https://cloud.example/tv"), fake)
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(command, [
            "cog",
            "--platform=wl",
            "--enable-page-cache=false",
            "--enable-offline-web-application-cache=true",
            "--enable-smooth-scrolling=false",
            "https://cloud.example/tv",
        ])
        self.assertEqual(environment["COG_PLATFORM_WL_VIEW_FULLSCREEN"], "1")

    def test_load_config_filters_disabled_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kiosk.json"
            path.write_text(json.dumps({"rotation_seconds": 25, "pages": [
                {"name": "One", "url": "http://one.test", "enabled": True},
                {"name": "Two", "url": "http://two.test", "enabled": False},
                {"name": "Three", "url": "http://three.test", "enabled": True},
            ]}), encoding="utf-8")
            with patch.object(controller, "CONFIG_PATH", path):
                config = controller.load_config()
        self.assertEqual([page["name"] for page in config["pages"]], ["One", "Three"])
        self.assertEqual(config["zoom_percent"], 100)
        self.assertTrue(config["audio_enabled"])
        self.assertEqual(config["audio_volume"], 60)

    def test_pi_zero_loads_only_first_enabled_page(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kiosk.json"
            path.write_text(json.dumps({"setup_complete": True, "pages": [
                {"name": "Baiamonte", "url": "https://cloud.example/tv", "enabled": True},
                {"name": "Heavy map", "url": "https://map.example/tv", "enabled": True},
            ]}), encoding="utf-8")
            with patch.object(controller, "CONFIG_PATH", path), patch.object(controller, "HARDWARE_PROFILE", "zero"):
                config = controller.load_config()
        self.assertEqual(config["pages"], [{"name": "Baiamonte", "url": "https://cloud.example/tv"}])

    def test_display_size_uses_current_hdmi_mode(self):
        result = MagicMock(stdout="HDMI-A-1\n  3840x2160 px, 60.000000 Hz (current)\n")
        with patch.object(controller.subprocess, "run", return_value=result):
            self.assertEqual(controller.display_size(), (3840, 2160))

    def test_devtools_accepts_plain_text_activation_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"Target activated"
        with patch.object(controller, "urlopen", return_value=response):
            self.assertEqual(controller.devtools("/json/activate/example"), "Target activated")

    def test_replace_tab_keeps_only_one_full_page_loaded(self):
        targets = [
            {"id": "new", "type": "page"},
            {"id": "blank", "type": "page"},
            {"id": "worker", "type": "service_worker"},
        ]
        with (
            patch.object(controller, "open_tab", return_value={"id": "new"}) as opened,
            patch.object(controller, "activate") as activated,
            patch.object(controller, "close") as closed,
            patch.object(controller, "devtools", return_value=targets),
        ):
            result = controller.replace_tab("http://example.test/tv", "old")

        self.assertEqual(result, "new")
        opened.assert_called_once_with("http://example.test/tv")
        activated.assert_called_once_with("new")
        self.assertEqual([call.args[0] for call in closed.call_args_list], ["old", "blank"])


if __name__ == "__main__":
    unittest.main()
