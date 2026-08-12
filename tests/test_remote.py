import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RemoteControlTests(unittest.TestCase):
    def test_samsung_remote_controls_navigation_and_zoom(self):
        script = (ROOT / "scripts" / "rahamin-kiosk-remote").read_text(encoding="utf-8")
        for command in ("key pressed: up", "key pressed: select", "key pressed: channel up", "key pressed: channel down", "key pressed: number0"):
            self.assertIn(command, script)
        self.assertIn('send_control_key plus', script)
        self.assertIn('send_control_key minus', script)
        self.assertIn('key pressed: fast forward', script)
        self.assertIn('key pressed: rewind', script)
        self.assertIn('key pressed: stop', script)
        self.assertIn('", 0)"', script)
        self.assertIn("cec-client -t p -d 31", script)
        self.assertIn("printf 'as\\n'", script)

    def test_remote_service_waits_for_wayland(self):
        unit = (ROOT / "systemd" / "tv-kiosk-remote.service").read_text(encoding="utf-8")
        self.assertIn("wayland-0", unit)
        self.assertIn("Restart=always", unit)

    def test_labwc_hides_and_warps_cursor(self):
        config = (ROOT / "session" / "labwc-rc.xml").read_text(encoding="utf-8")
        autostart = (ROOT / "session" / "labwc-autostart").read_text(encoding="utf-8")
        self.assertIn('action name="HideCursor"', config)
        self.assertIn('action name="WarpCursor"', config)
        self.assertNotIn("ToggleFullscreen", config)
        self.assertIn("wtype -M alt -M logo", autostart)

    def test_successful_updates_restart_remote_listener(self):
        updater = (ROOT / "scripts" / "update-kiosk.sh").read_text(encoding="utf-8")
        self.assertIn("try-restart tv-kiosk-remote.service", updater)


if __name__ == "__main__":
    unittest.main()
