from __future__ import annotations

import fcntl
import json
import logging
import os
import signal
import time
from dataclasses import asdict
from pathlib import Path

from .config import Config, ensure_dirs
from .locations import Location, load_locations
from .measure import http_check, measure_ping, public_ip, speed_test, to_dict
from .report import persian_report
from .telegram import Telegram, flush_queue, queue_message
from .windscribe import Windscribe, clean_output


LOG = logging.getLogger("windscribe_monitor")


class Monitor:
    def __init__(self, config: Config, home: Path):
        self.config = config
        self.home = ensure_dirs(home)
        self.ws = Windscribe(config)
        self.owns_connection = False
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: object) -> None:
            LOG.warning("received signal %s; cleaning up", signum)
            self.cleanup()
            raise SystemExit(128 + signum)
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signal.signal(getattr(signal, name), handler)

    def cleanup(self) -> None:
        if self.owns_connection:
            try:
                stable_disconnected_polls = 0
                last_state = "unknown"
                # Cancel the monitor's pending attempt even if status currently says
                # Disconnected; NetworkExtension can complete asynchronously later.
                self.ws.disconnect()
                for _attempt in range(15):
                    try:
                        status = self.ws.status()
                        last_state = status.connect_state
                        if status.disconnected:
                            stable_disconnected_polls += 1
                        else:
                            stable_disconnected_polls = 0
                            self.ws.disconnect()
                        if stable_disconnected_polls >= 5:
                            break
                    except Exception:
                        stable_disconnected_polls = 0
                        self.ws.disconnect()
                    time.sleep(1)
                if stable_disconnected_polls < 5:
                    raise RuntimeError(
                        f"could not verify stable disconnection of monitor-owned tunnel; last state: {last_state}"
                    )
            finally:
                self.owns_connection = False

    def assert_no_external_connection(self) -> None:
        status = self.ws.status()
        if not status.disconnected and not self.owns_connection:
            raise RuntimeError(
                f"Windscribe is not exactly disconnected ({status.connect_state}); aborting to avoid interference"
            )

    def wait_for_tunnel(self):
        deadline = time.monotonic() + self.config.connect_timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            try:
                status = self.ws.status()
                protocol_ok = status.protocol in (None, self.config.protocol)
                port_ok = status.port in (None, self.config.port)
                if status.connected and protocol_ok and port_ok:
                    return status, last_error
                if status.connect_state.startswith("Error:"):
                    return None, status.connect_state
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1)
        return None, last_error

    def flush_telegram_via_location(self, location: str) -> tuple[int, int]:
        self.assert_no_external_connection()
        self.owns_connection = True
        try:
            command = self.ws.connect(location)
            if command.returncode != 0 or command.timed_out:
                raise RuntimeError(f"could not connect to {location} for Telegram delivery")
            status, status_error = self.wait_for_tunnel()
            if not status:
                raise RuntimeError(
                    f"Telegram delivery tunnel failed for {location}: {status_error or 'timeout'}"
                )
            return flush_queue(self.home, Telegram(self.config))
        finally:
            self.cleanup()

    def _history(self) -> dict[str, dict[str, int]]:
        path = self.home / "history.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_history(self, history: dict[str, dict[str, int]]) -> None:
        path = self.home / "history.json"
        path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def test_location(self, location: Location, baseline_ip: str | None) -> dict:
        result: dict = {
            "location": location.name, "hostname": location.hostname,
            "connected": False, "attempts": 0, "errors": [],
            "udp_diagnostic": "not_available_without_official_hostname",
        }
        for attempt in range(1, self.config.retries + 1):
            self.assert_no_external_connection()
            result["attempts"] = attempt
            # Once connect is invoked, the attempt is ours even if the command times out;
            # the app may complete asynchronously and must be cleaned up.
            self.owns_connection = True
            command = self.ws.connect(location.name)
            output = clean_output(command)
            if command.timed_out:
                result["errors"].append("connection timeout")
            elif command.returncode != 0:
                result["errors"].append(output or f"connect exit {command.returncode}")
            else:
                status, status_error = self.wait_for_tunnel()
                if status:
                    result["status"] = asdict(status)
                    result["connected"] = True
                    result["tunnel_test_pending"] = status.pending
                    break
                if status_error:
                    result["errors"].append(f"status poll: {status_error}")
                result["errors"].append(f"{self.config.protocol} tunnel setup timeout")
            self.cleanup()
            time.sleep(1)

        if not result["connected"]:
            self.cleanup()
            return result
        try:
            time.sleep(self.config.settle_seconds)
            http = http_check(self.config)
            ip = public_ip(self.config)
            ping = measure_ping(self.config)
            result.update({
                "http": to_dict(http), "vpn_public_ip": ip,
                "public_ip_changed": bool(baseline_ip and ip and baseline_ip != ip),
                "ping": to_dict(ping),
            })
            result["functional"] = bool(http.ok and (not baseline_ip or (ip and ip != baseline_ip)))
            if not result["functional"]:
                result["errors"].append("HTTP/IP verification did not prove routed VPN connectivity")
        finally:
            self.cleanup()
        return result

    def run(self, locations_path: Path, limit: int | None = None, notify: bool = True,
            only: list[str] | None = None, resume: bool = False) -> dict:
        lock_path = self.home / "run.lock"
        lock_file = lock_path.open("w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another monitor run is already active") from exc
        lock_file.write(str(os.getpid()))
        lock_file.flush()

        initial = self.ws.status()
        if not initial.logged_in:
            raise RuntimeError("Windscribe is not logged in")
        if not initial.disconnected:
            raise RuntimeError(
                f"Windscribe was not exactly disconnected ({initial.connect_state}); refusing to interfere"
            )
        locations = load_locations(locations_path)
        if not locations:
            raise RuntimeError(f"no locations found in {locations_path}")
        if only:
            by_name = {location.name.casefold(): location for location in locations}
            missing = [name for name in only if name.casefold() not in by_name]
            if missing:
                raise RuntimeError(f"unknown requested locations: {', '.join(missing)}")
            locations = [by_name[name.casefold()] for name in only]
        if limit:
            locations = locations[:limit]
        all_locations = locations
        baseline_ip = public_ip(self.config)
        started = time.time()
        results = []
        checkpoint_path = self.home / "in-progress.json"
        start_index = 0
        if resume:
            if not checkpoint_path.exists():
                raise RuntimeError("no in-progress checkpoint exists to resume")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            existing = checkpoint.get("results", [])
            if checkpoint.get("total") != len(all_locations):
                raise RuntimeError("checkpoint location count does not match current inventory")
            expected_names = [location.name for location in all_locations[:len(existing)]]
            actual_names = [item.get("location") for item in existing]
            if expected_names != actual_names:
                raise RuntimeError("checkpoint location order does not match current inventory")
            results = existing
            start_index = len(results)
            started = checkpoint.get("started_at", started)
            locations = all_locations[start_index:]
        history = self._history()
        try:
            for index, location in enumerate(locations, start_index + 1):
                LOG.info("testing %s/%s: %s", index, len(all_locations), location.name)
                item = self.test_location(location, baseline_ip)
                record = history.setdefault(location.name, {"successes": 0, "runs": 0})
                record["runs"] += 1
                if item.get("functional"):
                    record["successes"] += 1
                item["reliability_percent"] = 100 * record["successes"] / record["runs"]
                results.append(item)
                checkpoint_path.write_text(json.dumps({
                    "started_at": started,
                    "updated_at": time.time(),
                    "completed": len(results),
                    "total": len(all_locations),
                    "results": results,
                }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                checkpoint_path.chmod(0o600)
        finally:
            self.cleanup()
            self._save_history(history)

        functional = [item for item in results if item.get("functional")]
        functional.sort(key=lambda item: (
            -item.get("reliability_percent", 0),
            item.get("ping", {}).get("packet_loss_percent") if item.get("ping", {}).get("packet_loss_percent") is not None else 101,
            item.get("ping", {}).get("latency_avg_ms") if item.get("ping", {}).get("latency_avg_ms") is not None else float("inf"),
        ))
        shortlist = functional[:self.config.shortlist_size]

        # Speed tests are separate, data-capped, and reconnect only shortlisted endpoints.
        for item in shortlist:
            location = Location(item["location"], item.get("hostname"))
            self.assert_no_external_connection()
            self.owns_connection = True
            try:
                command = self.ws.connect(location.name)
                if command.returncode == 0 and not command.timed_out:
                    status, status_error = self.wait_for_tunnel()
                    if status:
                        time.sleep(self.config.settle_seconds)
                        speed = speed_test(self.config)
                        item["speed"] = to_dict(speed)
                        item["throughput_mbps"] = speed.mbps
                    elif status_error:
                        item.setdefault("errors", []).append(f"speed-test status: {status_error}")
                else:
                    item.setdefault("errors", []).append("speed-test reconnect failed")
            except Exception as exc:
                item.setdefault("errors", []).append(f"speed test: {exc}")
            finally:
                self.cleanup()

        ranked = sorted(shortlist, key=lambda item: (
            -item.get("reliability_percent", 0),
            item.get("ping", {}).get("packet_loss_percent") if item.get("ping", {}).get("packet_loss_percent") is not None else 101,
            item.get("ping", {}).get("latency_avg_ms") if item.get("ping", {}).get("latency_avg_ms") is not None else float("inf"),
            -(item.get("throughput_mbps") or 0),
        ))
        top = []
        for item in ranked[:self.config.top_size]:
            top.append({
                "location": item["location"],
                "display_name": item["location"],
                "reliability_percent": item.get("reliability_percent"),
                "latency_avg_ms": item.get("ping", {}).get("latency_avg_ms"),
                "packet_loss_percent": item.get("ping", {}).get("packet_loss_percent"),
                "throughput_mbps": item.get("throughput_mbps"),
            })
        run = {
            "started_at": started, "finished_at": time.time(), "baseline_public_ip": baseline_ip,
            "protocol": self.config.protocol, "port": self.config.port,
            "tested": len(results), "working": len(functional), "failed": len(results) - len(functional),
            "results": results, "top": top,
        }
        run_path = self.home / "runs" / f"{int(run['started_at'])}.json"
        run_path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        run_path.chmod(0o600)
        (self.home / "last-run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        checkpoint_path.unlink(missing_ok=True)

        message = persian_report(run, self.config.timezone)
        if notify:
            telegram = Telegram(self.config)
            try:
                flush_queue(self.home, telegram)
                telegram.send(message, "HTML")
            except Exception as exc:
                LOG.error("Telegram delivery failed; queued locally: %s", exc)
                queue_message(self.home, message, "HTML")
                if top:
                    try:
                        sent, failed = self.flush_telegram_via_location(top[0]["location"])
                        LOG.info("Telegram VPN retry: sent=%s failed=%s", sent, failed)
                    except Exception as vpn_exc:
                        LOG.error("Telegram VPN retry failed; queue preserved: %s", vpn_exc)
        return run

