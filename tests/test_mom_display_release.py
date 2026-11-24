"""Regression checks for the isolated Mom display profile."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mom_browser_controller", ROOT / "scripts/browser-controller.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class MomDisplayReleaseTests(unittest.TestCase):
    a1 = {"name": "HDMI-A-1", "width": 3840, "height": 2160}
    a2 = {"name": "HDMI-A-2", "width": 3840, "height": 2160}

    def test_identical_real_edid_on_two_inputs_counts_as_two_but_forced_edid_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            for index in (1, 2):
                path = p / f"card1-HDMI-A-{index}"
                path.mkdir()
                (path / "status").write_text("connected\n")
            edid = bytes.fromhex("00ffffffffffff00") + bytes(119)
            edid += bytes([-sum(edid) % 256])
            (p / "card1-HDMI-A-1/edid").write_bytes(b"")
            (p / "card1-HDMI-A-2/edid").write_bytes(edid)
            with patch.object(controller, "DRM_CONNECTORS", p):
                self.assertEqual(controller.mom_physical_hdmi(), {"HDMI-A-2"})
                (p / "card1-HDMI-A-1/edid").write_bytes(edid)
                self.assertEqual(controller.mom_physical_hdmi(), {"HDMI-A-1", "HDMI-A-2"})

    def test_forced_output_cannot_displace_the_only_real_panel(self):
        with (
            patch.object(controller, "dual_hdmi_capable", return_value=True),
            patch.object(controller, "mom_physical_hdmi", return_value={"HDMI-A-2"}),
            patch.object(controller, "display_outputs", return_value=[self.a1, self.a2]),
            patch.object(controller, "mom_xwayland_ready", return_value=True) as x11,
            patch.object(controller.subprocess, "run", return_value=MagicMock(returncode=0)) as command,
        ):
            self.assertEqual(controller.mom_display_plan({"secondary_display_enabled": True}), ("HDMI-A-2", []))
        x11.assert_not_called()
        self.assertEqual(command.call_args.args[0], [
            "wlr-randr", "--output", "HDMI-A-2", "--on", "--pos", "0,0", "--output", "HDMI-A-1", "--off",
        ])

    def test_both_real_panels_and_usable_x11_follow_exact_miami_layout(self):
        with (
            patch.object(controller, "dual_hdmi_capable", return_value=True),
            patch.object(controller, "mom_physical_hdmi", return_value={"HDMI-A-1", "HDMI-A-2"}),
            patch.object(controller, "display_outputs", return_value=[self.a2, self.a1]),
            patch.object(controller, "mom_xwayland_ready", return_value=True),
            patch.object(controller.subprocess, "run", return_value=MagicMock(returncode=0)) as command,
        ):
            self.assertEqual(controller.mom_display_plan({"secondary_display_enabled": True}), ("HDMI-A-1", [self.a1, self.a2]))
        self.assertEqual(command.call_args.args[0], [
            "wlr-randr", "--output", "HDMI-A-1", "--on", "--pos", "0,0",
            "--output", "HDMI-A-2", "--on", "--pos", "3840,0",
        ])

    def test_no_display_env_uses_fallback_without_probing_unknown_xserver(self):
        with patch.dict(controller.os.environ, {}, clear=True), patch.object(controller.subprocess, "run") as command:
            self.assertFalse(controller.mom_xwayland_ready())
        command.assert_not_called()

    def test_layout_rejection_falls_back_to_single_screen(self):
        with (
            patch.object(controller, "dual_hdmi_capable", return_value=True),
            patch.object(controller, "mom_physical_hdmi", return_value={"HDMI-A-1", "HDMI-A-2"}),
            patch.object(controller, "display_outputs", return_value=[self.a1, self.a2]),
            patch.object(controller, "mom_xwayland_ready", return_value=True),
            patch.object(controller.subprocess, "run", side_effect=[MagicMock(returncode=1), MagicMock(returncode=0)]) as command,
        ):
            self.assertEqual(controller.mom_display_plan({"secondary_display_enabled": True}), ("HDMI-A-1", []))
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args.args[0][-3:], ["--output", "HDMI-A-2", "--off"])

    def test_secondary_failure_at_entry_point_loads_real_miami_page_in_native_wayland(self):
        conf = json.loads((ROOT / "config/kiosk-mom.json").read_text())
        secondary = MagicMock()
        secondary.poll.return_value = 1
        primary = MagicMock()
        primary.poll.return_value = None
        def show(*args, **kwargs):
            controller.running = False
            return "primary"
        with (
            patch.object(controller, "KIOSK_VARIANT", "mom"),
            patch.object(controller, "running", True),
            patch.object(controller, "load_config", return_value=conf),
            patch.object(controller, "configure_audio"),
            patch.object(controller, "mom_display_plan", side_effect=[("HDMI-A-1", [self.a1, self.a2]), ("HDMI-A-1", [])]) as plan,
            patch.object(controller, "mom_secondary_reachable", return_value=False),
            patch.object(controller, "launch_chromium", side_effect=[secondary, primary]) as launch,
            patch.object(controller, "wait_for_chromium", side_effect=[RuntimeError("secondary failed"), None]),
            patch.object(controller, "enforce_fullscreen"),
            patch.object(controller, "time") as mock_time,
            patch.object(controller, "show_page", side_effect=show) as show_page,
        ):
            mock_time.monotonic.return_value = 10
            controller.supervise()
        self.assertEqual([call.kwargs["dual"] for call in launch.call_args_list], [True, False])
        self.assertFalse(plan.call_args.kwargs["allow_dual"])
        self.assertEqual(show_page.call_args.args[0], conf["pages"][0]["url"])
        self.assertFalse(show_page.call_args.kwargs["preserve_app_window"])

    def test_403_and_cross_origin_access_login_are_not_reported_healthy(self):
        url = "http://127.0.0.1:18101/"
        with patch.object(controller, "urlopen", side_effect=HTTPError(url, 403, "Forbidden", None, None)):
            self.assertFalse(controller.mom_secondary_reachable(url))
        result = MagicMock()
        result.status = 200
        result.geturl.return_value = "https://example.cloudflareaccess.com/login"
        with patch.object(controller, "urlopen", return_value=MagicMock(__enter__=lambda _: result)) as request:
            self.assertFalse(controller.mom_secondary_reachable(url))
        self.assertEqual(request.call_args.args[0].get_method(), "GET")

    def test_root_provision_is_mom_only_and_uses_graphical_display_not_a_fabricated_value(self):
        install = (ROOT / "scripts/install-mom-xwayland.sh").read_text()
        startup = (ROOT / "session/labwc-autostart-mom").read_text()
        self.assertIn('mom:*"Raspberry Pi 4"*|mom:*"Raspberry Pi 5"*', install)
        self.assertIn('"$(id -u)" -ne 0', install)
        self.assertIn("xwayland", install)
        self.assertIn("import-environment DISPLAY", startup)
        self.assertNotIn("DISPLAY=:0", startup)
