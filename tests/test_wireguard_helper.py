import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "windscribe-monitor-netns"
LOADER = importlib.machinery.SourceFileLoader("windscribe_monitor_netns_helper", str(HELPER_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
HELPER = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(HELPER)


class WireGuardHelperTests(unittest.TestCase):
    def test_endpoint_port_override_keeps_authorized_host(self):
        self.assertEqual(HELPER.endpoint_ip("192.0.2.10:443", 80), "192.0.2.10:80")

    def test_parser_strips_hooks_and_keeps_wireguard_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.conf"
            path.write_text(
                "[Interface]\n"
                "PrivateKey = private-value\n"
                "Address = 10.2.0.2/32\n"
                "DNS = 10.255.255.3\n"
                "PostUp = touch /tmp/should-not-run\n"
                "[Peer]\n"
                "PublicKey = public-value\n"
                "AllowedIPs = 0.0.0.0/0, ::/0\n"
                "Endpoint = 192.0.2.20:443\n"
            )
            path.chmod(0o600)
            parsed = HELPER.parse_config(path, 80)
            self.assertIn("Endpoint = 192.0.2.20:80", parsed["stripped"])
            self.assertNotIn("PostUp", parsed["stripped"])
            self.assertEqual(parsed["allowed"], ["0.0.0.0/0", "::/0"])
            self.assertEqual(parsed["dns"], ["10.255.255.3"])

    def test_parser_rejects_world_readable_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.conf"
            path.write_text("[Interface]\nPrivateKey = hidden\nAddress = 10.0.0.2/32\n")
            path.chmod(0o644)
            with self.assertRaises(SystemExit):
                HELPER.parse_config(path, 80)


if __name__ == "__main__":
    unittest.main()
