from __future__ import annotations

import json
import platform
import re
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

from .config import Config
from .process import run_command


@dataclass
class PingMeasurement:
    ok: bool
    transmitted: int | None = None
    received: int | None = None
    packet_loss_percent: float | None = None
    latency_avg_ms: float | None = None
    raw: str = ""


@dataclass
class HttpMeasurement:
    ok: bool
    status: int | None
    elapsed_ms: float | None
    error: str | None = None


@dataclass
class SpeedMeasurement:
    ok: bool
    bytes_read: int
    elapsed_seconds: float
    mbps: float | None
    error: str | None = None


def measure_ping(config: Config) -> PingMeasurement:
    ping = shutil.which("ping") or "/sbin/ping"
    wait_value = "1000" if platform.system() == "Darwin" else "1"
    result = run_command(
        [ping, "-c", str(config.ping_count), "-W", wait_value, config.ping_target],
        timeout=config.ping_timeout_seconds,
    )
    raw = (result.stdout + "\n" + result.stderr).strip()
    packets = re.search(
        r"(\d+) packets transmitted, (\d+) (?:packets )?received, ([\d.]+)% packet loss", raw
    )
    stats = re.search(r"(?:round-trip|rtt) min/avg/max/(?:stddev|mdev) = [\d.]+/([\d.]+)/", raw)
    return PingMeasurement(
        ok=result.returncode == 0 and bool(packets),
        transmitted=int(packets.group(1)) if packets else None,
        received=int(packets.group(2)) if packets else None,
        packet_loss_percent=float(packets.group(3)) if packets else None,
        latency_avg_ms=float(stats.group(1)) if stats else None,
        raw=raw[-1500:],
    )


def http_check(config: Config) -> HttpMeasurement:
    request = urllib.request.Request(config.connectivity_url, headers={"User-Agent": "WindscribeMonitor/0.1"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout_seconds) as response:
            response.read(1024)
            return HttpMeasurement(True, response.status, (time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as exc:
        # A real HTTP response still proves routed application connectivity.
        return HttpMeasurement(True, exc.code, (time.monotonic() - started) * 1000)
    except Exception as exc:
        return HttpMeasurement(False, None, None, str(exc))


def public_ip(config: Config) -> str | None:
    request = urllib.request.Request(config.public_ip_url, headers={"User-Agent": "WindscribeMonitor/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout_seconds) as response:
            value = response.read(128).decode("ascii", "replace").strip()
            return value if re.fullmatch(r"[0-9a-fA-F:.]+", value) else None
    except Exception:
        return None


def speed_test(config: Config) -> SpeedMeasurement:
    url = config.speed_test_url.format(bytes=config.speed_test_bytes)
    request = urllib.request.Request(url, headers={"User-Agent": "WindscribeMonitor/0.1", "Cache-Control": "no-cache"})
    started = time.monotonic()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout_seconds) as response:
            while total < config.speed_test_bytes:
                chunk = response.read(min(64 * 1024, config.speed_test_bytes - total))
                if not chunk:
                    break
                total += len(chunk)
        elapsed = time.monotonic() - started
        mbps = (total * 8 / 1_000_000) / elapsed if elapsed > 0 and total else None
        return SpeedMeasurement(total > 0, total, elapsed, mbps)
    except Exception as exc:
        elapsed = time.monotonic() - started
        return SpeedMeasurement(False, total, elapsed, None, str(exc))


def to_dict(value: object) -> dict:
    return json.loads(json.dumps(asdict(value)))

