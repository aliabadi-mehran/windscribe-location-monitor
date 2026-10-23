import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from windscribe_monitor.config import Config
from windscribe_monitor.locations import import_rename_export, load_locations
from windscribe_monitor.locations import Location
from windscribe_monitor.process import CommandResult
from windscribe_monitor.report import persian_report
from windscribe_monitor.runner import Monitor
from windscribe_monitor.windscribe import parse_status


class CoreTests(unittest.TestCase):
    def test_status_disconnected(self):
        value = parse_status("Login state: Logged in\nFirewall state: Off\nConnect state: Disconnected\nPublic IP: 1.2.3.4")
        self.assertTrue(value.logged_in)
        self.assertFalse(value.connected)
        self.assertTrue(value.disconnected)

    def test_status_connected(self):
        value = parse_status("Login state: Logged in\nConnect state: Connected: Paris\nProtocol: IKEv2:500")
        self.assertTrue(value.connected)
        self.assertFalse(value.disconnected)
        self.assertEqual(value.protocol, "ikev2")
        self.assertEqual(value.port, 500)

    def test_error_without_tunnel_is_safe_disconnected(self):
        value = parse_status(
            "Login state: Logged in\nFirewall state: Off\n"
            "Connect state: Error: Location does not exist or is disabled\nPublic IP: 1.2.3.4"
        )
        self.assertFalse(value.connected)
        self.assertTrue(value.disconnected)

    def test_import_export(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rename.json"
            destination = Path(directory) / "locations.txt"
            source.write_text(json.dumps({"countries": [{"name": "Paris", "nickname": "Seine"}]}))
            self.assertEqual(import_rename_export(source, destination), 1)
            self.assertEqual(load_locations(destination)[0].name, "Seine")

    def test_persian_report_does_not_invent(self):
        report = persian_report({
            "finished_at": 0, "working": 1, "failed": 0, "tested": 1,
            "top": [{"location": "X", "reliability_percent": 100, "latency_avg_ms": None,
                     "packet_loss_percent": None, "throughput_mbps": None}],
        }, "UTC")
        self.assertIn("نامشخص", report)

    def test_persian_report_names_configured_protocol(self):
        report = persian_report({
            "finished_at": 0, "protocol": "wireguard", "working": 0, "failed": 1,
            "tested": 1, "top": [],
        }, "UTC")
        self.assertIn("WIREGUARD", report)
        self.assertNotIn("IKEv2", report)

    @patch("windscribe_monitor.runner.time.sleep", return_value=None)
    def test_timed_out_connect_is_disconnected(self, _sleep):
        class FakeWindscribe:
            disconnects = 0
            connected = False

            def connect(self, _location):
                return CommandResult([], 124, "", "", 1, timed_out=True)

            def disconnect(self):
                self.disconnects += 1
                self.connected = False
                return CommandResult([], 0, "", "", 1)

            def status(self):
                return type("Status", (), {
                    "connected": self.connected,
                    "disconnected": not self.connected,
                    "connect_state": "Connected" if self.connected else "Disconnected",
                })()

        monitor = object.__new__(Monitor)
        monitor.config = Config(retries=1)
        monitor.ws = FakeWindscribe()
        monitor.owns_connection = False
        result = monitor.test_location(Location("Example"), "1.2.3.4")
        self.assertFalse(result["connected"])
        self.assertEqual(monitor.ws.disconnects, 1)
        self.assertFalse(monitor.owns_connection)


if __name__ == "__main__":
    unittest.main()
