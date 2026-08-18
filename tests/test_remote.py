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
        self.assertIn('send_control_key 0', script)
        self.assertIn('key pressed: fast forward', script)
        self.assertIn('key pressed: rewind', script)
        self.assertIn('key pressed: stop', script)
        self.assertIn('*"key pressed:"*)', script)
        self.assertIn('last_action_ms', script)
        self.assertIn('date +%s%3N', script)
        self.assertIn('key pressed: enter', script)
        self.assertIn('echo previous_page', script)
        self.assertIn('echo next_page', script)
        self.assertIn('--kill-whom=main --signal=SIGUSR1 tv-kiosk-browser.service', script)
        self.assertIn('--kill-whom=main --signal=SIGUSR2 tv-kiosk-browser.service', script)
        self.assertIn("cec-client -t p -d 31", script)
        self.assertIn("</dev/null", script)
        self.assertNotIn("printf 'as\\n'", script)

    def test_remote_restart_never_wakes_or_selects_the_tv(self):
        script = (ROOT / "scripts" / "rahamin-kiosk-remote").read_text(encoding="utf-8")
        self.assertNotIn("printf 'as\\n'", script)
        self.assertNotIn("echo 'as'", script)
        self.assertNotIn("echo as", script)
        self.assertNotIn("on 0", script)
        self.assertNotIn("tx 10:04", script)

    def test_remote_service_waits_for_wayland(self):
        unit = (ROOT / "systemd" / "tv-kiosk-remote.service").read_text(encoding="utf-8")
        self.assertIn("wayland-0", unit)
        self.assertIn("Restart=always", unit)

    def test_baiamonte_remote_controls_dashboard_without_page_signals(self):
        script = (ROOT / "scripts" / "rahamin-kiosk-remote").read_text(encoding="utf-8")
        unit = (ROOT / "systemd" / "tv-kiosk-remote.service").read_text(encoding="utf-8")
        self.assertIn('KIOSK_VARIANT=${KIOSK_VARIANT:-auto}', script)
        self.assertIn('[ "$KIOSK_VARIANT" = baiamonte ]', script)
        self.assertIn("send_key left", script)
        self.assertIn("send_key right", script)
        self.assertIn("send_key Return", script)
        self.assertIn('play_pause) send_key space', script)
        self.assertIn("EnvironmentFile=-/etc/tv-kiosk/kiosk.env", unit)

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
        self.assertIn("systemctl --no-block --user try-restart tv-kiosk-remote.service", updater)


if __name__ == "__main__":
    unittest.main()
