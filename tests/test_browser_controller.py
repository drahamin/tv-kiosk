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
    def test_chromium_launch_forces_fullscreen(self):
        fake = MagicMock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "STATE_DIR", Path(directory)), patch.object(controller.subprocess, "Popen", return_value=fake) as popen:
            self.assertIs(controller.launch_chromium(), fake)
        command = popen.call_args.args[0]
        self.assertIn("--kiosk", command)
        self.assertIn("--start-fullscreen", command)

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
