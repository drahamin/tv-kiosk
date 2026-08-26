import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rahamin-kiosk-cloudconnexa"


class CloudConnexaFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = SourceFileLoader("cloudconnexa", str(SCRIPT))
        spec = importlib.util.spec_from_loader("cloudconnexa", loader)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_local_dashboard_does_not_start_vpn(self):
        self.assertEqual(self.module.desired_action(True, False, True, 2), ("local", 0))

    def test_returning_to_local_dashboard_stops_vpn(self):
        self.assertEqual(self.module.desired_action(True, True, True, 2), ("disconnect", 0))

    def test_three_direct_failures_start_vpn(self):
        failures = 0
        for expected in ("waiting", "waiting", "connect"):
            action, failures = self.module.desired_action(True, False, False, failures)
            self.assertEqual(action, expected)

    def test_active_vpn_stays_connected_while_local_is_unavailable(self):
        self.assertEqual(self.module.desired_action(True, True, False, 2), ("connected", 0))

    def test_missing_private_profile_is_dormant(self):
        self.assertEqual(self.module.desired_action(False, False, False, 2), ("unconfigured", 0))


if __name__ == "__main__":
    unittest.main()
