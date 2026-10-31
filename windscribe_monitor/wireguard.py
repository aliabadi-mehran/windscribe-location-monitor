from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import signal
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ensure_dirs
from .measure import public_ip
from .process import run_command
from .report import persian_report
from .telegram import Telegram, flush_queue, queue_message


LOG = logging.getLogger("windscribe_monitor")


@dataclass(frozen=True)
class WireGuardLocation:
    name: str
    config_path: Path
    country: str | None = None
    city: str | None = None
    server: str | None = None

    @property
    def display_name(self) -> str:
        place = ", ".join(value for value in (self.city, self.country) if value)
        server = self.server or self.name
        return f"{place} — {server}" if place else server


def discover_wireguard_configs(directory: Path) -> list[WireGuardLocation]:
    """Inventory only existing authorized .conf files; never synthesize endpoints."""
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        return []
    metadata_path = directory / "wireguard-locations.json"
    metadata: dict = {}
    if metadata_path.is_file() and not metadata_path.is_symlink():
        loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata = loaded.get("profiles", loaded) if isinstance(loaded, dict) else {}
    result: list[WireGuardLocation] = []
    seen: set[str] = set()
    for path in sorted(directory.rglob("*.conf"), key=lambda item: str(item).casefold()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(directory).with_suffix("")
        name = str(relative)
        key = name.casefold()
        if key in seen:
            raise RuntimeError(f"duplicate WireGuard configuration name: {name}")
        seen.add(key)
        relative_file = str(path.relative_to(directory))
        details = metadata.get(relative_file, {}) if isinstance(metadata, dict) else {}
        if not isinstance(details, dict):
            details = {}
        parts = relative.parts
        # Directory convention Country/City/Server.conf is deterministic and
        # optional; otherwise metadata must supply country/city explicitly.
        path_country = parts[-3] if len(parts) >= 3 else None
        path_city = parts[-2] if len(parts) >= 3 else None
        result.append(WireGuardLocation(
            name=name,
            config_path=path.resolve(),
            country=str(details.get("country") or path_country) if (details.get("country") or path_country) else None,
            city=str(details.get("city") or path_city) if (details.get("city") or path_city) else None,
            server=str(details.get("server") or parts[-1]),
        ))
    return result


def validate_profile_permissions(locations: list[WireGuardLocation]) -> list[str]:
    problems = []
    for location in locations:
        mode = location.config_path.stat().st_mode
        owner_ok = location.config_path.stat().st_uid == os.getuid()
        if not stat.S_ISREG(mode) or mode & 0o077 or not owner_ok:
            problems.append(f"{location.name}: expected a user-owned regular mode-600 file")
    return problems


class NamespaceTunnel:
    def __init__(self, config: Config, location: WireGuardLocation):
        self.config = config
        self.location = location
        token = secrets.token_hex(4)
        self.namespace = f"wsmon-{token}"
        self.interface = f"wsm{token}"
        self.started_at = 0.0
        self.active = False

    def _helper(self, action: str, *args: str, timeout: float | None = None) -> dict:
        command = ["sudo", "-n", self.config.wireguard_helper, action,
                   "--namespace", self.namespace, *args]
        result = run_command(command, timeout=timeout or self.config.command_timeout_seconds)
        if result.timed_out:
            raise RuntimeError(f"WireGuard namespace {action} timed out")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or f"WireGuard namespace {action} failed")
        try:
            return json.loads(result.stdout) if result.stdout.strip() else {"ok": True}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"WireGuard helper returned invalid {action} output") from exc

    def up(self) -> dict:
        self.started_at = time.time()
        dns_args: list[str] = []
        for server in self.config.wireguard_dns_servers:
            dns_args.extend(["--dns", server])
        result = self._helper(
            "setup", "--interface", self.interface,
            "--config", str(self.location.config_path), *dns_args,
            "--endpoint-port", str(self.config.wireguard_endpoint_port_override),
            timeout=self.config.connect_timeout_seconds,
        )
        self.active = True
        return result

    def probe(self) -> dict:
        return self._helper(
            "probe", "--interface", self.interface,
            "--started-at", str(self.started_at),
            "--handshake-timeout", str(self.config.wireguard_handshake_timeout_seconds),
            "--ping-target", self.config.ping_target,
            "--ping-count", str(self.config.ping_count),
            "--connectivity-url", self.config.connectivity_url,
            "--public-ip-url", self.config.public_ip_url,
            "--timeout", str(self.config.http_timeout_seconds),
            timeout=(self.config.ping_timeout_seconds + self.config.http_timeout_seconds * 2 +
                     self.config.wireguard_handshake_timeout_seconds + 15),
        )

    def speed(self) -> dict:
        return self._helper(
            "speed", "--interface", self.interface,
            "--url", self.config.speed_test_url.format(bytes=self.config.speed_test_bytes),
            "--bytes", str(self.config.speed_test_bytes),
            "--timeout", str(self.config.http_timeout_seconds),
            timeout=self.config.http_timeout_seconds + 10,
        )

    def cleanup(self) -> None:
        # cleanup is idempotent and is attempted even when setup only partially ran
        command = ["sudo", "-n", self.config.wireguard_helper, "cleanup",
                   "--namespace", self.namespace]
        result = run_command(command, timeout=self.config.disconnect_timeout_seconds)
        self.active = False
        if result.timed_out or result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or "WireGuard namespace cleanup failed")


class WireGuardMonitor:
    """Raspberry Pi monitor that never changes the host default route."""

    def __init__(self, config: Config, home: Path):
        self.config = config
        self.home = ensure_dirs(home)
        self.active_tunnel: NamespaceTunnel | None = None
        self._install_signal_handlers()

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: object) -> None:
            LOG.warning("received signal %s; cleaning up isolated WireGuard namespace", signum)
            self.cleanup()
            raise SystemExit(128 + signum)
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            signal.signal(getattr(signal, name), handler)

    def cleanup(self) -> None:
        if self.active_tunnel is not None:
            tunnel, self.active_tunnel = self.active_tunnel, None
            tunnel.cleanup()

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

    def test_location(self, location: WireGuardLocation, baseline_ip: str | None) -> dict:
        result: dict = {
            "location": location.name,
            "display_name": location.display_name,
            "country": location.country,
            "city": location.city,
            "server": location.server,
            "connected": False,
            "functional": False,
            "attempts": 0,
            "errors": [],
            "transport": "wireguard-config",
        }
        for attempt in range(1, self.config.retries + 1):
            result["attempts"] = attempt
            tunnel = NamespaceTunnel(self.config, location)
            self.active_tunnel = tunnel
            try:
                setup = tunnel.up()
                result["connected"] = True
                result["namespace_setup"] = setup
                probe = tunnel.probe()
                result.update({
                    "fresh_handshake": probe.get("fresh_handshake", False),
                    "latest_handshake": probe.get("latest_handshake"),
                    "rx_bytes": probe.get("rx_bytes"),
                    "tx_bytes": probe.get("tx_bytes"),
                    "data_transfer_verified": probe.get("data_transfer_verified", False),
                    "vpn_public_ip": probe.get("vpn_public_ip"),
                    "public_ip_changed": bool(
                        baseline_ip and probe.get("vpn_public_ip") and
                        baseline_ip != probe.get("vpn_public_ip")
                    ),
                    "http": probe.get("http", {}),
                    "ping": probe.get("ping", {}),
                    "isolation": probe.get("isolation", {}),
                })
                isolation = result["isolation"]
                result["functional"] = bool(
                    result["fresh_handshake"]
                    and result["data_transfer_verified"]
                    and result["http"].get("ok")
                    and (not baseline_ip or result["public_ip_changed"])
                    and isolation.get("host_uplink_absent")
                    and isolation.get("ipv4_default_via_tunnel")
                )
                if result["functional"]:
                    return result
                result["errors"].append(
                    "fresh handshake, routed transfer, external IP, or namespace isolation check failed"
                )
            except Exception as exc:
                result["errors"].append(str(exc))
            finally:
                try:
                    self.cleanup()
                except Exception as exc:
                    result["errors"].append(f"cleanup: {exc}")
                    raise
            if attempt < self.config.retries:
                time.sleep(1)
        return result

    def speed_location(self, location: WireGuardLocation) -> dict:
        tunnel = NamespaceTunnel(self.config, location)
        self.active_tunnel = tunnel
        try:
            tunnel.up()
            probe = tunnel.probe()
            if not (probe.get("fresh_handshake") and probe.get("data_transfer_verified")):
                raise RuntimeError("fresh WireGuard handshake was not verified before speed test")
            return tunnel.speed()
        finally:
            self.cleanup()

    def run(self, _locations_path: Path, limit: int | None = None, notify: bool = True,
            only: list[str] | None = None, resume: bool = False) -> dict:
        lock_file = (self.home / "run.lock").open("w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another monitor run is already active") from exc
        lock_file.write(str(os.getpid()))
        lock_file.flush()

        locations = discover_wireguard_configs(Path(self.config.wireguard_config_dir))
        if not locations:
            raise RuntimeError(
                "no authorized WireGuard .conf files found; download individual location profiles "
                f"and place mode-600 files under {Path(self.config.wireguard_config_dir).expanduser()}"
            )
        permission_problems = validate_profile_permissions(locations)
        if permission_problems:
            raise RuntimeError("; ".join(permission_problems))
        if only:
            by_name = {location.name.casefold(): location for location in locations}
            missing = [name for name in only if name.casefold() not in by_name]
            if missing:
                raise RuntimeError(f"unknown WireGuard configurations: {', '.join(missing)}")
            locations = [by_name[name.casefold()] for name in only]
        if limit:
            locations = locations[:limit]
        all_locations = locations
        baseline_ip = public_ip(self.config)
        started = time.time()
        results: list[dict] = []
        checkpoint_path = self.home / "in-progress.json"
        start_index = 0
        if resume:
            if not checkpoint_path.exists():
                raise RuntimeError("no in-progress checkpoint exists to resume")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            existing = checkpoint.get("results", [])
            if checkpoint.get("total") != len(all_locations):
                raise RuntimeError("checkpoint configuration count does not match current inventory")
            if [item.name for item in all_locations[:len(existing)]] != [item.get("location") for item in existing]:
                raise RuntimeError("checkpoint configuration order does not match current inventory")
            results = existing
            start_index = len(results)
            started = checkpoint.get("started_at", started)
            locations = all_locations[start_index:]
        history = self._history()
        try:
            for index, location in enumerate(locations, start_index + 1):
                LOG.info("testing WireGuard config %s/%s: %s", index, len(all_locations), location.name)
                item = self.test_location(location, baseline_ip)
                record = history.setdefault(location.name, {"successes": 0, "runs": 0})
                record["runs"] += 1
                if item.get("functional"):
                    record["successes"] += 1
                item["reliability_percent"] = 100 * record["successes"] / record["runs"]
                results.append(item)
                checkpoint_path.write_text(json.dumps({
                    "started_at": started, "updated_at": time.time(),
                    "completed": len(results), "total": len(all_locations), "results": results,
                }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                checkpoint_path.chmod(0o600)
        finally:
            self.cleanup()
            self._save_history(history)

        functional = [item for item in results if item.get("functional")]
        functional.sort(key=lambda item: (
            -item.get("reliability_percent", 0),
            item.get("ping", {}).get("packet_loss_percent")
            if item.get("ping", {}).get("packet_loss_percent") is not None else 101,
            item.get("ping", {}).get("latency_avg_ms")
            if item.get("ping", {}).get("latency_avg_ms") is not None else float("inf"),
        ))
        by_name = {location.name: location for location in all_locations}
        shortlist = functional[:self.config.shortlist_size]
        for item in shortlist:
            try:
                speed = self.speed_location(by_name[item["location"]])
                item["speed"] = speed
                item["throughput_mbps"] = speed.get("mbps")
                if not speed.get("ok"):
                    item.setdefault("errors", []).append("bandwidth-limited speed test failed")
            except Exception as exc:
                item.setdefault("errors", []).append(f"speed test: {exc}")

        ranked = sorted(shortlist, key=lambda item: (
            -item.get("reliability_percent", 0),
            item.get("ping", {}).get("packet_loss_percent")
            if item.get("ping", {}).get("packet_loss_percent") is not None else 101,
            item.get("ping", {}).get("latency_avg_ms")
            if item.get("ping", {}).get("latency_avg_ms") is not None else float("inf"),
            -(item.get("throughput_mbps") or 0),
        ))
        top = [{
            "location": item["location"],
            "display_name": item.get("display_name", item["location"]),
            "country": item.get("country"),
            "city": item.get("city"),
            "server": item.get("server"),
            "reliability_percent": item.get("reliability_percent"),
            "latency_avg_ms": item.get("ping", {}).get("latency_avg_ms"),
            "packet_loss_percent": item.get("ping", {}).get("packet_loss_percent"),
            "throughput_mbps": item.get("throughput_mbps"),
        } for item in ranked[:self.config.top_size]]
        run = {
            "started_at": started, "finished_at": time.time(), "baseline_public_ip": baseline_ip,
            "protocol": "wireguard", "port": None, "tested": len(results),
            "working": len(functional), "failed": len(results) - len(functional),
            "results": results, "top": top,
        }
        run_path = self.home / "runs" / f"{int(started)}.json"
        run_path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        run_path.chmod(0o600)
        last_path = self.home / "last-run.json"
        last_path.write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        last_path.chmod(0o600)
        checkpoint_path.unlink(missing_ok=True)

        if notify:
            message = persian_report(run, self.config.timezone)
            try:
                flush_queue(self.home, Telegram(self.config))
                Telegram(self.config).send(message, "HTML")
            except Exception as exc:
                LOG.error("Telegram delivery failed; queued locally: %s", exc)
                queue_message(self.home, message, "HTML")
        return run
