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
        self.assertIn("--disable-background-timer-throttling", command)
        self.assertIn("--disable-backgrounding-occluded-windows", command)
        self.assertIn("--disable-renderer-backgrounding", command)
        self.assertIn("--disk-cache-size=268435456", command)
        self.assertIn("--renderer-process-limit=3", command)
        self.assertNotIn("--incognito", command)
        self.assertIn("--app=file://", command[-1])
        self.assertTrue(command[-1].endswith("/session/boot.html?profile=multi&variant=auto"))
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
        self.assertIn("--app=file://", command[-1])
        self.assertTrue(command[-1].endswith("/session/boot.html?profile=zero&variant=auto"))

    def test_baiamonte_pi_three_uses_constrained_chromium(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller, "KIOSK_VARIANT", "baiamonte"), patch.object(controller, "hardware_model", return_value="Raspberry Pi 3 Model B Rev 1.2"), patch.object(controller, "display_size", return_value=(1920, 1080)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            controller.launch_chromium(100, True, hardware_profile="multi")
        command = popen.call_args.args[0]
        self.assertIn("--enable-low-end-device-mode", command)
        self.assertIn("--disable-smooth-scrolling", command)
        self.assertIn("--process-per-site", command)
        self.assertIn("--renderer-process-limit=2", command)
        self.assertIn("--disk-cache-size=134217728", command)
        self.assertIn("--js-flags=--max-old-space-size=256", command)

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

    def test_pi_four_dual_hdmi_uses_isolated_baiamonte_app(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller, "display_size", return_value=(1920, 1080)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            controller.launch_chromium(110, False, role="secondary", url="http://192.168.0.10:8101", output_name="HDMI-A-2", output_x=1920, dual=True)
        command = popen.call_args.args[0]
        self.assertIn("--class=BaiamonteSecondary", command)
        self.assertIn("--remote-debugging-port=9223", command)
        self.assertIn("--app=http://192.168.0.10:8101", command)
        self.assertIn("--ozone-platform=x11", command)
        self.assertIn("--window-position=1920,0", command)
        self.assertIn("--mute-audio", command)
        self.assertNotIn("--start-maximized", command)
        self.assertTrue(any("chromium-profile-secondary" in item for item in command))
        self.assertNotIn("--kiosk", command)

    def test_second_hdmi_is_limited_to_pi_four_and_five(self):
        with patch.dict(controller.os.environ, {"KIOSK_HARDWARE_MODEL": "Raspberry Pi 4 Model B Rev 1.5"}):
            self.assertTrue(controller.dual_hdmi_capable())
        with patch.dict(controller.os.environ, {"KIOSK_HARDWARE_MODEL": "Raspberry Pi 5 Model B Rev 1.0"}):
            self.assertTrue(controller.dual_hdmi_capable())
        with patch.dict(controller.os.environ, {"KIOSK_HARDWARE_MODEL": "Raspberry Pi 3 Model B Rev 1.2"}):
            self.assertFalse(controller.dual_hdmi_capable())

    def test_dual_output_parser_keeps_both_hdmi_connectors(self):
        result = MagicMock(stdout=(
            "HDMI-A-1\n  1920x1080 px, 60.000000 Hz (current, preferred)\n  Position: 0,0\n"
            "HDMI-A-2\n  3840x2160 px, 30.000000 Hz (current, preferred)\n  Position: 1920,0\n"
        ))
        with patch.object(controller.subprocess, "run", return_value=result):
            self.assertEqual(controller.display_outputs(), [
                {"name": "HDMI-A-1", "width": 1920, "height": 1080},
                {"name": "HDMI-A-2", "width": 3840, "height": 2160},
            ])

    def test_dual_window_placement_matches_browser_processes_and_covers_outputs(self):
        outputs = [
            {"name": "HDMI-A-1", "width": 1920, "height": 1080},
            {"name": "HDMI-A-2", "width": 3840, "height": 2160},
        ]
        primary = MagicMock(pid=1111)
        secondary = MagicMock(pid=2222)
        searches = [MagicMock(stdout="101\n"), MagicMock(stdout="202\n")]
        with patch.object(controller.subprocess, "run", side_effect=searches + [MagicMock(), MagicMock(), MagicMock()]) as run:
            self.assertTrue(controller.place_dual_windows(outputs, primary, secondary))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(["xdotool", "search", "--onlyvisible", "--pid", "1111"], commands)
        self.assertIn(["xdotool", "search", "--onlyvisible", "--pid", "2222"], commands)
        self.assertTrue(any(command[:5] == ["xdotool", "windowmove", "--sync", "202", "1920"] for command in commands))
        self.assertFalse(any("windowstate" in command for command in commands))

    def test_dual_geometry_is_reasserted_after_page_navigation(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("next_geometry_check", source)
        self.assertIn("place_dual_windows(outputs, process, secondary_process)", source)

    def test_devtools_accepts_plain_text_activation_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"Target activated"
        with patch.object(controller, "urlopen", return_value=response):
            self.assertEqual(controller.devtools("/json/activate/example"), "Target activated")

    def test_page_reachability_retries_network_failures_but_accepts_http_responses(self):
        response = MagicMock()
        response.__enter__.return_value = response
        with patch.object(controller, "urlopen", return_value=response):
            self.assertTrue(controller.page_reachable("http://example.test/tv"))
        with patch.object(controller, "urlopen", side_effect=controller.HTTPError("http://example.test/tv", 401, "Unauthorized", {}, None)):
            self.assertTrue(controller.page_reachable("http://example.test/tv"))
        with patch.object(controller, "urlopen", side_effect=controller.URLError("offline")):
            self.assertFalse(controller.page_reachable("http://example.test/tv"))

    def test_controller_keeps_boot_screen_and_retries_single_page_sites(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("wait_for_page(config[\"pages\"][0][\"url\"], process)", source)
        self.assertIn("Kiosk page connection restored; reloaded automatically", source)
        self.assertIn("Baiamonte second display connection restored; reloaded automatically", source)

    def test_chromium_startup_allows_slow_dual_4k_profiles(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("range(240)", source)
        self.assertIn("wait_for_chromium(secondary_process, DEBUG_PORT + 1)", source)

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
        opened.assert_called_once_with("http://example.test/tv", port=controller.DEBUG_PORT)
        activated.assert_called_once_with("new", port=controller.DEBUG_PORT)
        self.assertEqual([call.args[0] for call in closed.call_args_list], ["old", "blank"])
        self.assertTrue(all(call.kwargs == {"port": controller.DEBUG_PORT} for call in closed.call_args_list))


if __name__ == "__main__":
    unittest.main()
