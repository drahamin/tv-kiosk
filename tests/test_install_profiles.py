import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallProfileTests(unittest.TestCase):
    def detect(self, model, profile="auto"):
        env = {**os.environ, "KIOSK_HARDWARE_MODEL": model, "KIOSK_PROFILE": profile}
        return subprocess.run(["sh", str(ROOT / "scripts" / "detect-hardware-profile")], env=env, check=True, capture_output=True, text=True).stdout.strip()

    def test_zero_models_select_single_page_profile(self):
        self.assertEqual(self.detect("Raspberry Pi Zero W Rev 1.1"), "zero")
        self.assertEqual(self.detect("Raspberry Pi Zero 2 W Rev 1.0"), "zero")

    def test_pi_three_and_later_select_multi_page_profile(self):
        self.assertEqual(self.detect("Raspberry Pi 3 Model B Plus Rev 1.3"), "multi")
        self.assertEqual(self.detect("Raspberry Pi 5 Model B Rev 1.0"), "multi")

    def test_profile_can_be_overridden_for_image_testing(self):
        self.assertEqual(self.detect("Raspberry Pi 3 Model B", "zero"), "zero")

    def test_zero_default_is_one_baiamonte_tv_page(self):
        config = json.loads((ROOT / "config" / "kiosk-zero.json").read_text(encoding="utf-8"))
        self.assertTrue(config["setup_complete"])
        self.assertEqual(len(config["pages"]), 1)
        self.assertTrue(config["pages"][0]["url"].endswith("/tv"))

    def test_universal_image_contains_dual_wifi_and_profile_settings(self):
        builder = (ROOT / "image" / "build-image.sh").read_text(encoding="utf-8")
        self.assertIn("WIFI_PRIMARY_SSID", builder)
        self.assertIn("WIFI_SECONDARY_SSID", builder)
        self.assertIn("Rahamin-Home", builder)
        self.assertIn("Rahamin-Baiamonte", builder)
        self.assertIn("KIOSK_PROFILE", builder)
        self.assertIn("rahamin-kiosk-universal.img", builder)
        self.assertIn("raspios_lite_armhf_latest", builder)
        self.assertIn("band=bg", builder)
        self.assertIn("powersave=2", builder)
        self.assertIn("logo.nologo", builder)
        self.assertIn("disable_splash=1", builder)
        self.assertIn("userconfig.service", builder)

    def test_updates_detect_and_persist_missing_hardware_profile(self):
        setup = (ROOT / "scripts" / "apply-system-config.sh").read_text(encoding="utf-8")
        self.assertIn('KIOSK_PROFILE=auto KIOSK_HARDWARE_MODEL="$HARDWARE_MODEL"', setup)
        self.assertIn("KIOSK_HARDWARE_PROFILE=%s", setup)
        self.assertIn("config/kiosk-admin.pub", setup)
        self.assertIn('"/home/$KIOSK_USER/.ssh/authorized_keys"', setup)

    def test_profile_branding_and_hdmi_fallbacks_are_installed(self):
        boot = (ROOT / "session" / "boot.html").read_text(encoding="utf-8")
        browser = (ROOT / "scripts" / "browser-controller.py").read_text(encoding="utf-8")
        setup = (ROOT / "scripts" / "apply-system-config.sh").read_text(encoding="utf-8")
        self.assertIn("baiamonte-logo.svg", boot)
        self.assertIn("miami-logo.svg", boot)
        self.assertIn("?profile={quote(profile_name)}", browser)
        self.assertIn("display_auto_detect=1", setup)
        self.assertIn("hdmi_force_hotplug=1", setup)
        self.assertIn("hdmi_force_edid_audio=1", setup)
        self.assertIn("hdmi_group=1", setup)
        self.assertIn("hdmi_mode=16", setup)

    def test_firstboot_explicitly_tries_both_wifi_profiles_without_wizard(self):
        firstboot = (ROOT / "image" / "tv-kiosk-firstboot").read_text(encoding="utf-8")
        unit = (ROOT / "image" / "tv-kiosk-firstboot.service").read_text(encoding="utf-8")
        builder = (ROOT / "image" / "build-image.sh").read_text(encoding="utf-8")
        self.assertIn('"$WIFI_PRIMARY_SSID" "$WIFI_SECONDARY_SSID"', firstboot)
        self.assertIn('connection up "Rahamin WiFi $wifi_ssid"', firstboot)
        self.assertNotIn("userconfig.service", unit)
        self.assertNotIn("cloud-final.service", unit)
        self.assertNotIn("Before=", unit)
        self.assertNotIn("Conflicts=", unit)
        self.assertIn('getty@tty1.service"', builder)


if __name__ == "__main__":
    unittest.main()
