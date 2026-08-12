import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rahamin-kiosk-audio"
CHIME = ROOT / "scripts" / "startup-chime.py"


class HdmiAudioTests(unittest.TestCase):
    def test_auto_detects_hdmi_sink_and_applies_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            calls = directory / "calls"
            wpctl = directory / "wpctl"
            metadata = directory / "pw-metadata"
            wpctl.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = status ]; then\n"
                "  printf 'Audio\\n ├─ Sinks:\\n │  *  56. analog.stereo\\n │     65. alsa_output.platform-hdmi.hdmi-stereo\\n ├─ Sources:\\n'\n"
                "else\n"
                f"  printf '%s\\n' \"$*\" >> '{calls}'\n"
                "fi\n",
                encoding="utf-8",
            )
            wpctl.chmod(0o755)
            metadata.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\n", encoding="utf-8")
            metadata.chmod(0o755)
            environment = {**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"}
            subprocess.run([str(SCRIPT), "on", "60"], env=environment, check=True, capture_output=True, text=True)
            commands = calls.read_text(encoding="utf-8")
        self.assertIn('default.audio.sink {"name":"alsa_output.platform-hdmi.hdmi-stereo"}', commands)
        self.assertIn("set-volume 65 60%", commands)
        self.assertIn("set-mute 65 0", commands)

    def test_rejects_invalid_volume(self):
        result = subprocess.run([str(SCRIPT), "on", "101"], check=False, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_startup_twinkle_is_short_stereo_pcm(self):
        result = subprocess.run([str(CHIME)], check=True, capture_output=True)
        self.assertGreater(len(result.stdout), 100_000)
        self.assertLess(len(result.stdout), 200_000)
        self.assertEqual(len(result.stdout) % 4, 0)

    def test_audio_service_plays_twinkle_once(self):
        unit = (ROOT / "systemd" / "tv-kiosk-chime.service").read_text(encoding="utf-8")
        self.assertIn("WantedBy=default.target", unit)
        self.assertIn("startup-chime.py", unit)
        self.assertIn("--volume=0.25", unit)

    def test_successful_updates_replay_twinkle(self):
        updater = (ROOT / "scripts" / "update-kiosk.sh").read_text(encoding="utf-8")
        self.assertIn("restart tv-kiosk-chime.service", updater)


if __name__ == "__main__":
    unittest.main()
