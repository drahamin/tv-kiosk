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

    def test_remote_service_waits_for_wayland(self):
        unit = (ROOT / "systemd" / "tv-kiosk-remote.service").read_text(encoding="utf-8")
        self.assertIn("wayland-0", unit)
        self.assertIn("Restart=always", unit)


if __name__ == "__main__":
    unittest.main()
