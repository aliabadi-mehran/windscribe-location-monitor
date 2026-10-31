import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from windscribe_monitor.config import Config
from windscribe_monitor.cli import main
from windscribe_monitor.locations import import_rename_export, load_locations
from windscribe_monitor.locations import Location
from windscribe_monitor.process import CommandResult
from windscribe_monitor.report import persian_report
from windscribe_monitor.runner import Monitor
from windscribe_monitor.telegram import queue_message
from windscribe_monitor.windscribe import parse_status
from windscribe_monitor.wireguard import (WireGuardLocation, discover_wireguard_configs,
                                          validate_profile_permissions)


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
        self.assertIn("N/A", report)
        self.assertIn("<b>1. X</b>", report)

    def test_persian_report_names_configured_protocol(self):
        report = persian_report({
            "finished_at": 0, "protocol": "wireguard", "working": 0, "failed": 1,
            "tested": 1, "top": [],
        }, "UTC")
        self.assertIn("WIREGUARD", report)
        self.assertNotIn("IKEv2", report)

    def test_report_uses_full_location_and_html(self):
        report = persian_report({
            "finished_at": 0, "protocol": "wireguard", "working": 1, "failed": 0,
            "tested": 1, "top": [{
                "location": "Poland/Gdansk/Motlawa",
                "display_name": "Gdansk, Poland — Motlawa & North",
                "reliability_percent": 100, "latency_avg_ms": 25,
                "packet_loss_percent": 0, "throughput_mbps": 12,
            }],
        }, "UTC")
        self.assertIn("<b>1. Gdansk, Poland — Motlawa &amp; North</b>", report)
        self.assertIn("Top 1 Locations", report)

    def test_default_top_five_and_wireguard_port_80(self):
        config = Config()
        self.assertEqual(config.top_size, 5)
        self.assertEqual(config.wireguard_endpoint_port_override, 80)

    def test_raspberry_pi_timer_is_daily_at_six(self):
        timer = (Path(__file__).resolve().parents[1] / "systemd" / "windscribe-monitor.timer").read_text()
        self.assertIn("OnCalendar=*-*-* 06:00:00", timer)
        self.assertNotIn("00/3", timer)

    @patch("windscribe_monitor.cli.platform.system", return_value="Linux")
    def test_pi_configuration_preserves_telegram_and_sets_daily_defaults(self, _system):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "state"
            config = Config(telegram_chat_id="-100123", telegram_token_file="/private/token")
            config.save(home)
            profile_dir = Path(directory) / "profiles"
            self.assertEqual(main([
                "--home", str(home), "configure-raspberry-pi",
                "--config-dir", str(profile_dir), "--endpoint-port", "80",
            ]), 0)
            updated = Config.load(home)
            self.assertEqual(updated.telegram_chat_id, "-100123")
            self.assertEqual(updated.telegram_token_file, "/private/token")
            self.assertEqual(updated.interval_seconds, 86_400)
            self.assertEqual(updated.top_size, 5)
            self.assertEqual(updated.wireguard_endpoint_port_override, 80)

    def test_wireguard_discovery_uses_private_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "Poland" / "Gdansk" / "Motlawa.conf"
            profile.parent.mkdir(parents=True)
            profile.write_text("[Interface]\nPrivateKey = hidden\n")
            profile.chmod(0o600)
            (root / "wireguard-locations.json").write_text(json.dumps({
                "profiles": {
                    "Poland/Gdansk/Motlawa.conf": {
                        "country": "Poland", "city": "Gdansk", "server": "Motlawa"
                    }
                }
            }))
            locations = discover_wireguard_configs(root)
            self.assertEqual(len(locations), 1)
            self.assertEqual(locations[0].display_name, "Gdansk, Poland — Motlawa")
            self.assertEqual(validate_profile_permissions(locations), [])

    def test_wireguard_discovery_flags_open_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "Example.conf"
            profile.write_text("placeholder")
            profile.chmod(0o644)
            problems = validate_profile_permissions(discover_wireguard_configs(Path(directory)))
            self.assertEqual(len(problems), 1)

    def test_queue_preserves_html_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = queue_message(Path(directory), "<b>report</b>", "HTML")
            payload = json.loads(path.read_text())
            self.assertEqual(payload["parse_mode"], "HTML")

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
