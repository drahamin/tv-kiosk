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


if __name__ == "__main__":
    unittest.main()
