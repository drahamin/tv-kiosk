import importlib.machinery
import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "rahamin-kiosk-network"
loader = importlib.machinery.SourceFileLoader("network_helper", str(MODULE_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class NetworkHelperTests(unittest.TestCase):
    def test_hostname_validation(self):
        self.assertTrue(helper.valid_hostname("rahamin-kiosk"))
        self.assertFalse(helper.valid_hostname("bad hostname"))

    def test_manual_address_validation(self):
        data = {
            "wifi_ipv4_mode": "manual",
            "wifi_ipv4_address": "192.168.86.118/24",
            "wifi_ipv4_gateway": "192.168.86.1",
            "wifi_ipv4_dns": "1.1.1.1,8.8.8.8",
        }
        helper.validate_ip_settings("wifi_ipv4", data, 4)
        data["wifi_ipv4_address"] = "not-an-address"
        with self.assertRaises(ValueError):
            helper.validate_ip_settings("wifi_ipv4", data, 4)


if __name__ == "__main__":
    unittest.main()
